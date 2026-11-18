"""Desktop launcher (``layerforge_l1.app.launcher``) without a packaged build.

Port handling, the one-instance-per-folder lock, projects-folder checks and a
clean start/stop of the real server.  ``test_windows_build`` repeats the startup
checks against the packaged executable.
"""

from __future__ import annotations

import urllib.request

import pytest

from layerforge_l1.app import launcher, make_server


@pytest.fixture(autouse=True)
def local_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    return tmp_path / "localappdata"


def test_occupied_port_is_refused_and_the_next_one_used(tmp_path):
    first = make_server(tmp_path / "a", port=0)
    port = first.server_address[1]
    try:
        with pytest.raises(OSError):  # no silent sharing of an occupied port (Windows SO_REUSEADDR)
            make_server(tmp_path / "b", port=port)
        second = launcher.bind_server(tmp_path / "b", port)
        assert second.server_address[1] != port
        second.server_close()
    finally:
        first.server_close()


def test_runner_serves_and_stops_cleanly(tmp_path):
    runner = launcher.Runner(launcher.check_workspace(tmp_path / "ws"), 0)
    url = runner.start()
    with urllib.request.urlopen(url + "api/info", timeout=10) as response:
        assert response.status == 200
    assert runner.busy() == []
    runner.stop()
    with pytest.raises(OSError):
        urllib.request.urlopen(url + "api/info", timeout=3)


def test_one_instance_per_projects_folder(tmp_path):
    workspace = launcher.check_workspace(tmp_path / "ws")
    first, second = launcher.InstanceLock(workspace), launcher.InstanceLock(workspace)
    assert first.acquire()
    try:
        assert not second.acquire()
        assert second.running_url() is None  # nothing published or answering yet
        first.publish("http://127.0.0.1:1/")
        assert second.running_url() is None  # published but not answering
        assert launcher.InstanceLock(launcher.check_workspace(tmp_path / "other")).acquire()
    finally:
        first.release()
    assert not first.record_path.exists()
    assert second.acquire()
    second.release()


def test_projects_folder_checks(tmp_path, monkeypatch):
    blocked = tmp_path / "file"
    blocked.write_text("x")
    with pytest.raises(launcher.StartupError, match="cannot be created or written"):
        launcher.check_workspace(blocked / "sub")
    monkeypatch.setattr(launcher, "application_folder", lambda: tmp_path / "app")
    with pytest.raises(launcher.StartupError, match="inside the application folder"):
        launcher.check_workspace(tmp_path / "app" / "projects")
    assert launcher.check_workspace(tmp_path / "projects").is_dir()
    assert launcher.documents_folder().name  # resolves without error


def test_second_launch_reuses_the_running_app(tmp_path, monkeypatch):
    # Regression: the second launch renamed the log file the running app holds open and crashed.
    monkeypatch.setattr("sys.stdout", None)  # as in the windowed executable; restored after the test
    monkeypatch.setattr("sys.stderr", None)
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    workspace = launcher.check_workspace(tmp_path / "ws")
    log = launcher.setup_log(headless=False)
    held = open(log, "a", encoding="utf-8")  # noqa: SIM115 - the running app's open log
    lock = launcher.InstanceLock(workspace)
    runner = launcher.Runner(workspace, 0)
    assert lock.acquire()
    try:
        lock.publish(runner.start())
        assert launcher.main(["--workspace", str(workspace), "--no-browser"]) == 0
        assert opened == []
        assert launcher.main(["--workspace", str(workspace)]) == 0
        assert opened == [runner.url]
        assert launcher.main(["--workspace", str(workspace), "--headless"]) == launcher.EXIT_ALREADY_RUNNING
        assert "already running" in log.read_text(encoding="utf-8")
    finally:
        runner.stop()
        lock.release()
        held.close()


def _post(url: str, body: dict, origin: str | None = None) -> tuple[int, dict]:
    import json
    import urllib.error

    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def _get_json(url: str) -> dict:
    import json

    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read())


def test_the_launcher_opens_its_window(tmp_path, monkeypatch):
    """Normal startup runs the control window (``run_window``); --headless keeps the stdin-controlled mode."""

    calls = []
    monkeypatch.setattr(launcher, "run_window", lambda runner, lock, log, open_browser: calls.append(open_browser) or 0)
    workspace = launcher.check_workspace(tmp_path / "ws")
    assert launcher.main(["--workspace", str(workspace)]) == 0 and calls == [True]
    assert launcher.main(["--workspace", str(workspace), "--no-browser"]) == 0 and calls == [True, False]
    assert not hasattr(launcher, "run_background")


def test_a_startup_failure_is_shown_and_nothing_keeps_running(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    shown = []
    monkeypatch.setattr(launcher, "_show_error", lambda text, headless: shown.append((text, headless)))

    def no_port(*args, **kwargs):
        raise launcher.StartupError("No local port could be opened for the app server")

    monkeypatch.setattr(launcher, "bind_server", no_port)
    workspace = launcher.check_workspace(tmp_path / "ws")
    assert launcher.main(["--workspace", str(workspace), "--no-browser"]) == launcher.EXIT_STARTUP_ERROR
    assert len(shown) == 1 and "could not start" in shown[0][0] and shown[0][1] is False  # a visible message
    lock = launcher.InstanceLock(workspace)
    assert lock.acquire()  # the lock was released
    lock.release()


def _png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()
