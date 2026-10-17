"""Optimize subtractive full-sheet layer plans without changing visible pixels.

The optimizer works on the region-adjacency graph produced from an unannotated
flat image.  A search action assigns every currently reachable region of one
palette color to one physical depth.  Every search prefix must stay connected
to the common sheet perimeter, so no visible connector is ever synthesized.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .subtractive_poc import (
    DEFAULT_CONFIG,
    PocConfig,
    _compose,
    _render_comparison,
    _render_exploded,
    _save_layer_artifacts,
    analyze_image,
    construct_layers,
    discover_order,
    validate_plan,
)


@dataclass(frozen=True)
class OptimizationConfig:
    """Bounded, deterministic search and reporting controls."""

    max_states_per_depth: int = 65_536
    tiny_visible_fraction: float = 0.0005
    near_full_retained_fraction: float = 0.95
    region_connectivity: int = 4
    nominal_connection_width_mm: float = 0.8
    vectorization_safety_mm: float = 0.2
    cad_conversion_safety_mm: float = 0.2
    minimum_contact_pixels_override: int | None = None
    exempt_thin_decorative_regions_from_contact_width: bool = False


DEFAULT_OPTIMIZATION_CONFIG = OptimizationConfig()


@dataclass(frozen=True)
class RegionNode:
    index: int
    palette_index: int
    area: int
    touches_border: bool
    border_contact_pixels: int
    thin_decorative_at_required_width: bool


@dataclass(frozen=True)
class SearchRecord:
    sequence: tuple[int, ...]
    neighbour_bits: int
    assigned_area: int
    retained_pixel_layers: int
    tiny_layer_count: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _bit_indices(bits: int):
    while bits:
        lowest = bits & -bits
        yield lowest.bit_length() - 1
        bits -= lowest


def _bit_area(bits: int, areas: tuple[int, ...]) -> int:
    return sum(areas[index] for index in _bit_indices(bits))


def build_region_graph(
    labels: np.ndarray,
    connectivity: int = 4,
    minimum_contact_pixels: int = 1,
    exempt_thin_decorative_regions: bool = False,
) -> dict[str, Any]:
    """Build a fixture-agnostic directed support graph.

    Contact thresholds apply to the region being supported.  When the
    diagnostic-only decorative exemption is enabled, a source component that
    cannot contain a core at the required odd kernel is allowed through a
    one-pixel artwork contact.  This investigates ordering without pretending
    that decorative strokes are load-bearing members.
    """

    height, width = labels.shape
    region_map = np.full((height, width), -1, dtype=np.int32)
    nodes: list[RegionNode] = []
    kernel_size = minimum_contact_pixels if minimum_contact_pixels % 2 else minimum_contact_pixels + 1
    decorative_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
    )
    for palette_index in sorted(int(value) for value in np.unique(labels)):
        count, components, stats, _ = cv2.connectedComponentsWithStats(
            (labels == palette_index).astype(np.uint8), connectivity=connectivity
        )
        for component in range(1, count):
            mask = components == component
            index = len(nodes)
            region_map[mask] = index
            thin_decorative = not np.any(
                cv2.erode(mask.astype(np.uint8), decorative_kernel, iterations=1)
            )
            nodes.append(
                RegionNode(
                    index=index,
                    palette_index=palette_index,
                    area=int(stats[component, cv2.CC_STAT_AREA]),
                    touches_border=bool(
                        np.any(mask[0])
                        or np.any(mask[-1])
                        or np.any(mask[:, 0])
                        or np.any(mask[:, -1])
                    ),
                    border_contact_pixels=int(
                        np.count_nonzero(mask[0])
                        + np.count_nonzero(mask[-1])
                        + np.count_nonzero(mask[:, 0])
                        + np.count_nonzero(mask[:, -1])
                    ),
                    thin_decorative_at_required_width=thin_decorative,
                )
            )
    if np.any(region_map < 0):
        raise RuntimeError("region graph contains unassigned pixels")

    adjacency = [0] * len(nodes)
    pair_contacts: dict[tuple[int, int], int] = {}
    for first, second in (
        (region_map[:, :-1], region_map[:, 1:]),
        (region_map[:-1, :], region_map[1:, :]),
    ):
        differing = first != second
        pairs = np.stack((first[differing], second[differing]), axis=1)
        unique_pairs, contact_counts = np.unique(
            np.sort(pairs, axis=1), axis=0, return_counts=True
        )
        for (left, right), contact_count in zip(unique_pairs, contact_counts):
            left_index = int(left)
            right_index = int(right)
            pair = (left_index, right_index)
            pair_contacts[pair] = pair_contacts.get(pair, 0) + int(contact_count)

    def threshold(node: RegionNode) -> int:
        if exempt_thin_decorative_regions and node.thin_decorative_at_required_width:
            return 1
        return minimum_contact_pixels

    for (left_index, right_index), contact_count in pair_contacts.items():
        if contact_count >= threshold(nodes[right_index]):
            adjacency[left_index] |= 1 << right_index
        if contact_count >= threshold(nodes[left_index]):
            adjacency[right_index] |= 1 << left_index

    color_count = int(labels.max()) + 1
    color_bits = [0] * color_count
    border_bits = 0
    for node in nodes:
        color_bits[node.palette_index] |= 1 << node.index
        if node.border_contact_pixels >= threshold(node):
            border_bits |= 1 << node.index
    return {
        "region_map": region_map,
        "nodes": nodes,
        "adjacency": tuple(adjacency),
        "color_bits": tuple(color_bits),
        "border_bits": border_bits,
        "full_bits": (1 << len(nodes)) - 1,
        "minimum_contact_pixels": minimum_contact_pixels,
        "thin_decorative_exemption": exempt_thin_decorative_regions,
        "thin_decorative_region_count": sum(
            node.thin_decorative_at_required_width for node in nodes
        ),
        "contact_rule": "directed threshold is applied to the region being supported",
    }


def _available_actions(state: int, neighbour_bits: int, graph: dict[str, Any]) -> list[tuple[int, int]]:
    eligible = neighbour_bits | int(graph["border_bits"])
    actions: list[tuple[int, int]] = []
    for color, color_bits in enumerate(graph["color_bits"]):
        added = int(color_bits) & eligible & ~state
        if added:
            actions.append((color, added))
    return actions


def _advance_neighbours(current: int, added: int, adjacency: tuple[int, ...]) -> int:
    result = current
    for node in _bit_indices(added):
        result |= adjacency[node]
    return result


def optimize_region_sequence(
    labels: np.ndarray,
    optimization: OptimizationConfig = DEFAULT_OPTIMIZATION_CONFIG,
) -> tuple[tuple[int, ...], dict[str, Any], dict[str, Any]]:
    """Find a compact valid color-depth sequence with a bounded beam search.

    The objective is lexicographic: fewest depth actions reached, then fewest
    tiny-contribution actions, then least cumulative retained interior area.
    The beam makes this a bounded optimization, so the report never claims a
    proof of global minimality.
    """

    minimum_width_mm = (
        optimization.nominal_connection_width_mm
        + optimization.vectorization_safety_mm
        + optimization.cad_conversion_safety_mm
    )
    mm_per_pixel = DEFAULT_CONFIG.sheet_size_mm / DEFAULT_CONFIG.outer_pixels
    minimum_contact_pixels = (
        optimization.minimum_contact_pixels_override
        if optimization.minimum_contact_pixels_override is not None
        else math.ceil(minimum_width_mm / mm_per_pixel)
    )
    graph = build_region_graph(
        labels,
        optimization.region_connectivity,
        minimum_contact_pixels,
        optimization.exempt_thin_decorative_regions_from_contact_width,
    )
    nodes: tuple[RegionNode, ...] = tuple(graph["nodes"])
    adjacency: tuple[int, ...] = graph["adjacency"]
    areas = tuple(node.area for node in nodes)
    full = int(graph["full_bits"])
    aperture_pixels = int(labels.size)
    states: dict[int, SearchRecord] = {
        0: SearchRecord((), int(graph["border_bits"]), 0, 0, 0)
    }
    explored_states = 0
    pruned_states = 0
    frontier_widths: list[dict[str, int]] = []

    while states:
        candidates: dict[int, SearchRecord] = {}
        for state, record in states.items():
            for color, added in _available_actions(state, record.neighbour_bits, graph):
                next_state = state | added
                added_area = _bit_area(added, areas)
                assigned_area = record.assigned_area + added_area
                candidate = SearchRecord(
                    sequence=record.sequence + (color,),
                    neighbour_bits=_advance_neighbours(
                        record.neighbour_bits, added, adjacency
                    ),
                    assigned_area=assigned_area,
                    retained_pixel_layers=record.retained_pixel_layers + assigned_area,
                    tiny_layer_count=record.tiny_layer_count
                    + int(added_area < aperture_pixels * optimization.tiny_visible_fraction),
                )
                existing = candidates.get(next_state)
                candidate_score = (
                    candidate.tiny_layer_count,
                    candidate.retained_pixel_layers,
                    candidate.sequence,
                )
                if existing is None or candidate_score < (
                    existing.tiny_layer_count,
                    existing.retained_pixel_layers,
                    existing.sequence,
                ):
                    candidates[next_state] = candidate

        explored_states += len(candidates)
        depth = len(next(iter(candidates.values())).sequence) if candidates else 0
        if full in candidates:
            winner = candidates[full]
            frontier_widths.append(
                {"depth": depth, "candidate_states": len(candidates), "kept_states": len(candidates)}
            )
            return winner.sequence, graph, {
                "method": "bounded_region_graph_beam_search",
                "global_minimum_proven": False,
                "max_states_per_depth": optimization.max_states_per_depth,
                "explored_candidate_states": explored_states,
                "pruned_candidate_states": pruned_states,
                "frontier_widths": frontier_widths,
                "objective": [
                    "minimum completed layer count",
                    "minimum tiny-visible-contribution layer count",
                    "minimum cumulative retained interior pixel-layers",
                    "deterministic palette-index tie break",
                ],
                "winning_tiny_layer_count": winner.tiny_layer_count,
                "winning_retained_pixel_layers": winner.retained_pixel_layers,
                "minimum_contact_pixels": minimum_contact_pixels,
                "thin_decorative_exemption": optimization.exempt_thin_decorative_regions_from_contact_width,
                "thin_decorative_region_count": graph["thin_decorative_region_count"],
            }

        def rank(item: tuple[int, SearchRecord]) -> tuple[Any, ...]:
            state, record = item
            remaining_color_lower_bound = sum(
                bool(int(bits) & ~state) for bits in graph["color_bits"]
            )
            return (
                len(record.sequence) + remaining_color_lower_bound,
                record.tiny_layer_count,
                record.retained_pixel_layers,
                -record.assigned_area,
                record.sequence,
            )

        ranked = sorted(candidates.items(), key=rank)
        kept = ranked[: optimization.max_states_per_depth]
        pruned_states += max(0, len(ranked) - len(kept))
        frontier_widths.append(
            {"depth": depth, "candidate_states": len(ranked), "kept_states": len(kept)}
        )
        states = dict(kept)

    error = RuntimeError(
        "no perimeter-connected subtractive layer sequence was found within the bounded search"
    )
    error.search_report = {
        "method": "bounded_region_graph_beam_search",
        "status": "NO_PLAN_WITHIN_BOUNDS",
        "global_infeasibility_proven": False,
        "max_states_per_depth": optimization.max_states_per_depth,
        "explored_candidate_states": explored_states,
        "pruned_candidate_states": pruned_states,
        "frontier_widths": frontier_widths,
        "minimum_contact_pixels": minimum_contact_pixels,
        "thin_decorative_exemption": optimization.exempt_thin_decorative_regions_from_contact_width,
        "thin_decorative_region_count": graph["thin_decorative_region_count"],
        "limitation": "The beam is bounded and the region-contact proxy is not an exact mask-level morphology search; this result is not a proof of global infeasibility.",
    }
    raise error


def _replay_sequence(
    sequence: tuple[int, ...], graph: dict[str, Any], palette_rgb: np.ndarray
) -> tuple[np.ndarray, list[dict[str, Any]], list[int]]:
    region_map: np.ndarray = graph["region_map"]
    nodes: tuple[RegionNode, ...] = tuple(graph["nodes"])
    provider_labels = np.full(region_map.shape, 255, dtype=np.uint8)
    state = 0
    neighbours = int(graph["border_bits"])
    providers: list[dict[str, Any]] = []
    provider_groups: list[int] = []
    for provider_index, color in enumerate(sequence):
        actions = dict(_available_actions(state, neighbours, graph))
        added = actions.get(color, 0)
        if not added:
            raise RuntimeError("optimized sequence cannot be replayed")
        indices = list(_bit_indices(added))
        mask = np.isin(region_map, indices)
        provider_labels[mask] = provider_index
        providers.append(
            {
                "index": provider_index,
                "palette_index": color,
                "color": "#" + "".join(f"{int(channel):02X}" for channel in palette_rgb[color]),
                "role": "optimized_topology_search_batch",
                "pixels": int(sum(nodes[index].area for index in indices)),
                "source_region_count": len(indices),
                "front_to_back_rank": provider_index,
            }
        )
        provider_groups.append(added)
        state |= added
        neighbours = _advance_neighbours(neighbours, added, graph["adjacency"])
    if state != int(graph["full_bits"]) or np.any(provider_labels == 255):
        raise RuntimeError("optimized sequence did not assign every source region")
    return provider_labels, providers, provider_groups


def _prefix_valid(groups: list[int], graph: dict[str, Any]) -> tuple[bool, dict[str, int] | None]:
    assigned = 0
    region_map: np.ndarray = graph["region_map"]
    for depth, group in enumerate(groups):
        assigned |= group
        mask = np.isin(region_map, list(_bit_indices(assigned)))
        count, components = cv2.connectedComponents(
            mask.astype(np.uint8), connectivity=4
        )
        component_count = count - 1
        border_components = set(
            int(value)
            for value in np.unique(
                np.concatenate(
                    (components[0], components[-1], components[:, 0], components[:, -1])
                )
            )
            if value
        )
        if component_count == 0 or len(border_components) != component_count:
            return False, {
                "front_to_back_depth": depth,
                "component_count": component_count,
                "border_connected_component_count": len(border_components),
            }
    return True, None


def _one_layer_per_color_audit(labels: np.ndarray) -> dict[str, Any]:
    """Exactly test whether some single-depth-per-color order is feasible."""

    color_count = int(labels.max()) + 1
    valid_subsets = [False] * (1 << color_count)
    valid_subsets[0] = True
    for subset in range(1, 1 << color_count):
        selected = np.zeros(labels.shape, dtype=bool)
        for color in range(color_count):
            if subset & (1 << color):
                selected |= labels == color
        count, components = cv2.connectedComponents(
            selected.astype(np.uint8), connectivity=4
        )
        component_count = count - 1
        border_components = set(
            int(value)
            for value in np.unique(
                np.concatenate(
                    (components[0], components[-1], components[:, 0], components[:, -1])
                )
            )
            if value
        )
        valid_subsets[subset] = component_count > 0 and len(border_components) == component_count

    reachable: dict[int, tuple[int, ...]] = {0: ()}
    for _ in range(color_count):
        additions: dict[int, tuple[int, ...]] = {}
        for subset, sequence in reachable.items():
            for color in range(color_count):
                bit = 1 << color
                next_subset = subset | bit
                if subset & bit or not valid_subsets[next_subset]:
                    continue
                candidate = sequence + (color,)
                if next_subset not in reachable and (
                    next_subset not in additions or candidate < additions[next_subset]
                ):
                    additions[next_subset] = candidate
        if not additions:
            break
        reachable.update(additions)
    full = (1 << color_count) - 1
    longest = max((len(sequence) for sequence in reachable.values()), default=0)
    return {
        "feasible": full in reachable,
        "tested_color_subsets": (1 << color_count) - 1,
        "reachable_valid_subsets": len(reachable) - 1,
        "maximum_distinct_colors_reached": longest,
        "example_order_front_to_back": list(reachable[full]) if full in reachable else None,
        "conclusion": (
            "One layer per palette color has a valid perimeter-connected order."
            if full in reachable
            else "No ordering that uses each palette color exactly once keeps every cumulative retained mask connected to the perimeter. At least one repeated-color depth is required for exact reconstruction."
        ),
    }


def _merge_audit(
    sequence: tuple[int, ...], groups: list[int], graph: dict[str, Any], palette_hex: list[str]
) -> list[dict[str, Any]]:
    audits: list[dict[str, Any]] = []
    for color in sorted(set(sequence)):
        occurrences = [index for index, value in enumerate(sequence) if value == color]
        if len(occurrences) < 2:
            continue
        attempts: list[dict[str, Any]] = []
        for first_position, first in enumerate(occurrences):
            for second in occurrences[first_position + 1 :]:
                for target, source in ((first, second), (second, first)):
                    candidate_groups = list(groups)
                    candidate_groups[target] |= candidate_groups[source]
                    del candidate_groups[source]
                    valid, failure = _prefix_valid(candidate_groups, graph)
                    attempts.append(
                        {
                            "occurrences": [first, second],
                            "moved_to_occurrence": target,
                            "feasible": valid,
                            "first_failure": failure,
                        }
                    )
        audits.append(
            {
                "palette_index": color,
                "color": palette_hex[color],
                "occurrence_count": len(occurrences),
                "front_to_back_occurrences": occurrences,
                "pairwise_merge_attempts": len(attempts),
                "feasible_pairwise_merges": sum(attempt["feasible"] for attempt in attempts),
                "locally_necessary_in_selected_order": not any(
                    attempt["feasible"] for attempt in attempts
                ),
                "attempts": attempts,
            }
        )
    return audits


def _plan_metrics(
    validation: dict[str, Any], aperture_pixels: int, optimization: OptimizationConfig
) -> dict[str, Any]:
    retained = [
        int(layer["visible_pixels"]) + int(layer["hidden_material_pixels"])
        for layer in validation["layers"]
    ]
    visible = [int(layer["visible_pixels"]) for layer in validation["layers"]]
    return {
        "layer_count": len(validation["layers"]),
        "total_retained_interior_pixel_layers": sum(retained),
        "unnecessary_hidden_interior_pixel_layers": sum(
            int(layer["hidden_material_pixels"]) for layer in validation["layers"]
        ),
        "near_full_layer_count": sum(
            pixels >= aperture_pixels * optimization.near_full_retained_fraction
            for pixels in retained
        ),
        "near_full_threshold_fraction": optimization.near_full_retained_fraction,
        "tiny_visible_layer_count": sum(
            pixels < aperture_pixels * optimization.tiny_visible_fraction for pixels in visible
        ),
        "tiny_visible_threshold_fraction": optimization.tiny_visible_fraction,
        "minimum_visible_contribution_pixels": min(visible),
        "minimum_visible_contribution_fraction": min(visible) / aperture_pixels,
        "geometry_pixel_accuracy_against_printable_target": validation[
            "geometry_pixel_accuracy_against_quantized_target"
        ],
        "mean_absolute_rgb_error_against_original_0_255": validation[
            "mean_absolute_rgb_error_against_original_0_255"
        ],
        "unsupported_thin_detail_count": validation["unsupported_thin_detail_count"],
    }


def _component_retention(labels: np.ndarray, reconstructed: np.ndarray) -> dict[str, Any]:
    total = 0
    retained = 0
    by_color: list[dict[str, int]] = []
    for color in sorted(int(value) for value in np.unique(labels)):
        count, components = cv2.connectedComponents(
            (labels == color).astype(np.uint8), connectivity=4
        )
        color_total = count - 1
        color_retained = 0
        for component in range(1, count):
            mask = components == component
            if np.all(reconstructed[mask] == color):
                color_retained += 1
        total += color_total
        retained += color_retained
        by_color.append(
            {
                "palette_index": color,
                "source_regions": color_total,
                "regions_retained_exactly": color_retained,
            }
        )
    return {
        "policy": "Every connected region in the quantized target is retained; no size-based deletion is allowed during optimization.",
        "source_regions": total,
        "regions_retained_exactly": retained,
        "retention_fraction": retained / total if total else 1.0,
        "by_palette_color": by_color,
    }


def _reveal_relationships(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    for lower_index, lower in enumerate(layers):
        visible = lower["visible_aperture"]
        for upper in layers[lower_index + 1 :]:
            upper_open = ~upper["retained_aperture"]
            relationships.append(
                {
                    "lower_depth": lower["depth"],
                    "lower_color": lower["color"],
                    "upper_depth": upper["depth"],
                    "upper_color": upper["color"],
                    "lower_visible_pixels": int(np.count_nonzero(visible)),
                    "pixels_open_in_upper_layer": int(np.count_nonzero(visible & upper_open)),
                    "fully_revealed_through_upper_cutout": bool(np.all(upper_open[visible])),
                }
            )
    return relationships


def _render_before_after(before: Path, after: Path, output: Path) -> None:
    first = Image.open(before).convert("RGB")
    second = Image.open(after).convert("RGB")
    height = max(first.height, second.height)
    canvas = Image.new("RGB", (first.width + second.width, height + 34), "white")
    canvas.paste(first, (0, 34))
    canvas.paste(second, (first.width, 34))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 10), "BEFORE: GREEDY", fill="black")
    draw.text((first.width + 8, 10), "AFTER: OPTIMIZED", fill="black")
    canvas.save(output)


def run_optimization_case(
    source: Path,
    case_id: str,
    baseline: Path,
    output: Path,
    poc_config: PocConfig = DEFAULT_CONFIG,
    optimization: OptimizationConfig = DEFAULT_OPTIMIZATION_CONFIG,
) -> dict[str, Any]:
    """Preserve the existing plan and emit a separately reviewable optimized plan."""

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copytree(baseline, output / "before")
    (output / "original").mkdir()
    shutil.copy2(source, output / "original" / "source.png")
    optimized_root = output / "optimized"
    for directory in ("automatic/layers", "previews", "reports"):
        (optimized_root / directory).mkdir(parents=True, exist_ok=True)

    baseline_source = baseline / "original" / "source.png"
    if not baseline_source.exists() or _sha256(baseline_source) != _sha256(source):
        raise ValueError("baseline source does not match optimization source")
    baseline_validation = json.loads(
        (baseline / "reports" / "validation.json").read_text(encoding="utf-8")
    )

    analysis = analyze_image(source, poc_config)
    sequence, graph, search_report = optimize_region_sequence(
        analysis["labels"], optimization
    )
    provider_labels, providers, groups = _replay_sequence(
        sequence, graph, np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    )
    optimized_analysis = dict(analysis)
    optimized_analysis["provider_labels"] = provider_labels
    optimized_analysis["providers"] = providers
    optimized_analysis["provider_splits"] = [
        {
            "palette_index": color,
            "reason": "exact topology requires this color at separated reachable depths",
            "provider_indices_front_to_back": [
                index for index, value in enumerate(sequence) if value == color
            ],
        }
        for color in sorted(set(sequence))
        if sequence.count(color) > 1
    ]
    order = discover_order(optimized_analysis, poc_config)
    if order["status"] != "FEASIBLE":
        raise RuntimeError("optimized provider sequence failed independent order validation")
    layers = construct_layers(optimized_analysis, order, poc_config)
    layer_files = _save_layer_artifacts(
        optimized_root, layers, optimized_analysis, poc_config
    )
    validation, reconstruction, mismatch = validate_plan(
        optimized_analysis, order, layers, poc_config
    )
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    printable = palette[analysis["labels"]]
    Image.fromarray(printable, "RGB").save(
        optimized_root / "previews" / "printable_target.png"
    )
    Image.fromarray(reconstruction, "RGB").save(
        optimized_root / "previews" / "reconstructed_front.png"
    )
    _render_comparison(
        analysis,
        reconstruction,
        mismatch,
        optimized_root / "previews" / "source_difference.png",
    )
    _render_exploded(
        layers, optimized_analysis, optimized_root / "previews" / "exploded_layer_masks.png"
    )

    relationships = _reveal_relationships(layers)
    manifest = {
        "schema_version": "3.3.0-layer-optimization-poc",
        "case_id": case_id,
        "status": "AUTOMATIC_OPTIMIZED_UNCORRECTED",
        "source": "../../original/source.png",
        "source_sha256": _sha256(source),
        "construction": {
            "method": "optimized_nested_full_sheet_subtractive_masks",
            "common_outer_pixels": [poc_config.outer_pixels, poc_config.outer_pixels],
            "continuous_perimeter_pixels": poc_config.perimeter_pixels,
            "one_color_per_layer": True,
            "visible_connector_fallback": False,
            "small_detail_deletion": False,
            "cad_generated": False,
        },
        "order_front_to_back_palette_indices": list(sequence),
        "order_back_to_front": [record["id"] for record in layer_files],
        "layers": layer_files,
        "reveal_relationships": relationships,
    }
    _write_json(optimized_root / "automatic" / "layer_stack.json", manifest)
    proposal = {
        "source_sha256": _sha256(source),
        "palette": [
            {"index": index, "hex": color, "pixels": int(analysis["counts"][index])}
            for index, color in enumerate(analysis["palette_hex"])
        ],
        "config": asdict(poc_config),
        "optimization_config": asdict(optimization),
        "search": search_report,
        "providers": providers,
        "provider_splits": optimized_analysis["provider_splits"],
        "manual_annotations_used": False,
        "manual_corrections_applied": 0,
        "automatic_visible_bridge_fallback": False,
    }
    _write_json(optimized_root / "automatic" / "proposal.json", proposal)

    reconstructed_labels, _ = _compose(layers, palette, poc_config)
    p = poc_config.perimeter_pixels
    retention = _component_retention(
        analysis["labels"], reconstructed_labels[p:-p, p:-p]
    )
    one_per_color = _one_layer_per_color_audit(analysis["labels"])
    repeated_audit = _merge_audit(
        sequence, groups, graph, analysis["palette_hex"]
    )
    validation.update(
        {
            "case_id": case_id,
            "automatic_proposal_preserved": True,
            "manual_corrections_applied": 0,
            "cad_generated": False,
            "layer_count": len(layers),
            "layer_files": layer_files,
            "reveal_relationships": relationships,
            "critical_detail_retention": retention,
        }
    )
    _write_json(optimized_root / "reports" / "validation.json", validation)
    _write_json(
        optimized_root / "reports" / "correction_log.json",
        {
            "automatic_proposal_sha256": _sha256(
                optimized_root / "automatic" / "proposal.json"
            ),
            "operations": [],
        },
    )

    before_metrics = _plan_metrics(
        baseline_validation, poc_config.analysis_pixels**2, optimization
    )
    after_metrics = _plan_metrics(
        validation, poc_config.analysis_pixels**2, optimization
    )
    report = {
        "case_id": case_id,
        "status": "READY_FOR_USER_TEST" if validation["status"] == "PASS" else "FAIL",
        "scope": "2D layer-stack optimization only; no CAD, STL, STEP, frame, or frontend",
        "source_sha256": _sha256(source),
        "before": before_metrics,
        "optimized": after_metrics,
        "changes": {
            "layers_removed": before_metrics["layer_count"] - after_metrics["layer_count"],
            "retained_pixel_layers_reduction": before_metrics[
                "total_retained_interior_pixel_layers"
            ]
            - after_metrics["total_retained_interior_pixel_layers"],
            "retained_pixel_layers_reduction_fraction": 1.0
            - after_metrics["total_retained_interior_pixel_layers"]
            / before_metrics["total_retained_interior_pixel_layers"],
            "near_full_layers_removed": before_metrics["near_full_layer_count"]
            - after_metrics["near_full_layer_count"],
            "tiny_visible_layers_removed": before_metrics["tiny_visible_layer_count"]
            - after_metrics["tiny_visible_layer_count"],
        },
        "optimization_algorithm": search_report,
        "layer_count_optimality": {
            "palette_color_lower_bound": len(analysis["palette_hex"]),
            "optimized_layer_count": len(layers),
            "global_minimum_proven": bool(
                one_per_color["feasible"]
                and len(layers) == len(analysis["palette_hex"])
            ),
            "note": (
                "The palette lower bound is attained."
                if one_per_color["feasible"]
                and len(layers) == len(analysis["palette_hex"])
                else "The bounded search found the reported plan, but does not prove that no smaller repeated-color plan exists."
            ),
        },
        "one_layer_per_palette_color_audit": one_per_color,
        "repeated_color_layer_audit": repeated_audit,
        "critical_detail_retention": retention,
        "fidelity_tradeoff": {
            "pixels_changed_from_printable_target": validation[
                "geometry_mismatched_pixels"
            ],
            "details_removed_for_optimization": 0,
            "visible_connector_pixels": validation[
                "visible_automatic_connector_pixels"
            ],
        },
        "remaining_fabrication_constraints": {
            "unsupported_thin_detail_count": validation[
                "unsupported_thin_detail_count"
            ],
            "minimum_feature_mm_assumption": poc_config.minimum_feature_mm,
            "cad_validation_performed": False,
            "requires_user_resolution_before_fabrication": validation[
                "unsupported_thin_detail_count"
            ]
            > 0,
        },
        "manual_corrections_applied": 0,
    }
    _write_json(output / "reports" / "optimization.json", report)
    (output / "comparison").mkdir()
    _render_before_after(
        output / "before" / "previews" / "reconstructed_front.png",
        optimized_root / "previews" / "reconstructed_front.png",
        output / "comparison" / "before_after_reconstructed.png",
    )

    checksum_files = sorted(
        path
        for path in output.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    (output / "reports" / "checksums.sha256").write_text(
        "".join(
            f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_files
        ),
        encoding="utf-8",
    )
    return report
