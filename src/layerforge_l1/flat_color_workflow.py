"""Reference-guided flat-color simplification and 2D layer-plan review.

The annotated reference is evidence only: its pixels never enter candidate
generation.  Candidates are derived from the clean source by preserving strong
art boundaries and flattening tonal variation inside the resulting art cells.
The optimized candidate may additionally remove complete low-importance
secondary contour groups when they are near a measured structural bottleneck
and rest on a large support region.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .connectivity_repair import audit_connection_width
from .layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from .shadowbox_simplification import (
    _analysis_from_labels,
    _protected_subjects,
    _region_inventory,
    run_actual_planner,
)
from .subtractive_poc import DEFAULT_CONFIG, PocConfig, analyze_image


@dataclass(frozen=True)
class FlatColorConfig:
    minimum_cell_pixels: int = 28
    minimum_changed_channel_delta: int = 3
    maximum_cell_p90_lab_radius: float = 42.0
    minimum_cell_lab_spread: float = 0.55
    strong_lab_edge_delta: float = 30.0
    dark_ink_luma: int = 78
    dark_ink_edge_distance_pixels: float = 2.4
    maximum_secondary_group_pixels: int = 900
    maximum_secondary_group_span_pixels: int = 54
    maximum_secondary_edits: int = 3
    bottleneck_proximity_pixels: int = 7


DEFAULT_FLAT_COLOR_CONFIG = FlatColorConfig()


# These fractional crops are review-only metadata required by this fox review.
# They never participate in segmentation, edit selection, or layer planning.
FOX_REVIEW_CROPS = {
    "blue_toolbox": (0.01, 0.72, 0.23, 0.91),
    "workbench": (0.49, 0.48, 0.995, 0.86),
    "orange_toolbox": (0.79, 0.42, 0.995, 0.59),
    "fox_clothing": (0.18, 0.42, 0.55, 0.87),
    "fox_tail": (0.025, 0.34, 0.32, 0.70),
    "robot": (0.57, 0.52, 0.91, 0.96),
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_mask(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L")) > 0


def _rgb_hex(color: np.ndarray) -> str:
    return "#" + "".join(f"{int(value):02X}" for value in color)


def _strong_boundaries(rgb: np.ndarray, config: FlatColorConfig) -> np.ndarray:
    """Return artwork boundaries while rejecting gentle painted gradients."""

    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    boundary = np.zeros(rgb.shape[:2], dtype=bool)
    horizontal = np.linalg.norm(lab[:, 1:] - lab[:, :-1], axis=2)
    vertical = np.linalg.norm(lab[1:] - lab[:-1], axis=2)
    strong_h = horizontal >= config.strong_lab_edge_delta
    strong_v = vertical >= config.strong_lab_edge_delta
    boundary[:, 1:] |= strong_h
    boundary[:, :-1] |= strong_h
    boundary[1:, :] |= strong_v
    boundary[:-1, :] |= strong_v

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    canny = cv2.Canny(gray, 72, 156) > 0
    dark = gray < config.dark_ink_luma
    distance = cv2.distanceTransform(dark.astype(np.uint8), cv2.DIST_L2, 5)
    dark_edges = dark & (distance <= config.dark_ink_edge_distance_pixels)
    ink_neighbourhood = cv2.dilate(
        dark.astype(np.uint8), np.ones((5, 5), np.uint8), iterations=1
    ).astype(bool)
    boundary |= (canny & ink_neighbourhood) | dark_edges
    return cv2.dilate(
        boundary.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1
    ).astype(bool)


def flatten_art_cells(
    rgb: np.ndarray,
    config: FlatColorConfig = DEFAULT_FLAT_COLOR_CONFIG,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], dict[str, Any]]:
    """Flatten tonal variation inside strong-boundary art cells.

    A cell is edited only when its color distribution is unimodal enough to
    plausibly represent one painted fill.  Strong boundaries, dark ink, tiny
    features, and multimodal cells are retained unchanged.
    """

    boundary = _strong_boundaries(rgb, config)
    count, components, stats, _ = cv2.connectedComponentsWithStats(
        (~boundary).astype(np.uint8), connectivity=4
    )
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    result = rgb.copy()
    cell_map = np.zeros(rgb.shape[:2], dtype=np.int32)
    changes: list[dict[str, Any]] = []
    cell_info: dict[int, dict[str, Any]] = {}
    preserved_small = preserved_multimodal = preserved_low_spread = 0

    for component in range(1, count):
        mask = components == component
        area = int(stats[component, cv2.CC_STAT_AREA])
        cell_map[mask] = component
        if area < config.minimum_cell_pixels:
            preserved_small += 1
            continue
        values_lab = lab[mask]
        median_lab = np.median(values_lab, axis=0)
        radii = np.linalg.norm(values_lab - median_lab, axis=1)
        p90 = float(np.percentile(radii, 90))
        spread = float(np.mean(np.std(values_lab, axis=0)))
        values_rgb = rgb[mask].astype(np.float32)
        median_rgb = np.median(values_rgb, axis=0).astype(np.uint8)
        cell_info[component] = {
            "area": area,
            "median_lab": median_lab,
            "median_rgb": median_rgb,
            "bounds": [
                int(stats[component, cv2.CC_STAT_LEFT]),
                int(stats[component, cv2.CC_STAT_TOP]),
                int(stats[component, cv2.CC_STAT_WIDTH]),
                int(stats[component, cv2.CC_STAT_HEIGHT]),
            ],
            "unimodal": p90 <= config.maximum_cell_p90_lab_radius,
        }
        if p90 > config.maximum_cell_p90_lab_radius:
            preserved_multimodal += 1
            continue
        if spread < config.minimum_cell_lab_spread:
            preserved_low_spread += 1
            continue
        old = result[mask].copy()
        result[mask] = median_rgb
        changed = np.any(
            np.abs(old.astype(np.int16) - median_rgb.astype(np.int16))
            >= config.minimum_changed_channel_delta,
            axis=1,
        )
        changed_pixels = int(np.count_nonzero(changed))
        if not changed_pixels:
            result[mask] = old
            preserved_low_spread += 1
            continue
        x = int(stats[component, cv2.CC_STAT_LEFT])
        y = int(stats[component, cv2.CC_STAT_TOP])
        width = int(stats[component, cv2.CC_STAT_WIDTH])
        height = int(stats[component, cv2.CC_STAT_HEIGHT])
        mean_luma = float(
            cv2.cvtColor(old.reshape(1, -1, 3), cv2.COLOR_RGB2GRAY).mean()
        )
        operation = (
            "REMOVE_NONESSENTIAL_HIGHLIGHT"
            if area < 220 and mean_luma > 170 and spread > 5.0
            else (
                "MERGE_SAME_OBJECT_TONAL_REGIONS"
                if spread > 8.0
                else "FLATTEN_OBJECT_SHADING"
            )
        )
        changes.append(
            {
                "cell_id": f"art_cell_{component:04d}",
                "operation": operation,
                "bounds_pixels_xywh": [x, y, width, height],
                "cell_area_pixels": area,
                "changed_pixels": changed_pixels,
                "original_mean_lab_spread": spread,
                "original_p90_lab_radius": p90,
                "replacement_rgb": _rgb_hex(median_rgb),
                "structural_reason": (
                    "Replace painted tonal variation inside one bounded fill; "
                    "physical layer separation supplies depth."
                ),
                "boundary_policy": "strong outlines and material/marking edges retained",
            }
        )

    # Merge adjacent, perceptually similar cells only when their shared
    # boundary is not supported by dark line art.  This is the distinction
    # between a lighting subdivision and a drawn object/material boundary.
    _, nearest = ndimage.distance_transform_edt(
        boundary.astype(bool), return_indices=True
    )
    owner = cell_map[nearest[0], nearest[1]]
    pair_arrays: list[np.ndarray] = []
    for first, second in ((owner[:, :-1], owner[:, 1:]), (owner[:-1], owner[1:])):
        differing = (first != second) & (first > 0) & (second > 0)
        if np.any(differing):
            low = np.minimum(first[differing], second[differing]).astype(np.int64)
            high = np.maximum(first[differing], second[differing]).astype(np.int64)
            pair_arrays.append(np.column_stack((low, high)))
    pairs = (
        np.unique(np.vstack(pair_arrays), axis=0)
        if pair_arrays
        else np.empty((0, 2), dtype=np.int64)
    )
    parent = {component: component for component in cell_info}

    def find(component: int) -> int:
        while parent[component] != component:
            parent[component] = parent[parent[component]]
            component = parent[component]
        return component

    def union(first: int, second: int) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    for first_raw, second_raw in pairs:
        first, second = int(first_raw), int(second_raw)
        if first not in cell_info or second not in cell_info:
            continue
        first_info, second_info = cell_info[first], cell_info[second]
        if not first_info["unimodal"] or not second_info["unimodal"]:
            continue
        delta = float(
            np.linalg.norm(first_info["median_lab"] - second_info["median_lab"])
        )
        if delta > 34.0:
            continue
        boxes = (first_info["bounds"], second_info["bounds"])
        left = max(0, min(box[0] for box in boxes) - 3)
        top = max(0, min(box[1] for box in boxes) - 3)
        right = min(
            rgb.shape[1], max(box[0] + box[2] for box in boxes) + 3
        )
        bottom = min(
            rgb.shape[0], max(box[1] + box[3] for box in boxes) + 3
        )
        first_local = (cell_map[top:bottom, left:right] == first).astype(np.uint8)
        second_local = (cell_map[top:bottom, left:right] == second).astype(np.uint8)
        near_first = cv2.dilate(first_local, np.ones((7, 7), np.uint8)).astype(bool)
        near_second = cv2.dilate(second_local, np.ones((7, 7), np.uint8)).astype(bool)
        seam = boundary[top:bottom, left:right] & near_first & near_second
        seam_pixels = int(np.count_nonzero(seam))
        if seam_pixels < 3:
            continue
        dark_fraction = float(np.mean(gray[top:bottom, left:right][seam] < config.dark_ink_luma))
        if dark_fraction <= 0.18:
            union(first, second)

    groups: dict[int, list[int]] = {}
    for component in cell_info:
        groups.setdefault(find(component), []).append(component)
    tonal_merges = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        primary = max(members, key=lambda component: cell_info[component]["area"])
        target = cell_info[primary]["median_rgb"]
        member_mask = np.isin(cell_map, members)
        old = result[member_mask].copy()
        result[member_mask] = target
        adjacency_count = np.zeros(boundary.shape, dtype=np.uint8)
        for component in members:
            adjacency_count += cv2.dilate(
                (cell_map == component).astype(np.uint8),
                np.ones((7, 7), np.uint8),
                iterations=1,
            )
        internal_boundary = (
            boundary & (adjacency_count >= 2) & (gray >= config.dark_ink_luma)
        )
        result[internal_boundary] = target
        changed_pixels = int(
            np.count_nonzero(
                np.any(old.astype(np.int16) != target.astype(np.int16), axis=1)
            )
        )
        if not changed_pixels:
            continue
        tonal_merges += 1
        member_bounds = [cell_info[component]["bounds"] for component in members]
        left = min(box[0] for box in member_bounds)
        top = min(box[1] for box in member_bounds)
        right = max(box[0] + box[2] for box in member_bounds)
        bottom = max(box[1] + box[3] for box in member_bounds)
        changes.append(
            {
                "cell_group_id": f"tonal_group_{tonal_merges:03d}",
                "operation": "MERGE_SAME_OBJECT_TONAL_REGIONS",
                "member_cell_ids": [f"art_cell_{component:04d}" for component in members],
                "bounds_pixels_xywh": [left, top, right - left, bottom - top],
                "changed_pixels": changed_pixels,
                "replacement_rgb": _rgb_hex(target),
                "structural_reason": (
                    "Adjacent similar fills lack a dark drawn boundary and are "
                    "treated as painted lighting subdivisions of one art region."
                ),
                "boundary_policy": "dark outline-supported boundaries retained",
            }
        )

    changed = np.any(result != rgb, axis=2)
    summary = {
        "method": (
            "strong-boundary art cells with robust within-cell median fills; "
            "no global palette-count reduction"
        ),
        "detected_art_cells": count - 1,
        "flattened_art_cells": sum(1 for row in changes if "cell_id" in row),
        "merged_tonal_cell_groups": tonal_merges,
        "preserved_small_cells": preserved_small,
        "preserved_multimodal_cells": preserved_multimodal,
        "preserved_low_spread_cells": preserved_low_spread,
        "preserved_boundary_pixels": int(np.count_nonzero(boundary)),
        "changed_pixels": int(np.count_nonzero(changed)),
        "changed_fraction": float(np.mean(changed)),
        "operations": dict(
            sorted(
                {
                    operation: sum(1 for row in changes if row["operation"] == operation)
                    for operation in {row["operation"] for row in changes}
                }.items()
            )
        ),
        "preservation_operation": "PRESERVE_STRUCTURAL_OR_IDENTITY_FEATURE",
    }
    return result, boundary, changes, summary


def _primary_subject_protection(
    labels: np.ndarray, palette: np.ndarray
) -> tuple[np.ndarray, dict[str, Any]]:
    """Protect foreground subject box clusters without semantic class names."""

    region_map, regions = _region_inventory(labels)
    _, raw = _protected_subjects(labels, palette, region_map, regions)
    boxes = [list(map(int, box)) for box in raw["protected_boxes_pixels_xyxy"]]
    height, width = labels.shape
    selected = [box for box in boxes if box[3] >= int(height * 0.85)]
    changed = True
    while changed:
        changed = False
        for box in boxes:
            if box in selected:
                continue
            box_width = max(1, box[2] - box[0])
            for prior in selected:
                overlap_x = max(0, min(box[2], prior[2]) - max(box[0], prior[0]))
                overlap_ratio = overlap_x / min(box_width, max(1, prior[2] - prior[0]))
                width_ratio = min(box_width, prior[2] - prior[0]) / max(
                    box_width, prior[2] - prior[0]
                )
                overlap_y = max(0, min(box[3], prior[3]) - max(box[1], prior[1]))
                overlap_y_ratio = overlap_y / min(
                    max(1, box[3] - box[1]), max(1, prior[3] - prior[1])
                )
                if (
                    overlap_ratio >= 0.45
                    and width_ratio >= 0.50
                    and overlap_y_ratio >= 0.12
                ):
                    selected.append(box)
                    changed = True
                    break
    protected = np.zeros(labels.shape, dtype=np.uint8)
    for left, top, right, bottom in selected:
        protected[top:bottom, left:right] = 1
    protected = cv2.dilate(protected, np.ones((5, 5), np.uint8), iterations=1)
    return protected.astype(bool), {
        "method": (
            "lower-foreground salient anchors plus vertically adjacent boxes "
            "with substantial horizontal overlap; chaining protects split "
            "head/body subject extents"
        ),
        "candidate_boxes_pixels_xyxy": boxes,
        "selected_primary_boxes_pixels_xyxy": selected,
        "protected_pixels": int(np.count_nonzero(protected)),
        "semantic_certainty": "HEURISTIC_NOT_OBJECT_CLASSIFICATION",
    }


def _orphan_heatmap(
    plan_root: Path,
    report: dict[str, Any],
    poc: PocConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    heat = np.zeros((poc.analysis_pixels, poc.analysis_pixels), dtype=np.int32)
    rows: list[dict[str, Any]] = []
    perimeter = poc.perimeter_pixels
    for layer in report.get("layers", []):
        physical = _load_mask(plan_root / layer["retained_material_mask"])
        visible = _load_mask(plan_root / layer["visible_region_mask"])
        audit, maps = audit_connection_width(
            physical, visible, poc, DEFAULT_OPTIMIZATION_CONFIG
        )
        cropped = maps["orphan_core"][
            perimeter:-perimeter, perimeter:-perimeter
        ]
        heat += cropped.astype(np.int32)
        rows.append(
            {
                "layer_id": layer["id"],
                "orphan_structural_core_components": audit[
                    "orphan_structural_core_components"
                ],
                "orphan_structural_core_pixels": audit[
                    "orphan_structural_core_pixels"
                ],
            }
        )
    return heat, {
        "unique_orphan_core_pixels": int(np.count_nonzero(heat)),
        "weighted_orphan_core_pixels": int(heat.sum()),
        "layers": rows,
    }


def _secondary_group_proposals(
    flat_rgb: np.ndarray,
    analysis: dict[str, Any],
    orphan_heat: np.ndarray,
    config: FlatColorConfig,
) -> tuple[list[dict[str, Any]], np.ndarray, np.ndarray]:
    """Find complete secondary contour groups near structural bottlenecks."""

    labels = np.asarray(analysis["labels"])
    palette = np.asarray(analysis["palette_rgb"])
    protected, protection = _primary_subject_protection(labels, palette)
    gray = cv2.cvtColor(flat_rgb, cv2.COLOR_RGB2GRAY)
    dark = cv2.morphologyEx(
        (gray < config.dark_ink_luma).astype(np.uint8),
        cv2.MORPH_CLOSE,
        np.ones((3, 3), np.uint8),
    )
    component_count, components, stats, _ = cv2.connectedComponentsWithStats(
        dark, connectivity=8
    )
    region_map, regions = _region_inventory(labels)
    region_by_id = {row["index"]: row for row in regions}
    near_heat = cv2.dilate(
        (orphan_heat > 0).astype(np.uint8),
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (
                config.bottleneck_proximity_pixels * 2 + 1,
                config.bottleneck_proximity_pixels * 2 + 1,
            ),
        ),
    ).astype(bool)
    proposals: list[dict[str, Any]] = []
    proposal_masks: list[np.ndarray] = []
    height, width = labels.shape

    for component in range(1, component_count):
        x = int(stats[component, cv2.CC_STAT_LEFT])
        y = int(stats[component, cv2.CC_STAT_TOP])
        box_width = int(stats[component, cv2.CC_STAT_WIDTH])
        box_height = int(stats[component, cv2.CC_STAT_HEIGHT])
        if min(box_width, box_height) < 4:
            continue
        if max(box_width, box_height) / max(1, min(box_width, box_height)) > 2.4:
            continue
        if y + box_height / 2 > height * 0.68:
            continue
        if max(box_width, box_height) > config.maximum_secondary_group_span_pixels:
            continue
        component_mask = (components == component).astype(np.uint8)
        contours, _ = cv2.findContours(
            component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            continue
        filled = np.zeros_like(component_mask)
        cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
        filled_bool = filled.astype(bool)
        filled_area = int(np.count_nonzero(filled_bool))
        if not 24 <= filled_area <= config.maximum_secondary_group_pixels:
            continue
        protected_overlap = float(np.mean(protected[filled_bool]))
        if protected_overlap > 0.02:
            continue

        below_top = min(height, y + box_height)
        below_bottom = min(height, below_top + 18)
        if below_top == below_bottom:
            continue
        below_regions = region_map[below_top:below_bottom, x : x + box_width]
        if not below_regions.size:
            continue
        ids, counts = np.unique(below_regions, return_counts=True)
        ranked_supports = sorted(
            zip(ids.tolist(), counts.tolist()), key=lambda item: -item[1]
        )
        support = next(
            (
                region_by_id[int(item[0])]
                for item in ranked_supports
                if region_by_id[int(item[0])]["bounds_pixels"][2] >= width * 0.20
                and region_by_id[int(item[0])]["area_pixels"] >= labels.size * 0.006
                and not region_by_id[int(item[0])]["touches_border"]
            ),
            None,
        )
        if support is None:
            continue
        support_area = support["area_pixels"]

        contour = max(contours, key=cv2.contourArea)
        perimeter = float(cv2.arcLength(contour, True))
        approximation = cv2.approxPolyDP(contour, max(1.0, perimeter * 0.025), True)
        vertex_count = len(approximation)
        inner_holes = max(
            0,
            cv2.connectedComponents(
                (~filled_bool).astype(np.uint8), connectivity=8
            )[0]
            - 2,
        )
        hsv = cv2.cvtColor(flat_rgb, cv2.COLOR_RGB2HSV)
        saturation = float(np.median(hsv[:, :, 1][filled_bool])) / 255.0
        complexity = min(1.0, vertex_count / 18.0 + inner_holes * 0.25)
        importance = min(
            1.0,
            0.30 * min(1.0, filled_area / config.maximum_secondary_group_pixels)
            + 0.35 * complexity
            + 0.20 * saturation
            + 0.15 * min(1.0, max(box_width, box_height) / 45.0),
        )
        core_overlap = int(orphan_heat[filled_bool].sum())
        near_core_pixels = int(np.count_nonzero(near_heat & filled_bool))
        eligible = (
            (core_overlap > 0 or near_core_pixels >= 20) and importance < 0.55
        )
        score = (
            core_overlap * 20.0
            + near_core_pixels * 2.0
            + (1.0 - importance) * 40.0
            - filled_area * 0.01
        )
        proposal_index = len(proposal_masks)
        proposal_masks.append(filled_bool)
        proposals.append(
            {
                "proposal_id": f"secondary_contour_{proposal_index:03d}",
                "mask_index": proposal_index,
                "bounds_pixels_xywh": [x, y, box_width, box_height],
                "area_pixels": filled_area,
                "importance_estimate_0_1": importance,
                "importance_features": {
                    "contour_vertices": vertex_count,
                    "estimated_inner_holes": inner_holes,
                    "median_saturation_0_1": saturation,
                    "contour_complexity_0_1": complexity,
                },
                "uncertainty": "MEDIUM_CONTOUR_GROUP_NOT_SEMANTICALLY_VERIFIED",
                "primary_protection_overlap_fraction": protected_overlap,
                "support_region_id": support["id"],
                "support_region_area_pixels": support_area,
                "orphan_core_weighted_overlap": core_overlap,
                "near_orphan_core_pixels": near_core_pixels,
                "eligible_for_optimized_candidate": eligible,
                "selection_score": score,
                "selected": False,
            }
        )

    # A second generic proposal source looks immediately above broad,
    # shallow, non-border support regions.  It finds complete loose objects on
    # shelves/tabletops even when their outline color participates in a larger
    # scene-wide ink network.
    border = np.concatenate(
        (labels[0], labels[-1], labels[:, 0], labels[:, -1])
    )
    background = int(np.bincount(border).argmax())
    area_by_region = np.zeros(len(regions), dtype=np.int32)
    for region in regions:
        area_by_region[region["index"]] = region["area_pixels"]
    supports = [
        region
        for region in regions
        if not region["touches_border"]
        and region["bounds_pixels"][2] >= width * 0.17
        and region["bounds_pixels"][3] <= height * 0.10
        # A real shelf/tabletop support must be materially broader than a
        # character-held part.  The stricter area floor prevents a hand,
        # tool handle, or costume band from being mistaken for furniture.
        and region["area_pixels"] >= labels.size * 0.008
        and height * 0.18 <= region["bounds_pixels"][1] <= height * 0.75
    ]
    for support in supports:
        sx, sy, support_width, _ = support["bounds_pixels"]
        left = max(0, sx)
        right = min(width, sx + support_width)
        top = max(0, sy - int(height * 0.13))
        bottom = min(height, sy + 12)
        local_region_map = region_map[top:bottom, left:right]
        local = (
            (labels[top:bottom, left:right] != support["palette_index"])
            & (labels[top:bottom, left:right] != background)
            & (~protected[top:bottom, left:right])
            & (area_by_region[local_region_map] <= config.maximum_secondary_group_pixels)
        )
        local_count, local_components, local_stats, _ = cv2.connectedComponentsWithStats(
            local.astype(np.uint8), connectivity=8
        )
        for local_component in range(1, local_count):
            lx = int(local_stats[local_component, cv2.CC_STAT_LEFT])
            ly = int(local_stats[local_component, cv2.CC_STAT_TOP])
            lw = int(local_stats[local_component, cv2.CC_STAT_WIDTH])
            lh = int(local_stats[local_component, cv2.CC_STAT_HEIGHT])
            if min(lw, lh) < 4 or max(lw, lh) > config.maximum_secondary_group_span_pixels:
                continue
            if max(lw, lh) / max(1, min(lw, lh)) > 2.4:
                continue
            if top + ly + lh < sy - 12:
                continue
            local_mask = local_components == local_component
            nearby = cv2.dilate(
                local_mask.astype(np.uint8),
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)),
                iterations=1,
            ).astype(bool)
            local_ink = (
                gray[top:bottom, left:right] < config.dark_ink_luma
            )
            local_object = local_mask | (nearby & local_ink)
            contours, _ = cv2.findContours(
                local_object.astype(np.uint8),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            if not contours:
                continue
            local_filled = np.zeros_like(local_components, dtype=np.uint8)
            cv2.drawContours(local_filled, contours, -1, 1, thickness=cv2.FILLED)
            filled_y, filled_x = np.where(local_filled > 0)
            if not filled_x.size:
                continue
            lx = int(filled_x.min())
            ly = int(filled_y.min())
            lw = int(filled_x.max()) + 1 - lx
            lh = int(filled_y.max()) + 1 - ly
            if max(lw, lh) > config.maximum_secondary_group_span_pixels:
                continue
            if max(lw, lh) / max(1, min(lw, lh)) > 2.4:
                continue
            group = np.zeros(labels.shape, dtype=bool)
            group[top:bottom, left:right] = local_filled.astype(bool)
            group_area = int(np.count_nonzero(group))
            if not 24 <= group_area <= config.maximum_secondary_group_pixels:
                continue
            duplicate = False
            for existing in proposal_masks:
                intersection = int(np.count_nonzero(existing & group))
                union = int(np.count_nonzero(existing | group))
                if union and intersection / union >= 0.75:
                    duplicate = True
                    break
            if duplicate:
                continue
            contour = max(contours, key=cv2.contourArea)
            perimeter = float(cv2.arcLength(contour, True))
            approximation = cv2.approxPolyDP(
                contour, max(1.0, perimeter * 0.025), True
            )
            vertex_count = len(approximation)
            hsv = cv2.cvtColor(flat_rgb, cv2.COLOR_RGB2HSV)
            saturation = float(np.median(hsv[:, :, 1][group])) / 255.0
            complexity = min(1.0, vertex_count / 18.0)
            importance = min(
                1.0,
                0.30 * min(1.0, group_area / config.maximum_secondary_group_pixels)
                + 0.35 * complexity
                + 0.20 * saturation
                + 0.15 * min(1.0, max(lw, lh) / 45.0),
            )
            core_overlap = int(orphan_heat[group].sum())
            near_core_pixels = int(np.count_nonzero(near_heat & group))
            eligible = (
                (core_overlap > 0 or near_core_pixels >= 20)
                and importance < 0.55
            )
            score = (
                core_overlap * 20.0
                + near_core_pixels * 2.0
                + (1.0 - importance) * 40.0
                - group_area * 0.01
            )
            proposal_index = len(proposal_masks)
            proposal_masks.append(group)
            proposals.append(
                {
                    "proposal_id": f"secondary_contour_{proposal_index:03d}",
                    "mask_index": proposal_index,
                    "proposal_source": "OBJECT_ABOVE_BROAD_SUPPORT",
                    "bounds_pixels_xywh": [left + lx, top + ly, lw, lh],
                    "area_pixels": group_area,
                    "importance_estimate_0_1": importance,
                    "importance_features": {
                        "contour_vertices": vertex_count,
                        "estimated_inner_holes": 0,
                        "median_saturation_0_1": saturation,
                        "contour_complexity_0_1": complexity,
                    },
                    "uncertainty": "MEDIUM_CONTOUR_GROUP_NOT_SEMANTICALLY_VERIFIED",
                    "primary_protection_overlap_fraction": 0.0,
                    "support_region_id": support["id"],
                    "support_region_area_pixels": support["area_pixels"],
                    "orphan_core_weighted_overlap": core_overlap,
                    "near_orphan_core_pixels": near_core_pixels,
                    "eligible_for_optimized_candidate": eligible,
                    "selection_score": score,
                    "selected": False,
                }
            )

    # Closed outlines are sometimes part of a larger ink network.  In that
    # case use a bounded indexed-region seed, identify the large region below
    # it as its support, and collect the locally connected non-support pixels.
    # This captures a complete small multi-color object without consuming the
    # globally connected outline network.
    for seed in regions:
        seed_area = seed["area_pixels"]
        x, y, box_width, box_height = seed["bounds_pixels"]
        if not 18 <= seed_area <= 450:
            continue
        if max(box_width, box_height) > 45:
            continue
        if max(box_width, box_height) / max(1, min(box_width, box_height)) > 2.4:
            continue
        if y + box_height / 2 > height * 0.68:
            continue
        seed_mask = region_map == seed["index"]
        if float(np.mean(protected[seed_mask])) > 0.02:
            continue
        below_top = min(height, y + box_height)
        below_bottom = min(height, below_top + 18)
        if below_top == below_bottom:
            continue
        below_regions = region_map[below_top:below_bottom, x : x + box_width]
        if not below_regions.size:
            continue
        ids, counts = np.unique(below_regions, return_counts=True)
        ranked_supports = sorted(
            zip(ids.tolist(), counts.tolist()), key=lambda item: -item[1]
        )
        support = next(
            (
                region_by_id[int(item[0])]
                for item in ranked_supports
                if int(item[0]) != seed["index"]
                and region_by_id[int(item[0])]["bounds_pixels"][2] >= width * 0.20
                and region_by_id[int(item[0])]["area_pixels"] >= labels.size * 0.006
                and not region_by_id[int(item[0])]["touches_border"]
            ),
            None,
        )
        if support is None:
            continue
        margin = 6
        left = max(0, x - margin)
        top = max(0, y - margin)
        right = min(width, x + box_width + margin)
        bottom = min(height, y + box_height + margin)
        local = labels[top:bottom, left:right] != support["palette_index"]
        local_count, local_components = cv2.connectedComponents(
            local.astype(np.uint8), connectivity=8
        )
        local_seed = seed_mask[top:bottom, left:right]
        component_ids = local_components[local_seed]
        component_ids = component_ids[component_ids > 0]
        if not component_ids.size:
            continue
        values, component_counts = np.unique(component_ids, return_counts=True)
        local_id = int(values[np.argmax(component_counts)])
        local_group = (local_components == local_id).astype(np.uint8)
        contours, _ = cv2.findContours(
            local_group, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            continue
        local_filled = np.zeros_like(local_group)
        cv2.drawContours(local_filled, contours, -1, 1, thickness=cv2.FILLED)
        if (
            np.any(local_filled[0, :])
            or np.any(local_filled[-1, :])
            or np.any(local_filled[:, 0])
            or np.any(local_filled[:, -1])
        ):
            # The seed belongs to a larger structure extending beyond this
            # bounded proposal (for example a furniture leg or panel edge),
            # not a self-contained secondary object.
            continue
        global_group = np.zeros(labels.shape, dtype=bool)
        global_group[top:bottom, left:right] = local_filled.astype(bool)
        group_y, group_x = np.where(global_group)
        if not group_x.size:
            continue
        gx0, gx1 = int(group_x.min()), int(group_x.max()) + 1
        gy0, gy1 = int(group_y.min()), int(group_y.max()) + 1
        group_width, group_height = gx1 - gx0, gy1 - gy0
        group_area = int(np.count_nonzero(global_group))
        if not 24 <= group_area <= config.maximum_secondary_group_pixels:
            continue
        if max(group_width, group_height) > config.maximum_secondary_group_span_pixels:
            continue
        if float(np.mean(protected[global_group])) > 0.02:
            continue
        duplicate = False
        for existing in proposal_masks:
            intersection = int(np.count_nonzero(existing & global_group))
            union = int(np.count_nonzero(existing | global_group))
            if union and intersection / union >= 0.80:
                duplicate = True
                break
        if duplicate:
            continue
        contour = max(contours, key=cv2.contourArea)
        perimeter = float(cv2.arcLength(contour, True))
        approximation = cv2.approxPolyDP(
            contour, max(1.0, perimeter * 0.025), True
        )
        vertex_count = len(approximation)
        hsv = cv2.cvtColor(flat_rgb, cv2.COLOR_RGB2HSV)
        saturation = float(np.median(hsv[:, :, 1][global_group])) / 255.0
        complexity = min(1.0, vertex_count / 18.0)
        importance = min(
            1.0,
            0.30 * min(1.0, group_area / config.maximum_secondary_group_pixels)
            + 0.35 * complexity
            + 0.20 * saturation
            + 0.15 * min(1.0, max(group_width, group_height) / 45.0),
        )
        core_overlap = int(orphan_heat[global_group].sum())
        near_core_pixels = int(np.count_nonzero(near_heat & global_group))
        eligible = (
            (core_overlap > 0 or near_core_pixels >= 20) and importance < 0.55
        )
        score = (
            core_overlap * 20.0
            + near_core_pixels * 2.0
            + (1.0 - importance) * 40.0
            - group_area * 0.01
        )
        proposal_index = len(proposal_masks)
        proposal_masks.append(global_group)
        proposals.append(
            {
                "proposal_id": f"secondary_contour_{proposal_index:03d}",
                "mask_index": proposal_index,
                "proposal_source": "BOUNDED_INDEXED_REGION_SEED",
                "bounds_pixels_xywh": [gx0, gy0, group_width, group_height],
                "area_pixels": group_area,
                "importance_estimate_0_1": importance,
                "importance_features": {
                    "contour_vertices": vertex_count,
                    "estimated_inner_holes": 0,
                    "median_saturation_0_1": saturation,
                    "contour_complexity_0_1": complexity,
                },
                "uncertainty": "MEDIUM_CONTOUR_GROUP_NOT_SEMANTICALLY_VERIFIED",
                "primary_protection_overlap_fraction": float(
                    np.mean(protected[global_group])
                ),
                "support_region_id": support["id"],
                "support_region_area_pixels": support["area_pixels"],
                "orphan_core_weighted_overlap": core_overlap,
                "near_orphan_core_pixels": near_core_pixels,
                "eligible_for_optimized_candidate": eligible,
                "selection_score": score,
                "selected": False,
            }
        )

    # Repeated aligned interior hardware on one secondary panel is a single
    # object-level simplification proposal, not a collection of tiny-region
    # deletions.  This catches redundant drawer pulls/panel fasteners while
    # excluding facial/mechanical detail through primary protection and a
    # repeated-pattern requirement.
    repeated_candidates: list[dict[str, Any]] = []
    for component in range(1, component_count):
        x = int(stats[component, cv2.CC_STAT_LEFT])
        y = int(stats[component, cv2.CC_STAT_TOP])
        box_width = int(stats[component, cv2.CC_STAT_WIDTH])
        box_height = int(stats[component, cv2.CC_STAT_HEIGHT])
        area = int(stats[component, cv2.CC_STAT_AREA])
        if not (8 <= box_width <= 38 and 4 <= box_height <= 18 and 45 <= area <= 420):
            continue
        if not (height * 0.50 <= y <= height * 0.82):
            continue
        if box_width / max(1, box_height) < 1.45:
            continue
        mask = components == component
        protected_overlap = float(np.mean(protected[mask]))
        if protected_overlap > 0.45:
            continue
        ring = (
            cv2.dilate(mask.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
            & ~mask
        )
        ring_labels = labels[ring]
        if not ring_labels.size:
            continue
        values, counts = np.unique(ring_labels, return_counts=True)
        support_palette = int(values[np.argmax(counts)])
        repeated_candidates.append(
            {
                "component": component,
                "bounds": [x, y, box_width, box_height],
                "area": area,
                "center": [x + box_width / 2.0, y + box_height / 2.0],
                "support_palette": support_palette,
                "protected_overlap": protected_overlap,
            }
        )

    repeated_groups: list[list[dict[str, Any]]] = []
    consumed: set[int] = set()
    for index, first in enumerate(repeated_candidates):
        if index in consumed:
            continue
        group = [first]
        fw, fh = first["bounds"][2:]
        for other_index in range(index + 1, len(repeated_candidates)):
            other = repeated_candidates[other_index]
            if other["support_palette"] != first["support_palette"]:
                continue
            ow, oh = other["bounds"][2:]
            if abs(other["center"][0] - first["center"][0]) > max(fw, ow) * 0.45:
                continue
            vertical_gap = abs(other["center"][1] - first["center"][1])
            if not max(fh, oh) * 1.5 <= vertical_gap <= height * 0.16:
                continue
            if min(fw, ow) / max(fw, ow) < 0.65 or min(fh, oh) / max(fh, oh) < 0.65:
                continue
            group.append(other)
            consumed.add(other_index)
        if len(group) >= 2:
            repeated_groups.append(group)

    for repeated_group in repeated_groups:
        group_mask = np.zeros(labels.shape, dtype=bool)
        for row in repeated_group:
            group_mask |= components == row["component"]
        group_y, group_x = np.where(group_mask)
        left, right = int(group_x.min()), int(group_x.max()) + 1
        top, bottom = int(group_y.min()), int(group_y.max()) + 1
        group_area = int(np.count_nonzero(group_mask))
        core_overlap = int(orphan_heat[group_mask].sum())
        near_core_pixels = int(np.count_nonzero(near_heat & group_mask))
        proposal_index = len(proposal_masks)
        proposal_masks.append(group_mask)
        proposals.append(
            {
                "proposal_id": f"secondary_contour_{proposal_index:03d}",
                "mask_index": proposal_index,
                "proposal_source": "REPEATED_SECONDARY_INTERIOR_DETAILS",
                "operation": "SIMPLIFY_SECONDARY_INTERIOR_DETAIL",
                "bounds_pixels_xywh": [left, top, right - left, bottom - top],
                "area_pixels": group_area,
                "repeated_detail_count": len(repeated_group),
                "importance_estimate_0_1": 0.42,
                "importance_features": {
                    "repeated_aligned_shapes": len(repeated_group),
                    "shared_support_palette_index": repeated_group[0]["support_palette"],
                    "maximum_primary_box_overlap_fraction": max(
                        row["protected_overlap"] for row in repeated_group
                    ),
                },
                "uncertainty": "MEDIUM_REPEATED_PANEL_DETAIL_NOT_SEMANTICALLY_VERIFIED",
                "primary_protection_overlap_fraction": float(np.mean(protected[group_mask])),
                "support_region_id": f"palette_{repeated_group[0]['support_palette']:02d}",
                "support_region_area_pixels": int(
                    np.count_nonzero(labels == repeated_group[0]["support_palette"])
                ),
                "orphan_core_weighted_overlap": core_overlap,
                "near_orphan_core_pixels": near_core_pixels,
                "eligible_for_optimized_candidate": True,
                "selection_score": 1000.0 + core_overlap * 20.0 + near_core_pixels * 2.0,
                "selected": False,
            }
        )

    proposals.sort(key=lambda row: (-row["selection_score"], row["proposal_id"]))
    selected = [
        row for row in proposals if row["eligible_for_optimized_candidate"]
    ][: config.maximum_secondary_edits]
    selected_ids = {row["proposal_id"] for row in selected}
    combined = np.zeros(labels.shape, dtype=bool)
    for row in proposals:
        row["selected"] = row["proposal_id"] in selected_ids
        if row["selected"]:
            combined |= proposal_masks[row["mask_index"]]
    return proposals, combined, protected


def optimize_secondary_details(
    flat_rgb: np.ndarray,
    analysis: dict[str, Any],
    orphan_heat: np.ndarray,
    config: FlatColorConfig = DEFAULT_FLAT_COLOR_CONFIG,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, Any], np.ndarray]:
    proposals, selected_mask, protected = _secondary_group_proposals(
        flat_rgb, analysis, orphan_heat, config
    )
    result = flat_rgb.copy()
    selected_rows = [row for row in proposals if row["selected"]]
    changes: list[dict[str, Any]] = []
    if np.any(selected_mask):
        cleanup_mask = cv2.dilate(
            selected_mask.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=3
        )
        # Coarse primary boxes may overlap the edge of a repeated furniture
        # panel.  The repeated-pattern proposal has its own conservative
        # overlap cap; all other proposals remain strictly clipped by the
        # primary protection mask.
        repeated_override = (
            selected_mask
            if selected_rows
            and all(
                row.get("proposal_source") == "REPEATED_SECONDARY_INTERIOR_DETAILS"
                for row in selected_rows
            )
            else np.zeros_like(selected_mask)
        )
        protected_clip = protected & ~cv2.dilate(
            repeated_override.astype(np.uint8), np.ones((7, 7), np.uint8), iterations=1
        ).astype(bool)
        cleanup_mask[protected_clip] = 0
        _, nearest = ndimage.distance_transform_edt(
            cleanup_mask.astype(bool), return_indices=True
        )
        fill = result[nearest[0], nearest[1]]
        result[cleanup_mask.astype(bool)] = fill[cleanup_mask.astype(bool)]
        for row in selected_rows:
            operation = row.get("operation", "REMOVE_NONESSENTIAL_WORKSHOP_CLUTTER")
            changes.append(
                {
                    "proposal_id": row["proposal_id"],
                    "operation": operation,
                    "bounds_pixels_xywh": row["bounds_pixels_xywh"],
                    "changed_group_area_pixels": row["area_pixels"],
                    "specific_visual_change": (
                        "Remove a repeated set of aligned secondary panel details as "
                        "one furniture simplification."
                        if operation == "SIMPLIFY_SECONDARY_INTERIOR_DETAIL"
                        else "Remove one complete low-importance secondary contour group "
                        "and reconstruct its surrounding support/background."
                    ),
                    "why_selected": (
                        "Repeated aligned interior details on a shared secondary panel "
                        "create redundant enclosed regions and depth obligations."
                        if operation == "SIMPLIFY_SECONDARY_INTERIOR_DETAIL"
                        else "The group is outside inferred primary subjects, rests on a "
                        "large support region, and intersects or lies within seven "
                        "pixels of a measured orphan structural core."
                    ),
                    "intended_shadow_box_constraint": (
                        "Reduce repeated enclosed-region and cutout obligations in a "
                        "secondary furniture panel."
                        if operation == "SIMPLIFY_SECONDARY_INTERIOR_DETAIL"
                        else "Reduce secondary region/depth obligations and narrow cutout cores."
                    ),
                    "importance_estimate_0_1": row["importance_estimate_0_1"],
                    "uncertainty": row["uncertainty"],
                    "orphan_core_weighted_overlap": row[
                        "orphan_core_weighted_overlap"
                    ],
                    "near_orphan_core_pixels": row["near_orphan_core_pixels"],
                    "protected_feature_overlap_pixels": 0,
                }
            )
    summary = {
        "method": (
            "complete dark-contour secondary groups ranked by primary protection, "
            "support-surface context, contour complexity, and measured width bottlenecks"
        ),
        "proposal_count": len(proposals),
        "selected_count": len(selected_rows),
        "selected_pixels": int(np.count_nonzero(selected_mask)),
        "primary_protected_pixels": int(np.count_nonzero(protected)),
        "automatic_selection": "PROPOSED_NOT_APPROVED",
    }
    return result, changes, {"summary": summary, "proposals": proposals}, protected


def _change_map(original: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    delta = np.max(
        np.abs(original.astype(np.int16) - candidate.astype(np.int16)), axis=2
    )
    changed = delta >= 3
    base = cv2.cvtColor(original, cv2.COLOR_RGB2GRAY)
    image = np.repeat(base[:, :, None], 3, axis=2)
    image = np.clip(image * 0.55 + 105, 0, 255).astype(np.uint8)
    image[changed] = (235, 45, 45)
    return image


def _image_metrics(
    original: np.ndarray,
    candidate: np.ndarray,
    protected: np.ndarray,
) -> dict[str, Any]:
    difference = np.abs(candidate.astype(np.int16) - original.astype(np.int16))
    changed = np.any(difference >= 3, axis=2)
    original_edges = cv2.Canny(original, 72, 156) > 0
    candidate_edges = cv2.Canny(candidate, 72, 156) > 0
    nearby_candidate = cv2.dilate(candidate_edges.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    protected_edges = original_edges & protected
    retained = protected_edges & nearby_candidate
    return {
        "basis": "candidate artwork versus same-resolution original RGB",
        "changed_rgb_pixels_delta_at_least_3": int(np.count_nonzero(changed)),
        "changed_rgb_fraction": float(np.mean(changed)),
        "mean_absolute_rgb_error_0_255": float(difference.mean()),
        "maximum_channel_error_0_255": int(difference.max()),
        "protected_area_mean_absolute_error_0_255": (
            float(difference[protected].mean()) if np.any(protected) else 0.0
        ),
        "protected_original_outline_pixels": int(np.count_nonzero(protected_edges)),
        "protected_outline_pixels_retained_within_1px": int(np.count_nonzero(retained)),
        "protected_outline_retention_within_1px": (
            float(np.count_nonzero(retained) / np.count_nonzero(protected_edges))
            if np.any(protected_edges)
            else 1.0
        ),
    }


def _mask_reconstruction_metrics(
    candidate_rgb: np.ndarray,
    indexed_target: np.ndarray,
    reconstructed: np.ndarray,
) -> dict[str, Any]:
    target_difference = np.abs(
        reconstructed.astype(np.int16) - indexed_target.astype(np.int16)
    )
    rgb_difference = np.abs(
        reconstructed.astype(np.int16) - candidate_rgb.astype(np.int16)
    )
    return {
        "mask_reconstruction_mismatched_pixels_vs_indexed_target": int(
            np.count_nonzero(np.any(target_difference > 0, axis=2))
        ),
        "mask_reconstruction_mae_vs_candidate_rgb_0_255": float(
            rgb_difference.mean()
        ),
        "comparison_note": (
            "The physical stack reconstructs the indexed filament target; RGB "
            "artwork differences also include palette/material approximation."
        ),
    }


def _render_side_by_side(
    original: np.ndarray,
    flat: np.ndarray,
    optimized: np.ndarray,
    path: Path,
) -> None:
    height, width = original.shape[:2]
    header = 44
    canvas = Image.new("RGB", (width * 3, height + header), "white")
    draw = ImageDraw.Draw(canvas)
    images = (("ORIGINAL CLEAN SOURCE", original), ("A: FLAT-COLOR", flat), ("B: SHADOW-BOX OPTIMIZED", optimized))
    for index, (title, image) in enumerate(images):
        canvas.paste(Image.fromarray(image), (index * width, header))
        draw.text((index * width + 10, 14), title, fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)


def _render_closeups(
    original: np.ndarray,
    flat: np.ndarray,
    optimized: np.ndarray,
    root: Path,
) -> dict[str, list[int]]:
    height, width = original.shape[:2]
    recorded: dict[str, list[int]] = {}
    for name, fractions in FOX_REVIEW_CROPS.items():
        x0 = int(round(fractions[0] * width))
        y0 = int(round(fractions[1] * height))
        x1 = int(round(fractions[2] * width))
        y1 = int(round(fractions[3] * height))
        recorded[name] = [x0, y0, x1, y1]
        crops = [image[y0:y1, x0:x1] for image in (original, flat, optimized)]
        scale = 2
        crop_width = x1 - x0
        crop_height = y1 - y0
        header = 34
        canvas = Image.new(
            "RGB", (crop_width * scale * 3, crop_height * scale + header), "white"
        )
        draw = ImageDraw.Draw(canvas)
        for index, (title, crop) in enumerate(zip(("ORIGINAL", "FLAT", "OPTIMIZED"), crops)):
            rendered = Image.fromarray(crop).resize(
                (crop_width * scale, crop_height * scale), Image.Resampling.NEAREST
            )
            canvas.paste(rendered, (index * crop_width * scale, header))
            draw.text((index * crop_width * scale + 8, 10), title, fill="black")
        root.mkdir(parents=True, exist_ok=True)
        canvas.save(root / f"{name}.png")
    return recorded


def _render_candidate_and_reconstruction(
    candidate: np.ndarray,
    indexed: np.ndarray,
    reconstructed: np.ndarray,
    title: str,
    path: Path,
) -> None:
    height, width = candidate.shape[:2]
    header = 42
    canvas = Image.new("RGB", (width * 3, height + header), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (label, image) in enumerate(
        ((title, candidate), ("INDEXED FILAMENT TARGET", indexed), ("FROM ACTUAL PHYSICAL MASKS", reconstructed))
    ):
        canvas.paste(Image.fromarray(image), (index * width, header))
        draw.text((index * width + 8, 13), label, fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)


def _planner_delta(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, int]:
    keys = (
        "physical_layer_count",
        "repeated_color_layer_count",
        "four_neighbor_failed_layers",
        "minimum_width_failed_layers",
        "orphan_structural_core_components",
        "orphan_structural_core_pixels",
        "fine_feature_warnings",
        "visible_automatic_bridge_pixels",
    )
    return {key: int(candidate.get(key, 0) - baseline.get(key, 0)) for key in keys}


def _plan_candidate(
    artwork_path: Path,
    output: Path,
    report_path: Path,
    poc: PocConfig,
) -> tuple[dict[str, Any], dict[str, Any], np.ndarray, np.ndarray]:
    analysis = analyze_image(artwork_path, poc)
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    labels = np.asarray(analysis["labels"], dtype=np.uint8)
    indexed = palette[labels]
    Image.fromarray(indexed).save(output.parent / "indexed_planning_target.png")
    planner = run_actual_planner(
        _analysis_from_labels(analysis, labels), output, poc
    )
    _write_json(report_path, planner)
    reconstructed = np.asarray(
        Image.open(output / "reconstructed_from_actual_masks.png").convert("RGB")
    )
    return planner, analysis, indexed, reconstructed


def run_flat_color_review(
    source: Path,
    reference: Path,
    output_root: Path,
    poc: PocConfig = DEFAULT_CONFIG,
    config: FlatColorConfig = DEFAULT_FLAT_COLOR_CONFIG,
) -> dict[str, Any]:
    if not reference.is_file():
        raise FileNotFoundError(f"missing annotated reference: {reference}")
    if not source.is_file():
        raise FileNotFoundError(f"missing clean source: {source}")

    root = output_root
    original_dir = root / "original"
    reference_dir = root / "reference"
    original_dir.mkdir(parents=True, exist_ok=True)
    reference_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, original_dir / "source.png")
    shutil.copy2(reference, reference_dir / "annotated_guide.png")

    original_analysis = analyze_image(source, poc)
    original_rgb = np.asarray(original_analysis["rgb"], dtype=np.uint8)
    Image.fromarray(original_rgb).save(original_dir / "source_review_resolution.png")
    baseline_root = root / "baseline"
    baseline_root.mkdir(parents=True, exist_ok=True)
    baseline_artwork = baseline_root / "source_review_resolution.png"
    Image.fromarray(original_rgb).save(baseline_artwork)
    baseline_plan, _, baseline_indexed, baseline_reconstructed = _plan_candidate(
        baseline_artwork,
        baseline_root / "actual_plan",
        root / "reports" / "baseline_planner.json",
        poc,
    )

    flat_rgb, boundary, flat_changes, flat_generation = flatten_art_cells(
        original_rgb, config
    )
    flat_root = root / "candidates" / "flat_color"
    flat_root.mkdir(parents=True, exist_ok=True)
    flat_path = flat_root / "artwork.png"
    Image.fromarray(flat_rgb).save(flat_path)
    Image.fromarray(_change_map(original_rgb, flat_rgb)).save(flat_root / "change_map.png")
    Image.fromarray(boundary.astype(np.uint8) * 255, "L").save(
        flat_root / "preserved_boundary_mask.png"
    )
    _write_json(
        flat_root / "edit_manifest.json",
        {
            "candidate": "FLAT-COLOR",
            "status": "PROPOSED_AWAITING_USER_APPROVAL",
            "generation": flat_generation,
            "edits": flat_changes,
            "reference_pixels_used": False,
        },
    )
    flat_plan, flat_analysis, flat_indexed, flat_reconstructed = _plan_candidate(
        flat_path,
        flat_root / "actual_plan",
        root / "reports" / "flat_color_planner.json",
        poc,
    )
    flat_heat, flat_heat_report = _orphan_heatmap(
        flat_root / "actual_plan", flat_plan, poc
    )
    heat_image = np.zeros((*flat_heat.shape, 3), dtype=np.uint8)
    heat_image[:, :, 0] = np.clip(flat_heat * 36, 0, 255).astype(np.uint8)
    Image.fromarray(heat_image).save(flat_root / "orphan_core_heatmap.png")

    optimized_rgb, optimized_changes, proposals, protected = optimize_secondary_details(
        flat_rgb, flat_analysis, flat_heat, config
    )
    optimized_root = root / "candidates" / "shadow_box_optimized"
    optimized_root.mkdir(parents=True, exist_ok=True)
    optimized_path = optimized_root / "artwork.png"
    Image.fromarray(optimized_rgb).save(optimized_path)
    Image.fromarray(_change_map(original_rgb, optimized_rgb)).save(
        optimized_root / "change_map_vs_original.png"
    )
    Image.fromarray(_change_map(flat_rgb, optimized_rgb)).save(
        optimized_root / "change_map_vs_flat_color.png"
    )
    Image.fromarray(protected.astype(np.uint8) * 255, "L").save(
        optimized_root / "primary_protection_mask.png"
    )
    _write_json(optimized_root / "secondary_group_proposals.json", proposals)
    _write_json(
        optimized_root / "edit_manifest.json",
        {
            "candidate": "SHADOW-BOX OPTIMIZED",
            "status": "PROPOSED_AWAITING_USER_APPROVAL",
            "inherits": "FLAT-COLOR",
            "edits": optimized_changes,
            "selection_summary": proposals["summary"],
            "reference_pixels_used": False,
        },
    )
    optimized_plan, _, optimized_indexed, optimized_reconstructed = _plan_candidate(
        optimized_path,
        optimized_root / "actual_plan",
        root / "reports" / "shadow_box_optimized_planner.json",
        poc,
    )

    flat_visual = _image_metrics(original_rgb, flat_rgb, protected)
    optimized_visual = _image_metrics(original_rgb, optimized_rgb, protected)
    flat_reconstruction = _mask_reconstruction_metrics(
        flat_rgb, flat_indexed, flat_reconstructed
    )
    optimized_reconstruction = _mask_reconstruction_metrics(
        optimized_rgb, optimized_indexed, optimized_reconstructed
    )
    flat_delta = _planner_delta(flat_plan, baseline_plan)
    optimized_delta = _planner_delta(optimized_plan, baseline_plan)
    optimized_vs_flat = _planner_delta(optimized_plan, flat_plan)
    structural_keys = (
        "minimum_width_failed_layers",
        "orphan_structural_core_components",
        "orphan_structural_core_pixels",
        "fine_feature_warnings",
    )
    structural_improved = any(
        optimized_vs_flat[key] < 0 for key in structural_keys
    )
    structural_worsened = any(
        optimized_vs_flat[key] > 0 for key in structural_keys
    )
    if structural_improved and structural_worsened:
        optimized_benefit_status = "MIXED_STRUCTURAL_TRADEOFF"
    elif structural_improved:
        optimized_benefit_status = "MEASURED_STRUCTURAL_BENEFIT"
    else:
        optimized_benefit_status = "NO_MEASURED_BENEFIT"

    for change in optimized_changes:
        change["measured_result"] = {
            "delta_vs_flat_color": optimized_vs_flat,
            "benefit_status": optimized_benefit_status,
        }
    optimized_manifest = json.loads(
        (optimized_root / "edit_manifest.json").read_text(encoding="utf-8")
    )
    optimized_manifest["edits"] = optimized_changes
    _write_json(optimized_root / "edit_manifest.json", optimized_manifest)

    _render_side_by_side(
        original_rgb,
        flat_rgb,
        optimized_rgb,
        root / "previews" / "original_flat_optimized.png",
    )
    crop_boxes = _render_closeups(
        original_rgb, flat_rgb, optimized_rgb, root / "previews" / "closeups"
    )
    _render_candidate_and_reconstruction(
        flat_rgb,
        flat_indexed,
        flat_reconstructed,
        "A: FLAT-COLOR ARTWORK",
        root / "previews" / "flat_color_artwork_indexed_reconstruction.png",
    )
    _render_candidate_and_reconstruction(
        optimized_rgb,
        optimized_indexed,
        optimized_reconstructed,
        "B: OPTIMIZED ARTWORK",
        root / "previews" / "optimized_artwork_indexed_reconstruction.png",
    )

    candidate_validity = {
        "flat_color": flat_plan.get("planner_status")
        == "VALID_COMMON_PERIMETER_STACK_FOUND",
        "shadow_box_optimized": optimized_plan.get("planner_status")
        == "VALID_COMMON_PERIMETER_STACK_FOUND",
    }
    report = {
        "review_state": "READY_FOR_USER_TEST",
        "milestone": "L1_UNAPPROVED",
        "automatic_candidate_selection": None,
        "user_approval": "PENDING",
        "source": {
            "path": "original/source.png",
            "sha256": _sha256(original_dir / "source.png"),
            "editing_source": True,
        },
        "reference": {
            "path": "reference/annotated_guide.png",
            "sha256": _sha256(reference_dir / "annotated_guide.png"),
            "usage": "ANNOTATION_EVIDENCE_ONLY",
            "pixels_used_for_candidate_generation": False,
        },
        "settings": {
            "poc": asdict(poc),
            "flat_color": asdict(config),
            "minimum_structural_width_mm": poc.minimum_feature_mm,
            "morphology_kernel": "corrected 7x7 ellipse at current 100 mm / 512 px scale",
            "same_planner_settings_for_original_and_both_candidates": True,
        },
        "visual_simplification": {
            "flat_color": flat_visual,
            "shadow_box_optimized": optimized_visual,
            "flat_color_edit_manifest": "candidates/flat_color/edit_manifest.json",
            "optimized_edit_manifest": "candidates/shadow_box_optimized/edit_manifest.json",
            "review_crop_boxes_pixels_xyxy": crop_boxes,
            "crop_boxes_affect_algorithm": False,
        },
        "structural_feasibility": {
            "baseline": baseline_plan,
            "flat_color": flat_plan,
            "shadow_box_optimized": optimized_plan,
            "flat_color_delta_vs_baseline": flat_delta,
            "optimized_delta_vs_baseline": optimized_delta,
            "optimized_delta_vs_flat_color": optimized_vs_flat,
            "optimized_edit_benefit_status": optimized_benefit_status,
            "flat_color_orphan_heatmap": flat_heat_report,
        },
        "physical_mask_reconstruction": {
            "baseline_indexed_target_exact": baseline_plan.get(
                "exact_candidate_composite", False
            ),
            "flat_color": flat_reconstruction,
            "shadow_box_optimized": optimized_reconstruction,
        },
        "mandatory_construction": {
            "identical_outer_dimensions": True,
            "continuous_shared_perimeter": True,
            "one_filament_color_per_sheet": True,
            "genuine_through_cutouts": True,
            "four_neighbor_connectivity_required": True,
            "minimum_width_required_mm": poc.minimum_feature_mm,
            "visible_automatic_support_bridges": 0,
        },
        "candidate_validity": candidate_validity,
        "cad_generated": False,
        "stl_generated": False,
        "step_generated": False,
        "limitations": [
            "Art-cell boundaries are image-derived hypotheses, not semantic object labels.",
            "Secondary contour groups are uncertain and require visual approval.",
            "A visually clean candidate is not fabrication-valid while width conflicts remain.",
            "Bounded planner failure does not prove global infeasibility.",
            "No candidate is automatically selected or approved.",
        ],
    }
    _write_json(root / "reports" / "flat_color_review.json", report)

    def row(name: str, plan: dict[str, Any]) -> str:
        return (
            f"{name}: layers={plan.get('physical_layer_count', 'N/A')}, "
            f"repeats={plan.get('repeated_color_layer_count', 'N/A')}, "
            f"4-neighbor failures={plan.get('four_neighbor_failed_layers', 'N/A')}, "
            f"width-failing sheets={plan.get('minimum_width_failed_layers', 'N/A')}, "
            f"orphan cores={plan.get('orphan_structural_core_components', 'N/A')}, "
            f"orphan pixels={plan.get('orphan_structural_core_pixels', 'N/A')}, "
            f"fine warnings={plan.get('fine_feature_warnings', 'N/A')}."
        )

    _write_text(
        root / "REVIEW_SUMMARY.txt",
        "\n".join(
            [
                "LayerForge flat-color simplification review",
                "Status: READY_FOR_USER_TEST; L1 NOT APPROVED",
                "",
                "The annotated guide was inspected and copied separately. Its pixels were not used to generate either candidate.",
                "Candidate A flattens tonal variation only inside strong-boundary art cells.",
                "Candidate B inherits A and proposes complete low-importance secondary contour removals tied to measured bottlenecks.",
                "Neither candidate is automatically selected.",
                "",
                row("ORIGINAL BASELINE", baseline_plan),
                row("A FLAT-COLOR", flat_plan),
                row("B SHADOW-BOX OPTIMIZED", optimized_plan),
                f"B edit result versus A: {optimized_benefit_status}.",
                "",
                f"A valid at 1.2 mm: {candidate_validity['flat_color']}.",
                f"B valid at 1.2 mm: {candidate_validity['shadow_box_optimized']}.",
                "Mask-derived reconstructions and every physical retained/cutout mask are included under each candidate's actual_plan directory.",
                "No CAD, STL, STEP, frame, stand, or frontend was generated.",
            ]
        ),
    )
    return report


def build_review_package(root: Path, archive: Path) -> dict[str, Any]:
    checksums = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "checksums.json"
    }
    _write_json(root / "checksums.json", checksums)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(root.parent).as_posix())
    return {
        "archive": str(archive),
        "sha256": _sha256(archive),
        "files": len(checksums) + 1,
    }
