"""Review Layers: a read-only inspection workspace (``app/static/app.js`` ``reviewView``).

The screen shows the assembled stack and each layer (zoom, pan), the layer list, validation and the
issue overlays.  It has no sheet-geometry editing tools any more (Add / Cut / Line / Curve / Fill cutout /
Remove island / Split / Merge / raw edit requests); the Artwork editor stays on it, below the issues.  The
physical parameters (color, thickness, order, rim) stay in a collapsed Layer settings section, and earlier
edits stay undoable.  The sheet-editing backend is unchanged: a project whose review already holds
geometry edits (made through the API, as the earlier UI made them) loads, validates and replays exactly.
"""

from __future__ import annotations

import json

import pytest

from test_issue_inspector import _prepare_sheets, _sailboat_with_red_spot
from test_l4_app import app  # noqa: F401 - shared fixture

EDIT_OPS = {"add", "subtract", "fill_hole", "remove_island", "split", "merge", "bridge"}


def test_review_with_earlier_geometry_edits_loads_validates_and_replays(app, tmp_path):
    """The backend keeps every sheet edit: earlier geometry edits load, validate, replay and undo."""

    client, workspace = app
    name, sheets = _prepare_sheets(client, _sailboat_with_red_spot(tmp_path))
    state = client.state(name)
    draft = state["draft"]
    assert [e["op"]["op"] for e in draft["edits"]] == ["subtract", "add", "add", "add", "subtract", "add"]
    assert draft["validation"]["status"] in ("PASS", "FAIL") and len(draft["sheets"]) == len(sheets)
    client.run(name, "verify", {})
    assert client.state(name)["verify"]["replay_identical"]
    # The edit API still accepts geometry edits (not offered in the UI, kept for saved projects and scripts).
    assert EDIT_OPS <= set(client.ok("GET", "/api/info")["edit_ops"])
    client.run(name, "undo", {})
    assert len(client.state(name)["draft"]["edits"]) == 5


def test_browser_review_layers_is_read_only_and_keeps_inspection(app, tmp_path):
    """In a real browser (``tests/e2e/drive_review.mjs``): no sheet-editing control on the screen, the
    preview is the largest element, layer selection (click and arrow keys), assembled / layer views, wheel
    zoom, drag pan, issue navigation onto the layer, the Artwork editor below the issues, collapsed Layer
    settings, and the earlier edits listed with Undo."""

    import subprocess

    from test_l4_app import BROWSERS, ROOT, _node_ok

    client, workspace = app
    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    name, _ = _prepare_sheets(client, _sailboat_with_red_spot(tmp_path))
    shots = ROOT / "artifacts" / "review_layers_v1" / "after"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_review.mjs"), client.base + "/", str(browser), "-",
                          str(shots), name], capture_output=True, text=True, encoding="utf-8", timeout=600)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    r = json.loads(lines[-1])
    (shots.parent / "drive_result.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    assert r["ok"], r.get("error", "") + "\nconsole: " + str(r.get("console_errors")) + "\n" + run.stderr[-3000:]
    assert r["console_errors"] == []

    layout = r["layout"]
    assert layout["edit_controls"] == {} and layout["artwork_panel"] == 1  # no sheet editing; the artwork editor stays
    vp = layout["viewport"]
    assert vp["w"] >= 640 and vp["h"] >= 560  # the preview is the visual focus

    sel = r["select"]
    second = sel["rows"][1]
    assert sel["selected"] == second["id"] and sel["panel"] == second["name"] and sel["view"] == "Layer"
    assert sel["src"].split("?")[0] == sel["sheet_image"].split("?")[0]  # the layer alone is shown
    assert sel["after_arrow"] == sel["rows"][2]["id"]  # arrow down: the next layer toward the back
    assert r["views"]["labels"][:2] == ["Assembled", "Layer"] and "assembled" in r["views"]["front_src"]

    zoom = r["zoom"]
    assert zoom["before"] != zoom["after"] and int(zoom["after"].rstrip("%")) > int(zoom["before"].rstrip("%"))
    assert zoom["transform_panned"] != zoom["transform_zoomed"]  # a plain left drag pans the zoomed view
    assert zoom["fit"] == zoom["before"]

    issues = r["issues"]
    assert issues["first"] and issues["second"] and issues["first"] != issues["second"] or issues["second"]
    assert "Show on layer" in issues["card_buttons"] and "Edit layer" not in issues["card_buttons"]
    shown = issues["after_show"]
    assert shown["view"] == "Layer" and shown["zoom"] != zoom["before"] and shown["edit_controls"] == {}

    artwork = r["artwork"]  # directly below the issues; the "Edit artwork" step opens Review and scrolls to it
    assert artwork["hash"].endswith("/review") and artwork["panel"] == 1 and artwork["after_issues"] and artwork["in_view"]

    settings = r["settings"]
    assert settings["open"] is False  # collapsed by default
    assert set(settings["fields"]) == {"recolor", "thickness", "move", "rim"} and settings["history"] == 6 and settings["undo"]
