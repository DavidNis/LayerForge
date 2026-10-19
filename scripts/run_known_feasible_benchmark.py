"""Generate and independently plan the LayerForge known-feasible 2D benchmark.

The planner subprocess receives only the rendered composite path and ordinary
configuration. Ground-truth masks are created and validated by the controller
before that subprocess is launched.
"""

from __future__ import annotations

import json
import shutil
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
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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
    _render_exploded,
    _save_layer_artifacts,
    analyze_image,
    construct_layers,
    discover_order,
    validate_plan,
)
from layerforge_l1 import width_aware_planner as planner  # noqa: E402


OUTPUT = ROOT / "artifacts" / "l1_known_feasible_benchmark_v1"
SOURCE = OUTPUT / "source_composite.png"
PLANNER_PROGRESS = OUTPUT / "planner" / "search_progress.json"
PLANNER_RESULT = OUTPUT / "planner" / "search_result.json"
EXECUTION = OUTPUT / "planner" / "execution_report.json"

PALETTE = np.asarray(
    [
        [244, 231, 191],  # cream
        [242, 182, 50],   # yellow
        [23, 34, 56],     # dark navy
    ],
    dtype=np.uint8,
)
PALETTE_HEX = ["#F4E7BF", "#F2B632", "#172238"]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def save_mask(path: Path, mask: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255, "L").save(path)


def make_provider_map() -> np.ndarray:
    """Return manually defined front-to-back visible-provider assignments."""

    size = DEFAULT_CONFIG.analysis_pixels
    providers = np.zeros((size, size), dtype=np.uint8)

    # Provider 1: broad yellow foreground face. Provider 0 remains the cream
    # exterior/background and reaches every aperture edge.
    cv2.ellipse(providers, (256, 264), (148, 174), 0, 0, 360, 1, thickness=-1)
    left_ear = np.asarray([[152, 142], [184, 40], [226, 111], [215, 169]], np.int32)
    right_ear = np.asarray([[296, 111], [338, 40], [370, 142], [297, 169]], np.int32)
    cv2.fillPoly(providers, [left_ear, right_ear], 1)

    # Provider 2: several separated dark regions. The open eyebrows and mouth
    # are literal line-shaped openings through the yellow sheet.
    cv2.ellipse(providers, (205, 240), (43, 52), 0, 0, 360, 2, thickness=-1)
    cv2.ellipse(providers, (307, 240), (43, 52), 0, 0, 360, 2, thickness=-1)
    cv2.line(providers, (171, 176), (224, 166), 2, thickness=6, lineType=cv2.LINE_8)
    cv2.line(providers, (288, 166), (341, 176), 2, thickness=6, lineType=cv2.LINE_8)
    nose = np.asarray([[256, 289], [242, 306], [270, 306]], np.int32)
    cv2.fillPoly(providers, [nose], 2)
    mouth = np.asarray(
        [[207, 326], [221, 342], [240, 352], [256, 355], [272, 352], [291, 342], [305, 326]],
        np.int32,
    )
    cv2.polylines(providers, [mouth], False, 2, thickness=6, lineType=cv2.LINE_8)

    # Provider 3 repeats cream at a deeper physical depth inside both eyes.
    cv2.ellipse(providers, (205, 240), (25, 32), 0, 0, 360, 3, thickness=-1)
    cv2.ellipse(providers, (307, 240), (25, 32), 0, 0, 360, 3, thickness=-1)

    # Provider 4 repeats dark at the deepest depth. Because all five provider
    # masks partition the aperture, this final physical sheet is a continuous
    # solid backing even though only the two pupils remain visible.
    cv2.ellipse(providers, (205, 244), (10, 16), 0, 0, 360, 4, thickness=-1)
    cv2.ellipse(providers, (307, 244), (10, 16), 0, 0, 360, 4, thickness=-1)
    return providers


def ground_truth_analysis(provider_labels: np.ndarray) -> dict[str, Any]:
    provider_palette = np.asarray([0, 1, 2, 0, 2], dtype=np.uint8)
    labels = provider_palette[provider_labels]
    rgb = PALETTE[labels]
    providers = []
    roles = [
        "cream_exterior_foreground",
        "yellow_face_with_dark_cutouts",
        "dark_eye_rings_brows_nose_and_mouth",
        "repeated_cream_eye_insets",
        "repeated_dark_continuous_backing",
    ]
    for index, (palette_index, role) in enumerate(zip(provider_palette, roles)):
        mask = provider_labels == index
        count, _ = cv2.connectedComponents(mask.astype(np.uint8), connectivity=4)
        providers.append(
            {
                "index": index,
                "palette_index": int(palette_index),
                "color": PALETTE_HEX[int(palette_index)],
                "role": role,
                "pixels": int(np.count_nonzero(mask)),
                "source_region_count": count - 1,
                "front_to_back_rank": index,
            }
        )
    return {
        "rgb": rgb,
        "labels": labels,
        "palette_rgb": PALETTE,
        "palette_hex": PALETTE_HEX,
        "counts": np.bincount(labels.ravel(), minlength=len(PALETTE)),
        "provider_labels": provider_labels,
        "providers": providers,
        "provider_splits": [],
    }


def render_sheet_montage(
    layers: list[dict[str, Any]], analysis: dict[str, Any], path: Path
) -> None:
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    thumb = 230
    gap = 16
    canvas = Image.new("RGB", (thumb * len(layers) + gap * (len(layers) + 1), 300), "white")
    draw = ImageDraw.Draw(canvas)
    for index, layer in enumerate(layers):
        preview = np.full((*layer["physical_mask"].shape, 3), 250, dtype=np.uint8)
        preview[layer["physical_mask"]] = palette[layer["palette_index"]]
        tile = Image.fromarray(preview).resize((thumb, thumb), Image.Resampling.NEAREST)
        x = gap + index * (thumb + gap)
        canvas.paste(tile, (x, 42))
        draw.text((x, 10), f"{index} back-to-front {layer['color']}", fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)


def validate_ground_truth() -> dict[str, Any]:
    provider_labels = make_provider_map()
    analysis = ground_truth_analysis(provider_labels)
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(analysis["rgb"], "RGB").save(SOURCE)
    order = discover_order(analysis, DEFAULT_CONFIG)
    if order["status"] != "FEASIBLE":
        return {"status": "FAIL", "stage": "discover_order", "order": order}
    layers = construct_layers(analysis, order, DEFAULT_CONFIG)
    validation, reconstructed, mismatch = validate_plan(
        analysis, order, layers, DEFAULT_CONFIG
    )
    rows: list[dict[str, Any]] = []
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
                "provider_index": int(layer["provider_index"]),
                "color": layer["color"],
                "visible_region_components": int(
                    cv2.connectedComponents(
                        layer["visible_aperture"].astype(np.uint8), connectivity=4
                    )[0]
                    - 1
                ),
                "four_neighbor_status": edge["four_neighbor_status"],
                "four_neighbor_components": edge["four_neighbor_components"],
                "width_status": width["status"],
                "orphan_structural_core_components": width[
                    "orphan_structural_core_components"
                ],
                "thin_decorative_visible_components": width[
                    "thin_decorative_visible_components"
                ],
                "missing_perimeter_pixels": int(layer["missing_perimeter_pixels"]),
                "retained_pixels": int(np.count_nonzero(layer["physical_mask"])),
                "cutout_pixels": int(np.count_nonzero(layer["cutout_mask"])),
            }
        )
    valid = (
        validation["status"] == "PASS"
        and validation["geometry_mismatched_pixels"] == 0
        and all(row["four_neighbor_status"] == "PASS" for row in rows)
        and all(row["width_status"] == "PASS" for row in rows)
        and all(row["missing_perimeter_pixels"] == 0 for row in rows)
    )
    report = {
        "status": "PASS" if valid else "FAIL",
        "fixture": "synthetic_nested_face_v1",
        "provenance": "Original deterministic programmatic fixture created for this benchmark; no external artwork.",
        "physical_layer_count": len(layers),
        "distinct_colors": len(PALETTE),
        "front_to_back_provider_colors": [provider["color"] for provider in analysis["providers"]],
        "back_to_front_physical_colors": [layer["color"] for layer in layers],
        "repeated_colors": {
            color: count
            for color, count in Counter(layer["color"] for layer in layers).items()
            if count > 1
        },
        "geometry_validation": validation,
        "layers": rows,
        "thin_dark_line_construction": "Dark eyebrow and mouth pixels are openings in the yellow foreground sheet and are supplied by the deeper dark detail sheet.",
        "continuous_dark_backing_provider": 4,
    }
    root = OUTPUT / "ground_truth"
    _save_layer_artifacts(root, layers, analysis, DEFAULT_CONFIG)
    Image.fromarray(reconstructed).save(root / "reconstructed_front_view.png")
    Image.fromarray(mismatch.astype(np.uint8) * 255, "L").save(root / "difference.png")
    _render_exploded(layers, analysis, root / "exploded_layers.png")
    render_sheet_montage(layers, analysis, root / "sheet_mask_previews.png")
    write_json(root / "validation.json", report)
    return report


def planner_worker() -> int:
    started = time.perf_counter()
    try:
        analysis = analyze_image(SOURCE, DEFAULT_CONFIG)
        palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
        indexed = palette[np.asarray(analysis["labels"], dtype=np.uint8)]
        planner_root = OUTPUT / "planner"
        planner_root.mkdir(parents=True, exist_ok=True)
        Image.fromarray(indexed, "RGB").save(planner_root / "indexed_target.png")
        graph = build_region_graph(
            np.asarray(analysis["labels"]), connectivity=4, minimum_contact_pixels=1
        )
        evaluator = planner._ActualMaskEvaluator(
            graph, DEFAULT_CONFIG, DEFAULT_OPTIMIZATION_CONFIG
        )
        original_groups = planner._candidate_groups
        original_metrics = evaluator.metrics
        current_depth = 0
        counters: dict[str, Any] = {
            "candidate_generator_calls": 0,
            "accepted_groups_returned": 0,
            "new_mask_evaluations": 0,
            "four_neighbor_rejections": 0,
            "structural_width_rejections": 0,
            "per_depth": {},
        }

        def depth_row(depth: int) -> dict[str, int]:
            key = str(depth)
            if key not in counters["per_depth"]:
                counters["per_depth"][key] = {
                    "generator_calls": 0,
                    "accepted_groups_returned": 0,
                    "new_mask_evaluations": 0,
                    "four_neighbor_rejections": 0,
                    "structural_width_rejections": 0,
                }
            return counters["per_depth"][key]

        def traced_metrics(state: int):
            was_new = state not in evaluator.cache
            metrics = original_metrics(state)
            if was_new:
                counters["new_mask_evaluations"] += 1
                row = depth_row(current_depth)
                row["new_mask_evaluations"] += 1
                if metrics.four_neighbor_components != 1:
                    counters["four_neighbor_rejections"] += 1
                    row["four_neighbor_rejections"] += 1
                if metrics.orphan_core_components:
                    counters["structural_width_rejections"] += 1
                    row["structural_width_rejections"] += 1
            return metrics

        def traced_groups(*args, **kwargs):
            nonlocal current_depth
            import inspect

            frame = inspect.currentframe()
            caller = frame.f_back if frame is not None else None
            current_depth = int(caller.f_locals.get("depth", 0)) if caller else 0
            groups = original_groups(*args, **kwargs)
            counters["candidate_generator_calls"] += 1
            counters["accepted_groups_returned"] += len(groups)
            row = depth_row(current_depth)
            row["generator_calls"] += 1
            row["accepted_groups_returned"] += len(groups)
            return groups

        evaluator.metrics = traced_metrics  # type: ignore[method-assign]
        planner._candidate_groups = traced_groups
        search_ceiling = 12
        write_json(
            PLANNER_PROGRESS,
            {
                "status": "PLANNING",
                "indexed_colors": len(analysis["palette_rgb"]),
                "connected_regions": len(graph["nodes"]),
                "greedy_provider_groups": len(analysis["providers"]),
                "max_layers": search_ceiling,
                "max_states_per_depth": 8,
            },
        )
        search_started = time.perf_counter()
        record, search_report = planner._search(
            graph,
            evaluator,
            max_layers=search_ceiling,
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
            layer_rows = []
            for layer in layers:
                edge, _ = audit_edge_connectivity(layer["physical_mask"])
                width, _ = audit_connection_width(
                    layer["physical_mask"],
                    layer["visible_aperture"],
                    DEFAULT_CONFIG,
                    DEFAULT_OPTIMIZATION_CONFIG,
                )
                layer_rows.append(
                    {
                        "depth_back_to_front": layer["depth"],
                        "color": layer["color"],
                        "four_neighbor_status": edge["four_neighbor_status"],
                        "width_status": width["status"],
                        "orphan_structural_core_components": width[
                            "orphan_structural_core_components"
                        ],
                        "missing_perimeter_pixels": layer["missing_perimeter_pixels"],
                    }
                )
            final_valid = (
                validation["status"] == "PASS"
                and validation["geometry_mismatched_pixels"] == 0
                and all(row["four_neighbor_status"] == "PASS" for row in layer_rows)
                and all(row["width_status"] == "PASS" for row in layer_rows)
                and all(row["missing_perimeter_pixels"] == 0 for row in layer_rows)
            )
            colors = [layer["color"] for layer in layers]
            validation_summary = {
                "status": "PASS" if final_valid else "FAIL",
                "layer_count": len(layers),
                "front_to_back_colors": [
                    analysis["palette_hex"][color] for color in record.sequence
                ],
                "back_to_front_colors": colors,
                "repeated_colors": {
                    color: count for color, count in Counter(colors).items() if count > 1
                },
                "geometry_mismatched_pixels": validation[
                    "geometry_mismatched_pixels"
                ],
                "geometry_pixel_accuracy_against_indexed_target": validation[
                    "geometry_pixel_accuracy_against_quantized_target"
                ],
                "visible_automatic_connector_pixels": validation[
                    "visible_automatic_connector_pixels"
                ],
                "layers": layer_rows,
            }
            if final_valid:
                layer_files = _save_layer_artifacts(
                    planner_root, layers, planned, DEFAULT_CONFIG
                )
                Image.fromarray(reconstructed).save(
                    planner_root / "reconstructed_front_view.png"
                )
                Image.fromarray(mismatch.astype(np.uint8) * 255, "L").save(
                    planner_root / "difference.png"
                )
                _render_exploded(
                    layers, planned, planner_root / "exploded_layers.png"
                )
                render_sheet_montage(
                    layers, planned, planner_root / "sheet_mask_previews.png"
                )
        result = {
            "status": (
                "COMPLETE_VALID_PLAN_FOUND"
                if validation_summary and validation_summary["status"] == "PASS"
                else "COMPLETE_BUT_INVALID"
                if record is not None
                else "NO_COMPLETE_PLAN"
            ),
            "preprocessing": {
                "indexed_colors": len(analysis["palette_rgb"]),
                "palette_hex": analysis["palette_hex"],
                "connected_regions": len(graph["nodes"]),
                "greedy_provider_groups": len(analysis["providers"]),
            },
            "search_seconds": search_seconds,
            "search_ceiling_layers": search_ceiling,
            "record": asdict(record) if record is not None else None,
            "search_report": search_report,
            "instrumentation": counters,
            "validation": validation_summary,
            "layer_files": layer_files,
            "elapsed_worker_seconds": time.perf_counter() - started,
        }
        write_json(PLANNER_RESULT, result)
        write_json(PLANNER_PROGRESS, {"status": result["status"], **counters})
        print(json.dumps(result), flush=True)
        return 0
    except Exception:
        failure = {
            "status": "ERROR",
            "traceback": traceback.format_exc(),
            "elapsed_worker_seconds": time.perf_counter() - started,
        }
        write_json(PLANNER_PROGRESS, failure)
        traceback.print_exc()
        return 1
    finally:
        if "original_groups" in locals():
            planner._candidate_groups = original_groups


def controller() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite existing benchmark: {OUTPUT}")
    OUTPUT.mkdir(parents=True)
    ground_truth = validate_ground_truth()
    if ground_truth["status"] != "PASS":
        write_json(
            OUTPUT / "benchmark_report.json",
            {
                "status": "GROUND_TRUTH_VALIDATION_FAILED",
                "ground_truth": ground_truth,
                "planner_launched": False,
            },
        )
        print(json.dumps(ground_truth), flush=True)
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
            json.loads(PLANNER_PROGRESS.read_text(encoding="utf-8"))
            if PLANNER_PROGRESS.exists()
            else None
        ),
    }
    write_json(EXECUTION, execution)
    planner_result = (
        json.loads(PLANNER_RESULT.read_text(encoding="utf-8"))
        if PLANNER_RESULT.exists()
        else None
    )
    benchmark_status = (
        "PASS"
        if planner_result and planner_result["status"] == "COMPLETE_VALID_PLAN_FOUND"
        else "TIMEOUT"
        if timed_out
        else "PLANNER_FAILED"
    )
    report = {
        "status": benchmark_status,
        "ground_truth": ground_truth,
        "planner_execution": execution,
        "planner_result": planner_result,
        "interpretation": (
            "The current planner independently discovered a complete structurally valid exact-reconstruction stack from the composite alone."
            if benchmark_status == "PASS"
            else "The verified fixture is physically representable, but the current bounded planner did not independently produce a valid complete stack in this run."
        ),
    }
    write_json(OUTPUT / "benchmark_report.json", report)
    print(json.dumps(report), flush=True)
    return 0 if benchmark_status == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(
        planner_worker() if "--planner-worker" in sys.argv else controller()
    )
