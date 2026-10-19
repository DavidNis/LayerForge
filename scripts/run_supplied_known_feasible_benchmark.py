"""Validate and independently plan the user-supplied known-feasible fixture."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import time
import traceback
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from layerforge_l1 import width_aware_planner as planner  # noqa: E402
from layerforge_l1.connectivity_repair import (  # noqa: E402
    audit_connection_width,
    audit_edge_connectivity,
)
from layerforge_l1.layer_optimization import (  # noqa: E402
    DEFAULT_OPTIMIZATION_CONFIG,
    build_region_graph,
)
from layerforge_l1.subtractive_poc import (  # noqa: E402
    DEFAULT_CONFIG,
    _compose,
    _outer_and_perimeter,
    _render_exploded,
    _save_layer_artifacts,
    analyze_image,
    construct_layers,
    discover_order,
    validate_plan,
)


FIXTURE = ROOT / "fixtures" / "known_feasible_benchmark"
SOURCE = FIXTURE / "source_composite_only.png"
MASK_ROOT = FIXTURE / "private_reference_masks"
OUTPUT = ROOT / "artifacts" / "l1_supplied_known_feasible_benchmark_v1"
PLANNER_ROOT = OUTPUT / "planner"
PROGRESS = PLANNER_ROOT / "search_progress.json"
SEARCH_RESULT = PLANNER_ROOT / "search_result.json"
EXECUTION = PLANNER_ROOT / "execution_report.json"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def dominant_material_color(preview_path: Path, retained: np.ndarray) -> np.ndarray:
    preview = np.asarray(Image.open(preview_path).convert("RGB"), dtype=np.uint8)
    if preview.shape[:2] != retained.shape:
        raise ValueError(f"preview/mask shape mismatch: {preview_path}")
    values, counts = np.unique(preview[retained], axis=0, return_counts=True)
    dominant = values[int(np.argmax(counts))]
    if int(counts.max()) != int(np.count_nonzero(retained)):
        raise ValueError(f"retained material is not one color in {preview_path}")
    return dominant


def validate_reference_masks() -> dict[str, Any]:
    expected = [MASK_ROOT / f"layer_{index:02d}_retained.png" for index in range(1, 6)]
    previews = [MASK_ROOT / f"layer_{index:02d}_preview.png" for index in range(1, 6)]
    missing = [str(path) for path in [SOURCE, *expected, *previews] if not path.is_file()]
    if missing:
        return {"status": "FAIL", "stage": "inventory", "missing": missing}

    masks = [
        np.asarray(Image.open(path).convert("L"), dtype=np.uint8) >= 128
        for path in expected
    ]
    shapes = {mask.shape for mask in masks}
    expected_shape = (DEFAULT_CONFIG.outer_pixels, DEFAULT_CONFIG.outer_pixels)
    if shapes != {expected_shape}:
        return {
            "status": "FAIL",
            "stage": "dimensions",
            "observed_shapes": [list(shape) for shape in sorted(shapes)],
            "expected_shape": list(expected_shape),
        }
    colors = [dominant_material_color(path, mask) for path, mask in zip(previews, masks)]

    _, perimeter = _outer_and_perimeter(DEFAULT_CONFIG)
    p = DEFAULT_CONFIG.perimeter_pixels
    cumulative_front = np.zeros(expected_shape, dtype=bool)
    provider_labels_outer = np.full(expected_shape, 255, dtype=np.uint8)
    containment_failures: list[dict[str, int]] = []
    for index, mask in enumerate(masks):
        missing_prior = int(np.count_nonzero(cumulative_front & ~mask))
        if missing_prior:
            containment_failures.append(
                {"layer_front_to_back": index + 1, "missing_prior_material_pixels": missing_prior}
            )
        newly_visible = mask & ~cumulative_front
        provider_labels_outer[newly_visible] = index
        cumulative_front |= mask
    unassigned = int(np.count_nonzero(provider_labels_outer == 255))

    unique_colors: list[tuple[int, int, int]] = []
    provider_palette: list[int] = []
    for color in colors:
        key = tuple(int(channel) for channel in color)
        if key not in unique_colors:
            unique_colors.append(key)
        provider_palette.append(unique_colors.index(key))
    palette = np.asarray(unique_colors, dtype=np.uint8)
    provider_labels = provider_labels_outer[p:-p, p:-p]
    target_labels = np.asarray(provider_palette, dtype=np.uint8)[provider_labels]
    source_outer = np.asarray(Image.open(SOURCE).convert("RGB"), dtype=np.uint8)
    source_aperture = source_outer[p:-p, p:-p]
    providers: list[dict[str, Any]] = []
    for index, (color, palette_index) in enumerate(zip(colors, provider_palette)):
        visible = provider_labels == index
        components = cv2.connectedComponents(visible.astype(np.uint8), connectivity=4)[0] - 1
        providers.append(
            {
                "index": index,
                "palette_index": palette_index,
                "color": "#" + "".join(f"{int(channel):02X}" for channel in color),
                "role": "supplied_reference_mask",
                "pixels": int(np.count_nonzero(visible)),
                "source_region_count": int(components),
                "front_to_back_rank": index,
            }
        )
    analysis = {
        "rgb": source_aperture,
        "labels": target_labels,
        "palette_rgb": palette,
        "palette_hex": ["#" + "".join(f"{int(channel):02X}" for channel in color) for color in palette],
        "counts": np.bincount(target_labels.ravel(), minlength=len(palette)),
        "provider_labels": provider_labels,
        "providers": providers,
        "provider_splits": [],
    }
    order = discover_order(analysis, DEFAULT_CONFIG)
    if order["status"] != "FEASIBLE":
        return {"status": "FAIL", "stage": "existing_order_validation", "order": order}
    constructed = construct_layers(analysis, order, DEFAULT_CONFIG)
    validation, reconstructed_aperture, mismatch = validate_plan(
        analysis, order, constructed, DEFAULT_CONFIG
    )
    supplied_back_to_front = list(reversed(masks))
    physical_mask_mismatches = [
        int(np.count_nonzero(layer["physical_mask"] != supplied))
        for layer, supplied in zip(constructed, supplied_back_to_front)
    ]

    rows: list[dict[str, Any]] = []
    for front_index, (mask, color) in enumerate(zip(masks, colors)):
        previous = masks[front_index - 1] if front_index else np.zeros_like(mask)
        visible_outer = mask & ~previous
        edge, _ = audit_edge_connectivity(mask)
        width, _ = audit_connection_width(
            mask,
            visible_outer[p:-p, p:-p],
            DEFAULT_CONFIG,
            DEFAULT_OPTIMIZATION_CONFIG,
        )
        rows.append(
            {
                "layer_front_to_back": front_index + 1,
                "color": "#" + "".join(f"{int(channel):02X}" for channel in color),
                "dimensions_pixels": list(mask.shape[::-1]),
                "retained_pixels": int(np.count_nonzero(mask)),
                "cutout_pixels": int(mask.size - np.count_nonzero(mask)),
                "missing_shared_perimeter_pixels": int(np.count_nonzero(perimeter & ~mask)),
                "four_neighbor_status": edge["four_neighbor_status"],
                "four_neighbor_components": edge["four_neighbor_components"],
                "four_neighbor_orphan_components": edge["four_neighbor_orphan_components"],
                "width_status": width["status"],
                "orphan_structural_core_components": width[
                    "orphan_structural_core_components"
                ],
                "orphan_structural_core_pixels": width[
                    "orphan_structural_core_pixels"
                ],
                "morphology_kernel_pixels": width["morphology_kernel_size_pixels"],
                "enforced_connection_width_mm": width["enforced_connection_width_mm"],
                "visible_region_components": int(
                    cv2.connectedComponents(
                        visible_outer.astype(np.uint8), connectivity=4
                    )[0]
                    - 1
                ),
            }
        )

    composed_outer = np.zeros_like(source_outer)
    for mask, color in reversed(list(zip(masks, colors))):
        composed_outer[mask] = color
    outer_difference = np.any(composed_outer != source_outer, axis=2)
    target_rgb = palette[target_labels]
    aperture_target_difference = np.any(target_rgb != source_aperture, axis=2)
    valid = (
        not containment_failures
        and unassigned == 0
        and validation["status"] == "PASS"
        and validation["geometry_mismatched_pixels"] == 0
        and not any(physical_mask_mismatches)
        and not np.any(outer_difference)
        and not np.any(aperture_target_difference)
        and all(row["missing_shared_perimeter_pixels"] == 0 for row in rows)
        and all(row["four_neighbor_status"] == "PASS" for row in rows)
        and all(row["width_status"] == "PASS" for row in rows)
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    Image.fromarray(composed_outer, "RGB").save(OUTPUT / "reference_reconstructed_front.png")
    Image.fromarray(outer_difference.astype(np.uint8) * 255, "L").save(
        OUTPUT / "reference_source_difference.png"
    )
    report = {
        "status": "PASS" if valid else "FAIL",
        "method": "Independent use of current LayerForge mask construction, 4-neighbor audit, and 1.2 mm width audit; fixture_report.json results were not used.",
        "reference_layer_count": len(masks),
        "reference_order_front_to_back": [provider["color"] for provider in providers],
        "shared_dimensions_pixels": list(expected_shape[::-1]),
        "containment_failures": containment_failures,
        "unassigned_source_pixels": unassigned,
        "physical_mask_mismatch_pixels_against_existing_constructor": physical_mask_mismatches,
        "source_reconstruction_mismatched_pixels": int(np.count_nonzero(outer_difference)),
        "aperture_color_mismatched_pixels": int(np.count_nonzero(aperture_target_difference)),
        "existing_plan_validation": validation,
        "layers": rows,
    }
    write_json(OUTPUT / "reference_validation.json", report)
    return report


def planner_worker() -> int:
    started = time.perf_counter()
    try:
        # Deliberately load only the source composite. No reference path or
        # fixture report is read in this worker.
        analysis = analyze_image(SOURCE, DEFAULT_CONFIG)
        labels = np.asarray(analysis["labels"], dtype=np.uint8)
        palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
        indexed = palette[labels]
        PLANNER_ROOT.mkdir(parents=True, exist_ok=True)
        Image.fromarray(indexed, "RGB").save(PLANNER_ROOT / "indexed_target.png")
        Image.fromarray(np.asarray(analysis["rgb"], dtype=np.uint8), "RGB").save(
            PLANNER_ROOT / "preprocessed_source.png"
        )
        source_difference = np.abs(
            indexed.astype(np.int16) - np.asarray(analysis["rgb"], dtype=np.int16)
        )
        graph = build_region_graph(labels, connectivity=4, minimum_contact_pixels=1)
        evaluator = planner._ActualMaskEvaluator(
            graph, DEFAULT_CONFIG, DEFAULT_OPTIMIZATION_CONFIG
        )
        original_groups = planner._candidate_groups
        original_metrics = evaluator.metrics
        current_depth = 0
        instrumentation: dict[str, Any] = {
            "generator_calls": 0,
            "accepted_groups_returned": 0,
            "new_mask_evaluations": 0,
            "four_neighbor_rejections": 0,
            "structural_width_rejections": 0,
            "per_depth": {},
        }

        def depth_row(depth: int) -> dict[str, int]:
            key = str(depth)
            if key not in instrumentation["per_depth"]:
                instrumentation["per_depth"][key] = {
                    "generator_calls": 0,
                    "accepted_groups_returned": 0,
                    "new_mask_evaluations": 0,
                    "four_neighbor_rejections": 0,
                    "structural_width_rejections": 0,
                }
            return instrumentation["per_depth"][key]

        def traced_metrics(state: int):
            was_new = state not in evaluator.cache
            metrics = original_metrics(state)
            if was_new:
                row = depth_row(current_depth)
                instrumentation["new_mask_evaluations"] += 1
                row["new_mask_evaluations"] += 1
                if metrics.four_neighbor_components != 1:
                    instrumentation["four_neighbor_rejections"] += 1
                    row["four_neighbor_rejections"] += 1
                if metrics.orphan_core_components:
                    instrumentation["structural_width_rejections"] += 1
                    row["structural_width_rejections"] += 1
            return metrics

        def traced_groups(*args, **kwargs):
            nonlocal current_depth
            frame = inspect.currentframe()
            caller = frame.f_back if frame is not None else None
            current_depth = int(caller.f_locals.get("depth", 0)) if caller else 0
            groups = original_groups(*args, **kwargs)
            row = depth_row(current_depth)
            instrumentation["generator_calls"] += 1
            instrumentation["accepted_groups_returned"] += len(groups)
            row["generator_calls"] += 1
            row["accepted_groups_returned"] += len(groups)
            return groups

        evaluator.metrics = traced_metrics  # type: ignore[method-assign]
        planner._candidate_groups = traced_groups
        max_layers = len(analysis["providers"])
        write_json(
            PROGRESS,
            {
                "status": "PLANNING",
                "indexed_colors": len(palette),
                "connected_regions": len(graph["nodes"]),
                "greedy_provider_groups": len(analysis["providers"]),
                "max_layers": max_layers,
                "max_states_per_depth": 8,
            },
        )
        search_started = time.perf_counter()
        record, search_report = planner._search(
            graph,
            evaluator,
            max_layers=max_layers,
            max_states=8,
            hard_width=True,
            singleton_limit=2,
        )
        search_seconds = time.perf_counter() - search_started
        validation_summary = None
        layer_files = None
        if record is not None:
            planned = planner._materialize_plan(record, graph, analysis)
            order = discover_order(planned, DEFAULT_CONFIG)
            layers = construct_layers(planned, order, DEFAULT_CONFIG)
            validation, reconstructed, mismatch = validate_plan(
                planned, order, layers, DEFAULT_CONFIG
            )
            rows = []
            for layer in layers:
                edge, _ = audit_edge_connectivity(layer["physical_mask"])
                width, _ = audit_connection_width(
                    layer["physical_mask"],
                    layer["visible_aperture"],
                    DEFAULT_CONFIG,
                    DEFAULT_OPTIMIZATION_CONFIG,
                )
                rows.append(
                    {
                        "depth_back_to_front": int(layer["depth"]),
                        "color": layer["color"],
                        "four_neighbor_status": edge["four_neighbor_status"],
                        "width_status": width["status"],
                        "orphan_structural_core_components": width[
                            "orphan_structural_core_components"
                        ],
                        "missing_perimeter_pixels": int(layer["missing_perimeter_pixels"]),
                    }
                )
            final_valid = (
                validation["status"] == "PASS"
                and validation["geometry_mismatched_pixels"] == 0
                and validation["visible_automatic_connector_pixels"] == 0
                and all(row["four_neighbor_status"] == "PASS" for row in rows)
                and all(row["width_status"] == "PASS" for row in rows)
                and all(row["missing_perimeter_pixels"] == 0 for row in rows)
            )
            colors = [layer["color"] for layer in layers]
            validation_summary = {
                "status": "PASS" if final_valid else "FAIL",
                "physical_layer_count": len(layers),
                "front_to_back_colors": [
                    analysis["palette_hex"][index] for index in record.sequence
                ],
                "back_to_front_colors": colors,
                "repeated_colors": {
                    color: count for color, count in Counter(colors).items() if count > 1
                },
                "geometry_mismatched_pixels_against_indexed_target": validation[
                    "geometry_mismatched_pixels"
                ],
                "indexed_target_accuracy": validation[
                    "geometry_pixel_accuracy_against_quantized_target"
                ],
                "visible_automatic_connector_pixels": validation[
                    "visible_automatic_connector_pixels"
                ],
                "layers": rows,
            }
            if final_valid:
                layer_files = _save_layer_artifacts(
                    PLANNER_ROOT, layers, planned, DEFAULT_CONFIG
                )
                Image.fromarray(reconstructed, "RGB").save(
                    PLANNER_ROOT / "reconstructed_front_view.png"
                )
                Image.fromarray(mismatch.astype(np.uint8) * 255, "L").save(
                    PLANNER_ROOT / "indexed_target_difference.png"
                )
                _render_exploded(
                    layers, planned, PLANNER_ROOT / "exploded_layers.png"
                )
        result = {
            "status": (
                "COMPLETE_VALID_PLAN_FOUND"
                if validation_summary and validation_summary["status"] == "PASS"
                else "COMPLETE_BUT_INVALID"
                if record is not None
                else "NO_COMPLETE_PLAN"
            ),
            "planner_input": "source_composite_only.png only",
            "preprocessing": {
                "indexed_colors": len(palette),
                "palette_hex": analysis["palette_hex"],
                "connected_regions": len(graph["nodes"]),
                "greedy_provider_groups": len(analysis["providers"]),
                "changed_pixels_against_resized_source": int(
                    np.count_nonzero(np.any(source_difference != 0, axis=2))
                ),
                "mean_absolute_rgb_error_against_resized_source_0_255": float(
                    source_difference.mean()
                ),
                "maximum_rgb_error_against_resized_source_0_255": int(
                    source_difference.max()
                ),
            },
            "search_seconds": search_seconds,
            "search_report": search_report,
            "instrumentation": instrumentation,
            "record": asdict(record) if record is not None else None,
            "validation": validation_summary,
            "layer_files": layer_files,
            "worker_seconds": time.perf_counter() - started,
        }
        write_json(SEARCH_RESULT, result)
        write_json(PROGRESS, {"status": result["status"], **instrumentation})
        print(json.dumps(result), flush=True)
        return 0
    except Exception:
        failure = {
            "status": "ERROR",
            "traceback": traceback.format_exc(),
            "worker_seconds": time.perf_counter() - started,
        }
        write_json(PROGRESS, failure)
        traceback.print_exc()
        return 1
    finally:
        if "original_groups" in locals():
            planner._candidate_groups = original_groups


def controller() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite prior run: {OUTPUT}")
    OUTPUT.mkdir(parents=True)
    reference = validate_reference_masks()
    if reference["status"] != "PASS":
        write_json(
            OUTPUT / "benchmark_report.json",
            {
                "status": "REFERENCE_VALIDATION_FAILED",
                "reference_validation": reference,
                "planner_launched": False,
            },
        )
        print(json.dumps(reference), flush=True)
        return 2

    started = time.perf_counter()
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--planner-worker"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        stdout, stderr = process.communicate()
    execution = {
        "timed_out": timed_out,
        "timeout_seconds": 30,
        "exit_code": process.returncode,
        "wall_seconds": time.perf_counter() - started,
        "stdout": stdout,
        "stderr": stderr,
        "partial_progress": (
            json.loads(PROGRESS.read_text(encoding="utf-8"))
            if PROGRESS.exists()
            else None
        ),
    }
    write_json(EXECUTION, execution)
    result = (
        json.loads(SEARCH_RESULT.read_text(encoding="utf-8"))
        if SEARCH_RESULT.exists()
        else None
    )
    status = (
        "PASS"
        if result and result["status"] == "COMPLETE_VALID_PLAN_FOUND"
        else "TIMEOUT"
        if timed_out
        else "PLANNER_FAILED"
    )
    report = {
        "status": status,
        "reference_validation": reference,
        "planner_execution": execution,
        "planner_result": result,
    }
    write_json(OUTPUT / "benchmark_report.json", report)
    print(json.dumps(report), flush=True)
    return 0 if status == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(
        planner_worker() if "--planner-worker" in sys.argv else controller()
    )
