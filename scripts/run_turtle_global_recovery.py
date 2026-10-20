"""One bounded geometry-feedback recovery run for the saved turtle target.

The script deliberately reuses the experimental CP-SAT model, exact-prefix
no-good cut, mask construction, and existing validators.  It does not alter
the source target, palette, geometry rules, or production planner.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from layerforge_l1.connectivity_repair import (
    audit_connection_width,
    audit_edge_connectivity,
)
from layerforge_l1.experimental_global_planner import (
    _add_exact_prefix_no_good,
    _extract_assignment,
    _materialize_analysis,
    _validate_assignment_geometry,
    build_fixed_depth_model,
)
from layerforge_l1.layer_optimization import (
    DEFAULT_OPTIMIZATION_CONFIG,
    build_region_graph,
)
from layerforge_l1.subtractive_poc import (
    DEFAULT_CONFIG,
    _render_exploded,
    _save_layer_artifacts,
    construct_layers,
    discover_order,
    validate_plan,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "artifacts" / "l1_turtle_on_leaf_global_planner_v2"
TARGET = BASELINE / "automatic" / "indexed_target.png"
BASELINE_REPORT = BASELINE / "reports" / "checkpoint_report.json"
OUTPUT = ROOT / "artifacts" / "l1_turtle_on_leaf_global_recovery_v1"
FIXED_LAYERS = 12
TIME_LIMIT_SECONDS = 90.0
MAX_GEOMETRY_CANDIDATES = 20
RANDOM_SEED = 74031
SOLVER_WORKERS = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _load_unchanged_indexed_target() -> dict[str, Any]:
    checkpoint = json.loads(BASELINE_REPORT.read_text(encoding="utf-8"))
    palette_hex = checkpoint["input"]["palette_hex"]
    palette = np.asarray(
        [[int(color[index : index + 2], 16) for index in (1, 3, 5)] for color in palette_hex],
        dtype=np.uint8,
    )
    rgb = np.asarray(Image.open(TARGET).convert("RGB"), dtype=np.uint8)
    labels = np.full(rgb.shape[:2], -1, dtype=np.int16)
    for palette_index, color in enumerate(palette):
        labels[np.all(rgb == color, axis=2)] = palette_index
    if np.any(labels < 0):
        raise RuntimeError("target contains a color not present in its saved palette")
    return {
        "rgb": rgb,
        "labels": labels.astype(np.uint8),
        "palette_rgb": palette,
        "palette_hex": palette_hex,
        "counts": np.bincount(labels.ravel(), minlength=len(palette)),
        "original_size": tuple(reversed(rgb.shape[:2])),
    }


def _validate_complete(
    base: dict[str, Any],
    graph: dict[str, Any],
    depths: list[int],
    colors: list[int],
    groups: list[list[int]],
) -> tuple[dict[str, Any], list[dict[str, Any]], np.ndarray, np.ndarray, list[dict[str, Any]]]:
    analysis = _materialize_analysis(base, graph, depths, colors, groups)
    order = discover_order(analysis, DEFAULT_CONFIG)
    if order["status"] != "FEASIBLE":
        return analysis, [], np.empty((0,)), np.empty((0,)), [{"order": order}]
    layers = construct_layers(analysis, order, DEFAULT_CONFIG)
    validation, reconstruction, difference = validate_plan(
        analysis, order, layers, DEFAULT_CONFIG
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
                "physical_layer_index_back_to_front": int(layer["depth"]),
                "front_to_back_depth": int(layer["provider_index"]),
                "color": layer["color"],
                "four_neighbor_status": edge["four_neighbor_status"],
                "four_neighbor_components": edge["four_neighbor_components"],
                "four_neighbor_orphan_components": edge["four_neighbor_orphan_components"],
                "width_status": width["status"],
                "orphan_structural_core_components": width[
                    "orphan_structural_core_components"
                ],
                "orphan_structural_core_pixels": width["orphan_structural_core_pixels"],
                "missing_perimeter_pixels": int(layer["missing_perimeter_pixels"]),
                "cutout_is_exact_physical_complement": bool(
                    np.array_equal(layer["cutout_mask"], ~layer["physical_mask"])
                ),
            }
        )
    fully_valid = (
        validation["status"] == "PASS"
        and validation["geometry_mismatched_pixels"] == 0
        and validation["visible_automatic_connector_pixels"] == 0
        and all(row["four_neighbor_status"] == "PASS" for row in rows)
        and all(row["four_neighbor_components"] == 1 for row in rows)
        and all(row["four_neighbor_orphan_components"] == 0 for row in rows)
        and all(row["width_status"] == "PASS" for row in rows)
        and all(row["orphan_structural_core_components"] == 0 for row in rows)
        and all(row["missing_perimeter_pixels"] == 0 for row in rows)
        and all(row["cutout_is_exact_physical_complement"] for row in rows)
    )
    summary = {
        "status": "PASS" if fully_valid else "FAIL",
        "geometry_mismatched_pixels": validation["geometry_mismatched_pixels"],
        "indexed_target_accuracy": validation[
            "geometry_pixel_accuracy_against_quantized_target"
        ],
        "visible_automatic_connector_pixels": validation[
            "visible_automatic_connector_pixels"
        ],
        "identical_outer_dimensions": len(
            {tuple(layer["physical_mask"].shape) for layer in layers}
        )
        == 1,
        "layer_rows": rows,
    }
    return analysis, layers, reconstruction, difference, [summary]


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUTPUT}")
    for directory in ("automatic", "previews", "reports"):
        (OUTPUT / directory).mkdir(parents=True, exist_ok=True)
    shutil.copy2(TARGET, OUTPUT / "automatic" / "indexed_target.png")

    from ortools.sat.python import cp_model
    import ortools

    base = _load_unchanged_indexed_target()
    graph = build_region_graph(base["labels"], connectivity=4, minimum_contact_pixels=1)
    model, variables, model_description = build_fixed_depth_model(graph, FIXED_LAYERS)
    started = time.perf_counter()
    deadline = started + TIME_LIMIT_SECONDS
    no_goods: set[tuple[int, int]] = set()
    evaluated_assignments: set[tuple[int, ...]] = set()
    iterations: list[dict[str, Any]] = []
    final = None
    termination = "TIME_LIMIT"
    solver_final_status = "UNKNOWN"

    while len(evaluated_assignments) < MAX_GEOMETRY_CANDIDATES:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            termination = "TIME_LIMIT"
            break
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.num_search_workers = SOLVER_WORKERS
        solver.parameters.random_seed = RANDOM_SEED
        solver.parameters.stop_after_first_solution = True
        solver.parameters.log_search_progress = False
        solve_started = time.perf_counter()
        status = solver.solve(model)
        solver_final_status = solver.status_name(status)
        row: dict[str, Any] = {
            "iteration": len(iterations) + 1,
            "solver_status": solver_final_status,
            "wall_seconds": time.perf_counter() - solve_started,
            "solver_wall_seconds": solver.wall_time,
            "branches": solver.num_branches,
            "conflicts": solver.num_conflicts,
            "stop_after_first_solution": True,
        }
        iterations.append(row)
        if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
            termination = (
                "FIXED_MODEL_INFEASIBLE_AFTER_EXACT_PREFIX_CUTS"
                if status == cp_model.INFEASIBLE
                else "TIME_LIMIT"
            )
            break

        depths, colors, groups = _extract_assignment(
            solver, variables, graph, FIXED_LAYERS
        )
        assignment_key = tuple(depths)
        row["front_to_back_colors"] = [base["palette_hex"][color] for color in colors]
        row["regions_per_depth"] = [len(group) for group in groups]
        if assignment_key in evaluated_assignments:
            termination = "REPEATED_ASSIGNMENT_WITHOUT_NEW_INFORMATION"
            row["duplicate_geometry_assignment"] = True
            break
        evaluated_assignments.add(assignment_key)
        geometry_rows, failing_depths = _validate_assignment_geometry(
            graph, depths, FIXED_LAYERS, DEFAULT_CONFIG
        )
        row["geometry_failing_depths"] = failing_depths
        row["geometry"] = geometry_rows
        if not failing_depths:
            final = (depths, colors, groups)
            termination = "GEOMETRY_VALID_COMPLETE_ASSIGNMENT"
            break

        cuts = []
        for rejected_depth in failing_depths:
            prefix_ids = [
                region for region, assigned_depth in enumerate(depths) if assigned_depth <= rejected_depth
            ]
            prefix_bits = sum(1 << region for region in prefix_ids)
            signature = (rejected_depth, prefix_bits)
            if signature in no_goods:
                continue
            no_goods.add(signature)
            _add_exact_prefix_no_good(model, variables, depths, rejected_depth)
            cuts.append(
                {
                    "front_to_back_depth": rejected_depth,
                    "exact_prefix_region_ids": prefix_ids,
                    "exact_prefix_bitset_hex": hex(prefix_bits),
                }
            )
        row["exact_prefix_no_good_cuts_added"] = cuts
        if not cuts:
            termination = "INVALID_ASSIGNMENT_WITHOUT_NEW_EXACT_PREFIX_CUT"
            break
    else:
        termination = "GEOMETRY_CANDIDATE_LIMIT"

    result_status = "UNKNOWN_NO_VALID_STACK_WITHIN_BOUNDS"
    assignment = None
    validation = None
    layer_files = None
    if final is not None:
        depths, colors, groups = final
        analysis, layers, reconstruction, difference, validation_rows = _validate_complete(
            base, graph, depths, colors, groups
        )
        validation = validation_rows[0]
        if layers and validation["status"] == "PASS":
            result_status = "COMPLETE_VALID_PLAN_FOUND"
            assignment = {
                "front_to_back": [
                    {
                        "depth": depth,
                        "color": base["palette_hex"][colors[depth]],
                        "source_region_ids": groups[depth],
                    }
                    for depth in range(FIXED_LAYERS)
                ],
                "region_to_depth": depths,
            }
            layer_files = _save_layer_artifacts(OUTPUT, layers, analysis, DEFAULT_CONFIG)
            Image.fromarray(reconstruction, "RGB").save(
                OUTPUT / "previews" / "reconstructed_front.png"
            )
            Image.fromarray(difference.astype(np.uint8) * 255, "L").save(
                OUTPUT / "previews" / "indexed_target_difference.png"
            )
            _render_exploded(layers, analysis, OUTPUT / "previews" / "exploded_layers.png")
            _write_json(OUTPUT / "automatic" / "layer_assignment.json", assignment)
        else:
            result_status = "GEOMETRY_ORACLE_PASS_BUT_FINAL_VALIDATION_FAILED"

    report = {
        "status": result_status,
        "review_state": "READY_FOR_USER_TEST",
        "input": {
            "indexed_target": str(TARGET.relative_to(ROOT)).replace("\\", "/"),
            "indexed_target_sha256": _sha256(TARGET),
            "copied_indexed_target_sha256": _sha256(
                OUTPUT / "automatic" / "indexed_target.png"
            ),
            "palette_hex": base["palette_hex"],
            "palette_count": len(base["palette_hex"]),
            "source_region_count": len(graph["nodes"]),
            "image_or_palette_modified": False,
        },
        "configuration": {
            "fixed_layer_ceiling_used_as_exact_depth_in_current_backend": FIXED_LAYERS,
            "time_limit_seconds": TIME_LIMIT_SECONDS,
            "maximum_geometry_candidates": MAX_GEOMETRY_CANDIDATES,
            "random_seed": RANDOM_SEED,
            "solver_workers": SOLVER_WORKERS,
            "stop_after_first_solution_per_geometry_iteration": True,
        },
        "model": model_description,
        "solver": {
            "ortools_version": ortools.__version__,
            "final_status": solver_final_status,
            "termination_reason": termination,
            "total_wall_seconds": time.perf_counter() - started,
            "iterations": iterations,
        },
        "geometry_oracle": {
            "complete_assignments_evaluated": len(evaluated_assignments),
            "exact_prefix_no_good_cuts": len(no_goods),
            "cut_soundness_scope": (
                "Each cut excludes only one exact cumulative region set at one depth. "
                "That set deterministically produces the rejected physical prefix mask."
            ),
        },
        "assignment": assignment,
        "validation": validation,
        "layer_files": layer_files,
        "limitations": [
            "The current backend treats fixed_layers=12 as exactly twelve non-empty layers, not up to twelve.",
            "Each connected visible source-color region must be assigned wholly to one depth.",
            "An UNKNOWN result is bounded-search evidence, not geometric infeasibility.",
            "Printable opening size is not established by the retained-material width validator.",
        ],
        "cad_generated": False,
    }
    _write_json(OUTPUT / "reports" / "recovery_report.json", report)
    _write_json(OUTPUT / "reports" / "model.json", model_description)
    print(
        json.dumps(
            {
                "status": result_status,
                "termination": termination,
                "wall_seconds": report["solver"]["total_wall_seconds"],
                "geometry_candidates": len(evaluated_assignments),
                "exact_prefix_cuts": len(no_goods),
            },
            indent=2,
        ),
        flush=True,
    )
    return 0 if result_status == "COMPLETE_VALID_PLAN_FOUND" else 2


if __name__ == "__main__":
    raise SystemExit(main())
