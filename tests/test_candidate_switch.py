"""Switching the reviewed candidate of one planning run (Plan -> Compare candidates -> Review this candidate).

beam_colors is reviewed (with cutout widening, an edit, a printability estimate and an issue decision), then
beam_area, then beam_colors again: each review comes back exactly as it was left, nothing is re-planned, and
the same holds after the server restarts.  Uses the sailboat fixture of ``test_l4_app``.
"""

from __future__ import annotations

import json

import pytest

from test_l4_app import _propose, _tree_hashes, app, start  # noqa: F401  (app: the fixture)


def _review_snapshot(workspace, name: str, client) -> dict:
    project = workspace / name
    state = client.state(name)
    return {"candidate": state["record"]["candidate"],
            "review": _tree_hashes(project / "review"),
            "draft_edits": state["draft"]["edits"],
            "sheets": [(s["id"], s["color"]) for s in state["draft"]["sheets"]],
            "printability": _tree_hashes(project / "printability") if (project / "printability").exists() else {},
            "issue_reviews": (project / "issue_reviews.json").read_text(encoding="utf-8")
            if (project / "issue_reviews.json").exists() else None}


def test_switch_candidates_keeps_every_review_and_never_replans(app):
    client, workspace = app
    name, state = _propose(client)
    names = {c["name"] for c in state["proposal"]["candidates"] if c["status"] == "VALID"}
    if not {"beam_area", "beam_colors"} <= names:
        pytest.skip(f"the fixture plans {sorted(names)}")
    project = workspace / name
    planning_run = _tree_hashes(project / "proposal")
    recommended = state["proposal"]["selected"]

    # 1. Review beam_colors: widened at review start, then a user edit, an estimate and an issue decision.
    client.run(name, "review", {"candidate": "beam_colors", "widen_openings": True})
    back = client.state(name)["draft"]["sheets"][0]["id"]
    client.run(name, "edit", {"op": {"op": "rim", "sheet": back, "enabled": True, "width_mm": 6.0, "total_height_mm": 2.4}})
    client.run(name, "printability", {})
    issues = client.ok("GET", f"/api/projects/{name}/issues")["issues"]
    if issues:
        client.ok("POST", f"/api/projects/{name}/issues/review", {"keys": [issues[0]["key"]], "state": "fix_later"})
    colors = _review_snapshot(workspace, name, client)
    assert colors["candidate"] == "beam_colors" and colors["draft_edits"]
    # The same candidate cannot be "switched" to; nothing changes.
    refused = client.run(name, "review", {"candidate": "beam_colors"}, "failed")
    assert "already the candidate in review" in refused["error"]["message"]

    # 2. Back to the comparison, review beam_area: beam_colors is saved, beam_area starts from its plan.
    client.run(name, "review", {"candidate": "beam_area"})
    state = client.state(name)
    assert state["record"]["candidate"] == "beam_area"
    assert set(state["proposal"]["saved_reviews"]) == {"beam_colors"}
    assert state["proposal"]["saved_reviews"]["beam_colors"]["edits"] == len(colors["draft_edits"])
    assert state["proposal"]["selected"] == recommended  # the recommendation is unchanged
    assert "printability" not in state and not (project / "issue_reviews.json").exists()
    planned_area = json.loads((project / "proposal" / "candidates" / "beam_area" / "stack" / "sheet_stack.json")
                              .read_text(encoding="utf-8"))
    assert [s["id"] for s in state["draft"]["sheets"]] == [s["id"] for s in planned_area["sheets"]]
    front = state["draft"]["sheets"][-1]["id"]
    client.run(name, "edit", {"op": {"op": "thickness", "sheet": front, "mm": 2.0}})
    area = _review_snapshot(workspace, name, client)

    # 3. Back again to beam_colors: restored exactly (sheets, edits, widening, estimate, decisions).
    client.run(name, "review", {"candidate": "beam_colors"})
    assert _review_snapshot(workspace, name, client) == colors
    state = client.state(name)
    assert set(state["proposal"]["saved_reviews"]) == {"beam_area"}
    assert state["draft"]["widening"]["runs"] == 1  # the widening step of this review is kept, not redone

    # 4. Save and reopen: a new server on the same folder shows the same, and switching still works.
    server2, client2 = start(workspace)
    try:
        assert _review_snapshot(workspace, name, client2) == colors
        assert set(client2.state(name)["proposal"]["saved_reviews"]) == {"beam_area"}
        client2.run(name, "review", {"candidate": "beam_area"})
        assert _review_snapshot(workspace, name, client2) == area
        client2.run(name, "review", {"candidate": "beam_colors"})
        assert _review_snapshot(workspace, name, client2) == colors
    finally:
        server2.shutdown()
        server2.server_close()

    # No re-planning at any point: the planning run is byte-identical and still has both candidates.
    assert _tree_hashes(project / "proposal") == planning_run
    attempts = json.loads((project / "project.json").read_text(encoding="utf-8")).get("attempts", [])
    assert attempts == []  # switching candidates is not a historical attempt


def test_switch_is_refused_with_unsaved_artwork_changes(app, monkeypatch):
    client, workspace = app
    name, state = _propose(client)
    candidates = [c["name"] for c in state["proposal"]["candidates"] if c["status"] == "VALID"]
    if len(candidates) < 2:
        pytest.skip("the fixture plans one valid candidate")
    client.run(name, "review", {"candidate": candidates[0]})
    before = _tree_hashes(workspace / name / "review")
    from layerforge_l1.app.workspace import Workspace

    monkeypatch.setattr(Workspace, "unsaved_artwork", lambda self, n: 2)
    refused = client.run(name, "review", {"candidate": candidates[1]}, "failed")
    assert "unsaved artwork change" in refused["error"]["message"]
    assert client.state(name)["record"]["candidate"] == candidates[0]
    assert _tree_hashes(workspace / name / "review") == before and not (workspace / name / "reviews").exists()


def test_browser_compare_and_switch_candidates(app, tmp_path):
    """In a real browser (``tests/e2e/drive_candidates.mjs``): Review this candidate (beam_colors), back to the
    existing Compare candidates table from Review layers, Review this candidate (beam_area), back, and Resume
    saved review (beam_colors).  One table, the recommendation unchanged, no re-planning, no data loss."""

    import subprocess

    from test_l4_app import BROWSERS, ROOT, _node_ok

    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    client, workspace = app
    name, state = _propose(client)
    if not {"beam_area", "beam_colors"} <= {c["name"] for c in state["proposal"]["candidates"] if c["status"] == "VALID"}:
        pytest.skip("the fixture does not plan both beam_area and beam_colors")
    planning_run = _tree_hashes(workspace / name / "proposal")
    shots = tmp_path / "shots"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_candidates.mjs"), client.base + "/", str(browser), "-",
                          str(shots), name], capture_output=True, text=True, encoding="utf-8", timeout=900)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    r = json.loads(lines[-1])
    (shots / "drive_result.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    assert r["ok"], r.get("error", "") + "\n" + run.stderr[-3000:]
    assert r["console_errors"] == [] and r["dialogs"] == []
    s1, s2, s3, s4, s5 = r["steps"]
    assert s1["clicked"]["button"] == "Review this candidate" and s1["state"]["candidate"] == "beam_colors"
    for back, reviewing, other in ((s2, "beam_colors", "beam_area"), (s4, "beam_area", "beam_colors")):
        assert back["comparison_visible"] and back["tables"] == 1  # the existing table, not a second one
        assert "in review" in back["rows"][reviewing]["text"] and back["rows"][reviewing]["button"] is None
        assert back["rows"][other]["button"] in ("Review this candidate", "Resume saved review")
    assert s3["state"]["candidate"] == "beam_area" and s3["state"]["saved"] == ["beam_colors"]
    assert s4["rows"]["beam_colors"]["button"] == "Resume saved review" and "saved review" in s4["rows"]["beam_colors"]["text"]
    assert s5["state"]["candidate"] == "beam_colors" and s5["state"]["saved"] == ["beam_area"]
    assert s5["state"]["layers"] == s1["state"]["layers"] and s5["state"]["edits"] == s1["state"]["edits"]
    assert all(s["state"]["selected"] == state["proposal"]["selected"] and s["state"]["jobs"] == 0
               for s in (s1, s3, s5))
    assert _tree_hashes(workspace / name / "proposal") == planning_run


def test_a_failed_switch_leaves_both_reviews_as_they_were(app, monkeypatch):
    client, workspace = app
    name, state = _propose(client)
    candidates = [c["name"] for c in state["proposal"]["candidates"] if c["status"] == "VALID"]
    if len(candidates) < 2:
        pytest.skip("the fixture plans one valid candidate")
    first, second = candidates[:2]
    project = workspace / name
    client.run(name, "review", {"candidate": first})
    client.run(name, "review", {"candidate": second})  # first is now a saved review
    active, saved = _tree_hashes(project / "review"), _tree_hashes(project / "reviews" / first)
    from layerforge_l1.app.workspace import Workspace

    real = Workspace._save_record

    def fail_when_switching(self, n, record):
        if record.get("candidate") == first:
            raise OSError("disk full (simulated)")
        return real(self, n, record)

    monkeypatch.setattr(Workspace, "_save_record", fail_when_switching)
    failed = client.run(name, "review", {"candidate": first}, "failed")
    assert "disk full" in failed["error"]["message"]
    monkeypatch.setattr(Workspace, "_save_record", real)
    assert client.state(name)["record"]["candidate"] == second
    assert _tree_hashes(project / "review") == active
    assert _tree_hashes(project / "reviews" / first) == saved
    assert not (project / "reviews" / second).exists()
    client.run(name, "review", {"candidate": first})  # and the switch works once the fault is gone
    assert _tree_hashes(project / "reviews" / second / "review") == active
