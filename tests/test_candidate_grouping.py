from __future__ import annotations

from pathlib import Path

import pytest

from layerforge_l1.layer_optimization import (
    DEFAULT_OPTIMIZATION_CONFIG,
    _advance_neighbours,
    build_region_graph,
)
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG, analyze_image
from layerforge_l1.width_aware_planner import (
    _ActualMaskEvaluator,
    _candidate_groups,
    _candidate_groups_legacy,
)


FOX = Path("fox_simple_input.png")  # a local reference image, not part of the repository (removed 2026-11-13)


@pytest.mark.skipif(not FOX.is_file(), reason="fox_simple_input.png is a local image the user removed from the repo; "
                                              "put it in the repository root to run this check")
def test_fox_topology_cohort_adds_missing_valid_orange_pair() -> None:
    analysis = analyze_image(FOX)
    graph = build_region_graph(analysis["labels"], connectivity=4, minimum_contact_pixels=1)
    evaluator = _ActualMaskEvaluator(
        graph, DEFAULT_CONFIG, DEFAULT_OPTIMIZATION_CONFIG
    )
    state = 1 << 8
    neighbours = _advance_neighbours(
        int(graph["border_bits"]), state, graph["adjacency"]
    )
    group = (1 << 120) | (1 << 122)
    arguments = (
        state,
        neighbours,
        3,
        graph,
        evaluator,
        True,
        2,
        int(graph["full_bits"]),
    )

    legacy = _candidate_groups_legacy(*arguments)
    improved = _candidate_groups(*arguments)
    metrics = evaluator.metrics(state | group)

    assert analysis["palette_hex"][3] == "#F78524"
    assert group not in legacy
    assert group in improved
    assert metrics.four_neighbor_components == 1
    assert metrics.orphan_core_components == 0

