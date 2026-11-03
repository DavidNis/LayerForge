"""Artwork editor: Line, Curve, Brush clipped to selected regions and Close gap (``core/artwork.py``).

Fill and Replace color are the region recolor of ``test_artwork_recolor`` (click a region, choose a
color); these tests cover the stroke ops: their exact pixels, the clip, the gap snapping (straight and curved), region
splitting by lines and curves, refusals, and that they are logged, replayed and undone like every other artwork edit.
"""

from __future__ import annotations

import numpy as np
import pytest

from layerforge_l1.core import artwork as art

from test_artwork_recolor import FRAME, GREEN, RED, SUN, SUN_AT, WHITE, _image, _import, app  # noqa: F401 - shared fixtures

BLACK = "#000000"
MPP = 0.5  # mm per artwork pixel in the synthetic images


def _ring() -> np.ndarray:
    """White 40x40 with a frame border and a 2 px black square contour open at the top (columns 18-21)."""

    image = np.full((40, 40, 3), 255, np.uint8)
    image[:2] = image[-2:] = image[:, :2] = image[:, -2:] = (255, 0, 255)
    image[8:32, 8:32] = 0
    image[10:30, 10:30] = 255
    image[8:10, 18:22] = 255  # the gap
    return image


def _changed(before, after):
    return np.any(before != after, axis=2)


def test_line_paints_exactly_the_pixels_within_half_the_thickness():
    image = _image()
    op = art.check_op({"op": "paint_stroke", "points_px": [[16, 4], [16, 36]], "width_mm": 2.0, "color": BLACK})
    after, mask, sel, change = art.plan_edit(image, op, FRAME, MPP)
    rows, cols = np.nonzero(mask)
    assert sorted(set(rows)) == [14, 15, 16, 17, 18] and cols.min() == 2 and cols.max() == 37  # radius 2 px, round ends
    assert np.array_equal(_changed(image, after), mask) and (after[mask] == 0).all()
    assert sel["stroke_points_px"] == [[16, 4], [16, 36]] and sel["region_sha256"] == art.mask_sha256(mask)
    assert change["to"] == BLACK and change["colors_added"] == [BLACK]
    # Deterministic: the same op gives the same pixels; a thicker line covers the thinner one.
    again = art.plan_edit(image, op, FRAME, MPP)[1]
    thick = art.plan_edit(image, {**op, "width_mm": 4.0}, FRAME, MPP)[1]
    assert np.array_equal(again, mask) and (thick | mask).sum() == thick.sum() > mask.sum()


def test_a_line_never_paints_the_frame_and_must_change_something():
    image = _image()
    across = art.check_op({"op": "paint_stroke", "points_px": [[0, 20], [39, 20]], "width_mm": 1.0, "color": BLACK})
    after = art.plan_edit(image, across, FRAME, MPP)[0]
    assert (after[:2, 20] == (255, 0, 255)).all() and (after[-2:, 20] == (255, 0, 255)).all()  # frame kept
    assert (after[2:-2, 20] == 0).all()
    with pytest.raises(art.ArtworkError, match="frame color"):
        art.plan_edit(image, {**across, "color": FRAME}, FRAME, MPP)
    with pytest.raises(art.ArtworkError, match="changes no pixel"):
        art.plan_edit(image, art.check_op({"op": "paint_stroke", "points_px": [[35, 20], [36, 25]], "width_mm": 1.0,
                                           "color": WHITE}), FRAME, MPP)


def test_brush_paints_only_inside_the_selected_regions():
    image = _image()
    clip = [{"at_px": [25, 10]}]  # the green region (rows 20-29, columns 5-34)
    stroke = {"op": "paint_stroke", "points_px": [[14, 10], [36, 10], [36, 30], [14, 30]], "width_mm": 4.0, "color": RED}
    free = art.plan_edit(image, art.check_op(stroke), FRAME, MPP)[0]
    clipped_op = art.check_op({**stroke, "clip_regions": clip})
    after, mask, sel, _ = art.plan_edit(image, clipped_op, FRAME, MPP)
    region = art.select_regions(image, FRAME, clip, MPP)[0]
    changed = _changed(image, after)
    assert changed.any() and not (changed & ~region).any()  # no spill outside the selection
    assert np.array_equal(changed, _changed(image, free) & region)  # exactly the unclipped stroke inside it
    assert (after[20:30, 5:35][changed[20:30, 5:35]] == (255, 0, 0)).all()
    assert sel["clipped_to_regions"] == 1 and sel["colors"] == [GREEN]
    with pytest.raises(art.ArtworkError, match="inside the selected region"):  # a stroke that misses the selection
        art.plan_edit(image, art.check_op({**clipped_op, "points_px": [[35, 3], [35, 36]]}), FRAME, MPP)


def test_close_gap_snaps_to_the_contour_ends_and_closes_the_region():
    image = _ring()
    assert art.palette_summary(image, FRAME, MPP)[0]["regions_4"] == 1  # inside and outside are one white region
    op = art.check_op({"op": "close_gap", "points_px": [[5, 16], [5, 23]], "width_mm": 1.0, "color": BLACK})
    assert op["snap_color"] == BLACK
    after, mask, sel, change = art.plan_edit(image, op, FRAME, MPP)
    assert sel["stroke_points_px"] == [[8, 16], [8, 23]]  # each click snapped to the nearest black pixel
    assert change["regions_before"][WHITE] == 1 and change["regions_after"][WHITE] == 2  # the contour is closed
    assert (after[8:10, 18:22] == 0).all() and np.array_equal(_changed(image, after), mask & np.any(image != 0, axis=2))
    with pytest.raises(art.ArtworkError, match="no #000000 contour within 2 mm"):
        art.plan_edit(image, art.check_op({**op, "points_px": [[20, 20], [5, 23]]}), FRAME, MPP)
    with pytest.raises(art.ArtworkError, match="same pixel"):
        art.plan_edit(image, art.check_op({**op, "points_px": [[5, 16], [6, 16]]}), FRAME, MPP)


@pytest.mark.parametrize("bad, message", [
    ({"op": "paint_stroke", "points_px": [[1, 1]], "width_mm": 0, "color": RED}, "thickness"),
    ({"op": "paint_stroke", "points_px": [[1, 1]], "width_mm": 51, "color": RED}, "thickness"),
    ({"op": "paint_stroke", "points_px": [], "width_mm": 1, "color": RED}, "1 to 4000 points"),
    ({"op": "paint_stroke", "points_px": [[1, 1, 1]], "width_mm": 1, "color": RED}, "1 to 4000 points"),
    ({"op": "paint_stroke", "points_px": "x", "width_mm": 1, "color": RED}, "points_px"),
    ({"op": "close_gap", "points_px": [[1, 1], [2, 2], [3, 3]], "width_mm": 1, "color": RED}, "two end points"),
    ({"op": "spray", "points_px": [[1, 1]], "width_mm": 1, "color": RED}, "needs op one of"),
])
def test_invalid_strokes_are_rejected(bad, message):
    with pytest.raises(art.ArtworkError, match=message):
        art.check_op(bad)


def test_strokes_are_logged_replayed_and_undone(tmp_path):
    image = _ring()
    folder = tmp_path / "artwork"
    art.start(folder, image, {"stage": "test"}, FRAME, MPP)
    first, _ = art.apply(folder, art.check_op({"op": "close_gap", "points_px": [[5, 16], [5, 23]], "width_mm": 1.0, "color": BLACK}))
    second, _ = art.apply(folder, art.check_op({"at_px": [20, 20], "color": GREEN, "source": "palette"}))  # fill the closed inside
    assert (second[20, 20] == (0, 255, 0)).all() and (second[4, 4] == 255).all()  # outside not filled: the gap is closed
    third, entry = art.apply(folder, art.check_op({"op": "paint_stroke", "points_px": [[12, 12], [27, 27]], "width_mm": 2.0,
                                                   "color": RED, "clip_regions": [{"at_px": [20, 20]}]}))
    assert entry["op"]["clip_regions"] == [{"at_px": [20, 20]}] and entry["from"] == GREEN
    assert not (_changed(second, third) & ~np.all(second == (0, 255, 0), axis=2)).any()
    log = art.read_log(folder)
    assert [e["op"]["op"] for e in log["edits"]] == ["close_gap", "recolor_region", "paint_stroke"]
    assert art.verify(folder)["replay_identical"] and np.array_equal(art.replay(folder, log), third)
    undone, log = art.revert(folder, 1)
    assert len(log["edits"]) == 1 and np.array_equal(undone, first)
    assert art.verify(folder)["replay_identical"]


def test_line_and_brush_through_the_server(app):
    client, workspace, _ = app
    name = _import(client, "&plan=0")
    base = f"/api/projects/{name}/artwork"
    line = {"op": "paint_stroke", "points_px": [[60, 40], [60, 200]], "width_mm": 0.8, "color": "#202020", "source": "picker"}
    preview = client.ok("POST", f"{base}/preview", line)
    assert preview["op"]["op"] == "paint_stroke" and preview["stroke_px"] == [[60, 40], [60, 200]]
    assert any("1.2 mm" in n for n in preview["notes"])  # thinner than the structural width: said, not refused
    for key in ("selection", "after", "before_after"):
        assert client.ok("GET", preview["images"][key])[:4] == b"\x89PNG"
    before = art.load_rgb(workspace / name / "artwork" / "current.png")
    assert client.ok("POST", f"{base}/apply", {"op": preview["op"], "preview_id": preview["preview_id"]})["edit"] == 1
    after = art.load_rgb(workspace / name / "artwork" / "current.png")
    assert _changed(before, after).sum() == preview["change"]["pixels"] > 0

    brush = {"op": "paint_stroke", "points_px": [[SUN_AT[0], SUN_AT[1] - 80], [SUN_AT[0], SUN_AT[1] + 80]], "width_mm": 3.0,
             "color": "#C0392B", "source": "picker", "clip_regions": [{"at_px": SUN_AT}]}
    preview = client.ok("POST", f"{base}/preview", brush)
    assert any("only inside" in n for n in preview["notes"]) and preview["selection"]["colors"] == [SUN]
    client.ok("POST", f"{base}/apply", {"op": preview["op"], "preview_id": preview["preview_id"]})
    painted = art.load_rgb(workspace / name / "artwork" / "current.png")
    sun = art.select_region(after, None, SUN_AT)[0]
    assert _changed(after, painted).any() and not (_changed(after, painted) & ~sun).any()

    view = client.state(name)["artwork"]
    assert [e["op"]["op"] for e in view["edits"]] == ["paint_stroke", "paint_stroke"] and view["can_replan"]
    assert client.ok("POST", f"{base}/verify", {})["replay_identical"]
    assert client.ok("POST", f"{base}/undo", {}) == {"edits_before": 2, "edits_after": 1}
    assert np.array_equal(art.load_rgb(workspace / name / "artwork" / "current.png"), after)
    for bad in ({**line, "width_mm": 0}, {**line, "points_px": [[9999, 1]]}, {**brush, "points_px": [[5, 5], [5, 30]]}):
        assert client.call("POST", f"{base}/preview", bad)[0] == 400, bad


# -- region boundaries: lines and curves split regions; Fill then changes one side only ------------
def _square() -> np.ndarray:
    """White 40x40 with a frame border and one green 20x20 square region."""

    image = np.full((40, 40, 3), 255, np.uint8)
    image[:2] = image[-2:] = image[:, :2] = image[:, -2:] = (255, 0, 255)
    image[10:30, 10:30] = (0, 255, 0)
    return image


def _regions(image, hex_color):
    return {r["hex"]: r["regions_4"] for r in art.palette_summary(image, FRAME, MPP)}.get(hex_color, 0)


def _stroke(image, points, width_mm=0.5, path=None, color=BLACK):
    op = art.check_op({"op": "paint_stroke", "points_px": points, "width_mm": width_mm, "color": color,
                       **({"path": path} if path else {})})
    return art.plan_edit(image, op, FRAME, MPP)


@pytest.mark.parametrize("points, corner", [([[20, 5], [20, 35]], [11, 11]), ([[5, 7], [34, 33]], [28, 11]),
                                            ([[6, 30], [33, 12]], [11, 11])], ids=["horizontal", "diagonal", "steep"])
def test_a_thin_line_across_a_region_splits_it_into_independently_fillable_regions(points, corner):
    image = _square()
    after, mask, _, change = _stroke(image, points)  # 0.5 mm = 1 px: the thinnest stroke
    assert change["regions_before"][GREEN] == 1 and change["regions_after"][GREEN] == 2
    assert _regions(after, BLACK) == 1  # the line itself is one 4-connected region (no diagonal-only steps)
    colors = {tuple(c) for c in np.unique(after.reshape(-1, 3), axis=0).tolist()}
    assert colors == {(0, 0, 0), (0, 255, 0), (255, 255, 255), (255, 0, 255)}  # flat: no blended colors
    green = np.all(after == (0, 255, 0), axis=2)
    side, info = art.select_region(after, FRAME, corner)
    assert info["color"] == GREEN and 0 < side.sum() < green.sum()  # only this side is selected
    filled = art.recolor(after, side, RED, FRAME)
    assert _changed(after, filled).sum() == side.sum() and np.all(filled[green & ~side] == (0, 255, 0))


def test_a_curve_creates_a_curved_boundary_and_replays_exactly():
    image = _square()
    points = [[5, 12], [25, 18], [25, 22], [5, 28]]  # enters at the top, dips to row ~20, leaves at the top
    after, mask, sel, change = _stroke(image, points, path="cubic")
    assert sel["stroke_points_px"] == points and change["regions_after"][GREEN] == 2  # a pocket is cut off
    assert np.nonzero(mask)[0].max() >= 19  # bent far below the straight chord (row 5)
    chord = _stroke(image, [points[0], points[3]])[1]
    assert not np.array_equal(mask, chord) and _regions(after, BLACK) == 1
    pocket, info = art.select_region(after, FRAME, [11, 20])  # inside the curve's dip
    assert info["color"] == GREEN and pocket.sum() < 100 and not pocket[25, 15]
    assert np.array_equal(_stroke(image, points, path="cubic")[1], mask)  # deterministic
    with pytest.raises(art.ArtworkError, match="4 points"):
        art.check_op({"op": "paint_stroke", "points_px": points[:3], "width_mm": 1, "color": RED, "path": "cubic"})
    with pytest.raises(art.ArtworkError, match="path must be one of"):
        art.check_op({"op": "paint_stroke", "points_px": points, "width_mm": 1, "color": RED, "path": "spline"})


def test_a_curved_gap_closure_keeps_its_control_points_and_closes_the_region():
    image = _ring()
    op = art.check_op({"op": "close_gap", "path": "cubic", "points_px": [[6, 16], [3, 18], [3, 21], [6, 23]],
                       "width_mm": 1.0, "color": BLACK})
    after, mask, sel, change = art.plan_edit(image, op, FRAME, MPP)
    assert sel["stroke_points_px"] == [[8, 16], [3, 18], [3, 21], [8, 23]]  # ends snapped, controls kept
    assert change["regions_before"][WHITE] == 1 and change["regions_after"][WHITE] == 2
    assert mask[4:6, 18:22].any() and not (_changed(image, after) & ~mask).any()  # bulges above the contour
    inside, info = art.select_region(after, FRAME, [20, 20])
    assert info["color"] == WHITE and not inside[4, 4]  # the enclosed region fills on its own
    straight = art.plan_edit(image, art.check_op({**op, "path": "line", "points_px": [[6, 16], [6, 23]]}), FRAME, MPP)[1]
    assert not np.array_equal(mask, straight)


def test_curves_and_fills_save_reload_replay_and_undo(tmp_path):
    image = _square()
    folder = tmp_path / "artwork"
    art.start(folder, image, {"stage": "test"}, FRAME, MPP)
    curve = {"op": "paint_stroke", "path": "cubic", "points_px": [[5, 12], [25, 18], [25, 22], [5, 28]], "width_mm": 0.5,
             "color": BLACK, "source": "palette"}
    art.apply(folder, art.check_op(curve))
    second, _ = art.apply(folder, art.check_op({"at_px": [11, 20], "color": RED}))  # fill the pocket only
    third, _ = art.apply(folder, art.check_op({"op": "paint_stroke", "points_px": [[20, 5], [20, 35]], "width_mm": 0.5,
                                               "color": BLACK}))
    assert np.all(second[25, 15] == (0, 255, 0)) and np.all(second[11, 20] == (255, 0, 0))
    log = art.read_log(folder)  # reloaded from disk
    assert log["edits"][0]["op"]["path"] == "cubic" and "path" not in log["edits"][2]["op"]
    assert art.verify(folder)["replay_identical"] and np.array_equal(art.replay(folder, log), third)
    undone, log = art.revert(folder, 2)
    assert np.array_equal(undone, second) and art.verify(folder)["replay_identical"]
    assert np.array_equal(art.revert(folder, 0)[0], image)


def test_strokes_saved_before_the_raster_version_replay_with_their_original_geometry(tmp_path):
    """Strokes saved before ``stroke_raster`` was recorded (minimum radius 0.5 px) must replay, verify and
    undo with exactly the pixels they were applied with, not the current 4-connected minimum radius."""

    import json

    image = _square()
    diagonal = [[5, 5], [34, 34]]  # 45 degrees, 1 px: old geometry = the pixels with row == column only
    old = {"op": "paint_stroke", "points_px": diagonal, "width_mm": 0.5, "color": BLACK, "source": "palette"}
    expected_old = np.zeros(image.shape[:2], bool)
    expected_old[[*range(5, 35)], [*range(5, 35)]] = True
    expected_old &= art.artwork_area(image, FRAME)
    assert np.array_equal(art.plan_edit(image, art.check_op({**old, "stroke_raster": 1}), FRAME, MPP)[1], expected_old)
    new_mask = art.plan_edit(image, art.check_op(old), FRAME, MPP)[1]
    assert art.check_op(old)["stroke_raster"] == art.STROKE_RASTER == 2  # new strokes record their version
    assert new_mask.sum() > expected_old.sum() and (new_mask & expected_old).sum() == expected_old.sum()

    folder = tmp_path / "artwork"
    art.start(folder, image, {"stage": "test"}, FRAME, MPP)
    gap = {"op": "close_gap", "points_px": [[20, 3], [20, 36]], "width_mm": 0.5, "color": BLACK, "snap_color": "#FFFFFF",
           "source": "palette"}
    art.apply(folder, art.check_op({**old, "stroke_raster": 1}))
    art.apply(folder, art.check_op({**gap, "stroke_raster": 1}))
    art.apply(folder, art.check_op({"at_px": [11, 25], "color": RED}))  # a fill on the old-geometry result
    saved = art.load_rgb(folder / "current.png")
    log_file = folder / "edits.json"
    log = json.loads(log_file.read_text(encoding="utf-8"))
    for entry in log["edits"]:
        entry["op"].pop("stroke_raster", None)  # the form an earlier build wrote
    log_file.write_text(json.dumps(log, indent=2), encoding="utf-8")

    log = art.read_log(folder)
    assert "stroke_raster" not in log["edits"][0]["op"]
    assert art.verify(folder)["replay_identical"] and np.array_equal(art.replay(folder, log), saved)
    first = np.any(art.replay(folder, log, 1) != image, axis=2)
    assert np.array_equal(first, expected_old)  # replayed with its original pixels
    undone, log = art.revert(folder, 2)  # undo the fill: replays the two old strokes
    assert np.array_equal(undone, art.replay(folder, log)) and art.verify(folder)["replay_identical"]
    assert all("stroke_raster" not in e["op"] for e in log["edits"])  # the saved records are not rewritten
    added, _ = art.apply(folder, art.check_op({**old, "points_px": [[5, 34], [34, 5]]}))  # a new stroke today
    assert art.read_log(folder)["edits"][-1]["op"]["stroke_raster"] == 2 and art.verify(folder)["replay_identical"]
    with pytest.raises(art.ArtworkError, match="stroke_raster"):
        art.check_op({**old, "stroke_raster": 3})


SAIL_UPPER, SAIL_LOWER = [180, 266], [300, 266]  # the sail spans rows 130-320 (column 262 to its slanted edge)


def _files(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()}


def test_curve_splits_the_sail_fill_one_part_export_blocked_reload_and_replan(app):
    """Server: a curve across the reviewed sail, fill above it, export blocked, reload, explicit re-plan."""

    from test_l4_app import start

    client, workspace, _ = app
    name = _import(client)
    state = client.state(name)
    client.run(name, "review", {"candidate": state["proposal"]["selected"]})
    source = _files(workspace / name / "source")
    base = f"/api/projects/{name}/artwork"
    curve = {"op": "paint_stroke", "path": "cubic", "points_px": [[250, 240], [215, 280], [285, 300], [240, 345]],
             "width_mm": 1.0, "color": "#774627", "source": "palette"}
    preview = client.ok("POST", f"{base}/preview", curve)
    assert preview["op"]["path"] == "cubic" and preview["stroke_px"] == curve["points_px"]
    client.ok("POST", f"{base}/apply", {"op": preview["op"], "preview_id": preview["preview_id"]})
    split = art.load_rgb(workspace / name / "artwork" / "current.png")
    sail = art.hex_color(split[SAIL_UPPER[0], SAIL_UPPER[1]])
    assert art.hex_color(split[SAIL_LOWER[0], SAIL_LOWER[1]]) == sail
    upper = art.select_region(split, None, SAIL_UPPER)[0]
    assert not upper[SAIL_LOWER[0], SAIL_LOWER[1]]  # the curve split the sail into two regions

    fill = client.ok("POST", f"{base}/preview", {"at_px": SAIL_UPPER, "color": "#C0392B", "source": "picker"})
    assert fill["selection"]["pixels"] == int(upper.sum())
    client.ok("POST", f"{base}/apply", {"op": fill["op"], "preview_id": fill["preview_id"]})
    filled = art.load_rgb(workspace / name / "artwork" / "current.png")
    assert art.hex_color(filled[SAIL_LOWER[0], SAIL_LOWER[1]]) == sail and _changed(split, filled).sum() == upper.sum()

    state = client.state(name)
    assert state["artwork"]["differs_from_planned"] and state["artwork"]["can_replan"]  # the sheets are outdated
    assert [e["op"]["op"] for e in state["artwork"]["edits"]] == ["paint_stroke", "recolor_region"]
    status, error = client.call("POST", f"/api/projects/{name}/kits", {})
    assert status == 400 and "planned" in error["error"]["message"]  # validated export blocked

    # Save / reload: a new server on the same folder (for the packaged app: the exe is stopped and started
    # again) continues from the saved log.
    server2, client = start(workspace)
    try:
        assert client.ok("POST", f"{base}/verify", {})["replay_identical"]
        assert client.state(name)["artwork"]["edits"][0]["op"]["path"] == "cubic"
        assert client.ok("POST", f"{base}/undo", {}) == {"edits_before": 2, "edits_after": 1}  # undo the fill
        assert np.array_equal(art.load_rgb(workspace / name / "artwork" / "current.png"), split)
        again = client.ok("POST", f"{base}/preview", fill["op"])
        client.ok("POST", f"{base}/apply", {"op": again["op"], "preview_id": again["preview_id"]})

        client.run(name, "artwork/replan")  # only on the explicit request
        state = client.state(name)
    finally:
        server2.shutdown()
        server2.server_close()
    check = state["proposal"]["artwork_check"]
    assert (workspace / name / "attempts" / check["archived_as"] / "review" / "sheet_stack.json").is_file()  # kept
    assert not state["artwork"]["differs_from_planned"] and state["proposal"]["artwork_edits"]["region_recolors"] == 2
    assert np.array_equal(art.load_rgb(workspace / name / "proposal" / "intake" / "target.png"), filled)
    assert _files(workspace / name / "source") == source  # the uploaded image is never changed
