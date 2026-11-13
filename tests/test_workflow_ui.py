"""Main workflow and the Artwork editor's one consistent place (UX Phase 2, D-V3-052), in a real browser.

``tests/e2e/drive_workflow_flow.mjs`` (Chrome preferred, else Edge): import -> edit artwork -> plan -> review ->
back to editing from Review and from Plan -> reload -> save -> reload.  The editor always sits directly below the
Issues of the current stage (Plan before a review, Review after); every "Edit artwork" entry opens that one place.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from test_l4_app import BROWSERS, ROOT, SAILBOAT, _node_ok, start

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def test_workflow_with_one_artwork_editor_place(tmp_path):
    browser = CHROME if CHROME.is_file() else next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Chrome/Edge to drive the UI in a real browser")
    server, client = start(tmp_path / "workspace")
    shots = ROOT / "artifacts" / "ux_phase2" / "flow"
    try:
        run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_workflow_flow.mjs"), client.base + "/", str(browser),
                              str(SAILBOAT), str(shots)], capture_output=True, text=True, encoding="utf-8", timeout=1200)
    finally:
        server.shutdown()
        server.server_close()
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    r = json.loads(lines[-1])
    (shots / "drive_result.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    assert r["ok"], r.get("error", "") + "\n" + run.stderr[-3000:]
    assert r["console_errors"] == []
    s = r["steps"]

    # Before planning: the editing step is current; one next action at the top; the editor is open below the
    # folded Issues (the artwork is shown once).
    imp = s["imported"]
    assert imp["step"] == "artwork" and imp["next"] == "Plan sheets from this artwork" and imp["next_on_first_screen"]
    assert imp["artwork_panel"] == {"open": True, "after_issues": True} and imp["issues_open"] is False
    assert imp["art_images"] == 1 and imp["edit_artwork"] == ["Edit artwork"]
    # An unsaved change holds the next action until Save; the entry says so.
    assert s["unsaved"]["next_disabled"] is True and "1 unsaved" in s["unsaved"]["edit_artwork"][0]
    assert s["saved"]["next_disabled"] is False and s["saved"]["api"]["edits"] == 1

    # Planned: the next action is the review, the editor folds (still directly below Issues), one primary button.
    pl = s["planned"]
    assert pl["step"] == "plan" and pl["next"] == "Review this candidate" and pl["next_on_first_screen"]
    assert pl["primaries"] == ["Review this candidate"] and pl["artwork_panel"] == {"open": False, "after_issues": True}

    # Review: the editor lives below Issues (folded); "Edit artwork" in the header opens it there.
    rv = s["review"]
    assert rv["step"] == "review" and rv["primaries"] == ["Build print kit →"]
    assert rv["artwork_panel"] == {"open": False, "after_issues": True} and rv["edit_artwork"] == ["Edit artwork"]
    er = s["edit_from_review"]
    assert er["hash"].endswith("/review") and er["artwork_panel"]["open"] and er["in_view"]

    # From Plan, "Edit artwork" reaches the same editor (Review) with the unsaved change; no editor on Plan.
    pu = s["plan_with_unsaved"]
    assert pu["artwork_panel"] is None and pu["next"] == "Continue to Review layers →" and "1 unsaved" in pu["edit_artwork"][0]
    assert s["edit_from_plan"]["hash"].endswith("/review") and s["edit_from_plan"]["artwork_panel"]["open"]

    # Reload recovers the unsaved change; Save never re-plans; both headers then offer planning from it.
    assert s["after_reload"]["unsaved_text"].startswith("1 unsaved change") and s["after_reload"]["api"]["unsaved"] == 1
    sr = s["saved_in_review"]
    assert sr["api"]["differs"] and sr["api"]["proposal_seconds"] == pl["api"]["proposal_seconds"] and sr["api"]["draft"]
    assert sr["primaries"][0] == "Plan sheets from the edited artwork" and "Build print kit →" not in sr["primaries"]
    assert s["plan_after_save"]["next"] == "Plan sheets from the edited artwork" and s["plan_after_save"]["primaries"] == [
        "Plan sheets from the edited artwork"]
    assert s["reloaded"]["api"]["edits"] == 1 and s["reloaded"]["api"]["differs"]

    # History survives the reload; undo restores the planned artwork (nothing re-planned at any point).
    assert s["history_after_reload"] == 1
    au = s["after_undo"]
    assert au["api"]["edits"] == 0 and not au["api"]["differs"] and au["api"]["proposal_seconds"] == pl["api"]["proposal_seconds"]
    assert r["dialogs"] == [{"type": "beforeunload", "message": ""}]  # the reload with an unsaved change asked first
