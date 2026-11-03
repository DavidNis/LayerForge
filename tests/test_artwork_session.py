"""Artwork editor working session: several edits shown at once, saved together (``core/artwork.py``).

Every edit (Line, Curve, Fill, Brush, Close gap, region recolor) goes into an unsaved session on top of
the saved artwork; the next edit works on the result.  Undo/redo step through the unsaved edits,
Discard drops them, Save appends them all to the ordinary edit log at once.  The session is kept on
disk (recovery after a reload or restart) but is never read as saved or planned artwork: export,
planning and resize refuse while it has edits.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from layerforge_l1.core import artwork as art

from test_artwork_paint import BLACK, MPP, SAIL_LOWER, SAIL_UPPER, _changed, _files, _ring, _square
from test_artwork_recolor import FRAME, GREEN, RED, _import, app  # noqa: F401 - shared fixtures

BLUE = "#0000FF"


def _folder(tmp_path, image):
    folder = tmp_path / "artwork"
    art.start(folder, image, {"stage": "test"}, FRAME, MPP)
    return folder


def _saved(folder):
    return {name: (folder / name).read_bytes() for name in ("current.png", "edits.json")}


def _line(points, **extra):
    return art.check_op({"op": "paint_stroke", "points_px": points, "width_mm": 0.5, "color": BLACK, **extra})


def test_four_edits_build_on_each_other_and_are_saved_together(tmp_path):
    image = _square()
    folder = _folder(tmp_path, image)
    before = _saved(folder)
    art.session_add(folder, _line([[20, 5], [20, 35]]))  # Line splits the green square
    split, _, _ = art.session_add(folder, art.check_op({"at_px": [11, 11], "color": RED}))  # Fill one half only
    assert np.all(split[11, 11] == (255, 0, 0)) and np.all(split[28, 28] == (0, 255, 0))  # the other half stays
    curve = {"op": "paint_stroke", "path": "cubic", "points_px": [[20, 12], [33, 18], [33, 22], [20, 28]],
             "width_mm": 0.5, "color": BLACK}
    art.session_add(folder, art.check_op(curve))  # Curve cuts a pocket out of the lower half
    after, _, session = art.session_add(folder, art.check_op({"regions": [{"at_px": [25, 20]}], "color": BLUE}))
    assert np.all(after[25, 20] == (0, 0, 255)) and np.all(after[28, 11] == (0, 255, 0))  # only the pocket
    assert len(session["ops"]) == 4 and _saved(folder) == before  # nothing saved yet
    assert art.read_log(folder)["edits"] == [] and art.verify(folder)["replay_identical"]

    saved, entries = art.session_save(folder)
    assert np.array_equal(saved, after) and art.read_session(folder) is None
    log = art.read_log(folder)
    assert [e["index"] for e in log["edits"]] == [1, 2, 3, 4] and {e["batch"] for e in log["edits"]} == {"save_001"}
    assert [e["op"]["op"] for e in log["edits"]] == ["paint_stroke", "recolor_region", "paint_stroke", "recolor_regions"]
    assert art.verify(folder)["replay_identical"] and np.array_equal(art.load_rgb(folder / "current.png"), after)
    colors = {tuple(c) for c in np.unique(after.reshape(-1, 3), axis=0).tolist()}
    assert colors == {(0, 0, 0), (0, 255, 0), (255, 0, 0), (0, 0, 255), (255, 255, 255), (255, 0, 255)}  # flat
    art.session_add(folder, art.check_op({"at_px": [28, 11], "color": BLUE}))
    art.session_save(folder)
    assert art.read_log(folder)["edits"][-1]["batch"] == "save_002" and art.verify(folder)["replay_identical"]
    assert np.array_equal(art.revert(folder, 4)[0], after)  # saved history: revert to the first save


def test_undo_and_redo_single_unsaved_edits(tmp_path):
    image = _square()
    folder = _folder(tmp_path, image)
    one, _, _ = art.session_add(folder, _line([[20, 5], [20, 35]]))
    two, _, _ = art.session_add(folder, art.check_op({"at_px": [11, 11], "color": RED}))
    undone, session = art.session_undo(folder)
    assert np.array_equal(undone, one) and len(session["ops"]) == 1 and len(session["redo"]) == 1
    assert np.array_equal(art.session_undo(folder)[0], image)
    with pytest.raises(art.ArtworkError, match="no unsaved artwork change to undo"):
        art.session_undo(folder)
    assert np.array_equal(art.session_redo(folder)[0], one)
    redone, session = art.session_redo(folder)
    assert np.array_equal(redone, two) and session["redo"] == []
    with pytest.raises(art.ArtworkError, match="no undone"):
        art.session_redo(folder)
    art.session_undo(folder)
    art.session_add(folder, art.check_op({"at_px": [28, 28], "color": BLUE}))  # a new edit ends the redo chain
    assert art.read_session(folder)["redo"] == []
    saved, entries = art.session_save(folder)
    assert [e["to"] for e in entries] == [BLACK, BLUE] and np.all(saved[11, 11] == (0, 255, 0))


def test_fill_a_region_created_by_an_unsaved_line_or_curve(tmp_path):
    folder = _folder(tmp_path, _square())
    with pytest.raises(art.ArtworkError, match="already has"):  # before the line: one region, already green
        art.session_add(folder, art.check_op({"at_px": [11, 11], "color": GREEN}))
    art.session_add(folder, _line([[5, 7], [34, 33]]))  # thin diagonal line: two regions now
    filled, entry, _ = art.session_add(folder, art.check_op({"at_px": [28, 11], "color": RED}))
    green = np.all(filled == (0, 255, 0), axis=2)
    assert entry["change"]["regions_before"][GREEN] == 2 and green.sum() > 100  # the other side stays green
    assert not np.any(np.all(filled == (255, 0, 0), axis=2) & np.all(art.load_rgb(folder / "current.png") == (0, 0, 0), axis=2))
    folder2 = _folder(tmp_path / "curve", _square())
    art.session_add(folder2, art.check_op({"op": "paint_stroke", "path": "cubic", "width_mm": 0.5, "color": BLACK,
                                           "points_px": [[5, 12], [25, 18], [25, 22], [5, 28]]}))
    pocket, entry, _ = art.session_add(folder2, art.check_op({"at_px": [11, 20], "color": RED}))
    assert entry["selection"]["pixels"] < 100 and np.all(pocket[25, 15] == (0, 255, 0))
    gap = _folder(tmp_path / "gap", _ring())  # a gap closed in the session, then the enclosed region filled
    art.session_add(gap, art.check_op({"op": "close_gap", "points_px": [[6, 16], [6, 23]], "width_mm": 1.0, "color": BLACK}))
    inside, entry, _ = art.session_add(gap, art.check_op({"at_px": [20, 20], "color": RED}))
    assert np.all(inside[4, 4] == (255, 255, 255)) and np.all(inside[20, 20] == (255, 0, 0))


def test_discard_leaves_the_saved_artwork_untouched(tmp_path):
    folder = _folder(tmp_path, _square())
    art.session_add(folder, art.check_op({"at_px": [11, 11], "color": RED}))
    art.session_save(folder)
    before = _saved(folder)
    art.session_add(folder, _line([[20, 5], [20, 35]]))
    art.session_add(folder, art.check_op({"op": "paint_stroke", "points_px": [[15, 15], [16, 16]], "width_mm": 2,
                                          "color": BLUE, "clip_regions": [{"at_px": [11, 11]}]}))
    assert art.session_discard(folder) == 2
    assert _saved(folder) == before and art.read_session(folder) is None and art.verify(folder)["replay_identical"]
    assert art.session_discard(folder) == 0
    with pytest.raises(art.ArtworkError, match="no unsaved artwork changes to save"):
        art.session_save(folder)


def test_the_session_is_recovered_from_disk_and_checked(tmp_path):
    folder = _folder(tmp_path, _square())
    art.session_add(folder, _line([[20, 5], [20, 35]]))
    working, _, _ = art.session_add(folder, art.check_op({"at_px": [11, 11], "color": RED}))
    session = art.read_session(folder)  # a reload or a restart reads the session back
    log = art.read_log(folder)
    assert np.array_equal(art.session_image(folder, log, session), working)
    art.save_rgb(np.zeros_like(working), folder / "session" / "current.png")  # damaged working image
    assert np.array_equal(art.session_image(folder, log, session), working)  # rebuilt by replay
    assert np.array_equal(art.session_save(folder)[0], working)
    # A session made on artwork that has changed since (e.g. a saved edit undone elsewhere) cannot be saved.
    art.session_add(folder, art.check_op({"at_px": [28, 28], "color": BLUE}))
    art.revert(folder, 1)
    with pytest.raises(art.ArtworkError, match="changed after these unsaved changes"):
        art.session_save(folder)
    assert art.session_discard(folder) == 1 and art.verify(folder)["replay_identical"]


def test_session_through_the_server_save_reload_export_and_replan(app):
    """Server: edits shown at once, export/plan/resize refused while unsaved, Save, restart, explicit re-plan."""

    from test_l4_app import start

    client, workspace, _ = app
    name = _import(client)
    state = client.state(name)
    client.run(name, "review", {"candidate": state["proposal"]["selected"]})
    source = _files(workspace / name / "source")
    base = f"/api/projects/{name}/artwork/session"
    folder = workspace / name / "artwork"

    def add(op):
        return client.ok("POST", f"{base}/add", {"op": op})

    curve = {"op": "paint_stroke", "path": "cubic", "points_px": [[250, 240], [215, 280], [285, 300], [240, 345]],
             "width_mm": 1.0, "color": "#774627", "source": "palette"}
    assert add(curve)["unsaved"] == 1
    view = client.state(name)["artwork"]
    assert view["unsaved"]["count"] == 1 and "/artwork/session/current.png" in view["image"]
    assert not view["differs_from_planned"] and view["edits"] == []  # unsaved: not a saved revision
    working = art.load_rgb(folder / "session" / "current.png")
    upper = art.select_region(working, None, SAIL_UPPER)[0]
    assert not upper[SAIL_LOWER[0], SAIL_LOWER[1]]  # the unsaved curve already split the sail
    preview = client.ok("POST", f"/api/projects/{name}/artwork/preview", {"at_px": SAIL_UPPER})  # selects on it
    assert preview["selection"]["pixels"] == int(upper.sum())
    assert add({"at_px": SAIL_UPPER, "color": "#C0392B", "source": "picker"})["unsaved"] == 2
    add({"op": "paint_stroke", "points_px": [[80, 410], [100, 430]], "width_mm": 2.0, "color": "#C0392B",
         "source": "picker", "clip_regions": [{"at_px": [90, 420]}]})  # Brush inside the sun
    assert client.ok("POST", f"{base}/undo", {}) == {"unsaved": 2, "redo": 1}
    assert client.ok("POST", f"{base}/redo", {}) == {"unsaved": 3, "redo": 0}

    for path, payload, word in ((f"/api/projects/{name}/kits", {}, "print-kit export"),
                                (f"/api/projects/{name}/artwork/replan", {}, "planning"),
                                (f"/api/projects/{name}/resize/preview", {"size_mm": 160}, "physical resize"),
                                (f"/api/projects/{name}/artwork/undo", {}, "undoing saved edits")):
        status, error = client.call("POST", path, payload)
        assert status == 400 and "unsaved artwork change" in error["error"]["message"] and word in error["error"]["message"]
    status, error = client.call("POST", f"/api/projects/{name}/artwork/apply", {"op": curve, "preview_id": "x"})
    assert status == 400 and "unsaved" in error["error"]["message"]

    server2, client = start(workspace)  # recovery: a new server on the same folder still has the session
    try:
        assert client.state(name)["artwork"]["unsaved"]["count"] == 3
        saved = client.ok("POST", f"{base}/save", {})
        assert saved == {"saved": 3, "edits": 3, "batch": "save_001", "unsaved": 0, "redo": 0}
        state = client.state(name)
        assert state["artwork"]["unsaved"] is None and len(state["artwork"]["edits"]) == 3
        assert state["artwork"]["differs_from_planned"] and state["artwork"]["can_replan"]
        assert np.array_equal(art.load_rgb(folder / "current.png"), art.replay(folder, art.read_log(folder)))
        status, error = client.call("POST", f"/api/projects/{name}/kits", {})
        assert status == 400 and "planned" in error["error"]["message"]  # saved but not planned: still blocked
        assert client.ok("POST", f"/api/projects/{name}/artwork/verify", {})["replay_identical"]
        committed = art.load_rgb(folder / "current.png")
        add({"at_px": SAIL_LOWER, "color": "#1C478D", "source": "picker"})
        assert client.ok("POST", f"{base}/discard", {}) == {"discarded": 1, "unsaved": 0, "redo": 0}
        assert np.array_equal(art.load_rgb(folder / "current.png"), committed)

        client.run(name, "artwork/replan")  # only on the explicit request
        state = client.state(name)
        check = state["proposal"]["artwork_check"]
        assert (workspace / name / "attempts" / check["archived_as"] / "review" / "sheet_stack.json").is_file()
        assert state["proposal"]["artwork_edits"]["region_recolors"] == 3 and not state["artwork"]["differs_from_planned"]
        assert np.array_equal(art.load_rgb(workspace / name / "proposal" / "intake" / "target.png"), committed)
        edits = json.loads((workspace / name / "proposal" / "artwork_edits" / "edits.json").read_text(encoding="utf-8"))
        assert {e["batch"] for e in edits["edits"]} == {"save_001"}
        assert not (workspace / name / "proposal" / "artwork_edits" / "session").exists()
    finally:
        server2.shutdown()
        server2.server_close()
    assert _files(workspace / name / "source") == source  # the uploaded image is never changed


def test_bad_session_requests_are_refused(app):
    client, workspace, _ = app
    name = _import(client)
    base = f"/api/projects/{name}/artwork/session"
    for action, message in (("save", "no unsaved"), ("undo", "no unsaved"), ("redo", "no undone"), ("bogus", "unknown")):
        status, error = client.call("POST", f"{base}/{action}", {})
        assert status in (400, 404) and message in error["error"]["message"], (action, error)
    status, error = client.call("POST", f"{base}/add", {"op": {"op": "paint_stroke", "points_px": [[1, 1]],
                                                               "width_mm": 99, "color": BLACK}})
    assert status == 400 and "thickness" in error["error"]["message"]
    assert client.ok("POST", f"{base}/discard", {})["discarded"] == 0
