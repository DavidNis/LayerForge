"""One bounded fixed-K global-planning experiment for the approved turtle target."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

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
    load_lossless_target,
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
SOURCE = (
    ROOT
    / "artifacts"
    / "l1_turtle_final_two_contours_200mm_v1"
    / "previews"
    / "final_corrected_candidate.png"
)
OUTPUT = ROOT / "artifacts" / "l1_turtle_final_global_planning_200mm_v1"
MIN_FIXED_LAYERS = 9
MAX_FIXED_LAYERS = 18
TOTAL_TIME_LIMIT_SECONDS = 180.0
PER_K_TIME_LIMIT_SECONDS = 18.0
MAX_GEOMETRY_CANDIDATES = 60
MAX_GEOMETRY_CANDIDATES_PER_K = 6
RANDOM_SEED = 74031
SOLVER_WORKERS = 1
PHYSICAL_SHEET_MM = 200.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _opening_warnings(mask: np.ndarray, mm_per_pixel: float) -> dict[str, Any]:
    """Report small cutouts separately; this is not a retained-width failure."""

    openings = ~mask
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        openings.astype(np.uint8), connectivity=4
    )
    narrow: list[dict[str, Any]] = []
    for component in range(1, count):
        component_mask = (labels == component).astype(np.uint8)
        distance = cv2.distanceTransform(component_mask, cv2.DIST_L2, 5)
        maximum_width_mm = float(distance.max() * 2.0 * mm_per_pixel)
        if maximum_width_mm < 1.2:
            narrow.append(
                {
                    "component": component,
                    "area_pixels": int(stats[component, cv2.CC_STAT_AREA]),
                    "bounds_pixels": [int(value) for value in stats[component, :4]],
                    "maximum_inscribed_width_mm": maximum_width_mm,
                }
            )
    return {
        "opening_components": count - 1,
        "narrow_opening_components_under_1_2mm": len(narrow),
        "narrow_openings": narrow,
        "acceptance_effect": "DIAGNOSTIC_ONLY_NOT_RETAINED_WIDTH_FAILURE",
    }


def _validate_complete(
    base: dict[str, Any],
    graph: dict[str, Any],
    depths: list[int],
    colors: list[int],
    groups: list[list[int]],
    config: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]], np.ndarray, np.ndarray, list[dict[str, Any]]]:
    analysis = _materialize_analysis(base, graph, depths, colors, groups)
    order = discover_order(analysis, config)
    if order["status"] != "FEASIBLE":
        return analysis, [], np.empty((0,)), np.empty((0,)), [{"order": order}]
    layers = construct_layers(analysis, order, config)
    validation, reconstruction, difference = validate_plan(
        analysis, order, layers, config
    )
    mm_per_pixel = config.sheet_size_mm / config.outer_pixels
    rows: list[dict[str, Any]] = []
    for layer in layers:
        edge, _ = audit_edge_connectivity(layer["physical_mask"])
        width, _ = audit_connection_width(
            layer["physical_mask"],
            layer["visible_aperture"],
            config,
            DEFAULT_OPTIMIZATION_CONFIG,
        )
        rows.append(
            {
                "physical_layer_index_back_to_front": int(layer["depth"]),
                "front_to_back_depth": int(layer["provider_index"]),
                "color": layer["color"],
                "four_neighbor_status": edge["four_neighbor_status"],
                "four_neighbor_components": edge["four_neighbor_components"],
                "four_neighbor_orphan_components": edge[
                    "four_neighbor_orphan_components"
                ],
                "width_status": width["status"],
                "orphan_structural_core_components": width[
                    "orphan_structural_core_components"
                ],
                "orphan_structural_core_pixels": width[
                    "orphan_structural_core_pixels"
                ],
                "thin_decorative_visible_components": width[
                    "thin_decorative_visible_components"
                ],
                "missing_perimeter_pixels": int(layer["missing_perimeter_pixels"]),
                "cutout_is_exact_physical_complement": bool(
                    np.array_equal(layer["cutout_mask"], ~layer["physical_mask"])
                ),
                "opening_printability": _opening_warnings(
                    layer["physical_mask"], mm_per_pixel
                ),
            }
        )
    fully_valid = (
        validation["status"] == "PASS"
        and validation["geometry_mismatched_pixels"] == 0
        and validation["visible_automatic_connector_pixels"] == 0
        and validation["exposed_hidden_material_pixels"] == 0
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
        "exposed_hidden_material_pixels": validation[
            "exposed_hidden_material_pixels"
        ],
        "identical_outer_dimensions": len(
            {tuple(layer["physical_mask"].shape) for layer in layers}
        )
        == 1,
        "outer_pixels": [config.outer_pixels, config.outer_pixels],
        "outer_size_mm": [config.sheet_size_mm, config.sheet_size_mm],
        "aperture_pixels": [config.analysis_pixels, config.analysis_pixels],
        "aperture_size_mm": [
            config.sheet_size_mm * config.analysis_pixels / config.outer_pixels,
            config.sheet_size_mm * config.analysis_pixels / config.outer_pixels,
        ],
        "unsupported_thin_detail_count": validation[
            "unsupported_thin_detail_count"
        ],
        "layers": rows,
    }
    return analysis, layers, reconstruction, difference, [summary]


def _contact_sheet(
    layers: list[dict[str, Any]], analysis: dict[str, Any], output: Path
) -> None:
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    tile = 240
    label_height = 34
    columns = 4
    rows = (len(layers) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * tile, rows * (tile + label_height)), "white")
    draw = ImageDraw.Draw(canvas)
    for index, layer in enumerate(reversed(layers)):
        preview = np.full((*layer["physical_mask"].shape, 3), 250, dtype=np.uint8)
        preview[layer["physical_mask"]] = palette[layer["palette_index"]]
        image = Image.fromarray(preview, "RGB").resize((tile, tile), Image.Resampling.NEAREST)
        x = (index % columns) * tile
        y = (index // columns) * (tile + label_height)
        canvas.paste(image, (x, y + label_height))
        draw.text(
            (x + 6, y + 8),
            f"front depth {layer['provider_index']:02d} {layer['color']}",
            fill="black",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def _invalid_score(candidate: dict[str, Any]) -> tuple[int, int, int]:
    rows = candidate["geometry"]
    return (
        len(candidate["failing_depths"]),
        sum(int(row["orphan_structural_core_pixels"]) for row in rows),
        candidate["fixed_layers"],
    )


def main() -> int:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUTPUT}")
    for directory in ("source", "automatic", "previews", "reports"):
        (OUTPUT / directory).mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, OUTPUT / "source" / SOURCE.name)

    from ortools.sat.python import cp_model
    import ortools

    config = replace(DEFAULT_CONFIG, sheet_size_mm=PHYSICAL_SHEET_MM)
    source_rgb = np.asarray(Image.open(SOURCE).convert("RGB"), dtype=np.uint8)
    if source_rgb.shape != (512, 512, 3):
        raise RuntimeError(f"locked source has unexpected shape: {source_rgb.shape}")
    base = load_lossless_target(SOURCE, config)
    if not np.array_equal(source_rgb, base["rgb"]):
        raise RuntimeError("lossless target adapter changed the approved source")
    if len(base["palette_rgb"]) != 9:
        raise RuntimeError(f"locked source has {len(base['palette_rgb'])} colors, expected 9")
    target = np.asarray(base["palette_rgb"], dtype=np.uint8)[base["labels"]]
    Image.fromarray(target, "RGB").save(OUTPUT / "automatic" / "indexed_target.png")
    graph = build_region_graph(base["labels"], connectivity=4, minimum_contact_pixels=1)

    experiment_started = time.perf_counter()
    global_deadline = experiment_started + TOTAL_TIME_LIMIT_SECONDS
    total_geometry_candidates = 0
    total_no_goods = 0
    k_results: list[dict[str, Any]] = []
    rejected_prefixes: list[dict[str, Any]] = []
    best_invalid: dict[str, Any] | None = None
    final: dict[str, Any] | None = None

    for fixed_layers in range(MIN_FIXED_LAYERS, MAX_FIXED_LAYERS + 1):
        if time.perf_counter() >= global_deadline:
            break
        print(f"[{time.perf_counter() - experiment_started:8.3f}s] build K={fixed_layers}", flush=True)
        build_started = time.perf_counter()
        model, variables, model_description = build_fixed_depth_model(graph, fixed_layers)
        k_deadline = min(global_deadline, time.perf_counter() + PER_K_TIME_LIMIT_SECONDS)
        k_row: dict[str, Any] = {
            "fixed_layers": fixed_layers,
            "model_build_seconds": time.perf_counter() - build_started,
            "model": model_description,
            "iterations": [],
            "termination": "K_TIME_LIMIT",
        }
        evaluated: set[tuple[int, ...]] = set()
        local_no_goods: set[tuple[int, int]] = set()

        while (
            len(evaluated) < MAX_GEOMETRY_CANDIDATES_PER_K
            and total_geometry_candidates < MAX_GEOMETRY_CANDIDATES
            and time.perf_counter() < k_deadline
        ):
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = max(0.001, k_deadline - time.perf_counter())
            solver.parameters.num_search_workers = SOLVER_WORKERS
            solver.parameters.random_seed = RANDOM_SEED
            solver.parameters.stop_after_first_solution = True
            solver.parameters.log_search_progress = False
            solve_started = time.perf_counter()
            status = solver.solve(model)
            iteration: dict[str, Any] = {
                "iteration": len(k_row["iterations"]) + 1,
                "solver_status": solver.status_name(status),
                "wall_seconds": time.perf_counter() - solve_started,
                "solver_wall_seconds": solver.wall_time,
                "branches": solver.num_branches,
                "conflicts": solver.num_conflicts,
            }
            k_row["iterations"].append(iteration)
            print(
                f"[{time.perf_counter() - experiment_started:8.3f}s] "
                f"K={fixed_layers} solve={iteration['solver_status']}",
                flush=True,
            )
            if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
                k_row["termination"] = (
                    "MODEL_INFEASIBLE_AFTER_SOUND_PREFIX_CUTS"
                    if status == cp_model.INFEASIBLE
                    else "K_TIME_LIMIT"
                )
                break

            depths, colors, groups = _extract_assignment(
                solver, variables, graph, fixed_layers
            )
            key = tuple(depths)
            if key in evaluated:
                k_row["termination"] = "REPEATED_ASSIGNMENT_WITHOUT_NEW_INFORMATION"
                break
            evaluated.add(key)
            total_geometry_candidates += 1
            geometry_rows, failing_depths = _validate_assignment_geometry(
                graph, depths, fixed_layers, config
            )
            iteration["front_to_back_colors"] = [
                base["palette_hex"][color] for color in colors
            ]
            iteration["regions_per_depth"] = [len(group) for group in groups]
            iteration["geometry_failing_depths"] = failing_depths
            iteration["geometry"] = geometry_rows
            print(
                f"[{time.perf_counter() - experiment_started:8.3f}s] "
                f"K={fixed_layers} oracle={total_geometry_candidates} failures={failing_depths}",
                flush=True,
            )

            if not failing_depths:
                analysis, layers, reconstruction, difference, validation_rows = _validate_complete(
                    base, graph, depths, colors, groups, config
                )
                validation = validation_rows[0]
                iteration["complete_validation"] = validation
                if layers and validation["status"] == "PASS":
                    final = {
                        "fixed_layers": fixed_layers,
                        "depths": depths,
                        "colors": colors,
                        "groups": groups,
                        "analysis": analysis,
                        "layers": layers,
                        "reconstruction": reconstruction,
                        "difference": difference,
                        "validation": validation,
                    }
                    k_row["termination"] = "COMPLETE_VALID_PLAN_FOUND"
                    break
                k_row["termination"] = "ORACLE_PASS_BUT_FINAL_VALIDATION_FAILED"
                break

            candidate = {
                "fixed_layers": fixed_layers,
                "depths": depths,
                "colors": colors,
                "groups": groups,
                "geometry": geometry_rows,
                "failing_depths": failing_depths,
            }
            if best_invalid is None or _invalid_score(candidate) < _invalid_score(best_invalid):
                best_invalid = candidate

            cuts_added = 0
            for rejected_depth in failing_depths:
                prefix_ids = [
                    region
                    for region, assigned_depth in enumerate(depths)
                    if assigned_depth <= rejected_depth
                ]
                prefix_bits = sum(1 << region for region in prefix_ids)
                signature = (rejected_depth, prefix_bits)
                if signature in local_no_goods:
                    continue
                local_no_goods.add(signature)
                _add_exact_prefix_no_good(model, variables, depths, rejected_depth)
                cut = {
                    "fixed_layers": fixed_layers,
                    "front_to_back_depth": rejected_depth,
                    "exact_prefix_region_ids": prefix_ids,
                    "exact_prefix_bitset_hex": hex(prefix_bits),
                    "failure": geometry_rows[rejected_depth],
                }
                rejected_prefixes.append(cut)
                cuts_added += 1
                total_no_goods += 1
            iteration["exact_prefix_no_good_cuts_added"] = cuts_added
            if cuts_added == 0:
                k_row["termination"] = "INVALID_ASSIGNMENT_WITHOUT_NEW_PREFIX_CUT"
                break

        else:
            if total_geometry_candidates >= MAX_GEOMETRY_CANDIDATES:
                k_row["termination"] = "GLOBAL_GEOMETRY_CANDIDATE_LIMIT"
            elif len(evaluated) >= MAX_GEOMETRY_CANDIDATES_PER_K:
                k_row["termination"] = "PER_K_GEOMETRY_CANDIDATE_LIMIT"
            else:
                k_row["termination"] = "K_TIME_LIMIT"

        k_row["geometry_candidates_evaluated"] = len(evaluated)
        k_row["exact_prefix_no_good_cuts"] = len(local_no_goods)
        k_results.append(k_row)
        if final is not None or total_geometry_candidates >= MAX_GEOMETRY_CANDIDATES:
            break

    status = "UNKNOWN_NO_VALID_STACK_WITHIN_BOUNDS"
    assignment = None
    validation = None
    layer_files = None
    repeated_colors: dict[str, list[int]] = {}
    if final is not None:
        status = "COMPLETE_VALID_PLAN_FOUND"
        colors_hex = [base["palette_hex"][color] for color in final["colors"]]
        repeated_colors = {
            color: [index for index, value in enumerate(colors_hex) if value == color]
            for color in sorted(set(colors_hex))
            if colors_hex.count(color) > 1
        }
        assignment = {
            "front_to_back": [
                {
                    "depth": depth,
                    "color": colors_hex[depth],
                    "palette_index": final["colors"][depth],
                    "source_region_ids": final["groups"][depth],
                }
                for depth in range(final["fixed_layers"])
            ],
            "region_to_depth": final["depths"],
            "repeated_color_depths": repeated_colors,
        }
        layer_files = _save_layer_artifacts(
            OUTPUT, final["layers"], final["analysis"], config
        )
        Image.fromarray(final["reconstruction"], "RGB").save(
            OUTPUT / "previews" / "assembled_front_from_masks.png"
        )
        Image.fromarray(final["difference"].astype(np.uint8) * 255, "L").save(
            OUTPUT / "previews" / "reconstruction_difference.png"
        )
        _render_exploded(
            final["layers"],
            final["analysis"],
            OUTPUT / "previews" / "exploded_front_to_back.png",
        )
        _contact_sheet(
            final["layers"],
            final["analysis"],
            OUTPUT / "previews" / "individual_sheet_contact.png",
        )
        validation = final["validation"]
        _write_json(OUTPUT / "automatic" / "layer_assignment.json", assignment)
    elif best_invalid is not None:
        analysis, layers, reconstruction, difference, validation_rows = _validate_complete(
            base,
            graph,
            best_invalid["depths"],
            best_invalid["colors"],
            best_invalid["groups"],
            config,
        )
        rejected_root = OUTPUT / "rejected_best_candidate"
        (rejected_root / "previews").mkdir(parents=True, exist_ok=True)
        layer_files = _save_layer_artifacts(rejected_root, layers, analysis, config)
        Image.fromarray(reconstruction, "RGB").save(
            rejected_root / "previews" / "assembled_front_from_masks.png"
        )
        Image.fromarray(difference.astype(np.uint8) * 255, "L").save(
            rejected_root / "previews" / "reconstruction_difference.png"
        )
        _render_exploded(
            layers, analysis, rejected_root / "previews" / "exploded_front_to_back.png"
        )
        _contact_sheet(
            layers, analysis, rejected_root / "previews" / "individual_sheet_contact.png"
        )
        validation = validation_rows[0]
        assignment = {
            "status": "REJECTED_NOT_PRINTABLE",
            "fixed_layers": best_invalid["fixed_layers"],
            "front_to_back": [
                {
                    "depth": depth,
                    "color": base["palette_hex"][best_invalid["colors"][depth]],
                    "source_region_ids": best_invalid["groups"][depth],
                }
                for depth in range(best_invalid["fixed_layers"])
            ],
            "region_to_depth": best_invalid["depths"],
            "geometry_failing_depths": best_invalid["failing_depths"],
            "warning": "Diagnostic only; this is not a printable or approved stack.",
        }
        _write_json(rejected_root / "rejected_assignment.json", assignment)

    tested_k = [row["fixed_layers"] for row in k_results]
    report = {
        "status": status,
        "review_state": "READY_FOR_USER_TEST",
        "milestone_approval": "L1_NOT_APPROVED",
        "input": {
            "path": SOURCE.relative_to(ROOT).as_posix(),
            "sha256": _sha256(SOURCE),
            "copied_source_sha256": _sha256(OUTPUT / "source" / SOURCE.name),
            "indexed_target_sha256": _sha256(OUTPUT / "automatic" / "indexed_target.png"),
            "resolution": [512, 512],
            "palette_count": len(base["palette_rgb"]),
            "palette_hex": base["palette_hex"],
            "connected_source_regions": len(graph["nodes"]),
            "lossless_exact_indexing": True,
            "source_or_palette_modified": False,
        },
        "configuration": {
            "physical_sheet_mm": [PHYSICAL_SHEET_MM, PHYSICAL_SHEET_MM],
            "minimum_retained_width_mm": 1.2,
            "minimum_fixed_layers": MIN_FIXED_LAYERS,
            "maximum_fixed_layers": MAX_FIXED_LAYERS,
            "tested_fixed_layer_counts": tested_k,
            "total_time_limit_seconds": TOTAL_TIME_LIMIT_SECONDS,
            "per_k_time_limit_seconds": PER_K_TIME_LIMIT_SECONDS,
            "maximum_geometry_candidates": MAX_GEOMETRY_CANDIDATES,
            "maximum_geometry_candidates_per_k": MAX_GEOMETRY_CANDIDATES_PER_K,
            "random_seed": RANDOM_SEED,
            "solver_workers": SOLVER_WORKERS,
        },
        "solver": {
            "ortools_version": ortools.__version__,
            "total_wall_seconds": time.perf_counter() - experiment_started,
            "fixed_k_results": k_results,
        },
        "geometry_oracle": {
            "complete_assignments_evaluated": total_geometry_candidates,
            "exact_prefix_no_good_cuts": total_no_goods,
            "rejected_prefixes": rejected_prefixes,
            "cut_soundness_scope": (
                "Each cut excludes only the exact cumulative source-region set at "
                "the failing depth that deterministically produced the invalid mask."
            ),
        },
        "assignment": assignment,
        "validation": validation,
        "layer_files": layer_files,
        "optimality": {
            "globally_minimal_proven": False,
            "statement": (
                "A result is the first valid fixed-K stack found in the ascending "
                "bounded scan. Counts ending in timeout or candidate-limit status "
                "are not proofs of infeasibility."
            ),
        },
        "limitations": [
            "Each connected visible source-color region is indivisible and must occupy one depth.",
            "A bounded UNKNOWN result is not proof of geometric infeasibility.",
            "Narrow through-openings are diagnostic warnings separate from retained-width acceptance.",
            "The existing 32-pixel shared perimeter leaves a 177.778 mm square artwork aperture inside the 200 mm outer sheet.",
        ],
        "preprocessing_rerun": False,
        "cad_stl_step_generated": False,
    }
    _write_json(OUTPUT / "reports" / "planning_report.json", report)
    _write_json(
        OUTPUT / "reports" / "rejected_prefixes.json",
        rejected_prefixes,
    )
    print(
        json.dumps(
            {
                "status": status,
                "tested_fixed_layer_counts": tested_k,
                "geometry_candidates": total_geometry_candidates,
                "exact_prefix_no_good_cuts": total_no_goods,
                "wall_seconds": report["solver"]["total_wall_seconds"],
                "report": str(OUTPUT / "reports" / "planning_report.json"),
            },
            indent=2,
        ),
        flush=True,
    )
    return 0 if status == "COMPLETE_VALID_PLAN_FOUND" else 2


if __name__ == "__main__":
    raise SystemExit(main())
