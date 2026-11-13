"""Desktop launcher for the L4 application (the packaged ``LayerForge.exe``).

Double-clicking the executable starts the same local server ``python -m layerforge_l1 app``
runs, in the background (no window), and opens the UI in the default browser.  It stops by
itself when the last LayerForge tab has been closed, once nothing is running (``lifecycle.py``:
reloads, a second tab and running jobs keep it open; a crashed browser is noticed when its
heartbeats stop).  Exit in the page's top bar stops it at once (``POST /api/stop``).  Only a
startup failure shows a window (an error message).

Where files go (never inside the application folder):

    projects    Documents\\LayerForge (or ``--workspace``)
    log, cache  %LOCALAPPDATA%\\LayerForge\\logs, \\cache

Startup checks: the projects folder must be writable and outside the application
folder; one instance per projects folder (a second launch only reopens the
browser on the running one); an occupied port moves to the next free one.

``--headless`` runs without a window for automated checks of the packaged build:
it prints ``LAYERFORGE_READY <url>`` and serves until stdin is closed.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
import threading
import time
import traceback
import urllib.request
import uuid
import webbrowser
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLBACKEND", "Agg")  # the bundle contains tkinter; never let matplotlib pick it

APP_NAME = "LayerForge"
PORT_TRIES = 20
EXIT_STARTUP_ERROR, EXIT_ALREADY_RUNNING = 2, 3


class StartupError(Exception):
    """A problem the user can act on (shown in a message box and the log)."""


def local_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME


def documents_folder() -> Path:
    """The user's Documents folder (follows OneDrive/known-folder redirection)."""

    if sys.platform == "win32":
        try:
            folder_id = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")  # FOLDERID_Documents
            guid = (ctypes.c_byte * 16).from_buffer_copy(folder_id.bytes_le)
            path = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(path)) == 0:
                result = Path(path.value)
                ctypes.windll.ole32.CoTaskMemFree(path)
                return result
        except (OSError, AttributeError, ValueError):
            pass
    return Path.home() / "Documents"


def application_folder() -> Path | None:
    """The folder holding the packaged executable and its bundle (None when not packaged)."""

    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None


def check_workspace(workspace: Path) -> Path:
    workspace = workspace.expanduser().resolve()
    app_dir = application_folder()
    if app_dir is not None and workspace.is_relative_to(app_dir):
        raise StartupError(f"The projects folder {workspace} is inside the application folder {app_dir}. "
                           "Choose a folder outside it (--workspace) so projects survive replacing the app.")
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        probe = workspace / f".write-test-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as error:
        raise StartupError(f"The projects folder {workspace} cannot be created or written: {error}") from None
    return workspace


def setup_log(headless: bool) -> Path:
    """Send output to a log file when there is no console (windowed executable)."""

    logs = local_data() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / "layerforge.log"
    if sys.stdout is None or sys.stderr is None or not headless:
        try:
            if log.exists():
                log.replace(logs / "layerforge.previous.log")
        except OSError:
            pass  # held open by a running instance: append to it instead
        stream = open(log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives for the process
        sys.stdout = sys.stderr = stream
    print(f"[launcher] {time.strftime('%Y-%m-%d %H:%M:%S')} {APP_NAME} start, pid {os.getpid()}, "
          f"executable {sys.executable}", flush=True)
    return log


class InstanceLock:
    """One running app per projects folder; the record names the running server's URL."""

    def __init__(self, workspace: Path) -> None:
        key = hashlib.sha256(str(workspace).lower().encode()).hexdigest()[:16]
        folder = local_data() / "instances"
        folder.mkdir(parents=True, exist_ok=True)
        self.lock_path, self.record_path = folder / f"{key}.lock", folder / f"{key}.json"
        self.handle: Any = None

    def acquire(self) -> bool:
        handle = open(self.lock_path, "a+b")  # noqa: SIM115 - held until release()
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def running_url(self, announce: bool = False) -> str | None:
        """The running app's URL if it answers; ``announce``: a page is about to be opened on it, so it
        waits for that page instead of stopping for lack of open tabs."""

        try:
            url = json.loads(self.record_path.read_text(encoding="utf-8"))["url"]
            with urllib.request.urlopen(url + "api/info" + ("?launch=1" if announce else ""), timeout=3) as response:
                return url if response.status == 200 else None
        except (OSError, ValueError, KeyError):
            return None

    def publish(self, url: str) -> None:
        self.record_path.write_text(json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")

    def release(self) -> None:
        if self.handle is not None:
            try:
                self.record_path.unlink(missing_ok=True)
                if sys.platform == "win32":
                    import msvcrt

                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self.handle.close()
            self.handle = None


def bind_server(workspace: Path, port: int, open_folders: bool = False):
    """The app server on ``port``, or on the next free port when it is occupied."""

    from .server import make_server

    candidates = [port + offset for offset in range(PORT_TRIES)] if port else []
    errors = []
    for candidate in [*candidates, 0]:
        try:
            return make_server(workspace, "127.0.0.1", candidate, open_folders=open_folders)
        except OSError as error:
            errors.append(f"{candidate}: {error.strerror or error}")
    raise StartupError("No local port could be opened for the app server:\n" + "\n".join(errors[-3:]))


class Runner:
    """Starts and stops the server; shared by the window and headless mode."""

    def __init__(self, workspace: Path, port: int, open_folders: bool = False) -> None:
        self.workspace, self.port, self.open_folders = workspace, port, open_folders
        self.server: Any = None
        self.thread: threading.Thread | None = None
        self.url = ""
        self.abandoned: list[str] = []

    def start(self) -> str:
        self.server = bind_server(self.workspace, self.port, self.open_folders)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        self.thread = threading.Thread(target=self.server.serve_forever, name="layerforge-http", daemon=True)
        self.thread.start()
        with urllib.request.urlopen(self.url + "api/info", timeout=30) as response:  # the server really answers
            if response.status != 200:
                raise StartupError(f"The app server at {self.url} answered {response.status}")
        print(f"[launcher] serving {self.url} workspace {self.workspace}", flush=True)
        return self.url

    def busy(self) -> list[str]:
        if self.server is None:
            return []
        return [f"{job.kind} ({job.project})" for job in self.server.app.jobs.running()]

    def stop(self) -> None:
        if self.server is not None:
            self.abandoned = self.busy()
            if self.abandoned:
                print(f"[launcher] stopping while running: {self.abandoned}", flush=True)
            lifecycle = self.server.app.lifecycle
            lifecycle.close_now(lifecycle.reason or "stopped")  # later requests are refused (503), not cut off
            self.server.shutdown()  # stop accepting requests; the job thread ends with the process
            if not lifecycle.wait_idle(30):  # a save or download being answered finishes first
                print("[launcher] requests still being answered after 30 s; stopping anyway", flush=True)
            self.server.server_close()
            self.server = None
            print("[launcher] server stopped", flush=True)


def run_background(runner: Runner, lock: InstanceLock, open_browser: bool) -> int:
    """No window: start the server, open the browser, serve until the last LayerForge tab is closed (and
    nothing is running) or Exit on the page.

    A startup failure raises (``main`` shows it in a message box).  Launching again while it runs only
    reopens the browser on it (``InstanceLock``)."""

    stop = threading.Event()
    url = runner.start()
    app = runner.server.app
    app.on_stop = stop.set
    lock.publish(url)
    if open_browser:
        webbrowser.open(url)
    try:
        while not stop.wait(1.0):
            reason = app.lifecycle.try_close(jobs_running=bool(app.jobs.running()))
            if reason:
                print(f"[launcher] {reason}; stopping", flush=True)
                break
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop()
    return 0


def wait_for_stdin_close() -> None:
    """Return when the controlling process closes stdin, without ever leaving a read pending.

    A blocking read on a Windows pipe stalls every other thread that touches the standard
    handles until it completes; native libraries do that while their DLL loads (seen with
    SciPy's Fortran extension during a proposal), so the server froze mid-job.
    """

    if sys.stdin is not None and sys.platform != "win32":
        sys.stdin.read()
        return
    handle = None
    if sys.stdin is not None:
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetFileType.argtypes = [wintypes.HANDLE]
        kernel32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        if kernel32.GetFileType(handle) != 3:  # FILE_TYPE_PIPE; a console or file: run until interrupted
            handle = None
    available = ctypes.c_ulong()
    while True:
        if handle is not None:
            if not kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
                return  # the writer closed the pipe
            if available.value:
                os.read(sys.stdin.fileno(), available.value)  # input is ignored; this read never waits
        time.sleep(0.5)


def run_headless(runner: Runner, lock: InstanceLock) -> int:
    url = runner.start()
    lock.publish(url)
    print(f"LAYERFORGE_READY {url}", flush=True)
    try:
        wait_for_stdin_close()
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop()
    return 0


def _message(error: BaseException) -> str:
    return str(error) if isinstance(error, StartupError) else f"{type(error).__name__}: {error}"


def _show_error(text: str, headless: bool) -> None:
    print(f"[launcher] ERROR {text}", file=sys.stderr, flush=True)
    if not headless:
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(APP_NAME, text)
            root.destroy()
        except Exception:  # noqa: BLE001 - no display: the log has the message
            pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog=APP_NAME, description="Start the LayerForge app and open it in the browser.")
    result.add_argument("--workspace", type=Path, default=None,
                        help=r"projects folder (default: Documents\LayerForge)")
    result.add_argument("--port", type=int, default=8765, help="preferred local port; the next free one is used if taken")
    result.add_argument("--no-browser", action="store_true", help="do not open the browser")
    result.add_argument("--headless", action="store_true", help="no window; serve until stdin closes (automated checks)")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    log = local_data() / "logs" / "layerforge.log"
    lock: InstanceLock | None = None
    runner: Runner | None = None
    try:
        log = setup_log(args.headless)
        workspace = check_workspace(args.workspace or documents_folder() / APP_NAME)
        lock = InstanceLock(workspace)
        if not lock.acquire():
            url = lock.running_url(announce=not args.headless and not args.no_browser)
            if url is None and not args.headless:
                # The running copy may be closing right now (its last tab was just closed): wait for it.
                for _ in range(40):
                    time.sleep(0.5)
                    if lock.acquire():
                        break
                    url = lock.running_url(announce=not args.no_browser)
                    if url:
                        break
            if lock.handle is not None:
                print("[launcher] the previous copy has stopped; starting", flush=True)
            elif args.headless:
                _show_error(f"already running for {workspace} at {url}", True)
                return EXIT_ALREADY_RUNNING
            elif url:
                print(f"[launcher] already running at {url}", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                return 0
            else:
                raise StartupError(f"LayerForge is already running for {workspace} but does not answer yet. "
                                   "Wait a moment and try again.")
        runner = Runner(workspace, args.port, open_folders=not args.headless)
        if args.headless:
            return run_headless(runner, lock)
        return run_background(runner, lock, not args.no_browser)
    except BaseException as error:  # noqa: BLE001 - every startup failure is shown, not swallowed
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        traceback.print_exc()
        _show_error(f"LayerForge could not start.\n\n{_message(error)}\n\nLog: {log}", args.headless)
        return EXIT_STARTUP_ERROR
    finally:
        if lock is not None:
            lock.release()
        print("[launcher] exit", flush=True)
        if runner is not None and runner.abandoned:
            # A job thread is still inside the core (possibly native code); ending the
            # interpreter normally can crash while it holds the output lock.  Everything
            # the app owns is already closed: the server, the lock and the log.
            os._exit(0)
