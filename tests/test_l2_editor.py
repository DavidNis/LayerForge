"""L2 reviewed layer editor on project-owned inputs.

Unit tests edit the bow-face stack of ``test_l0_core`` (scale 1: 576 px grid,
225 mm sheet, 12.5 mm shared perimeter).  The integration test edits a stack
proposed by L1 from the anti-aliased JPEG of ``test_l1_propose``, saves and
reloads it, replays its log, and exports and reopens STEP/STL.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from layerforge_l1 import core
from layerforge_l1.core import editing, review
from layerforge_l1.core.editing import EditError, apply_edit, islands, visible_masks
from layerforge_l1.core.model import MANIFEST_NAME, load_stack, save_stack
from layerforge_l1.core.validation import cutout_hole_count

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_l0_core import CONFIG, PALETTE  # noqa: E402
from test_l1_propose import FAST, _render  # noqa: E402
from test_reveal_planner import _fixture  # noqa: E402

HIDDEN_CIRCLE = {"type": "circle", "center_mm": [30.0, 30.0], "radius_mm": 3.0}  # behind the front sky sheet
HIDDEN_RING = {"type": "rounded_rectangle_ring", "bounds_mm": [40.0, 20.0, 60.0, 40.0], "radius_mm": 0.0, "width_mm": 2.0}
FACE_CENTRE_MM = [112.7, 95.1]
LEFT_BOW_LOBE_MM = [95.0, 162.0]


@pytest.fixture(scope="module")
def stack() -> core.SheetStack:
    return core.plan_stack(PALETTE[_fixture()], CONFIG, name="bow_face")[0]


@pytest.fixture(scope="module")
def stack_dir(stack, tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("base") / "stack"
    save_stack(stack, path)
    return path


@pytest.fixture
def project(stack_dir, tmp_path) -> Path:
    review.start_review(stack_dir, tmp_path / "project")
    return tmp_path / "project"


def _ids(stack: core.SheetStack) -> list[str]:
    return [s.id for s in stack.sheets]


def _back(stack: core.SheetStack) -> str:
    return stack.sheets[0].id


# -- model and invariants ------------------------------------------------------------
def test_visible_rule_reproduces_planner_masks_and_plain_stacks_stay_1_0_0(stack, stack_dir) -> None:
    for sheet, derived in zip(stack.sheets, visible_masks(stack)):
        assert np.array_equal(sheet.visible, derived)
    assert json.loads((stack_dir / MANIFEST_NAME).read_text(encoding="utf-8"))["schema_version"] == "1.0.0"


def test_edits_never_modify_their_input(stack) -> None:
    before = review.geometry_fingerprint(stack)
    apply_edit(stack, {"op": "subtract", "sheet": _back(stack), "shape": HIDDEN_CIRCLE})
    apply_edit(stack, {"op": "move", "sheet": _ids(stack)[-1], "to": 0})
    assert review.geometry_fingerprint(stack) == before


# -- operations -----------------------------------------------------------------------
def test_reorder_and_move(stack) -> None:
    front = _ids(stack)[-1]
    moved, change = apply_edit(stack, {"op": "move", "sheet": front, "to": 0})
    assert _ids(moved)[0] == front and change["order_changed"]
    assert change["kind"] == "visible_artwork" and change["visible_changed_px"] > 0
    assert core.validate_stack(moved)["status"] == "FAIL"
    restored, change = apply_edit(moved, {"op": "reorder", "order": _ids(stack)})
    assert review.geometry_fingerprint(restored) == review.geometry_fingerprint(stack)
    with pytest.raises(EditError, match="every sheet id"):
        apply_edit(stack, {"op": "reorder", "order": _ids(stack)[1:]})


def test_recolor_is_a_visible_change_until_the_artwork_is_accepted(stack) -> None:
    red = next(s.id for s in stack.sheets if s.color == "#C83232")
    edited, change = apply_edit(stack, {"op": "recolor", "sheet": red, "color": "#b02020"})
    assert edited.sheets[_ids(stack).index(red)].color == "#B02020"
    assert change["kind"] == "visible_artwork" and change["visible_changed_px"] > 0
    validation = core.validate_stack(edited)
    assert validation["status"] == "FAIL"
    assert validation["reconstruction"]["mismatched_pixels"] == change["visible_changed_px"]
    accepted, change = apply_edit(edited, {"op": "accept_artwork"})
    assert change["kind"] == "artwork_reference" and change["visible_changed_px"] == 0
    assert core.validate_stack(accepted)["status"] == "PASS"
    assert stack.target_rgb is not None and not np.array_equal(accepted.target_rgb, stack.target_rgb)


def test_thickness_and_rim_are_physical_only_and_set_assembly_z(stack) -> None:
    back = _back(stack)
    thick, change = apply_edit(stack, {"op": "thickness", "sheet": "*", "mm": 2.0})
    assert change["kind"] == "physical_parameters" and thick.assembly_z_mm()[1] == pytest.approx(2.0)
    rimmed, change = apply_edit(stack, {"op": "rim", "sheet": back, "width_mm": 5, "total_height_mm": 3.0})
    assert change["kind"] == "physical_parameters" and change["visible_changed_px"] == 0
    assert rimmed.sheets[0].rim == {"width_mm": 5.0, "total_height_mm": 3.0}
    assert rimmed.assembly_z_mm()[1] == pytest.approx(3.0)  # the next sheet rests on the rim
    assert core.validate_stack(rimmed)["status"] == "PASS"
    removed, _ = apply_edit(rimmed, {"op": "rim", "sheet": back, "remove": True})
    assert removed.sheets[0].rim is None
    with pytest.raises(EditError, match="shared perimeter band"):
        apply_edit(stack, {"op": "rim", "sheet": back, "width_mm": 20, "total_height_mm": 3.0})
    with pytest.raises(EditError, match="exceed the sheet thickness"):
        apply_edit(rimmed, {"op": "thickness", "sheet": back, "mm": 3.5})
    with pytest.raises(EditError, match="positive"):
        apply_edit(stack, {"op": "thickness", "sheet": back, "mm": 0})


def test_hidden_cutout_is_added_and_filled_without_touching_the_artwork(stack) -> None:
    back = _back(stack)
    holes = cutout_hole_count(stack.sheets[0].retained)
    cut, change = apply_edit(stack, {"op": "subtract", "sheet": back, "shape": HIDDEN_CIRCLE})
    assert change["kind"] == "hidden_geometry" and change["visible_changed_px"] == 0
    assert change["retained_changed_px"][back] > 0
    assert cutout_hole_count(cut.sheets[0].retained) == holes + 1
    assert core.validate_stack(cut)["status"] == "PASS"
    filled, change = apply_edit(cut, {"op": "fill_hole", "sheet": back, "at_mm": HIDDEN_CIRCLE["center_mm"]})
    assert np.array_equal(filled.sheets[0].retained, stack.sheets[0].retained)
    added, change = apply_edit(cut, {"op": "add", "sheet": back, "shape": HIDDEN_CIRCLE})
    assert np.array_equal(added.sheets[0].retained, stack.sheets[0].retained)


def test_visible_cutout_change_is_reported_as_artwork_change(stack) -> None:
    front = _ids(stack)[-1]
    filled, change = apply_edit(stack, {"op": "fill_hole", "sheet": front, "at_mm": FACE_CENTRE_MM})
    assert change["kind"] == "visible_artwork" and change["visible_changed_px"] > 10000
    assert core.validate_stack(filled)["status"] == "FAIL"


def test_floating_island_is_detected_then_bridged_or_removed(stack) -> None:
    back = _back(stack)
    ringed, change = apply_edit(stack, {"op": "subtract", "sheet": back, "shape": HIDDEN_RING})
    found = islands(ringed, 0)
    assert len(found) == 1 and 200 < found[0]["area_mm2"] < 300  # 16 x 16 mm square
    validation = core.validate_stack(ringed)
    assert validation["status"] == "FAIL" and validation["sheets"][0]["four_neighbor"] == "FAIL"
    bridged, change = apply_edit(ringed, {"op": "bridge", "sheet": back, "from_mm": [50, 30], "to_mm": [50, 15],
                                          "width_mm": 3.0})
    assert islands(bridged, 0) == [] and change["kind"] == "hidden_geometry"
    assert core.validate_stack(bridged)["status"] == "PASS"
    removed, change = apply_edit(ringed, {"op": "remove_island", "sheet": back, "at_mm": found[0]["point_on_island_mm"]})
    assert islands(removed, 0) == [] and core.validate_stack(removed)["status"] == "PASS"
    with pytest.raises(EditError, match="not an island"):
        apply_edit(stack, {"op": "remove_island", "sheet": back, "at_mm": [30, 30]})


def test_split_then_merge_restores_the_sheet(stack) -> None:
    red = next(s.id for s in stack.sheets if s.color == "#C83232")
    split, change = apply_edit(stack, {"op": "split", "sheet": red, "region": {"region_at_mm": LEFT_BOW_LOBE_MM}})
    new = f"{red}_split1"
    assert _ids(split).index(new) == _ids(split).index(red) + 1
    assert change["kind"] == "hidden_geometry" and change["visible_changed_px"] == 0 and change["sheets_added"] == [new]
    # The lobe is not connected to the perimeter on its own sheet: reported, not repaired.
    assert islands(split, _ids(split).index(new)) and core.validate_stack(split)["status"] == "FAIL"
    merged, change = apply_edit(split, {"op": "merge", "sheet": red, "other": new})
    assert review.geometry_fingerprint(merged) == review.geometry_fingerprint(stack)
    assert change["sheets_removed"] == [new]


def test_cut_into_the_shared_perimeter_is_reported_not_corrected(stack) -> None:
    back = _back(stack)
    cut, change = apply_edit(stack, {"op": "subtract", "sheet": back,
                                     "shape": {"type": "circle", "center_mm": [2.0, 100.0], "radius_mm": 3.0}})
    assert not cut.sheets[0].retained[stack.grid.perimeter_mask()].all()
    validation = core.validate_stack(cut)
    assert validation["status"] == "FAIL" and not validation["sheets"][0]["shared_perimeter_complete"]


def test_invalid_requests_are_rejected(stack) -> None:
    back = _back(stack)
    cases = [
        ({"op": "recolor", "sheet": "nope", "color": "#000000"}, "no sheet"),
        ({"op": "explode"}, "unknown edit op"),
        ({"op": "recolor", "sheet": back}, "missing parameter"),
        ({"op": "recolor", "sheet": back, "color": "red"}, "color must be"),
        ({"op": "fill_hole", "sheet": back, "at_mm": [500, 10]}, "outside"),
        ({"op": "fill_hole", "sheet": back, "at_mm": [30, 30]}, "no cutout"),
        ({"op": "merge", "sheet": back, "other": back}, "two different"),
        ({"op": "split", "sheet": back, "region": {"shape": {"type": "circle", "center_mm": [2, 2], "radius_mm": 1}}},
         "selects no artwork"),
        ({"op": "move", "sheet": back, "to": 99}, "index"),
    ]
    for op, message in cases:
        with pytest.raises(EditError, match=message):
            apply_edit(stack, op)


# -- hybrid vector editing ---------------------------------------------------------------
VECTOR = {"precision": "vector"}


def test_vector_cut_keeps_exact_geometry_and_derives_the_raster(stack) -> None:
    back = _back(stack)
    edited, change = apply_edit(stack, {"op": "subtract", "sheet": back, "shape": HIDDEN_CIRCLE, **VECTOR})
    sheet = edited.sheets[0]
    assert sheet.geometry is not None and all(s.geometry is None for s in edited.sheets[1:])
    traced = editing.sheet_vector(stack, stack.sheets[0])
    circle = editing.shape_geometry(HIDDEN_CIRCLE)
    assert sheet.geometry.area == pytest.approx(traced.area - circle.area, abs=1e-6)  # exact, not pixel-counted
    assert np.array_equal(sheet.retained, core.rasterize_geometry(sheet.geometry, stack.grid))
    assert change["kind"] == "hidden_geometry" and change["exact_geometry_changed_mm2"][back] == pytest.approx(circle.area)
    validation = core.validate_stack(edited)
    assert validation["status"] == "PASS" and validation["sheets"][0]["vector_checks"]["pass"]
    assert validation["sheets"][0]["vector_checks"]["holes"] == cutout_hole_count(stack.sheets[0].retained) + 1
    raster, _ = apply_edit(stack, {"op": "subtract", "sheet": back, "shape": HIDDEN_CIRCLE})  # legacy key-less op
    # Raster subtraction removes pixels the circle covers >= half; the vector raster keeps
    # pixels with >= half material: only exact half-covered boundary pixels can differ.
    differ = raster.sheets[0].retained != sheet.retained
    n = stack.grid.outer_pixels
    supersampled = np.zeros((n * 4, n * 4), np.uint8)
    cv2.fillPoly(supersampled, [core.cad._to_subpixels(circle.exterior.coords, stack.grid)], 1, shift=4)
    coverage = supersampled.reshape(n, 4, n, 4).sum((1, 3))
    assert raster.sheets[0].geometry is None and (coverage[differ] == 8).all()


def test_subgrid_precision_survives_to_cad_and_is_reported(stack, tmp_path) -> None:
    back, front = _back(stack), _ids(stack)[-1]
    pin = {"type": "circle", "center_mm": [30.0, 30.0], "radius_mm": 0.08}  # far below the 0.39 mm grid
    hidden, change = apply_edit(stack, {"op": "subtract", "sheet": back, "shape": pin, **VECTOR})
    assert change["retained_changed_px"] == {} and change["visible_changed_px"] == 0
    assert change["kind"] == "hidden_geometry" and change["exact_geometry_changed_mm2"][back] > 0
    # The same pin in the front sheet shows the sheet behind it: reported although the grid cannot show it.
    shown, change = apply_edit(stack, {"op": "subtract", "sheet": front, "shape": pin, **VECTOR})
    assert change["kind"] == "visible_subgrid_geometry" and change["visible_changed_px"] == 0
    assert change["exact_visible_change_mm2"][front] == pytest.approx(editing.shape_geometry(pin).area, abs=1e-6)
    assert core.validate_stack(shown)["reconstruction"]["mismatched_pixels"] == 0
    # CAD is built from the exact geometry: the reopened STL keeps the 0.16 mm hole.
    core.export_stack(hidden, tmp_path)
    check, _ = core.revalidate_cad(hidden, tmp_path)
    row = check["layers"][0]
    assert check["pass"] and row["vector_checks_on_stl"]["holes"] == cutout_hole_count(stack.sheets[0].retained) + 1
    assert row["stl_mean_boundary_deviation_mm"] < 1e-4


def test_exact_width_check_rejects_a_thin_vector_bridge(stack) -> None:
    back = _back(stack)
    ringed, _ = apply_edit(stack, {"op": "subtract", "sheet": back, "shape": HIDDEN_RING, **VECTOR})
    thin, _ = apply_edit(ringed, {"op": "bridge", "sheet": back, "from_mm": [50, 30], "to_mm": [50, 15],
                                  "width_mm": 0.9, **VECTOR})
    exact = core.validate_stack(thin)["sheets"][0]["vector_checks"]
    assert exact["connected_parts"] == 1 and exact["orphan_structural_cores"] == 1 and not exact["pass"]
    wide, _ = apply_edit(ringed, {"op": "bridge", "sheet": back, "from_mm": [50, 30], "to_mm": [50, 15],
                                  "width_mm": 3.0, **VECTOR})
    assert core.validate_stack(wide)["status"] == "PASS"


def test_vector_fill_remove_split_merge(stack) -> None:
    back = _back(stack)
    cut, _ = apply_edit(stack, {"op": "subtract", "sheet": back, "shape": HIDDEN_CIRCLE, **VECTOR})
    filled, _ = apply_edit(cut, {"op": "fill_hole", "sheet": back, "at_mm": HIDDEN_CIRCLE["center_mm"]})
    traced = editing.sheet_vector(stack, stack.sheets[0])
    assert filled.sheets[0].geometry.symmetric_difference(traced).area < 1e-6
    ringed, _ = apply_edit(stack, {"op": "subtract", "sheet": back, "shape": HIDDEN_RING, **VECTOR})
    found = islands(ringed, 0)
    assert len(found) == 1 and found[0]["area_mm2"] == pytest.approx(16 * 16, abs=1e-6)  # exact, not pixels
    removed, _ = apply_edit(ringed, {"op": "remove_island", "sheet": back, "at_mm": found[0]["point_on_island_mm"]})
    assert islands(removed, 0) == [] and core.validate_stack(removed)["status"] == "PASS"
    lobe = {"type": "circle", "center_mm": [30.0, 30.0], "radius_mm": 10.0}
    split, change = apply_edit(cut, {"op": "split", "sheet": back, "region": {"shape": lobe}, **VECTOR})
    new = f"{back}_split1"
    assert change["visible_changed_px"] == 0 and split.sheets[1].id == new and split.sheets[1].geometry is not None
    merged, _ = apply_edit(split, {"op": "merge", "sheet": back, "other": new})
    assert merged.sheets[0].geometry.symmetric_difference(cut.sheets[0].geometry).area < 1e-6


def test_rim_is_optional_and_explicit_per_sheet(stack) -> None:
    back = _back(stack)
    assert all(s.rim is None for s in stack.sheets)  # never added automatically
    rimmed, _ = apply_edit(stack, {"op": "rim", "sheet": back, "enabled": True, "width_mm": 5, "total_height_mm": 3})
    taller, _ = apply_edit(rimmed, {"op": "rim", "sheet": back, "total_height_mm": 4})
    assert taller.sheets[0].rim == {"width_mm": 5.0, "total_height_mm": 4.0} and taller.sheets[1].rim is None
    off, _ = apply_edit(taller, {"op": "rim", "sheet": back, "enabled": False})
    assert off.sheets[0].rim is None and off.assembly_z_mm() == stack.assembly_z_mm()
    with pytest.raises(EditError, match="no rim yet"):
        apply_edit(stack, {"op": "rim", "sheet": back, "total_height_mm": 4})


def test_export_names_follow_order_and_color_with_stable_ids(stack, tmp_path) -> None:
    assert stack.export_names() == _ids(stack)  # planner ids already encode order and color
    front = _ids(stack)[-1]
    edited, _ = apply_edit(stack, {"op": "move", "sheet": front, "to": 0})
    edited, _ = apply_edit(edited, {"op": "recolor", "sheet": front, "color": "#123456"})
    assert edited.export_names()[0] == "layer_00_123456" and edited.sheets[0].id == front
    path = tmp_path / "stack"
    save_stack(edited, path)
    rows = json.loads((path / MANIFEST_NAME).read_text(encoding="utf-8"))["sheets"]
    assert [(r["id"], r["export_name"]) for r in rows] == list(zip(_ids(edited), edited.export_names()))
    report = core.generate_previews(edited, tmp_path / "preview")
    assert "sheets/layer_00_123456.png" in report["files"]


def test_vector_geometry_round_trip_and_tamper_detection(stack, tmp_path) -> None:
    edited, _ = apply_edit(stack, {"op": "subtract", "sheet": _back(stack), "shape": HIDDEN_CIRCLE, **VECTOR})
    path = tmp_path / "stack"
    save_stack(edited, path)
    manifest = json.loads((path / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1.2.0" and "geometry" in manifest["sheets"][0]
    reloaded = load_stack(path)
    assert reloaded.sheets[0].geometry.equals_exact(edited.sheets[0].geometry, 0.0)
    assert review.geometry_fingerprint(reloaded) == review.geometry_fingerprint(edited)
    wkb = path / manifest["sheets"][0]["geometry"]["file"]
    wkb.write_bytes(wkb.read_bytes()[:-1] + b"\x00")
    with pytest.raises(core.ManifestError, match="recorded hash"):
        load_stack(path)


# -- review project -------------------------------------------------------------------
def test_project_log_undo_revert_replay_and_mask_operands(project, stack, tmp_path) -> None:
    back = _back(stack)
    mask = np.zeros((stack.grid.outer_pixels,) * 2, np.uint8)
    mask[480:500, 60:80] = 255  # bottom-left artwork corner, hidden by the front sheet
    Image.fromarray(mask).save(tmp_path / "operand.png")
    review.apply_to_project(project, {"op": "subtract", "sheet": back, "mask": str(tmp_path / "operand.png")})
    edited, entry = review.apply_to_project(project, {"op": "recolor", "sheet": back, "color": "#EEEEEE"})
    assert entry["validation_status"] == "FAIL" and edited.review["current"]["status"] == review.DRAFT
    logged = edited.review["edits"][0]["op"]["mask"]
    assert logged.startswith("review/operands/") and (project / logged).is_file()  # copied: replay is self-contained
    (tmp_path / "operand.png").unlink()
    assert review.verify_project(project)["replay_identical"]
    undone, validation = review.undo(project)
    assert validation["status"] == "PASS" and len(undone.review["edits"]) == 1
    assert undone.review["current"]["status"] == review.VALID
    assert undone.review["reverted"][-1]["dropped"][0]["op"] == "recolor"
    base, _ = review.revert(project, 0)
    assert review.geometry_fingerprint(base) == review.geometry_fingerprint(stack)
    with pytest.raises(EditError, match="nothing to undo"):
        review.undo(project)


def test_invalid_draft_is_saved_for_inspection_but_never_exported(project, stack, tmp_path, monkeypatch) -> None:
    review.apply_to_project(project, {"op": "subtract", "sheet": _back(stack), "shape": HIDDEN_RING})
    reopened = load_stack(project)
    assert reopened.review["current"]["printable"] is False and reopened.review["current"]["floating_islands"] == 1
    report = review.preview_project(project, tmp_path / "preview")
    assert report["notice"].startswith("DRAFT_INVALID")
    assert report["changes_vs_base"]["hidden_changed_px"] > 0 and report["changes_vs_base"]["visible_changed_px"] == 0
    with pytest.raises(EditError, match="not exported"):
        review.export_project(project, tmp_path / "cad")
    assert not (tmp_path / "cad").exists()
    from layerforge_l1 import cli

    monkeypatch.setattr(sys, "argv", ["layerforge_l1", "stack-export", "--stack", str(project),
                                      "--output", str(tmp_path / "cad2")])
    assert cli.main() == 1 and not (tmp_path / "cad2").exists()


def test_cli_review_workflow(project, stack, tmp_path, monkeypatch, capsys) -> None:
    from layerforge_l1 import cli

    def run(*argv: str) -> int:
        monkeypatch.setattr(sys, "argv", ["layerforge_l1", "stack-edit", *argv])
        return cli.main()

    back = _back(stack)
    assert run("apply", "--project", str(project), "rim", f"sheet={back}", "width_mm=5", "total_height_mm=2.4") == 0
    assert run("apply", "--project", str(project), "move", f"sheet={_ids(stack)[-1]}", "to=0") == 1
    assert run("inspect", "--project", str(project), "--report", str(tmp_path / "inspect.json")) == 1
    inspected = json.loads((tmp_path / "inspect.json").read_text(encoding="utf-8"))
    assert [e["kind"] for e in inspected["edits"]] == ["physical_parameters", "visible_artwork"]
    assert run("preview", "--project", str(project), "--output", str(tmp_path / "p"), "--against", "previous") == 1
    assert (tmp_path / "p" / "changes_vs_previous.png").is_file()
    assert run("undo", "--project", str(project)) == 0
    assert run("verify", "--project", str(project)) == 0
    assert run("validate", "--project", str(project)) == 0
    assert run("apply", "--project", str(project), "recolor", "sheet=nope", "color=#000000") == 2
    assert load_stack(project).sheets[0].rim == {"width_mm": 5.0, "total_height_mm": 2.4}


# -- integration: L1 proposal -> edits -> save/load -> replay -> CAD -------------------------
def test_edit_l1_proposal_save_reload_and_export_cad(tmp_path) -> None:
    image = tmp_path / "dev_bow.jpg"
    Image.fromarray(_render(_fixture(), 900)).save(image, "JPEG", quality=85)
    from layerforge_l1.core.propose import READY, ProposalConfig, propose

    result = propose(image, tmp_path / "l1", config=ProposalConfig(planning=FAST.planning, export_cad=False))
    assert result["status"] == READY
    source = tmp_path / "l1" / "candidates" / result["selected"] / "stack"
    project = tmp_path / "project"
    original, validation = review.start_review(source, project)
    assert validation["status"] == "PASS" and original.provenance["provenance"] == "auto-proposed"
    back, front = original.sheets[0].id, original.sheets[-1].id
    red = min(original.sheets, key=lambda s: np.abs(np.array([int(s.color[i:i + 2], 16) for i in (1, 3, 5)])
                                                    - [200, 50, 50]).sum()).id

    edits = [
        {"op": "thickness", "sheet": "*", "mm": 1.8},
        {"op": "rim", "sheet": back, "width_mm": 6.0, "total_height_mm": 3.0},
        {"op": "subtract", "sheet": back, "shape": HIDDEN_CIRCLE},
        {"op": "subtract", "sheet": back, "shape": HIDDEN_RING},  # creates an island: invalid draft
        {"op": "bridge", "sheet": back, "from_mm": [50, 30], "to_mm": [50, 15], "width_mm": 3.0},
        {"op": "fill_hole", "sheet": front, "at_mm": FACE_CENTRE_MM},  # visible: undone below
        {"op": "recolor", "sheet": red, "color": "#A01818"},
        {"op": "accept_artwork"},
    ]
    statuses = []
    for op in edits:
        _, entry = review.apply_to_project(project, op)
        statuses.append((entry["change"]["kind"], entry["validation_status"]))
    assert statuses[3] == ("hidden_geometry", "FAIL") and statuses[4] == ("hidden_geometry", "PASS")
    assert statuses[5] == ("visible_artwork", "FAIL")
    review.revert(project, 5)  # drop the visible fill, recolor and accept
    for op in edits[6:]:
        _, entry = review.apply_to_project(project, op)
    assert entry["validation_status"] == "PASS"

    # Save -> reload reproduces geometry, metadata and the front view; the log replays from the base.
    reloaded = load_stack(project)
    assert json.loads((project / MANIFEST_NAME).read_text(encoding="utf-8"))["schema_version"] == "1.2.0"
    assert [e["op"]["op"] for e in reloaded.review["edits"]] == [op["op"] for op in edits[:5] + edits[6:]]
    assert [e["op"].get("precision") for e in reloaded.review["edits"][2:5]] == ["vector"] * 3  # logged explicitly
    assert reloaded.sheets[0].geometry is not None and all(s.geometry is None for s in reloaded.sheets[1:])
    assert reloaded.review["reverted"][0]["kept_edits"] == 5
    assert reloaded.review["base"]["provenance"]["provenance"] == "auto-proposed"
    assert reloaded.review["base"]["source_manifest_sha256"] == core.model.file_sha256(source / MANIFEST_NAME)
    assert reloaded.provenance == original.provenance
    assert [s.thickness_mm for s in reloaded.sheets] == [1.8] * len(reloaded.sheets)
    assert reloaded.sheets[0].rim == {"width_mm": 6.0, "total_height_mm": 3.0}
    assert reloaded.assembly_z_mm()[1] == pytest.approx(3.0)
    verification = review.verify_project(project)
    assert verification["replay_identical"], verification["differences"]
    validation = core.validate_stack(reloaded)
    assert validation["status"] == "PASS" and validation["reconstruction"]["mismatched_pixels"] == 0
    status = reloaded.review["current"]
    assert status["status"] == review.VALID and status["deviation_from_source_artwork_px"] > 0

    # Export with the L0 pipeline, reopen STEP/STL and compare with the reviewed geometry.
    summary = review.export_project(project, tmp_path / "cad")
    assert summary["pass"], summary
    assert summary["all_step_valid_single_solid"] and summary["all_stl_watertight_single_component"]
    assert summary["stack_mismatch_px"] == 0 and summary["reconstruction_from_stl"]["mismatched_pixels"] == 0
    names = reloaded.export_names()
    assert summary["exported_files"] == names
    recolored = names[[s.id for s in reloaded.sheets].index(red)]
    assert recolored.endswith("_a01818") and recolored != red  # file name follows the edit, id stays stable
    cad_manifest = json.loads((tmp_path / "cad" / "cad_manifest.json").read_text(encoding="utf-8"))
    assert [(r["layer"], r["sheet_id"]) for r in cad_manifest["assembly_back_to_front"]] == \
        list(zip(names, [s.id for s in reloaded.sheets]))
    for sheet, name in zip(reloaded.sheets, names):
        assert np.array_equal(core.rasterize_stl(tmp_path / "cad" / "stl" / f"{name}.stl", reloaded.grid),
                              sheet.retained)
    rows = {r["sheet_id"]: r for r in cad_manifest["layers"]}
    assert rows[back]["geometry_source"] == "exact vector geometry"
    assert rows[back]["stl_height_mm"] == pytest.approx(3.0, abs=1e-3)
    assert all(abs(r["stl_bounds_mm"][1][2] - 1.8) < 1e-3 for k, r in rows.items() if k != back)
    # The vector sheet is extruded from its exact geometry: the reopened STL footprint matches it,
    # and the physical checks pass on that exported footprint itself.
    exact = summary["vector_sheets"][names[0]]
    assert exact["vector_checks_on_stl_pass"] and exact["stl_mean_boundary_deviation_mm"] < 1e-3
    footprint = core.stl_footprint(tmp_path / "cad" / "stl" / f"{names[0]}.stl")
    assert footprint.symmetric_difference(reloaded.sheets[0].geometry).area < 0.01
    import cadquery as cq

    shape = cq.importers.importStep(str(tmp_path / "cad" / "step" / f"{names[0]}.step"))
    assert len(shape.solids().vals()) == 1 and shape.val().isValid()
    assert shape.val().BoundingBox().zlen == pytest.approx(3.0, abs=1e-3)
    band6 = 225.0 ** 2 - (225.0 - 12.0) ** 2  # the 6 mm rim lies inside the solid 12.5 mm perimeter band
    assert shape.val().Volume() == pytest.approx(reloaded.sheets[0].geometry.area * 1.8 + band6 * 1.2, rel=1e-4)
