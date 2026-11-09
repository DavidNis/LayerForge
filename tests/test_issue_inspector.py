"""Issue inspector (``core/issues.py``, ``GET /api/projects/<p>/issues``): located diagnostics.

Every issue comes from an existing check (sheet validation, the FDM geometry check of the shown
sheets or a current saved estimate, the proposal's obstruction map, unmatched-region map and intake
details); these tests check that the reported locations are the exact pixels/polygons of the finding
on the right layer, that thresholds are the configured ones, that the edit destination (layer or
artwork) is right, that intake findings stay on the artwork, that a valid layer has no issue, that
results refresh from new checks after an edit, and that reading issues changes nothing.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from shapely.geometry import Polygon

from layerforge_l1.core import issues as iss
from layerforge_l1.core.model import StackGrid

from test_l4_app import SAILBOAT, SLOT, app, start  # noqa: F401 - shared fixtures

GRID = StackGrid.for_planner(225.0, 1)  # the sailboat grid: 576 px outer, 32 px perimeter, 0.390625 mm/px
MM = GRID.mm_per_pixel


def _area(outline) -> float:
    return sum(Polygon(poly[0], poly[1:]).area for poly in outline)


def _tree(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*"))
            if p.is_file() and "printability" not in p.parts}


def test_mask_outlines_follow_pixel_edges_and_map_to_artwork_pixels() -> None:
    mask = np.zeros((GRID.outer_pixels,) * 2, bool)
    mask[100:103, 200:260] = True  # a 3 px x 60 px bar
    mask[101, 260:262] = True      # a one-pixel step
    outline, bbox, exact = iss.mask_outline(mask, GRID)
    assert exact and _area(outline) == pytest.approx(mask.sum() * MM * MM, rel=1e-9)
    n = GRID.outer_pixels
    assert bbox == [round(200 * MM, 6), round((n - 103) * MM, 6), round(262 * MM, 6), round((n - 100) * MM, 6)]
    p = GRID.perimeter_pixels
    assert iss.artwork_box(bbox, GRID) == [100 - p, 200 - p, 103 - p, 262 - p]  # same pixels on the artwork grid
    for r0, c0, r1, c1 in ([0, 0, 1, 1], [511, 511, 512, 512], [37, 400, 90, 401]):
        m = np.zeros_like(mask)
        m[r0 + p:r1 + p, c0 + p:c1 + p] = True
        assert iss.artwork_box(iss.mask_outline(m, GRID)[1], GRID) == [r0, c0, r1, c1]


RED_SPOT = (slice(150, 156), slice(120, 128))  # artwork rows/cols of an off-palette red spot in the sky
NECK = {"disc": {"type": "circle", "center_mm": [60.0, 175.0], "radius_mm": 3.0},
        "bar": {"type": "bar", "from_mm": [58.0, 175.0], "to_mm": [8.0, 175.0], "width_mm": 1.0}}
L2_SLOT = {"type": "bar", "from_mm": [30.0, 130.0], "to_mm": [75.0, 130.0], "width_mm": 0.5}  # visible sky of layer 2
DISC = {"type": "circle", "center_mm": [60.0, 150.0], "radius_mm": 2.0}
SPIKE = {"type": "bar", "from_mm": [150.0, 70.0], "to_mm": [150.0, 110.0], "width_mm": 0.5}


def _sailboat_with_red_spot(folder) -> bytes:
    """The sailboat with a small red spot the palette does not keep (an intake detail, not a sheet defect)."""

    import cv2

    from layerforge_l1.core.artwork import load_rgb

    img = load_rgb(SAILBOAT).copy()
    img[RED_SPOT] = (200, 30, 40)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "sailboat_red_spot.png"
    cv2.imwrite(str(path), img[..., ::-1])
    return path.read_bytes()


def _prepare_sheets(client, image: bytes, printability: bool = False) -> tuple[str, list[dict]]:
    """A reviewed sailboat with known defects: on layer 1 (front) a 0.6 mm slot, a floating disc, a disc held
    by a 1.0 mm bar and a 0.5 mm spike; on layer 2 a visible 0.5 mm slot."""

    name = client.ok("POST", "/api/projects?filename=sailboat_red_spot.png&scale=1", raw=image)["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    client.run(name, "review", {"candidate": client.state(name)["proposal"]["selected"], "widen_openings": False})  # sheets as planned
    sheets = client.state(name)["draft"]["sheets"]
    front, second = sheets[-1]["id"], sheets[-2]["id"]
    for sheet, op, shape in ((front, "subtract", SLOT), (front, "add", DISC), (front, "add", NECK["disc"]),
                             (front, "add", NECK["bar"]), (second, "subtract", L2_SLOT), (front, "add", SPIKE)):
        client.run(name, "edit", {"op": {"op": op, "sheet": sheet, "shape": shape}})
    if printability:
        client.run(name, "printability", {})
    return name, sheets


def test_candidate_sheets_are_checked_and_intake_warnings_stay_on_the_artwork(app, tmp_path):
    """Before any review or estimate the proposed sheets are checked with the configured thresholds; a valid
    candidate has no sheet issues; the intake's changed details are artwork findings at the detail's pixels."""

    from layerforge_l1.core.printkit import PrintProcess

    client, workspace = app
    name = client.ok("POST", "/api/projects?filename=sailboat_red_spot.png&scale=1",
                     raw=_sailboat_with_red_spot(tmp_path))["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    proposal = json.loads((workspace / name / "proposal" / "proposal.json").read_text(encoding="utf-8"))
    assert "INTAKE_DETAILS_CHANGED" in {f["code"] for f in proposal["flags"]}
    before = _tree(workspace / name)
    data = client.ok("GET", f"/api/projects/{name}/issues")
    assert _tree(workspace / name) == before  # read only: no estimate file, no review is created
    assert data["sheet_source"] == "candidate" and len(data["sheets"]) == 6 and not data["has_review"]
    assert {s["id"] for s in data["sources"]} == {"candidate", "geometry_check", "proposal"}
    default = PrintProcess()
    assert data["checks"] == {"geometry_source": f"Geometry check of these sheets ({default.name})", "structural_width_mm": 1.2,
                              "min_opening_mm": default.min_opening_mm, "nozzle_diameter_mm": default.nozzle_diameter_mm,
                              "min_positive_feature_mm": default.min_positive_feature_mm, "line_width_mm": default.line_width_mm}
    # A valid candidate: no issue on any sheet (nothing is made up to fill the inspector).
    assert [i for i in data["issues"] if i["sheet"] or i["destination"] == "layer"] == []
    # Intake: each changed detail on the artwork (its saved box), never on a sheet; INTAKE_DETAILS_CHANGED is located.
    details = [i for i in data["issues"] if i["kind"] == "intake_detail_changed"]
    assert len(details) == len(proposal["intake_details"]) >= 2
    assert all(i["sheet"] is None and i["destination"] == "artwork" and not i["also_artwork"] and i["source"] == "intake"
               and not i["outline_exact"] and i["details"]["image_url"] for i in details)
    (spot,) = [i for i in details if i["artwork_box_px"] == [RED_SPOT[0].start, RED_SPOT[1].start, RED_SPOT[0].stop, RED_SPOT[1].stop]]
    p, n = GRID.perimeter_pixels, GRID.outer_pixels
    assert spot["bbox_mm"] == pytest.approx([(120 + p) * MM, (n - p - 156) * MM, (128 + p) * MM, (n - p - 150) * MM], abs=1e-6)
    assert spot["details"]["what"] == "color missing from the palette" and spot["severity"] == "WARN"
    assert not any(u["code"] == "INTAKE_DETAILS_CHANGED" for u in data["unlocalized"])


def test_sheet_findings_are_located_with_the_configured_thresholds_and_refresh(app, tmp_path):
    """Server: known defects on two layers located at their exact places by the existing checks (the
    geometry check runs on the review's sheets without an estimate); a saved estimate's process is used
    when current; an edit refreshes the locations; reading changes nothing."""

    client, workspace = app
    name, sheets = _prepare_sheets(client, _sailboat_with_red_spot(tmp_path))
    front, second = sheets[-1]["id"], sheets[-2]["id"]
    before = {part: _tree(workspace / name / part) for part in ("review", "draft", "proposal")}
    data = client.ok("GET", f"/api/projects/{name}/issues")
    assert {part: _tree(workspace / name / part) for part in ("review", "draft", "proposal")} == before  # read only
    assert not (workspace / name / "printability").exists()
    assert data["sheet_source"] == "review" and data["basis"] == f"draft:{client.state(name)['draft']['updated_utc']}"
    assert {s["id"]: s["stale"] for s in data["sources"]} == {"validation": False, "geometry_check": False, "proposal": False}
    by_kind = {}
    for issue in data["issues"]:
        by_kind.setdefault(issue["kind"], []).append(issue)

    # Narrow opening on layer 1: the slot itself, measured against the process thresholds (not a fixed rule).
    (slot,) = [i for i in by_kind["opening_may_close"] if i["sheet"] == front]
    assert slot["source"] == "geometry_check" and slot["bbox_mm"] == pytest.approx([40.0, 39.7, 100.0, 40.3], abs=0.01)
    assert slot["outline_exact"] and _area(slot["outline_mm"]) == pytest.approx(60 * 0.6, rel=0.02)
    assert slot["measured"]["value"] == pytest.approx(0.6, abs=0.02) and slot["threshold"]["value"] == 0.8
    assert slot["threshold"]["unsupported_below_mm"] == 0.4 and slot["severity"] == "RISK"
    assert slot["destination"] == "layer" and slot["also_artwork"] and slot["category"] == "narrow_opening"
    p = GRID.perimeter_pixels
    r = GRID.outer_pixels - 1 - int(40.0 / MM) - p
    box = slot["artwork_box_px"]
    assert box[0] <= r < box[2] and box[1] == int(40.0 / MM) - p and box[3] == int(np.ceil(100.0 / MM)) - p
    # Narrow opening on layer 2: located on that layer, not on layer 1.
    (slot2,) = [i for i in by_kind["opening_may_close"] if i["sheet"] == second]
    assert slot2["bbox_mm"] == pytest.approx([30.0, 129.75, 75.0, 130.25], abs=MM)
    assert slot2["measured"]["value"] == pytest.approx(0.5, abs=0.05) and slot2["severity"] == "RISK"
    # Narrow retained connection on layer 1: the 1.0 mm bar (the neck), below the structural width.
    (neck,) = [i for i in by_kind["narrow_connection"] if i["sheet"] == front]
    x0, y0, x1, y1 = neck["bbox_mm"]
    assert 8.0 - MM <= x0 and x1 <= 58.0 + MM and 174.0 <= y0 and y1 <= 176.0 and x1 - x0 > 30  # along the bar only
    assert neck["severity"] == "FAIL" and neck["measured"]["value"] < neck["threshold"]["value"]
    assert neck["threshold"]["nominal_mm"] == 1.2 and neck["details"]["held_material_outline_mm"]
    held = Polygon(neck["details"]["held_material_outline_mm"][0][0])
    assert held.contains(Polygon([(59, 174), (61, 174), (61, 176), (59, 176)]))  # the disc it holds
    # Disconnected material: the floating disc (not a narrow connection).
    (disc,) = by_kind["floating_material"]
    assert disc["sheet"] == front and disc["severity"] == "FAIL" and disc["category"] == "disconnected"
    assert disc["bbox_mm"] == pytest.approx([58.0, 148.0, 62.0, 152.0], abs=MM)
    assert disc["measured"]["value"] == pytest.approx(np.pi * 4, rel=0.1)
    # Thin material: the 0.5 mm spike, against min_positive_feature_mm.
    thin = [i for i in by_kind["thin_material"] if i["sheet"] == front and i["bbox_mm"][0] > 149 and i["bbox_mm"][2] < 151]
    assert thin and thin[0]["measured"]["value"] == pytest.approx(0.5, abs=0.02) and thin[0]["threshold"]["value"] == 0.84
    assert thin[0]["bbox_mm"][3] == pytest.approx(110.25, abs=0.3)
    # Front view vs artwork: exactly the mismatched pixels the validation counted (whole stack, no layer).
    (mismatch,) = by_kind["front_view_mismatch"]
    recon = client.state(name)["draft"]["validation"]["reconstruction"]
    assert mismatch["measured"]["value"] == recon["mismatched_pixels"] and mismatch["sheet"] is None
    assert _area(mismatch["outline_mm"]) == pytest.approx(recon["mismatched_pixels"] * MM * MM, rel=1e-6)
    # Layers without an edit have no issues; intake details are artwork findings only.
    clean = [s["id"] for s in sheets if not any(i["sheet"] == s["id"] for i in data["issues"])]
    assert len(clean) == 4 and front not in clean and second not in clean
    assert all(i["sheet"] is None for i in by_kind["intake_detail_changed"])

    # A saved estimate of these sheets with a stricter process: its own thresholds are used.
    strict = {"name": "strict", "source": "test", "nozzle_diameter_mm": 0.4, "line_width_mm": 0.42, "layer_height_mm": 0.2,
              "first_layer_height_mm": 0.2, "min_opening_mm": 1.0, "min_positive_feature_mm": 1.1, "bed_x_mm": 256,
              "bed_y_mm": 256, "max_z_mm": 256, "filament_density_g_cm3": 1.24}
    client.run(name, "printability", {"process": strict})
    data = client.ok("GET", f"/api/projects/{name}/issues")
    assert {s["id"]: s["stale"] for s in data["sources"]} == {"validation": False, "fdm_estimate": False, "proposal": False}
    (slot,) = [i for i in data["issues"] if i["kind"] == "opening_may_close" and i["sheet"] == front]
    assert slot["threshold"]["value"] == 1.0 and slot["details"]["process"] == "strict" and slot["source"] == "fdm_estimate"
    assert data["checks"]["min_opening_mm"] == 1.0
    estimate = client.state(name)["printability"]
    assert sum(1 for i in data["issues"] if i["source"] == "fdm_estimate" and i["severity"] != "INFO") == \
        estimate["counts"]["RISK"] + estimate["counts"]["UNSUPPORTED"] - sum(
            1 for f in estimate["features"] if f["severity"] != "INFO" and f["kind"] not in iss.KINDS)

    # A layer edit: the sheets are checked again at once (the estimate's process); the saved estimate is stale.
    old_basis = data["basis"]
    client.run(name, "undo", {})  # the spike
    data = client.ok("GET", f"/api/projects/{name}/issues")
    assert data["basis"] != old_basis
    sources = {s["id"]: s for s in data["sources"]}
    assert sources["fdm_estimate"]["stale"] and "Run the printability estimate again" in sources["fdm_estimate"]["stale_reason"]
    assert not sources["geometry_check"]["stale"] and "strict" in sources["geometry_check"]["label"]
    assert not any(i["kind"] == "thin_material" and i["bbox_mm"][0] > 149 and i["bbox_mm"][2] < 151 for i in data["issues"])
    assert not any(i["source"] == "fdm_estimate" for i in data["issues"])  # no old locations drawn as current
    (slot,) = [i for i in data["issues"] if i["kind"] == "opening_may_close" and i["sheet"] == front]
    assert slot["source"] == "geometry_check" and slot["threshold"]["value"] == 1.0
    # The project state stays small: outlines are served by /issues only.
    assert all("outline_mm" not in f for f in client.state(name)["printability"]["features"])


def test_failed_proposal_shows_the_blocked_artwork_pixels_and_no_sheet_issues(app, monkeypatch):
    """The planner's obstruction map of a failed closure: artwork-level issues with exact pixels; no sheets are
    created for editing.  The failure is simulated (planning raises with a known obstruction mask); the map is
    saved and read back by the real code."""

    import layerforge_l1.core.propose as core_propose
    from layerforge_l1.core.planning import PlanningError

    obstruction = np.zeros((512, 512), bool)
    obstruction[100:104, 100:300] = True
    obstruction[300:310, 400:405] = True

    def fails(target, config, name="stack"):
        raise PlanningError("reveal closure is infeasible: some cells can never be supported (simulated)",
                            {"closure_feasible": False}, obstruction)

    monkeypatch.setattr(core_propose, "plan_stack", fails)
    client, workspace = app
    name = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    assert client.state(name)["proposal"]["status"] == "NEEDS_USER_DECISION"
    data = client.ok("GET", f"/api/projects/{name}/issues")
    assert "sheet-level locations are unavailable" in data["notice"] and data["sheets"] == []
    assert [i["key"] for i in data["images"]] == ["artwork"] and not data["has_review"]
    blocked = sorted((i for i in data["issues"] if i["kind"] == "planner_blocked_feature"), key=lambda i: i["artwork_box_px"])
    assert [i["artwork_box_px"] for i in blocked] == [[100, 100, 104, 300], [300, 400, 310, 405]]
    proposal = json.loads((workspace / name / "proposal" / "proposal.json").read_text(encoding="utf-8"))
    for issue, pixels in zip(blocked, (4 * 200, 10 * 5)):
        assert issue["destination"] == "artwork" and not issue["also_artwork"] and issue["sheet"] is None
        assert _area(issue["outline_mm"]) == pytest.approx(pixels * MM * MM, rel=1e-9)
        assert issue["threshold"]["value"] == proposal["intake"]["structural_width_mm"]
    from layerforge_l1.core.artwork import load_rgb
    from layerforge_l1.core.resize import obstruction_issues

    target = load_rgb(workspace / name / "proposal" / "intake" / "target.png")
    expected = obstruction_issues(obstruction, target, MM, proposal["intake"]["structural_width_mm"])
    assert sorted(i["measured"]["value"] for i in blocked) == sorted(r["width_mm"] for r in expected)  # the existing measure
    assert not any(i["source"] in ("validation", "fdm_estimate", "geometry_check") for i in data["issues"])
    assert all(i["sheet"] is None and i["destination"] == "artwork" for i in data["issues"])
    assert [s["id"] for s in data["sources"]] == ["proposal"] and data["checks"] is None
    # Artwork edits after the proposal: its issues are marked stale until planning again.
    client.ok("POST", f"/api/projects/{name}/artwork/session/add", {"op": {"at_px": [480, 40], "color": "#F9C832"}})
    data = client.ok("GET", f"/api/projects/{name}/issues")
    assert {s["id"]: s["stale"] for s in data["sources"]} == {"proposal": True}


def test_browser_issue_inspector(app, monkeypatch, tmp_path):
    """In a real browser (``tests/e2e/drive_issues.mjs``): problems outlined and numbered on their own layer,
    overlay drawn exactly on the geometry at several zoom levels, a narrow opening and a narrow connection
    selected (zoomed, measured vs threshold), a click after zoom and pan, issues on a second layer, a layer
    with none, intake findings only on the artwork, Edit layer opening the editor on the sheet and location,
    locations marked out of date after an edit until re-checked, a valid candidate, a failed proposal."""

    import subprocess

    import layerforge_l1.core.propose as core_propose
    from layerforge_l1.core.planning import PlanningError
    from test_l4_app import BROWSERS, ROOT, _node_ok

    client, workspace = app
    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    image = _sailboat_with_red_spot(tmp_path)
    reviewed, _ = _prepare_sheets(client, image)
    candidate = client.ok("POST", "/api/projects?filename=sailboat_red_spot.png&scale=1", raw=image)["project"]
    client.wait({"id": client.state(candidate)["job"]["id"]})

    obstruction = np.zeros((512, 512), bool)
    obstruction[100:104, 100:300] = True

    def fails(target, config, name="stack"):
        raise PlanningError("reveal closure is infeasible (simulated)", {"closure_feasible": False}, obstruction)

    with monkeypatch.context() as patch:
        patch.setattr(core_propose, "plan_stack", fails)
        failed = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1", raw=SAILBOAT.read_bytes())["project"]
        client.wait({"id": client.state(failed)["job"]["id"]})
    shots = ROOT / "artifacts" / "issue_inspector_v2" / "screens"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_issues.mjs"), client.base + "/", str(browser), "-",
                          str(shots), reviewed, candidate, failed], capture_output=True, text=True, encoding="utf-8", timeout=900)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    result = json.loads(lines[-1])
    (shots.parent / "drive_result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    assert result["ok"], result.get("error", "") + "\nconsole: " + str(result.get("console_errors")) + "\n" + run.stderr[-3000:]
    assert result["console_errors"] == []
    drawn = lambda px: px is not None and px[3] > 0  # noqa: E731 - overlay pixel present
    start = result["start"]
    assert start["shown"].startswith("Layer 1") and start["rows"] == start["api_layer1"] >= 4  # layer 1 first, its issues only
    chips = {c["key"]: c for c in start["chips"]}
    assert chips["artwork"]["text"].startswith("Artwork") and not chips["artwork"]["ok"]  # intake findings counted there
    assert sum(1 for c in start["chips"] if c["ok"]) == 4  # the four unedited layers: no issues
    slot = result["slot"]
    assert slot["title"] == "Opening may close" and "0.59" in slot["measured"] and "0.8" in slot["threshold"]
    assert "unsupported below 0.4" in slot["threshold"] and "Layer 1 (from front)" in slot["where"]
    assert "primary" in slot["layer_button"] and slot["shown"].startswith("Layer 1")
    assert 0.4 < slot["fill"] < 0.5 and slot["centre_offset_px"] < 3  # zoomed onto it (45 % of the view), centred
    for align in slot["align"]:  # focused, wheel-zoomed out, Fit: drawn on the slot, not beside it
        assert drawn(align["inside"]) and not drawn(align["outside"]), align
    assert len({a["zoom"] for a in slot["align"]}) == 3
    neck = result["neck"]
    assert "widest passing connection test" in neck["measured"] and "required" in neck["threshold"]
    assert drawn(neck["bar"]) and not drawn(neck["disc_centre"])  # the neck is highlighted, the held disc is not filled
    assert result["click"]["title"] == "Floating material"  # exact hit after zoom and pan
    nav = result["nav"]
    assert nav["next"] != nav["start"] and nav["prev"] == nav["start"]
    layer2 = result["layer2"]
    assert layer2["shown"].startswith("Layer 2") and layer2["rows"] == layer2["api"] == 1
    assert "0.49" in layer2["measured"] and "Layer 2 (from front)" in layer2["where"] and drawn(layer2["inside"])
    clean = result["clean"]
    assert clean["rows"] == 0 and "No geometric issues found on this layer" in clean["text"] and clean["empty"]
    assert "1.20 mm" in clean["text"] and "0.80 mm" in clean["text"]  # the configured thresholds are named
    art = result["artwork"]
    assert art["rows"] == art["api"] >= art["intake"] >= 2
    assert "not a sheet defect" in art["where"] and art["layer_buttons"] == 0 and art["artwork_buttons"] == 1
    assert art["detail_image"] == 1
    edit = result["edit_layer"]
    assert edit["export_name"] in edit["image"] and edit["hash"].endswith("/review")
    assert 0.4 < edit["fill"] < 0.5 and edit["centre_offset_px"] < 3 and edit["selected_layer"]
    stale = result["stale"]
    assert stale["seen"] and stale["basis_changed"] and stale["spike_before"] and not stale["spike_after"]
    assert stale["rows_now"] == stale["api_now"]
    cand = result["candidate"]
    assert cand["sheet_issues"] == 0 and all(c["ok"] for c in cand["chips"] if c["key"] not in ("artwork", "front"))
    assert cand["shown"].startswith("Layer 1") and "No geometric issues found on this layer" in cand["text"]
    assert cand["rows"] == 0 and cand["empty"] and "sheets: no issues" in cand["summary"]
    assert cand["artwork_rows"] == cand["art_api"] >= cand["intake"] >= 2
    fail = result["failed"]
    assert "sheet-level locations are unavailable" in fail["notice"] and fail["rows"] == fail["api"] >= 2
    assert fail["chips"] == ["artwork"] and fail["layer_buttons"] == 0 and fail["shown"].startswith("Artwork")
