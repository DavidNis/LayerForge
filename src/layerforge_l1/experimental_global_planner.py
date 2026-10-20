"""Experimental fixed-depth CP-SAT planner for LayerForge checkpoint 1.

This module is intentionally separate from the existing candidate-group and
beam planner.  It assigns every connected visible source-color region to one
front-to-back depth jointly.  Connected visible regions cannot yet be split
across multiple depths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
from PIL import Image

from . import PROJECT_ROOT
from .connectivity_repair import audit_connection_width, audit_edge_connectivity
from .layer_optimization import DEFAULT_OPTIMIZATION_CONFIG, build_region_graph
from .subtractive_poc import (
    DEFAULT_CONFIG,
    PocConfig,
    _outer_and_perimeter,
    _render_exploded,
    _save_layer_artifacts,
    analyze_image,
    construct_layers,
    discover_order,
    validate_plan,
)


DEFAULT_INPUT = (
    PROJECT_ROOT / "fixtures" / "known_feasible_benchmark" / "source_composite_only.png"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts" / "l1_global_planner_checkpoint1_v1"


@dataclass(frozen=True)
class GlobalPlannerConfig:
    fixed_layers: int = 5
    time_limit_seconds: float = 60.0
    random_seed: int = 74031
    solver_workers: int = 1


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hex(color: np.ndarray) -> str:
    return "#" + "".join(f"{int(channel):02X}" for channel in color)


def exact_index(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Losslessly map exact RGB triplets to deterministic palette indices."""

    palette, inverse = np.unique(rgb.reshape(-1, 3), axis=0, return_inverse=True)
    return inverse.reshape(rgb.shape[:2]).astype(np.uint8), palette.astype(np.uint8)


def load_lossless_target(
    source: Path, config: PocConfig = DEFAULT_CONFIG
) -> dict[str, Any]:
    """Load only the composite and resize with nearest-neighbour sampling."""

    with Image.open(source) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        original_size = image.size
    target = cv2.resize(
        rgb,
        (config.analysis_pixels, config.analysis_pixels),
        interpolation=cv2.INTER_NEAREST,
    )
    labels, palette = exact_index(target)
    return {
        "rgb": target,
        "labels": labels,
        "palette_rgb": palette,
        "palette_hex": [_hex(color) for color in palette],
        "counts": np.bincount(labels.ravel(), minlength=len(palette)),
        "original_size": original_size,
    }


def load_existing_indexed_target(
    source: Path, config: PocConfig = DEFAULT_CONFIG
) -> dict[str, Any]:
    """Use the existing production indexer without retaining its layer proposal."""

    analysis = analyze_image(source, config)
    return {
        "rgb": analysis["rgb"],
        "labels": analysis["labels"],
        "palette_rgb": analysis["palette_rgb"],
        "palette_hex": analysis["palette_hex"],
        "counts": analysis["counts"],
        "original_size": analysis["original_size"],
        "preprocessing_diagnostics": {
            "cleanup_event_count": len(analysis["cleanup_events"]),
            "derived_greedy_provider_count": len(analysis["providers"]),
        },
    }


def _bit_indices(bits: int) -> Iterable[int]:
    while bits:
        least = bits & -bits
        yield least.bit_length() - 1
        bits ^= least


def proven_precedence_pairs(graph: dict[str, Any]) -> tuple[tuple[int, int], ...]:
    """Find region dominators proved by the rooted undirected adjacency graph.

    Pair ``(before, after)`` means removing ``before`` disconnects ``after``
    from every border-touching region, so a connected cumulative prefix cannot
    assign ``after`` to a shallower depth than ``before``.
    """

    node_count = len(graph["nodes"])
    adjacency = tuple(int(bits) for bits in graph["adjacency"])
    border = set(_bit_indices(int(graph["border_bits"])))
    pairs: set[tuple[int, int]] = set()
    for removed in range(node_count):
        pending = [node for node in border if node != removed]
        reached = set(pending)
        while pending:
            current = pending.pop()
            for neighbour in _bit_indices(adjacency[current]):
                if neighbour != removed and neighbour not in reached:
                    reached.add(neighbour)
                    pending.append(neighbour)
        for target in range(node_count):
            if target != removed and target not in reached:
                pairs.add((removed, target))
    return tuple(sorted(pairs))


def _undirected_edges(graph: dict[str, Any]) -> tuple[tuple[int, int], ...]:
    edges: set[tuple[int, int]] = set()
    for left, bits in enumerate(graph["adjacency"]):
        for right in _bit_indices(int(bits)):
            edges.add((min(left, right), max(left, right)))
    return tuple(sorted(edges))


def build_fixed_depth_model(
    graph: dict[str, Any], fixed_layers: int
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Build the joint region/depth CP-SAT model without reference metadata."""

    try:
        from ortools.sat.python import cp_model
    except ImportError as error:  # pragma: no cover - environment-specific guard
        raise RuntimeError(
            "experimental global planner requires ortools==9.14.6206; "
            "install with: pip install -e \".[global-planner]\""
        ) from error

    nodes = tuple(graph["nodes"])
    region_count = len(nodes)
    color_count = len(graph["color_bits"])
    if fixed_layers < color_count:
        raise ValueError("fixed layer count cannot be smaller than the palette")

    model = cp_model.CpModel()
    x = {
        (region, depth): model.new_bool_var(f"x_r{region}_d{depth}")
        for region in range(region_count)
        for depth in range(fixed_layers)
    }
    y = {
        (depth, color): model.new_bool_var(f"y_d{depth}_c{color}")
        for depth in range(fixed_layers)
        for color in range(color_count)
    }
    prefix = {
        (region, depth): model.new_bool_var(f"p_r{region}_d{depth}")
        for region in range(region_count)
        for depth in range(fixed_layers)
    }
    depth_var = {
        region: model.new_int_var(0, fixed_layers - 1, f"depth_r{region}")
        for region in range(region_count)
    }

    constraint_counts = {
        "region_exactly_one_depth": 0,
        "layer_exactly_one_color": 0,
        "layer_nonempty": 0,
        "region_color_compatibility": 0,
        "prefix_definition": 0,
        "no_consecutive_same_color": 0,
        "proven_precedence": 0,
        "flow_conservation": 0,
        "flow_capacity": 0,
    }
    for region, node in enumerate(nodes):
        model.add_exactly_one(x[region, depth] for depth in range(fixed_layers))
        constraint_counts["region_exactly_one_depth"] += 1
        model.add(
            depth_var[region]
            == sum(depth * x[region, depth] for depth in range(fixed_layers))
        )
        for depth in range(fixed_layers):
            model.add(x[region, depth] <= y[depth, int(node.palette_index)])
            constraint_counts["region_color_compatibility"] += 1
            model.add(
                prefix[region, depth]
                == sum(x[region, earlier] for earlier in range(depth + 1))
            )
            constraint_counts["prefix_definition"] += 1

    for depth in range(fixed_layers):
        model.add_exactly_one(y[depth, color] for color in range(color_count))
        constraint_counts["layer_exactly_one_color"] += 1
        model.add(sum(x[region, depth] for region in range(region_count)) >= 1)
        constraint_counts["layer_nonempty"] += 1
    for depth in range(fixed_layers - 1):
        for color in range(color_count):
            model.add(y[depth, color] + y[depth + 1, color] <= 1)
            constraint_counts["no_consecutive_same_color"] += 1

    precedence = proven_precedence_pairs(graph)
    for before, after in precedence:
        model.add(depth_var[before] <= depth_var[after])
        constraint_counts["proven_precedence"] += 1

    # Single-commodity flow makes every cumulative region prefix connected to
    # a virtual common-perimeter root. Exact raster width remains an oracle.
    root = region_count
    undirected = list(_undirected_edges(graph))
    undirected.extend((root, region) for region in _bit_indices(int(graph["border_bits"])))
    directed = tuple(
        direction for left, right in undirected for direction in ((left, right), (right, left))
    )
    flow: dict[tuple[int, int, int], Any] = {}
    for depth in range(fixed_layers):
        for left, right in directed:
            variable = model.new_int_var(0, region_count, f"f_d{depth}_{left}_{right}")
            flow[depth, left, right] = variable
            if left != root:
                model.add(variable <= region_count * prefix[left, depth])
                constraint_counts["flow_capacity"] += 1
            if right != root:
                model.add(variable <= region_count * prefix[right, depth])
                constraint_counts["flow_capacity"] += 1
        for region in range(region_count):
            incoming = [flow[depth, left, right] for left, right in directed if right == region]
            outgoing = [flow[depth, left, right] for left, right in directed if left == region]
            model.add(sum(incoming) - sum(outgoing) == prefix[region, depth])
            constraint_counts["flow_conservation"] += 1
        root_incoming = [flow[depth, left, right] for left, right in directed if right == root]
        root_outgoing = [flow[depth, left, right] for left, right in directed if left == root]
        model.add(
            sum(root_outgoing) - sum(root_incoming)
            == sum(prefix[region, depth] for region in range(region_count))
        )
        constraint_counts["flow_conservation"] += 1

    # Fixed K makes repeated-depth count constant. Prefer less cumulative
    # hidden material without supplying any known order or region assignment.
    model.minimize(
        sum(
            int(nodes[region].area) * (fixed_layers - depth) * x[region, depth]
            for region in range(region_count)
            for depth in range(fixed_layers)
        )
    )
    variables = {"x": x, "y": y, "prefix": prefix, "depth": depth_var}
    description = {
        "backend": "experimental_fixed_depth_cp_sat",
        "fixed_layer_count": fixed_layers,
        "region_count": region_count,
        "color_count": color_count,
        "variables": {
            "region_depth_boolean": len(x),
            "layer_color_boolean": len(y),
            "cumulative_prefix_boolean": len(prefix),
            "region_depth_integer": len(depth_var),
            "flow_integer": len(flow),
        },
        "constraints": constraint_counts,
        "proven_precedence_pairs": [list(pair) for pair in precedence],
        "reference_masks_or_order_used": False,
        "candidate_group_generator_used": False,
        "beam_search_used": False,
        "connected_visible_region_splitting_supported": False,
    }
    return model, variables, description


def _extract_assignment(
    solver: Any,
    variables: dict[str, Any],
    graph: dict[str, Any],
    fixed_layers: int,
) -> tuple[list[int], list[int], list[list[int]]]:
    depths = [int(solver.value(variables["depth"][region])) for region in range(len(graph["nodes"]))]
    colors = [
        next(
            color
            for color in range(len(graph["color_bits"]))
            if solver.boolean_value(variables["y"][depth, color])
        )
        for depth in range(fixed_layers)
    ]
    groups = [
        [region for region, assigned in enumerate(depths) if assigned == depth]
        for depth in range(fixed_layers)
    ]
    return depths, colors, groups


def _physical_prefix(
    region_map: np.ndarray,
    depths: list[int],
    depth: int,
    config: PocConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    region_depths = np.asarray(depths, dtype=np.int16)
    retained = region_depths[region_map] <= depth
    visible = region_depths[region_map] == depth
    _, perimeter = _outer_and_perimeter(config)
    physical = perimeter.copy()
    p = config.perimeter_pixels
    physical[p:-p, p:-p] = retained
    return physical, retained, visible


def _validate_assignment_geometry(
    graph: dict[str, Any],
    depths: list[int],
    fixed_layers: int,
    config: PocConfig,
) -> tuple[list[dict[str, Any]], list[int]]:
    rows: list[dict[str, Any]] = []
    failing_depths: list[int] = []
    for depth in range(fixed_layers):
        physical, _, visible = _physical_prefix(
            graph["region_map"], depths, depth, config
        )
        edge, _ = audit_edge_connectivity(physical)
        width, _ = audit_connection_width(
            physical,
            visible,
            config,
            DEFAULT_OPTIMIZATION_CONFIG,
        )
        passed = edge["four_neighbor_status"] == "PASS" and width["status"] == "PASS"
        if not passed:
            failing_depths.append(depth)
        rows.append(
            {
                "front_to_back_depth": depth,
                "assigned_prefix_region_count": sum(value <= depth for value in depths),
                "four_neighbor_status": edge["four_neighbor_status"],
                "four_neighbor_components": edge["four_neighbor_components"],
                "four_neighbor_orphan_components": edge["four_neighbor_orphan_components"],
                "width_status": width["status"],
                "orphan_structural_core_components": width[
                    "orphan_structural_core_components"
                ],
                "orphan_structural_core_pixels": width["orphan_structural_core_pixels"],
                "thin_decorative_visible_components": width[
                    "thin_decorative_visible_components"
                ],
                "morphology_kernel_pixels": width["morphology_kernel_size_pixels"],
                "enforced_connection_width_mm": width["enforced_connection_width_mm"],
            }
        )
    return rows, failing_depths


def _add_exact_prefix_no_good(
    model: Any,
    variables: dict[str, Any],
    depths: list[int],
    rejected_depth: int,
) -> None:
    different = []
    for region, assigned_depth in enumerate(depths):
        variable = variables["prefix"][region, rejected_depth]
        different.append(1 - variable if assigned_depth <= rejected_depth else variable)
    model.add(sum(different) >= 1)


def _materialize_analysis(
    base: dict[str, Any],
    graph: dict[str, Any],
    depths: list[int],
    colors: list[int],
    groups: list[list[int]],
) -> dict[str, Any]:
    region_depths = np.asarray(depths, dtype=np.uint8)
    provider_labels = region_depths[graph["region_map"]]
    providers = []
    for depth, (color, group) in enumerate(zip(colors, groups)):
        providers.append(
            {
                "index": depth,
                "palette_index": color,
                "color": base["palette_hex"][color],
                "role": "experimental_global_cp_sat_region_assignment",
                "pixels": int(sum(graph["nodes"][region].area for region in group)),
                "source_region_count": len(group),
                "source_region_indices": group,
                "front_to_back_rank": depth,
            }
        )
    return {
        **base,
        "provider_labels": provider_labels,
        "providers": providers,
        "provider_splits": [],
    }


def run_checkpoint(
    source: Path,
    output: Path,
    planner_config: GlobalPlannerConfig = GlobalPlannerConfig(),
    config: PocConfig = DEFAULT_CONFIG,
    preprocessing_mode: str = "lossless",
    preserve_rejected: bool = False,
) -> dict[str, Any]:
    """Run one bounded checkpoint invocation, including lazy exact-prefix cuts."""

    if not source.is_file():
        raise FileNotFoundError(source)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    for directory in ("original", "automatic", "previews", "reports"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output / "original" / source.name)

    started = time.perf_counter()
    if preprocessing_mode == "lossless":
        base = load_lossless_target(source, config)
    elif preprocessing_mode == "existing-indexer":
        base = load_existing_indexed_target(source, config)
    else:
        raise ValueError(f"unknown preprocessing mode: {preprocessing_mode}")
    target = np.asarray(base["palette_rgb"], dtype=np.uint8)[base["labels"]]
    Image.fromarray(target, "RGB").save(output / "automatic" / "indexed_target.png")
    graph = build_region_graph(base["labels"], connectivity=4, minimum_contact_pixels=1)
    model, variables, model_description = build_fixed_depth_model(
        graph, planner_config.fixed_layers
    )

    from ortools.sat.python import cp_model
    import ortools

    oracle_cache: dict[tuple[int, ...], tuple[list[dict[str, Any]], list[int]]] = {}
    solver_iterations: list[dict[str, Any]] = []
    no_good_prefixes: set[tuple[int, int]] = set()
    final: tuple[list[int], list[int], list[list[int]], list[dict[str, Any]]] | None = None
    last_rejected: tuple[
        list[int], list[int], list[list[int]], list[dict[str, Any]]
    ] | None = None
    termination = "TIME_LIMIT"
    final_solver_status = "UNKNOWN"

    while True:
        elapsed = time.perf_counter() - started
        remaining = planner_config.time_limit_seconds - elapsed
        if remaining <= 0:
            break
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.num_search_workers = planner_config.solver_workers
        solver.parameters.random_seed = planner_config.random_seed
        solver.parameters.log_search_progress = False
        iteration_started = time.perf_counter()
        status = solver.solve(model)
        final_solver_status = solver.status_name(status)
        iteration = {
            "iteration": len(solver_iterations) + 1,
            "status": final_solver_status,
            "wall_seconds": time.perf_counter() - iteration_started,
            "solver_wall_seconds": solver.wall_time,
            "branches": solver.num_branches,
            "conflicts": solver.num_conflicts,
            "objective_value": solver.objective_value if status in (cp_model.FEASIBLE, cp_model.OPTIMAL) else None,
            "best_objective_bound": solver.best_objective_bound if status in (cp_model.FEASIBLE, cp_model.OPTIMAL) else None,
        }
        solver_iterations.append(iteration)
        if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
            termination = "MODEL_INFEASIBLE" if status == cp_model.INFEASIBLE else "TIME_LIMIT"
            break

        depths, colors, groups = _extract_assignment(
            solver, variables, graph, planner_config.fixed_layers
        )
        key = tuple(depths)
        if key not in oracle_cache:
            oracle_cache[key] = _validate_assignment_geometry(
                graph, depths, planner_config.fixed_layers, config
            )
        geometry_rows, failing_depths = oracle_cache[key]
        iteration["front_to_back_colors"] = [base["palette_hex"][color] for color in colors]
        iteration["regions_per_depth"] = [len(group) for group in groups]
        iteration["geometry_failing_depths"] = failing_depths
        if not failing_depths:
            final = depths, colors, groups, geometry_rows
            termination = "COMPLETE_GEOMETRY_VALID_ASSIGNMENT"
            break
        last_rejected = depths, colors, groups, geometry_rows

        cuts_added = 0
        for rejected_depth in failing_depths:
            prefix_bits = sum(
                1 << region
                for region, assigned_depth in enumerate(depths)
                if assigned_depth <= rejected_depth
            )
            signature = (rejected_depth, prefix_bits)
            if signature in no_good_prefixes:
                continue
            no_good_prefixes.add(signature)
            _add_exact_prefix_no_good(model, variables, depths, rejected_depth)
            cuts_added += 1
        iteration["exact_prefix_no_good_cuts_added"] = cuts_added
        if cuts_added == 0:
            termination = "REPEATED_INVALID_ASSIGNMENT_WITHOUT_NEW_CUT"
            break

    result_status = "NO_VALID_ASSIGNMENT"
    validation_summary = None
    layer_files = None
    assignment_manifest = None
    rejected_candidate = None
    if final is not None:
        depths, colors, groups, geometry_rows = final
        analysis = _materialize_analysis(base, graph, depths, colors, groups)
        order = discover_order(analysis, config)
        if order["status"] != "FEASIBLE":
            raise RuntimeError(f"solver assignment failed existing order validator: {order}")
        layers = construct_layers(analysis, order, config)
        validation, reconstructed, mismatch = validate_plan(analysis, order, layers, config)
        final_rows = []
        for layer in layers:
            edge, _ = audit_edge_connectivity(layer["physical_mask"])
            width, _ = audit_connection_width(
                layer["physical_mask"],
                layer["visible_aperture"],
                config,
                DEFAULT_OPTIMIZATION_CONFIG,
            )
            final_rows.append(
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
            and all(row["four_neighbor_status"] == "PASS" for row in final_rows)
            and all(row["width_status"] == "PASS" for row in final_rows)
            and all(row["orphan_structural_core_components"] == 0 for row in final_rows)
            and all(row["missing_perimeter_pixels"] == 0 for row in final_rows)
        )
        result_status = "COMPLETE_VALID_PLAN_FOUND" if final_valid else "COMPLETE_BUT_INVALID"
        validation_summary = {
            "status": "PASS" if final_valid else "FAIL",
            "physical_layer_count": len(layers),
            "front_to_back_colors": [base["palette_hex"][color] for color in colors],
            "back_to_front_colors": [layer["color"] for layer in layers],
            "geometry_mismatched_pixels": validation["geometry_mismatched_pixels"],
            "indexed_target_accuracy": validation[
                "geometry_pixel_accuracy_against_quantized_target"
            ],
            "visible_automatic_connector_pixels": validation[
                "visible_automatic_connector_pixels"
            ],
            "front_to_back_prefix_geometry": geometry_rows,
            "layers": final_rows,
        }
        assignment_manifest = {
            "model_limitations": {
                "connected_visible_region_splitting_across_depths": "NOT_SUPPORTED",
                "global_infeasibility_scope": "only this fixed-depth, one-depth-per-connected-region model",
            },
            "front_to_back": [
                {
                    "depth": depth,
                    "color": base["palette_hex"][colors[depth]],
                    "palette_index": colors[depth],
                    "source_region_ids": groups[depth],
                }
                for depth in range(planner_config.fixed_layers)
            ],
            "region_to_depth": depths,
        }
        if final_valid:
            layer_files = _save_layer_artifacts(output, layers, analysis, config)
            Image.fromarray(reconstructed, "RGB").save(
                output / "previews" / "reconstructed_front.png"
            )
            Image.fromarray(mismatch.astype(np.uint8) * 255, "L").save(
                output / "previews" / "indexed_target_difference.png"
            )
            _render_exploded(layers, analysis, output / "previews" / "exploded_layers.png")
            _write_json(output / "automatic" / "layer_assignment.json", assignment_manifest)

    if final is None and preserve_rejected and last_rejected is not None:
        rejected_depths, rejected_colors, rejected_groups, rejected_geometry = last_rejected
        rejected_analysis = _materialize_analysis(
            base, graph, rejected_depths, rejected_colors, rejected_groups
        )
        rejected_order = discover_order(rejected_analysis, config)
        if rejected_order["status"] == "FEASIBLE":
            rejected_layers = construct_layers(rejected_analysis, rejected_order, config)
            rejected_validation, rejected_reconstruction, rejected_mismatch = validate_plan(
                rejected_analysis, rejected_order, rejected_layers, config
            )
            rejected_root = output / "rejected_candidate"
            rejected_files = _save_layer_artifacts(
                rejected_root, rejected_layers, rejected_analysis, config
            )
            (rejected_root / "previews").mkdir(parents=True, exist_ok=True)
            Image.fromarray(rejected_reconstruction, "RGB").save(
                rejected_root / "previews" / "reconstructed_front.png"
            )
            Image.fromarray(rejected_mismatch.astype(np.uint8) * 255, "L").save(
                rejected_root / "previews" / "indexed_target_difference.png"
            )
            _render_exploded(
                rejected_layers,
                rejected_analysis,
                rejected_root / "previews" / "exploded_layers.png",
            )
            rejected_candidate = {
                "status": "REJECTED_STRUCTURAL_CANDIDATE_NOT_A_VALID_STACK",
                "reason": "One or more cumulative physical masks failed existing connectivity or retained-width validation.",
                "front_to_back_colors": [
                    base["palette_hex"][color] for color in rejected_colors
                ],
                "front_to_back_region_groups": rejected_groups,
                "region_to_depth": rejected_depths,
                "prefix_geometry": rejected_geometry,
                "mask_composite_validation": rejected_validation,
                "layer_files": rejected_files,
                "preview_files": {
                    "reconstructed_front": "previews/reconstructed_front.png",
                    "indexed_target_difference": "previews/indexed_target_difference.png",
                    "exploded_layers": "previews/exploded_layers.png",
                },
                "warning": "These masks are diagnostic evidence only and must not be treated as printable or approved.",
            }
            _write_json(
                rejected_root / "rejected_candidate_report.json", rejected_candidate
            )
        else:
            rejected_candidate = {
                "status": "REJECTED_TOPOLOGY_CANDIDATE_NOT_RENDERED",
                "order_validation": rejected_order,
            }

    report = {
        "milestone": "L1_GLOBAL_PLANNER_CHECKPOINT_1",
        "review_state": "READY_FOR_USER_TEST",
        "status": result_status,
        "input": {
            "path": str(source),
            "sha256": _sha256(source),
            "original_pixels": list(base["original_size"]),
            "indexed_target_pixels": [config.analysis_pixels, config.analysis_pixels],
            "exact_palette_count": len(base["palette_rgb"]),
            "palette_hex": base["palette_hex"],
            "connected_source_regions": len(graph["nodes"]),
            "preprocessing_mode": preprocessing_mode,
            "lossless_nearest_neighbour_adapter": preprocessing_mode == "lossless",
            "preprocessing_diagnostics": base.get("preprocessing_diagnostics"),
            "reference_masks_or_order_used": False,
        },
        "dependency": {
            "ortools_version": ortools.__version__,
            "pin": "ortools==9.14.6206",
            "installation_scope": "optional project dependency: global-planner",
        },
        "configuration": asdict(planner_config),
        "model": model_description,
        "solver": {
            "final_status": final_solver_status,
            "termination_reason": termination,
            "total_wall_seconds": time.perf_counter() - started,
            "iterations": solver_iterations,
        },
        "geometry_oracle": {
            "complete_assignments_evaluated": len(oracle_cache),
            "exact_prefix_no_good_cuts": len(no_good_prefixes),
            "safe_cut_scope": "forbid only the exact failing cumulative region prefix at its depth",
        },
        "assignment": assignment_manifest,
        "validation": validation_summary,
        "layer_files": layer_files,
        "rejected_candidate": rejected_candidate,
        "limitations": [
            "Each connected source-color region is assigned wholly to one depth; splitting is unsupported.",
            "The result does not prove global feasibility outside this representation.",
            "The result does not prove five layers are globally minimal.",
            "Printable opening width remains a reported manufacturing concern separate from retained-material width.",
        ],
        "cad_generated": False,
        "existing_candidate_generator_called": False,
        "existing_beam_search_called": False,
    }
    _write_json(output / "reports" / "checkpoint_report.json", report)
    _write_json(output / "reports" / "model.json", model_description)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m layerforge_l1.experimental_global_planner",
        description="Run LayerForge global-planner checkpoint 1 only.",
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--fixed-layers", type=int, default=5)
    parser.add_argument("--time-limit-seconds", type=float, default=60.0)
    parser.add_argument(
        "--preprocessing-mode",
        choices=("lossless", "existing-indexer"),
        default="lossless",
    )
    parser.add_argument(
        "--preserve-rejected",
        action="store_true",
        help="save the last rejected assignment as explicitly invalid diagnostic masks",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = run_checkpoint(
        args.input,
        args.output,
        GlobalPlannerConfig(
            fixed_layers=args.fixed_layers,
            time_limit_seconds=args.time_limit_seconds,
        ),
        preprocessing_mode=args.preprocessing_mode,
        preserve_rejected=args.preserve_rejected,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "review_state": report["review_state"],
                "report": str(args.output / "reports" / "checkpoint_report.json"),
            },
            indent=2,
        ),
        flush=True,
    )
    return 0 if report["status"] == "COMPLETE_VALID_PLAN_FOUND" else 1


if __name__ == "__main__":
    raise SystemExit(main())
