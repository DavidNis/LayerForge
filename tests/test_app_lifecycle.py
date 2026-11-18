"""The launcher window owns the app's lifetime (D-V3-055, replacing the tab-based stop of D-V3-050).

Request tracking (``app/lifecycle.py``): requests being answered finish before the server closes, later ones are
refused.  The real launcher (``packaging/layerforge_launcher.py``, the exe's entry point, run from source) in a
real window: it stays running when the browser (Chrome) closes, a second start does not open a second copy,
"Stop LayerForge" / closing the window stops it cleanly, and with a planning step running it asks first and
stops that step through its own cancellation (nothing half-written is left).  The window is driven through the
Win32 API (WM_CLOSE, the confirmation dialog's Yes) - the same messages a user's clicks send.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from layerforge_l1.app import lifecycle, make_server
from layerforge_l1.app import server as server_module

ROOT = Path(__file__).resolve().parents[1]
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
# The launcher under test: the source entry point, or the packaged exe when LAYERFORGE_TEST_EXE names it.
_EXE = os.environ.get("LAYERFORGE_TEST_EXE")
LAUNCH = [_EXE] if _EXE else [sys.executable, str(ROOT / "packaging" / "layerforge_launcher.py")]


# -- requests in progress -----------------------------------------------------------------------------------
def test_wait_idle_returns_when_the_last_request_is_answered():
    life = lifecycle.Lifecycle()
    assert life.request_started()
    threading.Timer(0.3, life.request_finished).start()
    assert not life.wait_idle(0.05)
    assert life.wait_idle(5)
    life.close_now("test")
    assert not life.request_started()


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_requests_in_progress_finish_and_later_ones_are_refused_once_closing(tmp_path):
    server = make_server(tmp_path / "ws", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    life = server.app.lifecycle
    started, release = threading.Event(), threading.Event()
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
        life.close_now("test")
        status, body = _get(url + "api/info")
        assert status == 503 and body["error"]["closing"] is True
        assert not life.wait_idle(0.1)
        release.set()
        thread.join(10)
        assert replies[0][0] == 200 and life.wait_idle(1)  # answered, not cut off
    finally:
        server_module.Handler._get = original
        release.set()
        server.shutdown()
        server.server_close()


def test_no_page_stop_and_no_heartbeat_endpoints(tmp_path):
    server = make_server(tmp_path / "ws", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        info = _get(url + "api/info")[1]
        assert "can_stop" not in info and "heartbeat_s" not in info
        for path in ("api/stop", "api/presence"):
            request = urllib.request.Request(url + path, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=10)
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


# -- the real launcher window ---------------------------------------------------------------------------------
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the launcher window is tested on Windows")


def _user32():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    return user32


def _process_tree(pid: int) -> set[int]:
    """The process and its descendants (a venv's python.exe starts the real interpreter as a child)."""

    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    parents: dict[int, int] = {}
    entry = Entry()
    entry.dwSize = ctypes.sizeof(Entry)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        parents[entry.th32ProcessID] = entry.th32ParentProcessID
        ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    kernel32.CloseHandle(snap)
    tree, grew = {pid}, True
    while grew:
        grew = False
        for child, parent in parents.items():
            if parent in tree and child not in tree:
                tree.add(child)
                grew = True
    return tree


def _windows_of(pid: int, title: str, class_name: str | None = None) -> list[int]:
    """Visible top-level windows of a process (or its children) with this title (and class)."""

    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    found: list[int] = []
    pids = _process_tree(pid)
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            text = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, text, 256)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if text.value == title and (class_name is None or cls.value == class_name):
                found.append(hwnd)
        return True

    user32.EnumWindows(proc(visit), 0)
    return found


class Launcher:
    """``packaging/layerforge_launcher.py`` in a subprocess with its own projects folder and local data."""

    def __init__(self, tmp: Path, *extra: str) -> None:
        self.env = {**os.environ, "LOCALAPPDATA": str(tmp / "localappdata"), "PYTHONPATH": str(ROOT / "src")}
        self.workspace = tmp / "ws"
        self.proc = subprocess.Popen([*LAUNCH, "--workspace", str(self.workspace), "--port", "0", "--no-browser", *extra],
                                     env=self.env, cwd=ROOT)
        self.url = self._published_url()

    def _published_url(self) -> str:
        folder = Path(self.env["LOCALAPPDATA"]) / "LayerForge" / "instances"
        end = time.time() + 90
        while time.time() < end:
            for record in folder.glob("*.json"):
                try:
                    url = json.loads(record.read_text(encoding="utf-8"))["url"]
                    if _get(url + "api/info")[0] == 200:
                        return url
                except (OSError, ValueError, KeyError):
                    pass
            if self.proc.poll() is not None:
                raise AssertionError(f"launcher exited with {self.proc.returncode}")
            time.sleep(0.3)
        raise AssertionError("launcher did not start")

    def window(self) -> int:
        end = time.time() + 20
        while time.time() < end:
            found = _windows_of(self.proc.pid, "LayerForge", "TkTopLevel")
            if found:
                return found[0]
            time.sleep(0.2)
        raise AssertionError("no LayerForge window")

    def close_window(self) -> None:
        """What the window's close box and "Stop LayerForge" do (WM_CLOSE runs the same stop routine)."""

        assert _user32().PostMessageW(self.window(), 0x0010, 0, 0)  # WM_CLOSE

    def answer_yes(self, timeout: float = 20) -> str:
        """Press Yes in the launcher's confirmation dialog; returns once it was found."""

        end = time.time() + timeout
        while time.time() < end:
            dialogs = _windows_of(self.proc.pid, "LayerForge", "#32770")
            if dialogs:
                assert _user32().PostMessageW(dialogs[0], 0x0111, 6, 0)  # WM_COMMAND IDYES
                return "yes"
            time.sleep(0.2)
        raise AssertionError("no confirmation dialog")

    def alive(self) -> bool:
        port = int(self.url.rsplit(":", 1)[1].strip("/"))
        try:
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
            return True
        except OSError:
            return False

    def kill(self) -> None:
        if self.proc.poll() is None:
            for pid in sorted(_process_tree(self.proc.pid) - {self.proc.pid}):
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            self.proc.kill()
            self.proc.wait(10)


@pytest.fixture
def launched(tmp_path):
    started: list[Launcher] = []

    def start(*extra: str) -> Launcher:
        launcher = Launcher(tmp_path, *extra)
        started.append(launcher)
        return launcher

    yield start
    for launcher in started:
        launcher.kill()


@windows_only
def test_window_keeps_the_app_running_after_the_browser_closes_and_stops_it_cleanly(launched, tmp_path):
    app = launched()
    assert app.window()  # the visible launcher window
    if CHROME.is_file():  # open LayerForge in Chrome, then close the browser
        profile = tmp_path / "chrome"
        chrome = subprocess.Popen([str(CHROME), "--headless=new", f"--user-data-dir={profile}", "--no-first-run",
                                   "--disable-sync", app.url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(6)
        chrome.terminate()
        chrome.wait(20)
    time.sleep(15)  # well past the old automatic stop's grace period
    assert app.alive() and app.proc.poll() is None
    # A second start opens no second copy: it returns at once and the one window stays.
    second = subprocess.run([*LAUNCH, "--workspace", str(app.workspace), "--no-browser"], env=app.env, cwd=ROOT, timeout=60)
    assert second.returncode == 0 and len(_windows_of(app.proc.pid, "LayerForge", "TkTopLevel")) == 1
    app.close_window()  # no step running: stops without asking
    assert app.proc.wait(60) == 0 and not app.alive()
    log = (Path(app.env["LOCALAPPDATA"]) / "LayerForge" / "logs" / "layerforge.log").read_text(encoding="utf-8")
    assert "server stopped" in log
    again = launched()  # the projects folder is free again
    assert again.alive()
    again.close_window()
    assert again.proc.wait(60) == 0


@windows_only
def test_stopping_with_a_planning_step_running_asks_and_stops_that_step_cleanly(launched):
    from test_l4_app import SAILBOAT

    app = launched()
    request = urllib.request.Request(app.url + "api/projects?filename=sailboat_512.png&scale=2&name=busy",
                                     data=SAILBOAT.read_bytes(), method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        job = json.loads(response.read())["job"]
    end = time.time() + 120
    while time.time() < end:  # inside the planner's search
        current = _get(app.url + f"api/jobs/{job['id']}")[1]
        if any(e["stage"] == "L1_INTAKE_DONE" for e in current["events"]):
            break
        time.sleep(0.05)
    app.close_window()
    assert app.answer_yes() == "yes"  # "LayerForge is still working on: propose (busy) ... Stop anyway?"
    assert app.proc.wait(90) == 0 and not app.alive()
    project = app.workspace / "busy"
    assert not (project / "proposal.next").exists() and not (project / "proposal").exists()  # nothing half-written
    log = (Path(app.env["LOCALAPPDATA"]) / "LayerForge" / "logs" / "layerforge.log").read_text(encoding="utf-8")
    assert "stopping while running" not in log  # the step was cancelled before the server stopped
