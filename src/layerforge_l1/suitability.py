"""Pre-layer suitability assessment and auditable 2D simplification.

This module does not create physical layers.  It measures the topology that a
shared-perimeter subtractive planner will have to represent and, when useful,
creates a deliberately labelled intermediate illustration for human review.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .subtractive_poc import DEFAULT_CONFIG, PocConfig, analyze_image


@dataclass(frozen=True)
class SuitabilityConfig:
    simplification_pixels: int = 256
    simplified_palette_colors: int = 8
    minimum_region_pixels: int = 16
    smoothing_window: int = 3
    direct_max_palette: int = 8
    direct_max_regions: int = 40
    direct_max_small_details: int = 12
    direct_max_likely_layers: int = 10


DEFAULT_SUITABILITY_CONFIG = SuitabilityConfig()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _component_metrics(labels: np.ndarray, required_width_pixels: int) -> dict[str, Any]:
    height, width = labels.shape
    rows: list[dict[str, Any]] = []
    enclosed_by_color: dict[int, int] = {}
    for color in sorted(int(value) for value in np.unique(labels)):
        count, components, stats, _ = cv2.connectedComponentsWithStats(
            (labels == color).astype(np.uint8), connectivity=4
        )
        for component in range(1, count):
            left = int(stats[component, cv2.CC_STAT_LEFT])
            top = int(stats[component, cv2.CC_STAT_TOP])
            component_width = int(stats[component, cv2.CC_STAT_WIDTH])
            component_height = int(stats[component, cv2.CC_STAT_HEIGHT])
            area = int(stats[component, cv2.CC_STAT_AREA])
            touches_border = (
                left == 0
                or top == 0
                or left + component_width == width
                or top + component_height == height
            )
            mask = (components == component).astype(np.uint8)
            maximum_width = float(cv2.distanceTransform(mask, cv2.DIST_L2, 5).max() * 2.0)
            is_small = maximum_width < required_width_pixels or area < required_width_pixels**2
            rows.append(
                {
                    "palette_index": color,
                    "component": component,
                    "area_pixels": area,
                    "maximum_local_width_pixels": maximum_width,
                    "touches_image_border": touches_border,
                    "small_detail": is_small,
                }
            )
            if not touches_border:
                enclosed_by_color[color] = enclosed_by_color.get(color, 0) + 1
    repeated_enclosed = sum(max(0, count - 1) for count in enclosed_by_color.values())
    return {
        "region_count_4_neighbor": len(rows),
        "small_detail_count": sum(1 for row in rows if row["small_detail"]),
        "small_detail_pixels": sum(row["area_pixels"] for row in rows if row["small_detail"]),
        "enclosed_region_count": sum(enclosed_by_color.values()),
        "repeated_enclosed_region_count": repeated_enclosed,
        "colors_with_repeated_enclosed_regions": sum(1 for count in enclosed_by_color.values() if count > 1),
        "components": rows,
    }


def _boundary_metrics(labels: np.ndarray) -> dict[str, Any]:
    horizontal = int(np.count_nonzero(labels[:, 1:] != labels[:, :-1]))
    vertical = int(np.count_nonzero(labels[1:, :] != labels[:-1, :]))
    transitions = horizontal + vertical
    return {
        "boundary_edge_count_pixels": transitions,
        "boundary_edges_per_megapixel": float(transitions / labels.size * 1_000_000.0),
    }


def assess_analysis(
    analysis: dict[str, Any],
    poc_config: PocConfig = DEFAULT_CONFIG,
    suitability_config: SuitabilityConfig = DEFAULT_SUITABILITY_CONFIG,
) -> dict[str, Any]:
    labels = np.asarray(analysis["labels"], dtype=np.uint8)
    required_width_pixels = int(
        np.ceil(poc_config.minimum_feature_mm / (poc_config.sheet_size_mm / poc_config.analysis_pixels))
    )
    components = _component_metrics(labels, required_width_pixels)
    likely_layers = len(analysis["providers"])
    palette_count = len(analysis["palette_rgb"])
    direct = (
        palette_count <= suitability_config.direct_max_palette
        and components["region_count_4_neighbor"] <= suitability_config.direct_max_regions
        and components["small_detail_count"] <= suitability_config.direct_max_small_details
        and likely_layers <= suitability_config.direct_max_likely_layers
    )
    bounded = palette_count <= 16 and components["region_count_4_neighbor"] <= 1000
    classification = "DIRECT" if direct else ("SIMPLIFY_FIRST" if bounded else "INFEASIBLE / NEEDS_USER_DECISION")
    difficulty_score = (
        max(0, palette_count - 5) * 2
        + max(0, likely_layers - palette_count) * 3
        + components["small_detail_count"]
        + components["repeated_enclosed_region_count"]
    )
    structural_difficulty = "LOW" if difficulty_score <= 20 else ("HIGH" if difficulty_score >= 80 else "MODERATE")
    return {
        "classification": classification,
        "classification_scope": "pre-layer estimate; not structural-feasibility proof",
        "ready_for_layer_planning": classification == "DIRECT",
        "configured_minimum_structural_width_mm": poc_config.minimum_feature_mm,
        "analysis_mm_per_pixel": poc_config.sheet_size_mm / poc_config.analysis_pixels,
        "required_width_pixels_ceiling": required_width_pixels,
        "palette_complexity": {
            "colors": palette_count,
            "palette": analysis["palette_hex"],
        },
        "likely_physical_layer_count_before_optimization": likely_layers,
        "provider_color_repetitions": likely_layers - palette_count,
        "structural_difficulty": structural_difficulty,
        "structural_difficulty_score": difficulty_score,
        **{key: value for key, value in components.items() if key != "components"},
        **_boundary_metrics(labels),
        "decision_thresholds": {
            "direct_max_palette": suitability_config.direct_max_palette,
            "direct_max_regions": suitability_config.direct_max_regions,
            "direct_max_small_details": suitability_config.direct_max_small_details,
            "direct_max_likely_layers": suitability_config.direct_max_likely_layers,
        },
        "warnings": [
            "Small-detail measurements describe visible artwork; they are not automatically treated as failed load-bearing connections.",
            "A DIRECT result still requires the width-aware layer planner and exact composite validation.",
        ],
    }


def _mode_smooth(labels: np.ndarray, colors: int, window: int) -> np.ndarray:
    if window <= 1:
        return labels.copy()
    scores = np.stack(
        [
            cv2.boxFilter((labels == color).astype(np.float32), -1, (window, window), normalize=False)
            for color in range(colors)
        ],
        axis=2,
    )
    return np.argmax(scores, axis=2).astype(np.uint8)


def _merge_small_regions(labels: np.ndarray, minimum: int) -> tuple[np.ndarray, list[dict[str, int]]]:
    result = labels.copy()
    events: list[dict[str, int]] = []
    kernel = np.ones((3, 3), dtype=np.uint8)
    for _ in range(4):
        changed = False
        for color in sorted(int(value) for value in np.unique(result)):
            count, components, stats, _ = cv2.connectedComponentsWithStats(
                (result == color).astype(np.uint8), connectivity=4
            )
            for component in range(1, count):
                area = int(stats[component, cv2.CC_STAT_AREA])
                if area >= minimum:
                    continue
                mask = components == component
                ring = cv2.dilate(mask.astype(np.uint8), kernel).astype(bool) & ~mask
                neighbors = result[ring]
                neighbors = neighbors[neighbors != color]
                if not neighbors.size:
                    continue
                values, counts = np.unique(neighbors, return_counts=True)
                replacement = int(values[np.argmax(counts)])
                result[mask] = replacement
                events.append({"from_palette_index": color, "to_palette_index": replacement, "pixels": area})
                changed = True
        if not changed:
            break
    return result, events


def simplify_image(
    source: Path,
    poc_config: PocConfig = DEFAULT_CONFIG,
    config: SuitabilityConfig = DEFAULT_SUITABILITY_CONFIG,
) -> tuple[np.ndarray, dict[str, Any]]:
    original = np.asarray(Image.open(source).convert("RGB"), dtype=np.uint8)
    working = cv2.resize(
        original,
        (config.simplification_pixels, config.simplification_pixels),
        interpolation=cv2.INTER_AREA,
    )
    filtered = cv2.bilateralFilter(working, 7, 28, 28)
    lab = cv2.cvtColor(filtered, cv2.COLOR_RGB2LAB).astype(np.float32)
    cv2.setRNGSeed(poc_config.random_seed)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.1)
    _, flat_labels, _ = cv2.kmeans(
        lab.reshape(-1, 3),
        config.simplified_palette_colors,
        None,
        criteria,
        1,
        cv2.KMEANS_PP_CENTERS,
    )
    labels = flat_labels.reshape(lab.shape[:2]).astype(np.uint8)
    labels = _mode_smooth(labels, config.simplified_palette_colors, config.smoothing_window)
    labels, events = _merge_small_regions(labels, config.minimum_region_pixels)
    unique = sorted(int(value) for value in np.unique(labels))
    remap = {old: new for new, old in enumerate(unique)}
    labels = np.vectorize(remap.get, otypes=[np.uint8])(labels)
    centers = np.asarray([working[labels == remap[old]].mean(axis=0) for old in unique])
    palette = np.clip(np.rint(centers), 0, 255).astype(np.uint8)
    candidate_small = palette[labels]
    candidate = cv2.resize(
        candidate_small,
        (poc_config.analysis_pixels, poc_config.analysis_pixels),
        interpolation=cv2.INTER_NEAREST,
    )
    source_analysis = cv2.resize(
        original,
        (poc_config.analysis_pixels, poc_config.analysis_pixels),
        interpolation=cv2.INTER_AREA,
    )
    delta = np.max(np.abs(candidate.astype(np.int16) - source_analysis.astype(np.int16)), axis=2)
    record = {
        "artifact_role": "automatic intermediate simplification candidate; user approval required before layer planning",
        "algorithm": "edge-preserving downsample + deterministic Lab palette reduction + local mode boundary cleanup + explicit tiny-region merging",
        "working_resolution": [config.simplification_pixels, config.simplification_pixels],
        "output_resolution": [poc_config.analysis_pixels, poc_config.analysis_pixels],
        "target_palette_colors": config.simplified_palette_colors,
        "actual_palette_colors": len(palette),
        "palette_rgb": palette.tolist(),
        "small_region_merge_events": events,
        "merged_pixels_at_working_resolution": sum(event["pixels"] for event in events),
        "changed_pixels_vs_resampled_original": int(np.count_nonzero(delta)),
        "changed_pixel_fraction_vs_resampled_original": float(np.mean(delta > 0)),
        "materially_changed_pixels_delta_gt_12": int(np.count_nonzero(delta > 12)),
        "materially_changed_fraction_delta_gt_12": float(np.mean(delta > 12)),
        "mean_absolute_rgb_error_0_255": float(
            np.abs(candidate.astype(np.int16) - source_analysis.astype(np.int16)).mean()
        ),
        "maximum_channel_error_0_255": int(delta.max()),
        "manual_corrections_applied": 0,
    }
    return candidate, record


def _comparison_canvas(original: np.ndarray, candidate: np.ndarray) -> Image.Image:
    height = 560
    tile = 512
    canvas = Image.new("RGB", (tile * 2, height), "white")
    canvas.paste(Image.fromarray(original).resize((tile, tile)), (0, 48))
    canvas.paste(Image.fromarray(candidate).resize((tile, tile)), (tile, 48))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 14), "ORIGINAL INPUT", fill=(20, 20, 20))
    draw.text((tile + 12, 14), "AUTOMATIC SIMPLIFIED CANDIDATE — NOT A LAYER PLAN", fill=(20, 20, 20))
    return canvas


def _change_map(original: np.ndarray, candidate: np.ndarray) -> Image.Image:
    delta = np.max(np.abs(original.astype(np.int16) - candidate.astype(np.int16)), axis=2)
    image = np.zeros((*delta.shape, 3), dtype=np.uint8)
    image[:, :, 0] = np.clip(delta * 3, 0, 255).astype(np.uint8)
    image[:, :, 1] = np.clip(delta, 0, 96).astype(np.uint8)
    return Image.fromarray(image)


def _assessment_card(
    source_rgb: np.ndarray,
    assessment: dict[str, Any],
    simplified: dict[str, Any] | None = None,
) -> Image.Image:
    canvas = Image.new("RGB", (1000, 560), (247, 245, 239))
    canvas.paste(Image.fromarray(source_rgb).resize((512, 512)), (24, 24))
    draw = ImageDraw.Draw(canvas)
    x = 565
    lines = [
        "SUITABILITY ASSESSMENT",
        f"Classification: {assessment['classification']}",
        "",
        f"Palette colors: {assessment['palette_complexity']['colors']}",
        f"4-neighbor regions: {assessment['region_count_4_neighbor']}",
        f"Small details: {assessment['small_detail_count']}",
        f"Enclosed regions: {assessment['enclosed_region_count']}",
        f"Likely provider layers: {assessment['likely_physical_layer_count_before_optimization']}",
        f"Structural difficulty: {assessment['structural_difficulty']}",
    ]
    if simplified is not None:
        lines.extend(
            [
                "",
                "AFTER AUTOMATIC SIMPLIFICATION",
                f"Palette: {simplified['palette_complexity']['colors']}",
                f"Regions: {simplified['region_count_4_neighbor']}",
                f"Small details: {simplified['small_detail_count']}",
                f"Likely layers: {simplified['likely_physical_layer_count_before_optimization']}",
                "Status: AWAITING USER APPROVAL",
            ]
        )
    for index, line in enumerate(lines):
        color = (120, 35, 25) if "Classification:" in line else (25, 31, 38)
        draw.text((x, 28 + index * 30), line, fill=color)
    draw.text((x, 518), "PRE-LAYER ESTIMATE — NOT A FEASIBILITY PASS", fill=(110, 65, 25))
    return canvas


def run_suitability_case(
    source: Path,
    case_id: str,
    output_root: Path,
    poc_config: PocConfig = DEFAULT_CONFIG,
    config: SuitabilityConfig = DEFAULT_SUITABILITY_CONFIG,
) -> dict[str, Any]:
    case_root = output_root / case_id
    original_dir = case_root / "original"
    report_dir = case_root / "reports"
    original_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    original_path = original_dir / "source.png"
    shutil.copy2(source, original_path)

    analysis = analyze_image(source, poc_config)
    assessment = assess_analysis(analysis, poc_config, config)
    result: dict[str, Any] = {
        "case_id": case_id,
        "source": "original/source.png",
        "source_sha256": _sha256(original_path),
        "assessment": assessment,
        "simplification": None,
        "simplified_assessment": None,
        "final_layer_plan": None,
        "final_layer_plan_status": "NOT_GENERATED_IN_THIS_PRE_LAYER_TASK",
        "manual_corrections_applied": 0,
    }

    if assessment["classification"] == "SIMPLIFY_FIRST":
        simplified_dir = case_root / "simplified"
        simplified_dir.mkdir(parents=True, exist_ok=True)
        candidate, simplification = simplify_image(source, poc_config, config)
        candidate_path = simplified_dir / "candidate.png"
        Image.fromarray(candidate).save(candidate_path)
        original_analysis = analysis["rgb"]
        _comparison_canvas(original_analysis, candidate).save(simplified_dir / "original_vs_candidate.png")
        _change_map(original_analysis, candidate).save(simplified_dir / "change_map.png")
        candidate_analysis = analyze_image(candidate_path, poc_config)
        simplified_assessment = assess_analysis(candidate_analysis, poc_config, config)
        events = simplification.pop("small_region_merge_events")
        _write_json(simplified_dir / "region_merge_events.json", events)
        transitions: dict[str, dict[str, int]] = {}
        for event in events:
            key = f"{event['from_palette_index']}->{event['to_palette_index']}"
            row = transitions.setdefault(key, {"events": 0, "pixels": 0})
            row["events"] += 1
            row["pixels"] += event["pixels"]
        simplification["small_region_merge_event_count"] = len(events)
        simplification["small_region_merge_event_log"] = "simplified/region_merge_events.json"
        simplification["small_region_merge_transition_summary"] = transitions
        simplification["candidate"] = "simplified/candidate.png"
        simplification["candidate_sha256"] = _sha256(candidate_path)
        simplification["review_status"] = "AWAITING_USER_APPROVAL"
        simplification["automatic_result_preserved"] = True
        result["simplification"] = simplification
        result["simplified_assessment"] = simplified_assessment
        result["predicted_impact"] = {
            "palette_colors_before_after": [
                assessment["palette_complexity"]["colors"],
                simplified_assessment["palette_complexity"]["colors"],
            ],
            "likely_layers_before_after": [
                assessment["likely_physical_layer_count_before_optimization"],
                simplified_assessment["likely_physical_layer_count_before_optimization"],
            ],
            "regions_before_after": [
                assessment["region_count_4_neighbor"],
                simplified_assessment["region_count_4_neighbor"],
            ],
            "small_details_before_after": [
                assessment["small_detail_count"],
                simplified_assessment["small_detail_count"],
            ],
            "structural_difficulty_before_after": [
                assessment["structural_difficulty"],
                simplified_assessment["structural_difficulty"],
            ],
            "conclusion": "Complexity is reduced, but the candidate remains unresolved until visual approval and a later width-aware layer-planning attempt.",
        }
        result["ready_to_proceed"] = False
        result["next_action"] = "USER_APPROVE_OR_REJECT_SIMPLIFIED_CANDIDATE"
    elif assessment["classification"] == "DIRECT":
        result["ready_to_proceed"] = True
        result["next_action"] = "RUN_WIDTH_AWARE_LAYER_PLANNER_IN_A_SEPARATE_APPROVED_TASK"
    else:
        result["ready_to_proceed"] = False
        result["next_action"] = "USER_DECISION_REQUIRED; AUTOMATIC SIMPLIFICATION_NOT_ASSERTED_SAFE"

    preview_dir = case_root / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    _assessment_card(
        analysis["rgb"], assessment, result["simplified_assessment"]
    ).save(preview_dir / "suitability_summary.png")

    _write_json(report_dir / "suitability.json", result)
    explanation = [
        f"Case: {case_id}",
        f"Classification: {assessment['classification']}",
        f"Palette colors: {assessment['palette_complexity']['colors']}",
        f"4-neighbor regions: {assessment['region_count_4_neighbor']}",
        f"Small visible details: {assessment['small_detail_count']}",
        f"Repeated enclosed regions: {assessment['repeated_enclosed_region_count']}",
        f"Estimated pre-optimization layer providers: {assessment['likely_physical_layer_count_before_optimization']}",
        f"Structural difficulty: {assessment['structural_difficulty']}",
        "No physical layer masks, CAD, STL, or STEP were generated.",
    ]
    if result["simplification"]:
        after = result["simplified_assessment"]
        explanation.extend(
            [
                "Simplification operations: palette reduction, local boundary smoothing, and explicit tiny-region merging.",
                f"Predicted provider layers: {assessment['likely_physical_layer_count_before_optimization']} -> {after['likely_physical_layer_count_before_optimization']}",
                f"Small visible details: {assessment['small_detail_count']} -> {after['small_detail_count']}",
                "The candidate remains an intermediate artifact and requires user approval before layer generation.",
            ]
        )
    (report_dir / "explanation.txt").write_text("\n".join(explanation) + "\n", encoding="utf-8")
    return result


def build_review_package(output_root: Path, archive: Path) -> dict[str, Any]:
    files = sorted(path for path in output_root.rglob("*") if path.is_file())
    checksums = {path.relative_to(output_root).as_posix(): _sha256(path) for path in files}
    _write_json(output_root / "checksums.json", checksums)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(item for item in output_root.rglob("*") if item.is_file()):
            handle.write(path, path.relative_to(output_root.parent).as_posix())
    return {
        "archive": str(archive),
        "archive_sha256": _sha256(archive),
        "file_count": len(files) + 1,
    }
