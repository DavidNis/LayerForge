"""Controlled, geometry-first simplification for shadow-box planning.

Candidates begin from the same high-resolution indexed target.  The primary
subjects and their internal marks are protected; only explicit connected
regions outside those zones may be merged.  Every candidate is subsequently
run through the actual common-perimeter subtractive planner.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .connectivity_repair import audit_connection_width, audit_edge_connectivity
from .layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from .subtractive_poc import (
    DEFAULT_CONFIG,
    PocConfig,
    _compose,
    _provider_regions,
    analyze_image,
    construct_layers,
    discover_order,
    validate_plan,
)


def _progress(stage: str, **details: object) -> None:
    print(
        json.dumps(
            {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "stage": stage,
                **details,
            }
        ),
        flush=True,
    )
from .width_aware_planner import (
    WidthAwareSearchConfig,
    _materialize_plan,
    plan_width_aware,
)


@dataclass(frozen=True)
class CandidatePolicy:
    name: str
    remove_area_pixels: int
    similar_merge_area_pixels: int
    maximum_similar_delta_lab: float
    description: str


POLICIES = (
    CandidatePolicy(
        "LIGHT",
        36,
        120,
        7.0,
        "Only insignificant unprotected specks and nearly identical adjacent regions.",
    ),
    CandidatePolicy(
        "BALANCED",
        90,
        480,
        14.0,
        "Secondary background marks and similar-color regions; primary subjects remain locked.",
    ),
    CandidatePolicy(
        "AGGRESSIVE",
        220,
        1400,
        22.0,
        "Broader background and secondary-object cleanup while retaining protected subjects and major silhouettes.",
    ),
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _region_inventory(labels: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
    region_map = np.full(labels.shape, -1, dtype=np.int32)
    regions: list[dict[str, Any]] = []
    height, width = labels.shape
    for color in sorted(int(value) for value in np.unique(labels)):
        count, components, stats, centroids = cv2.connectedComponentsWithStats(
            (labels == color).astype(np.uint8), connectivity=4
        )
        for component in range(1, count):
            index = len(regions)
            region_map[components == component] = index
            left = int(stats[component, cv2.CC_STAT_LEFT])
            top = int(stats[component, cv2.CC_STAT_TOP])
            region_width = int(stats[component, cv2.CC_STAT_WIDTH])
            region_height = int(stats[component, cv2.CC_STAT_HEIGHT])
            regions.append(
                {
                    "index": index,
                    "id": f"palette_{color:02d}_component_{component:03d}",
                    "palette_index": color,
                    "area_pixels": int(stats[component, cv2.CC_STAT_AREA]),
                    "bounds_pixels": [left, top, region_width, region_height],
                    "centroid_pixels": [float(centroids[component, 0]), float(centroids[component, 1])],
                    "touches_border": left == 0 or top == 0 or left + region_width == width or top + region_height == height,
                }
            )
    if np.any(region_map < 0):
        raise RuntimeError("region inventory left pixels unassigned")
    return region_map, regions


def _protected_subjects(
    labels: np.ndarray, palette_rgb: np.ndarray, region_map: np.ndarray, regions: list[dict[str, Any]]
) -> tuple[np.ndarray, dict[str, Any]]:
    """Find primary colorful subject zones without semantic or fixture rules."""

    hsv = cv2.cvtColor(palette_rgb.reshape(1, -1, 3), cv2.COLOR_RGB2HSV).reshape(-1, 3)
    height, width = labels.shape
    candidates: list[tuple[float, dict[str, Any]]] = []
    for region in regions:
        if region["touches_border"] or region["area_pixels"] < labels.size * 0.004:
            continue
        color = region["palette_index"]
        saturation = float(hsv[color, 1]) / 255.0
        value = float(hsv[color, 2]) / 255.0
        left, top, region_width, region_height = region["bounds_pixels"]
        box_fraction = region_width * region_height / labels.size
        # Sprawling outline networks can cover the whole scene and are not a
        # useful subject-localization anchor.  They remain protected later if
        # they are a major region, but do not define a full-canvas lock box.
        if saturation < 0.28 or value < 0.35 or box_fraction > 0.45:
            continue
        cx, cy = region["centroid_pixels"]
        centrality = 1.0 - min(1.0, np.hypot((cx - width / 2) / width, (cy - height * 0.56) / height))
        score = region["area_pixels"] * (0.7 + saturation) * (0.7 + centrality)
        candidates.append((score, region))
    candidates.sort(key=lambda item: (-item[0], item[1]["index"]))

    boxes: list[list[int]] = []
    anchors: list[str] = []
    for _, region in candidates:
        # Subject zones are deliberately over-inclusive: every sufficiently
        # large colorful anchor may protect its surrounding internal details.
        # This is safer than allowing two colors of one character to consume a
        # small fixed quota and leaving a second character's face unprotected.
        if region["area_pixels"] < labels.size * 0.01:
            continue
        left, top, box_width, box_height = region["bounds_pixels"]
        margin = max(12, int(max(box_width, box_height) * 0.24))
        box = [
            max(0, left - margin),
            max(0, top - margin),
            min(width, left + box_width + margin),
            min(height, top + box_height + margin),
        ]
        # A large colorful anchor in the lower half usually belongs to a
        # foreground subject body.  Extend its protection through the feet so
        # local detail cleanup cannot nibble the primary silhouette.
        if region["centroid_pixels"][1] > height * 0.45:
            box[3] = height
        overlap = False
        for prior in boxes:
            intersection = max(0, min(box[2], prior[2]) - max(box[0], prior[0])) * max(
                0, min(box[3], prior[3]) - max(box[1], prior[1])
            )
            smaller = min((box[2] - box[0]) * (box[3] - box[1]), (prior[2] - prior[0]) * (prior[3] - prior[1]))
            if smaller and intersection / smaller > 0.35:
                overlap = True
                break
        if not overlap:
            boxes.append(box)
            anchors.append(region["id"])
        if len(boxes) == 6:
            break

    protected = np.zeros(labels.shape, dtype=bool)
    for left, top, right, bottom in boxes:
        protected[top:bottom, left:right] = True
    # Major regions are protected regardless of location.  This locks large
    # silhouettes/background relationships while allowing local clutter edits.
    for region in regions:
        if region["area_pixels"] >= labels.size * 0.012:
            protected |= region_map == region["index"]
    protected_regions = [
        region["id"] for region in regions if np.any(protected & (region_map == region["index"]))
    ]
    return protected, {
        "method": "up to six non-redundant large high-saturation subject anchors plus all major regions; anchor boxes protect enclosed facial/accessory detail",
        "anchor_region_ids": anchors,
        "protected_boxes_pixels_xyxy": boxes,
        "protected_region_ids": protected_regions,
        "protected_pixel_count": int(np.count_nonzero(protected)),
    }


def _lab_palette(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb.reshape(1, -1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)


def _neighbor_choice(
    labels: np.ndarray,
    mask: np.ndarray,
    source_color: int,
    palette_lab: np.ndarray,
) -> tuple[int | None, int, float]:
    ring = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~mask
    neighbors = labels[ring]
    neighbors = neighbors[neighbors != source_color]
    if not neighbors.size:
        return None, 0, float("inf")
    values, counts = np.unique(neighbors, return_counts=True)
    ranked = sorted(
        zip(values.tolist(), counts.tolist()),
        key=lambda row: (-row[1], float(np.linalg.norm(palette_lab[source_color] - palette_lab[row[0]])), row[0]),
    )
    replacement, contact = ranked[0]
    delta = float(np.linalg.norm(palette_lab[source_color] - palette_lab[replacement]))
    return int(replacement), int(contact), delta


def generate_candidate(
    original_labels: np.ndarray,
    palette_rgb: np.ndarray,
    policy: CandidatePolicy,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, Any]]:
    region_map, regions = _region_inventory(original_labels)
    protected, protection = _protected_subjects(
        original_labels, palette_rgb, region_map, regions
    )
    palette_lab = _lab_palette(palette_rgb)
    candidate = original_labels.copy()
    changes: list[dict[str, Any]] = []
    for region in sorted(regions, key=lambda row: (row["area_pixels"], row["index"])):
        original_mask = region_map == region["index"]
        protected_overlap = int(np.count_nonzero(original_mask & protected))
        if protected_overlap:
            continue
        current_mask = original_mask & (candidate == region["palette_index"])
        if not np.any(current_mask):
            continue
        replacement, contact, delta = _neighbor_choice(
            candidate, current_mask, region["palette_index"], palette_lab
        )
        if replacement is None:
            continue
        operation: str | None = None
        reason: str | None = None
        intended_constraint: str | None = None
        if region["area_pixels"] <= policy.remove_area_pixels:
            operation = "REMOVE_TINY_UNPROTECTED_DECORATION"
            reason = "Region is below this candidate's area permission and lies outside protected subject zones."
            intended_constraint = "Reduce fine-feature warnings and avoid a provider depth justified only by insignificant decoration."
        elif (
            region["area_pixels"] <= policy.similar_merge_area_pixels
            and delta <= policy.maximum_similar_delta_lab
        ):
            operation = "MERGE_ADJACENT_SIMILAR_COLOR_REGION"
            reason = "Region is secondary, adjacent to a perceptually similar color, and outside protected subject zones."
            intended_constraint = "Reduce region/depth complexity without changing primary silhouettes or facial features."
        if operation is None:
            continue
        candidate[current_mask] = replacement
        changes.append(
            {
                "original_region_id": region["id"],
                "original_palette_index": region["palette_index"],
                "replacement_palette_index": replacement,
                "original_area_pixels": region["area_pixels"],
                "changed_pixels": int(np.count_nonzero(current_mask)),
                "bounds_pixels": region["bounds_pixels"],
                "operation": operation,
                "specific_visual_change": "The complete small connected region adopts the dominant adjacent artwork color.",
                "why_selected": reason,
                "intended_shadow_box_constraint": intended_constraint,
                "shared_boundary_contact_pixels": contact,
                "palette_delta_lab": delta,
                "protected_feature_overlap_pixels": 0,
                "measured_result_ref": f"reports/{policy.name.lower()}_planner.json",
            }
        )
    changed = candidate != original_labels
    return candidate, changes, {
        "policy": asdict(policy),
        "protection": protection,
        "changed_pixels": int(np.count_nonzero(changed)),
        "changed_fraction": float(np.mean(changed)),
        "protected_changed_pixels": int(np.count_nonzero(changed & protected)),
        "change_count": len(changes),
    }


def _analysis_from_labels(base: dict[str, Any], labels: np.ndarray) -> dict[str, Any]:
    palette = np.asarray(base["palette_rgb"], dtype=np.uint8)
    provider_labels, providers, splits = _provider_regions(labels, palette)
    return {
        **base,
        "labels": labels,
        "counts": np.bincount(labels.ravel(), minlength=len(palette)),
        "provider_labels": provider_labels,
        "providers": providers,
        "provider_splits": splits,
    }


def _save_mask(mask: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255, "L").save(path)


def run_actual_planner(
    analysis: dict[str, Any],
    output: Path,
    poc: PocConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    _progress(
        "L1_2D_PLANNER_ENTERED",
        indexed_colors=len(analysis["palette_rgb"]),
        greedy_provider_groups=len(analysis["providers"]),
    )
    baseline_layers = len(analysis["providers"])
    bounded_search: dict[str, Any]
    try:
        record, graph, bounded_search = plan_width_aware(
            analysis["labels"],
            baseline_layers,
            poc,
            search_config=WidthAwareSearchConfig(
                hard_beam_states=8,
                unresolved_beam_states=12,
                max_layer_increase=0,
                candidate_singletons_per_color=2,
            ),
        )
        planned = _materialize_plan(record, graph, analysis)
        selected_mode = bounded_search["selected_mode"]
    except RuntimeError as error:
        planned = analysis
        selected_mode = "GREEDY_TOPOLOGY_FALLBACK_AFTER_BOUNDED_SEARCH_FAILURE"
        bounded_search = {
            "selected_mode": selected_mode,
            "error": str(error),
            "global_infeasibility_proven": False,
        }
    order = discover_order(planned, poc)
    if order["status"] != "FEASIBLE":
        return {
            "planner_status": "NO_COMPLETE_TOPOLOGICAL_PLAN_WITHIN_SELECTED_METHOD",
            "selected_mode": selected_mode,
            "global_infeasibility_proven": False,
            "bounded_search": bounded_search,
        }
    layers = construct_layers(planned, order, poc)
    validation, reconstructed, mismatch = validate_plan(planned, order, layers, poc)
    edge_failures = 0
    edge_orphans = 0
    width_failed = 0
    orphan_cores = 0
    orphan_core_pixels = 0
    fine_features = 0
    layer_rows: list[dict[str, Any]] = []
    colors: list[str] = []
    for layer in layers:
        edge, _ = audit_edge_connectivity(layer["physical_mask"])
        width, _ = audit_connection_width(
            layer["physical_mask"],
            layer["visible_aperture"],
            poc,
            DEFAULT_OPTIMIZATION_CONFIG,
        )
        edge_failures += int(edge["four_neighbor_status"] != "PASS")
        edge_orphans += edge["four_neighbor_orphan_components"]
        width_failed += int(width["status"] != "PASS")
        orphan_cores += width["orphan_structural_core_components"]
        orphan_core_pixels += width["orphan_structural_core_pixels"]
        fine_features += width["thin_decorative_visible_components"]
        colors.append(layer["color"])
        layer_id = f"layer_{layer['depth']:02d}_{layer['color'].lstrip('#').lower()}"
        layer_dir = output / "layers" / layer_id
        _save_mask(layer["physical_mask"], layer_dir / "retained_material_mask.png")
        _save_mask(layer["cutout_mask"], layer_dir / "through_cutouts_mask.png")
        _save_mask(layer["visible_aperture"], layer_dir / "visible_region_mask.png")
        layer_rows.append(
            {
                "id": layer_id,
                "depth_back_to_front": layer["depth"],
                "color": layer["color"],
                "retained_material_mask": f"layers/{layer_id}/retained_material_mask.png",
                "through_cutouts_mask": f"layers/{layer_id}/through_cutouts_mask.png",
                "visible_region_mask": f"layers/{layer_id}/visible_region_mask.png",
                "four_neighbor_status": edge["four_neighbor_status"],
                "four_neighbor_orphan_components": edge["four_neighbor_orphan_components"],
                "width_status": width["status"],
                "orphan_structural_core_components": width["orphan_structural_core_components"],
                "thin_decorative_visible_components": width["thin_decorative_visible_components"],
            }
        )
    output.mkdir(parents=True, exist_ok=True)
    Image.fromarray(reconstructed).save(output / "reconstructed_from_actual_masks.png")
    Image.fromarray(mismatch.astype(np.uint8) * 255, "L").save(output / "candidate_target_difference.png")
    repeated = {color: count for color, count in Counter(colors).items() if count > 1}
    valid = (
        validation["geometry_mismatched_pixels"] == 0
        and edge_failures == 0
        and width_failed == 0
    )
    return {
        "planner_status": "VALID_COMMON_PERIMETER_STACK_FOUND" if valid else "UNRESOLVED_PLAN_REQUIRES_USER_DECISION",
        "selected_mode": selected_mode,
        "global_infeasibility_proven": False,
        "physical_layer_count": len(layers),
        "baseline_greedy_provider_count": baseline_layers,
        "repeated_color_depths": repeated,
        "repeated_color_layer_count": sum(count - 1 for count in Counter(colors).values()),
        "four_neighbor_failed_layers": edge_failures,
        "four_neighbor_orphan_components": edge_orphans,
        "minimum_width_failed_layers": width_failed,
        "orphan_structural_core_components": orphan_cores,
        "orphan_structural_core_pixels": orphan_core_pixels,
        "fine_feature_warnings": fine_features,
        "exact_candidate_composite": validation["geometry_mismatched_pixels"] == 0,
        "candidate_mismatched_pixels": validation["geometry_mismatched_pixels"],
        "visible_automatic_bridge_pixels": 0,
        "common_perimeter_model": True,
        "layers": layer_rows,
        "bounded_search": bounded_search,
    }


def _render_candidate(labels: np.ndarray, palette: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(palette[labels].astype(np.uint8), "RGB").save(path)


def _render_difference(original: np.ndarray, candidate: np.ndarray, path: Path) -> None:
    changed = original != candidate
    image = np.zeros((*changed.shape, 3), dtype=np.uint8)
    image[changed] = (230, 55, 50)
    image[~changed] = (245, 245, 245)
    Image.fromarray(image).save(path)


def _render_side_by_side(
    source: np.ndarray,
    candidates: list[tuple[str, np.ndarray]],
    path: Path,
) -> None:
    tile = source.shape[0]
    canvas = Image.new("RGB", (tile * (len(candidates) + 1), tile + 42), "white")
    canvas.paste(Image.fromarray(source), (0, 42))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 14), "ORIGINAL", fill="black")
    for index, (name, image) in enumerate(candidates, start=1):
        canvas.paste(Image.fromarray(image), (index * tile, 42))
        draw.text((index * tile + 12, 14), f"{name} — USER REVIEW REQUIRED", fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)


def _render_change_map_montage(root: Path, path: Path) -> None:
    names = ("light", "balanced", "aggressive")
    maps = [Image.open(root / "candidates" / name / "changed_regions.png").convert("RGB") for name in names]
    tile = maps[0].width
    canvas = Image.new("RGB", (tile * len(maps), tile + 42), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (name, image) in enumerate(zip(names, maps)):
        canvas.paste(image, (index * tile, 42))
        draw.text((index * tile + 12, 14), f"{name.upper()} CHANGED PIXELS (RED)", fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)


def run_controlled_simplification(
    source: Path,
    case_id: str,
    output_root: Path,
    poc: PocConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    root = output_root / case_id
    original_dir = root / "original"
    original_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, original_dir / "source.png")
    base = analyze_image(source, poc)
    palette = np.asarray(base["palette_rgb"], dtype=np.uint8)
    original_labels = np.asarray(base["labels"], dtype=np.uint8)
    indexed_original = palette[original_labels]
    _render_candidate(original_labels, palette, original_dir / "indexed_planning_target.png")

    baseline_analysis = _analysis_from_labels(base, original_labels)
    baseline_report_path = root / "reports" / "baseline_planner.json"
    if baseline_report_path.exists():
        baseline_report = json.loads(baseline_report_path.read_text(encoding="utf-8"))
    else:
        baseline_report = run_actual_planner(baseline_analysis, root / "baseline_plan", poc)
        _write_json(baseline_report_path, baseline_report)

    candidates_for_montage: list[tuple[str, np.ndarray]] = []
    results: list[dict[str, Any]] = []
    for policy in POLICIES:
        candidate_labels, changes, generation = generate_candidate(
            original_labels, palette, policy
        )
        name = policy.name.lower()
        candidate_root = root / "candidates" / name
        candidate_rgb = palette[candidate_labels]
        _render_candidate(candidate_labels, palette, candidate_root / "candidate.png")
        _render_difference(original_labels, candidate_labels, candidate_root / "changed_regions.png")
        candidates_for_montage.append((policy.name, candidate_rgb))
        planned_analysis = _analysis_from_labels(base, candidate_labels)
        planner_path = root / "reports" / f"{name}_planner.json"
        if planner_path.exists():
            planner = json.loads(planner_path.read_text(encoding="utf-8"))
        else:
            planner = run_actual_planner(planned_analysis, candidate_root / "actual_plan", poc)
            _write_json(planner_path, planner)

        improvement = {
            "physical_layer_delta": planner.get("physical_layer_count", 0) - baseline_report.get("physical_layer_count", 0),
            "width_failed_layer_delta": planner.get("minimum_width_failed_layers", 0) - baseline_report.get("minimum_width_failed_layers", 0),
            "orphan_core_delta": planner.get("orphan_structural_core_components", 0) - baseline_report.get("orphan_structural_core_components", 0),
            "fine_feature_warning_delta": planner.get("fine_feature_warnings", 0) - baseline_report.get("fine_feature_warnings", 0),
        }
        border = np.concatenate(
            (original_labels[0], original_labels[-1], original_labels[:, 0], original_labels[:, -1])
        )
        background = int(np.bincount(border).argmax())
        original_foreground = original_labels != background
        candidate_foreground = candidate_labels != background
        union = np.count_nonzero(original_foreground | candidate_foreground)
        silhouette_iou = float(
            np.count_nonzero(original_foreground & candidate_foreground) / union
        ) if union else 1.0
        meaningful = any(value < 0 for value in improvement.values())
        for change in changes:
            change["measured_candidate_result"] = {
                "meaningful_aggregate_improvement": meaningful,
                **improvement,
            }
        _write_json(candidate_root / "change_log.json", changes)
        result = {
            "name": policy.name,
            "candidate": f"candidates/{name}/candidate.png",
            "change_map": f"candidates/{name}/changed_regions.png",
            "change_log": f"candidates/{name}/change_log.json",
            "generation": generation,
            "planner_report": f"reports/{name}_planner.json",
            "planner": planner,
            "measured_improvement_vs_original": improvement,
            "meaningful_structural_improvement": meaningful,
            "protected_feature_pixel_retention": 1.0 if generation["protected_changed_pixels"] == 0 else float("nan"),
            "protected_critical_feature_changed_pixels": generation["protected_changed_pixels"],
            "foreground_silhouette_iou_vs_indexed_original": silhouette_iou,
            "overall_indexed_pixel_accuracy_vs_original": float(np.mean(candidate_labels == original_labels)),
            "user_approval": "PENDING",
            "automatically_selected": False,
        }
        results.append(result)

    _render_side_by_side(
        base["rgb"],
        candidates_for_montage,
        root / "previews" / "original_light_balanced_aggressive.png",
    )
    _render_change_map_montage(
        root, root / "previews" / "light_balanced_aggressive_change_maps.png"
    )
    report = {
        "case_id": case_id,
        "review_state": "READY_FOR_USER_TEST",
        "source": "original/source.png",
        "indexed_planning_target": "original/indexed_planning_target.png",
        "source_sha256": _sha256(original_dir / "source.png"),
        "simplification_definition": "smallest justified connected-region edits after geometry-first planning; not global downsampling or arbitrary palette loss",
        "baseline_planner": baseline_report,
        "candidates": results,
        "automatic_selection": None,
        "final_layer_plan": None,
        "cad_generated": False,
        "next_action": "USER_APPROVE_OR_REJECT_ARTWORK_AND_2D_PLAN",
    }
    _write_json(root / "reports" / "controlled_simplification.json", report)
    return report


def run_direct_case(
    source: Path,
    case_id: str,
    output_root: Path,
    poc: PocConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    _progress("DIRECT_2D_PREPROCESSING_START", source=str(source))
    root = output_root / case_id
    original_dir = root / "original"
    original_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, original_dir / "source.png")
    analysis = analyze_image(source, poc)
    _progress(
        "DIRECT_2D_PREPROCESSING_COMPLETE",
        indexed_colors=len(analysis["palette_rgb"]),
        greedy_provider_groups=len(analysis["providers"]),
    )
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    labels = np.asarray(analysis["labels"], dtype=np.uint8)
    _render_candidate(labels, palette, original_dir / "indexed_planning_target.png")
    _progress("DIRECT_2D_PLANNING_START")
    planner = run_actual_planner(
        _analysis_from_labels(analysis, labels), root / "direct_plan", poc
    )
    report = {
        "case_id": case_id,
        "classification": "DIRECT",
        "classification_scope": "ready to attempt 2D planning, not an automatic feasibility pass",
        "source": "original/source.png",
        "indexed_planning_target": "original/indexed_planning_target.png",
        "planner": planner,
        "simplification_candidates": [],
        "automatic_selection": None,
        "cad_generated": False,
        "review_state": "READY_FOR_USER_TEST",
    }
    _write_json(root / "reports" / "direct_planner.json", report)
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
    return {"archive": str(archive), "sha256": _sha256(archive), "files": len(checksums) + 1}
