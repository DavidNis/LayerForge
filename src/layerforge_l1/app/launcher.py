"""Desktop launcher for the L4 application (the packaged ``LayerForge.exe``).

Double-clicking the executable opens a small control window, starts the same
local server ``python -m layerforge_l1 app`` runs, and opens the UI in the
default browser.  The window shows the address and the projects folder; closing
it (or "Quit") stops the server cleanly.

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

    def running_url(self) -> str | None:
        try:
            url = json.loads(self.record_path.read_text(encoding="utf-8"))["url"]
            with urllib.request.urlopen(url + "api/info", timeout=3) as response:
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
            self.server.shutdown()  # stop accepting requests; the job thread ends with the process
            self.server.server_close()
            self.server = None
            print("[launcher] server stopped", flush=True)


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - the user's own projects folder
    else:
        webbrowser.open(path.as_uri())


def run_window(runner: Runner, lock: InstanceLock, log: Path, open_browser: bool) -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title(APP_NAME)
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=16)
    frame.grid()
    ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
    status = tk.StringVar(value="Starting the local server…")
    ttk.Label(frame, textvariable=status, wraplength=460).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 2))
    ttk.Label(frame, text=f"Projects folder: {runner.workspace}", wraplength=460,
              foreground="#555").grid(row=2, column=0, columnspan=3, sticky="w")
    ttk.Label(frame, text="Closing this window stops LayerForge.", foreground="#555").grid(
        row=3, column=0, columnspan=3, sticky="w", pady=(0, 10))
    open_button = ttk.Button(frame, text="Open LayerForge", state="disabled", command=lambda: webbrowser.open(runner.url))
    open_button.grid(row=4, column=0, sticky="w")
    ttk.Button(frame, text="Projects folder", command=lambda: open_folder(runner.workspace)).grid(row=4, column=1, padx=8)
    outcome: dict[str, Any] = {}

    def quit_app() -> None:
        running = runner.busy()
        if running and not messagebox.askyesno(
                APP_NAME, "LayerForge is still working on:\n  " + "\n  ".join(running)
                + "\n\nQuitting stops it; that step's result is not saved (your project and earlier "
                  "steps are kept). Quit anyway?", icon="warning"):
            return
        status.set("Stopping…")
        root.update_idletasks()
        root.destroy()

    ttk.Button(frame, text="Quit", command=quit_app).grid(row=4, column=2, sticky="e")
    root.protocol("WM_DELETE_WINDOW", quit_app)

    def start_in_background() -> None:
        try:
            outcome["url"] = runner.start()
        except BaseException as error:  # noqa: BLE001 - reported in the window
            traceback.print_exc()
            outcome["error"] = error

    def poll() -> None:
        if "url" in outcome:
            lock.publish(outcome["url"])
            status.set(f"Running at {outcome['url']}")
            open_button.configure(state="normal")
            if open_browser:
                webbrowser.open(outcome["url"])
        elif "error" in outcome:
            messagebox.showerror(APP_NAME, f"LayerForge could not start.\n\n{_message(outcome['error'])}\n\nLog: {log}")
            outcome["failed"] = True
            root.destroy()
        else:
            root.after(150, poll)

    threading.Thread(target=start_in_background, name="layerforge-start", daemon=True).start()
    root.after(150, poll)
    root.mainloop()
    runner.stop()
    return EXIT_STARTUP_ERROR if outcome.get("failed") else 0


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
            url = lock.running_url()
            if args.headless:
                _show_error(f"already running for {workspace} at {url}", True)
                return EXIT_ALREADY_RUNNING
            if url:
                print(f"[launcher] already running at {url}", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                return 0
            raise StartupError(f"LayerForge is already running for {workspace} but does not answer yet. "
                               "Wait a moment and try again, or close the other LayerForge window.")
        runner = Runner(workspace, args.port, open_folders=not args.headless)
        if args.headless:
            return run_headless(runner, lock)
        return run_window(runner, lock, log, not args.no_browser)
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
