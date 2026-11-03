"""Line / Curve connectors: ``add`` of a ``stroke`` shape (core.editing).

Uses the bow-face stack and the hidden 16 x 16 mm island of ``test_l2_editor`` (a ring cut into the
back sheet, behind the front sheets), so connecting the island is a hidden change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point

from layerforge_l1 import core
from layerforge_l1.core import review
from layerforge_l1.core.editing import STROKE_SEGMENTS, EditError, apply_edit, islands, shape_geometry, stroke_centre_line
from layerforge_l1.core.model import MANIFEST_NAME

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_l2_editor import FACE_CENTRE_MM, HIDDEN_RING, project, stack, stack_dir  # noqa: E402,F401 - shared fixtures

VECTOR = {"precision": "vector"}
DIAGONAL = {"type": "stroke", "path": "line", "points_mm": [[52.0, 33.0], [33.0, 12.0]], "width_mm": 3.0, "cap": "round"}
S_CURVE = {"type": "stroke", "path": "cubic", "points_mm": [[50.0, 30.0], [62.0, 24.0], [38.0, 20.0], [50.0, 12.0]],
           "width_mm": 3.0, "cap": "round"}


def _island(stack):
    back = stack.sheets[0].id
    ringed, _ = apply_edit(stack, {"op": "subtract", "sheet": back, "shape": HIDDEN_RING, **VECTOR})
    assert len(islands(ringed, 0)) == 1
    return ringed, back


def test_stroke_geometry_is_the_buffered_centre_line() -> None:
    line = shape_geometry(DIAGONAL)
    length = LineString(DIAGONAL["points_mm"]).length
    assert line.area == pytest.approx(3.0 * length + 3.141592653589793 * 1.5**2, rel=2e-3)  # round ends
    flat = shape_geometry({**DIAGONAL, "cap": "flat"})
    assert flat.area == pytest.approx(3.0 * length, rel=1e-3) and flat.area < line.area
    curve = shape_geometry(S_CURVE)
    centre = stroke_centre_line(S_CURVE)
    assert len(centre) == STROKE_SEGMENTS + 1 and centre[0] == (50.0, 30.0) and centre[-1] == (50.0, 12.0)
    straight = LineString([S_CURVE["points_mm"][0], S_CURVE["points_mm"][-1]])
    assert curve.area > 3.0 * straight.length  # the bend makes the path longer than the chord
    assert curve.contains(Point(centre[16])) and not straight.buffer(1.5).contains(Point(centre[16]))
    assert shape_geometry(S_CURVE).equals(curve)  # deterministic: replay is exact


@pytest.mark.parametrize("bad, message", [
    ({**DIAGONAL, "width_mm": 0}, "width must be positive"),
    ({**DIAGONAL, "points_mm": [[1, 1]]}, "needs 2 points"),
    ({**S_CURVE, "points_mm": S_CURVE["points_mm"][:3]}, "needs 4 points"),
    ({**DIAGONAL, "path": "spiral"}, "path must be one of"),
    ({**DIAGONAL, "cap": "square"}, "cap must be one of"),
    ({**DIAGONAL, "points_mm": [[5, 5], [5, 5]]}, "two different end points"),
])
def test_invalid_strokes_are_rejected(bad, message) -> None:
    with pytest.raises(EditError, match=message):
        shape_geometry(bad)


def test_diagonal_line_and_curve_connect_an_island_as_hidden_material(stack) -> None:
    ringed, back = _island(stack)
    for shape in (DIAGONAL, S_CURVE):
        joined, change = apply_edit(ringed, {"op": "add", "sheet": back, "shape": shape, **VECTOR})
        assert islands(joined, 0) == [] and change["kind"] == "hidden_geometry" and change["visible_changed_px"] == 0
        validation = core.validate_stack(joined)
        assert validation["status"] == "PASS" and validation["sheets"][0]["vector_checks"]["pass"]
    thin, _ = apply_edit(ringed, {"op": "add", "sheet": back, "shape": {**DIAGONAL, "width_mm": 0.9}, **VECTOR})
    assert core.validate_stack(thin)["sheets"][0]["vector_checks"]["orphan_structural_cores"] == 1  # 1.2 mm rule holds


def test_a_visible_stroke_needs_the_artwork_to_be_accepted(stack) -> None:
    front = stack.sheets[-1].id
    x, y = FACE_CENTRE_MM
    over = {"type": "stroke", "path": "line", "points_mm": [[x - 6, y], [x + 6, y + 4]], "width_mm": 2.0}
    shown, change = apply_edit(stack, {"op": "add", "sheet": front, "shape": over, **VECTOR})
    assert change["kind"] == "visible_artwork" and change["visible_changed_px"] > 0
    assert core.validate_stack(shown)["status"] == "FAIL"  # never exported without the explicit approval
    accepted, _ = apply_edit(shown, {"op": "accept_artwork"})
    assert core.validate_stack(accepted)["reconstruction"]["mismatched_pixels"] == 0


def test_strokes_replay_undo_and_old_bridge_edits_stay_compatible(project, stack) -> None:
    back = stack.sheets[0].id
    review.apply_to_project(project, {"op": "subtract", "sheet": back, "shape": HIDDEN_RING})
    review.apply_to_project(project, {"op": "bridge", "sheet": back, "from_mm": [50, 30], "to_mm": [50, 15], "width_mm": 3.0})
    review.apply_to_project(project, {"op": "add", "sheet": back, "shape": DIAGONAL})
    edited, entry = review.apply_to_project(project, {"op": "add", "sheet": back, "shape": S_CURVE})
    assert entry["op"]["precision"] == "vector" and entry["validation_status"] == "PASS"
    assert [e["op"]["op"] for e in edited.review["edits"]] == ["subtract", "bridge", "add", "add"]
    assert review.verify_project(project)["replay_identical"]  # bridge and both strokes replay bit-exact
    undone, validation = review.undo(project)
    assert len(undone.review["edits"]) == 3 and validation["status"] == "PASS"
    assert review.verify_project(project)["replay_identical"]


def test_dry_run_reports_the_change_and_saves_nothing(project, stack) -> None:
    back = stack.sheets[0].id
    review.apply_to_project(project, {"op": "subtract", "sheet": back, "shape": HIDDEN_RING})
    before = (project / MANIFEST_NAME).read_bytes()
    result = review.check_edit(project, {"op": "add", "sheet": back, "shape": S_CURVE})
    assert result["change"]["kind"] == "hidden_geometry" and result["validation_status"] == "PASS"
    assert result["op"]["precision"] == "vector"
    assert (project / MANIFEST_NAME).read_bytes() == before and len(review.load_project(project).review["edits"]) == 1


def test_curved_connector_exports_to_valid_cad(stack, tmp_path) -> None:
    """The committed curve is real CAD: one valid STEP solid per sheet, watertight STL, front view kept."""

    from layerforge_l1.core.cad import export_stack, revalidate_cad

    ringed, back = _island(stack)
    joined, _ = apply_edit(ringed, {"op": "add", "sheet": back, "shape": S_CURVE, **VECTOR})
    assert core.validate_stack(joined)["status"] == "PASS"
    manifest = export_stack(joined, tmp_path / "cad")
    check, _ = revalidate_cad(joined, tmp_path / "cad")
    assert check["pass"] and check["stack_mismatch_px"] == 0
    assert all(r["step_valid"] and r["step_solids"] == 1 and r["stl_watertight"] and r["stl_components"] == 1
               for r in manifest["layers"])
