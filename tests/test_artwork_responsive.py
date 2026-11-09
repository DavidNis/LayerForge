"""Artwork editor responsiveness: the same results, computed once (``core/artwork.py``, ``app/workspace.py``,
``app/static/app.js``).

A line on a 1024 px artwork took 3-7 s per request: the palette and change summaries ran
``np.unique(..., axis=0)`` (a row sort of every pixel, ~0.7 s each) five times per edit, adding the edit
recomputed what its preview had just computed, and every replay (undo, redo, save, verify) recomputed
summaries it does not use.  These tests pin that the faster paths give exactly the earlier results, that
a preview is reused only for the same op on the same pixels, and (in a real browser) that the editor
shows a drawn line at once, marks it as not applied until the server answers, refuses a second submission
meanwhile and leaves no wrong artwork on screen when a request fails.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from scipy import ndimage

from layerforge_l1.core import artwork as art
from layerforge_l1.core.model import hex_color

from test_artwork_paint import BLACK, MPP, _square
from test_artwork_recolor import FRAME, RED, _import, app  # noqa: F401 - shared fixtures

FOUR = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)


def _row_unique(pixels, return_counts=False):
    """The earlier implementation: a row sort of every pixel."""

    return np.unique(pixels.reshape(-1, 3), axis=0, return_counts=return_counts)


def _flat_image(seed: int, shape=(90, 120), colors=7) -> np.ndarray:
    """Flat colour blobs (several regions per colour) inside a frame border."""

    rng = np.random.default_rng(seed)
    palette = rng.integers(0, 256, (colors, 3)).astype(np.uint8)
    small = rng.integers(0, colors, (shape[0] // 6, shape[1] // 6))
    image = palette[np.kron(small, np.ones((6, 6), int))]
    image[:2] = image[-2:] = image[:, :2] = image[:, -2:] = (255, 0, 255)
    return np.ascontiguousarray(image)


def _reference_palette(image, frame_hex, mpp):
    inside = ~np.all(image == np.asarray(art.parse_hex(frame_hex), np.uint8), axis=2)
    colors, counts = _row_unique(image[inside], return_counts=True)
    rows = []
    for rgb, count in zip(colors, counts):
        mask = np.all(image == rgb, axis=2) & inside
        regions = int(ndimage.label(mask, structure=FOUR)[1]) if mask.any() else 0
        rows.append({"hex": hex_color(tuple(int(v) for v in rgb)), "pixels": int(count),
                     "area_mm2": round(float(count) * mpp**2, 2), "regions_4": regions})
    rows.sort(key=lambda r: (-r["pixels"], r["hex"]))
    return rows


def test_unique_colors_is_the_row_unique():
    rng = np.random.default_rng(3)
    cases = [np.zeros((0, 3), np.uint8), np.zeros((1, 3), np.uint8), _flat_image(1)[5:9, 5:9]]
    cases += [rng.integers(0, high, shape).astype(np.uint8) for high in (2, 7, 256) for shape in ((50, 3), (40, 30, 3))]
    for pixels in cases:
        for counts in (False, True):
            got, want = art.unique_colors(pixels, counts), _row_unique(pixels, counts)
            got, want = (got, want) if counts else ((got,), (want,))
            for g, w in zip(got, want):
                assert g.dtype == w.dtype and g.shape == w.shape and np.array_equal(g, w)


def test_palette_and_change_summaries_are_unchanged(monkeypatch):
    for seed in (1, 2, 5):
        image = _flat_image(seed)
        assert art.palette_summary(image, FRAME, MPP) == _reference_palette(image, FRAME, MPP)
        op = art.check_op({"op": "paint_stroke", "points_px": [[20, 10], [60, 100]], "width_mm": 1.5, "color": BLACK})
        after, mask, selection, change = art.plan_edit(image, op, FRAME, MPP)
        fill = art.check_op({"at_px": [30, 30], "color": RED})
        fill_change = art.plan_edit(after, fill, FRAME, MPP)[3]
        with monkeypatch.context() as m:  # the same summaries through the earlier row sort
            m.setattr(art, "unique_colors", _row_unique)
            assert art.plan_edit(image, op, FRAME, MPP)[3] == change
            assert art.plan_edit(after, fill, FRAME, MPP)[3] == fill_change
            assert art.change_summary(image, after, mask, selection["colors"], BLACK, FRAME) == change


def test_a_replay_skips_only_the_unused_summary(tmp_path):
    image = _flat_image(4)
    op = art.check_op({"op": "paint_stroke", "points_px": [[15, 15], [70, 90]], "width_mm": 1.0, "color": BLACK})
    full = art.plan_edit(image, op, FRAME, MPP)
    fast = art.plan_edit(image, op, FRAME, MPP, summarize=False)
    assert np.array_equal(full[0], fast[0]) and np.array_equal(full[1], fast[1]) and full[2] == fast[2]
    assert fast[3] is None and full[3]["pixels"] > 0
    # Undo, redo, save and verify (all replays) still reproduce and check every edit.
    folder = tmp_path / "artwork"
    art.start(folder, image, {"stage": "test"}, FRAME, MPP)
    art.session_add(folder, op)
    second, _, _ = art.session_add(folder, art.check_op({"at_px": [30, 30], "color": RED}))
    undone, _ = art.session_undo(folder)
    assert np.array_equal(undone, full[0])
    redone, _ = art.session_redo(folder)
    assert np.array_equal(redone, second)
    saved, entries = art.session_save(folder)
    assert np.array_equal(saved, second) and len(entries) == 2
    assert art.verify(folder)["replay_identical"]
    assert np.array_equal(art.replay(folder, art.read_log(folder)), second)


def _twin(tmp_path, image, name):
    folder = tmp_path / name
    art.start(folder, image, {"stage": "test"}, FRAME, MPP)
    return folder


def _without_time(entry):
    return {k: v for k, v in entry.items() if k != "applied_utc"}


def test_a_preview_is_reused_only_for_the_same_op_on_the_same_pixels(tmp_path):
    image = _square()
    line = art.check_op({"op": "paint_stroke", "points_px": [[20, 5], [20, 35]], "width_mm": 0.5, "color": BLACK})
    planned = art.planned_edit(image, line, FRAME, MPP)
    kept = copy.deepcopy({k: planned[k] for k in ("selection", "change", "palette_after")})

    reused, fresh = _twin(tmp_path, image, "reused"), _twin(tmp_path, image, "fresh")
    a_img, a_entry, _ = art.session_add(reused, line, planned)
    b_img, b_entry, _ = art.session_add(fresh, line)
    assert np.array_equal(a_img, b_img) and _without_time(a_entry) == _without_time(b_entry)
    a_entry["change"]["pixels"] = -1  # the log entry is a copy: the kept preview is not changed through it
    assert {k: planned[k] for k in kept} == kept

    # Planned on other pixels (the artwork changed since the preview): computed again on the current ones.
    curve = art.check_op({"op": "paint_stroke", "points_px": [[12, 8], [12, 30]], "width_mm": 0.5, "color": RED})
    other = image.copy()
    other[5:15, 5:35] = (0, 0, 255)  # pixels the session never had
    stale = art.planned_edit(other, curve, FRAME, MPP)
    c_img, c_entry, _ = art.session_add(reused, curve, stale)
    d_img, d_entry, _ = art.session_add(fresh, curve)
    assert np.array_equal(c_img, d_img) and _without_time(c_entry) == _without_time(d_entry)
    assert "#0000FF" in stale["selection"]["colors"] and "#0000FF" not in c_entry["from_colors"]

    # Planned for another op: not used either.
    other = art.check_op({"op": "paint_stroke", "points_px": [[28, 8], [28, 30]], "width_mm": 0.5, "color": BLACK})
    shifted = art.check_op({**other, "points_px": [[24, 8], [24, 30]]})
    e_img, e_entry, _ = art.session_add(reused, other, art.planned_edit(c_img, shifted, FRAME, MPP))  # same pixels, other op
    f_img, f_entry, _ = art.session_add(fresh, other)
    assert np.array_equal(e_img, f_img) and _without_time(e_entry) == _without_time(f_entry)
    assert art.session_save(reused)[0].tobytes() == art.session_save(fresh)[0].tobytes()
    assert art.verify(reused)["replay_identical"]


def test_server_reuses_the_line_preview_and_recomputes_after_a_change(app, monkeypatch):
    """Preview -> add of the same line computes it once; when the pixels changed after the preview
    (here: Undo), the kept preview is not used and the add is computed on the current pixels; the
    saved artwork replays exactly, with nothing lost or duplicated."""

    client, workspace, _ = app
    name = _import(client, "&plan=0")
    folder = workspace / name / "artwork"
    calls = []
    real = art.planned_edit
    monkeypatch.setattr(art, "planned_edit", lambda *a: calls.append(1) or real(*a))
    base = f"/api/projects/{name}/artwork"
    line = {"op": "paint_stroke", "points_px": [[240, 200], [240, 330]], "width_mm": 2.0, "color": "#774627", "source": "palette"}
    preview = client.ok("POST", f"{base}/preview", line)
    assert len(calls) == 1
    added = client.ok("POST", f"{base}/session/add", {"op": preview["op"]})
    assert len(calls) == 1 and added["change"] == preview["change"]  # reused: computed once, same result

    crossing = {**line, "points_px": [[200, 260], [280, 270]]}  # crosses the first line
    preview2 = client.ok("POST", f"{base}/preview", crossing)
    assert client.ok("POST", f"{base}/session/undo", {})["unsaved"] == 0  # the pixels under the preview change
    calls.clear()
    added2 = client.ok("POST", f"{base}/session/add", {"op": preview2["op"]})
    assert len(calls) == 1 and added2["unsaved"] == 1  # computed again on the current pixels
    session = art.read_session(folder)
    current = art.load_rgb(folder / "current.png")
    logged = art.read_log(folder)["base"]
    fresh = art.plan_edit(current, art.check_op(crossing), logged["frame_color"], logged["mm_per_pixel"])
    assert session["ops"][0]["region_sha256"] == art.mask_sha256(fresh[1]) and session["ops"][0]["change"] == fresh[3]
    assert np.array_equal(art.load_rgb(folder / "session" / "current.png"), fresh[0])

    client.ok("POST", f"{base}/session/add", {"op": preview["op"]})  # the first line again, on top
    assert client.ok("POST", f"{base}/session/save", {})["saved"] == 2
    log = art.read_log(folder)
    assert [e["index"] for e in log["edits"]] == [1, 2] and [e["op"]["points_px"] for e in log["edits"]] == [
        crossing["points_px"], line["points_px"]]
    assert client.ok("POST", f"{base}/verify", {})["replay_identical"]


def test_browser_line_feedback_pending_failure_and_no_duplicates(app, tmp_path):
    """In a real browser (``tests/e2e/drive_artwork_pending.mjs``; the server's answers are held or
    failed through the DevTools Fetch domain, so the states are observed deterministically)."""

    import subprocess

    from test_l4_app import BROWSERS, ROOT, _node_ok

    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    client, workspace, _ = app
    name = _import(client, "&plan=0")
    shots = ROOT / "artifacts" / "artwork_pending_v1"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_artwork_pending.mjs"), client.base + "/", str(browser), "-",
                          str(shots), name], capture_output=True, text=True, encoding="utf-8", timeout=900)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    r = json.loads(lines[-1])
    (shots / "drive_result.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    assert r["ok"], r.get("error", "") + "\n" + run.stderr[-3000:]
    assert r["console_errors"] == []

    # While the preview is held: the stroke is on the canvas at once (local), marked as in progress,
    # nothing is shown as applied, and a second line is not sent.
    held = r["preview_held"]
    assert held["pending"].startswith("Previewing line") and held["ghost_alpha"] > 0
    assert "/artwork/preview/" not in held["image"] and held["done_disabled"] and held["save_disabled"]
    assert held["unsaved"] == "All changes saved" and held["zoom"] == r["zoom_before"]
    assert r["requests_while_held"] == 0  # the second drag and the Done key sent nothing
    shown = r["preview_shown"]
    assert not shown["pending"] and "/artwork/preview/after.png" in shown["image"]
    assert shown["info"].startswith("Preview, not applied yet:") and not shown["ghost_alpha"]

    # Done while the add is held: in progress, then applied (unsaved) with the server's pixels.
    assert r["add_held"]["pending"].startswith("Applying Line") and r["add_held"]["done_disabled"]
    done = r["added"]
    assert done["unsaved"] == "1 unsaved change" and "/artwork/session/current.png" in done["image"]
    assert done["last"].startswith("Line 2 mm") and done["ok_mark"] and done["zoom"] == r["zoom_before"]

    # A failed add: message, no local stroke left, the artwork as on the server, the shape kept and
    # previewed again so it can be retried.
    failed = r["add_failed"]
    assert failed["last"].startswith("Not changed: injected failure") and not failed["pending"]
    assert failed["unsaved"] == "1 unsaved change" and not failed["ghost_alpha"]
    assert failed["shape"] and failed["info"].startswith("Preview, not applied yet:")
    assert r["retried"]["unsaved"] == "2 unsaved changes"

    # A failed preview: no stroke left on the canvas, the current artwork shown.
    pf = r["preview_failed"]
    assert pf["last"].startswith("Not drawn: injected failure") and not pf["ghost_alpha"] and not pf["pending"]
    assert "/artwork/preview/" not in pf["image"]

    # Exactly the edits made: two lines, saved once, replaying to the saved artwork.
    folder = workspace / name / "artwork"
    log = art.read_log(folder)
    assert [e["op"]["op"] for e in log["edits"]] == ["paint_stroke", "paint_stroke"]
    assert [e["op"]["points_px"] for e in log["edits"]] == r["saved_points"]
    assert art.verify(folder)["replay_identical"] and art.read_session(folder) is None


def test_browser_canvas_stays_put_while_issues_load_and_refresh(app):
    """In a real browser (``tests/e2e/drive_artwork_stable.mjs``): the Issues panel above the Artwork editor
    grows when it loads and changes when it re-checks after an edit.  Its answers are held and released
    while a stroke is in progress (pointer down): the first load, then the refresh after zooming,
    panning and scrolling the page.  The canvas does not move on screen, zoom / pan / scroll stay as
    the user left them, and every line is saved at exactly the intended pixels, once, in order."""

    import subprocess

    from test_l4_app import BROWSERS, ROOT, _node_ok

    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    client, workspace, _ = app
    name = _import(client, "&plan=0")
    shots = ROOT / "artifacts" / "artwork_stable_v1"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_artwork_stable.mjs"), client.base + "/", str(browser), "-",
                          str(shots), name], capture_output=True, text=True, encoding="utf-8", timeout=900)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    r = json.loads(lines[-1])
    (shots / "drive_result.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    assert r["ok"], r.get("error", "") + "\n" + run.stderr[-3000:]
    assert r["console_errors"] == []

    # First load during a stroke: the panel grew by hundreds of pixels; the canvas stayed where it was.
    load = r["load"]
    assert r["loading_placeholder"].startswith("Checking the sheets for issues")
    assert load["insp_after"] - load["insp_before"] > 300
    assert abs(load["top_after"] - load["top_before"]) < 1.5
    assert load["view_after"]["zoom"] == load["view_before"]["zoom"]
    # The refresh after an edit, after zoom, pan and a page scroll by the user: all kept, canvas still.
    ref = r["refresh"]
    assert abs(ref["top_after"] - ref["top_before"]) < 1.5
    assert ref["view_before"]["zoom"] != "fit" and ref["view_after"]["zoom"] == ref["view_before"]["zoom"]
    assert ref["view_after"]["stage"] == ref["view_before"]["stage"]  # pan
    assert abs(r["sequential_top"]["after"] - r["sequential_top"]["before"]) < 1.5
    assert r["final_view"]["zoom"] == ref["view_before"]["zoom"] and r["final_view"]["stage"] == ref["view_before"]["stage"]

    # Every stroke at exactly the intended pixels; nothing lost or duplicated.
    intended = [[[240, 200], [240, 330]], [[250, 220], [250, 300]], [[270, 220], [270, 300]], [[230, 220], [230, 300]]]
    for key in "ABCD":
        assert r[key]["points"] == r[key]["intended"], key
    assert r["saved_points"] == intended
    folder = workspace / name / "artwork"
    log = art.read_log(folder)
    assert [e["op"]["points_px"] for e in log["edits"]] == intended and [e["index"] for e in log["edits"]] == [1, 2, 3, 4]
    assert art.verify(folder)["replay_identical"] and art.read_session(folder) is None
