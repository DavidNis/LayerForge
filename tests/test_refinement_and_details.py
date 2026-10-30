"""Local cell refinement after an infeasible closure, and intake detail reporting/decisions.

All inputs are drawn here (project-owned, no user artwork):

* ``_eyes``: two small black eyes, each with a 2 px brow above a 1 px skin gap, placed so the
  brow, the gap and the eye share one 3.9 mm planner tile.  A coarse cell then holds wide skin
  (which must lie in front of the eye) and the sub-width gap (which must lie behind the brow),
  so the coarse closure deadlocks although the drawing is physically printable.
* ``_face``: a flat-color face with 4 px white eye highlights (below the speckle-merge area) and
  red lips whose color the palette (max 4 colors) does not keep.
"""

from __future__ import annotations

import numpy as np
import pytest

from layerforge_l1.core.intake import IntakeConfig, prepare_target
from layerforge_l1.core.model import StackError, StackGrid
from layerforge_l1.core.planning import PlanningConfig, PlanningError, plan_stack
from layerforge_l1.core.validation import validate_stack
from layerforge_l1.experimental_global_planner import exact_index
from layerforge_l1.reveal_planner import RevealPlanner, refine_cells

BLUE, SKIN, BLACK, WHITE, RED = (96, 174, 229), (252, 183, 127), (22, 16, 13), (250, 243, 228), (166, 42, 39)


def _eyes() -> np.ndarray:
    img = np.full((512, 512, 3), BLUE, np.uint8)
    img[120:400, 120:400] = SKIN
    for c0 in (242, 302):
        img[241:243, c0:c0 + 10] = BLACK          # brow, 2 px (0.5 mm)
        img[244:248, c0 + 1:c0 + 9] = BLACK       # eye below a 1 px skin gap
    return img


def _face() -> np.ndarray:
    import cv2

    img = np.full((1024, 1024, 3), BLUE, np.uint8)
    cv2.rectangle(img, (0, 760), (1023, 1023), WHITE, -1)                       # white shirt (keeps white in the palette)
    cv2.ellipse(img, (512, 300), (330, 260), 0, 0, 360, BLACK, -1, cv2.LINE_AA)  # hair (keeps black in the palette)
    cv2.ellipse(img, (512, 420), (260, 320), 0, 0, 360, SKIN, -1, cv2.LINE_AA)
    for cx in (420, 604):
        cv2.ellipse(img, (cx, 360), (40, 18), 0, 0, 360, BLACK, -1, cv2.LINE_AA)
        img[352:356, cx + 12:cx + 16] = WHITE                                     # 4 x 4 px highlight (0.27 mm2)
    cv2.ellipse(img, (512, 560), (70, 16), 0, 0, 360, RED, -1, cv2.LINE_AA)     # lips
    return img


# -- local refinement --------------------------------------------------------------------------
def test_refine_cells_changes_only_the_targets() -> None:
    labels, _ = exact_index(_eyes())
    planner = RevealPlanner(labels, PlanningConfig(outer_sheet_mm=150.0, scale=1).reveal_config(None))
    targets = np.zeros(planner.cell_count, bool)
    targets[[0, 5]] = True
    cells, regions = refine_cells(planner.native_cells, planner.cell_region, targets, tile=4, min_pixels=3)
    untouched = ~np.isin(planner.native_cells, [0, 5])
    # Every other cell keeps exactly its pixels (relabelled only by compaction).
    for old in np.unique(planner.native_cells[untouched])[:50]:
        assert len(np.unique(cells[planner.native_cells == old])) == 1
    # Refined pieces never cross their parent cell and keep its source region.
    for old in (0, 5):
        pieces = np.unique(cells[planner.native_cells == old])
        assert len(pieces) > 1 and all(regions[p] == planner.cell_region[old] for p in pieces)
        assert not np.isin(cells[untouched], pieces).any()


def test_deadlocked_coarse_cells_are_refined_locally_and_validate() -> None:
    labels, _ = exact_index(_eyes())
    planner = RevealPlanner(labels, PlanningConfig(outer_sheet_mm=150.0, scale=1).reveal_config(None))
    coarse = planner.closure()
    assert coarse["feasible"] is False and coarse["obstruction_cells"]
    refined, closure, log = planner.closure_with_refinement()
    assert closure["feasible"] is True
    assert log[0]["neighbor_rings"] == 0 and log[0]["cells_refined"] == len(coarse["obstruction_cells"])
    assert refined.cell_count - planner.cell_count < 100  # local: only the blocked cells were cut
    # The unchanged validators accept the result: no bridge, exact front view.
    stack, report = plan_stack(_eyes(), PlanningConfig(outer_sheet_mm=150.0, scale=1))
    assert report["cell_refinement"][0]["blocked_cells_before"] == len(coarse["obstruction_cells"])
    validation = validate_stack(stack)
    assert validation["status"] == "PASS" and validation["reconstruction"]["mismatched_pixels"] == 0


def test_feasible_images_keep_the_original_path() -> None:
    img = _eyes()
    img[241:243] = SKIN  # no brows: no deadlock
    labels, _ = exact_index(img)
    planner = RevealPlanner(labels, PlanningConfig(outer_sheet_mm=150.0, scale=1).reveal_config(None))
    same, closure, log = planner.closure_with_refinement(extra_rings=-1)
    assert closure["feasible"] and log == [] and same is planner
    _, report = plan_stack(img, PlanningConfig(outer_sheet_mm=150.0, scale=1))
    assert "cell_refinement" not in report


def test_wider_refinement_is_an_option_not_a_default() -> None:
    _, minimal = plan_stack(_eyes(), PlanningConfig(outer_sheet_mm=150.0, scale=1))
    _, wide = plan_stack(_eyes(), PlanningConfig(outer_sheet_mm=150.0, scale=1, refinement_extra_rings=-1))
    assert not any(r.get("widened") for r in minimal["cell_refinement"])
    widened = wide["cell_refinement"][-1]
    assert widened["widened"] and widened["used"] and widened["neighbor_rings"] == "all"
    assert wide["cells_after_refinement"] > minimal["cells_after_refinement"]


def test_refinement_is_bounded_and_reported_when_it_cannot_help(monkeypatch) -> None:
    import layerforge_l1.reveal_planner as rp

    # With refinement disabled the deadlock stays and is reported with its obstruction map.
    monkeypatch.setattr(rp, "REFINEMENT_ROUNDS", ())
    with pytest.raises(PlanningError) as caught:
        plan_stack(_eyes(), PlanningConfig(outer_sheet_mm=150.0, scale=1))
    assert caught.value.report["closure_feasible"] is False and caught.value.obstruction.any()
    # Rounds never exceed the configured bound.
    monkeypatch.setattr(rp, "REFINEMENT_ROUNDS", ((3.9, 0.9, 0),))  # a "refinement" that changes nothing
    labels, _ = exact_index(_eyes())
    planner = RevealPlanner(labels, PlanningConfig(outer_sheet_mm=150.0, scale=1).reveal_config(None))
    _, closure, log = planner.closure_with_refinement()
    assert len(log) == 1 and closure["feasible"] is False


def test_one_pixel_tails_are_traced_exactly() -> None:
    from types import SimpleNamespace

    from layerforge_l1.core.cad import mask_to_geometry, rasterize_geometry

    mask = np.zeros((96, 96), bool)
    mask[20:60, 20:60] = True
    mask[30, 60:70] = True      # horizontal 1 px tail (a zero-width spike for centre-line contours)
    mask[60:68, 40] = True      # vertical 1 px tail
    mask[61, 41] = True         # with a side pixel
    geometry = mask_to_geometry(mask, 0.15, 0.03)
    raster = rasterize_geometry(geometry, SimpleNamespace(outer_pixels=96, mm_per_pixel=0.15))
    assert np.array_equal(raster, mask)
    assert geometry.is_valid and geometry.geom_type == "Polygon"


# -- intake details ----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def face_default():
    return prepare_target(_face(), None, StackGrid.for_planner(150.0, 2), IntakeConfig(max_colors=4))


def _by_kind(prepared, kind):
    return [d for d in prepared["details"] if d["kind"] == kind]


def test_removed_and_recolored_details_are_reported_with_comparisons(face_default) -> None:
    p = face_default
    removed, recolored = _by_kind(p, "removed_by_cleanup"), _by_kind(p, "color_not_in_palette")
    assert len(removed) == 2 and len(recolored) == 1 and len(p["details"]) == 3  # no anti-aliasing reported
    lips = recolored[0]
    assert np.linalg.norm(np.array([int(lips["original_hex"][i:i + 2], 16) for i in (1, 3, 5)]) - RED) < 40
    assert lips["action"] == {"action": "adjust", "color": lips["processed_hex"], "default": True}
    for highlight in removed:
        assert highlight["original_hex"] == highlight["result_hex"] != highlight["processed_hex"]
        assert highlight["action"] == {"action": "preserve", "default": True}  # never dropped for being small
        assert highlight["size_notes"]  # size noted, not used to reject
    for d in p["details"]:
        crop = p["detail_crops"][d["id"]]
        assert crop.ndim == 3 and crop.shape[1] > 3 * crop.shape[0] / 2  # original | default | planned
    assert p["detail_overview"].shape == p["target_rgb"].shape
    # Preserved highlights are in the planner target.
    r0, c0, r1, c1 = removed[0]["bbox_px"]
    assert (p["target_rgb"][r0:r1, c0:c1] == np.array(WHITE)).all(-1).any() or \
        any((p["target_rgb"][r0:r1, c0:c1] == np.array(c)).all(-1).any() for c in p["palette"])


def test_decisions_preserve_remove_adjust(face_default) -> None:
    lips = _by_kind(face_default, "color_not_in_palette")[0]
    a, b = _by_kind(face_default, "removed_by_cleanup")
    skin = "#%02X%02X%02X" % tuple(face_default["palette"][2])
    decided = prepare_target(_face(), None, StackGrid.for_planner(150.0, 2), IntakeConfig(max_colors=4), {
        lips["id"]: {"action": "preserve"},
        a["id"]: {"action": "remove"},
        b["id"]: {"action": "adjust", "color": skin},
    })
    rows = {d["id"]: d for d in decided["details"]}
    assert rows[lips["id"]]["result_hex"] == lips["original_hex"]
    assert decided["measurements"]["palette_added_by_decisions"] == [lips["original_hex"]]
    assert len(decided["palette"]) == len(face_default["palette"]) + 1
    assert rows[a["id"]]["result_hex"] == a["processed_hex"] and rows[a["id"]]["action"]["default"] is False
    assert rows[b["id"]]["result_hex"] == skin
    lr0, lc0, lr1, lc1 = lips["bbox_px"]
    red = np.array([int(lips["original_hex"][i:i + 2], 16) for i in (1, 3, 5)])
    lips_px = lips["area_mm2"] / decided["measurements"]["artwork_mm_per_pixel"] ** 2
    assert (decided["target_rgb"][lr0:lr1, lc0:lc1] == red).all(-1).sum() >= 0.9 * lips_px
    with pytest.raises(StackError, match="unknown intake details"):
        prepare_target(_face(), None, StackGrid.for_planner(150.0, 2), IntakeConfig(max_colors=4), {"color_0000_0000": {"action": "remove"}})
    with pytest.raises(StackError, match="action must be"):
        prepare_target(_face(), None, StackGrid.for_planner(150.0, 2), IntakeConfig(max_colors=4), {lips["id"]: {"action": "drop"}})
