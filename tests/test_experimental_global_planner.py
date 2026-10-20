from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from layerforge_l1 import PROJECT_ROOT
from layerforge_l1.experimental_global_planner import (
    build_fixed_depth_model,
    exact_index,
    load_existing_indexed_target,
    load_lossless_target,
    proven_precedence_pairs,
)
from layerforge_l1.layer_optimization import build_region_graph


SOURCE = (
    PROJECT_ROOT / "fixtures" / "known_feasible_benchmark" / "source_composite_only.png"
)


def test_lossless_adapter_preserves_exact_four_color_benchmark() -> None:
    analysis = load_lossless_target(SOURCE)
    assert analysis["rgb"].shape == (512, 512, 3)
    assert len(analysis["palette_rgb"]) == 4
    assert analysis["palette_hex"] == ["#242939", "#ED852B", "#F2C84B", "#FAF7F0"]
    reconstructed = analysis["palette_rgb"][analysis["labels"]]
    assert np.array_equal(reconstructed, analysis["rgb"])


def test_region_topology_is_derived_only_from_indexed_composite() -> None:
    analysis = load_lossless_target(SOURCE)
    graph = build_region_graph(
        analysis["labels"], connectivity=4, minimum_contact_pixels=1
    )
    assert len(graph["nodes"]) == 19
    assert [int(bits).bit_count() for bits in graph["color_bits"]] == [9, 5, 3, 2]
    assert int(graph["border_bits"]).bit_count() == 1
    assert proven_precedence_pairs(graph)


def test_fixed_depth_model_jointly_represents_all_regions_and_layers() -> None:
    analysis = load_lossless_target(SOURCE)
    graph = build_region_graph(
        analysis["labels"], connectivity=4, minimum_contact_pixels=1
    )
    _, variables, description = build_fixed_depth_model(graph, fixed_layers=5)
    assert description["variables"]["region_depth_boolean"] == 19 * 5
    assert description["variables"]["layer_color_boolean"] == 5 * 4
    assert description["constraints"]["region_exactly_one_depth"] == 19
    assert description["constraints"]["layer_exactly_one_color"] == 5
    assert description["constraints"]["proven_precedence"] > 0
    assert len(variables["depth"]) == 19
    assert not description["reference_masks_or_order_used"]
    assert not description["candidate_group_generator_used"]
    assert not description["beam_search_used"]


def test_exact_index_creates_no_intermediate_colors() -> None:
    rgb = np.asarray(
        [[[1, 2, 3], [4, 5, 6]], [[1, 2, 3], [7, 8, 9]]], dtype=np.uint8
    )
    labels, palette = exact_index(rgb)
    assert len(palette) == 3
    assert np.array_equal(palette[labels], rgb)
    assert (
        cv2.connectedComponents(
            (labels == labels[0, 0]).astype(np.uint8), connectivity=4
        )[0]
        - 1
        == 1
    )


def test_experimental_module_does_not_depend_on_private_reference_masks() -> None:
    module_path = Path(
        PROJECT_ROOT / "src" / "layerforge_l1" / "experimental_global_planner.py"
    )
    source = module_path.read_text(encoding="utf-8")
    assert "private_reference_masks" not in source
    assert "_candidate_groups" not in source
    assert "width_aware_planner" not in source


def test_existing_indexer_adapter_discards_prior_layer_proposal() -> None:
    analysis = load_existing_indexed_target(SOURCE)
    assert "provider_labels" not in analysis
    assert "providers" not in analysis
    assert "provider_splits" not in analysis
    assert analysis["preprocessing_diagnostics"]["derived_greedy_provider_count"] > 0
