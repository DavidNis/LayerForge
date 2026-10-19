"""One bounded legacy-versus-diverse beam-selection comparison."""

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
    _provider_regions,
    _render_exploded,
    _save_layer_artifacts,
    construct_layers,
    discover_order,
    validate_plan,
)


INPUT_ROOT = ROOT / "artifacts" / "l1_sci_fi_preprocessing_diagnostic_v1"
INDEXED_TARGET = INPUT_ROOT / "indexed_target.png"
PREPROCESSING_REPORT = INPUT_ROOT / "preprocessing_report.json"
OUTPUT = ROOT / "artifacts" / "l1_sci_fi_search_selection_ab_v1"
MODES = ("legacy", "diverse")
TIMEOUT_SECONDS = 45


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_fixed_analysis() -> dict[str, Any]:
    report = json.loads(PREPROCESSING_REPORT.read_text(encoding="utf-8"))
    rgb = np.asarray(Image.open(INDEXED_TARGET).convert("RGB"), dtype=np.uint8)
    palette = np.asarray(report["palette_rgb"], dtype=np.uint8)
    lookup = {
        tuple(int(channel) for channel in color): index
        for index, color in enumerate(palette)
    }
    flat = rgb.reshape(-1, 3)
    unknown = {
        tuple(int(channel) for channel in color)
        for color in np.unique(flat, axis=0)
        if tuple(int(channel) for channel in color) not in lookup
    }
    if unknown:
        raise RuntimeError(f"indexed target contains colors outside fixed palette: {unknown}")
    labels = np.fromiter(
        (lookup[tuple(int(channel) for channel in color)] for color in flat),
        dtype=np.uint8,
        count=len(flat),
    ).reshape(rgb.shape[:2])
    provider_labels, providers, provider_splits = _provider_regions(labels, palette)
    return {
        "rgb": rgb,
        "labels": labels,
        "palette_rgb": palette,
        "palette_hex": report["palette_hex"],
        "counts": np.bincount(labels.ravel(), minlength=len(palette)),
        "provider_labels": provider_labels,
        "providers": providers,
        "provider_splits": provider_splits,
    }


def termination_reason(record: object, search_report: dict[str, Any]) -> str:
    if record is not None:
        return "COMPLETE_CANDIDATE_FOUND"
    frontier = search_report["frontier_widths"]
    if frontier and frontier[-1]["candidate_states"] == 0:
        return "SEARCH_FRONTIER_EXHAUSTED"
    if frontier and frontier[-1]["depth"] >= search_report["max_layers"]:
        return "LAYER_CEILING_REACHED_WITH_ACTIVE_OR_PRUNED_FRONTIER"
    return "NO_COMPLETE_CANDIDATE_WITHIN_BOUNDS"


def worker(mode: str) -> int:
    mode_root = OUTPUT / mode
    progress_path = mode_root / "progress.json"
    result_path = mode_root / "search_result.json"
    started = time.perf_counter()
    try:
        analysis = load_fixed_analysis()
        labels = np.asarray(analysis["labels"], dtype=np.uint8)
        graph = build_region_graph(labels, connectivity=4, minimum_contact_pixels=1)
        if len(graph["nodes"]) != 196 or len(analysis["palette_rgb"]) != 9:
            raise RuntimeError(
                f"fixed input changed: colors={len(analysis['palette_rgb'])}, regions={len(graph['nodes'])}"
            )
        evaluator = planner._ActualMaskEvaluator(
            graph, DEFAULT_CONFIG, DEFAULT_OPTIMIZATION_CONFIG
        )
        progress_rows: list[dict[str, Any]] = []

        def progress(row: dict[str, Any]) -> None:
            progress_rows.append(row)
            write_json(
                progress_path,
                {
                    "status": "PLANNING",
                    "selection_strategy": mode,
                    "latest_depth": row["depth"],
                    "frontier_widths": progress_rows,
                    "elapsed_seconds": time.perf_counter() - started,
                },
            )

        search_started = time.perf_counter()
        record, search_report = planner._search(
            graph,
            evaluator,
            max_layers=18,
            max_states=8,
            hard_width=True,
            singleton_limit=2,
            selection_strategy=mode,
            progress_callback=progress,
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
            layer_rows: list[dict[str, Any]] = []
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
                        "depth_back_to_front": int(layer["depth"]),
                        "color": layer["color"],
                        "four_neighbor_status": edge["four_neighbor_status"],
                        "four_neighbor_components": edge["four_neighbor_components"],
                        "width_status": width["status"],
                        "orphan_structural_core_components": width[
                            "orphan_structural_core_components"
                        ],
                        "orphan_structural_core_pixels": width[
                            "orphan_structural_core_pixels"
                        ],
                        "missing_perimeter_pixels": int(layer["missing_perimeter_pixels"]),
                    }
                )
            final_valid = (
                validation["status"] == "PASS"
                and validation["geometry_mismatched_pixels"] == 0
                and validation["visible_automatic_connector_pixels"] == 0
                and all(row["four_neighbor_status"] == "PASS" for row in layer_rows)
                and all(row["width_status"] == "PASS" for row in layer_rows)
                and all(
                    row["orphan_structural_core_components"] == 0
                    for row in layer_rows
                )
                and all(row["missing_perimeter_pixels"] == 0 for row in layer_rows)
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
                    color: count
                    for color, count in Counter(colors).items()
                    if count > 1
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
                "layers": layer_rows,
            }
            if final_valid:
                layer_files = _save_layer_artifacts(
                    mode_root, layers, planned, DEFAULT_CONFIG
                )
                Image.fromarray(reconstructed, "RGB").save(
                    mode_root / "reconstructed_front_view.png"
                )
                Image.fromarray(mismatch.astype(np.uint8) * 255, "L").save(
                    mode_root / "indexed_target_difference.png"
                )
                _render_exploded(
                    layers, planned, mode_root / "exploded_layers.png"
                )

        result = {
            "status": (
                "COMPLETE_VALID_PLAN_FOUND"
                if validation_summary and validation_summary["status"] == "PASS"
                else "COMPLETE_BUT_INVALID"
                if record is not None
                else "NO_COMPLETE_PLAN"
            ),
            "selection_strategy": mode,
            "fixed_input": {
                "indexed_target": str(INDEXED_TARGET.relative_to(ROOT)),
                "indexed_colors": len(analysis["palette_rgb"]),
                "palette_hex": analysis["palette_hex"],
                "connected_regions": len(graph["nodes"]),
                "greedy_provider_groups": len(analysis["providers"]),
                "changed_pixels": 0,
            },
            "configuration": {
                "max_layers": 18,
                "max_states_per_depth": 8,
                "hard_width_gate": True,
                "minimum_structural_width_mm": 1.2,
                "candidate_singletons_per_color": 2,
            },
            "search_seconds": search_seconds,
            "termination_reason": termination_reason(record, search_report),
            "deepest_search_depth": max(
                (row["depth"] for row in search_report["frontier_widths"]),
                default=0,
            ),
            "search_report": search_report,
            "record": asdict(record) if record is not None else None,
            "validation": validation_summary,
            "layer_files": layer_files,
            "worker_seconds": time.perf_counter() - started,
        }
        write_json(result_path, result)
        write_json(progress_path, {"status": result["status"], **result})
        print(json.dumps(result), flush=True)
        return 0
    except Exception:
        failure = {
            "status": "ERROR",
            "selection_strategy": mode,
            "traceback": traceback.format_exc(),
            "worker_seconds": time.perf_counter() - started,
        }
        write_json(progress_path, failure)
        traceback.print_exc()
        return 1


def summarize_run(mode: str, execution: dict[str, Any]) -> dict[str, Any]:
    result_path = OUTPUT / mode / "search_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else None
    partial_path = OUTPUT / mode / "progress.json"
    partial = json.loads(partial_path.read_text(encoding="utf-8")) if partial_path.exists() else None
    return {
        "execution": execution,
        "result": result,
        "partial_progress": partial if result is None else None,
    }


def controller() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite prior comparison: {OUTPUT}")
    if not INDEXED_TARGET.is_file() or not PREPROCESSING_REPORT.is_file():
        raise FileNotFoundError("fixed sci-fi indexed target or palette report is missing")
    OUTPUT.mkdir(parents=True)
    shutil.copy2(INDEXED_TARGET, OUTPUT / "indexed_target_preserved.png")
    comparison: dict[str, Any] = {
        "status": "IN_PROGRESS",
        "input": str(INDEXED_TARGET.relative_to(ROOT)),
        "runs": {},
    }
    for mode in MODES:
        started = time.perf_counter()
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker", mode],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            stdout, stderr = process.communicate()
        execution = {
            "timed_out": timed_out,
            "timeout_seconds": TIMEOUT_SECONDS,
            "exit_code": process.returncode,
            "wall_seconds": time.perf_counter() - started,
            "stdout": stdout,
            "stderr": stderr,
        }
        write_json(OUTPUT / mode / "execution_report.json", execution)
        comparison["runs"][mode] = summarize_run(mode, execution)
        write_json(OUTPUT / "comparison_report.json", comparison)

    results = [comparison["runs"][mode]["result"] for mode in MODES]
    comparison["status"] = (
        "COMPLETE_VALID_PLAN_FOUND"
        if any(result and result["status"] == "COMPLETE_VALID_PLAN_FOUND" for result in results)
        else "INCONCLUSIVE_NO_COMPLETE_VALID_PLAN"
    )
    write_json(OUTPUT / "comparison_report.json", comparison)
    print(json.dumps(comparison), flush=True)
    return 0


if __name__ == "__main__":
    if "--worker" in sys.argv:
        raise SystemExit(worker(sys.argv[sys.argv.index("--worker") + 1]))
    raise SystemExit(controller())
