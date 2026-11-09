"""Fit Artwork to Canvas (``core/artwork.py`` ``fit_composition``): find the subject, enlarge and centre it.

All inputs are drawn here (project-owned): ``_pet`` is a flat-color "pet" in the upper left of a portrait
picture (so intake pads it with frame strips), drawn without anti-aliasing.  The fit is an ordinary
artwork edit: previewed, added to the working session, saved, replayed, undone, and planned only by the
explicit re-plan, from which the unchanged planner and validators build new sheets.
"""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from layerforge_l1.core import artwork as art
from layerforge_l1.core.intake import IntakeConfig, prepare_target, use_edited_target
from layerforge_l1.core.model import StackError, StackGrid

from test_l4_app import Client, start

SKY, BROWN, CREAM, DARK, NAVY = (135, 158, 159), (168, 112, 68), (238, 235, 223), (30, 31, 26), (33, 54, 97)
FRAME = "#FF00FF"
MPP = 200.0 / 512


def _pet(width: int = 384, height: int = 512) -> np.ndarray:
    """A pet (body, head, ears, eyes, nose, a ball) on a plain background, flat colors only."""

    img = np.full((height, width, 3), SKY, np.uint8)
    cv2.ellipse(img, (150, 170), (110, 60), 0, 0, 360, BROWN, -1, cv2.LINE_8)        # body
    cv2.circle(img, (95, 175), 48, CREAM, -1, cv2.LINE_8)                               # head
    for x in (62, 128):
        cv2.ellipse(img, (x, 175), (16, 36), 0, 0, 360, BROWN, -1, cv2.LINE_8)        # ears
    for x in (82, 108):
        cv2.circle(img, (x, 165), 6, DARK, -1, cv2.LINE_8)                              # eyes
    cv2.circle(img, (95, 192), 7, DARK, -1, cv2.LINE_8)                                 # nose
    cv2.circle(img, (215, 218), 22, NAVY, -1, cv2.LINE_8)                               # ball
    return img


def _target(image: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """The pet as intake makes it: 512 px square artwork, frame strips left and right."""

    prepared = prepare_target(image if image is not None else _pet(), None, StackGrid.for_planner(225.0, 1),
                              IntakeConfig(max_colors=8))
    return prepared["target_rgb"], prepared


def _colors(image: np.ndarray, frame: str | None = FRAME) -> set[str]:
    inside = art.artwork_area(image, frame)
    return {art.hex_color(tuple(int(v) for v in c)) for c in np.unique(image[inside].reshape(-1, 3), axis=0)}


# -- detection and the automatic fit ------------------------------------------------------------------
def test_subject_is_found_enlarged_centred_without_distortion_and_colors_kept():
    target, prepared = _target()
    frame = prepared["frame_color"]
    assert prepared["has_frame"] and frame == FRAME
    a = art.analyse_composition(target, frame, MPP)
    assert a["status"] == "READY" and a["background"]["kind"] == "color" and a["background"]["share"] >= 0.9
    background = a["background"]["background"]
    fg = art.foreground_mask(target, frame, background)
    assert a["foreground_box_px"] == art._bbox(fg) and a["components"] == 1
    op = art.check_op(a["op"])
    r0, c0, r1, c1 = op["source_box_px"]
    bh, bw = r1 - r0, c1 - c0
    after, mask, selection, change = art.plan_edit(target, op, frame, MPP)

    # One factor for both axes (aspect kept), the largest that leaves 8 % on each side of the limiting axis.
    s = op["scale"]
    assert s > 1.5 and change["scaled_size_px"] == [art.scaled_size(bh, s), art.scaled_size(bw, s)]
    sh, sw = change["scaled_size_px"]
    assert abs(sh / bh - sw / bw) < 2 / min(bh, bw)  # same factor to within one pixel of rounding
    margin_h, margin_w = (512 - sh) / 2, (512 - sw) / 2
    assert min(margin_h, margin_w) >= 0.08 * 512 - 1 and min(margin_h, margin_w) <= 0.08 * 512 + 1
    d0, e0, d1, e1 = change["dest_box_px"]
    assert abs((d0 + d1) / 2 - 256) <= 1 and abs((e0 + e1) / 2 - 256) <= 1  # centred
    assert change["foreground_box_after_px"] == change["dest_box_px"]  # the subject fills its box as before

    # Exact pixel copies: no new color, no anti-aliasing, the frame strips became background.
    assert _colors(after) == _colors(target) and not change["colors_added"] and not change["colors_removed"]
    assert change["frame_pixels_before"] > 0 and change["frame_pixels_after"] == 0
    assert change["regions_before"] == change["regions_after"] and change["subject_regions_changed"] == {}
    assert change["foreground_outside_box_px"] == 0 and change["subject_clipped_px"] == 0 and change["subject_skipped_px"] == 0
    rows, cols = np.arange(d0, d1), np.arange(e0, e1)
    src_r = r0 + np.floor((rows - d0 + 0.5) / s).astype(int)
    src_c = c0 + np.floor((cols - e0 + 0.5) / s).astype(int)
    assert np.array_equal(after[d0:d1, e0:e1], target[np.ix_(src_r, src_c)])  # nearest neighbour, nothing else
    outside = np.ones(after.shape[:2], bool)
    outside[d0:d1, e0:e1] = False
    assert np.all(after[outside] == np.asarray(art.parse_hex(background), np.uint8))
    assert selection["region_sha256"] == art.mask_sha256(mask) and mask.sum() == change["pixels"]
    assert change["foreground_mm_after"][1] > change["foreground_mm_before"][1] * 1.5


def test_margin_and_cut_edge_anchoring():
    target, prepared = _target()
    tight = art.analyse_composition(target, FRAME, MPP, margin=0.0)["op"]
    loose = art.analyse_composition(target, FRAME, MPP, margin=0.2)["op"]
    assert tight["scale"] > loose["scale"] > 1
    # A subject cut off by the original image edge: it touches that side; anchoring keeps it there.
    cut = _pet()
    cut[:, :40] = cut[:, 40:41]  # the head now runs into the left border
    target, prepared = _target(cut)
    a = art.analyse_composition(target, prepared["frame_color"], MPP)
    assert "left" in a["touches_edges"]
    anchored = art.analyse_composition(target, prepared["frame_color"], MPP, anchor_cut_edges=True)
    assert anchored["op"]["offset_px"][1] == 0 and anchored["op"]["scale"] >= a["op"]["scale"]
    with pytest.raises(art.ArtworkError, match="margin"):
        art.analyse_composition(target, FRAME, MPP, margin=0.5)


def test_ambiguous_background_and_separate_pieces_are_not_guessed():
    # Border split between two colors (sky above, sea below): no automatic fit; manual framing works.
    img = np.full((512, 512, 3), SKY, np.uint8)
    img[300:] = NAVY
    cv2.circle(img, (256, 200), 40, BROWN, -1, cv2.LINE_8)
    a = art.analyse_composition(img, None, MPP)
    assert a["status"] == "AMBIGUOUS_BACKGROUND" and a["op"] is None and a["manual"]["background"] is None
    manual = art.check_op({"op": "fit_composition", "source_box_px": [150, 200, 250, 312], "scale": 2.0,
                           "offset_px": [100, 144], "background": "#879E9F"})
    after, _, _, change = art.plan_edit(img, manual, None, MPP)
    assert change["foreground_outside_box_px"] > 0  # the sea outside the box is not kept: reported
    assert _colors(after, None) <= _colors(img, None)

    # Two separate pieces: the box covering both is offered as a manual start, nothing is applied.
    two = np.full((512, 512, 3), SKY, np.uint8)
    cv2.circle(two, (150, 200), 30, BROWN, -1, cv2.LINE_8)
    cv2.circle(two, (330, 260), 20, NAVY, -1, cv2.LINE_8)
    b = art.analyse_composition(two, None, MPP)
    assert b["status"] == "MULTIPLE_FOREGROUND_REGIONS" and b["op"] is None and b["components"] == 2
    assert b["manual"]["source_box_px"] == [170, 120, 281, 351]


def test_transparent_subject_keeps_the_frame_as_background():
    img = np.zeros((512, 512, 4), np.uint8)
    cv2.circle(img, (200, 200), 60, (*BROWN, 255), -1, cv2.LINE_8)
    cv2.circle(img, (200, 200), 15, (*CREAM, 255), -1, cv2.LINE_8)
    prepared = prepare_target(img[..., :3], img[..., 3] >= 128, StackGrid.for_planner(225.0, 1), IntakeConfig(max_colors=4))
    target, frame = prepared["target_rgb"], prepared["frame_color"]
    a = art.analyse_composition(target, frame, MPP)
    assert a["background"]["kind"] == "frame" and a["status"] == "READY"
    after, _, _, change = art.plan_edit(target, art.check_op(a["op"]), frame, MPP)
    inside = art.artwork_area(after, frame)
    assert change["frame_pixels_after"] > 0 and _colors(after, frame) == _colors(target, frame)
    assert int(inside.sum()) > int(art.artwork_area(target, frame).sum())  # the subject is larger


def test_scaling_up_keeps_every_region_and_down_reports_losses():
    rng = np.random.default_rng(4)
    labels = rng.integers(0, 3, (12, 16)).repeat(5, 0).repeat(5, 1)
    palette = np.array([BROWN, CREAM, NAVY], np.uint8)
    img = np.full((256, 256, 3), SKY, np.uint8)  # room for the box at twice its size
    img[30:90, 20:100] = palette[labels]
    for s in (1.0, 1.37, 1.5, 2.0):
        op = art.check_op({"op": "fit_composition", "source_box_px": [30, 20, 90, 100], "scale": s,
                           "offset_px": [0, 0], "background": "#879E9F"})
        before, after = art._region_counts(img, None), art._region_counts(art.compose(img, None, op)[0], None)
        assert {k: v for k, v in before.items() if k != "#879E9F"} == {k: v for k, v in after.items() if k != "#879E9F"}
    small = art.check_op({"op": "fit_composition", "source_box_px": [30, 20, 90, 100], "scale": 0.3,
                          "offset_px": [0, 0], "background": "#879E9F"})
    assert art.plan_edit(img, small, None, MPP)[3]["scale"] == 0.3  # allowed manually; the app warns below 100 %
    off = art.check_op({"op": "fit_composition", "source_box_px": [30, 20, 90, 100], "scale": 2.0,
                        "offset_px": [0, 150], "background": "#879E9F"})
    assert art.plan_edit(img, off, None, MPP)[3]["subject_clipped_px"] > 0  # part of it leaves the canvas


# -- clipping: measured on the exact mapping of the edit ------------------------------------------------
def _block_scene() -> np.ndarray:
    """A 200 x 200 canvas with a 40 x 60 subject (two colors, one-pixel stripes) at rows 50..90, cols 30..90."""

    img = np.full((200, 200, 3), SKY, np.uint8)
    img[50:90, 30:90] = BROWN
    img[50:90:2, 30:90] = NAVY  # thin stripes: a reduction skips some of them
    return img


def _fit(box, scale, offset, **extra):
    return art.check_op({"op": "fit_composition", "source_box_px": box, "scale": scale, "offset_px": offset,
                         "background": "#879E9F", **extra})


def _clipped_by_padding(img, op):
    """Independent count: compose onto a canvas padded by 400 px on every side and count the subject
    pixels that land outside the original canvas."""

    pad = 400
    big = np.full((img.shape[0] + 2 * pad, img.shape[1] + 2 * pad, 3), SKY, np.uint8)
    big[pad:-pad, pad:-pad] = img
    r0, c0, r1, c1 = op["source_box_px"]
    moved = {**op, "source_box_px": [r0 + pad, c0 + pad, r1 + pad, c1 + pad],
             "offset_px": [op["offset_px"][0] + pad, op["offset_px"][1] + pad]}
    after = art.compose(big, None, moved)[0]
    fg = np.any(after != np.asarray(SKY, np.uint8), axis=2)
    inside = np.zeros_like(fg)
    inside[pad:-pad, pad:-pad] = True
    return int((fg & ~inside).sum())


def test_clipping_counts_inside_edge_contact_and_past_the_edge():
    img = _block_scene()
    box = [50, 30, 90, 90]
    # Fully inside, every scale: nothing clipped.  Below 100 % rows / columns are skipped, which is reported
    # separately and is not clipping (the earlier report called these "outside the canvas").
    for s, off in ((1.0, [80, 70]), (2.0, [10, 10]), (0.9, [60, 60]), (0.5, [0, 0])):
        info = art.compose(img, None, _fit(box, s, off))[1]
        assert info["subject_clipped_px"] == 0 and info["subject_clipped_source_px"] == 0 and info["clipped_edges"] == []
        assert (info["subject_skipped_px"] > 0) == (s < 1)
    # Exact edge contact on every side (scale 1: 40 x 60 in each corner): still nothing clipped.
    for off in ([0, 0], [160, 140], [0, 140], [160, 0]):
        info = art.compose(img, None, _fit(box, 1.0, off))[1]
        assert info["subject_clipped_px"] == 0 and info["clipped_edges"] == []
    # One pixel past an edge: exactly that row / column of the subject is clipped, on that edge.
    for off, edge, n in (([80, 141], "right", 40), ([161, 70], "bottom", 60), ([-1, 70], "top", 60), ([80, -1], "left", 40)):
        op = _fit(box, 1.0, off)
        info = art.compose(img, None, op)[1]
        assert info["subject_clipped_px"] == n == _clipped_by_padding(img, op) and info["clipped_edges"] == [edge]
        loss, mask = art.subject_loss(img, None, op)
        assert int(mask.sum()) == n == loss["subject_clipped_source_px"]
    # Enlarged and pushed past a corner: the count equals the independent padded composition, and the mask
    # is exactly the source pixels none of whose copies stay on the canvas.
    s, oy, ox = 2.5, 120, 90
    op = _fit(box, s, [oy, ox])
    info = art.compose(img, None, op)[1]
    assert info["subject_clipped_px"] == _clipped_by_padding(img, op) > 0
    assert sorted(info["clipped_edges"]) == ["bottom", "right"]
    _, mask = art.subject_loss(img, None, op)
    sh, sw = info["scaled_size_px"]
    dest_rows = {r: [oy + d for d in range(sh) if min(50 + int(np.floor((d + 0.5) / s)), 89) == r] for r in range(50, 90)}
    dest_cols = {c: [ox + d for d in range(sw) if min(30 + int(np.floor((d + 0.5) / s)), 89) == c] for c in range(30, 90)}
    expected = np.zeros_like(mask)
    for r in range(50, 90):
        for c in range(30, 90):
            expected[r, c] = all(y >= 200 for y in dest_rows[r]) or all(x >= 200 for x in dest_cols[c])
    assert np.array_equal(mask, expected)


def test_source_cropping_is_told_apart_from_new_clipping():
    cut = _pet()
    cut[:, :40] = cut[:, 40:41]  # the head runs into the left border of the original picture
    target, prepared = _target(cut)
    frame = prepared["frame_color"]
    a = art.analyse_composition(target, frame, MPP)
    info = art.plan_edit(target, art.check_op(a["op"]), frame, MPP)[3]
    assert list(info["source_cropped_edges"]) == ["left"] and info["source_cropped_edges"]["left"] > 0
    assert info["subject_clipped_px"] == 0 and info["clipped_edges"] == []  # the automatic fit cuts nothing new
    pushed = art.check_op({**a["op"], "offset_px": [a["op"]["offset_px"][0], -30]})
    info = art.plan_edit(target, pushed, frame, MPP)[3]
    assert info["clipped_edges"] == ["left"] and info["subject_clipped_px"] > 0  # new clipping, reported as such
    assert list(info["source_cropped_edges"]) == ["left"]  # the original crop is still reported, separately
    # The uncut pet touches no edge of its picture: nothing is reported as cropped in the source.
    plain, prep2 = _target()
    a2 = art.analyse_composition(plain, prep2["frame_color"], MPP)
    assert art.plan_edit(plain, art.check_op(a2["op"]), prep2["frame_color"], MPP)[3]["source_cropped_edges"] == {}


def test_clipping_needs_explicit_approval_and_approval_is_kept_in_the_op(tmp_path):
    img = _block_scene()
    folder = tmp_path / "artwork"
    art.start(folder, img, {"stage": "test"}, None, MPP)
    inside = _fit([50, 30, 90, 90], 1.0, [80, 70])
    assert "accept_clipping" not in inside  # ops without approval look exactly as before (same replay hashes)
    off = _fit([50, 30, 90, 90], 1.0, [80, 160])
    with pytest.raises(art.ArtworkError, match="off the canvas"):
        art.session_add(folder, off)
    with pytest.raises(art.ArtworkError, match="off the canvas"):
        art.apply(folder, off)
    approved = _fit([50, 30, 90, 90], 1.0, [80, 160], accept_clipping=True)
    assert approved["accept_clipping"] is True
    _, entry, _ = art.session_add(folder, approved)
    assert entry["change"]["subject_clipped_px"] == 20 * 40 and entry["change"]["accept_clipping"]
    art.session_save(folder)
    assert art.verify(folder)["replay_identical"]


def test_fit_endpoint_warnings_overlay_and_apply_follow_the_current_framing(app):
    from PIL import Image

    client, workspace, tmp_path = app
    name = _import(client, tmp_path, plan=False)
    url = f"/api/projects/{name}/artwork/fit"
    auto = client.ok("POST", url, {})
    op = auto["op"]
    box = op["source_box_px"]
    bh, bw = box[2] - box[0], box[3] - box[1]
    assert auto["warnings"] == [] and auto["clipping"]["new_clipped_px"] == 0 and auto["can_apply"]

    def manual(s, off, **kw):
        return client.ok("POST", url, {"manual": {"source_box_px": box, "scale": s, "offset_px": off,
                                                  "background": op["background"], **kw}})

    # Below 100 %, fully inside: only the reduction warning, nothing clipped, Apply allowed.
    small = manual(0.6, [100, 100])
    assert small["clipping"]["new_clipped_px"] == 0 and small["clipping"]["skipped_px"] > 0 and small["can_apply"]
    assert len(small["warnings"]) == 1 and "below 100 %" in small["warnings"][0] and "not cut off" in small["warnings"][0]
    # At 100 %, against the bottom-right edge exactly: no warning at all (nothing left over from before).
    edge = manual(1.0, [512 - bh, 512 - bw])
    assert edge["warnings"] == [] and edge["clipping"]["new_clipped_px"] == 0 and edge["can_apply"]
    # Ten pixels past the right edge: clipped, a red overlay of exactly the lost source pixels, Apply refused.
    past = manual(1.0, [512 - bh, 512 - bw + 10])
    clip = past["clipping"]
    assert clip["new_clipped_px"] > 0 and clip["clipped_edges"] == ["right"] and clip["needs_approval"]
    assert not past["can_apply"] and any("cut off past the right edge" in w for w in past["warnings"])
    overlay = np.asarray(Image.open(workspace / name / "artwork" / "preview" / "clipped.png"))
    red = overlay[..., 3] > 0
    assert int(red.sum()) == clip["new_clipped_source_px"] > 0
    assert np.nonzero(red)[1].min() >= box[3] - 10  # within the last ten source columns of the subject
    status, refused = client.call("POST", f"/api/projects/{name}/artwork/session/add", {"op": past["op"]})
    assert status == 400 and "off the canvas" in refused["error"]["message"]
    # Approved explicitly: allowed, and the approval is part of the edit.
    ok = manual(1.0, [512 - bh, 512 - bw + 10], accept_clipping=True)
    assert ok["can_apply"] and ok["clipping"]["approved"] and ok["op"]["accept_clipping"] is True
    client.ok("POST", f"/api/projects/{name}/artwork/session/add", {"op": ok["op"]})


def test_fit_is_an_ordinary_logged_edit_replay_undo_and_redo(tmp_path):
    target, prepared = _target()
    folder = tmp_path / "artwork"
    art.start(folder, target, {"stage": "test"}, FRAME, MPP)
    op = art.check_op(art.analyse_composition(target, FRAME, MPP)["op"])
    fitted, entry, session = art.session_add(folder, op)
    assert entry["from"] == "composition" and len(session["ops"]) == 1
    assert np.array_equal(art.session_undo(folder)[0], target)
    assert np.array_equal(art.session_redo(folder)[0], fitted)
    recolor = art.check_op({"at_px": list(np.argwhere(np.all(fitted == CREAM, axis=2))[0]), "color": "#FFFFFF"})
    final, _, _ = art.session_add(folder, recolor)  # later edits work on the fitted artwork
    art.session_save(folder)
    log = art.read_log(folder)
    assert [e["op"]["op"] for e in log["edits"]] == ["fit_composition", "recolor_region"]
    assert art.verify(folder)["replay_identical"] and np.array_equal(art.replay(folder, log), final)
    assert art.composition_ops(log) == [log["edits"][0]["op"]]
    assert np.array_equal(art.revert(folder, 0)[0], target)


def test_planning_accepts_the_recomposed_frame_only_with_the_fit_and_moves_intake_evidence():
    target, prepared = _target()
    op = art.check_op(art.analyse_composition(target, FRAME, MPP)["op"])
    fitted = art.compose(target, FRAME, op)[0]
    with pytest.raises(StackError, match="frame padding"):
        use_edited_target(prepared, fitted)  # an ordinary recolor may never move the frame
    used = use_edited_target(prepared, fitted, composition_ops=[op])
    assert used["has_frame"] is False and used["artwork_mask"].all() and np.array_equal(used["target_rgb"], fitted)
    assert used["measurements"]["recomposed_by_artwork_edits"] == 1
    assert {c["hex"] for c in used["measurements"]["colors"]} == _colors(target)
    moved = art.remap(prepared["unmatched_mask"], op, False)
    assert np.array_equal(used["unmatched_mask"], moved)


# -- app: before planning, Save, explicit re-plan, and an already planned project ------------------------
@pytest.fixture()
def app(tmp_path):
    server, client = start(tmp_path / "workspace")
    yield client, tmp_path / "workspace", tmp_path
    server.shutdown()
    server.server_close()


def _import(client: Client, tmp_path, plan: bool) -> str:
    from PIL import Image
    import io

    buffer = io.BytesIO()
    Image.fromarray(_pet()).save(buffer, format="PNG")
    query = "" if plan else "&plan=0"
    name = client.ok("POST", f"/api/projects?filename=pet.png&scale=1{query}", raw=buffer.getvalue())["project"]
    result = client.wait({"id": client.state(name)["job"]["id"]})
    assert result["status"] == "done", result["error"]
    return name


def _fit_and_save(client: Client, name: str) -> dict:
    base = f"/api/projects/{name}/artwork"
    fit = client.ok("POST", f"{base}/fit", {})
    assert fit["analysis"]["status"] == "READY" and fit["can_apply"] and fit["op"]["mode"] == "auto"
    assert client.ok("GET", fit["images"]["before_after"])[:4] == b"\x89PNG"
    assert fit["change"]["colors_added"] == [] and fit["change"]["subject_regions_changed"] == {}
    added = client.ok("POST", f"{base}/session/add", {"op": fit["op"]})
    assert added["unsaved"] == 1
    assert client.ok("POST", f"{base}/session/undo", {})["unsaved"] == 0
    assert client.ok("POST", f"{base}/session/redo", {})["unsaved"] == 1
    status, refused = client.call("POST", f"/api/projects/{name}/artwork/replan", {})
    assert status == 400 and "unsaved" in refused["error"]["message"]  # planning waits for Save Changes
    assert client.ok("POST", f"{base}/session/save", {})["saved"] == 1
    assert client.ok("POST", f"{base}/verify", {})["replay_identical"]
    return fit


def test_fit_before_planning_save_reload_and_replan_uses_the_fitted_artwork(app):
    from layerforge_l1.core.model import file_sha256

    client, workspace, tmp_path = app
    name = _import(client, tmp_path, plan=False)
    state = client.state(name)
    assert state["proposal"]["status"] == "ARTWORK_READY_NOT_PLANNED"
    source = workspace / name / state["record"]["source"]["file"]
    source_sha = file_sha256(source)
    planning_before = json.loads((workspace / name / "proposal" / "proposal.json").read_text())["planning_config"]
    fit = _fit_and_save(client, name)
    fitted = art.load_rgb(workspace / name / "artwork" / "current.png")
    assert client.state(name)["artwork"]["edits"][0]["op"]["op"] == "fit_composition"  # history, survives reload

    client.run(name, "artwork/replan")
    proposal = json.loads((workspace / name / "proposal" / "proposal.json").read_text())
    assert proposal["status"] == "PROPOSAL_READY_FOR_REVIEW" and proposal["frame_color"] is None
    planned = art.load_rgb(workspace / name / "proposal" / "intake" / "target.png")
    assert np.array_equal(planned, fitted)  # re-planning used the transformed artwork
    assert proposal["composition_ops"] == [fit["op"]] and "ARTWORK_RECOMPOSED" in {f["code"] for f in proposal["flags"]}
    assert proposal["planning_config"]["outer_sheet_mm"] == planning_before["outer_sheet_mm"]  # sheet size unchanged
    validation = json.loads((workspace / name / "proposal" / "candidates" / proposal["selected"] / "validation.json").read_text())
    assert validation["status"] == "PASS" and validation["reconstruction"]["mismatched_pixels"] == 0
    assert validation["outer_sheet_mm"] == [225.0, 225.0]
    assert file_sha256(source) == source_sha  # the uploaded image is never changed
    again = client.ok("POST", f"/api/projects/{name}/artwork/fit", {})
    assert again["analysis"]["status"] == "ALREADY_FITTED" and not again["can_apply"]


def test_fit_on_a_planned_project_blocks_export_until_replanned(app):
    client, workspace, tmp_path = app
    name = _import(client, tmp_path, plan=True)
    before = json.loads((workspace / name / "proposal" / "proposal.json").read_text())
    assert before["status"] == "PROPOSAL_READY_FOR_REVIEW" and before["frame_color"] == FRAME
    old_stack = (workspace / name / "proposal" / "candidates" / before["selected"] / "stack" / "sheet_stack.json").read_bytes()
    _fit_and_save(client, name)
    assert client.state(name)["artwork"]["differs_from_planned"]
    status, refused = client.call("POST", f"/api/projects/{name}/kits", {})
    assert status == 400 and "artwork was edited" in refused["error"]["message"]  # the old sheets are not scaled or exported
    assert (workspace / name / "proposal" / "candidates" / before["selected"] / "stack" / "sheet_stack.json").read_bytes() == old_stack
    client.run(name, "artwork/replan")
    after = json.loads((workspace / name / "proposal" / "proposal.json").read_text())
    assert after["status"] == "PROPOSAL_READY_FOR_REVIEW" and after["composition_ops"]
    assert (workspace / name / "attempts" / "attempt_01_artwork" / "proposal" / "proposal.json").is_file()  # kept
    assert not client.state(name)["artwork"].get("differs_from_planned")


def test_browser_fit_card_mouse_move_resize_bounds_apply_and_save(app):
    """The Fit card in a real browser (``tests/e2e/drive_fit.mjs``), mouse only: only the live preview and the
    buttons Fit automatically / Apply fit; drag the subject to move it; drag the bottom-left handle to enlarge
    and to shrink it (aspect ratio kept, top-right corner fixed); drags past the canvas stop at its edges;
    Fit automatically restores the detected fit; Apply adds one artwork edit; Save; nothing is planned."""

    import subprocess

    from test_l4_app import BROWSERS, ROOT, _node_ok

    client, workspace, tmp_path = app
    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    name = _import(client, tmp_path, plan=False)
    shots = ROOT / "artifacts" / "artwork_fit_v1" / "screens"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_fit.mjs"), client.base + "/", str(browser), name,
                          str(shots)], capture_output=True, text=True, encoding="utf-8", timeout=600)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    r = json.loads(lines[-1])
    assert r["ok"], r.get("error", "") + " console: " + str(r.get("console_errors")) + " " + run.stderr[-3000:]
    assert r["console_errors"] == []
    # Simplified card: no numeric fields, only the two actions (and Close).
    assert r["controls"]["inputs"] == 0 and r["controls"]["buttons"] == ["Close", "Fit automatically", "Apply fit"]
    assert r["controls"]["status"] == "Subject found" and r["auto"]["op"]["mode"] == "auto"
    h, w = r["auto"]["ap"]
    aspect = lambda q: q["w"] / q["h"]  # noqa: E731
    base = aspect(r["auto"]["rect"])

    move = r["move"]  # dragged up 60 and right 40: moved by exactly the drag, same size, checked by the server
    dy, dx = move["requested"]["dy"], move["requested"]["dx"]  # the drag in canvas pixels, from whole screen pixels
    assert abs(dy + 60) <= 1.5 and abs(dx - 40) <= 1.5
    assert move["after"]["top"] == move["before"]["top"] + round(dy) and move["after"]["left"] == move["before"]["left"] + round(dx)
    assert (move["after"]["w"], move["after"]["h"]) == (move["before"]["w"], move["before"]["h"])
    assert move["server_op"]["mode"] == "manual" and move["live"] == move["after"]  # the live preview showed it while dragging

    grow = r["grow"]  # bottom-left handle out: larger, the top-right corner stays, proportions kept
    assert grow["scale"] > grow["scale_before"] and grow["after"]["w"] > grow["before"]["w"]
    assert grow["after"]["top"] == grow["before"]["top"] and abs(grow["after"]["right"] - grow["before"]["right"]) <= 1
    assert abs(aspect(grow["after"]) - base) < 0.02 and grow["live"] == grow["after"]
    shrink = r["shrink"]
    assert shrink["scale"] < shrink["scale_before"] and abs(aspect(shrink["after"]) - base) < 0.02
    assert shrink["after"]["top"] == shrink["before"]["top"] and abs(shrink["after"]["right"] - shrink["before"]["right"]) <= 1

    for q in (r["bounds"]["pushed"], r["bounds"]["huge"]):  # never outside the artwork aperture
        assert q["top"] >= 0 and q["left"] >= 0 and q["bottom"] <= h and q["right"] <= w
    assert r["bounds"]["pushed"]["top"] == 0 and r["bounds"]["pushed"]["right"] == w  # stopped at the edges
    huge = r["bounds"]["huge"]  # resize stopped at the left or bottom edge (within the 1 px of whole-pixel rounding)
    assert huge["left"] <= 1 or huge["bottom"] >= h - 1
    assert r["bounds"]["warnings"] == 0  # nothing cut off
    for key in ("pushed_clipping", "clipping"):  # edge contact (top and right, then left or bottom) is not clipping
        assert r["bounds"][key]["new_clipped_px"] == 0 and r["bounds"][key]["clipped_edges"] == []
    assert not r["bounds"]["ui"]["overlay"] and not r["bounds"]["ui"]["apply_disabled"]

    # Clipping is checked again after every drag, resize and Fit automatically; never a stale warning.
    for key in ("auto", "move"):
        assert r[key]["clipping"]["new_clipped_px"] == 0 and r[key]["ui"]["warnings"] == [] and not r[key]["ui"]["overlay"]
    assert r["move"]["during"]["warnings"] == [] and r["move"]["during"]["apply_disabled"] and r["move"]["during_stale"]
    small = r["small"]  # below 100 %: only the reduction warning, nothing cut off, Apply allowed
    assert small["scale"] < 1 and small["clipping"]["new_clipped_px"] == 0 and small["clipping"]["skipped_px"] > 0
    assert len(small["ui"]["warnings"]) == 1 and "below 100 %" in small["ui"]["warnings"][0]
    assert not small["ui"]["overlay"] and not small["ui"]["apply_disabled"]
    large = r["large_again"]  # back above 100 %: the reduction warning is gone, also while dragging
    assert large["scale"] >= 1 and large["ui"]["warnings"] == [] and large["during"]["warnings"] == []
    assert large["during_stale"] and large["clipping"]["new_clipped_px"] == 0
    assert r["auto_again_ui"]["warnings"] == [] and not r["auto_again_ui"]["apply_disabled"]

    assert r["auto_again"] == r["auto"]["op"]  # Fit automatically restores the detected fit
    assert r["after_apply"]["card_closed"] and "Fit artwork to canvas" in r["after_apply"]["history"]
    assert r["saved"]["replan_button"] and not r["saved"]["job_running"]  # nothing planned automatically
    log = art.read_log(workspace / name / "artwork")
    assert [e["op"]["op"] for e in log["edits"]] == ["fit_composition"]
    assert log["edits"][0]["op"] == art.check_op(r["applied_op"])  # Apply created exactly the previewed edit
    assert art.verify(workspace / name / "artwork")["replay_identical"]
    assert client.state(name)["proposal"]["status"] == "ARTWORK_READY_NOT_PLANNED"
