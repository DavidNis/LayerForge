"""Simplify tiny tips (``core/tips.py``, the ``simplify_tips`` artwork edit, the Artwork editor card).

A conservative suggestion: at the places where the printability check reports a cutout narrower than the
minimum *because of the image*, the narrow tip of the revealed color region is filled with the one color
around it.  Original synthetic fixture (drawn here): a black band with three pointed spikes (tips), a long
whisker (too large to be a tiny tip), a short free-standing line (a whole thin feature, not a tip), a small
teardrop eye (a small shape: never changed), a pink wedge pointing out of the face (a tip) and a pink wedge
along the band whose point lies between black and peach (a three-color junction: left for the user).
The places are given as issues in the inspector's form (on Chupa they come from the real checks).
"""

from __future__ import annotations

import io
import json

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw
from scipy import ndimage

from layerforge_l1.core import artwork as art
from layerforge_l1.core import tips as tp

from test_artwork_recolor import app  # noqa: F401 - shared fixture

CREAM, PEACH, BLACK, PINK = (247, 241, 224), (237, 183, 129), (20, 20, 20), (232, 123, 121)
MPP = 0.1953125  # 112.5 mm sheet, 512 px (the server test imports it at this size)
SITES = [([262, 154, 280, 167], BLACK), ([262, 250, 280, 263], BLACK), ([262, 346, 280, 359], BLACK),  # spikes
         ([405, 70, 452, 120], BLACK),   # whisker
         ([441, 278, 448, 294], BLACK),  # free-standing short line
         ([164, 196, 182, 207], BLACK),  # teardrop eye
         ([200, 420, 214, 454], PINK),   # wedge out of the face
         ([399, 300, 410, 332], PINK)]   # wedge along the band: a three-color junction
FOUR = ndimage.generate_binary_structure(2, 1)


def fixture() -> np.ndarray:
    im = Image.new("RGB", (512, 512), CREAM)
    d = ImageDraw.Draw(im)
    d.ellipse([80, 120, 432, 472], fill=PEACH)
    d.rectangle([120, 300, 392, 400], fill=BLACK)
    for x in (160, 256, 352):
        d.polygon([(x - 6, 300), (x + 6, 300), (x, 262)], fill=BLACK)
    d.line([(124, 398), (70, 452)], fill=BLACK, width=3)
    d.line([(280, 444), (292, 444)], fill=BLACK, width=3)
    d.ellipse([194, 180, 208, 194], fill=BLACK)
    d.polygon([(198, 182), (204, 182), (201, 166)], fill=BLACK)
    d.polygon([(372, 196), (372, 216), (452, 208)], fill=PINK)
    d.rectangle([200, 401, 300, 440], fill=PINK)
    d.polygon([(300, 401), (300, 409), (330, 401)], fill=PINK)
    return np.array(im)


def hexc(rgb) -> str:
    return "#%02X%02X%02X" % tuple(rgb)


def issues_at(sites) -> list[dict]:
    """The sites in the inspector's issue form (image-caused narrow cutouts)."""

    return [{"id": f"issue_{n:03d}", "group": f"g{n}", "kind": "narrow_slot_or_gap", "artwork_box_px": list(box),
             "feature": {"cause": "image", "threshold_mm": 0.8, "regions": [{"color": c if isinstance(c, str) else hexc(c), "narrow": True}]}}
            for n, (box, c) in enumerate(sites, 1)]


def apply_tips(image, tips):
    out = image.copy()
    for tip in tips:
        rows, cols = np.array(tip["pixels"]).T
        out[rows, cols] = art.parse_hex(tip["fill"])
    return out


def narrow(mask, element):
    return mask & ~cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, element).astype(bool)


def test_suggestion_shortens_tips_and_keeps_details_lines_and_junctions():
    image = fixture()
    s = tp.suggest(image, tp.sites_from_issues(issues_at(SITES)), MPP, None, 0.8)
    by_site = {t["site"]: t for t in s["tips"]}
    # Sites are numbered by position: 1 eye, 2 wedge out of the face, 3-5 spikes, 6 junction, 7 whisker, 8 line.
    assert sorted(by_site) == [2, 3, 4, 5]
    for site in (3, 4, 5):
        assert by_site[site]["color"] == hexc(BLACK) and by_site[site]["fill"] == hexc(PEACH)
    assert by_site[2]["color"] == hexc(PINK) and by_site[2]["fill"] == hexc(CREAM)
    kept = {k["site"]: k["why_kept"] for k in s["kept"] if k["pixels_count"] >= tp.MIN_TIP_PX}
    assert kept[1] == "part of a small shape (kept as a detail)"
    assert kept[6].startswith("between two other colors")
    assert kept[7] == "larger than a tiny tip"
    assert kept[8] == "a whole thin feature, not a tip of a wider shape"

    after = apply_tips(image, s["tips"])
    changed = np.any(after != image, axis=2)
    assert changed.sum() == sum(t["pixels_count"] for t in s["tips"])
    windows = np.zeros(changed.shape, bool)  # only inside the reported places' windows
    pad = 2 * tp.disc(0.8, MPP).shape[0]
    for box, _ in SITES:
        windows[max(box[0] - pad, 0):box[2] + pad, max(box[1] - pad, 0):box[3] + pad] = True
    assert not (changed & ~windows).any()
    pal = lambda im: {(c["hex"], c["regions_4"]) for c in art.palette_summary(im, None, MPP)}  # noqa: E731
    assert pal(after) == pal(image)  # same colors, same number of regions of each
    element = tp.disc(0.8, MPP)
    for rgb in (CREAM, PEACH, BLACK, PINK):  # no new part narrower than the minimum, anywhere
        before_n, after_n = narrow(np.all(image == rgb, axis=2), element), narrow(np.all(after == rgb, axis=2), element)
        assert not (after_n & ~before_n).any(), rgb
    # The eye and the junction are untouched.
    for box, _ in (SITES[5], SITES[7]):
        r0, c0, r1, c1 = box
        assert np.array_equal(after[r0 - 6:r1 + 6, c0 - 6:c1 + 6], image[r0 - 6:r1 + 6, c0 - 6:c1 + 6])


def test_any_selection_is_safe_and_applies_exactly(tmp_path):
    image = fixture()
    s = tp.suggest(image, tp.sites_from_issues(issues_at(SITES)), MPP, None, 0.8)
    tips = s["tips"]
    for k in range(len(tips)):  # each tip alone and each tip left out
        assert tp.check_selection(image, [tips[k]], MPP) is None
        assert tp.check_selection(image, tips[:k] + tips[k + 1:], MPP) is None
    folder = tmp_path / "artwork"
    art.start(folder, image, {"stage": "test"}, None, MPP)
    chosen = [t for t in tips if t["id"] != "t2"]
    op = art.check_op({"op": "simplify_tips", "tips": [{k: t[k] for k in ("id", "color", "fill", "pixels")} for t in chosen]})
    planned = art.planned_edit(image, op, None, MPP)
    after, entry, _ = art.session_add(folder, op, planned)
    assert np.array_equal(after, apply_tips(image, chosen))
    assert entry["change"]["pixels"] == sum(t["pixels_count"] for t in chosen) and entry["change"]["colors_removed"] == []
    assert entry["to"] == "mixed" and entry["change"]["to_colors"] == sorted({t["fill"] for t in chosen})
    undone, _ = art.session_undo(folder)
    assert np.array_equal(undone, image)
    redone, _ = art.session_redo(folder)
    assert np.array_equal(redone, after)
    saved, _ = art.session_save(folder)
    assert np.array_equal(saved, after) and art.verify(folder)["replay_identical"]
    assert np.array_equal(art.replay(folder, art.read_log(folder)), after)
    # A suggestion made on other pixels is refused, not applied somewhere else.
    with pytest.raises(art.ArtworkError, match="no longer matches"):
        art.plan_edit(after, op, None, MPP)


@pytest.mark.parametrize("bad, message", [
    ({"op": "simplify_tips", "tips": []}, "1 to"),
    ({"op": "simplify_tips", "tips": [{"color": "#000000", "fill": "#000000", "pixels": [[1, 1]]}]}, "differ"),
    ({"op": "simplify_tips", "tips": [{"color": "#000000", "fill": "#FFFFFF", "pixels": [["x", 1]]}]}, "row, column"),
    ({"op": "simplify_tips", "tips": [{"color": "nope", "fill": "#FFFFFF", "pixels": [[1, 1]]}]}, "#RRGGBB"),
])
def test_bad_tip_edits_are_refused(bad, message):
    with pytest.raises(art.ArtworkError, match=message):
        art.check_op(bad)
    op = art.check_op({"op": "simplify_tips", "tips": [{"color": hexc(CREAM), "fill": hexc(BLACK), "pixels": [[600, 1]]}]})
    with pytest.raises(art.ArtworkError, match="outside"):
        art.plan_edit(fixture(), op, None, MPP)


def _import_fixture(client, plan: bool = False) -> str:
    raw = io.BytesIO()
    Image.fromarray(fixture()).save(raw, "PNG")
    query = "filename=tips_fixture.png&scale=1&outer_mm=112.5" + ("" if plan else "&plan=0")
    name = client.ok("POST", f"/api/projects?{query}", raw=raw.getvalue())["project"]
    assert client.wait({"id": client.state(name)["job"]["id"]})["status"] == "done"
    return name


def test_server_suggestion_exclusion_apply_and_replay(app, monkeypatch):  # noqa: F811
    """Nothing changes until the edit is added; excluding a tip changes only that tip; the added edit is an
    ordinary logged edit (undo, redo, save, verify)."""

    from layerforge_l1.app.workspace import Workspace

    client, workspace, _ = app
    name = _import_fixture(client)
    monkeypatch.setattr(Workspace, "_tip_issues", lambda self, project: issues_at(SITES))
    folder = workspace / name / "artwork"
    target = art.load_rgb(workspace / name / "proposal" / "intake" / "target.png")
    base = f"/api/projects/{name}/artwork"
    r = client.ok("POST", f"{base}/simplify", {})
    assert len(r["tips"]) == 4 and r["op"] and r["changed_pixels"] == sum(t["pixels_count"] for t in r["tips"])
    assert r["changed_percent"] == round(100 * r["changed_pixels"] / target[..., 0].size, 4)
    assert np.array_equal(art.load_rgb(folder / "current.png"), target) and art.read_session(folder) is None  # nothing applied
    preview = art.load_rgb(workspace / name / "artwork" / "preview" / "after.png")
    assert np.array_equal(preview, apply_tips(target, r["tips"]))  # the "after" shown is exactly the result
    junction = next(k for k in r["kept"] if k["why_kept"].startswith("between two other colors"))
    r0, c0, r1, c1 = junction["box"]
    assert np.array_equal(preview[r0:r1, c0:c1], target[r0:r1, c0:c1])

    left_out = r["tips"][1]["id"]
    r2 = client.ok("POST", f"{base}/simplify", {"exclude": [left_out]})
    assert r2["excluded"] == [left_out] and [t["id"] for t in r2["op"]["tips"]] == [t["id"] for t in r["tips"] if t["id"] != left_out]
    assert r2["changed_pixels"] == r["changed_pixels"] - r["tips"][1]["pixels_count"]
    status, error = client.call("POST", f"{base}/simplify", {"exclude": "t1"})
    assert status == 400 and "list" in error["error"]["message"]

    added = client.ok("POST", f"{base}/session/add", {"op": r2["op"]})
    assert added["unsaved"] == 1 and added["change"]["pixels"] == r2["changed_pixels"]
    chosen = [t for t in r["tips"] if t["id"] != left_out]
    assert np.array_equal(art.load_rgb(folder / "session" / "current.png"), apply_tips(target, chosen))
    assert client.ok("POST", f"{base}/session/undo", {})["unsaved"] == 0
    assert client.ok("POST", f"{base}/session/redo", {})["unsaved"] == 1
    assert client.ok("POST", f"{base}/session/save", {})["saved"] == 1
    assert client.ok("POST", f"{base}/verify", {})["replay_identical"]
    assert np.array_equal(art.load_rgb(folder / "current.png"), apply_tips(target, chosen))
    edits = client.state(name)["artwork"]["edits"]
    assert [e["op"]["op"] for e in edits] == ["simplify_tips"] and edits[0]["selection"]["pixels"] == r2["changed_pixels"]
    again = client.ok("POST", f"{base}/simplify", {})  # on the new artwork only the left-out tip is still there
    assert [t["pixels_count"] for t in again["tips"]] == [r["tips"][1]["pixels_count"]]


def test_browser_simplify_tiny_tips(app, monkeypatch):  # noqa: F811
    """In a real browser (``tests/e2e/drive_artwork_tips.mjs``): the card lists the tips with numbers on the
    artwork, After / Before / Changes show the result, the current artwork and every changed pixel; Show
    zooms to a tip; a tip can be left out; Apply adds one edit (Undo / Redo / Save as usual)."""

    import subprocess

    from layerforge_l1.app.workspace import Workspace
    from test_l4_app import BROWSERS, ROOT, _node_ok

    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    client, workspace, _ = app
    name = _import_fixture(client, plan=True)  # the card is offered once sheets are planned
    monkeypatch.setattr(Workspace, "_tip_issues", lambda self, project: issues_at(SITES))
    target = art.load_rgb(workspace / name / "proposal" / "intake" / "target.png")
    shots = ROOT / "artifacts" / "simplify_tips_v1"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_artwork_tips.mjs"), client.base + "/", str(browser), "-",
                          str(shots), name], capture_output=True, text=True, encoding="utf-8", timeout=900)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    r = json.loads(lines[-1])
    (shots / "drive_result.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    assert r["ok"], r.get("error", "") + "\n" + run.stderr[-3000:]
    assert r["console_errors"] == []

    assert r["opened"]["summary"].startswith("4 of 4 tips") and r["opened"]["rows"] == 4
    assert r["opened"]["kept"].startswith("4 places left unchanged")
    assert r["views"]["after"].endswith("/artwork/preview/after.png") and "/artwork/preview/" not in r["views"]["before"]
    assert r["views"]["changes_magenta"] > 0 and r["views"]["after_magenta"] == 0  # changed pixels drawn only in Changes
    assert r["show"]["zoom"] != r["show"]["zoom_before"]
    assert r["excluded"]["summary"].startswith("3 of 4 tips") and r["excluded"]["apply"] == "Apply 3 changes"
    assert r["artwork_untouched_before_apply"]
    assert r["applied"]["unsaved"] == "1 unsaved change" and "Simplify 3 tiny tips" in r["applied"]["history"]
    assert not r["applied"]["card_open"]
    assert r["undo"] == "All changes saved" and r["redo"] == "1 unsaved change" and r["saved"] == "All changes saved"
    # Reopened after the saved edit: the cutouts are from the earlier sheets, and the card says so.
    assert r["reopened"]["stale"].startswith("Based on the cutouts found in the sheets planned before your latest edits")
    assert r["reopened"]["summary"].startswith("1 of 1 tip")  # the spike left out; the other three are done
    assert r["reopened"]["edited_rows"] == 0  # it is far from the pixels edited since planning
    folder = workspace / name / "artwork"
    log = art.read_log(folder)
    assert [e["op"]["op"] for e in log["edits"]] == ["simplify_tips"] and len(log["edits"][0]["op"]["tips"]) == 3
    assert art.verify(folder)["replay_identical"]
    assert np.array_equal(art.load_rgb(folder / "current.png"), apply_tips(target, log["edits"][0]["op"]["tips"]))


def test_concurrent_issue_checks_share_one_computation(app):  # noqa: F811
    """Opening a project, the Issues panel and Simplify tiny tips ask for the same check at once: it runs
    once and both get its result.  New inputs start a new check; a failed check is not cached."""

    import threading
    import time

    from layerforge_l1.app.workspace import Workspace

    client, workspace, _ = app
    name = _import_fixture(client, plan=True)
    ws = Workspace(workspace)
    real = Workspace._compute_issues
    calls, fail = [], []

    def slow(self, *a, **k):
        calls.append(1)
        time.sleep(0.6)  # long enough for the other requests to arrive while it runs
        if fail:
            raise RuntimeError("check failed")
        return real(self, *a, **k)

    ws._compute_issues = slow.__get__(ws)
    out, errors = [], []

    def ask():
        try:
            out.append(ws.issues(name))
        except RuntimeError as error:
            errors.append(str(error))

    def together(n=3):
        threads = [threading.Thread(target=ask) for _ in range(n)]
        for t in threads:
            t.start()
            time.sleep(0.05)
        for t in threads:
            t.join()

    together()
    assert len(calls) == 1 and len(out) == 3 and not errors
    assert all(o["issues"] == out[0]["issues"] for o in out)
    ws.issues(name)
    assert len(calls) == 1  # cached
    proposal = workspace / name / "proposal" / "proposal.json"
    proposal.write_bytes(proposal.read_bytes())  # new input stamp: checked again
    time.sleep(0.01)
    fail.append(1)
    out.clear()
    together()
    assert len(calls) == 2 and errors == ["check failed"] * 3 and not out  # one failing check, the same error for all
    fail.clear()
    ws.issues(name)
    assert len(calls) == 3  # a failure is not cached


def test_stale_planning_is_shown_and_changes_are_checked_on_the_current_artwork(app, monkeypatch):  # noqa: F811
    from layerforge_l1.app.workspace import Workspace

    client, workspace, _ = app
    name = _import_fixture(client, plan=True)
    monkeypatch.setattr(Workspace, "_tip_issues", lambda self, project: issues_at(SITES))
    base = f"/api/projects/{name}/artwork"
    fresh = client.ok("POST", f"{base}/simplify", {})
    assert fresh["planned"] == {"stale": False, "edited_px": 0} and not any(t["edited_since_planning"] for t in fresh["tips"])

    far = {"op": "paint_stroke", "points_px": [[20, 20], [20, 60]], "width_mm": 1.0, "color": hexc(PINK), "source": "palette"}
    client.ok("POST", f"{base}/session/add", {"op": far})  # an unsaved edit far from every tip
    r = client.ok("POST", f"{base}/simplify", {})
    assert r["planned"]["stale"] and r["planned"]["edited_px"] > 0
    assert not any(t["edited_since_planning"] for t in r["tips"]) and len(r["tips"]) == len(fresh["tips"])

    spike = next(t for t in r["tips"] if t["color"] == hexc(BLACK))
    r0, c0, r1, c1 = spike["box"]
    near = {"op": "paint_stroke", "points_px": [[r0 - 4, c0 - 6], [r0 - 4, c0 - 1]], "width_mm": 0.5, "color": hexc(PINK), "source": "palette"}
    client.ok("POST", f"{base}/session/add", {"op": near})  # an edit next to that tip
    r2 = client.ok("POST", f"{base}/simplify", {})
    flagged = [t for t in r2["tips"] if t["edited_since_planning"]]
    assert flagged and all(t["box"] == spike["box"] for t in flagged)
    working = art.load_rgb(workspace / name / "artwork" / "session" / "current.png")
    for tip in r2["tips"]:  # every proposed pixel still has its color in the current artwork
        rows, cols = np.array(tip["pixels"]).T
        assert np.all(working[rows, cols] == art.parse_hex(tip["color"]))

    # A suggestion made before an edit that changed its pixels is refused, nothing is applied.
    over = {"op": "paint_stroke", "points_px": [[r0, c0], [r1, c1]], "width_mm": 1.5, "color": hexc(PINK), "source": "palette"}
    client.ok("POST", f"{base}/session/add", {"op": over})
    status, error = client.call("POST", f"{base}/session/add", {"op": r2["op"]})
    assert status == 400 and "no longer matches" in error["error"]["message"]
    assert client.state(name)["artwork"]["unsaved"]["count"] == 3
    r3 = client.ok("POST", f"{base}/simplify", {})  # made again on the current artwork: applied and replayed exactly
    added = client.ok("POST", f"{base}/session/add", {"op": r3["op"]})
    assert added["unsaved"] == 4
    client.ok("POST", f"{base}/session/save", {})
    assert client.ok("POST", f"{base}/verify", {})["replay_identical"]
