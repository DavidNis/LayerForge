"""Packaged Windows build: the real ``dist/LayerForge/LayerForge.exe`` runs the L4 workflow.

Each test starts the executable (``--headless``: no window, serves until stdin is
closed) with a temporary projects folder and a temporary ``LOCALAPPDATA``, with
Python removed from ``PATH`` and ``PYTHONPATH``/``PYTHONHOME`` unset, and talks to it
only over HTTP.  The import/review/edit/restart/estimate/kit/zip and browser
workflows are the unchanged ``test_l4_app`` functions; a "restart" stops the running
executable and starts a new one on the same folder.

Skipped, with the reason, when the build is missing (``scripts/build_windows.ps1``).
This runs on the development machine, not on a clean Windows installation.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

import test_l4_app as l4

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "dist" / "LayerForge"
EXE = APP_DIR / "LayerForge.exe"

pytestmark = pytest.mark.skipif(not EXE.is_file(), reason=f"packaged build not found at {EXE}; run "
                                "scripts/build_windows.ps1 first")


def _clean_env(local_appdata: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    python_dirs = {Path(sys.executable).parent.resolve(), Path(sys.base_prefix).resolve()}
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                  if p and "python" not in p.lower()
                                  and not any(Path(p).resolve() == d or d in Path(p).resolve().parents
                                              for d in python_dirs))
    env["LOCALAPPDATA"] = str(local_appdata)
    return env


class ExeServer:
    """The packaged app as a process, with the ``shutdown``/``server_close`` of a server."""

    def __init__(self, workspace: Path, local_appdata: Path, port: int = 0) -> None:
        self.output: list[str] = []
        self.process = subprocess.Popen(
            [str(EXE), "--headless", "--workspace", str(workspace), "--port", str(port)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=_clean_env(local_appdata), cwd=str(workspace.parent))
        ready = threading.Event()

        def read() -> None:
            for line in self.process.stdout:
                self.output.append(line.rstrip())
                if line.startswith("LAYERFORGE_READY "):
                    self.url = line.split(" ", 1)[1].strip()
                    ready.set()
            ready.set()

        threading.Thread(target=read, daemon=True).start()
        ready.wait(180)
        if not getattr(self, "url", None):
            self.process.kill()
            raise AssertionError("packaged app did not become ready:\n" + "\n".join(self.output[-40:]))
        port = int(self.url.rstrip("/").rsplit(":", 1)[1])
        self.server_address = ("127.0.0.1", port)
        self.returncode: int | None = None

    def shutdown(self) -> None:
        if self.returncode is None:
            self.process.stdin.close()  # the launcher's clean-stop signal in headless mode
            self.returncode = self.process.wait(120)

    def server_close(self) -> None:
        pass


@pytest.fixture()
def exe_app(tmp_path, monkeypatch):
    local_appdata = tmp_path / "localappdata"
    running: dict[Path, ExeServer] = {}

    def start(workspace: Path):
        if workspace in running:  # restart: the old process must stop first (one app per folder)
            running[workspace].shutdown()
        server = running[workspace] = ExeServer(workspace, local_appdata)
        return server, l4.Client(server.url.rstrip("/"))

    monkeypatch.setattr(l4, "start", start)
    before = _snapshot(APP_DIR)
    server, client = start(tmp_path / "workspace")
    yield client, tmp_path / "workspace"
    for item in running.values():
        item.shutdown()
        assert item.returncode == 0, "\n".join(item.output[-40:])
    assert _snapshot(APP_DIR) == before, "the packaged app wrote into its application folder"
    assert not list((local_appdata / "LayerForge" / "instances").glob("*.json")), "instance record left behind"


def _snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    return {p.relative_to(folder).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in folder.rglob("*") if p.is_file()}


def test_packaged_info_static_and_request_errors(exe_app):
    l4.test_info_static_and_request_errors(exe_app)


def test_packaged_api_workflow_import_review_edit_reload_export(exe_app, tmp_path):
    l4.test_api_workflow_import_review_edit_reload_export(exe_app, tmp_path)


def test_packaged_browser_workflow(exe_app):
    l4.test_browser_workflow(exe_app)


def test_packaged_artwork_recolor_browser(exe_app):
    """Artwork colors in the frozen app: select, preview, apply, eyedropper, undo, verify, reload, Try Again."""

    import test_artwork_recolor as recolor

    client, workspace = exe_app
    recolor.test_browser_artwork_panel((client, workspace, None))


def _launch(args: list[str], local_appdata: Path, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(EXE), "--headless", *args], input="", capture_output=True, text=True, timeout=180,
                          env=_clean_env(local_appdata), cwd=str(cwd))


def test_packaged_startup_errors_ports_and_single_instance(tmp_path):
    local_appdata = tmp_path / "localappdata"
    workspace = tmp_path / "workspace"
    first = ExeServer(workspace, local_appdata)
    try:
        # A second launch on the same projects folder does not start a second server.
        second = _launch(["--workspace", str(workspace)], local_appdata, tmp_path)
        assert second.returncode == 3 and "already running" in second.stdout + second.stderr
        assert first.url in second.stdout + second.stderr

        # Its port is occupied: another folder's app falls back to the next free port.
        other = ExeServer(tmp_path / "other", local_appdata, port=first.server_address[1])
        try:
            assert other.server_address[1] != first.server_address[1]
            for server in (first, other):
                with urllib.request.urlopen(server.url + "api/projects", timeout=10) as response:
                    assert response.status == 200
        finally:
            other.shutdown()
        assert other.returncode == 0
    finally:
        first.shutdown()
    assert first.returncode == 0 and "server stopped" in "\n".join(first.output)

    # Startup errors are reported with a non-zero exit, not a silent crash.
    inside = _launch(["--workspace", str(APP_DIR / "projects")], local_appdata, tmp_path)
    assert inside.returncode == 2 and "inside the application folder" in inside.stdout + inside.stderr
    assert not (APP_DIR / "projects").exists()
    blocked = tmp_path / "a_file"
    blocked.write_text("not a folder")
    bad = _launch(["--workspace", str(blocked / "sub")], local_appdata, tmp_path)
    assert bad.returncode == 2 and "cannot be created or written" in bad.stdout + bad.stderr

    # After a clean stop the same folder starts again (the lock was released).
    again = ExeServer(workspace, local_appdata)
    again.shutdown()
    assert again.returncode == 0


def test_packaged_stop_while_a_job_runs(tmp_path):
    # Closing the app during planning stops it cleanly (exit 0, no crash) and keeps the project.
    server = ExeServer(tmp_path / "workspace", tmp_path / "localappdata")
    client = l4.Client(server.url.rstrip("/"))
    created = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1", raw=l4.SAILBOAT.read_bytes())
    time.sleep(3)
    assert client.state(created["project"])["job"]["status"] == "running"
    server.shutdown()
    assert server.returncode == 0, "\n".join(server.output[-40:])
    assert any("stopping while running" in line for line in server.output)
    assert (tmp_path / "workspace" / created["project"] / "project.json").is_file()
