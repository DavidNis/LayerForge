"""Issue inspector, second part (``core/issue_review.py``, ``POST /api/projects/<p>/issues/review``).

What an issue shows in the processed image and why it exists (a small image detail, or the layer
planner), the alignment of the processed image with the layer, grouping of the same place across
layers, and review decisions (Needs review / Fix later / Accept risk) that persist by stable key and
lapse when the image, the geometry or the print settings change.  Nothing here may change geometry,
severities or which issues exist.

The synthetic stack (225 mm sheet at scale 1: 576 px, 32 px perimeter, 0.390625 mm/px):

* image: white, a 1 px black line (cols 100, rows 100-299, artwork pixels), a wide red block
  (rows 350-419, cols 200-299);
* sheets back to front: 0 black (everything), 1 red (open at the line, and at a hidden slot),
  2 white (open at the line and the red block), 3 red in front (the red block except a 1 px slit at
  col 250, which layer 1 shows).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from layerforge_l1.core import issue_review as rv
from layerforge_l1.core import issues as iss
from layerforge_l1.core.model import Sheet, SheetStack, StackGrid

from test_issue_inspector import L2_SLOT, SLOT, _prepare_sheets, _sailboat_with_red_spot, _tree  # noqa: F401
from test_l4_app import app, start  # noqa: F401 - shared fixtures

GRID = StackGrid.for_planner(225.0, 1)
MM, P, N = GRID.mm_per_pixel, GRID.perimeter_pixels, GRID.outer_pixels
WHITE, BLACK, RED = "#FFFFFF", "#000000", "#D02020"
LINE = (slice(100, 300), slice(100, 101))       # artwork pixels of the 1 px black line
BLOCK = (slice(350, 420), slice(200, 300))      # the wide red block
SLIT = (slice(350, 420), slice(250, 251))       # the column of the block layer 1 shows (front red open there)
HIDDEN = (slice(450, 451), slice(50, 150))      # a 1 px slot in layer 1 under the solid white layer 2


def outer(art: tuple[slice, slice]) -> tuple[slice, slice]:
    return slice(art[0].start + P, art[0].stop + P), slice(art[1].start + P, art[1].stop + P)


def _rgb(hex_: str) -> list[int]:
    return [int(hex_[i:i + 2], 16) for i in (1, 3, 5)]


def _stack() -> SheetStack:
    a = GRID.artwork_pixels
    target = np.empty((a, a, 3), np.uint8)
    target[:] = _rgb(WHITE)
    target[LINE] = _rgb(BLACK)
    target[BLOCK] = _rgb(RED)
    full = np.ones((N, N), bool)
    perimeter = GRID.perimeter_mask()

    def mask(*parts):
        m = np.zeros((N, N), bool)
        for part in parts:
            m[outer(part)] = True
        return m

    line, block, slit, hidden = mask(LINE), mask(BLOCK), mask(SLIT), mask(HIDDEN)
    s0 = Sheet("s0_black", BLACK, full.copy(), line.copy(), 1.6)
    s1 = Sheet("s1_red", RED, full & ~line & ~hidden, slit.copy(), 1.6)
    s2 = Sheet("s2_white", WHITE, full & ~line & ~block, ~perimeter & ~line & ~block, 1.6)
    s3 = Sheet("s3_red_front", RED, perimeter | (block & ~slit), block & ~slit, 1.6)
    return SheetStack("synthetic", GRID, [s0, s1, s2, s3], target_rgb=target)


def _issue(kind: str, sheet: str, mask: np.ndarray, threshold: float, issue_id: str = "issue_001", **extra) -> dict:
    outline, bbox, _ = iss.mask_outline(mask, GRID)
    return {"id": issue_id, "kind": kind, "sheet": sheet, "source": "geometry_check", "severity": "RISK",
            "outline_mm": outline, "bbox_mm": bbox, "at_mm": iss._centre(bbox), "details": {},
            "measured": {"label": "narrowest width", "value": extra.pop("measured", 0.39), "unit": "mm"},
            "threshold": {"label": "process", "value": threshold, "unit": "mm"}, **extra}


def _outer_mask(art: tuple[slice, slice]) -> np.ndarray:
    m = np.zeros((N, N), bool)
    m[outer(art)] = True
    return m


@pytest.fixture(scope="module")
def stack() -> SheetStack:
    return _stack()


def test_thin_image_detail_is_highlighted_on_its_exact_processed_image_pixels(stack) -> None:
    """An opening on the line: an image detail; the highlight is the line's own pixels, aligned with the layer."""

    features = rv.FeatureMap(stack)
    line = _outer_mask(LINE)
    for sheet in ("s2_white", "s1_red"):  # every layer in front of the black layer opens at the line
        f = features.attribute(_issue("opening_may_close", sheet, line, 0.8))
        assert f["cause"] == "image" and f["role"] == "opening" and "Small detail in the processed image" in f["cause_text"]
        (region,) = f["regions"]
        assert region["color"] == BLACK and region["narrow"] and region["width_mm"] == pytest.approx(MM, abs=1e-3)
        assert region["shown_by"] == ["s0_black"] and region["revealed_pixels"] == 200
        # The highlight drawn on the processed image is exactly the revealed pixels and the black region.
        assert np.array_equal(rv.outline_mask(f["revealed_outline_mm"], GRID), line)
        assert np.array_equal(rv.outline_mask(region["outline_mm"], GRID), line) and not region["outline_clipped"]
        assert region["area_mm2"] == pytest.approx(200 * MM * MM, abs=1e-3)
    # Alignment: the same sheet-mm outline lands on the line's artwork pixels (processed image grid).
    outline, bbox, _ = iss.mask_outline(line, GRID)
    assert iss.artwork_box(bbox, GRID) == [100, 100, 300, 101]
    assert (stack.target_rgb[100:300, 100] == _rgb(BLACK)).all()
    assert np.array_equal(rv.outline_mask(outline, GRID)[outer(LINE)], np.ones((200, 1), bool))
    # A windowed footprint is the same pixels as the full one.
    win = rv.bbox_window(bbox, GRID, 7)
    assert np.array_equal(rv.outline_mask(outline, GRID, win), line[win])


def test_planner_split_and_hidden_openings_are_planner_issues(stack) -> None:
    """A narrow opening that is not narrow in the image (a wide region split across layers), or that shows
    nothing at all (hidden under the front layers), is a planner issue: no image detail is claimed."""

    features = rv.FeatureMap(stack)
    f = features.attribute(_issue("narrow_slot_or_gap", "s3_red_front", _outer_mask(SLIT), 0.8))
    assert f["cause"] == "planner" and "Introduced by the layer planner" in f["cause_text"]
    (region,) = f["regions"]
    assert region["color"] == RED and not region["narrow"] and region["width_mm"] >= 0.8
    assert region["shown_by"] == ["s1_red"] and region["outline_clipped"]  # a large region is outlined near the issue only
    hidden = features.attribute(_issue("opening_may_close", "s1_red", _outer_mask(HIDDEN), 0.8))
    assert hidden["cause"] == "planner" and hidden["regions"] == [] and hidden["revealed_outline_mm"] is None
    assert hidden["covered_by"] == ["s2_white"] and hidden["hidden_pixels"] == 100
    assert "Nothing of the image shows through this opening" in hidden["cause_text"]
    # Material: the layer's own visible pixels are measured (the black line shown by layer 0).
    m = features.attribute(_issue("thin_material", "s0_black", _outer_mask(LINE), 0.84))
    assert m["role"] == "material" and m["cause"] == "image" and m["regions"][0]["color"] == BLACK
    # Kinds without a width (floating material, perimeter gaps, ...) and artwork findings are not classified.
    assert features.attribute(_issue("floating_material", "s1_red", _outer_mask(LINE), 1.2)) is None
    assert rv.artwork_cause({"source": "planner", "kind": "planner_blocked_feature"})["cause"] == "planner"
    assert rv.artwork_cause({"source": "intake", "kind": "intake_detail_changed"})["cause"] == "image"


def test_real_geometry_check_issues_are_attributed_and_grouped_across_layers(stack) -> None:
    """The FDM geometry check on the synthetic sheets: the line opening on layers 1 and 2 is one place
    (a group of two, each with its own measurement), an image detail; the slit and the hidden slot are planner issues."""

    from layerforge_l1.core.printkit import PrintProcess, assess_printability

    report = assess_printability(stack, PrintProcess())
    located, _ = iss.fdm_issues(report, source="geometry_check")
    issues = iss.number([i for i in located if i["severity"] != "INFO"] + [i for i in located if i["severity"] == "INFO"], GRID)
    features = rv.FeatureMap(stack)
    for issue in issues:
        issue["feature"] = features.attribute(issue)
    rv.assign_keys(issues)
    groups = rv.group_by_location(issues, GRID)
    x, y = (100 + P + 0.5) * MM, (N - P - 200) * MM  # the middle of the line
    at_line = [i for i in issues if i["bbox_mm"][0] < x < i["bbox_mm"][2] and i["bbox_mm"][1] < y < i["bbox_mm"][3]
               and i["kind"] in ("opening_may_close", "narrow_slot_or_gap")]
    assert {i["sheet"] for i in at_line} == {"s1_red", "s2_white"}
    assert all(i["feature"]["cause"] == "image" and i["feature"]["regions"][0]["color"] == BLACK for i in at_line)
    group = next(g for g in groups if g["id"] == at_line[0]["group"])
    assert set(group["sheets"]) == {"s1_red", "s2_white"} and {i["group"] for i in at_line} == {group["id"]}
    assert all(i["measured"]["value"] is not None for i in at_line)  # each layer keeps its own measurement
    slit = [i for i in issues if i["sheet"] == "s3_red_front" and i["kind"] in rv.ROLES and i["feature"]["role"] == "opening"]
    assert slit and all(i["feature"]["cause"] == "planner" for i in slit)
    hidden = [i for i in issues if i["sheet"] == "s1_red" and i["bbox_mm"][1] < (N - P - 450.5) * MM < i["bbox_mm"][3]]
    assert hidden and hidden[0]["feature"]["cause"] == "planner" and hidden[0]["feature"]["regions"] == []
    assert len({i["key"] for i in issues}) == len(issues)


def test_grouping_by_place_keeps_each_layer_measurement() -> None:
    line = _outer_mask(LINE)
    near = _outer_mask((slice(100, 300), slice(102, 103)))  # 1 px gap: the same place (tolerance 3 px)
    far = _outer_mask((slice(10, 20), slice(400, 401)))
    issues = [_issue("opening_may_close", "a", line, 0.8, "issue_001", measured=0.31),
              _issue("narrow_slot_or_gap", "b", near, 0.8, "issue_002", measured=0.52),
              _issue("opening_may_close", "c", line, 0.8, "issue_003", measured=0.44),
              _issue("opening_may_close", "a", far, 0.8, "issue_004", measured=0.2),
              {**_issue("intake_detail_changed", None, line, 0.8, "issue_005"), "sheet": None}]
    rv.assign_keys(issues)
    groups = rv.group_by_location(issues, GRID)
    big = groups[0]
    assert big["members"] == ["issue_001", "issue_002", "issue_003"] and big["sheets"] == ["a", "b", "c"]
    assert [i["measured"]["value"] for i in issues[:3]] == [0.31, 0.52, 0.44]  # unchanged per layer
    assert issues[3]["group"] != big["id"] and "group" not in issues[4]  # elsewhere; artwork findings are not grouped
    assert len(groups) == 2
    again = [dict(i) for i in issues]
    rv.assign_keys(again)
    assert rv.group_by_location(again, GRID)[0]["id"] == big["id"]  # stable ids


def test_review_decisions_persist_by_key_and_lapse_with_what_changed() -> None:
    def issue(key_box, fp, severity="RISK"):
        return {"id": "issue_001", "kind": "opening_may_close", "sheet": "a", "source": "geometry_check",
                "severity": severity, "bbox_mm": key_box, "measured": {"value": 0.5}, "details": {},
                "fingerprint": dict(fp)}

    fp = {"measurement": "m1", "settings": "s1", "artwork": "a1", "geometry": "g1"}
    first = issue([10, 10, 12, 11], fp)
    rv.assign_keys([first])
    decisions = {first["key"]: rv.decision_record(first, "accept_risk", "2026-11-08T10:00:00+00:00")}
    with pytest.raises(ValueError):
        rv.decision_record(first, "ignore", "now")
    same = issue([10, 10, 12, 11], fp)
    rv.assign_keys([same])
    summary = rv.apply_decisions([same], decisions)
    assert same["review"]["state"] == "accept_risk" and "lapsed" not in same["review"]
    assert summary["counts"]["accept_risk"] == 1 and summary["unmatched"] == 0
    assert same["severity"] == "RISK" and same["measured"] == {"value": 0.5}  # never suppressed or changed
    for changed, words in (({"geometry": "g2"}, "the layer geometry"), ({"artwork": "a2"}, "the processed image"),
                           ({"settings": "s2", "measurement": "m2"}, "the measurement and the print settings")):
        now = issue([10, 10, 12, 11], {**fp, **changed})
        rv.assign_keys([now])
        rv.apply_decisions([now], decisions)
        assert now["review"]["state"] == "needs_review" and now["review"]["lapsed"]["state"] == "accept_risk"
        assert now["review"]["lapsed"]["reason"] == words + " changed"
    moved = issue([10.6, 10, 12.6, 11], fp)  # another key, overlapping place: the issue moved
    rv.assign_keys([moved])
    assert moved["key"] != first["key"]
    rv.apply_decisions([moved], decisions)
    assert moved["review"]["lapsed"]["changed"] == ["location"] and moved["review"]["lapsed"]["key"] == first["key"]
    gone = issue([100, 100, 101, 101], fp)
    rv.assign_keys([gone])
    assert rv.apply_decisions([gone], decisions)["unmatched"] == 1 and gone["review"] == {"state": "needs_review"}


def test_review_api_persists_and_lapses_after_an_edit_and_a_settings_change(app, tmp_path) -> None:
    """Accept risk / Fix later are saved per issue key; the geometry, the checks and the issue stay as they
    were; a layer edit at the issue and a new print process make the decisions lapse; bad requests are refused."""

    client, workspace = app
    name, sheets = _prepare_sheets(client, _sailboat_with_red_spot(tmp_path))
    front, second = sheets[-1]["id"], sheets[-2]["id"]
    data = client.ok("GET", f"/api/projects/{name}/issues")
    assert data["review"] == {"counts": {"needs_review": len(data["issues"]), "fix_later": 0, "accept_risk": 0},
                              "lapsed": 0, "unmatched": 0, "artwork_edited": False}
    (slot,) = [i for i in data["issues"] if i["kind"] == "opening_may_close" and i["sheet"] == front]
    (slot2,) = [i for i in data["issues"] if i["kind"] == "opening_may_close" and i["sheet"] == second]
    assert slot2["feature"]["role"] == "opening" and slot2["feature"]["cause"] in ("image", "planner")
    before = {part: _tree(workspace / name / part) for part in ("review", "draft", "proposal")}
    after = client.ok("POST", f"/api/projects/{name}/issues/review", {"keys": [slot2["key"]], "state": "accept_risk"})
    after = client.ok("POST", f"/api/projects/{name}/issues/review", {"keys": [slot["key"]], "state": "fix_later"})
    assert {part: _tree(workspace / name / part) for part in ("review", "draft", "proposal")} == before  # decision only
    saved = json.loads((workspace / name / "issue_reviews.json").read_text(encoding="utf-8"))
    assert {k: v["state"] for k, v in saved["decisions"].items()} == {slot2["key"]: "accept_risk", slot["key"]: "fix_later"}
    by_key = {i["key"]: i for i in after["issues"]}
    assert by_key[slot2["key"]]["review"]["state"] == "accept_risk" and by_key[slot["key"]]["review"]["state"] == "fix_later"
    strip = lambda i: {k: v for k, v in i.items() if k != "review"}  # noqa: E731
    assert [strip(i) for i in after["issues"]] == [strip(i) for i in data["issues"]]  # warnings kept, unchanged
    assert after["review"]["counts"] == {"needs_review": len(data["issues"]) - 2, "fix_later": 1, "accept_risk": 1}
    reread = client.ok("GET", f"/api/projects/{name}/issues")  # persisted
    assert {i["key"]: i["review"]["state"] for i in reread["issues"]} == {i["key"]: i["review"]["state"] for i in after["issues"]}
    for body in ({"keys": [slot["key"]], "state": "ignore"}, {"keys": ["nope"], "state": "fix_later"}, {"keys": [], "state": "fix_later"}):
        status, _ = client.call("POST", f"/api/projects/{name}/issues/review", body)
        assert status == 400

    # A layer edit at the accepted opening (widen part of it): that decision lapses, the other one stays.
    client.run(name, "edit", {"op": {"op": "subtract", "sheet": second,
                                     "shape": {"type": "circle", "center_mm": [52.0, 130.0], "radius_mm": 1.0}}})
    edited = client.ok("GET", f"/api/projects/{name}/issues")
    near = [i for i in edited["issues"] if i["sheet"] == second and i["kind"] in ("opening_may_close", "narrow_slot_or_gap")]
    assert near and all(i["review"]["state"] == "needs_review" for i in near)
    assert any(i["review"].get("lapsed", {}).get("state") == "accept_risk" for i in near) or edited["review"]["unmatched"] >= 1
    (slot_now,) = [i for i in edited["issues"] if i["key"] == slot["key"]]
    assert slot_now["review"]["state"] == "fix_later"  # far from the edit: still applies

    # Other print settings: the front slot's decision lapses (its settings and measurement changed).
    strict = {"name": "strict", "source": "test", "nozzle_diameter_mm": 0.4, "line_width_mm": 0.42, "layer_height_mm": 0.2,
              "first_layer_height_mm": 0.2, "min_opening_mm": 1.0, "min_positive_feature_mm": 1.1, "bed_x_mm": 256,
              "bed_y_mm": 256, "max_z_mm": 256, "filament_density_g_cm3": 1.24}
    client.run(name, "printability", {"process": strict})
    stricter = client.ok("GET", f"/api/projects/{name}/issues")
    (slot_strict,) = [i for i in stricter["issues"] if i["key"] == slot["key"]]
    assert slot_strict["review"]["state"] == "needs_review" and slot_strict["review"]["lapsed"]["state"] == "fix_later"
    assert "settings" in slot_strict["review"]["lapsed"]["changed"]
    assert slot_strict["severity"] == slot["severity"]


def test_browser_issue_review(app, tmp_path) -> None:
    """In a real browser (``tests/e2e/drive_issue_review.mjs``): the processed image beside the layer, both
    focused on the selected issue and synchronized under wheel zoom, pan, Previous / Next and Fit; the
    revealed pixels highlighted on the processed image at the issue; cause and colors in the card; the same
    place on another layer in one click; Accept risk / Same for all layers / Fix later / Needs review saved
    and kept after a reload with the warning unchanged; a narrow window stacks the views."""

    import subprocess

    from test_l4_app import BROWSERS, ROOT, _node_ok

    client, workspace = app
    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    name, sheets = _prepare_sheets(client, _sailboat_with_red_spot(tmp_path))
    # The same slot on layer 2 as on layer 1: one place on two layers.
    client.run(name, "edit", {"op": {"op": "subtract", "sheet": sheets[-2]["id"], "shape": SLOT}})
    shots = ROOT / "artifacts" / "issue_review_v1" / "screens"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_issue_review.mjs"), client.base + "/", str(browser), "-",
                          str(shots), name], capture_output=True, text=True, encoding="utf-8", timeout=900)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    result = json.loads(lines[-1])
    (shots.parent / "drive_result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    assert result["ok"], result.get("error", "") + "\nconsole: " + str(result.get("console_errors")) + "\n" + run.stderr[-3000:]
    assert result["console_errors"] == []
    drawn = lambda px: px is not None and px[3] > 0  # noqa: E731
    start = result["start"]
    assert start["art_views"] == 1 and "Processed image" in start["art_caption"] and start["shown"].startswith("Layer 1")
    assert start["sizes"][0] == GRID.outer_pixels  # padded with the perimeter band: the same sheet extent as the layer
    assert start["same_as_target"]["diff"] == 0 and start["same_as_target"]["band"] == 32  # the planned target, unchanged
    assert start["group_sheets"] == 2 and start["mate"]
    sel = result["select"]
    assert sel["offsets"]["diff"] < 1.0 and abs(sel["offsets"]["scale_ratio"] - 1) < 1e-3  # same place, same scale
    assert sel["cause"] == sel["api_cause"] and sel["cause"] in ("image", "planner") and sel["cause_text"]
    assert sel["api_colors"] and all(c in sel["reveals"] for c in sel["api_colors"])
    assert sel["pixel_under"] in sel["api_colors"]  # the processed image under the issue is the revealed color
    assert drawn(sel["overlay_in"]) and not drawn(sel["overlay_far"]) and drawn(sel["layer_overlay_in"])
    assert sel["mates"] == 1 and "1 other layer" in sel["group"] and "L2" in sel["group"]
    assert "0.8" in sel["threshold"] and "Layer 1 (from front)" in sel["where"] and sel["state"] == "needs_review"
    assert sel["offsets_after_shots"]["diff"] < 1.0 and sel["states_after_el"][0] == sel["states_after_el"][1]  # page capture
    for step in ("wheel", "pan", "after_resize"):
        assert result[step]["offsets"]["diff"] < 1.0 and abs(result[step]["offsets"]["scale_ratio"] - 1) < 1e-3, step
    assert result["wheel"]["offsets"]["layer"]["w"] > sel["offsets"]["layer"]["w"]  # zoomed in
    mate = result["mate"]
    assert mate["shown"].startswith("Layer 2") and mate["title"] == "Opening may close" and mate["card_key"] == mate["api_key"]
    assert mate["offsets"]["diff"] < 1.0 and "L1" in mate["back_mates"]
    review = result["review"]
    assert "geometry and the warning are unchanged" in review["accepted_note"] and review["group_button"] == 1
    assert review["badge"] == "RISK" and review["api_states"] == ["accept_risk", "accept_risk"]
    assert set(review["api_severities"]) == {"RISK"} and review["tags"] >= 1 and "2 accepted" in review["summary"]
    assert review["filtered_rows"] == 1
    reload = result["reload"]
    assert reload["state"] == "accept_risk" and reload["badge"] == "RISK" and reload["rows"] == reload["api_rows"]
    assert reload["after_reset"] == "needs_review"
    assert result["next"]["title"] and (result["next"]["offsets"] is None or result["next"]["offsets"]["diff"] < 1.0)
    assert result["fit"][0] == result["fit"][1] and result["fit_offsets"]["diff"] < 1.0
    narrow = result["narrow"]
    assert narrow["art_above_layer"] and narrow["side_below"] and narrow["page_w"] <= 600
