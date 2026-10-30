"""Local HTTP server for the L4 application (Python standard library only).

``python -m layerforge_l1 app`` serves the single-page UI in ``static/`` and a
small JSON API on 127.0.0.1.  Long operations are submitted to ``JobRunner``
and return a job id at once; the page polls ``/api/jobs/<id>`` for progress.

    GET  /api/info                              version, edit ops, presets, physical status
    GET  /api/projects                          project list
    POST /api/projects?filename=&scale=&...     body = image bytes -> project + L1 proposal job
    GET  /api/projects/<p>                      project state (+ active or latest job)
    POST /api/projects/<p>/propose              re-run the L1 proposal (before a review exists)
    POST /api/projects/<p>/review               {"candidate"} -> start the L2 review
    POST /api/projects/<p>/edit                 {"op": {...}} -> one L2 edit, saved and validated
    POST /api/projects/<p>/undo                 {} or {"keep": n} -> undo last / revert to n edits
    POST /api/projects/<p>/verify               replay the edit log from the base
    POST /api/projects/<p>/printability         {"process"} -> L3 estimate of the current draft
    POST /api/projects/<p>/kits                 {"process", "bambu_printer"?} -> L3 print kit
    POST /api/projects/<p>/kits/<k>/verify      check the kit's checksums
    POST /api/projects/<p>/kits/<k>/delete      delete the kit's folder and record entry (not while a job runs)
    POST /api/projects/<p>/kits/<k>/open        show the kit folder in the file manager (desktop app only)
    GET  /api/projects/<p>/kits/<k>/zip         download the kit
    GET  /api/jobs/<id>                         job status, progress events, result or error
    GET  /files/<p>/<path>                      a file inside a project (previews, kit files)
"""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__
from ..core.bambu3mf import environment as bambu_environment
from ..core.editing import EDIT_OPS
from .jobs import JobConflict, JobRunner, describe_error
from .workspace import (MAX_UPLOAD_BYTES, PHYSICAL_STATUS, SCALES, AppError, Workspace, check_detail_decisions,
                        process_presets)

STATIC = Path(__file__).resolve().parent / "static"
MAX_JSON_BYTES = 1024 * 1024
TYPES = {".stl": "model/stl", ".step": "model/step", ".md": "text/markdown; charset=utf-8",
         ".json": "application/json", ".sha256": "text/plain; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8"}


def open_folder(folder: Path) -> None:
    """Show a folder in the system file manager (desktop sessions only)."""

    if sys.platform == "win32":
        os.startfile(folder)  # type: ignore[attr-defined]  # noqa: S606 - a verified kit directory
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


class App:
    def __init__(self, workspace: Path, open_folders: bool = False) -> None:
        self.workspace = Workspace(workspace)
        self.jobs = JobRunner()
        # Only an interactive desktop session may open file-manager windows (not headless/tests).
        self.open_folders = open_folders

    def submit(self, kind: str, project: str, work: Callable[[Callable[..., None]], Any]) -> dict[str, Any]:
        self.workspace.path(project)  # unknown project -> AppError before queueing
        return self.jobs.submit(kind, project, work).to_json()


class Handler(BaseHTTPRequestHandler):
    server_version = f"LayerForge/{__version__}"

    def __init__(self, *args: Any, app: App, **kwargs: Any) -> None:
        self.app = app
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature of the base class
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            sys.stderr.write("[app] " + format % args + "\n")

    # -- responses ---------------------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, value: Any, status: int = 200) -> None:
        self._send(status, json.dumps(value).encode("utf-8"), "application/json")

    def _error(self, status: int, message: str, **extra: Any) -> None:
        self._json({"error": {"message": message, **extra}}, status)

    def _file(self, path: Path) -> None:
        kind = TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), kind)

    def _body(self, limit: int = MAX_JSON_BYTES) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise AppError(f"request body is larger than {limit // (1024 * 1024)} MB")
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> dict[str, Any]:
        raw = self._body()
        try:
            value = json.loads(raw or b"{}")
        except json.JSONDecodeError as error:
            raise AppError(f"request body is not JSON: {error}") from None
        if not isinstance(value, dict):
            raise AppError("request body must be a JSON object")
        return value

    # -- dispatch ------------------------------------------------------------------------
    def do_HEAD(self) -> None:  # noqa: N802 - http.server naming
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(self._get)

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
            self._error(403, "cross-origin requests are not accepted")
            return
        self._dispatch(self._post)

    def _dispatch(self, route: Callable[[list[str], dict[str, list[str]]], None]) -> None:
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.split("/") if p]
        try:
            route(parts, parse_qs(url.query))
        except JobConflict as error:
            self._error(409, str(error))
        except AppError as error:
            self._error(404 if str(error).startswith("no ") else 400, str(error))
        except Exception as error:  # noqa: BLE001 - report instead of dropping the connection
            import traceback

            traceback.print_exc()
            self._error(500, describe_error(error)["message"], hint=describe_error(error).get("hint"))

    def _get(self, parts: list[str], query: dict[str, list[str]]) -> None:
        ws = self.app.workspace
        if not parts:
            self._file(STATIC / "index.html")
        elif parts[0] == "static" and len(parts) == 2 and (STATIC / parts[1]).is_file():
            self._file(STATIC / parts[1])
        elif parts[0] == "files" and len(parts) >= 3:
            self._file(ws.file(parts[1], "/".join(parts[2:])))
        elif parts == ["api", "info"]:
            self._json({"version": __version__, "workspace": ws.root.as_posix(), "edit_ops": list(EDIT_OPS),
                        "scales": list(SCALES), "physical": PHYSICAL_STATUS, "process_presets": process_presets(),
                        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024), "bambu": bambu_environment(),
                        "can_open_folders": self.app.open_folders})
        elif parts == ["api", "projects"]:
            self._json({"projects": ws.projects()})
        elif len(parts) == 3 and parts[:2] == ["api", "projects"]:
            state = ws.state(parts[2])
            job = self.app.jobs.active(parts[2]) or self.app.jobs.latest(parts[2])
            state["job"] = job.to_json() if job else None
            self._json(state)
        elif len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3] == "kits" and parts[5] == "zip":
            data = ws.kit_zip(parts[2], parts[4])
            self._send(200, data, "application/zip",
                       {"Content-Disposition": f'attachment; filename="{parts[2]}_{parts[4]}.zip"'})
        elif len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            job = self.app.jobs.get(parts[2])
            if job is None:
                self._error(404, f"no job {parts[2]} (jobs are kept until the app restarts)")
            else:
                self._json(job.to_json())
        else:
            self._error(404, f"no such page: {self.path}")

    def _post(self, parts: list[str], query: dict[str, list[str]]) -> None:
        ws, app = self.app.workspace, self.app
        if parts == ["api", "projects"]:
            settings = {k: v[0] for k, v in query.items()}
            filename = settings.pop("filename", "")
            data = self._body(MAX_UPLOAD_BYTES)
            name = ws.create(filename, data, settings)
            job = app.submit("propose", name, partial(ws.propose, name))
            self._json({"project": name, "job": job}, 201)
            return
        if len(parts) < 4 or parts[:2] != ["api", "projects"]:
            self._error(404, f"no such action: {self.path}")
            return
        name, action = parts[2], parts[3]
        body = self._json_body()
        if action == "delete" and len(parts) == 4:
            active = app.jobs.active(name)
            if active is not None:  # a running job writes into the folder
                raise JobConflict(f"'{active.kind}' is still {active.status} for this project; delete it when it has finished")
            self._json(ws.delete_project(name))
            return
        if action == "propose" and len(parts) == 4:
            decisions = body.get("detail_decisions")
            if decisions is not None:
                decisions = check_detail_decisions(decisions)  # a bad request fails now, not in the job
            job = app.submit("propose", name, partial(ws.propose, name, decisions=decisions))
        elif action == "resize" and len(parts) == 5 and parts[4] == "analyze":
            ws.resize_subject(name)  # something to assess; checked now, not in the job
            job = app.submit("resize_analysis", name, partial(ws.resize_analysis, name, body.get("process"),
                                                              body.get("bambu_printer")))
        elif action == "resize" and len(parts) == 5 and parts[4] == "search":
            _, _, analysis = ws.current_analysis(name)
            if not analysis.get("trial_search", {}).get("available"):
                raise AppError("trial planning is only offered for a failed proposal whose failure widths cannot predict")
            job = app.submit("resize_search", name, partial(ws.resize_search, name))
        elif action == "resize" and len(parts) == 5 and parts[4] == "preview":
            _, _, analysis = ws.current_analysis(name)
            size = ws.check_size(body.get("size_mm"), analysis)
            job = app.submit("resize_preview", name, partial(ws.resize_preview, name, size))
        elif action == "resize" and len(parts) == 5 and parts[4] == "retry":
            args = (body.get("preview_id"), body.get("approve_size"), body.get("approve_changes"))
            ws.check_resize_retry(name, *args)  # stale preview or missing approval: refused now
            job = app.submit("resize_retry", name, partial(ws.resize_retry, name, *args))
        elif action == "upscale" and len(parts) == 5 and parts[4] == "preview":
            factor = ws.check_factor(body.get("factor"))
            ws.failed_proposal(name)  # only for a failed proposal; checked now, not in the job
            job = app.submit("upscale_preview", name, partial(ws.upscale_preview, name, factor))
        elif action == "upscale" and len(parts) == 5 and parts[4] == "retry":
            args = (body.get("factor"), body.get("preview_id"), body.get("approve_changes"))
            ws.check_upscale_retry(name, *args)  # stale preview, missing approval or memory: refused now
            job = app.submit("upscale_retry", name, partial(ws.upscale_retry, name, *args))
        elif action == "review" and len(parts) == 4:
            job = app.submit("start_review", name, partial(ws.start_review, name, str(body.get("candidate", ""))))
        elif action == "edit" and len(parts) == 4:
            job = app.submit("edit", name, partial(ws.apply_edit, name, ws.check_op(body.get("op"))))
        elif action == "undo" and len(parts) == 4:
            job = app.submit("undo", name, partial(ws.undo, name, body.get("keep")))
        elif action == "verify" and len(parts) == 4:
            job = app.submit("verify", name, partial(ws.verify, name))
        elif action == "printability" and len(parts) == 4:
            job = app.submit("printability", name, partial(ws.printability, name, body.get("process")))
        elif action == "kits" and len(parts) == 4:
            job = app.submit("kit", name, partial(ws.build_kit, name, body.get("process"), bambu_printer=body.get("bambu_printer")))
        elif action == "kits" and len(parts) == 6 and parts[5] == "verify":
            self._json(ws.verify_kit(name, parts[4]))
            return
        elif action == "kits" and len(parts) == 6 and parts[5] == "delete":
            active = app.jobs.active(name)
            if active is not None:  # a running kit job saves the kit list when it finishes
                raise JobConflict(f"'{active.kind}' is still {active.status} for this project; delete the kit when it has finished")
            self._json(ws.delete_kit(name, parts[4]))
            return
        elif action == "kits" and len(parts) == 6 and parts[5] == "open":
            if not app.open_folders:
                raise AppError("opening folders is only available in the desktop app")
            folder = ws.kit_folder(name, parts[4])
            open_folder(folder)
            self._json({"kit": parts[4], "opened": folder.as_posix()})
            return
        else:
            self._error(404, f"no such action: {self.path}")
            return
        self._json({"job": job}, 202)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, SO_REUSEADDR lets a second server bind a port that is already in use (requests
    # then reach either process); claim the port exclusively so an occupied port fails to bind.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(workspace: Path, host: str = "127.0.0.1", port: int = 8765,
                open_folders: bool = False) -> ThreadingHTTPServer:
    app = App(workspace, open_folders)
    server = _Server((host, port), partial(Handler, app=app))
    server.app = app  # type: ignore[attr-defined]
    return server


def serve(workspace: Path, host: str, port: int, open_browser: bool) -> int:
    server = make_server(workspace, host, port, open_folders=open_browser and host in ("127.0.0.1", "localhost"))
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"LayerForge app: {url}  (workspace {Path(workspace).resolve().as_posix()}; Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
