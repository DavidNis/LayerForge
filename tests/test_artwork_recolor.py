"""Region recoloring of the processed flat-color artwork (``core/artwork.py`` + app workflow).

Core tests use small synthetic targets.  App tests run the real HTTP server (as in
``test_l4_app.py``) on the project-owned sailboat fixture at scale 1, whose sky color has four
4-connected regions: the sky and three portholes in the hull.
"""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest

from layerforge_l1.core import artwork as art
from layerforge_l1.core.intake import use_edited_target

from test_l4_app import SAILBOAT, Client, start  # noqa: F401 - shared server helpers

RED, BLUE, GREEN, WHITE, FRAME = "#FF0000", "#0000FF", "#00FF00", "#FFFFFF", "#FF00FF"
SKY, SUN = "#AAD7F4", "#F9C832"
PORTHOLES = ([352, 210], [352, 250], [352, 290])  # [row, col] in the 512 px sailboat target
SUN_AT = [90, 420]
SEA_LEFT, SEA_RIGHT = [480, 40], [480, 470]


def _image() -> np.ndarray:
    """White 40x40 with two separate red squares, a blue bar touching the right square, frame border."""

    image = np.full((40, 40, 3), 255, np.uint8)
    image[:2] = image[-2:] = image[:, :2] = image[:, -2:] = (255, 0, 255)
    image[5:12, 5:12] = (255, 0, 0)
    image[5:12, 20:27] = (255, 0, 0)
    image[5:12, 27:33] = (0, 0, 255)  # 4-adjacent to the right red square
    image[20:30, 5:35] = (0, 255, 0)  # one green region (the "trousers")
    return image


# -- core ---------------------------------------------------------------------------------------
def test_click_selects_the_connected_region_not_every_pixel_of_the_color():
    image = _image()
    mask, info = art.select_region(image, FRAME, [8, 8])
    assert info["color"] == RED and info["pixels"] == 49 and info["regions_of_color"] == 2
    assert mask[8, 8] and not mask[8, 22]  # the other red square is not selected
    assert info["bbox_px"] == [5, 5, 12, 12] and not info["limited_by_box"]
    with pytest.raises(art.ArtworkError, match="frame"):
        art.select_region(image, FRAME, [0, 0])
    with pytest.raises(art.ArtworkError, match="outside"):
        art.select_region(image, FRAME, [40, 3])


def test_recolor_one_region_and_merge_with_an_adjacent_region():
    image = _image()
    op = art.check_op({"at_px": [8, 8], "color": "#00ff00", "source": "picker"})
    after, mask, _, change = art.plan_edit(image, op, FRAME, 0.5)
    assert (after[5:12, 5:12] == (0, 255, 0)).all() and (after[5:12, 20:27] == (255, 0, 0)).all()
    assert change["regions_before"] == {RED: 2, GREEN: 1} and change["regions_after"] == {RED: 1, GREEN: 2}
    assert change["palette_size_before"] == change["palette_size_after"] == 4 and change["merged_with_adjacent_regions"] == 0
    # The right red square becomes blue: it merges with the touching blue bar into one region.
    after2, _, _, merge = art.plan_edit(after, art.check_op({"at_px": [8, 22], "color": BLUE, "source": "palette"}), FRAME, 0.5)
    assert merge["merged_with_adjacent_regions"] == 1 and merge["regions_after"][BLUE] == 1
    assert merge["colors_removed"] == [RED] and merge["palette_size_after"] == 3
    rows = {r["hex"]: r for r in art.palette_summary(after2, FRAME, 0.5)}
    assert set(rows) == {WHITE, GREEN, BLUE} and rows[BLUE]["regions_4"] == 1 and rows[GREEN]["regions_4"] == 2
    with pytest.raises(art.ArtworkError, match="already"):
        art.plan_edit(after2, art.check_op({"at_px": [8, 22], "color": BLUE}), FRAME, 0.5)
    with pytest.raises(art.ArtworkError, match="frame color"):
        art.plan_edit(image, art.check_op({"at_px": [8, 8], "color": FRAME}), FRAME, 0.5)


def test_limit_box_changes_one_part_of_a_connected_region():
    image = _image()
    mask, info = art.select_region(image, FRAME, [25, 10], [18, 2, 32, 20])
    assert info["limited_by_box"] and info["pixels"] == 10 * 15 and info["whole_region_pixels"] == 300
    after = art.recolor(image, mask, BLUE, FRAME)
    assert (after[25, 10] == (0, 0, 255)).all() and (after[25, 30] == (0, 255, 0)).all()
    with pytest.raises(art.ArtworkError, match="outside the limit box"):
        art.select_region(image, FRAME, [25, 30], [18, 2, 32, 20])


def test_eyedropper_color_must_still_be_at_the_sampled_point():
    image = _image()
    op = art.check_op({"at_px": [8, 8], "color": BLUE, "source": "eyedropper", "sampled_at_px": [8, 30]})
    after, *_ = art.plan_edit(image, op, FRAME, 0.5)
    assert (after[8, 8] == (0, 0, 255)).all()
    with pytest.raises(art.ArtworkError, match="sample again"):
        art.plan_edit(image, {**op, "color": GREEN}, FRAME, 0.5)
    with pytest.raises(art.ArtworkError, match="sampled point"):
        art.check_op({"at_px": [8, 8], "color": BLUE, "source": "eyedropper"})


def test_log_apply_undo_revert_verify_and_tamper_detection(tmp_path):
    image = _image()
    folder = tmp_path / "artwork"
    art.start(folder, image, {"stage": "test"}, FRAME, 0.5)
    first, _ = art.apply(folder, art.check_op({"at_px": [8, 8], "color": GREEN}))
    second, entry = art.apply(folder, art.check_op({"at_px": [8, 22], "color": BLUE, "source": "palette"}))
    assert entry["index"] == 2 and entry["change"]["merged_with_adjacent_regions"] == 1
    assert art.verify(folder)["replay_identical"]
    assert np.array_equal(art.replay(folder, art.read_log(folder)), second)
    undone, log = art.revert(folder, 1)
    assert len(log["edits"]) == 1 and np.array_equal(undone, first)
    assert np.array_equal(art.load_rgb(folder / "current.png"), first)
    reverted, log = art.revert(folder, 0)
    assert np.array_equal(reverted, image) and log["current_sha256"] == art.rgb_sha256(image)
    art.apply(folder, art.check_op({"at_px": [8, 8], "color": GREEN}))
    # A changed base or current image is detected, never silently trusted.
    tampered = art.load_rgb(folder / "current.png")
    tampered[20, 20] = (1, 2, 3)
    art.save_rgb(tampered, folder / "current.png")
    report = art.verify(folder)
    assert not report["replay_identical"] and report["problems"]
    with pytest.raises(art.ArtworkError, match="does not match"):
        art.apply(folder, art.check_op({"at_px": [8, 22], "color": GREEN}))


def test_edited_target_is_remeasured_and_may_not_touch_the_frame():
    target = _image()
    inside = ~np.all(target == (255, 0, 255), axis=2)
    colors = [{"hex": h} for h in (WHITE, GREEN, RED, BLUE)]
    prepared = {"target_rgb": target, "artwork_mask": inside, "frame_color": FRAME,
                "measurements": {"artwork_mm_per_pixel": 0.5, "structural_width_px": 3, "colors": colors}}
    edited = target.copy()
    edited[5:12, 20:27] = (0, 0, 255)  # red square -> blue: merges with the bar
    edited[5:12, 5:12] = (0, 0, 255)
    out = use_edited_target(prepared, edited)
    m = out["measurements"]
    assert [c["hex"] for c in m["colors"]] == [WHITE, GREEN, BLUE] and m["palette_size"] == 3
    assert {c["hex"]: c["source_regions_4"] for c in m["colors"]}[BLUE] == 2
    assert out["intake_target_rgb"] is target and out["target_rgb"] is not target
    bad = edited.copy()
    bad[0, 0] = (0, 0, 0)
    with pytest.raises(Exception, match="frame"):
        use_edited_target(prepared, bad)
    with pytest.raises(Exception, match="px"):
        use_edited_target(prepared, edited[:30])


# -- multi-region selection and batch recolor ------------------------------------------------
LEFT_RED, RIGHT_RED, BAR, GREEN_AT = [8, 8], [8, 22], [8, 30], [25, 10]


def _union(image, regions):
    mask = np.zeros(image.shape[:2], bool)
    for region in regions:
        mask |= art.select_region(image, FRAME, region["at_px"], region.get("box_px"))[0]
    return mask


def test_shift_click_adds_and_removes_disconnected_and_mixed_color_regions():
    image = _image()
    regions = art.toggle_region(image, FRAME, [], LEFT_RED)
    regions = art.toggle_region(image, FRAME, regions, RIGHT_RED)  # same color, disconnected
    regions = art.toggle_region(image, FRAME, regions, BAR)  # another color
    mask, info = art.select_regions(image, FRAME, regions)
    assert info["regions"] == 3 and info["colors"] == [BLUE, RED] and info["color"] == "mixed"
    assert info["pixels"] == 49 + 49 + 42 and np.array_equal(mask, _union(image, regions))
    # Shift+Click anywhere inside a selected region removes exactly that region.
    fewer = art.toggle_region(image, FRAME, regions, [10, 10])
    assert [r["at_px"] for r in fewer] == [RIGHT_RED, BAR]
    assert art.toggle_region(image, FRAME, fewer, [11, 5]) == fewer + [{"at_px": [11, 5]}]
    # The same region listed twice counts once; the frame and points outside a box are refused.
    _, twice = art.select_regions(image, FRAME, [{"at_px": LEFT_RED}, {"at_px": [6, 6]}])
    assert twice["regions"] == 1 and twice["pixels"] == 49
    with pytest.raises(art.ArtworkError, match="frame"):
        art.toggle_region(image, FRAME, regions, [0, 0])
    with pytest.raises(art.ArtworkError, match="outside the limit box"):
        art.toggle_region(image, FRAME, regions, [25, 30], [18, 2, 32, 20])
    with pytest.raises(art.ArtworkError, match="no region"):
        art.select_regions(image, FRAME, [])


def test_batch_recolor_changes_only_the_selected_pixels_including_a_limit_box():
    image = _image()
    regions = [{"at_px": LEFT_RED}, {"at_px": BAR}, {"at_px": GREEN_AT, "box_px": [18, 2, 32, 20]}]
    op = art.check_op({"op": "recolor_regions", "regions": regions, "color": "#123456", "source": "picker"})
    after, mask, selection, change = art.plan_edit(image, op, FRAME, 0.5)
    changed = np.any(after != image, axis=2)
    assert np.array_equal(changed, mask) and np.array_equal(mask, _union(image, regions))
    assert (after[mask] == (0x12, 0x34, 0x56)).all() and np.array_equal(after[~mask], image[~mask])
    assert selection["regions"] == 3 and selection["limited_by_box"] and (after[25, 30] == (0, 255, 0)).all()
    assert change["from"] == "mixed" and change["from_colors"] == [BLUE, GREEN, RED] and change["pixels"] == int(mask.sum())
    assert change["regions_before"][RED] == 2 and change["regions_after"][RED] == 1
    assert change["colors_removed"] == [BLUE] and change["colors_added"] == ["#123456"]
    # Regions that already have the new color are kept as they are; all of them -> nothing to do.
    same = art.check_op({"regions": [{"at_px": LEFT_RED}, {"at_px": RIGHT_RED}], "color": RED})
    with pytest.raises(art.ArtworkError, match="every selected region already has"):
        art.plan_edit(image, same, FRAME, 0.5)
    partly = art.check_op({"regions": [{"at_px": LEFT_RED}, {"at_px": BAR}], "color": RED})
    after, mask, _, change = art.plan_edit(image, partly, FRAME, 0.5)
    bar = _union(image, [{"at_px": BAR}])
    assert change["pixels"] == int(bar.sum()) == 42 and np.array_equal(np.any(after != image, axis=2), bar)
    for bad in ({"regions": [], "color": RED}, {"regions": [{"at_px": [1]}], "color": RED},
                {"regions": "x", "color": RED}, {"op": "paint", "color": RED}):
        with pytest.raises(art.ArtworkError):
            art.check_op(bad)


def test_batch_recolor_is_one_undoable_logged_edit_that_replays(tmp_path):
    image = _image()
    folder = tmp_path / "artwork"
    art.start(folder, image, {"stage": "test"}, FRAME, 0.5)
    first, _ = art.apply(folder, art.check_op({"at_px": GREEN_AT, "color": WHITE}))  # single-region op (legacy form)
    batch = art.check_op({"regions": [{"at_px": LEFT_RED}, {"at_px": RIGHT_RED}, {"at_px": BAR}], "color": GREEN})
    second, entry = art.apply(folder, batch)
    log = art.read_log(folder)
    assert len(log["edits"]) == 2 and entry["selection"]["regions"] == 3 and entry["from_colors"] == [BLUE, RED]
    assert np.array_equal(np.any(second != first, axis=2), _union(first, batch["regions"]))
    assert art.verify(folder)["replay_identical"] and np.array_equal(art.replay(folder, log), second)
    undone, log = art.revert(folder, 1)  # the whole batch is one undo step
    assert len(log["edits"]) == 1 and np.array_equal(undone, first)

# -- app ----------------------------------------------------------------------------------------
@pytest.fixture()
def app(tmp_path):
    server, client = start(tmp_path / "workspace")
    yield client, tmp_path / "workspace", tmp_path
    server.shutdown()
    server.server_close()


def _import(client: Client, query: str = "") -> str:
    name = client.ok("POST", f"/api/projects?filename=sailboat_512.png&scale=1{query}", raw=SAILBOAT.read_bytes())["project"]
    result = client.wait({"id": client.state(name)["job"]["id"]})
    assert result["status"] == "done", result["error"]
    return name


def _recolor(client: Client, name: str, at, color: str, source: str = "picker", **extra) -> dict:
    base = f"/api/projects/{name}/artwork"
    preview = client.ok("POST", f"{base}/preview", {"at_px": at, "color": color, "source": source, **extra})
    for key in ("selection", "after", "before_after"):  # what the user sees before Apply
        assert client.ok("GET", preview["images"][key])[:4] == b"\x89PNG"
    applied =client.ok("POST", f"{base}/apply", {"op": preview["op"], "preview_id": preview["preview_id"]})
    return {"preview": preview, "applied": applied}


def test_recolor_before_planning_undo_eyedropper_reload_and_plan(app):
    from layerforge_l1.core.model import file_sha256

    client, workspace, _ = app
    name = _import(client, "&plan=0")
    state = client.state(name)
    assert state["proposal"]["status"] == "ARTWORK_READY_NOT_PLANNED" and not state["proposal"]["candidates"]
    view = state["artwork"]
    assert view["available"] and not view["planned"] and view["can_replan"] and not view["edits"]
    source = workspace / name / state["record"]["source"]["file"]
    source_sha = file_sha256(source)
    base = f"/api/projects/{name}/artwork"

    # Selection only: exactly one porthole, highlighted before anything changes.
    picked = client.ok("POST", f"{base}/preview", {"at_px": PORTHOLES[0]})
    sel = picked["selection"]
    assert sel["color"] == SKY and sel["regions_of_color"] == 4 and 10 < sel["pixels"] < 200
    assert client.ok("GET", picked["images"]["selection"])[:4] == b"\x89PNG"
    assert (workspace / name / "artwork" / "current.png").is_file()  # the log starts on the intake target

    # Recolor one porthole with a palette color; the other two keep the sky color.
    done = _recolor(client, name, PORTHOLES[0], SUN, "palette")
    change = done["preview"]["change"]
    assert change["regions_before"] == {SKY: 4, SUN: 1} and change["regions_after"] == {SKY: 3, SUN: 2}
    assert change["palette_size_before"] == change["palette_size_after"] == 5
    current = art.load_rgb(workspace / name / "artwork" / "current.png")
    assert art.hex_color(current[PORTHOLES[0][0], PORTHOLES[0][1]]) == SUN
    assert art.hex_color(current[PORTHOLES[1][0], PORTHOLES[1][1]]) == SKY

    # A preview id from another artwork state is refused; nothing is applied without a preview.
    stale = client.ok("POST", f"{base}/preview", {"at_px": PORTHOLES[1], "color": "#112233"})
    status, error = client.call("POST", f"{base}/apply", {"op": stale["op"], "preview_id": "0" * 16})
    assert status == 400 and "out of date" in error["error"]["message"]

    # Eyedropper: sample the sun, apply to the second porthole.
    sampled = client.ok("POST", f"{base}/sample", {"at_px": SUN_AT})
    assert sampled["color"] == SUN
    _recolor(client, name, PORTHOLES[1], sampled["color"], "eyedropper", sampled_at_px=SUN_AT)
    # Undo removes only the last edit.
    assert client.ok("POST", f"{base}/undo", {}) == {"edits_before": 2, "edits_after": 1}
    after_undo = art.load_rgb(workspace / name / "artwork" / "current.png")
    assert art.hex_color(after_undo[PORTHOLES[1][0], PORTHOLES[1][1]]) == SKY
    assert art.hex_color(after_undo[PORTHOLES[0][0], PORTHOLES[0][1]]) == SUN
    _recolor(client, name, PORTHOLES[2], SUN, "eyedropper", sampled_at_px=SUN_AT)
    assert client.ok("POST", f"{base}/verify", {})["replay_identical"]
    edited_sha = client.state(name)["artwork"]["current_sha256"]

    # Save/reload: a new server on the same workspace sees the same log and artwork.
    server2, client2 = start(workspace)
    try:
        view = client2.state(name)["artwork"]
        assert [e["index"] for e in view["edits"]] == [1, 2] and view["current_sha256"] == edited_sha
        assert client2.ok("POST", f"{base}/verify", {})["replay_identical"]
        assert {r["hex"]: r["regions_4"] for r in view["palette"]}[SUN] == 3
    finally:
        server2.shutdown()
        server2.server_close()

    # Re-running intake would drop the edits: refused, nothing is planned automatically.
    status, error = client.call("POST", f"/api/projects/{name}/propose", {})
    assert status == 400 and "2 region recolor(s) are not planned yet" in error["error"]["message"]
    assert "re-running intake is not available" in client.state(name)["proposal"]["intake_rerun_blocked"]
    assert client.state(name)["proposal"]["status"] == "ARTWORK_READY_NOT_PLANNED"

    client.run(name, "artwork/replan")
    state = client.state(name)
    p = state["proposal"]
    assert p["status"] == "PROPOSAL_READY_FOR_REVIEW"
    assert p["artwork_edits"]["region_recolors"] == 2 and p["artwork_edits"]["artwork_sha256"] == edited_sha
    assert any(f["code"] == "ARTWORK_EDITED" for f in p["flags"])
    planned = art.load_rgb(workspace / name / "proposal" / "intake" / "target.png")
    assert art.rgb_sha256(planned) == edited_sha  # the planner got the edited artwork
    check = p["artwork_check"]
    assert check["before"]["sheet_count"] is None and check["after"]["sheet_count"] >= 1
    assert check["after"]["palette_size"] == 5 and check["archived_as"] is None  # intake only: nothing to archive
    assert (workspace / name / "proposal" / "artwork_edits" / "edits.json").is_file()
    assert not (workspace / name / "artwork").exists() and file_sha256(source) == source_sha
    stack_target = art.load_rgb(workspace / name / "proposal" / "candidates" / p["selected"] / "stack" / "target.png")
    assert np.array_equal(stack_target, planned)
    view = state["artwork"]
    assert not view["edits"] and not view["differs_from_planned"] and view["planned_from_edited_artwork"]


def test_failed_proposal_recovers_by_recoloring_and_replanning(app, monkeypatch):
    """Planning fails while the artwork has its sun (simulated); merging the sun into the sky plans."""

    import layerforge_l1.core.propose as core_propose
    from layerforge_l1.core.model import file_sha256
    from layerforge_l1.core.planning import PlanningError

    real = core_propose.plan_stack

    def fails_with_sun(target, config, name="stack"):
        if np.all(target == np.array(art.parse_hex(SUN), np.uint8), axis=2).any():
            raise PlanningError("reveal closure is infeasible (simulated: the sun)", {"closure_feasible": False})
        return real(target, config, name)

    monkeypatch.setattr(core_propose, "plan_stack", fails_with_sun)
    client, workspace, _ = app
    name = _import(client)
    state = client.state(name)
    assert state["proposal"]["status"] == "NEEDS_USER_DECISION" and state["artwork"]["available"]
    source_sha = file_sha256(workspace / name / state["record"]["source"]["file"])
    status, error = client.call("POST", f"/api/projects/{name}/artwork/replan", {})
    assert status == 400 and "nothing to re-plan" in error["error"]["message"]  # no edit yet

    done = _recolor(client, name, SUN_AT, SKY, "palette")
    change = done["preview"]["change"]
    assert change["merged_with_adjacent_regions"] == 1 and change["colors_removed"] == [SUN]
    assert change["palette_size_before"] == 5 and change["palette_size_after"] == 4
    view = client.state(name)["artwork"]
    assert view["differs_from_planned"] and view["can_replan"]
    # Upscale & Retry and re-running intake would discard the edit: refused.
    status, error = client.call("POST", f"/api/projects/{name}/upscale/preview", {"factor": 2})
    assert status == 400 and "finer pixel grid" in error["error"]["message"]
    assert "Upscale & Retry is not available" in client.state(name)["upscale"]["blocked_by_artwork"]

    client.run(name, "artwork/replan")
    state = client.state(name)
    assert state["proposal"]["status"] == "PROPOSAL_READY_FOR_REVIEW"
    check = state["proposal"]["artwork_check"]
    assert check["before"]["status"] == "NEEDS_USER_DECISION" and check["before"]["palette_size"] == 5
    assert check["after"]["palette_size"] == 4 and check["after"]["sheet_count"] >= 1
    attempt = state["upscale"]["attempts"][0]
    assert attempt["id"] == check["archived_as"] == "attempt_01_artwork"
    assert attempt["status"] == "NEEDS_USER_DECISION" and attempt["replaced_by"]["kind"] == "artwork_edit"
    old = json.loads((workspace / name / "attempts" / attempt["id"] / "proposal" / "proposal.json").read_text(encoding="utf-8"))
    assert "simulated" in old["candidates"][0]["reason"]  # the failed attempt's diagnostics are kept
    assert file_sha256(workspace / name / state["record"]["source"]["file"]) == source_sha
    client.run(name, "review", {"candidate": state["proposal"]["selected"]})
    assert client.state(name)["draft"]["status"]["status"] == "REVIEWED_VALID"


def test_review_artwork_edit_blocks_export_until_replanned_or_undone(app):
    client, workspace, _ = app
    name = _import(client)
    state = client.state(name)
    client.run(name, "review", {"candidate": state["proposal"]["selected"]})
    state = client.state(name)
    sheets_before = len(state["draft"]["sheets"])
    manifest_before = (workspace / name / "review" / "sheet_stack.json").read_bytes()
    assert state["artwork"]["stage"] == "review" and not state["artwork"]["differs_from_planned"]

    # Sea is one connected region; a limit box recolors only its left part (one "leg").
    left = _recolor(client, name, SEA_LEFT, "#2E5FA8", box_px=[400, 0, 512, 256])
    assert left["preview"]["selection"]["limited_by_box"]
    current = art.load_rgb(workspace / name / "artwork" / "current.png")
    assert art.hex_color(current[SEA_RIGHT[0], SEA_RIGHT[1]]) != "#2E5FA8"
    state = client.state(name)
    assert state["artwork"]["differs_from_planned"] and state["artwork"]["can_replan"]
    # The artwork edit is not a sheet edit: the review stack and its validation are unchanged.
    assert (workspace / name / "review" / "sheet_stack.json").read_bytes() == manifest_before
    assert state["draft"]["edits"] == [] and state["draft"]["status"]["printable"]
    # Validated export of the old stack is refused while the artwork differs.
    status, error = client.call("POST", f"/api/projects/{name}/kits", {})
    assert status == 400 and "planned" in error["error"]["message"]
    client.ok("POST", f"/api/projects/{name}/artwork/undo", {"keep": 0})
    assert not client.state(name)["artwork"]["differs_from_planned"]

    _recolor(client, name, SEA_LEFT, "#2E5FA8", box_px=[400, 0, 512, 256])
    client.run(name, "artwork/replan")
    state = client.state(name)
    assert "draft" not in state and state["record"]["candidate"] is None  # the review is archived
    check = state["proposal"]["artwork_check"]
    assert check["before"]["stage"] == "review" and check["before"]["sheet_count"] == sheets_before
    assert check["after"]["palette_size"] == check["before"]["palette_size"] + 1
    archive = workspace / name / "attempts" / check["archived_as"]
    assert (archive / "review" / "sheet_stack.json").read_bytes() == manifest_before
    assert not state["artwork"]["differs_from_planned"]
    status, error = client.call("POST", f"/api/projects/{name}/propose", {})
    assert status == 400 and "planned from artwork with your region recolors" in error["error"]["message"]


def test_physical_resize_keeps_region_recolors(app):
    """Resize & Retry plans the edited artwork (pixel for pixel) at the new size, unplanned or planned."""

    from layerforge_l1.core.model import file_sha256

    client, workspace, _ = app
    name = _import(client, "&outer_mm=60")
    state = client.state(name)
    source = workspace / name / state["record"]["source"]["file"]
    source_sha = file_sha256(source)
    _recolor(client, name, PORTHOLES[0], SUN, "palette")
    edited = art.load_rgb(workspace / name / "artwork" / "current.png")
    resize = f"/api/projects/{name}/resize"

    client.run(name, "resize/analyze", {"process": None, "bambu_printer": None})
    analysis = client.state(name)["resize"]["analysis"]
    assert analysis["basis"]["artwork"] == {"sha256": art.rgb_sha256(edited), "region_recolors": 1, "pending": True}
    assert "previous artwork" in analysis["measured"]["source"]
    size = analysis["options"]["recommended"]["sheet_mm"]
    client.run(name, "resize/preview", {"size_mm": size})
    preview = client.state(name)["resize"]["preview"]
    assert preview["current"] and preview["status"] == "READY_TO_RESIZE" and preview["boundaries"]["pass"]
    assert preview["artwork_edits"]["region_recolors"] == 1 and preview["artwork_edits"]["pending"]
    assert preview["changes"] == [] and preview["details"] == []  # the edited artwork is kept as it is
    assert np.array_equal(art.load_rgb(workspace / name / "resize" / "preview" / "target.png"), edited)

    # Changing the artwork after the preview makes it out of date: no retry from a stale approval.
    client.ok("POST", f"/api/projects/{name}/artwork/undo", {})
    status, error = client.call("POST", f"{resize}/retry", {"preview_id": preview["preview_id"], "approve_size": True})
    assert status == 400 and "out of date" in error["error"]["message"]
    _recolor(client, name, PORTHOLES[0], SUN, "palette")
    client.run(name, "resize/analyze", {"process": None, "bambu_printer": None})
    client.run(name, "resize/preview", {"size_mm": size})
    preview = client.state(name)["resize"]["preview"]
    status, error = client.call("POST", f"{resize}/retry", {"preview_id": preview["preview_id"]})
    assert status == 400 and "approve changing the sheet" in error["error"]["message"]  # approval still required
    client.run(name, "resize/retry", {"preview_id": preview["preview_id"], "approve_size": True})

    state = client.state(name)
    p = state["proposal"]
    assert p["planning_config"]["outer_sheet_mm"] == size and state["record"]["sheet_outer_mm"] == size
    assert p["artwork_edits"]["region_recolors"] == 1 and p["artwork_edits"]["carried_by"] == "physical_resize"
    planned = art.load_rgb(workspace / name / "proposal" / "intake" / "target.png")
    assert np.array_equal(planned, edited)  # not restarted from the unedited source
    assert art.hex_color(planned[PORTHOLES[0][0], PORTHOLES[0][1]]) == SUN
    log = json.loads((workspace / name / "proposal" / "artwork_edits" / "edits.json").read_text(encoding="utf-8"))
    assert len(log["edits"]) == 1 and not (workspace / name / "artwork").exists()
    assert state["resize"]["last_check"]["artwork_edits"]["kept_pixel_for_pixel"]
    first = state["upscale"]["attempts"][0]
    assert first["replaced_by"]["kind"] == "physical_resize" and first["sheet_mm"] == 60.0
    assert file_sha256(source) == source_sha
    assert not state["artwork"]["differs_from_planned"]

    # A second resize from the proposal planned from edited artwork keeps the recolor and its history.
    client.run(name, "resize/analyze", {"process": None, "bambu_printer": None})
    analysis = client.state(name)["resize"]["analysis"]
    assert analysis["basis"]["artwork"]["pending"] is False
    bigger = analysis["limit"]["max_sheet_mm"] if analysis["options"]["recommended"] is None else analysis["options"]["recommended"]["sheet_mm"]
    client.run(name, "resize/preview", {"size_mm": bigger})
    preview = client.state(name)["resize"]["preview"]
    assert preview["artwork_edits"]["pending"] is False and preview["changes"] == []
    client.run(name, "resize/retry", {"preview_id": preview["preview_id"], "approve_size": True})
    state = client.state(name)
    assert np.array_equal(art.load_rgb(workspace / name / "proposal" / "intake" / "target.png"), edited)
    second = state["upscale"]["attempts"][1]
    assert (workspace / name / "attempts" / second["id"] / "proposal" / "artwork_edits" / "edits.json").is_file()
    assert (workspace / name / "proposal" / "artwork_edits" / "edits.json").is_file()


def test_multi_region_batch_recolor_through_the_server(app):
    """Shift+Click toggling (server-side), mixed colors and a limit box, one batch edit, undo, reload, explicit planning."""

    client, workspace, _ = app
    name = _import(client, "&plan=0")
    base = f"/api/projects/{name}/artwork"

    def toggle(regions, at, box=None):
        result = client.ok("POST", f"{base}/preview", {"regions": regions, "toggle": {"at_px": at, "box_px": box}})
        return result["regions"], result["selection"]

    regions, sel = toggle([], PORTHOLES[0])
    assert sel["regions"] == 1 and sel["colors"] == [SKY]
    regions, sel = toggle(regions, PORTHOLES[1])  # another disconnected region of the same color
    regions, sel = toggle(regions, SUN_AT)  # a different original color
    regions, sel = toggle(regions, SEA_LEFT, [400, 0, 512, 256])  # part of the sea only
    assert sel["regions"] == 4 and sel["colors"] == ["#1C478D", SKY, SUN] and sel["limited_by_box"]
    regions, sel = toggle(regions, [PORTHOLES[1][0] + 1, PORTHOLES[1][1]])  # Shift+Click a selected one: removed
    assert sel["regions"] == 3 and [r["at_px"] for r in regions] == [PORTHOLES[0], SUN_AT, SEA_LEFT]
    empty, none = toggle([{"at_px": PORTHOLES[0]}], PORTHOLES[0])
    assert empty == [] and none is None  # deselecting the last region clears the selection

    before = art.load_rgb(workspace / name / "artwork" / "current.png")
    preview = client.ok("POST", f"{base}/preview", {"regions": regions, "color": "#C0392B", "source": "picker"})
    assert preview["op"]["op"] == "recolor_regions" and preview["selection"]["regions"] == 3
    assert preview["change"]["from"] == "mixed" and "one edit" in " ".join(preview["notes"])
    for key in ("selection", "after", "before_after"):
        assert client.ok("GET", preview["images"][key])[:4] == b"\x89PNG"
    applied = client.ok("POST", f"{base}/apply", {"op": preview["op"], "preview_id": preview["preview_id"]})
    assert applied["edit"] == 1
    after = art.load_rgb(workspace / name / "artwork" / "current.png")
    expected = np.zeros(before.shape[:2], bool)
    for region in regions:
        expected |= art.select_region(before, None, region["at_px"], region.get("box_px"))[0]
    assert np.array_equal(np.any(after != before, axis=2), expected)  # only the selected pixels changed
    assert np.array_equal(after[~expected], before[~expected]) and (after[expected] == (0xC0, 0x39, 0x2B)).all()
    assert art.hex_color(after[PORTHOLES[1][0], PORTHOLES[1][1]]) == SKY and art.hex_color(after[SEA_RIGHT[0], SEA_RIGHT[1]]) == "#1C478D"

    view = client.state(name)["artwork"]
    assert len(view["edits"]) == 1 and view["edits"][0]["selection"]["regions"] == 3
    assert view["edits"][0]["from_colors"] == ["#1C478D", SKY, SUN] and view["can_replan"]
    assert client.ok("POST", f"{base}/undo", {}) == {"edits_before": 1, "edits_after": 0}  # one undo step
    assert np.array_equal(art.load_rgb(workspace / name / "artwork" / "current.png"), before)
    preview = client.ok("POST", f"{base}/preview", {"regions": regions, "color": "#C0392B", "source": "picker"})
    client.ok("POST", f"{base}/apply", {"op": preview["op"], "preview_id": preview["preview_id"]})

    server2, client2 = start(workspace)  # save/reload
    try:
        assert client2.ok("POST", f"{base}/verify", {})["replay_identical"]
        assert client2.state(name)["artwork"]["edits"][0]["selection"]["regions"] == 3
    finally:
        server2.shutdown()
        server2.server_close()
    client.run(name, "artwork/replan")  # Plan sheets from this artwork: explicit, never automatic
    p = client.state(name)["proposal"]
    assert p["status"] == "PROPOSAL_READY_FOR_REVIEW" and p["artwork_edits"]["region_recolors"] == 1
    assert np.array_equal(art.load_rgb(workspace / name / "proposal" / "intake" / "target.png"), after)


def test_artwork_requests_are_checked(app):
    client, workspace, _ = app
    name = _import(client, "&plan=0")
    base = f"/api/projects/{name}/artwork"
    for body in ({}, {"at_px": [9999, 1]}, {"at_px": "x"}, {"at_px": [10, 10], "color": "blue"},
                 {"at_px": [10, 10], "color": SUN, "source": "magic"}):
        status, error = client.call("POST", f"{base}/preview", body)
        assert status == 400, (body, error)
    assert client.call("POST", f"{base}/undo", {})[0] == 400  # nothing to undo
    assert client.call("POST", f"{base}/apply", {"op": {"at_px": [10, 10], "color": SUN}, "preview_id": "x"})[0] == 400
    shutil.rmtree(workspace / name / "artwork")
    assert client.state(name)["artwork"]["available"]


def test_browser_artwork_panel(app):
    """The Artwork colors panel in a real browser: select, preview, cancel, apply, eyedropper, undo,
    verify, reload, limit box and explicit planning (``tests/e2e/drive_artwork.mjs``)."""

    import subprocess

    from test_l4_app import BROWSERS, ROOT, _node_ok

    client, _, _ = app
    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    shots = ROOT / "artifacts" / "artwork_recolor_v1" / "screens"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_artwork.mjs"), client.base + "/", str(browser),
                          str(SAILBOAT), str(shots)], capture_output=True, text=True, encoding="utf-8", timeout=900)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    result = json.loads(lines[-1])
    assert result["ok"], result.get("error", "") + "\n" + run.stderr[-3000:]
    assert result["console_errors"] == []
    assert result["create_label"] == "Import and prepare artwork" and "no sheets are planned" in result["not_planned_banner"]
    assert "One connected region of 4" in result["selection"]
    assert result["preview_palette"].startswith("5 → 5 colors") and "Apply to artwork" in result["preview_text"]
    assert result["history_after_cancel"] == 0
    assert "eyedropper" in result["eyedropper_preview"] or SUN in result["eyedropper_preview"]
    assert "eyedropper" in result["history_text"] and "palette color" in result["history_text"]
    assert "reproduces the saved artwork exactly" in result["verify"] and result["history_after_reload"] == 1
    assert "limited to the box" in result["box_selection"]
    # Zoom: buttons, Fit, Ctrl+wheel around the cursor; hit-testing and the eyedropper exact when zoomed.
    zoom = result["zoom"]
    pct = lambda label: int(label.split("%")[0])  # noqa: E731
    assert zoom["no_plus_minus_buttons"] and zoom["fit_active_at_start"] and not zoom["fit_active_when_zoomed"]
    assert pct(zoom["after_wheel"]) >= 3 * pct(zoom["fit"])  # plain wheel, no modifier
    assert not zoom["page_scrolled_while_zooming"] and zoom["page_scrolls_outside"]
    assert pct(zoom["fit"]) < pct(zoom["after_wheel_down"]) < pct(zoom["after_wheel"])  # wheel down zooms out
    assert zoom["anchor_drift_down_px"] < zoom["screen_px_per_art_px"] * 1.5
    near = lambda a, b: abs(pct(a) - pct(b)) <= 2  # noqa: E731 - fit depends on the viewport width (scrollbar)
    assert near(zoom["after_fit"], zoom["fit"]) and pct(zoom["after_key_plus"]) > pct(zoom["fit"]) + 5 and near(zoom["after_key_zero"], zoom["fit"])
    # The artwork pixel under the cursor stays under it while zooming (drift below one artwork pixel).
    assert zoom["anchor_drift_px"] < zoom["screen_px_per_art_px"], zoom
    assert "#AAD7F4" in result["zoomed_selection"] and "69 px" in result["zoomed_selection"]
    assert "#774627" in result["zoomed_eyedropper"]  # hull sampled a few pixels from two portholes
    # Pan: Space + drag and middle drag move the view (no edit); clicks stay exact afterwards.
    pan = result["pan"]
    assert pan["space_drag"] != pan["before"] and pan["middle_drag"] != pan["space_drag"] and pan["history"] == 1
    assert "#AAD7F4" in result["panned_selection"] and "69 px" in result["panned_selection"]
    # Multi-region: Shift+Click adds/removes, mixed colors, one batch edit, one undo step.
    multi = result["multi"]
    assert "3 regions selected" in multi["three"] and "2 different original colors" in multi["three"]
    assert multi["after_remove"].startswith("2 regions selected") and "in 2 regions" in multi["preview"]
    assert "2 regions" in multi["history"] and multi["try_again"] and multi["after_undo"] == 1  # entry #2 only
    assert "After 1 region recolor" in result["check_after_row"]
    # Review & edit canvas: wheel zoom toward the cursor, exact clicks at high zoom, panning never edits.
    rz = result["review_zoom"]
    assert int(rz["after_wheel"].split("%")[0]) >= 3 * int(rz["fit"].split("%")[0]) and not rz["page_scrolled"]
    assert rz["anchor_drift_px"] < rz["screen_px_per_image_px"] * 1.5
    assert rz["clicked_mm"] and all(abs(a - b) <= 0.15 for a, b in zip(rz["clicked_mm"], rz["target_mm"])), rz
    assert rz["panned"] and rz["pending_unchanged_by_pan"] and near(rz["after_fit"], rz["fit"])
    assert "Plan sheets from this artwork" in result["try_again"] and result["try_again_in_view"] and result["replan_label"] == "Plan sheets from this artwork"
