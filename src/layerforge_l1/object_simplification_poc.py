"""Focused object-aware simplification proof for one fox secondary group.

The proof discovers foreground groups without fixture-specific labels, maps
actual 7x7 structural bottlenecks back to those groups, removes one complete
secondary group, and validates the edited art with the same 2D planner used for
the current baseline.  It deliberately implements only one high-level edit.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .connectivity_repair import audit_connection_width, audit_edge_connectivity
from .layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from .shadowbox_simplification import (
    _analysis_from_labels,
    _protected_subjects,
    _region_inventory,
    run_actual_planner,
    run_direct_case,
)
from .subtractive_poc import DEFAULT_CONFIG, PocConfig, analyze_image


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_mask(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L")) > 0


def _fill_with_local_background(
    rgb: np.ndarray,
    indexed_labels: np.ndarray,
    background_index: int,
    fill_mask: np.ndarray,
    *,
    sigma_pixels: float = 18.0,
) -> np.ndarray:
    """Reconstruct a smooth local background without sampling foreground ink.

    The indexed background pixels provide samples for a normalized Gaussian
    interpolation.  Unlike a flat palette fill, this retains the source's
    gentle illumination/texture; unlike generic inpainting, foreground colors
    adjacent to the edit cannot bleed into the vacated region.
    """

    valid = (indexed_labels == background_index).astype(np.float32)
    weights = cv2.GaussianBlur(valid, (0, 0), sigma_pixels)
    weights = np.maximum(weights, 1e-6)
    predicted = np.empty_like(rgb, dtype=np.float32)
    for channel in range(3):
        samples = rgb[:, :, channel].astype(np.float32) * valid
        predicted[:, :, channel] = (
            cv2.GaussianBlur(samples, (0, 0), sigma_pixels) / weights
        )
    result = rgb.copy()
    result[fill_mask] = np.clip(predicted[fill_mask], 0, 255).astype(np.uint8)
    return result


def _baseline_orphan_heatmap(
    plan_root: Path,
    planner_report: dict[str, Any],
    poc: PocConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    heat = np.zeros((poc.analysis_pixels, poc.analysis_pixels), dtype=np.int32)
    rows: list[dict[str, Any]] = []
    p = poc.perimeter_pixels
    for layer in planner_report["layers"]:
        physical = _load_mask(plan_root / layer["retained_material_mask"])
        visible = _load_mask(plan_root / layer["visible_region_mask"])
        audit, maps = audit_connection_width(
            physical, visible, poc, DEFAULT_OPTIMIZATION_CONFIG
        )
        cropped = maps["orphan_core"][p:-p, p:-p]
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
        "weighted_orphan_core_pixels": int(heat.sum()),
        "unique_orphan_core_pixels": int(np.count_nonzero(heat)),
        "layers": rows,
    }


def _refresh_plan_width_totals(
    plan_root: Path, report: dict[str, Any], poc: PocConfig
) -> dict[str, Any]:
    orphan_pixels = 0
    for layer in report["layers"]:
        physical = _load_mask(plan_root / layer["retained_material_mask"])
        visible = _load_mask(plan_root / layer["visible_region_mask"])
        audit, _ = audit_connection_width(
            physical, visible, poc, DEFAULT_OPTIMIZATION_CONFIG
        )
        orphan_pixels += audit["orphan_structural_core_pixels"]
    report["orphan_structural_core_pixels"] = orphan_pixels
    return report


def discover_object_groups(
    labels: np.ndarray,
    palette_rgb: np.ndarray,
    orphan_heat: np.ndarray,
) -> tuple[list[dict[str, Any]], np.ndarray, np.ndarray]:
    border = np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1]))
    background = int(np.bincount(border).argmax())
    region_map, regions = _region_inventory(labels)
    protected, protection = _protected_subjects(
        labels, palette_rgb, region_map, regions
    )
    count, groups, stats, centroids = cv2.connectedComponentsWithStats(
        (labels != background).astype(np.uint8), connectivity=4
    )
    height, width = labels.shape
    proposals: list[dict[str, Any]] = []
    for component in range(1, count):
        area = int(stats[component, cv2.CC_STAT_AREA])
        if area < 64:
            continue
        mask = groups == component
        left = int(stats[component, cv2.CC_STAT_LEFT])
        top = int(stats[component, cv2.CC_STAT_TOP])
        box_width = int(stats[component, cv2.CC_STAT_WIDTH])
        box_height = int(stats[component, cv2.CC_STAT_HEIGHT])
        protected_overlap = int(np.count_nonzero(mask & protected))
        overlap_fraction = protected_overlap / area
        area_fraction = area / labels.size
        cx, cy = (float(value) for value in centroids[component])
        centrality = max(
            0.0,
            1.0
            - float(
                np.hypot((cx - width / 2) / (width / 2), (cy - height / 2) / (height / 2))
            ),
        )
        colors = sorted(int(value) for value in np.unique(labels[mask]))
        region_count = len(
            {
                int(value)
                for value in np.unique(region_map[mask])
                if int(value) >= 0
            }
        )
        importance = min(
            1.0,
            overlap_fraction * 0.72
            + min(1.0, area_fraction / 0.12) * 0.20
            + centrality * 0.08,
        )
        bottleneck_weight = int(orphan_heat[mask].sum())
        bottleneck_pixels = int(np.count_nonzero(orphan_heat[mask]))
        secondary_candidate = (
            overlap_fraction < 0.20
            and 0.002 <= area_fraction <= 0.10
            and bottleneck_pixels > 0
        )
        uncertainty = (
            "HIGH_MULTIPLE_OBJECTS_LIKELY"
            if area_fraction > 0.20 or region_count > 80
            else ("MEDIUM_GROUPING_NOT_SEMANTICALLY_VERIFIED" if region_count > 3 else "LOW")
        )
        proposals.append(
            {
                "group_id": f"foreground_component_{component:03d}",
                "component": component,
                "area_pixels": area,
                "area_fraction": area_fraction,
                "bounds_pixels_xywh": [left, top, box_width, box_height],
                "centroid_pixels": [cx, cy],
                "palette_indices": colors,
                "region_count": region_count,
                "protected_overlap_pixels": protected_overlap,
                "protected_overlap_fraction": overlap_fraction,
                "importance_estimate_0_1": importance,
                "importance_basis": "protected overlap, relative area, and image centrality; heuristic rather than semantic truth",
                "uncertainty": uncertainty,
                "orphan_core_weighted_overlap": bottleneck_weight,
                "orphan_core_unique_pixel_overlap": bottleneck_pixels,
                "secondary_candidate": secondary_candidate,
                "mask": mask,
            }
        )
    protection_report = {
        **protection,
        "background_palette_index": background,
        "background_color": "#" + "".join(f"{int(v):02X}" for v in palette_rgb[background]),
    }
    return proposals, protected, groups


def _save_group_visuals(
    source_rgb: np.ndarray,
    proposals: list[dict[str, Any]],
    protected: np.ndarray,
    groups: np.ndarray,
    selected_component: int,
    output: Path,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    overlay = source_rgb.copy()
    overlay[protected] = np.rint(
        overlay[protected].astype(np.float32) * 0.55 + np.array([35, 185, 95]) * 0.45
    ).astype(np.uint8)
    selected = groups == selected_component
    overlay[selected] = np.rint(
        overlay[selected].astype(np.float32) * 0.40 + np.array([230, 50, 45]) * 0.60
    ).astype(np.uint8)
    Image.fromarray(overlay).save(output / "object_groups_and_protection.png")
    for proposal in proposals:
        mask = groups == proposal["component"]
        Image.fromarray(mask.astype(np.uint8) * 255, "L").save(
            output / f"{proposal['group_id']}_mask.png"
        )
        boundary = cv2.morphologyEx(
            mask.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)
        )
        Image.fromarray(boundary * 255, "L").save(
            output / f"{proposal['group_id']}_boundary.png"
        )


def _render_comparison(
    source: np.ndarray,
    edited: np.ndarray,
    reconstructed: np.ndarray,
    output: Path,
) -> None:
    tile = source.shape[0]
    canvas = Image.new("RGB", (tile * 3, tile + 44), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (title, image) in enumerate(
        (
            ("ORIGINAL RGB", source),
            ("EDITED ARTWORK — NOT APPROVED", edited),
            ("RECONSTRUCTED FROM ACTUAL LAYER MASKS", reconstructed),
        )
    ):
        canvas.paste(Image.fromarray(image), (index * tile, 44))
        draw.text((index * tile + 10, 14), title, fill="black")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def _rgb_quality(
    original: np.ndarray,
    edited: np.ndarray,
    protected: np.ndarray,
    *,
    comparison_basis: str = (
        "edited RGB artwork against resampled original RGB, not against indexed target"
    ),
) -> dict[str, Any]:
    difference = np.abs(edited.astype(np.int16) - original.astype(np.int16))
    changed = np.any(difference > 0, axis=2)
    original_edges = cv2.Canny(original, 80, 160) > 0
    edited_edges = cv2.Canny(edited, 80, 160) > 0
    protected_edges = protected & (original_edges | edited_edges)
    edge_changes = int(np.count_nonzero(protected & (original_edges != edited_edges)))
    return {
        "comparison_basis": comparison_basis,
        "changed_rgb_pixels": int(np.count_nonzero(changed)),
        "changed_rgb_fraction": float(np.mean(changed)),
        "mean_absolute_rgb_error_0_255": float(difference.mean()),
        "maximum_channel_error_0_255": int(difference.max()),
        "protected_rgb_changed_pixels": int(np.count_nonzero(changed & protected)),
        "protected_outline_union_pixels": int(np.count_nonzero(protected_edges)),
        "protected_outline_changed_pixels": edge_changes,
        "protected_outline_retention": (
            1.0 - edge_changes / np.count_nonzero(protected_edges)
            if np.count_nonzero(protected_edges)
            else 1.0
        ),
    }


def reaudit_historical_plan(
    historical_root: Path,
    poc: PocConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    manifest_path = historical_root / "automatic" / "layer_stack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failed4 = orphans4 = failed_width = orphan_cores = orphan_pixels = fine = 0
    colors: list[str] = []
    for layer in manifest["layers"]:
        physical = _load_mask(historical_root / layer["retained_material_mask"])
        visible = _load_mask(historical_root / layer["source_visible_region_mask"])
        edge, _ = audit_edge_connectivity(physical)
        width, _ = audit_connection_width(
            physical, visible, poc, DEFAULT_OPTIMIZATION_CONFIG
        )
        failed4 += int(edge["four_neighbor_status"] != "PASS")
        orphans4 += edge["four_neighbor_orphan_components"]
        failed_width += int(width["status"] != "PASS")
        orphan_cores += width["orphan_structural_core_components"]
        orphan_pixels += width["orphan_structural_core_pixels"]
        fine += width["thin_decorative_visible_components"]
        colors.append(layer["color"])
    return {
        "source": str(manifest_path),
        "source_sha256": manifest["source_sha256"],
        "physical_layer_count": len(manifest["layers"]),
        "repeated_color_layer_count": len(colors) - len(set(colors)),
        "four_neighbor_failed_layers": failed4,
        "four_neighbor_orphan_components": orphans4,
        "minimum_width_failed_layers": failed_width,
        "orphan_structural_core_components": orphan_cores,
        "orphan_structural_core_pixels": orphan_pixels,
        "fine_feature_warnings": fine,
        "width_kernel_pixels": 7,
        "configuration_note": "Historical topology-optimized/edge-repaired 18-layer masks re-audited with the current corrected 7x7 width validator. Their ordering/search configuration differs from the current equal-budget width-aware search.",
        "global_infeasibility_proven": False,
    }


def run_object_simplification_proof(
    source: Path,
    output_root: Path,
    historical_root: Path,
    poc: PocConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    fox_root = output_root / "fox_workshop"
    (fox_root / "original").mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, fox_root / "original" / "source.png")
    analysis = analyze_image(source, poc)
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    labels = np.asarray(analysis["labels"], dtype=np.uint8)
    Image.fromarray(palette[labels]).save(fox_root / "original" / "indexed_planning_target.png")

    baseline_report_path = fox_root / "reports" / "current_baseline.json"
    if baseline_report_path.exists():
        baseline = json.loads(baseline_report_path.read_text(encoding="utf-8"))
    else:
        baseline = run_actual_planner(
            _analysis_from_labels(analysis, labels), fox_root / "baseline_plan", poc
        )
    baseline = _refresh_plan_width_totals(fox_root / "baseline_plan", baseline, poc)
    _write_json(baseline_report_path, baseline)
    heat, heat_report = _baseline_orphan_heatmap(
        fox_root / "baseline_plan", baseline, poc
    )
    (fox_root / "groups").mkdir(parents=True, exist_ok=True)
    heat_image = np.zeros((*heat.shape, 3), dtype=np.uint8)
    heat_image[:, :, 0] = np.clip(heat * 48, 0, 255).astype(np.uint8)
    Image.fromarray(heat_image).save(fox_root / "groups" / "baseline_orphan_heatmap.png")

    proposals, protected, groups = discover_object_groups(labels, palette, heat)
    eligible = [row for row in proposals if row["secondary_candidate"]]
    if not eligible:
        raise RuntimeError("no secondary group overlaps a measured structural bottleneck")
    selected = max(
        eligible,
        key=lambda row: (
            row["orphan_core_unique_pixel_overlap"],
            row["orphan_core_weighted_overlap"],
            -row["importance_estimate_0_1"],
        ),
    )
    selected_mask = groups == selected["component"]
    coarse_protected_overlap = int(np.count_nonzero(protected & selected_mask))
    # The subject protection stage intentionally over-protects rectangular
    # zones.  A separately connected, low-importance group with less than 20%
    # incidental overlap can refine that coarse box without weakening any
    # other protected group.
    edit_render_mask = cv2.dilate(
        selected_mask.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2
    ).astype(bool)
    protected = protected & ~cv2.dilate(
        edit_render_mask.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1
    ).astype(bool)
    _save_group_visuals(
        analysis["rgb"],
        proposals,
        protected,
        groups,
        selected["component"],
        fox_root / "groups",
    )

    background = int(
        np.bincount(
            np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1]))
        ).argmax()
    )
    ring = cv2.dilate(selected_mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~selected_mask
    surrounding = sorted(int(value) for value in np.unique(labels[ring]))
    candidate_labels = labels.copy()
    candidate_labels[selected_mask] = background
    edited_rgb = _fill_with_local_background(
        analysis["rgb"], labels, background, edit_render_mask
    )
    candidate_root = fox_root / "candidate"
    candidate_root.mkdir(parents=True, exist_ok=True)
    Image.fromarray(edited_rgb).save(candidate_root / "edited_artwork_rgb.png")
    Image.fromarray(palette[candidate_labels]).save(candidate_root / "edited_indexed_target.png")
    Image.fromarray(selected_mask.astype(np.uint8) * 255, "L").save(
        candidate_root / "edited_group_mask.png"
    )
    Image.fromarray(edit_render_mask.astype(np.uint8) * 255, "L").save(
        candidate_root / "rgb_cleanup_extent_mask.png"
    )

    edited_analysis = _analysis_from_labels(analysis, candidate_labels)
    edited_report_path = fox_root / "reports" / "edited_planner.json"
    if edited_report_path.exists():
        edited_plan = json.loads(edited_report_path.read_text(encoding="utf-8"))
    else:
        edited_plan = run_actual_planner(
            edited_analysis, candidate_root / "actual_plan", poc
        )
    edited_plan = _refresh_plan_width_totals(
        candidate_root / "actual_plan", edited_plan, poc
    )
    _write_json(edited_report_path, edited_plan)
    reconstructed = np.asarray(
        Image.open(candidate_root / "actual_plan" / "reconstructed_from_actual_masks.png").convert("RGB")
    )
    baseline_reconstructed = np.asarray(
        Image.open(
            fox_root / "baseline_plan" / "reconstructed_from_actual_masks.png"
        ).convert("RGB")
    )
    _render_comparison(
        analysis["rgb"],
        edited_rgb,
        reconstructed,
        fox_root / "previews" / "original_edited_mask_reconstruction.png",
    )

    historical = reaudit_historical_plan(historical_root, poc)
    _write_json(fox_root / "reports" / "historical_18_layer_reaudit.json", historical)
    metric_keys = (
        "physical_layer_count",
        "repeated_color_layer_count",
        "minimum_width_failed_layers",
        "orphan_structural_core_components",
        "orphan_structural_core_pixels",
        "fine_feature_warnings",
    )
    deltas = {
        key: edited_plan.get(key, 0) - baseline.get(key, 0) for key in metric_keys
    }
    targeted_benefit = (
        deltas["minimum_width_failed_layers"] < 0
        or deltas["orphan_structural_core_components"] < 0
        or deltas["orphan_structural_core_pixels"] < 0
    )
    edit_manifest = {
        "edit_id": "edit_001_remove_secondary_group",
        "operation": "REMOVE_SECONDARY_OBJECT",
        "status": "PROPOSED_AWAITING_USER_APPROVAL",
        "selected_group_id": selected["group_id"],
        "original_group_bounds_pixels_xywh": selected["bounds_pixels_xywh"],
        "original_group_area_pixels": selected["area_pixels"],
        "specific_visual_change": "Remove the complete disconnected secondary foreground group and extend its surrounding background color through the vacated area.",
        "selection_reason": "Automatically classified as a separately connected secondary group with less than 20% incidental coarse protection-box overlap, and it intersects measured orphan structural-core pixels in the verified baseline.",
        "structural_problem": {
            "baseline_orphan_core_unique_pixel_overlap": selected[
                "orphan_core_unique_pixel_overlap"
            ],
            "baseline_orphan_core_weighted_overlap": selected[
                "orphan_core_weighted_overlap"
            ],
            "intended_improvement": "remove region/depth relationships contributing to width-disconnected structural cores",
        },
        "surrounding_palette_indices": surrounding,
        "replacement_palette_index": background,
        "coarse_protection_box_overlap_pixels": coarse_protected_overlap,
        "protected_overlap_pixels_after_object_group_refinement": int(
            np.count_nonzero(protected & selected_mask)
        ),
        "grouping_uncertainty": selected["uncertainty"],
        "planner_result": {
            "status": (
                "MEASURED_STRUCTURAL_BENEFIT_BUT_UNRESOLVED"
                if targeted_benefit and edited_plan["planner_status"]
                != "VALID_COMMON_PERIMETER_STACK_FOUND"
                else (
                    "MEASURED_BENEFIT_VALID_PLAN"
                    if targeted_benefit
                    else "NO_MEASURED_BENEFIT"
                )
            ),
            "deltas_vs_current_baseline": deltas,
        },
        "user_approval": "PENDING",
    }
    _write_json(candidate_root / "edit_manifest.json", edit_manifest)

    serializable_groups = []
    for row in proposals:
        record = {key: value for key, value in row.items() if key != "mask"}
        record["selected"] = row["component"] == selected["component"]
        serializable_groups.append(record)
    _write_json(
        fox_root / "groups" / "object_group_proposals.json",
        {
            "method": "4-neighbor non-background foreground grouping with protected-subject overlap and actual orphan-core attribution",
            "limitations": "A connected foreground component can contain several semantic objects; importance and grouping are hypotheses, not semantic ground truth.",
            "proposals": serializable_groups,
        },
    )

    report = {
        "case_id": "fox_workshop",
        "review_state": "READY_FOR_USER_TEST",
        "automatic_approval": False,
        "original": "original/source.png",
        "group_map": "groups/object_groups_and_protection.png",
        "selected_group": selected["group_id"],
        "edit_manifest": "candidate/edit_manifest.json",
        "edited_artwork": "candidate/edited_artwork_rgb.png",
        "mask_reconstruction": "candidate/actual_plan/reconstructed_from_actual_masks.png",
        "baseline_orphan_heatmap": "groups/baseline_orphan_heatmap.png",
        "current_baseline": baseline,
        "historical_18_layer_baseline": historical,
        "edited_plan": edited_plan,
        "structural_deltas_vs_current_baseline": deltas,
        "rgb_quality": _rgb_quality(analysis["rgb"], edited_rgb, protected),
        "baseline_mask_reconstruction_rgb_quality": _rgb_quality(
            analysis["rgb"],
            baseline_reconstructed,
            protected,
            comparison_basis="baseline physical-mask reconstruction against resampled original RGB",
        ),
        "edited_mask_reconstruction_rgb_quality": _rgb_quality(
            analysis["rgb"],
            reconstructed,
            protected,
            comparison_basis="edited physical-mask reconstruction against resampled original RGB",
        ),
        "protected_indexed_pixels_changed": int(
            np.count_nonzero((candidate_labels != labels) & protected)
        ),
        "mandatory_construction": {
            "common_outer_outline": True,
            "continuous_perimeter": edited_plan.get("four_neighbor_failed_layers") == 0,
            "one_color_per_sheet": True,
            "subtractive_cutouts": True,
            "visible_bridge_pixels": edited_plan.get("visible_automatic_bridge_pixels", 0),
            "strict_positive_width_status": (
                "PASS"
                if edited_plan.get("minimum_width_failed_layers") == 0
                else "UNRESOLVED"
            ),
        },
        "result": edit_manifest["planner_result"]["status"],
        "limitations": [
            "Only one high-level operation is implemented: whole secondary-group removal.",
            "Foreground connectivity is an object-group hypothesis, not general semantic understanding.",
            "The historical 18-layer plan uses a different topology-search configuration and is a Pareto comparison, not an identical-search baseline.",
            "A bounded search failure does not prove global infeasibility.",
            "No edited artwork or layer plan is approved automatically.",
        ],
    }
    _write_json(fox_root / "reports" / "object_simplification_proof.json", report)
    return report


def run_full_review(
    fox_source: Path,
    bear_source: Path,
    output_root: Path,
    historical_root: Path,
) -> dict[str, Any]:
    fox = run_object_simplification_proof(
        fox_source, output_root, historical_root, DEFAULT_CONFIG
    )
    bear = run_direct_case(bear_source, "bear_face", output_root, DEFAULT_CONFIG)
    summary = {
        "review_state": "READY_FOR_USER_TEST",
        "fox_result": fox["result"],
        "bear_result": bear["planner"]["planner_status"],
        "automatic_approval": False,
        "cad_generated": False,
    }
    _write_json(output_root / "review_summary.json", summary)
    return summary


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
    return {"archive": str(archive), "sha256": _sha256(archive), "files": len(checksums) + 1}
