"""Run the supplied benchmark with lossless four-colour indexing.

This adapter deliberately bypasses production image preprocessing.  It does
not alter planner or validator code: the source and supplied masks are resized
once with nearest-neighbour sampling, and the source RGB values are mapped
directly to exact palette indices.
"""

from __future__ import annotations

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
    _outer_and_perimeter,
    _provider_regions,
    _render_exploded,
    _save_layer_artifacts,
    construct_layers,
    discover_order,
    validate_plan,
)


FIXTURE = ROOT / "fixtures" / "known_feasible_benchmark"
SOURCE = FIXTURE / "source_composite_only.png"
MASK_ROOT = FIXTURE / "private_reference_masks"
OUTPUT = ROOT / "artifacts" / "l1_lossless_four_color_benchmark_v1"
TARGET = OUTPUT / "lossless_four_color_target_512.png"
REFERENCE_ROOT = OUTPUT / "reference_512"
PLANNER_ROOT = OUTPUT / "planner"
PROGRESS = PLANNER_ROOT / "search_progress.json"
SEARCH_RESULT = PLANNER_ROOT / "search_result.json"
EXECUTION = PLANNER_ROOT / "execution_report.json"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def hex_color(color: np.ndarray) -> str:
    return "#" + "".join(f"{int(channel):02X}" for channel in color)


def exact_index(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Map exact RGB triplets to indices without fitting or filtering."""
    palette, inverse = np.unique(
        rgb.reshape(-1, 3), axis=0, return_inverse=True
    )
    return inverse.reshape(rgb.shape[:2]).astype(np.uint8), palette.astype(np.uint8)


def dominant_material_color(preview_path: Path, retained: np.ndarray) -> np.ndarray:
    preview = np.asarray(Image.open(preview_path).convert("RGB"), dtype=np.uint8)
    if preview.shape[:2] != retained.shape:
        raise ValueError(f"preview/mask shape mismatch: {preview_path}")
    values, counts = np.unique(preview[retained], axis=0, return_counts=True)
    dominant = values[int(np.argmax(counts))]
    if int(counts.max()) != int(np.count_nonzero(retained)):
        raise ValueError(f"retained material is not one color in {preview_path}")
    return dominant


def validate_resized_reference() -> dict[str, Any]:
    mask_paths = [MASK_ROOT / f"layer_{index:02d}_retained.png" for index in range(1, 6)]
    preview_paths = [MASK_ROOT / f"layer_{index:02d}_preview.png" for index in range(1, 6)]
    missing = [str(path) for path in [SOURCE, *mask_paths, *preview_paths] if not path.is_file()]
    if missing:
        return {"status": "FAIL", "stage": "inventory", "missing": missing}

    source = np.asarray(Image.open(SOURCE).convert("RGB"), dtype=np.uint8)
    original_labels, original_palette = exact_index(source)
    target = cv2.resize(
        source,
        (DEFAULT_CONFIG.analysis_pixels, DEFAULT_CONFIG.analysis_pixels),
        interpolation=cv2.INTER_NEAREST,
    )
    labels, palette = exact_index(target)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    Image.fromarray(target, "RGB").save(TARGET)

    original_masks = [
        np.asarray(Image.open(path).convert("L"), dtype=np.uint8) >= 128
        for path in mask_paths
    ]
    source_shape = source.shape[:2]
    if any(mask.shape != source_shape for mask in original_masks):
        return {
            "status": "FAIL",
            "stage": "source_dimensions",
            "source_shape": list(source_shape[::-1]),
            "mask_shapes": [list(mask.shape[::-1]) for mask in original_masks],
        }
    colors = [
        dominant_material_color(path, mask)
        for path, mask in zip(preview_paths, original_masks)
    ]
    masks = [
        cv2.resize(
            mask.astype(np.uint8),
            (DEFAULT_CONFIG.analysis_pixels, DEFAULT_CONFIG.analysis_pixels),
            interpolation=cv2.INTER_NEAREST,
        ).astype(bool)
        for mask in original_masks
    ]

    REFERENCE_ROOT.mkdir(parents=True, exist_ok=True)
    for index, mask in enumerate(masks, start=1):
        Image.fromarray(mask.astype(np.uint8) * 255, "L").save(
            REFERENCE_ROOT / f"layer_{index:02d}_retained_aperture_512.png"
        )

    provider_labels = np.full(labels.shape, 255, dtype=np.uint8)
    cumulative_front = np.zeros(labels.shape, dtype=bool)
    containment_failures: list[dict[str, int]] = []
    visible_masks: list[np.ndarray] = []
    for index, mask in enumerate(masks):
        missing_prior = int(np.count_nonzero(cumulative_front & ~mask))
        if missing_prior:
            containment_failures.append(
                {"layer_front_to_back": index + 1, "missing_prior_pixels": missing_prior}
            )
        visible = mask & ~cumulative_front
        provider_labels[visible] = index
        visible_masks.append(visible)
        cumulative_front |= mask

    unique_colors: list[tuple[int, int, int]] = []
    provider_palette: list[int] = []
    for color in colors:
        key = tuple(int(channel) for channel in color)
        if key not in unique_colors:
            unique_colors.append(key)
        provider_palette.append(unique_colors.index(key))
    reference_palette = np.asarray(unique_colors, dtype=np.uint8)
    reference_target_labels = np.asarray(provider_palette, dtype=np.uint8)[provider_labels]
    reconstructed = reference_palette[reference_target_labels]
    difference = np.any(reconstructed != target, axis=2)

    providers: list[dict[str, Any]] = []
    for index, (color, palette_index, visible) in enumerate(
        zip(colors, provider_palette, visible_masks)
    ):
        components = cv2.connectedComponents(visible.astype(np.uint8), connectivity=4)[0] - 1
        providers.append(
            {
                "index": index,
                "palette_index": palette_index,
                "color": hex_color(color),
                "role": "resized_supplied_reference_mask",
                "pixels": int(np.count_nonzero(visible)),
                "source_region_count": int(components),
                "front_to_back_rank": index,
            }
        )
    analysis = {
        "rgb": target,
        "labels": reference_target_labels,
        "palette_rgb": reference_palette,
        "palette_hex": [hex_color(color) for color in reference_palette],
        "counts": np.bincount(reference_target_labels.ravel(), minlength=len(reference_palette)),
        "provider_labels": provider_labels,
        "providers": providers,
        "provider_splits": [],
    }
    order = discover_order(analysis, DEFAULT_CONFIG)
    if order["status"] != "FEASIBLE":
        return {"status": "FAIL", "stage": "reference_order", "order": order}
    layers = construct_layers(analysis, order, DEFAULT_CONFIG)
    validation, validation_reconstruction, validation_mismatch = validate_plan(
        analysis, order, layers, DEFAULT_CONFIG
    )

    _, perimeter = _outer_and_perimeter(DEFAULT_CONFIG)
    p = DEFAULT_CONFIG.perimeter_pixels
    rows: list[dict[str, Any]] = []
    expected_back_to_front = list(reversed(masks))
    for back_index, (layer, expected_aperture) in enumerate(zip(layers, expected_back_to_front)):
        expected_physical = perimeter.copy()
        expected_physical[p:-p, p:-p] = expected_aperture
        physical_mismatch = int(np.count_nonzero(layer["physical_mask"] != expected_physical))
        edge, _ = audit_edge_connectivity(layer["physical_mask"])
        width, _ = audit_connection_width(
            layer["physical_mask"],
            layer["visible_aperture"],
            DEFAULT_CONFIG,
            DEFAULT_OPTIMIZATION_CONFIG,
        )
        layer_number = len(layers) - back_index
        Image.fromarray(layer["physical_mask"].astype(np.uint8) * 255, "L").save(
            REFERENCE_ROOT / f"layer_{layer_number:02d}_physical_mask.png"
        )
        rows.append(
            {
                "layer_front_to_back": layer_number,
                "color": layer["color"],
                "physical_dimensions_pixels": list(layer["physical_mask"].shape[::-1]),
                "missing_shared_perimeter_pixels": int(
                    np.count_nonzero(perimeter & ~layer["physical_mask"])
                ),
                "physical_mask_mismatch_pixels": physical_mismatch,
                "four_neighbor_status": edge["four_neighbor_status"],
                "four_neighbor_components": edge["four_neighbor_components"],
                "four_neighbor_orphan_components": edge["four_neighbor_orphan_components"],
                "width_status": width["status"],
                "orphan_structural_core_components": width["orphan_structural_core_components"],
                "orphan_structural_core_pixels": width["orphan_structural_core_pixels"],
                "morphology_kernel_pixels": width["morphology_kernel_size_pixels"],
                "enforced_connection_width_mm": width["enforced_connection_width_mm"],
            }
        )

    Image.fromarray(reconstructed, "RGB").save(REFERENCE_ROOT / "reconstructed_front.png")
    Image.fromarray(difference.astype(np.uint8) * 255, "L").save(
        REFERENCE_ROOT / "source_difference.png"
    )
    Image.fromarray(validation_reconstruction, "RGB").save(
        REFERENCE_ROOT / "validator_reconstructed_front.png"
    )
    Image.fromarray(validation_mismatch.astype(np.uint8) * 255, "L").save(
        REFERENCE_ROOT / "validator_difference.png"
    )

    palette_preserved = {
        tuple(int(channel) for channel in color) for color in original_palette
    } == {tuple(int(channel) for channel in color) for color in palette}
    unassigned = int(np.count_nonzero(provider_labels == 255))
    valid = (
        len(original_palette) == 4
        and len(palette) == 4
        and palette_preserved
        and not containment_failures
        and unassigned == 0
        and not np.any(difference)
        and validation["status"] == "PASS"
        and validation["geometry_mismatched_pixels"] == 0
        and all(row["physical_mask_mismatch_pixels"] == 0 for row in rows)
        and all(row["missing_shared_perimeter_pixels"] == 0 for row in rows)
        and all(row["four_neighbor_status"] == "PASS" for row in rows)
        and all(row["width_status"] == "PASS" for row in rows)
        and all(row["orphan_structural_core_components"] == 0 for row in rows)
    )
    report = {
        "status": "PASS" if valid else "FAIL",
        "method": "Nearest-neighbour resize plus exact RGB indexing; current LayerForge validators applied to perimeter-embedded masks.",
        "source_dimensions_pixels": list(source.shape[1::-1]),
        "target_dimensions_pixels": list(target.shape[1::-1]),
        "source_exact_palette_count": len(original_palette),
        "source_exact_palette_hex": [hex_color(color) for color in original_palette],
        "target_exact_palette_count": len(palette),
        "target_exact_palette_hex": [hex_color(color) for color in palette],
        "palette_preserved": palette_preserved,
        "reference_layer_count": len(layers),
        "reference_order_front_to_back": [provider["color"] for provider in providers],
        "containment_failures": containment_failures,
        "unassigned_target_pixels": unassigned,
        "source_reconstruction_mismatched_pixels": int(np.count_nonzero(difference)),
        "existing_plan_validation": validation,
        "layers": sorted(rows, key=lambda row: row["layer_front_to_back"]),
    }
    write_json(OUTPUT / "reference_validation.json", report)
    return report


def planner_worker() -> int:
    started = time.perf_counter()
    try:
        # The worker deliberately receives only the composite image. It does
        # not access reference masks, layer order, or fixture_report.json.
        rgb = np.asarray(Image.open(TARGET).convert("RGB"), dtype=np.uint8)
        labels, palette = exact_index(rgb)
        provider_labels, providers, provider_splits = _provider_regions(labels, palette)
        analysis = {
            "rgb": rgb,
            "labels": labels,
            "palette_rgb": palette,
            "palette_hex": [hex_color(color) for color in palette],
            "counts": np.bincount(labels.ravel(), minlength=len(palette)),
            "provider_labels": provider_labels,
            "providers": providers,
            "provider_splits": provider_splits,
        }
        PLANNER_ROOT.mkdir(parents=True, exist_ok=True)
        Image.fromarray(palette[labels], "RGB").save(PLANNER_ROOT / "indexed_target.png")
        graph = build_region_graph(labels, connectivity=4, minimum_contact_pixels=1)
        evaluator = planner._ActualMaskEvaluator(graph, DEFAULT_CONFIG, DEFAULT_OPTIMIZATION_CONFIG)
        max_layers = len(providers)
        write_json(
            PROGRESS,
            {
                "status": "PLANNING",
                "indexed_colors": len(palette),
                "connected_regions": len(graph["nodes"]),
                "greedy_provider_groups": len(providers),
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
                        "four_neighbor_components": edge["four_neighbor_components"],
                        "width_status": width["status"],
                        "orphan_structural_core_components": width["orphan_structural_core_components"],
                        "orphan_structural_core_pixels": width["orphan_structural_core_pixels"],
                        "missing_perimeter_pixels": int(layer["missing_perimeter_pixels"]),
                    }
                )
            final_valid = (
                validation["status"] == "PASS"
                and validation["geometry_mismatched_pixels"] == 0
                and validation["visible_automatic_connector_pixels"] == 0
                and all(row["four_neighbor_status"] == "PASS" for row in rows)
                and all(row["width_status"] == "PASS" for row in rows)
                and all(row["orphan_structural_core_components"] == 0 for row in rows)
                and all(row["missing_perimeter_pixels"] == 0 for row in rows)
            )
            colors = [layer["color"] for layer in layers]
            validation_summary = {
                "status": "PASS" if final_valid else "FAIL",
                "physical_layer_count": len(layers),
                "front_to_back_colors": [analysis["palette_hex"][index] for index in record.sequence],
                "back_to_front_colors": colors,
                "repeated_colors": {
                    color: count for color, count in Counter(colors).items() if count > 1
                },
                "geometry_mismatched_pixels_against_indexed_target": validation["geometry_mismatched_pixels"],
                "indexed_target_accuracy": validation["geometry_pixel_accuracy_against_quantized_target"],
                "visible_automatic_connector_pixels": validation["visible_automatic_connector_pixels"],
                "layers": rows,
            }
            if final_valid:
                layer_files = _save_layer_artifacts(PLANNER_ROOT, layers, planned, DEFAULT_CONFIG)
                Image.fromarray(reconstructed, "RGB").save(
                    PLANNER_ROOT / "reconstructed_front_view.png"
                )
                Image.fromarray(mismatch.astype(np.uint8) * 255, "L").save(
                    PLANNER_ROOT / "indexed_target_difference.png"
                )
                _render_exploded(layers, planned, PLANNER_ROOT / "exploded_layers.png")

        result = {
            "status": (
                "COMPLETE_VALID_PLAN_FOUND"
                if validation_summary and validation_summary["status"] == "PASS"
                else "COMPLETE_BUT_INVALID"
                if record is not None
                else "NO_COMPLETE_PLAN"
            ),
            "planner_input": TARGET.name + " only",
            "preprocessing": {
                "mode": "exact_rgb_lossless_indexing",
                "indexed_colors": len(palette),
                "palette_hex": analysis["palette_hex"],
                "connected_regions": len(graph["nodes"]),
                "greedy_provider_groups": len(providers),
                "changed_pixels_against_input": 0,
            },
            "search_seconds": search_seconds,
            "search_report": search_report,
            "record": asdict(record) if record is not None else None,
            "validation": validation_summary,
            "layer_files": layer_files,
            "worker_seconds": time.perf_counter() - started,
        }
        write_json(SEARCH_RESULT, result)
        write_json(PROGRESS, {"status": result["status"], "search_report": search_report})
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


def controller() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite prior run: {OUTPUT}")
    OUTPUT.mkdir(parents=True)
    reference = validate_resized_reference()
    if reference["status"] != "PASS":
        report = {
            "status": "REFERENCE_VALIDATION_FAILED",
            "reference_validation": reference,
            "planner_launched": False,
        }
        write_json(OUTPUT / "benchmark_report.json", report)
        print(json.dumps(report), flush=True)
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
        "partial_progress": json.loads(PROGRESS.read_text(encoding="utf-8")) if PROGRESS.exists() else None,
    }
    write_json(EXECUTION, execution)
    result = json.loads(SEARCH_RESULT.read_text(encoding="utf-8")) if SEARCH_RESULT.exists() else None
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
    raise SystemExit(planner_worker() if "--planner-worker" in sys.argv else controller())
