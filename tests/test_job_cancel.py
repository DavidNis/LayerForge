"""Stopping a planning job (D-V3-055): cooperative cancellation through the existing job runner.

Unit level: the runner, the "interruptible" rule and non-cancellable jobs.  App level, with the real planner on
the sailboat fixture: stop right after starting, stop during candidate planning (a long stage), stop a re-plan
from edited artwork (with an accepted plan and a review on disk), stop a physical-resize re-plan.  Each time the
whole project folder must be byte-identical afterwards (previous plan, artwork, reviews, accepted checkpoint),
no staging folder may remain, nothing is locked (the project can be deleted), and planning again works and
gives the same result as an uninterrupted run.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import pytest

from layerforge_l1.app.jobs import CANCELLED, DONE, JobConflict, JobRunner
from layerforge_l1.core import cancel
from test_l4_app import SAILBOAT, Client, start

PORTHOLE = [352, 210]  # sailboat at scale 1 (as the artwork tests)


# -- runner ------------------------------------------------------------------------------------------------
def _wait(job, timeout=20.0):
    end = time.time() + timeout
    while job.status not in ("done", "failed", "cancelled") and time.time() < end:
        time.sleep(0.02)
    return job.status


def test_a_running_cancellable_job_stops_at_its_next_check_and_the_runner_goes_on():
    runner = JobRunner()
    steps = []

    def work(progress):
        with cancel.interruptible():
            for i in range(2000):
                steps.append(i)
                progress("STEP", i=i)
                time.sleep(0.005)
        return "finished"

    job = runner.submit("propose", "p", work, cancellable=True)
    while len(steps) < 5:
        time.sleep(0.01)
    runner.cancel(job.id)
    assert _wait(job) == CANCELLED and job.result is None and job.error is None
    assert job.events[-1]["stage"] == "CANCELLED" and len(steps) < 2000
    after = runner.submit("propose", "p", lambda progress: "again", cancellable=True)  # usable at once
    assert _wait(after) == DONE and after.result == "again"
    with pytest.raises(JobConflict):
        runner.cancel(after.id)  # already finished


def test_a_queued_job_never_starts_and_other_jobs_are_not_cancellable():
    runner = JobRunner()
    release = threading.Event()
    blocker = runner.submit("kit", "a", lambda progress: release.wait(10))  # not cancellable
    with pytest.raises(JobConflict):
        runner.cancel(blocker.id)
    ran = []
    queued = runner.submit("propose", "b", lambda progress: ran.append(1), cancellable=True)
    runner.cancel(queued.id)
    release.set()
    assert _wait(blocker) == DONE and _wait(queued) == CANCELLED and not ran
    assert queued.events == [{"stage": "CANCELLED", "elapsed_s": 0.0, "while": "queued"}]


def test_checks_only_stop_interruptible_work():
    event = threading.Event()
    event.set()
    with cancel.bound(event):
        cancel.check()  # publishing a finished plan: never interrupted
        with pytest.raises(cancel.JobCancelled):
            with cancel.interruptible():
                pass
    with cancel.interruptible():
        cancel.check()  # no job bound (tests, CLI): nothing to stop


# -- the app, real planning -------------------------------------------------------------------------------
def _snapshot(folder: Path) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _wait_job(client: Client, job_id: str, timeout: float = 600.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        job = client.ok("GET", f"/api/jobs/{job_id}")
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish")


def _cancel_when(client: Client, job_id: str, stage: str | None) -> dict:
    """Stop the job as soon as ``stage`` was reported (None: at once); returns the finished job."""

    if stage is not None:
        end = time.time() + 300
        while time.time() < end:
            job = client.ok("GET", f"/api/jobs/{job_id}")
            if any(e["stage"] == stage for e in job["events"]) or job["status"] not in ("queued", "running"):
                break
            time.sleep(0.03)
    reply = client.ok("POST", f"/api/jobs/{job_id}/cancel", {})
    assert reply["cancel_requested"] is True
    return _wait_job(client, job_id)


def _stages(job: dict) -> list[str]:
    return [e["stage"] for e in job["events"]]


def _candidates(path: Path) -> dict[str, str]:
    """Digest of every candidate's planned sheets (what planning produced)."""

    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((path / "proposal" / "candidates").rglob("retained_material_mask.png"))}


@pytest.fixture
def app(tmp_path):
    server, client = start(tmp_path / "workspace")
    yield client, tmp_path / "workspace"
    server.shutdown()
    server.server_close()


def _import(client: Client, query: str) -> str:
    return client.ok("POST", f"/api/projects?filename=sailboat_512.png{query}", raw=SAILBOAT.read_bytes())["project"]


def test_stop_the_first_plan_shortly_after_it_started_then_plan_again(app):
    client, workspace = app
    name = _import(client, "&scale=1&name=first")
    job = _cancel_when(client, client.state(name)["job"]["id"], None)
    assert job["status"] == "cancelled" and "L1_PROPOSAL_DONE" not in _stages(job)
    path = workspace / name
    assert not (path / "proposal").exists() and not (path / "proposal.next").exists()  # nothing published
    state = client.state(name)
    assert state.get("proposal") is None and state["stage"] == "new"
    again = _wait_job(client, client.ok("POST", f"/api/projects/{name}/propose", {})["job"]["id"])
    assert again["status"] == "done" and client.state(name)["proposal"]["status"] == "PROPOSAL_READY_FOR_REVIEW"
    # Same planning output as a project that was never stopped.
    other = _import(client, "&scale=1&name=reference")
    _wait_job(client, client.state(other)["job"]["id"])
    assert _candidates(path) == _candidates(workspace / other) and _candidates(path)


def test_stop_during_candidate_planning_keeps_the_previous_proposal(app):
    client, workspace = app
    name = _import(client, "&scale=2&name=long")  # scale 2: each candidate plans for several seconds
    _wait_job(client, client.state(name)["job"]["id"])
    path = workspace / name
    before = _snapshot(path)
    rerun = client.ok("POST", f"/api/projects/{name}/propose", {})["job"]["id"]
    t0 = time.time()
    job = _cancel_when(client, rerun, "L1_INTAKE_DONE")  # inside the planner's search for the first candidate
    stopped_in = time.time() - t0
    stages = _stages(job)
    assert job["status"] == "cancelled" and "L1_INTAKE_DONE" in stages and "L1_CANDIDATE_DONE" not in stages, stages
    assert _snapshot(path) == before and not (path / "proposal.next").exists()
    assert stopped_in < 30
    again = _wait_job(client, client.ok("POST", f"/api/projects/{name}/propose", {})["job"]["id"])
    assert again["status"] == "done" and _candidates(path) and set(_candidates(path)) == {
        k for k in before if k.startswith("proposal/candidates/") and k.endswith("retained_material_mask.png")}


def test_stop_a_replan_from_edited_artwork_keeps_review_artwork_and_accepted_plan(app):
    from test_artwork_recolor import _recolor

    client, workspace = app
    name = _import(client, "&scale=1&name=edited")
    _wait_job(client, client.state(name)["job"]["id"])
    state = client.state(name)
    _wait_job(client, client.ok("POST", f"/api/projects/{name}/review", {"candidate": state["proposal"]["selected"]})["job"]["id"])
    _wait_job(client, client.ok("POST", f"/api/projects/{name}/plan/accept", {})["job"]["id"])
    _recolor(client, name, PORTHOLE, "#F9C832")
    assert client.state(name)["artwork"]["differs_from_planned"]
    path = workspace / name
    before = _snapshot(path)
    assert any(k.startswith("attempts/accepted/") for k in before) and any(k.startswith("review/") for k in before)
    job = _cancel_when(client, client.ok("POST", f"/api/projects/{name}/artwork/replan", {})["job"]["id"], "L1_INTAKE_DONE")
    assert job["status"] == "cancelled" and "L1_PROPOSAL_DONE" not in _stages(job)
    assert _snapshot(path) == before and not (path / "proposal.next").exists()
    after = client.state(name)
    assert after["artwork"]["differs_from_planned"] and after["checkpoint"]["exists"] and after["draft"]
    again = _wait_job(client, client.ok("POST", f"/api/projects/{name}/artwork/replan", {})["job"]["id"])
    assert again["status"] == "done" and not client.state(name)["artwork"]["differs_from_planned"]


def test_stop_a_physical_resize_replan(app):
    client, workspace = app
    name = _import(client, "&scale=1&outer_mm=120&name=resize")
    _wait_job(client, client.state(name)["job"]["id"])
    _wait_job(client, client.ok("POST", f"/api/projects/{name}/resize/analyze", {"process": None, "bambu_printer": None})["job"]["id"])
    _wait_job(client, client.ok("POST", f"/api/projects/{name}/resize/preview", {"size_mm": 180})["job"]["id"])
    preview = client.state(name)["resize"]["preview"]
    path = workspace / name
    before = _snapshot(path)
    body = {"preview_id": preview["preview_id"], "approve_size": True, "approve_changes": True}
    job = _cancel_when(client, client.ok("POST", f"/api/projects/{name}/resize/retry", body)["job"]["id"], "L1_INTAKE_DONE")
    assert job["status"] == "cancelled" and "L1_PROPOSAL_DONE" not in _stages(job)
    assert _snapshot(path) == before and not (path / "proposal.next").exists()
    assert client.state(name)["resize"]["sheet_size_mm"] == [120.0, 120.0]
    again = _wait_job(client, client.ok("POST", f"/api/projects/{name}/resize/retry", body)["job"]["id"])
    assert again["status"] == "done" and client.state(name)["resize"]["sheet_size_mm"] == [180.0, 180.0]


def _review(client: Client, name: str, candidate: str, widen: bool = True) -> str:
    return client.ok("POST", f"/api/projects/{name}/review", {"candidate": candidate, "widen_openings": widen})["job"]["id"]


@pytest.mark.parametrize("stage", [None, "WIDENING_CUTOUTS", "PREVIEWING"])
def test_stop_review_this_candidate_leaves_the_plan_as_it_was(app, stage):
    """"Review this candidate" (D-V3-056): stopped at once, during the automatic widening or during previews,
    the review being created is removed and project.json is unchanged; starting it again works."""

    client, workspace = app
    name = _import(client, f"&scale=2&name=review_{stage or 'now'}".lower())
    _wait_job(client, client.state(name)["job"]["id"])
    path = workspace / name
    before = _snapshot(path)
    candidate = client.state(name)["proposal"]["selected"]
    t0 = time.time()
    job = _cancel_when(client, _review(client, name, candidate), stage)
    stopped_in = time.time() - t0
    assert job["status"] == "cancelled" and "FINISHED" not in _stages(job), job["events"]
    assert not (path / "review").exists() and not (path / "draft").exists()
    after = _snapshot(path)
    assert after == before, {k for k in set(after) | set(before) if after.get(k) != before.get(k)}
    state = client.state(name)
    assert not state.get("draft") and state["stage"] != "review"
    assert stopped_in < 30, stopped_in
    again = _wait_job(client, _review(client, name, candidate))
    assert again["status"] == "done" and (path / "review").is_dir()


def test_stop_switching_to_another_candidate_puts_both_reviews_back(app):
    client, workspace = app
    name = _import(client, "&scale=2&name=switch")
    _wait_job(client, client.state(name)["job"]["id"])
    proposal = client.state(name)["proposal"]
    others = [c["name"] for c in proposal["candidates"]
              if c["name"] != proposal["selected"] and c["status"] != "PLANNING_FAILED"]
    assert others, "the fixture needs a second candidate"
    _wait_job(client, _review(client, name, proposal["selected"]))
    path = workspace / name
    before = _snapshot(path)
    job = _cancel_when(client, _review(client, name, others[0]), "WIDENING_CUTOUTS")
    assert job["status"] == "cancelled", job["events"]
    assert not (path / "reviews").exists()
    after = _snapshot(path)
    assert after == before, {k for k in set(after) | set(before) if after.get(k) != before.get(k)}
    assert client.state(name)["record"]["candidate"] == proposal["selected"]
    again = _wait_job(client, _review(client, name, others[0]))
    assert again["status"] == "done" and (path / "reviews" / proposal["selected"]).is_dir()


def test_after_a_stop_nothing_is_locked_and_no_thread_is_left(app):
    client, workspace = app
    threads = {t.name for t in threading.enumerate()}
    name = _import(client, "&scale=1&name=locks")
    job = _cancel_when(client, client.state(name)["job"]["id"], "L1_INTAKE_DONE")
    assert job["status"] == "cancelled"
    left = {t.name for t in threading.enumerate() if "process_request_thread" not in t.name}  # HTTP requests in flight
    assert left - threads <= {"layerforge-jobs"}  # planning ran on the one worker and left nothing behind
    assert client.ok("POST", f"/api/projects/{name}/delete", {})  # every file can be removed: nothing is held open
    assert not (workspace / name).exists()


def test_only_planning_jobs_offer_stop(app):
    client, _ = app
    name = _import(client, "&scale=1&name=other")
    job = client.ok("GET", f"/api/jobs/{client.state(name)['job']['id']}")
    assert job["cancellable"] is True
    _wait_job(client, job["id"])
    state = client.state(name)
    review = client.ok("POST", f"/api/projects/{name}/review", {"candidate": state["proposal"]["selected"]})["job"]
    assert review["cancellable"] is True  # "Review this candidate" (D-V3-056)
    _wait_job(client, review["id"])
    verify = client.ok("POST", f"/api/projects/{name}/verify", {})["job"]
    assert verify["cancellable"] is False
    assert client.call("POST", f"/api/jobs/{verify['id']}/cancel", {})[0] == 409
    _wait_job(client, verify["id"])
    assert client.call("POST", "/api/jobs/job999/cancel", {})[0] == 404


def test_browser_stop_button(app):
    """In Chrome (``tests/e2e/drive_cancel.mjs``): Stop in the progress bar of a running planning step, "Stopping…",
    then a short notice, the previous proposal unchanged, the page usable, and planning again works."""

    import json
    import subprocess

    from test_l4_app import BROWSERS, ROOT, _node_ok

    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    browser = chrome if chrome.is_file() else next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Chrome/Edge")
    client, workspace = app
    name = _import(client, "&scale=2&name=stopme")
    _wait_job(client, client.state(name)["job"]["id"])
    before = _snapshot(workspace / name)
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_cancel.mjs"), client.base + "/", str(browser), name,
                          str(ROOT / "artifacts" / "job_cancel_v1")], capture_output=True, text=True, encoding="utf-8", timeout=900)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    r = json.loads(lines[-1])
    assert r["ok"], r.get("error", "")
    assert r["console_errors"] == []
    assert r["stopping"] == "Stopping…" and "stopped. Nothing was changed." in r["notice"]
    assert r["after"]["seconds"] == r["before"]["seconds"] and r["next_enabled"]  # the previous proposal, usable
    assert r["again"]["status"] == "PROPOSAL_READY_FOR_REVIEW" and r["again"]["job"] == "done"
    assert not (workspace / name / "proposal.next").exists() and before  # (the second run replaced the proposal)
    # "Review this candidate" stopped from the same Stop button: no review, the Plan screen usable, then it opens.
    assert "Opening the layer review" in r["review_banner"] and "stopped. Nothing was changed." in r["review_notice"]
    assert r["review_after"]["draft"] is False and r["review_plan_screen"]
    assert r["review_again"] == {"draft": True, "job": "done"}
