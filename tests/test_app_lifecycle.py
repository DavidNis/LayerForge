"""The desktop app stops by itself when its last tab is closed, when that is safe (D-V3-050).

``lifecycle.Lifecycle`` holds the rules (unit-tested with a fake clock); the server feeds it heartbeats,
unload beacons and every request it handles; the launcher asks it once a second.  The browser tests run the
real launcher (in this process, with shorter timings) and a real headless Edge/Chrome:
``tests/e2e/drive_lifecycle.mjs``.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

from layerforge_l1.app import launcher, lifecycle, make_server
from layerforge_l1.app import server as server_module


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def rules(**timings: float) -> tuple[lifecycle.Lifecycle, Clock]:
    clock = Clock()
    return lifecycle.Lifecycle(clock, **{"stale_s": 1800, "grace_s": 10, "await_s": 180, "sleep_s": 8 * 3600,
                                         **timings}), clock


def test_defaults_stop_quickly_on_close_but_wait_long_for_silent_tabs():
    assert lifecycle.GRACE_S <= 10  # closing the last tab: stopped within seconds
    assert lifecycle.STALE_S >= 30 * 60  # a tab silent for minutes (throttled, briefly frozen) is not given up
    assert lifecycle.SLEEP_S >= 8 * 3600


def test_waits_for_the_first_page_after_a_start():
    life, clock = rules()
    clock.now += 179
    assert life.try_close(False) is None  # the browser may be slow to open
    clock.now += 2
    assert life.try_close(False) == "no LayerForge tab is open"  # it never came: do not run forever
    assert life.closing


def test_an_open_tab_keeps_it_running_and_the_last_one_leaving_stops_it_after_the_grace_period():
    life, clock = rules()
    assert life.seen("a") and life.seen("b")
    clock.now += 100
    life.seen("a"), life.seen("b")
    life.left("a")  # one of two tabs closed
    clock.now += 60
    assert life.try_close(False) is None
    life.left("b")  # the last one
    clock.now += 9
    assert life.try_close(False) is None
    clock.now += 2
    assert life.try_close(False)


def test_a_reload_checks_in_again_within_the_grace_period():
    life, clock = rules()
    life.seen("a")
    life.left("a")  # pagehide of the reload
    clock.now += 1.5
    assert life.request_started()  # the new page loads
    life.request_finished()
    life.seen("a2")
    clock.now += 60
    assert life.try_close(False) is None and life.open_tabs() == 1


def test_a_crashed_browser_is_noticed_when_heartbeats_stop():
    life, clock = rules()
    life.seen("a")
    clock.now += 1799
    assert life.try_close(False) is None
    clock.now += 2
    assert life.open_tabs() == 0 and life.try_close(False)


def test_a_sleeping_tab_keeps_it_running_until_it_wakes():
    life, clock = rules()
    life.seen("a")
    life.seen("a", sleeping=True)  # the page's freeze event: no heartbeats until it wakes
    clock.now += 7 * 3600
    assert life.try_close(False) is None and life.open_tabs() == 1
    life.seen("a")  # woke up: heartbeats again, normal rules
    clock.now += 1801
    assert life.try_close(False)


def test_a_sleeping_tab_is_given_up_after_the_sleep_limit():
    life, clock = rules()
    life.seen("a", sleeping=True)
    clock.now += 8 * 3600 + 1  # e.g. the browser was closed while the tab slept (no unload event)
    assert life.try_close(False)


def test_running_jobs_and_requests_being_answered_keep_it_running():
    life, clock = rules()
    life.seen("a")
    life.left("a")
    clock.now += 600
    assert life.try_close(True) is None  # planning / export still running
    assert life.request_started()  # a save or download being answered
    clock.now += 600
    assert life.try_close(False) is None
    life.request_finished()
    clock.now += 11
    assert life.try_close(False)


def test_closing_refuses_new_requests_and_heartbeats():
    life, clock = rules()
    life.seen("a")
    life.left("a")
    clock.now += 11
    assert life.try_close(False)
    assert not life.request_started() and not life.seen("b")
    assert life.try_close(False) is None  # decided once
    assert life.wait_idle(0.1)


def test_a_second_start_waits_for_its_page():
    life, clock = rules()
    life.seen("a")
    life.left("a")
    clock.now += 5
    life.expect_page()  # LayerForge.exe double-clicked again: a browser page is coming
    clock.now += 100
    assert life.try_close(False) is None
    life.seen("b")  # it arrived: back to the normal rules
    life.left("b")
    clock.now += 11
    assert life.try_close(False)


def test_wait_idle_returns_when_the_last_request_is_answered():
    life, _ = rules()
    assert life.request_started()
    threading.Timer(0.3, life.request_finished).start()
    assert not life.wait_idle(0.05)
    assert life.wait_idle(5)


# -- the server --------------------------------------------------------------------------------------------
def _post(url: str, body: object, origin: str | None = None, content_type: str = "application/json"):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": content_type, **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


@pytest.fixture
def served(tmp_path):
    server = make_server(tmp_path / "ws", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


def test_presence_endpoint(served):
    server, url = served
    life = server.app.lifecycle
    assert _get(url + "api/info")[1]["heartbeat_s"] == lifecycle.HEARTBEAT_S
    # sendBeacon posts text/plain from the page's own origin
    assert _post(url + "api/presence", {"tab": "t1"}, origin=url.rstrip("/"), content_type="text/plain")[0] == 200
    assert life.open_tabs() == 1
    assert _post(url + "api/presence", {"tab": "t1", "leaving": True}, content_type="text/plain")[0] == 200
    assert life.open_tabs() == 0
    assert _post(url + "api/presence", {"tab": "t2", "sleeping": True}, content_type="text/plain")[0] == 200
    assert life._tabs["t2"][1] is True and life.open_tabs() == 1
    assert _post(url + "api/presence", {"tab": "t2", "leaving": True})[0] == 200
    assert _post(url + "api/presence", {"tab": "x"}, origin="http://elsewhere.example")[0] == 403
    assert _post(url + "api/presence", {})[0] == 400
    assert life.open_tabs() == 0


def test_requests_are_counted_and_refused_once_closing(served):
    server, url = served
    life = server.app.lifecycle
    started = threading.Event()
    release = threading.Event()
    original = server_module.Handler._get

    def slow_get(self, parts, query):
        if parts == ["api", "projects"]:
            started.set()
            release.wait(10)
        return original(self, parts, query)

    server_module.Handler._get = slow_get
    try:
        replies = []
        thread = threading.Thread(target=lambda: replies.append(_get(url + "api/projects")))
        thread.start()
        assert started.wait(5)
        assert life.try_close(False) is None  # a request is being answered
        life.close_now("test")
        status, body = _get(url + "api/info")
        assert status == 503 and body["error"]["closing"] is True
        assert _post(url + "api/presence", {"tab": "late"})[0] == 503
        assert not life.wait_idle(0.1)
        release.set()
        thread.join(10)
        assert replies[0][0] == 200  # the request in progress was answered, not cut off
        assert life.wait_idle(1)
    finally:
        server_module.Handler._get = original
        release.set()


def test_launch_announcement_extends_the_wait(served):
    server, url = served
    life = server.app.lifecycle
    life.grace_s, life._await_until, life.await_s = 0.0, 0.0, 60.0  # would close now if nothing were coming
    assert _get(url + "api/info")[0] == 200
    time.sleep(0.05)
    assert life._await_until == 0.0  # a plain request announces nothing
    assert _get(url + "api/info?launch=1")[0] == 200  # LayerForge.exe started again: a page is coming
    time.sleep(0.05)
    assert life.try_close(False) is None


# -- the launcher ------------------------------------------------------------------------------------------
@pytest.fixture
def local_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr("sys.stdout", None)  # as in the windowed executable; restored after the test
    monkeypatch.setattr("sys.stderr", None)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: None)
    return tmp_path / "localappdata"


def _start(workspace, runners=None):
    """The real launcher (no window) in a thread; returns (thread, result, url)."""

    result: dict = {}
    thread = threading.Thread(target=lambda: result.setdefault(
        "code", launcher.main(["--workspace", str(workspace), "--port", "0"])), daemon=True)
    thread.start()
    lock = launcher.InstanceLock(workspace)
    for _ in range(150):
        url = lock.running_url()
        if url:
            return thread, result, url
        thread.join(0.2)
    raise AssertionError("launcher did not start")


def test_never_opened_page_stops_after_the_wait_and_the_folder_can_start_again(tmp_path, local_appdata, monkeypatch):
    monkeypatch.setattr(lifecycle, "AWAIT_S", 2.0)
    monkeypatch.setattr(lifecycle, "GRACE_S", 0.5)
    workspace = launcher.check_workspace(tmp_path / "ws")
    thread, result, url = _start(workspace)
    thread.join(20)
    assert not thread.is_alive() and result["code"] == 0
    log = (local_appdata / "LayerForge" / "logs" / "layerforge.log").read_text(encoding="utf-8")
    assert "no LayerForge tab is open; stopping" in log and "server stopped" in log
    thread, result, url2 = _start(workspace)  # starting again (the EXE double-clicked again)
    assert _get(url2 + "api/info")[0] == 200
    assert _post(url2 + "api/stop", {})[0] == 200
    thread.join(20)
    assert not thread.is_alive() and result["code"] == 0


def test_second_start_while_the_first_is_closing_waits_and_starts(tmp_path, local_appdata):
    workspace = launcher.check_workspace(tmp_path / "ws")
    closing = launcher.InstanceLock(workspace)
    assert closing.acquire()  # held by a copy that no longer answers (it is shutting down)
    threading.Timer(2.0, closing.release).start()
    thread, result, url = _start(workspace)  # waits for the lock instead of reporting "does not answer"
    assert _get(url + "api/info")[0] == 200
    assert _post(url + "api/stop", {})[0] == 200
    thread.join(20)
    assert result["code"] == 0


# -- real browser ------------------------------------------------------------------------------------------
def _browser_or_skip():
    from test_l4_app import BROWSERS, _node_ok

    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    return browser


def _drive(url, browser, scenario, on_checkpoint=lambda name: None):
    from test_l4_app import ROOT

    shots = ROOT / "artifacts" / "app_lifecycle_v1"
    driver = subprocess.Popen(["node", str(ROOT / "tests" / "e2e" / "drive_lifecycle.mjs"), url, str(browser), scenario,
                               str(shots)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8")
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(driver.stderr), daemon=True).start()
    last = ""
    for line in driver.stdout:
        if line.startswith("@@"):
            on_checkpoint(line[2:].strip())
            driver.stdin.write("go\n")
            driver.stdin.flush()
        elif line.startswith("{"):
            last = line
    driver.wait(120)
    assert last, "".join(stderr)[-3000:]
    r = json.loads(last)
    (shots / f"drive_{scenario}.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    assert r["ok"], r.get("error", "") + "\n" + "".join(stderr)[-3000:]
    assert r["console_errors"] == []
    return {step["name"]: step for step in r["steps"]}


def test_browser_tabs_reload_and_running_job(tmp_path, local_appdata, monkeypatch):
    """Two tabs, close one: still running; reload: still running; close the last one while a step runs:
    still running until it finishes, then it stops within the grace period (the unload beacon, not the
    much longer heartbeat timeout)."""

    browser = _browser_or_skip()
    monkeypatch.setattr(lifecycle, "GRACE_S", 3.0)
    monkeypatch.setattr(lifecycle, "STALE_S", 60.0)
    monkeypatch.setattr(server_module, "HEARTBEAT_S", 2.0)
    captured = {}
    original_start = launcher.Runner.start

    def start(self):
        captured["runner"] = self
        return original_start(self)

    monkeypatch.setattr(launcher.Runner, "start", start)
    workspace = launcher.check_workspace(tmp_path / "ws")
    thread, result, url = _start(workspace)
    app = captured["runner"].server.app
    from test_l4_launcher import _png

    name = app.workspace.create("job.png", _png(), {})
    release = threading.Event()
    finished = threading.Event()

    def checkpoint(step):
        if step == "job":
            app.submit("test_step", name, lambda progress: (release.wait(60), finished.set()))
        elif step == "release":
            release.set()

    try:
        steps = _drive(url, browser, "tabs", checkpoint)
        assert steps["two_tabs_open"]["exit_label"] == "Exit"
        assert steps["closed_one_of_two"]["alive"] is True
        assert steps["after_reload"]["alive"] is True
        assert steps["last_tab_closed_while_job_runs"]["alive"] is True
        assert finished.is_set()
        stopped = steps["after_job_finished"]["stopped_after_s"]
        assert stopped is not None and stopped < 10  # grace 3 s + the 1 s check, far below the 60 s timeout
        thread.join(20)
        assert not thread.is_alive() and result["code"] == 0
        assert captured["runner"].abandoned == []  # nothing was cut off
    finally:
        release.set()
        if thread.is_alive():
            _post(url + "api/stop", {"force": True})
            thread.join(20)


def test_browser_crash_is_noticed(tmp_path, local_appdata, monkeypatch):
    browser = _browser_or_skip()
    monkeypatch.setattr(lifecycle, "GRACE_S", 2.0)
    monkeypatch.setattr(lifecycle, "STALE_S", 12.0)
    monkeypatch.setattr(server_module, "HEARTBEAT_S", 2.0)
    thread, result, url = _start(launcher.check_workspace(tmp_path / "ws"))
    try:
        steps = _drive(url, browser, "crash")
        assert steps["five_s_after_crash"]["alive"] is True  # no premature stop
        assert steps["after_crash"]["crash_to_stop_s"] is not None and steps["after_crash"]["crash_to_stop_s"] < 25
        thread.join(20)
        assert result["code"] == 0
    finally:
        if thread.is_alive():
            _post(url + "api/stop", {"force": True})
            thread.join(20)


def test_browser_exit_stops_and_other_tabs_say_so(tmp_path, local_appdata, monkeypatch):
    browser = _browser_or_skip()
    monkeypatch.setattr(server_module, "HEARTBEAT_S", 2.0)
    thread, result, url = _start(launcher.check_workspace(tmp_path / "ws"))
    try:
        steps = _drive(url, browser, "exit")
        assert steps["exit_clicked"]["dialogs"] == ["Exit LayerForge?"]
        assert steps["exit_clicked"]["stopped_after_s"] is not None
        assert steps["other_tab"]["shows_stopped"] is True
        thread.join(20)
        assert result["code"] == 0
    finally:
        if thread.is_alive():
            _post(url + "api/stop", {"force": True})
            thread.join(20)


def test_browser_sleeping_tab_keeps_it_running(tmp_path, local_appdata, monkeypatch):
    """The browser freezes the only tab (as Edge sleeping tabs do): the page's freeze event says so, and the app
    keeps running well past the heartbeat timeout; when the tab wakes, it checks in again."""

    browser = _browser_or_skip()
    monkeypatch.setattr(lifecycle, "GRACE_S", 2.0)
    monkeypatch.setattr(lifecycle, "STALE_S", 6.0)
    monkeypatch.setattr(lifecycle, "SLEEP_S", 600.0)
    monkeypatch.setattr(server_module, "HEARTBEAT_S", 2.0)
    captured = {}
    original_start = launcher.Runner.start

    def start(self):
        captured["runner"] = self
        return original_start(self)

    monkeypatch.setattr(launcher.Runner, "start", start)
    thread, result, url = _start(launcher.check_workspace(tmp_path / "ws"))
    try:
        steps = _drive(url, browser, "sleep")
        assert steps["frozen_beyond_stale"]["alive"] is True  # 15 s frozen > 6 s heartbeat timeout
        assert steps["woken"]["alive"] is True and steps["woken"]["stopped_page"] is False
        assert captured["runner"].server.app.lifecycle.open_tabs() == 0  # the driver closed its tab at the end
    finally:
        if thread.is_alive():
            _post(url + "api/stop", {"force": True})
            thread.join(20)
