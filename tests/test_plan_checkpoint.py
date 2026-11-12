"""The accepted plan: one rollback checkpoint (``attempts/accepted``) beside the working plan.

Accept copies the working plan (artwork, candidates, current and saved reviews with their edits and widening,
issue decisions, size and scale settings); experiments never write it; Restore brings it back exactly without
planning; a re-plan discards the outgoing working plan instead of adding an attempt.  Real server on the
sailboat fixture of ``test_l4_app`` (scale 1); failures are injected where the files are moved.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from resource_guard import PLENTY, fixed_memory, require_memory_for_planning
from test_artwork_recolor import PORTHOLES, SUN, _recolor
from test_l4_app import SAILBOAT, _propose, _tree_hashes, app, start  # noqa: F401  (app: the fixture)

PARTS = ("proposal", "review", "draft", "printability", "reviews", "artwork", "issue_reviews.json")
SETTINGS = ("proposal_settings", "candidate", "processing_scale", "sheet_outer_mm", "sheet_outer_height_mm",
            "detail_decisions")
LEFTOVERS = ("restore.json", "restore.next", "restore.old", "replaced.tmp", "attempts/accepted.next",
             "attempts/accepted.old")


def _plan(project: Path) -> dict:
    """The working plan exactly: every file of its parts and the plan settings of project.json."""

    out: dict = {}
    for part in PARTS:
        path = project / part
        if path.is_file():
            out[part] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            out[part] = _tree_hashes(path)
    record = json.loads((project / "project.json").read_text(encoding="utf-8"))
    out["settings"] = {k: record.get(k) for k in SETTINGS}
    return out


def _clean(project: Path) -> bool:
    return not any((project / leftover).exists() for leftover in LEFTOVERS)


def _refused(client, name: str, action: str, words: str) -> None:
    status, error = client.call("POST", f"/api/projects/{name}/{action}", {})
    assert status == 400 and words in error["error"]["message"], (status, error)


def test_accept_experiments_restore_reaccept_and_restart(app):
    client, workspace = app
    name, state = _propose(client)
    project = workspace / name
    assert state["checkpoint"] == {"exists": False, "working_plan": True}
    _refused(client, name, "plan/restore", "no accepted plan")
    proposal = state["proposal"]
    first = proposal["selected"]
    valid = [c["name"] for c in proposal["candidates"] if c["status"] == "VALID"]
    other = next((c for c in valid if c != first), None)

    # Plan A: candidate switching and review edits (widening, a rim on the other candidate, a thickness),
    # an issue decision and a print kit, all before accepting.
    client.run(name, "review", {"candidate": first, "widen_openings": True})
    if other:
        client.run(name, "review", {"candidate": other})
        back = client.state(name)["draft"]["sheets"][0]["id"]
        client.run(name, "edit", {"op": {"op": "rim", "sheet": back, "enabled": True, "width_mm": 6.0, "total_height_mm": 2.4}})
        client.run(name, "review", {"candidate": first})
    front = client.state(name)["draft"]["sheets"][-1]["id"]
    client.run(name, "edit", {"op": {"op": "thickness", "sheet": front, "mm": 2.0}})
    issues = client.ok("GET", f"/api/projects/{name}/issues")["issues"]
    if issues:
        client.ok("POST", f"/api/projects/{name}/issues/review", {"keys": [issues[0]["key"]], "state": "fix_later"})
    kit = client.run(name, "kits", {})["result"]["kit"]
    edits_a = client.state(name)["draft"]["edits"]
    plan_a = _plan(project)

    client.run(name, "plan/accept")
    checkpoint = project / "attempts" / "accepted"
    saved = _tree_hashes(checkpoint)
    view = client.state(name)["checkpoint"]
    assert view["exists"] and view["working_is_accepted"] and view["bytes"] > 0
    summary = view["summary"]
    assert summary["candidate"] == first and summary["stage"] == "review" and summary["review_edits"] == len(edits_a)
    assert summary["saved_reviews"] == ([other] if other else []) and summary["sheet_size_mm"] == [225.0, 225.0]
    assert _plan(project) == plan_a and _clean(project)  # accepting changes nothing in the working plan
    for part in ("proposal", "review", "reviews", "draft"):  # an exact copy
        if part in plan_a:
            assert _tree_hashes(checkpoint / part) == plan_a[part]
    _refused(client, name, "plan/accept", "already the accepted plan")
    _refused(client, name, "plan/restore", "already the accepted plan")

    # Experiment B: an artwork edit planned (re-plan).  Experiment C: review B, edit it, another planned edit.
    _recolor(client, name, PORTHOLES[0], SUN, "palette")
    state = client.state(name)
    assert not state["checkpoint"]["working_is_accepted"]
    assert "after these sheets were planned" in state["checkpoint"]["accept_blocked"]  # artwork and sheets differ
    client.run(name, "artwork/replan")
    client.run(name, "review", {"candidate": client.state(name)["proposal"]["selected"]})
    client.run(name, "edit", {"op": {"op": "thickness", "sheet": client.state(name)["draft"]["sheets"][-1]["id"], "mm": 2.4}})
    _recolor(client, name, PORTHOLES[1], SUN, "palette")
    client.run(name, "artwork/replan")
    state = client.state(name)
    assert [p.name for p in (project / "attempts").iterdir()] == ["accepted"] and state["upscale"]["attempts"] == []
    assert _tree_hashes(checkpoint) == saved and _clean(project)  # experiments never write the checkpoint
    assert not state["checkpoint"]["working_is_accepted"] and "draft" not in state

    # Restore A: exactly the accepted files and settings, no planning; the checkpoint and the kit stay.
    restored = client.run(name, "plan/restore")
    assert not any(e["stage"].startswith("L1_") for e in restored["events"])  # nothing was planned
    assert _plan(project) == plan_a
    state = client.state(name)
    assert state["checkpoint"]["working_is_accepted"] and _tree_hashes(checkpoint) == saved and _clean(project)
    assert state["record"]["candidate"] == first and state["draft"]["edits"] == edits_a
    assert [k["id"] for k in state["kits"]] == [kit] and (project / "kits" / kit / "print_kit.json").is_file()
    if other:
        assert set(state["proposal"]["saved_reviews"]) == {other}

    # After a restart: the same plan and checkpoint.  Then modify the restored plan and accept again.
    server2, client2 = start(workspace)
    try:
        state = client2.state(name)
        assert state["checkpoint"]["working_is_accepted"] and state["draft"]["edits"] == edits_a
        assert _plan(project) == plan_a
        client2.run(name, "edit", {"op": {"op": "thickness", "sheet": front, "mm": 2.4}})
        assert not client2.state(name)["checkpoint"]["working_is_accepted"]
        plan_a2 = _plan(project)
        client2.run(name, "plan/accept")
        state = client2.state(name)
        assert state["checkpoint"]["working_is_accepted"] and state["checkpoint"]["accepted_utc"] >= view["accepted_utc"]
        assert _tree_hashes(checkpoint / "review") == plan_a2["review"] != plan_a["review"]
        assert [p.name for p in (project / "attempts").iterdir()] == ["accepted"] and _clean(project)
        client2.run(name, "undo", {})  # an unaccepted change, then back to the new accepted plan
        assert not client2.state(name)["checkpoint"]["working_is_accepted"]
        client2.run(name, "plan/restore")
        assert _plan(project) == plan_a2
    finally:
        server2.shutdown()
        server2.server_close()


def test_accept_with_failing_validation_shows_the_status_and_is_refused_when_unsaved(app, monkeypatch):
    client, workspace = app
    name, state = _propose(client)
    client.run(name, "review", {"candidate": state["proposal"]["selected"], "widen_openings": False})
    sheets = client.state(name)["draft"]["sheets"]
    client.run(name, "edit", {"op": {"op": "move", "sheet": sheets[-1]["id"], "to": 0}})  # front sheet to the back
    state = client.state(name)
    status = state["draft"]["validation"]["status"]
    assert status != "PASS" and "accept_blocked" not in state["checkpoint"]  # allowed, the status is shown

    from layerforge_l1.app.workspace import Workspace

    real = Workspace.unsaved_artwork
    monkeypatch.setattr(Workspace, "unsaved_artwork", lambda self, n: 2)
    _refused(client, name, "plan/accept", "unsaved artwork change")
    assert "unsaved artwork change" in client.state(name)["checkpoint"]["accept_blocked"]
    monkeypatch.setattr(Workspace, "unsaved_artwork", real)

    client.run(name, "plan/accept")
    view = client.state(name)["checkpoint"]
    assert view["working_is_accepted"] and view["summary"]["validation"] == status
    client.run(name, "undo", {})  # back to the planned order: now there is something to restore
    monkeypatch.setattr(Workspace, "unsaved_artwork", lambda self, n: 2)
    _refused(client, name, "plan/restore", "unsaved artwork change")


def test_upscale_after_accepting_a_failed_proposal_then_restore(app, monkeypatch):
    import layerforge_l1.core.propose as core_propose
    from layerforge_l1.core.planning import PlanningError

    real = core_propose.plan_stack

    def fails_at_scale_1(target, config, name="stack"):
        if config.scale == 1:
            raise PlanningError("reveal closure is infeasible (simulated)", {"closure_feasible": False})
        return real(target, config, name)

    monkeypatch.setattr(core_propose, "plan_stack", fails_at_scale_1)
    fixed_memory(monkeypatch, PLENTY)
    require_memory_for_planning(150.0, 2, "the real scale-2 retry")
    client, workspace = app
    name = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&outer_mm=150", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    project = workspace / name
    state = client.state(name)
    assert state["proposal"]["status"] == "NEEDS_USER_DECISION" and "accept_blocked" not in state["checkpoint"]
    client.run(name, "plan/accept")  # a failed proposal can be kept too (its diagnostics)
    plan_a = _plan(project)
    saved = _tree_hashes(project / "attempts" / "accepted")
    assert client.state(name)["checkpoint"]["summary"]["proposal_status"] == "NEEDS_USER_DECISION"

    client.run(name, "upscale/preview", {"factor": 2})
    preview = client.state(name)["upscale"]["previews"]["2"]
    client.run(name, "upscale/retry", {"factor": 2, "preview_id": preview["preview_id"], "approve_changes": True})
    state = client.state(name)
    assert state["proposal"]["status"] == "PROPOSAL_READY_FOR_REVIEW" and state["record"]["processing_scale"] == 2
    assert state["upscale"]["attempts"] == [] and _tree_hashes(project / "attempts" / "accepted") == saved

    client.run(name, "plan/restore")
    state = client.state(name)
    assert _plan(project) == plan_a and "processing_scale" not in state["record"]
    assert state["proposal"]["status"] == "NEEDS_USER_DECISION" and state["upscale"]["available"]
    assert not (project / "upscale").exists() and _clean(project)


def test_failures_while_accepting_or_restoring_change_nothing(app, monkeypatch):
    import layerforge_l1.app.workspace as wsmod

    client, workspace = app
    name, state = _propose(client)
    project = workspace / name
    client.run(name, "review", {"candidate": state["proposal"]["selected"], "widen_openings": False})
    client.run(name, "plan/accept")
    checkpoint = project / "attempts" / "accepted"
    saved = _tree_hashes(checkpoint)
    front = client.state(name)["draft"]["sheets"][-1]["id"]
    client.run(name, "edit", {"op": {"op": "thickness", "sheet": front, "mm": 2.0}})
    working = _plan(project)
    real_replace, real_copytree = wsmod.replace_path, shutil.copytree

    def check_unchanged():
        assert _tree_hashes(checkpoint) == saved and _plan(project) == working and _clean(project)

    # 1. Accept fails while copying, or while swapping the new copy in: the previous accepted plan stays.
    def copy_fails(src, dst, *args, **kwargs):
        if "accepted.next" in str(dst) and Path(src).name == "review":
            raise OSError("disk full (simulated)")
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(wsmod.shutil, "copytree", copy_fails)
    assert "disk full" in client.run(name, "plan/accept", None, "failed")["error"]["message"]
    monkeypatch.setattr(wsmod.shutil, "copytree", real_copytree)
    check_unchanged()

    def swap_fails(src, dst):
        if Path(src).name == "accepted.next":
            raise OSError("access denied (simulated)")
        return real_replace(src, dst)

    monkeypatch.setattr(wsmod, "replace_path", swap_fails)
    assert "access denied" in client.run(name, "plan/accept", None, "failed")["error"]["message"]
    monkeypatch.setattr(wsmod, "replace_path", real_replace)
    check_unchanged()

    # 2. Restore fails after part of the working plan was swapped: it is put back exactly.
    def review_fails(src, dst):
        if Path(src).parent.name == "restore.next" and Path(src).name == "review":
            raise OSError("file in use (simulated)")
        return real_replace(src, dst)

    monkeypatch.setattr(wsmod, "replace_path", review_fails)
    assert "file in use" in client.run(name, "plan/restore", None, "failed")["error"]["message"]
    check_unchanged()

    # 3. A crash in the middle of a restore (nothing put back) is undone when the app starts again.
    monkeypatch.setattr(wsmod.Workspace, "_undo_restore", lambda self, path: None)
    client.run(name, "plan/restore", None, "failed")
    assert (project / "restore.json").is_file() and not (project / "review").exists()  # as a crash leaves it
    monkeypatch.undo()
    server2, client2 = start(workspace)
    try:
        check_unchanged()
        assert "undone" in client2.state(name)["checkpoint"]["recovered"][0]

        # 4. A damaged accepted plan is never restored.
        manifest = checkpoint / "review" / "sheet_stack.json"
        original = manifest.read_bytes()
        manifest.write_bytes(original.replace(b'"', b"'", 1))
        assert "incomplete or changed" in client2.run(name, "plan/restore", None, "failed")["error"]["message"]
        assert _plan(project) == working and _clean(project)
        manifest.write_bytes(original)
        client2.run(name, "plan/restore")  # and it works once the copy is intact again
        assert _plan(project)["review"] == _tree_hashes(checkpoint / "review")
    finally:
        server2.shutdown()
        server2.server_close()


def test_projects_with_earlier_attempts_keep_them_untouched(app):
    client, workspace = app
    name, state = _propose(client)
    project = workspace / name
    # An attempt archived by an earlier version (folder + record entry), no accepted plan.
    legacy = project / "attempts" / "attempt_01_scale1"
    shutil.copytree(project / "proposal", legacy / "proposal")
    record = json.loads((project / "project.json").read_text(encoding="utf-8"))
    record["attempts"] = [{"id": legacy.name, "archived_utc": "2026-11-01T10:00:00+00:00", "scale": 1,
                           "status": "NEEDS_USER_DECISION", "blockers": [], "detail_decisions": {},
                           "replaced_by": {"upscale_factor": 2, "scale": 2, "status": "PROPOSAL_READY_FOR_REVIEW",
                                           "approved_visible_changes": 0, "preview_id": "x"}}]
    (project / "project.json").write_text(json.dumps(record), encoding="utf-8")
    kept = _tree_hashes(legacy)
    state = client.state(name)
    assert [a["id"] for a in state["upscale"]["attempts"]] == [legacy.name] and not state["checkpoint"]["exists"]

    client.run(name, "review", {"candidate": state["proposal"]["selected"], "widen_openings": False})
    client.run(name, "plan/accept")
    plan_a = _plan(project)
    _recolor(client, name, PORTHOLES[0], SUN, "palette")
    client.run(name, "artwork/replan")
    client.run(name, "plan/restore")
    state = client.state(name)
    assert _plan(project) == plan_a and [a["id"] for a in state["upscale"]["attempts"]] == [legacy.name]
    assert sorted(p.name for p in (project / "attempts").iterdir()) == ["accepted", legacy.name]
    assert _tree_hashes(legacy) == kept and _clean(project)
