"""Width-aware 2D planning over actual cumulative retained-material masks.

Every search state is a front-to-back prefix of assigned source regions.  That
prefix is exactly the aperture retained by one physical sheet, so the planner
evaluates the real full-sheet mask (including the common perimeter) before it
accepts or ranks the state.  Same-color source regions may be deferred to a
later occurrence instead of being forced into one provider batch.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .connectivity_repair import (
    _render_montage,
    _render_width_map,
    _sha256,
    _width_parameters,
    _write_json,
    audit_connection_width,
    audit_edge_connectivity,
)
from .layer_optimization import (
    DEFAULT_OPTIMIZATION_CONFIG,
    OptimizationConfig,
    _advance_neighbours,
    _bit_area,
    _bit_indices,
    _replay_sequence,
    build_region_graph,
)
from .subtractive_poc import (
    DEFAULT_CONFIG,
    PocConfig,
    _outer_and_perimeter,
    _render_comparison,
    _render_exploded,
    _save_layer_artifacts,
    analyze_image,
    construct_layers,
    discover_order,
    validate_plan,
)


@dataclass(frozen=True)
class WidthAwareSearchConfig:
    hard_beam_states: int = 32
    unresolved_beam_states: int = 64
    max_layer_increase: int = 0
    candidate_singletons_per_color: int = 2
    selection_strategy: str = "diverse"


DEFAULT_WIDTH_AWARE_CONFIG = WidthAwareSearchConfig()


@dataclass(frozen=True)
class WidthStateMetrics:
    orphan_core_components: int
    orphan_core_pixels: int
    four_neighbor_components: int
    retained_pixels: int


@dataclass(frozen=True)
class WidthSearchRecord:
    sequence: tuple[int, ...]
    groups: tuple[int, ...]
    neighbour_bits: int
    assigned_area: int
    retained_pixel_layers: int
    failed_prefixes: int
    cumulative_orphan_components: int
    cumulative_orphan_pixels: int
    worst_prefix_orphan_components: int


@dataclass(frozen=True)
class _ContinuationProfile:
    valid_next_states: int
    valid_colors: int
    valid_color_bits: int
    candidate_region_bits: int
    reachable_region_bits: int
    largest_group_regions: int
    largest_group_area: int


class _ActualMaskEvaluator:
    def __init__(
        self,
        graph: dict[str, Any],
        poc: PocConfig,
        optimization: OptimizationConfig,
    ) -> None:
        self.graph = graph
        self.poc = poc
        self.region_map: np.ndarray = graph["region_map"]
        self.node_count = len(graph["nodes"])
        self.p = poc.perimeter_pixels
        _, self.perimeter = _outer_and_perimeter(poc)
        parameters = _width_parameters(poc, optimization)
        self.kernel_size = int(parameters["morphology_kernel_size_pixels"])
        self.kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (self.kernel_size, self.kernel_size)
        )
        self.cache: dict[int, WidthStateMetrics] = {}

    def aperture(self, state: int) -> np.ndarray:
        selected = np.zeros(self.node_count, dtype=bool)
        selected[list(_bit_indices(state))] = True
        return selected[self.region_map]

    def physical(self, state: int) -> np.ndarray:
        physical = self.perimeter.copy()
        physical[self.p : -self.p, self.p : -self.p] = self.aperture(state)
        return physical

    def metrics(self, state: int) -> WidthStateMetrics:
        cached = self.cache.get(state)
        if cached is not None:
            return cached
        physical = self.physical(state)
        count4, _ = cv2.connectedComponents(
            physical.astype(np.uint8), connectivity=4
        )
        core = cv2.erode(
            physical.astype(np.uint8), self.kernel, iterations=1
        ).astype(bool)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            core.astype(np.uint8), connectivity=4
        )
        anchored = set(
            int(value)
            for value in np.unique(labels[core & self.perimeter])
            if value
        )
        orphan_ids = [
            component for component in range(1, count) if component not in anchored
        ]
        result = WidthStateMetrics(
            orphan_core_components=len(orphan_ids),
            orphan_core_pixels=int(
                sum(stats[component, cv2.CC_STAT_AREA] for component in orphan_ids)
            ),
            four_neighbor_components=count4 - 1,
            retained_pixels=int(np.count_nonzero(physical)),
        )
        self.cache[state] = result
        return result


def _candidate_groups_legacy(
    state: int,
    neighbour_bits: int,
    color: int,
    graph: dict[str, Any],
    evaluator: _ActualMaskEvaluator,
    hard_width: bool,
    singleton_limit: int,
    allowed_bits: int,
) -> list[int]:
    eligible = (
        (neighbour_bits | int(graph["border_bits"]))
        & int(graph["color_bits"][color])
        & allowed_bits
        & ~state
    )
    if not eligible:
        return []
    nodes = graph["nodes"]
    indices = list(_bit_indices(eligible))
    orders = (
        sorted(indices, key=lambda index: (-nodes[index].area, index)),
        sorted(indices, key=lambda index: (nodes[index].area, index)),
    )
    groups: set[int] = {eligible}
    for order in orders:
        group = 0
        deferred: list[int] = []
        for index in order:
            candidate = state | group | (1 << index)
            metrics = evaluator.metrics(candidate)
            if metrics.four_neighbor_components == 1 and (
                not hard_width or metrics.orphan_core_components == 0
            ):
                group |= 1 << index
            else:
                deferred.append(index)
        # A second pass catches regions that become supportable after the first
        # group has widened the cumulative retained geometry.
        for index in deferred:
            candidate = state | group | (1 << index)
            metrics = evaluator.metrics(candidate)
            if metrics.four_neighbor_components == 1 and (
                not hard_width or metrics.orphan_core_components == 0
            ):
                group |= 1 << index
        if group:
            groups.add(group)

    ranked_singletons = sorted(
        indices,
        key=lambda index: (
            evaluator.metrics(state | (1 << index)).orphan_core_components,
            evaluator.metrics(state | (1 << index)).orphan_core_pixels,
            -nodes[index].area,
            index,
        ),
    )
    for index in ranked_singletons[:singleton_limit]:
        groups.add(1 << index)

    accepted: list[int] = []
    for group in groups:
        metrics = evaluator.metrics(state | group)
        if metrics.four_neighbor_components != 1:
            continue
        if hard_width and metrics.orphan_core_components:
            continue
        accepted.append(group)
    return sorted(
        accepted,
        key=lambda group: (
            evaluator.metrics(state | group).orphan_core_components,
            evaluator.metrics(state | group).orphan_core_pixels,
            -_bit_area(group, tuple(node.area for node in nodes)),
            group,
        ),
    )


def _topology_cohort_groups(
    state: int,
    eligible: int,
    graph: dict[str, Any],
    allowed_bits: int,
) -> set[int]:
    """Return bounded partial groups with the same occlusion role.

    A cohort contains same-color regions that are already supported by the same
    assigned regions (or by the outer boundary) and expose the same palette
    colors on their still-hidden side.  These signatures are a compact proxy
    for a common containment/occlusion role.  One cohort is emitted per exact
    signature, rather than enumerating arbitrary region subsets.
    """

    nodes = graph["nodes"]
    adjacency = graph["adjacency"]
    border_bits = int(graph["border_bits"])
    cohorts: dict[tuple[int, bool, int], int] = {}
    for index in _bit_indices(eligible):
        adjacent = int(adjacency[index])
        support_bits = adjacent & state
        future_bits = adjacent & allowed_bits & ~state & ~eligible
        future_colors = 0
        for future_index in _bit_indices(future_bits):
            future_colors |= 1 << int(nodes[future_index].palette_index)
        signature = (
            support_bits,
            bool((1 << index) & border_bits),
            future_colors,
        )
        cohorts[signature] = cohorts.get(signature, 0) | (1 << index)
    return {
        group
        for group in cohorts.values()
        if group.bit_count() > 1 and group != eligible
    }


def _candidate_groups(
    state: int,
    neighbour_bits: int,
    color: int,
    graph: dict[str, Any],
    evaluator: _ActualMaskEvaluator,
    hard_width: bool,
    singleton_limit: int,
    allowed_bits: int,
) -> list[int]:
    """Extend the legacy choices with bounded topology-coherent subsets."""

    legacy = _candidate_groups_legacy(
        state,
        neighbour_bits,
        color,
        graph,
        evaluator,
        hard_width,
        singleton_limit,
        allowed_bits,
    )
    eligible = (
        (neighbour_bits | int(graph["border_bits"]))
        & int(graph["color_bits"][color])
        & allowed_bits
        & ~state
    )
    if eligible.bit_count() < 3:
        return legacy

    groups = set(legacy)
    for group in _topology_cohort_groups(state, eligible, graph, allowed_bits):
        metrics = evaluator.metrics(state | group)
        if metrics.four_neighbor_components != 1:
            continue
        if hard_width and metrics.orphan_core_components:
            continue
        groups.add(group)
    nodes = graph["nodes"]
    areas = tuple(node.area for node in nodes)
    return sorted(
        groups,
        key=lambda group: (
            evaluator.metrics(state | group).orphan_core_components,
            evaluator.metrics(state | group).orphan_core_pixels,
            -group.bit_count(),
            -_bit_area(group, areas),
            group,
        ),
    )


def _continuation_profile(
    state: int,
    record: WidthSearchRecord,
    graph: dict[str, Any],
    evaluator: _ActualMaskEvaluator,
    hard_width: bool,
    singleton_limit: int,
    allowed_bits: int,
    areas: tuple[int, ...],
) -> _ContinuationProfile:
    """Bounded one-layer feasibility probe using the existing generator."""

    remaining = allowed_bits & ~state
    reachable = (
        (record.neighbour_bits | int(graph["border_bits"])) & remaining
    )
    next_states: set[int] = set()
    valid_color_bits = 0
    candidate_region_bits = 0
    largest_group_regions = 0
    largest_group_area = 0
    for color in range(len(graph["color_bits"])):
        groups = _candidate_groups(
            state,
            record.neighbour_bits,
            color,
            graph,
            evaluator,
            hard_width,
            singleton_limit,
            allowed_bits,
        )
        if groups:
            valid_color_bits |= 1 << color
        for group in groups:
            next_states.add(state | group)
            candidate_region_bits |= group
            largest_group_regions = max(largest_group_regions, group.bit_count())
            largest_group_area = max(largest_group_area, _bit_area(group, areas))
    return _ContinuationProfile(
        valid_next_states=len(next_states),
        valid_colors=valid_color_bits.bit_count(),
        valid_color_bits=valid_color_bits,
        candidate_region_bits=candidate_region_bits,
        reachable_region_bits=reachable,
        largest_group_regions=largest_group_regions,
        largest_group_area=largest_group_area,
    )


def _legacy_state_rank(
    state: int,
    record: WidthSearchRecord,
    depth: int,
    graph: dict[str, Any],
    evaluator: _ActualMaskEvaluator,
    allowed_bits: int,
) -> tuple[Any, ...]:
    remaining_colors = sum(
        bool(int(bits) & allowed_bits & ~state) for bits in graph["color_bits"]
    )
    current = evaluator.metrics(state)
    return (
        depth + remaining_colors,
        record.failed_prefixes,
        current.orphan_core_components,
        record.cumulative_orphan_components,
        current.orphan_core_pixels,
        record.cumulative_orphan_pixels,
        -record.assigned_area,
        record.retained_pixel_layers,
        record.sequence,
    )


def _diverse_state_rank(
    state: int,
    record: WidthSearchRecord,
    profile: _ContinuationProfile,
    depth: int,
    max_layers: int,
    graph: dict[str, Any],
    evaluator: _ActualMaskEvaluator,
    allowed_bits: int,
) -> tuple[Any, ...]:
    """Rank completion viability before current prefix size."""

    remaining = allowed_bits & ~state
    remaining_colors = sum(
        bool(int(bits) & remaining) for bits in graph["color_bits"]
    )
    layers_left = max_layers - depth
    required_regions_per_layer = (
        (remaining.bit_count() + layers_left - 1) // layers_left
        if layers_left > 0
        else remaining.bit_count()
    )
    current = evaluator.metrics(state)
    repeated_depths = len(record.sequence) - len(set(record.sequence))
    dead_end = bool(remaining) and profile.valid_next_states == 0
    return (
        int(dead_end),
        max(0, remaining_colors - layers_left),
        max(0, required_regions_per_layer - profile.largest_group_regions),
        record.failed_prefixes,
        current.orphan_core_components,
        profile.reachable_region_bits.bit_count()
        - profile.candidate_region_bits.bit_count(),
        -profile.valid_colors,
        -profile.valid_next_states,
        -profile.candidate_region_bits.bit_count(),
        repeated_depths,
        -state.bit_count(),
        -record.assigned_area,
        record.retained_pixel_layers,
        record.sequence,
    )


def _bit_jaccard(first: int, second: int) -> float:
    union = (first | second).bit_count()
    return (first & second).bit_count() / union if union else 1.0


def _select_diverse_beam(
    ranked: list[tuple[int, WidthSearchRecord]],
    profiles: dict[int, _ContinuationProfile],
    max_states: int,
) -> list[tuple[int, WidthSearchRecord]]:
    """Keep high-quality representatives, then maximize topology diversity."""

    if len(ranked) <= max_states:
        return ranked
    rank_index = {state: index for index, (state, _) in enumerate(ranked)}
    selected: list[tuple[int, WidthSearchRecord]] = []
    selected_states: set[int] = set()

    # Reserve half the beam for distinct layer-order/continuation signatures.
    signature_quota = max(1, max_states // 2)
    signatures: set[tuple[int, int, int]] = set()
    for item in ranked:
        state, record = item
        profile = profiles[state]
        signature = (
            profile.valid_color_bits,
            record.sequence[-1] if record.sequence else -1,
            min(profile.valid_next_states, 3),
        )
        if signature in signatures:
            continue
        signatures.add(signature)
        selected.append(item)
        selected_states.add(state)
        if len(selected) >= signature_quota:
            break

    while len(selected) < max_states:
        remaining = [item for item in ranked if item[0] not in selected_states]
        if not remaining:
            break

        def diversity_score(item: tuple[int, WidthSearchRecord]) -> tuple[Any, ...]:
            state, _ = item
            profile = profiles[state]
            maximum_similarity = max(
                0.7 * _bit_jaccard(state, kept_state)
                + 0.3
                * _bit_jaccard(
                    profile.candidate_region_bits,
                    profiles[kept_state].candidate_region_bits,
                )
                for kept_state in selected_states
            )
            return (
                int(profile.valid_next_states == 0),
                round(maximum_similarity, 6),
                rank_index[state],
                state,
            )

        chosen = min(remaining, key=diversity_score)
        selected.append(chosen)
        selected_states.add(chosen[0])
    return sorted(selected, key=lambda item: rank_index[item[0]])


def _mean_pairwise_state_distance(states: list[int]) -> float:
    if len(states) < 2:
        return 0.0
    distances = [
        1.0 - _bit_jaccard(states[first], states[second])
        for first in range(len(states))
        for second in range(first + 1, len(states))
    ]
    return float(sum(distances) / len(distances))


def _search(
    graph: dict[str, Any],
    evaluator: _ActualMaskEvaluator,
    max_layers: int,
    max_states: int,
    hard_width: bool,
    singleton_limit: int,
    goal_bits: int | None = None,
    allowed_bits: int | None = None,
    start_state: int = 0,
    start_neighbour_bits: int | None = None,
    selection_strategy: str = "diverse",
    progress_callback: Any | None = None,
) -> tuple[WidthSearchRecord | None, dict[str, Any]]:
    if selection_strategy not in {"legacy", "diverse"}:
        raise ValueError(f"unknown search selection strategy: {selection_strategy}")
    nodes = tuple(graph["nodes"])
    areas = tuple(node.area for node in nodes)
    full = int(graph["full_bits"] if goal_bits is None else goal_bits)
    allowed = int(graph["full_bits"] if allowed_bits is None else allowed_bits)
    aperture_pixels = int(evaluator.region_map.size)
    initial_neighbours = (
        int(graph["border_bits"])
        if start_neighbour_bits is None
        else start_neighbour_bits
    )
    states: dict[int, WidthSearchRecord] = {
        start_state: WidthSearchRecord(
            (), (), initial_neighbours, 0, 0, 0, 0, 0, 0
        )
    }
    explored = 0
    pruned = 0
    frontier: list[dict[str, Any]] = []
    best_complete: WidthSearchRecord | None = None
    continuation_cache: dict[int, _ContinuationProfile] = {}
    total_candidate_groups = 0

    for depth in range(1, max_layers + 1):
        cache_before = set(evaluator.cache)
        candidates: dict[int, WidthSearchRecord] = {}
        for state, record in states.items():
            for color in range(len(graph["color_bits"])):
                for group in _candidate_groups(
                    state,
                    record.neighbour_bits,
                    color,
                    graph,
                    evaluator,
                    hard_width,
                    singleton_limit,
                    allowed,
                ):
                    total_candidate_groups += 1
                    next_state = state | group
                    if next_state == state:
                        continue
                    metrics = evaluator.metrics(next_state)
                    added_area = _bit_area(group, areas)
                    candidate = WidthSearchRecord(
                        sequence=record.sequence + (color,),
                        groups=record.groups + (group,),
                        neighbour_bits=_advance_neighbours(
                            record.neighbour_bits, group, graph["adjacency"]
                        ),
                        assigned_area=record.assigned_area + added_area,
                        retained_pixel_layers=record.retained_pixel_layers
                        + int(np.count_nonzero(evaluator.aperture(next_state))),
                        failed_prefixes=record.failed_prefixes
                        + int(metrics.orphan_core_components > 0),
                        cumulative_orphan_components=record.cumulative_orphan_components
                        + metrics.orphan_core_components,
                        cumulative_orphan_pixels=record.cumulative_orphan_pixels
                        + metrics.orphan_core_pixels,
                        worst_prefix_orphan_components=max(
                            record.worst_prefix_orphan_components,
                            metrics.orphan_core_components,
                        ),
                    )
                    existing = candidates.get(next_state)
                    score = (
                        candidate.failed_prefixes,
                        candidate.cumulative_orphan_components,
                        candidate.cumulative_orphan_pixels,
                        candidate.retained_pixel_layers,
                        candidate.sequence,
                    )
                    if existing is None or score < (
                        existing.failed_prefixes,
                        existing.cumulative_orphan_components,
                        existing.cumulative_orphan_pixels,
                        existing.retained_pixel_layers,
                        existing.sequence,
                    ):
                        candidates[next_state] = candidate

        explored += len(candidates)
        if full in candidates:
            best_complete = candidates[full]
            frontier.append(
                {
                    "depth": depth,
                    "candidate_states": len(candidates),
                    "kept_states": len(candidates),
                    "assigned_source_regions_min": min(
                        state.bit_count() for state in candidates
                    ),
                    "assigned_source_regions_max": max(
                        state.bit_count() for state in candidates
                    ),
                    "assigned_visible_pixels_min": min(
                        record.assigned_area for record in candidates.values()
                    ),
                    "assigned_visible_pixels_max": max(
                        record.assigned_area for record in candidates.values()
                    ),
                    "complete_state_found": True,
                }
            )
            if progress_callback is not None:
                progress_callback(dict(frontier[-1]))
            break

        profiles: dict[int, _ContinuationProfile] = {}
        if selection_strategy == "diverse" and depth < max_layers:
            for state, record in candidates.items():
                profile = continuation_cache.get(state)
                if profile is None:
                    profile = _continuation_profile(
                        state,
                        record,
                        graph,
                        evaluator,
                        hard_width,
                        singleton_limit,
                        allowed,
                        areas,
                    )
                    continuation_cache[state] = profile
                profiles[state] = profile
            ranked = sorted(
                candidates.items(),
                key=lambda item: _diverse_state_rank(
                    item[0],
                    item[1],
                    profiles[item[0]],
                    depth,
                    max_layers,
                    graph,
                    evaluator,
                    allowed,
                ),
            )
            kept = _select_diverse_beam(ranked, profiles, max_states)
        else:
            ranked = sorted(
                candidates.items(),
                key=lambda item: _legacy_state_rank(
                    item[0], item[1], depth, graph, evaluator, allowed
                ),
            )
            kept = ranked[:max_states]

        # The legacy selector gets the same retained-frontier diagnostics, but
        # these probes happen only after selection and cannot affect it.
        if depth < max_layers:
            for state, record in kept:
                profile = continuation_cache.get(state)
                if profile is None:
                    profile = _continuation_profile(
                        state,
                        record,
                        graph,
                        evaluator,
                        hard_width,
                        singleton_limit,
                        allowed,
                        areas,
                    )
                    continuation_cache[state] = profile
                profiles[state] = profile
        pruned += max(0, len(ranked) - len(kept))
        newly_evaluated = [
            metrics
            for state, metrics in evaluator.cache.items()
            if state not in cache_before
        ]
        kept_states = [state for state, _ in kept]
        frontier.append(
            {
                "depth": depth,
                "candidate_states": len(ranked),
                "kept_states": len(kept),
                "assigned_source_regions_min": min(
                    (state.bit_count() for state in kept_states), default=0
                ),
                "assigned_source_regions_max": max(
                    (state.bit_count() for state in kept_states), default=0
                ),
                "assigned_visible_pixels_min": min(
                    (record.assigned_area for _, record in kept), default=0
                ),
                "assigned_visible_pixels_max": max(
                    (record.assigned_area for _, record in kept), default=0
                ),
                "remaining_reachable_regions_min": min(
                    (
                        profiles[state].reachable_region_bits.bit_count()
                        for state in kept_states
                    ),
                    default=0,
                ),
                "remaining_reachable_regions_max": max(
                    (
                        profiles[state].reachable_region_bits.bit_count()
                        for state in kept_states
                    ),
                    default=0,
                ),
                "valid_next_states_total": sum(
                    profiles[state].valid_next_states for state in kept_states
                ),
                "dead_end_states_retained": sum(
                    profiles[state].valid_next_states == 0 for state in kept_states
                ),
                "mean_pairwise_assigned_state_distance": _mean_pairwise_state_distance(
                    kept_states
                ),
                "new_actual_masks_evaluated": len(newly_evaluated),
                "new_four_neighbor_rejections": sum(
                    metrics.four_neighbor_components != 1
                    for metrics in newly_evaluated
                ),
                "new_width_rejections": sum(
                    metrics.orphan_core_components > 0
                    for metrics in newly_evaluated
                ),
                "complete_state_found": False,
            }
        )
        if progress_callback is not None:
            progress_callback(dict(frontier[-1]))
        states = dict(kept)
        if not states:
            break

    report = {
        "method": "bounded_actual_cumulative_mask_beam_search",
        "hard_width_gate_during_search": hard_width,
        "global_optimality_proven": False,
        "global_infeasibility_proven": False,
        "max_layers": max_layers,
        "goal_source_regions": full.bit_count(),
        "allowed_source_regions": allowed.bit_count(),
        "fixed_start_source_regions": start_state.bit_count(),
        "max_states_per_depth": max_states,
        "selection_strategy": selection_strategy,
        "explored_candidate_states": explored,
        "candidate_groups_expanded": total_candidate_groups,
        "pruned_candidate_states": pruned,
        "evaluated_unique_actual_masks": len(evaluator.cache),
        "four_neighbor_rejected_actual_masks": sum(
            metrics.four_neighbor_components != 1
            for metrics in evaluator.cache.values()
        ),
        "width_rejected_actual_masks": sum(
            metrics.orphan_core_components > 0
            for metrics in evaluator.cache.values()
        ),
        "morphology_kernel_pixels": evaluator.kernel_size,
        "frontier_widths": frontier,
        "status": "CANDIDATE_FOUND" if best_complete else "NO_COMPLETE_CANDIDATE_WITHIN_BOUNDS",
    }
    return best_complete, report


def plan_width_aware(
    labels: np.ndarray,
    baseline_layer_count: int,
    poc: PocConfig = DEFAULT_CONFIG,
    optimization: OptimizationConfig = DEFAULT_OPTIMIZATION_CONFIG,
    search_config: WidthAwareSearchConfig = DEFAULT_WIDTH_AWARE_CONFIG,
) -> tuple[WidthSearchRecord, dict[str, Any], dict[str, Any]]:
    graph = build_region_graph(labels, connectivity=4, minimum_contact_pixels=1)
    evaluator = _ActualMaskEvaluator(graph, poc, optimization)
    max_layers = baseline_layer_count + search_config.max_layer_increase
    hard, hard_report = _search(
        graph,
        evaluator,
        max_layers,
        search_config.hard_beam_states,
        True,
        search_config.candidate_singletons_per_color,
        goal_bits=None,
        allowed_bits=None,
        selection_strategy=search_config.selection_strategy,
    )
    if hard is not None:
        return hard, graph, {
            "selected_mode": "STRICT_WIDTH_VALID",
            "hard_search": hard_report,
            "fallback_search": None,
        }
    unresolved, fallback_report = _search(
        graph,
        evaluator,
        max_layers,
        search_config.unresolved_beam_states,
        False,
        search_config.candidate_singletons_per_color,
        goal_bits=None,
        allowed_bits=None,
        selection_strategy=search_config.selection_strategy,
    )
    if unresolved is None:
        raise RuntimeError("width-aware planner found no complete candidate within bounds")
    return unresolved, graph, {
        "selected_mode": "UNRESOLVED_BEST_BOUNDED_CANDIDATE",
        "hard_search": hard_report,
        "fallback_search": fallback_report,
    }


def plan_width_aware_prefix(
    labels: np.ndarray,
    palette_rgb: np.ndarray,
    baseline_sequence: tuple[int, ...],
    prefix_layers: int,
    poc: PocConfig = DEFAULT_CONFIG,
    optimization: OptimizationConfig = DEFAULT_OPTIMIZATION_CONFIG,
    search_config: WidthAwareSearchConfig = DEFAULT_WIDTH_AWARE_CONFIG,
) -> tuple[WidthSearchRecord, dict[str, Any], dict[str, Any]]:
    """Replan a problematic front prefix and preserve the complete suffix.

    Reaching the identical prefix-region union means every later cumulative
    physical mask and all surrounding occlusion context remain unchanged.
    """

    graph = build_region_graph(labels, connectivity=4, minimum_contact_pixels=1)
    _, _, baseline_groups = _replay_sequence(
        baseline_sequence, graph, palette_rgb
    )
    if prefix_layers <= 0 or prefix_layers >= len(baseline_groups):
        raise ValueError("prefix_layers must select a proper non-empty prefix")
    goal = 0
    for group in baseline_groups[:prefix_layers]:
        goal |= group
    evaluator = _ActualMaskEvaluator(graph, poc, optimization)
    hard, hard_report = _search(
        graph,
        evaluator,
        prefix_layers,
        search_config.hard_beam_states,
        True,
        search_config.candidate_singletons_per_color,
        goal_bits=goal,
        allowed_bits=goal,
        selection_strategy=search_config.selection_strategy,
    )
    selected = hard
    fallback_report: dict[str, Any] | None = None
    selected_mode = "STRICT_WIDTH_VALID_TARGET_PREFIX"
    if selected is None:
        selected, fallback_report = _search(
            graph,
            evaluator,
            prefix_layers,
            search_config.unresolved_beam_states,
            False,
            search_config.candidate_singletons_per_color,
            goal_bits=goal,
            allowed_bits=goal,
            selection_strategy=search_config.selection_strategy,
        )
        selected_mode = "UNRESOLVED_TARGET_PREFIX_CANDIDATE"
    if selected is None:
        raise RuntimeError("width-aware prefix planner found no complete candidate")

    full_sequence = selected.sequence + baseline_sequence[prefix_layers:]
    full_groups = selected.groups + tuple(baseline_groups[prefix_layers:])
    state = 0
    neighbours = int(graph["border_bits"])
    failed = 0
    orphan_components = 0
    orphan_pixels = 0
    worst = 0
    retained = 0
    assigned_area = 0
    areas = tuple(node.area for node in graph["nodes"])
    for group in full_groups:
        state |= group
        neighbours = _advance_neighbours(neighbours, group, graph["adjacency"])
        metrics = evaluator.metrics(state)
        failed += int(metrics.orphan_core_components > 0)
        orphan_components += metrics.orphan_core_components
        orphan_pixels += metrics.orphan_core_pixels
        worst = max(worst, metrics.orphan_core_components)
        retained += int(np.count_nonzero(evaluator.aperture(state)))
        assigned_area += _bit_area(group, areas)
    full_record = WidthSearchRecord(
        sequence=full_sequence,
        groups=full_groups,
        neighbour_bits=neighbours,
        assigned_area=assigned_area,
        retained_pixel_layers=retained,
        failed_prefixes=failed,
        cumulative_orphan_components=orphan_components,
        cumulative_orphan_pixels=orphan_pixels,
        worst_prefix_orphan_components=worst,
    )
    return full_record, graph, {
        "selected_mode": selected_mode,
        "scope": {
            "kind": "representative_problematic_full_sheet_prefix",
            "replanned_front_to_back_depths": [0, prefix_layers - 1],
            "corresponding_baseline_back_to_front_depths": [
                len(baseline_groups) - prefix_layers,
                len(baseline_groups) - 1,
            ],
            "prefix_source_region_union_preserved": True,
            "remaining_provider_groups_preserved": len(baseline_groups)
            - prefix_layers,
            "cropped_geometry_used": False,
            "artificial_frame_used": False,
        },
        "hard_search": hard_report,
        "fallback_search": fallback_report,
    }


def plan_width_aware_window(
    labels: np.ndarray,
    palette_rgb: np.ndarray,
    baseline_sequence: tuple[int, ...],
    start_depth: int,
    end_depth_exclusive: int,
    poc: PocConfig = DEFAULT_CONFIG,
    optimization: OptimizationConfig = DEFAULT_OPTIMIZATION_CONFIG,
    search_config: WidthAwareSearchConfig = DEFAULT_WIDTH_AWARE_CONFIG,
) -> tuple[WidthSearchRecord, dict[str, Any], dict[str, Any]]:
    """Replan a bounded provider window with exact full-sheet context."""

    graph = build_region_graph(labels, connectivity=4, minimum_contact_pixels=1)
    _, _, baseline_groups = _replay_sequence(
        baseline_sequence, graph, palette_rgb
    )
    if not (0 <= start_depth < end_depth_exclusive <= len(baseline_groups)):
        raise ValueError("invalid provider window")
    start_state = 0
    start_neighbours = int(graph["border_bits"])
    for group in baseline_groups[:start_depth]:
        start_state |= group
        start_neighbours = _advance_neighbours(
            start_neighbours, group, graph["adjacency"]
        )
    goal = start_state
    for group in baseline_groups[start_depth:end_depth_exclusive]:
        goal |= group
    allowed = goal & ~start_state
    evaluator = _ActualMaskEvaluator(graph, poc, optimization)
    selected, search_report = _search(
        graph,
        evaluator,
        end_depth_exclusive - start_depth,
        search_config.unresolved_beam_states,
        False,
        search_config.candidate_singletons_per_color,
        goal_bits=goal,
        allowed_bits=allowed,
        start_state=start_state,
        start_neighbour_bits=start_neighbours,
        selection_strategy=search_config.selection_strategy,
    )
    if selected is None:
        raise RuntimeError("width-aware window planner found no complete candidate")

    full_sequence = (
        baseline_sequence[:start_depth]
        + selected.sequence
        + baseline_sequence[end_depth_exclusive:]
    )
    full_groups = (
        tuple(baseline_groups[:start_depth])
        + selected.groups
        + tuple(baseline_groups[end_depth_exclusive:])
    )
    state = 0
    neighbours = int(graph["border_bits"])
    failed = 0
    orphan_components = 0
    orphan_pixels = 0
    worst = 0
    retained = 0
    assigned_area = 0
    areas = tuple(node.area for node in graph["nodes"])
    for group in full_groups:
        state |= group
        neighbours = _advance_neighbours(neighbours, group, graph["adjacency"])
        metrics = evaluator.metrics(state)
        failed += int(metrics.orphan_core_components > 0)
        orphan_components += metrics.orphan_core_components
        orphan_pixels += metrics.orphan_core_pixels
        worst = max(worst, metrics.orphan_core_components)
        retained += int(np.count_nonzero(evaluator.aperture(state)))
        assigned_area += _bit_area(group, areas)
    full_record = WidthSearchRecord(
        sequence=full_sequence,
        groups=full_groups,
        neighbour_bits=neighbours,
        assigned_area=assigned_area,
        retained_pixel_layers=retained,
        failed_prefixes=failed,
        cumulative_orphan_components=orphan_components,
        cumulative_orphan_pixels=orphan_pixels,
        worst_prefix_orphan_components=worst,
    )
    return full_record, graph, {
        "selected_mode": "UNRESOLVED_WIDTH_AWARE_WINDOW_CANDIDATE",
        "scope": {
            "kind": "representative_problematic_full_sheet_window",
            "replanned_front_to_back_depths": [
                start_depth,
                end_depth_exclusive - 1,
            ],
            "corresponding_baseline_back_to_front_depths": [
                len(baseline_groups) - end_depth_exclusive,
                len(baseline_groups) - start_depth - 1,
            ],
            "window_entry_state_preserved": True,
            "window_exit_source_region_union_preserved": True,
            "foreground_provider_groups_preserved": start_depth,
            "remaining_provider_groups_preserved": len(baseline_groups)
            - end_depth_exclusive,
            "cropped_geometry_used": False,
            "artificial_frame_used": False,
        },
        "hard_search": None,
        "fallback_search": search_report,
    }


def _materialize_plan(
    record: WidthSearchRecord,
    graph: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    region_map: np.ndarray = graph["region_map"]
    nodes = graph["nodes"]
    provider_labels = np.full(region_map.shape, 255, dtype=np.uint8)
    providers: list[dict[str, Any]] = []
    for provider_index, (color, group) in enumerate(
        zip(record.sequence, record.groups)
    ):
        indices = list(_bit_indices(group))
        mask = np.isin(region_map, indices)
        if np.any(provider_labels[mask] != 255):
            raise RuntimeError("width-aware provider groups overlap")
        provider_labels[mask] = provider_index
        providers.append(
            {
                "index": provider_index,
                "palette_index": color,
                "color": analysis["palette_hex"][color],
                "role": "width_aware_actual_mask_search_batch",
                "pixels": int(sum(nodes[index].area for index in indices)),
                "source_region_count": len(indices),
                "source_region_indices": indices,
                "front_to_back_rank": provider_index,
            }
        )
    if np.any(provider_labels == 255):
        raise RuntimeError("width-aware plan did not assign every source pixel")
    planned = dict(analysis)
    planned["provider_labels"] = provider_labels
    planned["providers"] = providers
    return planned


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _width_rows(
    layers: list[dict[str, Any]],
    records: list[dict[str, Any]],
    output: Path,
    poc: PocConfig,
    optimization: OptimizationConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    paths: list[Path] = []
    for layer, record in zip(layers, records):
        edge, _ = audit_edge_connectivity(layer["physical_mask"])
        width, maps = audit_connection_width(
            layer["physical_mask"], layer["visible_aperture"], poc, optimization
        )
        row = {
            "id": record["id"],
            "depth_back_to_front": record["depth_back_to_front"],
            "color": record["color"],
            "edge_connectivity": edge,
            "width": width,
        }
        rows.append(row)
        path = output / f"{record['id']}_width.png"
        _render_width_map(
            layer["physical_mask"],
            maps,
            path,
            f"depth {record['depth_back_to_front']} {record['color']} | 4-neighbour={edge['four_neighbor_status']} width={width['status']} orphans={width['orphan_structural_core_components']}",
        )
        paths.append(path)
    _render_montage(paths, output / "width_montage.png")
    summary = {
        "layer_count": len(rows),
        "four_neighbor_failed_layers": sum(
            row["edge_connectivity"]["four_neighbor_status"] != "PASS"
            for row in rows
        ),
        "width_failed_layers": sum(row["width"]["status"] != "PASS" for row in rows),
        "orphan_structural_core_components": sum(
            row["width"]["orphan_structural_core_components"] for row in rows
        ),
        "orphan_structural_core_pixels": sum(
            row["width"]["orphan_structural_core_pixels"] for row in rows
        ),
        "thin_decorative_visible_components": sum(
            row["width"]["thin_decorative_visible_components"] for row in rows
        ),
    }
    return rows, summary


def _saved_width_rows(
    root: Path,
    manifest: dict[str, Any],
    output: Path,
    poc: PocConfig,
    optimization: OptimizationConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    layers: list[dict[str, Any]] = []
    for record in manifest["layers"]:
        physical = np.asarray(
            Image.open(root / record["retained_material_mask"]).convert("L")
        ) > 0
        visible = np.asarray(
            Image.open(root / record["source_visible_region_mask"]).convert("L")
        ) > 0
        layers.append({"physical_mask": physical, "visible_aperture": visible})
    return _width_rows(layers, manifest["layers"], output, poc, optimization)


def _render_plan_comparison(before: Path, after: Path, output: Path) -> None:
    left = Image.open(before).convert("RGB")
    right = Image.open(after).convert("RGB")
    canvas = Image.new("RGB", (left.width + right.width, max(left.height, right.height) + 40), "white")
    canvas.paste(left, (0, 40))
    canvas.paste(right, (left.width, 40))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 12), "PREVIOUS", fill="black")
    draw.text((left.width + 8, 12), "WIDTH-AWARE", fill="black")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def run_width_aware_case(
    source: Path,
    case_id: str,
    baseline: Path,
    output: Path,
    poc: PocConfig = DEFAULT_CONFIG,
    optimization: OptimizationConfig = DEFAULT_OPTIMIZATION_CONFIG,
    search_config: WidthAwareSearchConfig = DEFAULT_WIDTH_AWARE_CONFIG,
    prefix_layers: int | None = None,
    window: tuple[int, int] | None = None,
) -> dict[str, Any]:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copytree(baseline, output / "previous")
    (output / "original").mkdir()
    shutil.copy2(source, output / "original" / "source.png")
    planned_root = output / "width_aware"
    for directory in ("automatic/layers", "previews", "reports"):
        (planned_root / directory).mkdir(parents=True, exist_ok=True)

    baseline_manifest = _load_json(baseline / "automatic" / "layer_stack.json")
    if baseline_manifest["source_sha256"] != _sha256(source):
        raise ValueError("baseline and source hashes differ")
    analysis = analyze_image(source, poc)
    if window is not None:
        record, graph, search = plan_width_aware_window(
            analysis["labels"],
            np.asarray(analysis["palette_rgb"], dtype=np.uint8),
            tuple(baseline_manifest["order_front_to_back_palette_indices"]),
            window[0],
            window[1],
            poc,
            optimization,
            search_config,
        )
    elif prefix_layers is None:
        record, graph, search = plan_width_aware(
            analysis["labels"],
            len(baseline_manifest["layers"]),
            poc,
            optimization,
            search_config,
        )
    else:
        record, graph, search = plan_width_aware_prefix(
            analysis["labels"],
            np.asarray(analysis["palette_rgb"], dtype=np.uint8),
            tuple(baseline_manifest["order_front_to_back_palette_indices"]),
            prefix_layers,
            poc,
            optimization,
            search_config,
        )
    planned = _materialize_plan(record, graph, analysis)
    order = discover_order(planned, poc)
    if order["status"] != "FEASIBLE":
        raise RuntimeError("width-aware provider plan failed independent order replay")
    layers = construct_layers(planned, order, poc)
    layer_files = _save_layer_artifacts(planned_root, layers, planned, poc)
    validation, reconstruction, mismatch = validate_plan(
        planned, order, layers, poc
    )
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    Image.fromarray(palette[analysis["labels"]], "RGB").save(
        planned_root / "previews" / "printable_target.png"
    )
    Image.fromarray(reconstruction, "RGB").save(
        planned_root / "previews" / "reconstructed_front.png"
    )
    _render_comparison(
        analysis,
        reconstruction,
        mismatch,
        planned_root / "previews" / "source_difference.png",
    )
    _render_exploded(
        layers, planned, planned_root / "previews" / "exploded_layer_masks.png"
    )
    width_rows, width_summary = _width_rows(
        layers,
        layer_files,
        output / "diagnostics" / "width_aware",
        poc,
        optimization,
    )
    previous_width_rows, previous_width_summary = _saved_width_rows(
        output / "previous",
        baseline_manifest,
        output / "diagnostics" / "previous",
        poc,
        optimization,
    )
    baseline_reconstruction = np.asarray(
        Image.open(output / "previous" / "previews" / "reconstructed_front.png").convert("RGB")
    )
    changed_from_previous = np.any(
        baseline_reconstruction != reconstruction, axis=2
    )
    changed_map = np.zeros((*changed_from_previous.shape, 3), dtype=np.uint8)
    changed_map[changed_from_previous] = (255, 0, 255)
    (output / "comparison").mkdir()
    Image.fromarray(changed_map, "RGB").save(
        output / "comparison" / "changed_pixels_previous_to_width_aware.png"
    )
    _render_plan_comparison(
        output / "previous" / "previews" / "reconstructed_front.png",
        planned_root / "previews" / "reconstructed_front.png",
        output / "comparison" / "previous_vs_width_aware_front.png",
    )
    _render_plan_comparison(
        output / "previous" / "previews" / "exploded_layer_masks.png",
        planned_root / "previews" / "exploded_layer_masks.png",
        output / "comparison" / "previous_vs_width_aware_exploded.png",
    )
    previous_by_depth = {
        row["depth_back_to_front"]: row for row in previous_width_rows
    }
    width_aware_by_depth = {
        row["depth_back_to_front"]: row for row in width_rows
    }
    per_layer_changes: list[dict[str, Any]] = []
    width_comparison_dir = output / "comparison" / "width_maps"
    for depth in sorted(previous_by_depth):
        previous_row = previous_by_depth[depth]
        new_row = width_aware_by_depth[depth]
        previous_width = previous_row["width"]
        new_width = new_row["width"]
        relative = f"depth_{depth:02d}_previous_vs_width_aware.png"
        _render_plan_comparison(
            output
            / "diagnostics"
            / "previous"
            / f"{previous_row['id']}_width.png",
            output
            / "diagnostics"
            / "width_aware"
            / f"{new_row['id']}_width.png",
            width_comparison_dir / relative,
        )
        per_layer_changes.append(
            {
                "depth_back_to_front": depth,
                "previous_layer_id": previous_row["id"],
                "width_aware_layer_id": new_row["id"],
                "previous_orphan_structural_core_components": previous_width[
                    "orphan_structural_core_components"
                ],
                "width_aware_orphan_structural_core_components": new_width[
                    "orphan_structural_core_components"
                ],
                "orphan_component_delta": new_width[
                    "orphan_structural_core_components"
                ]
                - previous_width["orphan_structural_core_components"],
                "previous_orphan_structural_core_pixels": previous_width[
                    "orphan_structural_core_pixels"
                ],
                "width_aware_orphan_structural_core_pixels": new_width[
                    "orphan_structural_core_pixels"
                ],
                "orphan_pixel_delta": new_width["orphan_structural_core_pixels"]
                - previous_width["orphan_structural_core_pixels"],
                "comparison_map": f"comparison/width_maps/{relative}",
            }
        )

    counts = Counter(record.sequence)
    repeated = [
        {
            "palette_index": color,
            "color": analysis["palette_hex"][color],
            "physical_layers": count,
            "front_to_back_depths": [
                depth for depth, value in enumerate(record.sequence) if value == color
            ],
        }
        for color, count in sorted(counts.items())
        if count > 1
    ]
    manifest = {
        "schema_version": "3.6.0-width-aware-2d-planner-poc",
        "case_id": case_id,
        "status": (
            "AUTOMATIC_WIDTH_VALID_UNCORRECTED"
            if width_summary["width_failed_layers"] == 0
            else "AUTOMATIC_UNRESOLVED_WIDTH_CANDIDATE"
        ),
        "source": "../../original/source.png",
        "source_sha256": _sha256(source),
        "construction": {
            "method": "bounded_actual_cumulative_mask_width_aware_search",
            "common_outer_pixels": [poc.outer_pixels, poc.outer_pixels],
            "continuous_perimeter_pixels": poc.perimeter_pixels,
            "one_color_per_layer": True,
            "visible_connector_fallback": False,
            "visible_pixels_edited": False,
            "cad_generated": False,
        },
        "order_front_to_back_palette_indices": list(record.sequence),
        "order_back_to_front": [item["id"] for item in layer_files],
        "repeated_colors": repeated,
        "layers": layer_files,
    }
    _write_json(planned_root / "automatic" / "layer_stack.json", manifest)
    _write_json(
        planned_root / "automatic" / "proposal.json",
        {
            "source_sha256": _sha256(source),
            "search_config": asdict(search_config),
            "width_config": _width_parameters(poc, optimization),
            "search": search,
            "providers": planned["providers"],
            "manual_annotations_used": False,
            "manual_corrections_applied": 0,
            "details_deleted_or_enlarged": False,
            "visible_bridge_fallback": False,
        },
    )
    validation.update(
        {
            "case_id": case_id,
            "review_state": "READY_FOR_USER_TEST",
            "width_gate_status": (
                "PASS"
                if width_summary["width_failed_layers"] == 0
                else "UNRESOLVED"
            ),
            "width_summary": width_summary,
            "width_layers": width_rows,
            "changed_pixels_from_previous_composite": int(
                np.count_nonzero(changed_from_previous)
            ),
            "repeated_color_layers": repeated,
            "cad_generated": False,
        }
    )
    _write_json(planned_root / "reports" / "validation.json", validation)

    comparison = {
        "case_id": case_id,
        "review_state": "READY_FOR_USER_TEST",
        "l1_approved": False,
        "scope": "width-aware 2D planning only; no CAD, STL, STEP, frontend, external frame, or printing",
        "previous": {
            "layer_count": len(baseline_manifest["layers"]),
            "order_front_to_back_palette_indices": baseline_manifest[
                "order_front_to_back_palette_indices"
            ],
            **previous_width_summary,
        },
        "width_aware": {
            "layer_count": len(layers),
            "order_front_to_back_palette_indices": list(record.sequence),
            "repeated_color_layers": repeated,
            **width_summary,
        },
        "fidelity": {
            "geometry_mismatched_pixels_against_indexed_target": validation[
                "geometry_mismatched_pixels"
            ],
            "changed_pixels_from_previous_composite": int(
                np.count_nonzero(changed_from_previous)
            ),
            "visible_connector_pixels": validation[
                "visible_automatic_connector_pixels"
            ],
            "details_deleted_or_enlarged": False,
        },
        "per_layer_width_changes": per_layer_changes,
        "search": search,
        "search_changes": {
            "actual_mask_geometry_during_planning": True,
            "actual_common_perimeter_in_every_candidate": True,
            "corrected_7x7_ellipse_during_planning": True,
            "same_color_source_regions_can_be_deferred": True,
            "window_entry_and_exit_context_preserved": bool(window),
            "objective": [
                "complete within the existing layer budget",
                "fewest width-failing candidate prefixes",
                "fewest orphan structural cores",
                "fewest orphan structural-core pixels",
                "least retained pixel-layers",
            ],
        },
        "acceptance": {
            "candidate_width_valid": width_summary["width_failed_layers"] == 0,
            "candidate_explicitly_unresolved": width_summary["width_failed_layers"] > 0,
            "global_infeasibility_claimed": False,
            "layer_count_increased": len(layers) > len(baseline_manifest["layers"]),
        },
        "decorative_limitations": {
            "thin_decorative_visible_components": width_summary[
                "thin_decorative_visible_components"
            ],
            "status": "REPORTED_SEPARATELY_NOT_AUTOMATICALLY_RESOLVED",
        },
        "limitations": {
            "full_fox_search_exhausted": False,
            "representative_full_sheet_window_used": window is not None,
            "remaining_structural_failures": width_summary[
                "orphan_structural_core_components"
            ],
            "claim": "This is a bounded improvement, not a proof that the remaining conflicts are globally infeasible.",
        },
    }
    _write_json(output / "reports" / "comparison.json", comparison)

    checksum_files = sorted(
        path for path in output.rglob("*") if path.is_file() and path.name != "checksums.sha256"
    )
    (output / "reports" / "checksums.sha256").write_text(
        "".join(
            f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_files
        ),
        encoding="utf-8",
    )
    return comparison

