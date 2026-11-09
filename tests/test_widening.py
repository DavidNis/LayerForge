"""Cutout widening into hidden material (``core/widening.py``, the ``widen_openings`` edit, the app step).

Synthetic stack on the Chupa grid (200 mm sheet at scale 2: 1152 px, 64 px perimeter, 0.1736 mm/px, so
the same 2 px growth, 5 px opening disk and 7 px structural kernel as the investigation):

* image: white; a 2 px black line (artwork rows 200-399, col 300-301); a red square V (rows 600-699,
  cols 600-699) inside a 3 px black ring, except where a 10 px bridge crosses the ring (rows 645-654),
  which shows white;
* sheets back to front: 0 black (everything), 1 red (open at the line and the ring, so V hangs on the
  hidden bridge), 2 white in front (open at the line, V and the ring, covering the bridge crossing).

The line opening of sheet 1 is narrow (2 px < 0.8 mm) and surrounded by hidden material: it is widened.
Widening the ring of sheet 1 would thin the bridge that holds V to 6 px, below the 7 px structural
kernel: that spot must be rejected and kept as material.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from layerforge_l1.core import review, widening
from layerforge_l1.core.editing import EditError, apply_edit
from layerforge_l1.core.model import Sheet, SheetStack, StackGrid, save_stack
from layerforge_l1.core.validation import validate_sheet_mask, validate_stack

GRID = StackGrid.for_planner(200.0, 2)
P, N = GRID.perimeter_pixels, GRID.outer_pixels
WHITE, BLACK, RED = "#FFFFFF", "#000000", "#D02020"


def _box(r0, r1, c0, c1) -> np.ndarray:
    m = np.zeros((N, N), bool)
    m[r0 + P:r1 + P, c0 + P:c1 + P] = True
    return m


LINE = _box(200, 400, 300, 302)
V = _box(600, 700, 600, 700)
RING = _box(597, 703, 597, 703) & ~V
CROSSING = RING & _box(645, 655, 700, 703)
RING_OPEN = RING & ~CROSSING


def _stack() -> SheetStack:
    full = np.ones((N, N), bool)
    perimeter = GRID.perimeter_mask()
    a = GRID.artwork_pixels
    target = np.full((a, a, 3), 255, np.uint8)
    inner = (slice(P, -P), slice(P, -P))
    target[(LINE | RING_OPEN)[inner]] = 0
    target[V[inner]] = [0xD0, 0x20, 0x20]
    s0 = Sheet("s0_black", BLACK, full.copy(), LINE | RING_OPEN, 1.6)
    s1 = Sheet("s1_red", RED, full & ~LINE & ~RING_OPEN, V.copy(), 1.6)
    s2 = Sheet("s2_white", WHITE, full & ~LINE & ~V & ~RING_OPEN, ~perimeter & ~LINE & ~V & ~RING_OPEN, 1.6)
    return SheetStack("widening_fixture", GRID, [s0, s1, s2], target_rgb=target)


@pytest.fixture(scope="module")
def stack() -> SheetStack:
    built = _stack()
    assert validate_stack(built)["status"] == "PASS"
    return built


@pytest.fixture(scope="module")
def widened(stack):
    return widening.widen_openings(stack)


def test_line_is_widened_and_the_bridge_spot_is_rejected(stack, widened) -> None:
    result, report = widened
    assert report["status"] == widening.APPLIED and result is not stack
    s1_before, s1_after = stack.sheets[1].retained, result.sheets[1].retained
    removed = s1_before & ~s1_after
    # The line opening grew by 2 px per side into hidden material: 2 px -> 6 px wide.
    row = 300 + P
    assert np.flatnonzero(~s1_after[row, 290 + P:312 + P]).tolist() == list(range(8, 14))
    # The ring spot would leave V on a 6 px bridge: rejected, the bridge and the ring band stay.
    assert report["rejected"] >= 1
    assert any(r["sheet"] == "s1_red" for r in report["rejected_spots"])
    assert s1_after[CROSSING].all() and not removed[_box(590, 710, 590, 710)].any()
    assert validate_sheet_mask(result, s1_after, result.sheets[1].visible)["pass"]
    # Nothing outside the hidden material of the intermediate sheet changed.
    assert np.array_equal(result.sheets[0].retained, stack.sheets[0].retained)
    assert np.array_equal(result.sheets[2].retained, stack.sheets[2].retained)
    assert report["sheets"][1]["removed_px"] == int(removed.sum()) == report["removed_px"]


def test_geometry_preservation(stack, widened) -> None:
    result, report = widened
    gate = report["gate"]
    assert gate["pass"] and gate["reconstruction_mismatched_pixels"] == 0
    assert gate["visible_masks_unchanged"] and gate["front_view_unchanged"] and gate["material_only_removed"]
    assert [(s.id, s.color, s.thickness_mm) for s in result.sheets] == [(s.id, s.color, s.thickness_mm) for s in stack.sheets]
    for a, b in zip(stack.sheets, result.sheets):
        assert np.array_equal(a.visible, b.visible)
        assert not (b.retained & ~a.retained).any()
        assert b.retained[GRID.perimeter_mask()].all()
        removed = a.retained & ~b.retained
        assert not (removed & a.visible).any()
    assert np.array_equal(stack.composite_sheet_index(), result.composite_sheet_index())
    assert validate_stack(result)["status"] == "PASS"
    assert np.array_equal(result.target_rgb, stack.target_rgb)


def test_removed_material_always_joins_an_opening(stack, widened) -> None:
    """No new separate cutout: every removed pixel is 4-connected to an existing opening (a pixel the
    growth reaches across a corner or past visible material is kept)."""

    import cv2

    result, report = widened
    assert report["gate"]["no_new_separate_cutouts"]
    perim = GRID.perimeter_mask()
    for a, b in zip(stack.sheets, result.sheets):
        old_open, new_open = ~a.retained & ~perim, ~b.retained & ~perim
        _, labels = cv2.connectedComponents(new_open.astype(np.uint8), connectivity=4)
        assert set(np.unique(labels[new_open])) == set(np.unique(labels[old_open])) - {0} or not old_open.any()
    # Also with a 1 px opening whose growth reaches hidden pixels across corners: each candidate piece
    # joins that opening edge to edge.
    s = _stack()
    s1 = s.sheets[1]
    y, x = 150 + P, 290 + P  # away from everything; a 1 px opening with a diagonal neighbour
    s1.retained[y, x] = False
    s.sheets[0].visible[y, x] = True
    s.target_rgb[y - P, x - P] = 0  # the back black sheet shows through it
    s.sheets[2].retained[y, x] = False
    s.sheets[2].visible[y, x] = False
    assert validate_stack(s)["status"] == "PASS"
    add, _ = widening.candidate_pixels(s, 1, s.composite_sheet_index(), widening.WideningConfig(), perim)
    _, labels = cv2.connectedComponents((~s1.retained & ~perim | add).astype(np.uint8), connectivity=4)
    opened = ~s1.retained & ~perim
    for comp in np.unique(labels[add]):
        assert (opened & (labels == comp)).any()


def test_repeatable(stack, widened) -> None:
    again, report = widening.widen_openings(stack)
    for a, b in zip(widened[0].sheets, again.sheets):
        assert np.array_equal(a.retained, b.retained)
    keys = ("removed_px", "spots", "rejected", "rejected_spots", "local_checks", "full_sheet_fallbacks")
    assert {k: report[k] for k in keys} == {k: widened[1][k] for k in keys}


def test_local_check_is_never_unsafe(stack) -> None:
    """Whenever the windowed check accepts a removal, the whole-sheet validator passes too."""

    structure = widening._Structure(stack)
    sheet = stack.sheets[1]
    hidden = sheet.retained & ~sheet.visible & ~GRID.perimeter_mask() & (stack.composite_sheet_index() > 1)
    rng = np.random.default_rng(7)
    near_bridge = [(640 + P + int(rng.integers(-12, 12)), 700 + P + int(rng.integers(-6, 8))) for _ in range(40)]
    anywhere = list(zip(*np.nonzero(hidden)))
    picks = near_bridge + [anywhere[i] for i in rng.choice(len(anywhere), 40, replace=False)]
    outcomes = set()
    for y, x in picks:
        h, w = int(rng.integers(1, 9)), int(rng.integers(1, 9))
        box = (y, y + h, x, x + w)
        spot = hidden[y:y + h, x:x + w].copy()
        if not spot.any():
            continue
        trial = sheet.retained.copy()
        trial[y:y + h, x:x + w] &= ~spot
        full = validate_sheet_mask(stack, trial, sheet.visible)["pass"]
        assert structure.sheet_ok(trial) == full
        local = structure.local_change_is_safe(sheet.retained, box, spot)
        assert not (local and not full), (box, local, full)
        outcomes.add((local, full))
    assert (True, True) in outcomes and (False, False) in outcomes


def _two_lines(gap_px: int) -> SheetStack:
    """The fixture plus a second 2 px line ``gap_px`` columns right of a first one (rows 200-399, cols
    500-501): the hidden strip between the two openings of sheet 1 is ``gap_px`` px wide."""

    s = _stack()
    a, b = _box(200, 400, 500, 502), _box(200, 400, 502 + gap_px, 504 + gap_px)
    inner = (slice(P, -P), slice(P, -P))
    for line in (a, b):
        s.target_rgb[line[inner]] = 0
        s.sheets[0].visible |= line
        s.sheets[1].retained &= ~line
        s.sheets[2].retained &= ~line
        s.sheets[2].visible &= ~line
    assert validate_stack(s)["status"] == "PASS"
    return s


def test_no_new_thin_material_below_the_structural_width() -> None:
    """Two narrow cutouts 10 px (1.74 mm) apart: growing both by 2 px into the hidden strip between them
    would leave it 6 px (1.04 mm), under the 1.2 mm width.  One of them is refused; the strip keeps at
    least the 7 px structural kernel, and the traced geometry (the CAD input) has no new thin part."""

    from layerforge_l1.core.cad import perimeter_band, sheet_geometry
    from layerforge_l1.core.printkit import _narrow_parts

    s = _two_lines(10)
    result, report = widening.widen_openings(s)
    assert report["status"] == widening.APPLIED and report["gate"]["no_new_thin_material"]
    assert report["gate"]["new_thin_regions"] == 0 and report["gate"]["visible_parts_losing_core"] == 0
    refused = [r for r in report["rejected_spots"] if r["sheet"] == "s1_red" and "new material" in r["reason"]]
    assert len(refused) == 1
    after = result.sheets[1].retained
    strip = after[300 + P, 502 + P:512 + P]
    assert strip.sum() >= 7  # what is left between the two cutouts
    assert not after[300 + P, 498 + P:500 + P].any() or not after[300 + P, 514 + P:516 + P].any()  # one widened
    band = perimeter_band(result)
    for i in range(3):
        before = _narrow_parts(sheet_geometry(s, i).difference(band), 1.2)[0]
        assert len(_narrow_parts(sheet_geometry(result, i).difference(band), 1.2)[0]) <= len(before)
    # With room for both (16 px strip: 12 px after both grow), both are widened.
    wide, wide_report = widening.widen_openings(_two_lines(16))
    assert not [r for r in wide_report["rejected_spots"] if "new material" in r["reason"]]
    assert wide.sheets[1].retained[300 + P, 502 + P:518 + P].sum() == 12


def test_thin_guard_window_agrees_with_the_whole_sheet() -> None:
    """The windowed thin-material check gives the whole-sheet answer for removals near the strip."""

    s = _two_lines(10)
    sheet = s.sheets[1]
    structure = widening._Structure(s)
    guard = widening._ThinGuard(structure, sheet, GRID.perimeter_mask(), GRID.mm_per_pixel)
    hidden = sheet.retained & ~sheet.visible & ~GRID.perimeter_mask() & (s.composite_sheet_index() > 1)
    rng = np.random.default_rng(3)
    seen = set()
    for _ in range(60):
        y, x = 200 + P + int(rng.integers(-10, 200)), 500 + P + int(rng.integers(-4, 16))
        h, w = int(rng.integers(1, 30)), int(rng.integers(1, 5))
        spot = hidden[y:y + h, x:x + w].copy()
        if not spot.any():
            continue
        trial = sheet.retained.copy()
        trial[y:y + h, x:x + w] &= ~spot
        whole = widening.new_thin_material(structure, sheet, type(sheet)(sheet.id, sheet.color, trial, sheet.visible,
                                                                           sheet.thickness_mm), GRID.perimeter_mask(),
                                           GRID.mm_per_pixel)
        expected = not whole["new_thin_regions"] and not whole["visible_parts_losing_core"]
        assert guard.allows(sheet.retained, (y, y + h, x, x + w), spot) == expected
        seen.add(expected)
    assert seen == {True, False}


def test_fallback_to_the_whole_sheet_check_gives_the_same_result(stack, widened, monkeypatch) -> None:
    monkeypatch.setattr(widening._Structure, "local_change_is_safe", lambda self, *a: False)
    result, report = widening.widen_openings(stack)
    # Every check is a whole-sheet check; refused spots are tried once more in the closing pass.
    assert report["full_sheet_fallbacks"] == report["local_checks"] == report["spots"] + report["rejected"]
    for a, b in zip(widened[0].sheets, result.sheets):
        assert np.array_equal(a.retained, b.retained)
    # The default run needed the whole-sheet check only where the window could not decide.
    assert widened[1]["full_sheet_fallbacks"] < widened[1]["local_checks"]


def test_failed_final_checks_restore_the_original(stack, monkeypatch) -> None:
    real = widening.validate_stack

    def failing(candidate):
        result = real(candidate)
        if candidate is not stack:
            result = {**result, "status": "FAIL", "failures": ["injected failure"]}
        return result

    monkeypatch.setattr(widening, "validate_stack", failing)
    result, report = widening.widen_openings(stack)
    assert report["status"] == widening.RESTORED and result is stack
    assert report["gate"]["pass"] is False and "injected failure" in report["gate"]["validation_failures"]


def test_invalid_plans_and_vector_sheets_are_not_widened(stack) -> None:
    broken = _stack()
    broken.sheets[1].retained[_box(99, 151, 99, 151) & ~_box(100, 150, 100, 150)] = False  # a hidden floating island
    result, report = widening.widen_openings(broken)
    assert report["status"] == widening.SKIPPED_INVALID and result is broken

    from layerforge_l1.core.editing import mask_geometry

    vector = _stack()
    vector.sheets[1].geometry = mask_geometry(vector, vector.sheets[1].retained)
    result, report = widening.widen_openings(vector)
    assert report["sheets"][1].get("skipped") and report["status"] == widening.NO_CHANGE


def test_options_plain_growth_and_slivers(stack) -> None:
    plain, report = widening.widen_openings(stack, widening.WideningConfig(avoid_new_slivers=False))
    assert report["status"] == widening.APPLIED and report["config"]["avoid_new_slivers"] is False
    # Sliver avoidance only keeps material: it never removes more than the plain growth.
    default, _ = widening.widen_openings(stack)
    for p_sheet, d_sheet, o_sheet in zip(plain.sheets, default.sheets, stack.sheets):
        assert not ((o_sheet.retained & ~d_sheet.retained) & p_sheet.retained).any()


def test_review_edit_logs_masks_and_replays(stack, tmp_path) -> None:
    save_stack(stack, tmp_path / "stack")
    project = tmp_path / "review"
    review.start_review(tmp_path / "stack", project)
    seen = []
    edited, entry, report = review.widen_project(project, automatic=True, measure=lambda s: seen.append(s) or {"n": len(seen)})
    assert report["status"] == widening.APPLIED and entry["index"] == 1
    op = entry["op"]
    assert op["op"] == "widen_openings" and op["automatic"] is True and list(op["removed"]) == ["s1_red"]
    assert (project / op["removed"]["s1_red"]).is_file()
    assert op["summary"]["before"] == {"n": 1} and op["summary"]["after"] == {"n": 2}
    assert entry["change"]["kind"] == "hidden_geometry" and entry["change"]["visible_changed_px"] == 0
    assert entry["validation_status"] == "PASS"
    saved = review.load_project(project)
    expected = widening.widen_openings(stack)[0]
    assert np.array_equal(saved.sheets[1].retained, expected.sheets[1].retained)
    assert review.verify_project(project)["replay_identical"]
    # Undo restores the planned sheets.
    undone, _ = review.undo(project)
    assert np.array_equal(undone.sheets[1].retained, stack.sheets[1].retained)
    # A logged mask may not reach visible material (a hand-edited or corrupted log).
    bad = dict(op, removed={"s1_red": op["removed"]["s1_red"]})
    from PIL import Image

    mask = np.asarray(Image.open(project / op["removed"]["s1_red"])) > 0
    mask = mask | V
    Image.fromarray(mask.astype(np.uint8) * 255).save(tmp_path / "bad.png")
    bad["removed"] = {"s1_red": (tmp_path / "bad.png").as_posix()}
    with pytest.raises(EditError, match="visible"):
        apply_edit(stack, bad, project)


def test_nothing_to_widen_logs_nothing(tmp_path) -> None:
    save_stack(_stack(), tmp_path / "stack")
    project = tmp_path / "review"
    review.start_review(tmp_path / "stack", project)
    review.widen_project(project)
    # Second run right away (the app refuses it; the core run is idempotent): nothing new to widen.
    stack, entry, report = review.widen_project(project)
    assert entry is None and stack is None and report["status"] == widening.NO_CHANGE
    assert len(review.load_project(project).review["edits"]) == 1


def test_repeated_run_is_idempotent_and_never_grows_a_widened_opening(stack, widened) -> None:
    result, report = widened
    previous = widening.removed_masks(stack, result)
    again, report2 = widening.widen_openings(result, previous=previous)
    assert report2["status"] == widening.NO_CHANGE and again is result
    assert report2["rejected"] == report["rejected"]  # the same spots are tried and kept again
    # An opening that is still narrow after one run (1 px growth: the 2 px line becomes 4 px < 5 px):
    # without the record of the first run it would grow again on every run; with it, never.
    small = widening.WideningConfig(grow_mm=0.17)
    once, _ = widening.widen_openings(stack, small)
    row = 300 + P
    assert np.count_nonzero(~once.sheets[1].retained[row, 290 + P:312 + P]) == 4
    twice, rep = widening.widen_openings(once, small, previous=widening.removed_masks(stack, once))
    assert rep["status"] == widening.NO_CHANGE and twice is once
    grown, rep = widening.widen_openings(once, small)  # the earlier, cumulative behaviour
    assert rep["status"] == widening.APPLIED
    assert np.count_nonzero(~grown.sheets[1].retained[row, 290 + P:312 + P]) == 6


def test_after_edits_only_new_opportunities_are_widened_and_user_material_is_kept(stack, tmp_path) -> None:
    save_stack(stack, tmp_path / "stack")
    project = tmp_path / "review"
    review.start_review(tmp_path / "stack", project)
    first = review.widen_project(project)[0]
    removed_first = first.sheets[1].retained ^ stack.sheets[1].retained
    # Manual edit 1: put material back in part of the widened band next to the line (hidden under sheet 2).
    band = removed_first & _box(250, 350, 290, 312)
    assert band.any()
    from PIL import Image

    Image.fromarray(band.astype(np.uint8) * 255).save(tmp_path / "band.png")
    review.apply_to_project(project, {"op": "add", "sheet": "s1_red", "mask": (tmp_path / "band.png").as_posix()})
    # Manual edit 2: a new narrow slot in sheet 1 elsewhere, also hidden.
    slot = _box(300, 400, 800, 802)
    Image.fromarray(slot.astype(np.uint8) * 255).save(tmp_path / "slot.png")
    edited, entry = review.apply_to_project(project, {"op": "subtract", "sheet": "s1_red", "mask": (tmp_path / "slot.png").as_posix()})
    assert entry["validation_status"] == "PASS" and entry["change"]["visible_changed_px"] == 0
    after, entry, report = review.widen_project(project)
    assert report["status"] == widening.APPLIED and entry["index"] == 4
    new = edited.sheets[1].retained & ~after.sheets[1].retained
    # Only the new slot is widened (2 px per side); the line's opening is not grown again and the
    # material the user put back stays.
    assert new.any() and not (new & ~_box(290, 410, 790, 812)).any()
    assert np.flatnonzero(~after.sheets[1].retained[350 + P, 790 + P:812 + P]).tolist() == list(range(8, 14))
    assert after.sheets[1].retained[band].all()
    # Running again: nothing new.
    assert review.widen_project(project)[2]["status"] == widening.NO_CHANGE
    assert review.verify_project(project)["replay_identical"]
    # Undo the second widening, then run it again: the same result.
    review.undo(project)
    again, entry2, _ = review.widen_project(project)
    assert np.array_equal(again.sheets[1].retained, after.sheets[1].retained) and entry2["index"] == 4
    # Undo everything, run once: the first widening again, bit for bit.
    review.revert(project, 0)
    once = review.widen_project(project)[0]
    assert all(np.array_equal(a.retained, b.retained) for a, b in zip(once.sheets, first.sheets))
    assert review.verify_project(project)["replay_identical"]


def test_cli_widen(stack, tmp_path, capsys) -> None:
    from layerforge_l1.cli import main
    import sys

    save_stack(stack, tmp_path / "stack")
    project = tmp_path / "review"
    argv = sys.argv
    try:
        sys.argv = ["layerforge", "stack-edit", "init", "--from", str(tmp_path / "stack"), "--project", str(project)]
        assert main() == 0
        sys.argv = ["layerforge", "stack-edit", "widen", "--project", str(project), "--report", str(tmp_path / "w.json")]
        assert main() == 0
    finally:
        sys.argv = argv
    data = json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))
    assert data["widening"]["status"] == "APPLIED" and data["edit"]["index"] == 1
    assert data["status"]["status"] == "REVIEWED_VALID"


def test_app_widens_on_review_start_reruns_only_on_request_and_rechecks_decisions(tmp_path) -> None:
    from test_l4_app import _propose, start

    server, client = start(tmp_path / "workspace")
    try:
        name, state = _propose(client)
        assert state["record"]["proposal_settings"]["widen_openings"] is True
        # Accept risk for every opening issue of the proposed candidate, then start the review.
        before = client.ok("GET", f"/api/projects/{name}/issues")
        openings = [i["key"] for i in before["issues"] if i["kind"] in ("narrow_slot_or_gap", "opening_may_close", "narrow_neck_in_opening")]
        if openings:
            client.ok("POST", f"/api/projects/{name}/issues/review", {"keys": openings, "state": "accept_risk"})
        status, _ = client.call("POST", f"/api/projects/{name}/widen", {})
        assert status == 400  # no review yet
        client.run(name, "review", {"candidate": state["proposal"]["selected"]})
        draft = client.state(name)["draft"]
        w = draft["widening"]
        assert w["enabled"] and w["runs"] == 1 and w["last"]["automatic"] and w["last"]["index"] == 1
        assert [e["op"]["op"] for e in draft["edits"]] == ["widen_openings"]
        assert draft["edits"][0]["kind"] == "hidden_geometry" and draft["edits"][0]["visible_changed_px"] == 0
        assert draft["status"]["status"] == "REVIEWED_VALID"
        summary = w["last"]["summary"]
        assert summary["after"]["risky_cutouts"] <= summary["before"]["risky_cutouts"]
        assert draft["last_change"]["action"] == "widen" and draft["last_change"]["index"] == 1
        # Nothing changed since: refused at once.  The inspector shows the widened sheets.
        assert not w["can_run"]
        status, error = client.call("POST", f"/api/projects/{name}/widen", {})
        assert status == 400 and "nothing changed" in error["error"]["message"]
        after = client.ok("GET", f"/api/projects/{name}/issues")
        assert after["sheet_source"] == "review"
        risky = [i for i in after["issues"] if i["kind"] in ("narrow_slot_or_gap", "opening_may_close", "narrow_neck_in_opening")
                 and i["severity"] in ("RISK", "UNSUPPORTED")]
        assert len(risky) == summary["after"]["risky_cutouts"]
        if openings:  # decisions on widened places are re-checked: kept only where nothing changed
            changed = after["review"]["lapsed"] + after["review"]["unmatched"]
            kept = after["review"]["counts"]["accept_risk"]
            assert kept + changed >= 1 and kept <= len(openings)
        # A manual edit: not widened automatically; the user may run it again.
        back = draft["sheets"][0]["id"]
        client.run(name, "edit", {"op": {"op": "thickness", "sheet": back, "mm": 2.0}})
        draft = client.state(name)["draft"]
        assert draft["widening"]["edits_after"] == 1 and draft["widening"]["can_run"]
        assert [e["op"]["op"] for e in draft["edits"]] == ["widen_openings", "thickness"]
        result = client.run(name, "widen")
        assert result["result"]["status"] in ("APPLIED", "NO_CHANGE")
        assert client.run(name, "verify")["result"]["replay_identical"]
        # Undo back to the planned sheets.
        client.run(name, "undo", {"keep": 0})
        draft = client.state(name)["draft"]
        assert draft["edits"] == [] and draft["widening"]["runs"] == 0 and draft["widening"]["can_run"]
    finally:
        server.shutdown()
        server.server_close()


def test_existing_projects_are_not_widened_without_a_choice(stack, tmp_path) -> None:
    """A project saved before the option existed (no ``widen_openings`` setting) keeps its sheets as
    planned when its review starts, unless the user chooses widening; the choice is then saved."""

    from layerforge_l1.app.workspace import Workspace

    def project(name: str) -> Workspace:
        root = tmp_path / "ws"
        save_stack(stack, root / name / "proposal" / "candidates" / "beam_area" / "stack")
        (root / name / "proposal" / "proposal.json").write_text(json.dumps(
            {"candidates": [{"name": "beam_area", "status": "VALID"}], "selected": "beam_area"}), encoding="utf-8")
        (root / name / "project.json").write_text(json.dumps(
            {"name": name, "candidate": None, "kits": [],
             "proposal_settings": {"scale": 2, "outer_mm": 200.0, "thickness_mm": 1.6, "max_colors": 12}}), encoding="utf-8")
        return Workspace(root)

    ws = project("old_project")
    assert ws.widening_enabled(ws.record("old_project")) is None
    out = ws.start_review("old_project", "beam_area", lambda *a, **k: None)
    assert "widening" not in out and review.load_project(ws.review_dir("old_project")).review["edits"] == []
    assert "widen_openings" not in ws.record("old_project")["proposal_settings"]
    ws = project("old_project_2")
    out = ws.start_review("old_project_2", "beam_area", lambda *a, **k: None, widen_openings=True)
    assert out["widening"]["status"] == widening.APPLIED
    assert ws.record("old_project_2")["proposal_settings"]["widen_openings"] is True
