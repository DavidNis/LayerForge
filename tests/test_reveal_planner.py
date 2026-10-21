"""Reveal-closure planner on the bow-and-face fixture from the architecture note.

One black region (outline plus a 1.17 mm divider joining it) cannot sit at a
single depth: in front of the red lobes the divider is a sub-width bar; behind
them the lobes become islands.  Per-cell depth must solve it exactly.
"""

from __future__ import annotations

import cv2
import numpy as np

from layerforge_l1.connectivity_repair import audit_connection_width, audit_edge_connectivity
from layerforge_l1.layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from layerforge_l1.reveal_planner import RevealConfig, RevealPlanner, embed

SKY, BLACK, YELLOW, RED, WHITE = 0, 1, 2, 3, 4


def _fixture() -> np.ndarray:
    labels = np.full((512, 512), SKY, dtype=np.uint8)
    cv2.circle(labels, (256, 300), 130, BLACK, -1)
    cv2.circle(labels, (256, 300), 124, YELLOW, -1)
    # Bow above the face: black outline, two red lobes, 3 px (1.17 mm) divider.
    cv2.rectangle(labels, (196, 100), (316, 156), BLACK, -1)
    cv2.rectangle(labels, (202, 106), (310, 150), RED, -1)
    labels[106:151, 255:258] = BLACK
    for x in (210, 302):
        cv2.circle(labels, (x, 280), 16, BLACK, -1)
        cv2.circle(labels, (x + 5, 275), 4, WHITE, -1)
    cv2.ellipse(labels, (256, 340), (50, 20), 0, 20, 160, BLACK, 4)
    return labels


def _config(**overrides) -> RevealConfig:
    return RevealConfig(scale=2, **overrides)


def _composite(planner: RevealPlanner, sheets: list[dict], colors: list[int]) -> np.ndarray:
    shown = np.full(planner.cell_map.shape, -1, dtype=np.int16)
    for sheet, color in zip(sheets, colors):
        shown[(shown < 0) & sheet["physical_mask"]] = color
    return shown


def test_region_indivisible_fixture_is_stuck() -> None:
    planner = RevealPlanner(_fixture(), _config(split_regions=False))
    result = planner.closure()
    assert not result["feasible"]
    assert result["obstruction_cells"]


def test_per_cell_depth_builds_valid_exact_six_sheet_stack() -> None:
    labels = _fixture()
    planner = RevealPlanner(labels, _config())
    assert planner.closure()["feasible"]
    plan = planner.beam_search()
    assert plan["status"] == "COMPLETE"
    assert len(plan["front_to_back_colors"]) == 6
    assert plan["front_to_back_colors"].count(BLACK) == 2

    sheets = planner.materialize(plan["reveals"])
    p = planner.grid.perimeter_pixels
    for sheet in sheets:
        edge, _ = audit_edge_connectivity(sheet["physical_mask"])
        width, _ = audit_connection_width(
            sheet["physical_mask"],
            sheet["visible_mask"][p:-p, p:-p],
            planner.grid,
            DEFAULT_OPTIMIZATION_CONFIG,
        )
        assert edge["four_neighbor_status"] == "PASS"
        assert width["status"] == "PASS"
        assert np.all(sheet["physical_mask"][planner.perimeter])

    composite = _composite(planner, sheets, plan["front_to_back_colors"])
    target = embed(labels.astype(np.int16), planner.grid, -1)
    inside = ~planner.perimeter
    assert np.array_equal(composite[inside], target[inside])
    assert planner.seam_report(plan["reveals"])["source_regions_split_across_sheets"] >= 1
