"""L4 application: the complete workflow through the real HTTP server and a real browser.

Every test starts ``make_server`` (the same server ``python -m layerforge_l1 app``
runs) on a free port with a temporary workspace and talks to it over HTTP; no
handler or core function is called directly.  The project-owned sailboat fixture
(``fixtures/l3_print_kit``) is planned at scale 1 for speed.

``test_browser_workflow`` additionally drives the UI in headless Edge/Chrome via
``tests/e2e/drive_app.mjs`` (Node >= 22, no npm packages); it is skipped, with the
reason, only when Node or a Chromium browser is not installed.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from layerforge_l1.app import make_server
from resource_guard import PLENTY, fixed_memory, require_free_memory, require_memory_for_planning

ROOT = Path(__file__).resolve().parents[1]
SAILBOAT = ROOT / "fixtures" / "l3_print_kit" / "sailboat_512.png"
SLOT = {"type": "bar", "from_mm": [40.0, 40.0], "to_mm": [100.0, 40.0], "width_mm": 0.6}  # in the front sea sheet
SOLID_SEA_MM = [60.0, 40.0]
BROWSERS = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
)


class Client:
    def __init__(self, base: str) -> None:
        self.base = base

    def call(self, method: str, path: str, body=None, raw: bytes | None = None, headers=None):
        data = raw if raw is not None else None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
                kind = response.headers.get("Content-Type", "")
                return response.status, json.loads(payload) if kind.startswith("application/json") else payload
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def ok(self, method: str, path: str, body=None, raw=None, expect=(200, 201, 202)):
        status, data = self.call(method, path, body, raw)
        assert status in expect, (status, data)
        return data

    def wait(self, job: dict, timeout: float = 600.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            current = self.ok("GET", f"/api/jobs/{job['id']}")
            if current["status"] in ("done", "failed", "cancelled"):
                return current
            time.sleep(0.3)
        raise AssertionError(f"job {job} did not finish in {timeout} s")

    def run(self, project: str, action: str, body=None, expect: str = "done") -> dict:
        job = self.ok("POST", f"/api/projects/{project}/{action}", body or {})["job"]
        result = self.wait(job)
        assert result["status"] == expect, result["error"]
        return result

    def state(self, project: str) -> dict:
        return self.ok("GET", f"/api/projects/{project}")


def start(workspace: Path):
    server = make_server(workspace, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, Client(f"http://127.0.0.1:{server.server_address[1]}")


@pytest.fixture()
def app(tmp_path):
    server, client = start(tmp_path / "workspace")
    yield client, tmp_path / "workspace"
    server.shutdown()
    server.server_close()


def _propose(client: Client) -> tuple[str, dict]:
    created = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1", raw=SAILBOAT.read_bytes())
    name = created["project"]
    # A second job for the same project is refused while the proposal runs.
    status, conflict = client.call("POST", f"/api/projects/{name}/review", {"candidate": "beam_area"})
    assert status == 409 and "still" in conflict["error"]["message"]
    # The server answers while the worker plans (no frozen UI).
    slowest = 0.0
    while True:
        started = time.time()
        state = client.state(name)
        slowest = max(slowest, time.time() - started)
        if state["job"]["status"] in ("done", "failed"):
            break
        time.sleep(0.3)
    assert state["job"]["status"] == "done", state["job"]["error"]
    assert slowest < 10.0
    assert [e["stage"] for e in state["job"]["events"]][:2] == ["STARTED", "L1_INTAKE_DONE"]
    return name, state


def test_info_static_and_request_errors(app):
    client, workspace = app
    info = client.ok("GET", "/api/info")
    assert info["physical"]["status"] == "NOT_VERIFIED"
    assert "rim" in info["edit_ops"] and info["scales"] == [1, 2, 4]
    assert info["process_presets"][0]["source"].startswith("ASSUMPTION")
    assert any(p["file"] == "generic_fdm_0.4mm.json" for p in info["process_presets"])
    page = client.ok("GET", "/")
    assert b"/static/app.js" in page
    assert b"NOT VERIFIED" in client.ok("GET", "/static/app.js")

    status, error = client.call("POST", "/api/projects?filename=notes.txt", raw=b"hello")
    assert status == 400 and "PNG or JPEG" in error["error"]["message"]
    status, error = client.call("POST", "/api/projects?filename=broken.png", raw=b"not an image")
    assert status == 400 and "not a readable image" in error["error"]["message"]
    status, error = client.call("POST", "/api/projects?filename=s.png&scale=3", raw=SAILBOAT.read_bytes())
    assert status == 400 and "scale" in error["error"]["message"]
    assert client.call("GET", "/api/projects/missing")[0] == 404
    status, _ = client.call("POST", "/api/projects/missing/edit", {"op": {"op": "undo"}})
    assert status in (400, 404)
    status, error = client.call("POST", "/api/projects", raw=b"x", headers={"Origin": "http://evil.example"})
    assert status == 403
    assert client.ok("GET", "/api/projects")["projects"] == []
    assert not any(workspace.iterdir())


def test_api_workflow_import_review_edit_reload_export(app, tmp_path):
    client, workspace = app
    name, state = _propose(client)
    proposal = state["proposal"]
    assert proposal["status"] == "PROPOSAL_READY_FOR_REVIEW"
    # Intake detail decisions are checked before any job runs.
    for bad in ({"color_0001_0002": {"action": "drop"}}, {"../x": {"action": "remove"}},
                {"removed_0001_0002": {"action": "adjust", "color": "red"}}, ["preserve"]):
        status, error = client.call("POST", f"/api/projects/{name}/propose", {"detail_decisions": bad})
        assert status == 400, (bad, status, error)
    assert isinstance(proposal["details"], list)
    assert state["physical"]["status"] == "NOT_VERIFIED"
    assert client.ok("GET", proposal["images"]["assembled_front"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.call("GET", f"/files/{name}/../../etc/passwd")[0] == 404
    assert client.call("GET", f"/files/{name}/..%2F..%2Fproject.json")[0] in (400, 404)

    # Review the selected candidate (as planned: cutout widening off; tests/test_widening.py covers it).
    status, error = client.call("POST", f"/api/projects/{name}/review", {"candidate": proposal["selected"], "widen_openings": "yes"})
    assert status == 400 and "true or false" in error["error"]["message"]
    client.run(name, "review", {"candidate": proposal["selected"], "widen_openings": False})
    state = client.state(name)
    draft = state["draft"]
    assert draft["status"]["status"] == "REVIEWED_VALID" and draft["edits"] == []
    sheets = draft["sheets"]
    front, back = sheets[-1]["id"], sheets[0]["id"]
    assert all(client.ok("GET", s["image"])[:4] == b"\x89PNG" for s in sheets)

    # Request errors are immediate; edit errors come back from the job with the core's message.
    status, error = client.call("POST", f"/api/projects/{name}/edit", {"op": {"op": "explode"}})
    assert status == 400 and "one of" in error["error"]["message"]
    status, error = client.call("POST", f"/api/projects/{name}/edit",
                                {"op": {"op": "add", "sheet": front, "mask": "C:/x.png"}})
    assert status == 400 and "command line" in error["error"]["message"]
    failed = client.run(name, "edit", {"op": {"op": "fill_hole", "sheet": front, "at_mm": SOLID_SEA_MM}}, "failed")
    assert failed["error"]["type"] == "EditError" and "no cutout to fill" in failed["error"]["message"]
    assert failed["error"]["expected"] is True
    assert client.state(name)["draft"]["edits"] == []

    # A visible 0.6 mm slot: saved, but DRAFT_INVALID until the change is accepted.
    client.run(name, "edit", {"op": {"op": "subtract", "sheet": front, "shape": SLOT}})
    draft = client.state(name)["draft"]
    assert draft["status"]["status"] == "DRAFT_INVALID"
    assert draft["last_change"]["change"]["kind"] == "visible_artwork"
    assert draft["last_change"]["op"]["precision"] == "vector"
    assert any("reconstruction differs" in f for f in draft["validation"]["failures"])
    assert draft["validation"]["details_recomputed"] is True
    assert "changes_vs_previous" in draft["previews"]["images"]
    refused = client.run(name, "kits", {}, "failed")
    assert refused["error"]["type"] == "PrintKitError" and "not valid" in refused["error"]["message"]
    assert not (workspace / name / "kits").exists() or not any((workspace / name / "kits").iterdir())

    client.run(name, "edit", {"op": {"op": "accept_artwork"}})
    client.run(name, "edit", {"op": {"op": "rim", "sheet": back, "enabled": True, "width_mm": 6.0, "total_height_mm": 2.4}})
    draft = client.state(name)["draft"]
    assert draft["status"]["status"] == "REVIEWED_VALID"
    assert draft["last_change"]["change"]["kind"] == "physical_parameters"
    assert draft["validation"]["details_recomputed"] is False
    assert draft["sheets"][0]["rim"] == {"width_mm": 6.0, "total_height_mm": 2.4}

    # Undo, revert and re-apply; then verify the saved project replays exactly.
    client.run(name, "undo", {})
    assert client.state(name)["draft"]["sheets"][0]["rim"] is None
    client.run(name, "undo", {"keep": 0})
    assert client.state(name)["draft"]["edits"] == []
    client.run(name, "edit", {"op": {"op": "subtract", "sheet": front, "shape": SLOT}})
    client.run(name, "edit", {"op": {"op": "accept_artwork"}})
    client.run(name, "edit", {"op": {"op": "rim", "sheet": back, "enabled": True, "width_mm": 6.0, "total_height_mm": 2.4}})
    verify = client.run(name, "verify")["result"]
    assert verify["replay_identical"] is True and verify["edits"] == 3
    before_restart = client.state(name)

    # Restart: a new server on the same workspace reopens the saved project unchanged.
    server2, client2 = start(workspace)
    try:
        reopened = client2.state(name)
        assert reopened["draft"]["edits"] == before_restart["draft"]["edits"]
        assert reopened["draft"]["sheets"] == before_restart["draft"]["sheets"]
        assert reopened["draft"]["stale"] is False
        assert [p["name"] for p in client2.ok("GET", "/api/projects")["projects"]] == [name]

        # FDM estimate of the draft: the 0.6 mm slot is a RISK (generic 0.4 mm nozzle assumption).
        estimate = client2.run(name, "printability", {})["result"]
        assert estimate["status"] == "RISKS_REPORTED"
        found = client2.state(name)["printability"]
        slot = [f for f in found["features"] if f["sheet_id"] == front and f["severity"] == "RISK"]
        assert slot and abs(slot[0]["width_mm"] - 0.6) < 0.02
        assert found["physical"]["status"] == "NOT_VERIFIED" and found["stale"] is False

        # A custom process changes the verdict, and a bad one is rejected with its reason.
        custom = client2.run(name, "printability", {"process": {"min_opening_mm": 0.5, "name": "fine"}})["result"]
        assert custom["status"] == "NO_RISKS_FOUND"
        bad = client2.run(name, "printability", {"process": {"min_opening_mm": 0.2}}, "failed")
        assert "min_opening_mm" in bad["error"]["message"]

        # Print kit.
        built = client2.run(name, "kits", {})["result"]
        assert built["status"] == {"cad": "CAD_VALID", "fdm_estimate": "RISKS_REPORTED", "physical": "NOT_VERIFIED"}
        kit = client2.state(name)["kits"][0]
        assert kit["stale"] is False and kit["cad_problems"] == []
        assert kit["comparisons"]["approved_artwork_vs_reopened_cad_front_view"]["mismatched_px"] == 0
        assert kit["assembly"][0]["rim"] == {"width_mm": 6.0, "total_height_mm": 2.4}
        assert client2.ok("POST", f"/api/projects/{name}/kits/{kit['id']}/verify", {})["checksums_ok"] is True
        archive = zipfile.ZipFile(io.BytesIO(client2.ok("GET", kit["zip_url"])))
        files = archive.namelist()
        assert sum(f.endswith(".step") for f in files) == len(kit["assembly"]) == sum(f.endswith(".stl") for f in files)
        manifest = json.loads(archive.read(next(f for f in files if f.endswith("/print_kit.json"))))
        assert manifest["status"]["physical"] == "NOT_VERIFIED"

        # Editing after the kit marks it (and the estimate) out of date.
        client2.run(name, "edit", {"op": {"op": "thickness", "sheet": front, "mm": 2.0}})
        later = client2.state(name)
        assert later["kits"][0]["stale"] is True and later["printability"]["stale"] is True
        assert later["physical"]["status"] == "NOT_VERIFIED"
    finally:
        server2.shutdown()
        server2.server_close()


def _node_ok() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    return int(version.split(".")[0] or 0) >= 22


def test_browser_workflow(app):
    client, workspace = app
    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    # A second project for the drive to delete from the project list (also run against the packaged
    # exe, so it is created through the API; deleting failed projects is covered by the API test).
    failed = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&name=second_project", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(failed)["job"]["id"]})
    shots = ROOT / "artifacts" / "l4_app_v1" / "screens"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_app.mjs"), client.base + "/", str(browser),
                          str(SAILBOAT), str(shots)], capture_output=True, text=True, encoding="utf-8", timeout=1500)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    result = json.loads(lines[-1])
    assert result["ok"], result.get("error", "") + "\n" + run.stderr[-3000:]
    assert run.returncode == 0
    assert result["console_errors"] == []
    # Status cards appear once a review exists; on Export the current kit's own cards replace them (D-V3-051).
    assert result["physical_after_proposal"] == "", result["physical_after_proposal"]
    assert result["physical_at_end"] == "", result["physical_at_end"]
    assert "NOT VERIFIED" in result["kit"]["physical"]
    assert result["review_tools"] == 0  # Review Layers is read-only: no sheet-geometry tools
    assert "no cutout to fill" in result["error_banner"]  # earlier-style geometry edits: still checked by the edit API
    assert result["after_slot"]["validation"].split()[-1] == "Invalid"  # was DRAFT_INVALID (code kept in the tooltip)
    assert "Visible change" in result["after_slot"]["change"]
    assert result["after_accept"].split()[-1] == "Valid"  # was REVIEWED_VALID
    assert "rim 6" in result["rim_layer"] and "rim" not in result["after_undo_layer"]
    assert result["rim_all_default"] is True and result["rim_single_rows"] == 1
    assert result["rim_all_rows"] == result["layer_rows"] > 1
    assert result["history_after_reload"] == 3 and result["rim_after_reload"] is True
    assert "Risks reported" in result["printability"] and any("RISK" in row for row in result["findings"])
    assert "Risks reported (estimate)" in result["fdm_card"]
    assert "Files verified" in result["kit"]["cad"] and "NOT VERIFIED" in result["kit"]["physical"]  # was CAD_VALID
    assert result["kit"]["geometry_card"] == ""  # the kit card says it; no second status row on Export
    assert result["kit"]["zip"].endswith("/zip")
    state = result["state_before_delete"]  # the project is deleted at the end of the drive
    assert state["kits"][0]["status"]["physical"] == "NOT_VERIFIED"
    # Optional Bambu Studio project: off by default; the kit card reports it either way.
    assert result["bambu_option_default"] is False and result["bambu_printer_enabled"] is True
    bambu = state["kits"][0]["bambu"]
    assert bambu is not None and bambu["printer"] == "Bambu Lab P1S 0.4 nozzle"
    if bambu["status"] == "WRITTEN":
        assert result["kit"]["bambu_href"].endswith(".3mf") and len(bambu["plates"]) == len(state["kits"][0]["assembly"])
        assert bambu["validation"] != "FAILED", bambu
    else:
        assert "Not written" in result["kit"]["bambu"] and bambu["problems"]

    # Print Kit UX: one filled primary action, collapsed sections, visible warnings.
    ui = result["kit_ui"]
    assert ui["title"] == "Print Kit Ready"
    assert len(ui["primary_buttons"]) == 1 and ui["primary_buttons"][0].endswith("Download Print Kit (.zip)")
    assert "primary" in ui["download_class"]
    assert ui["guide_href"].endswith("PRINT_AND_STACK_GUIDE.md")
    assert ui["sections"] == ["Files & Exports", "Assembly & Layers", "Validation & Printability", "Technical details"]
    assert ui["sections_open_initially"] == [False] * 4 and ui["technical_visible_before"] is False
    assert ui["build_open"] is False  # with a kit, the creation form stays folded
    assert ui["new_button"] == {"text": "+ New Print Kit", "right": 0, "above": True}  # top right, above the kit
    assert result["kit_delete_form_opened"] is True  # "+ New Print Kit" opens the existing form
    assert result["kit_delete"]["form_closed_after_build"] is True
    assert "printability risk" in ui["warnings"]  # the 0.6 mm slot stays visible as a warning
    assert ui["zip_status"].startswith("200 application/zip") and ui["guide_status"].startswith("# Print and stack guide")
    assert ui["sections_open_after"] == [True] * 4 and ui["sections_open_after_render"] == [True] * 4
    assert result["kit"]["file_links"] == 2 * len(state["kits"][0]["assembly"])
    mobile = result["kit_mobile"]
    assert mobile["overflow"] <= 0 and mobile["download_width"] == mobile["actions_width"]

    # Delete from the UI: confirmation (cancel keeps it), then the folder and record entry are gone.
    deleted = result["kit_delete"]
    assert deleted["earlier_before"] == [deleted["first"]] and deleted["after_cancel"] is True
    assert "permanently removes its folder" in deleted["confirm_text"] and "not touched" in deleted["confirm_text"]
    assert f"Deleted {deleted['first']}" in deleted["notice"] and deleted["cards_after"] == [deleted["second"]]
    # (Kit deletion on disk is checked in test_kit_delete_is_confined_persistent_and_reports_failures.)

    # Delete the project from its screen: confirmation names it (cancel keeps it), then the folder,
    # the list entry and the selector entry are gone and the import screen is ready for a new image.
    gone = result["project_delete"]
    assert gone["name"] == state["name"] and gone["after_cancel"] is True
    assert f"Delete project “{state['name']}” permanently?" in gone["confirm_text"] and "cannot be undone" in gone["confirm_text"]
    assert "not touched" in gone["confirm_text"] and f"Deleted project {state['name']}" in gone["notice"]
    assert gone["hash"] in ("", "#/") and gone["import_ready"] is True
    assert state["name"] not in gone["listed_after"] and state["name"] not in gone["in_select_after"]
    if workspace is not None and Path(workspace).exists():
        assert not (Path(workspace) / state["name"]).exists()
    listed = result["project_list_delete"]
    assert listed["name"] == failed and f"Delete project “{failed}” permanently?" in listed["confirm_text"]
    assert f"Deleted project {failed}" in listed["notice"] and listed["listed_after"] == []
    assert client.ok("GET", "/api/projects")["projects"] == []
    if workspace is not None and Path(workspace).exists():
        assert not (Path(workspace) / failed).exists()


def _tree_hashes(folder: Path) -> dict[str, str]:
    import hashlib

    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def test_kit_delete_is_confined_persistent_and_reports_failures(app, tmp_path, monkeypatch):
    client, workspace = app
    name, state = _propose(client)
    client.run(name, "review", {"candidate": state["proposal"]["selected"]})
    first = client.run(name, "kits", {})["result"]["kit"]
    project = workspace / name
    kits = project / "kits"

    # Refused while a job runs (a kit build saves the kit list when it finishes).
    job = client.ok("POST", f"/api/projects/{name}/kits", {})["job"]
    status, error = client.call("POST", f"/api/projects/{name}/kits/{first}/delete", {})
    assert status == 409 and "still" in error["error"]["message"]
    second = client.wait(job)["result"]["kit"]
    assert [k["id"] for k in client.state(name)["kits"]] == [second, first]
    others = {p: _tree_hashes(project / p) for p in ("source", "proposal", "review", "draft")}
    second_files = _tree_hashes(kits / second)
    record_before = json.loads((project / "project.json").read_text(encoding="utf-8"))

    # Refused: other origins, ids that are not kit ids (no path parts), unknown kits.
    status, _ = client.call("POST", f"/api/projects/{name}/kits/{first}/delete", {}, headers={"Origin": "http://evil.example"})
    assert status == 403
    for bad in ("review", "..", "kit_1", "kit_20260101_000000/..", "proposal", "kit_20260101_000000%2F..%2F..%2Freview"):
        status, error = client.call("POST", f"/api/projects/{name}/kits/{bad}/delete", {})
        assert status in (400, 404), (bad, status)
    status, error = client.call("POST", f"/api/projects/{name}/kits/kit_20000101_000000/delete", {})
    assert status == 404 and "no kit" in error["error"]["message"]
    assert (kits / first / "print_kit.json").is_file() and _tree_hashes(project / "review") == others["review"]

    # A link inside the kit to an outside folder: removed as a link, the outside file survives.
    outside = tmp_path / "outside_user_data"
    outside.mkdir()
    (outside / "keep.txt").write_text("user data", encoding="utf-8")
    linked = False
    try:
        import _winapi

        _winapi.CreateJunction(str(outside), str(kits / first / "previews" / "linked_folder"))
        linked = True
    except (ImportError, OSError):
        try:
            (kits / first / "previews" / "linked_folder").symlink_to(outside, target_is_directory=True)
            linked = True
        except OSError:
            pass

    # A file held open by another program: partial delete, kit still listed, retry succeeds.
    held = open(kits / first / "PRINT_AND_STACK_GUIDE.md", "rb")  # noqa: SIM115
    try:
        partial = client.ok("POST", f"/api/projects/{name}/kits/{first}/delete", {})
    finally:
        held.close()
    if partial["status"] == "PARTIALLY_DELETED":  # Windows refuses to delete an open file
        assert partial["failed_count"] >= 1 and any("PRINT_AND_STACK_GUIDE.md" in f["path"] for f in partial["failed"])
        listed = client.state(name)["kits"]
        assert first in [k["id"] for k in listed]
        assert next(k for k in listed if k["id"] == first)["missing_files"]  # shown as incomplete
        done = client.ok("POST", f"/api/projects/{name}/kits/{first}/delete", {})
    else:
        done = partial
    assert done["status"] == "DELETED" and not (kits / first).exists()
    if linked:
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "user data"

    # Nothing else changed: other kit, source, proposal, review, draft; record keeps the other kit.
    assert _tree_hashes(kits / second) == second_files
    assert {p: _tree_hashes(project / p) for p in others} == others
    record = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert [k["id"] for k in record["kits"]] == [second]
    assert {k: v for k, v in record.items() if k != "kits"} == {k: v for k, v in record_before.items() if k != "kits"}
    assert [k["id"] for k in client.state(name)["kits"]] == [second]

    # A kit folder removed outside the app: deleting it only drops the record entry.
    shutil.rmtree(kits / second)
    gone = client.ok("POST", f"/api/projects/{name}/kits/{second}/delete", {})
    assert gone["status"] == "DELETED" and "already missing" in gone["notes"][0]

    # Persistence: a restarted server on the same workspace does not list the deleted kits.
    server2, client2 = start(workspace)
    try:
        assert client2.state(name)["kits"] == []
        assert client2.state(name)["draft"]["status"]["status"] == "REVIEWED_VALID"
        assert client2.ok("GET", "/api/info")["can_open_folders"] is False
        status, error = client2.call("POST", f"/api/projects/{name}/kits/{second}/open", {})
        assert status == 400 and "desktop app" in error["error"]["message"]
    finally:
        server2.shutdown()
        server2.server_close()

    # Open Kit Folder (desktop sessions): opens exactly the verified kit folder.
    from layerforge_l1.app import server as server_module

    third = client.run(name, "kits", {})["result"]["kit"]
    opened = []
    monkeypatch.setattr(server_module, "open_folder", opened.append)
    desktop = make_server(workspace, port=0, open_folders=True)
    threading.Thread(target=desktop.serve_forever, daemon=True).start()
    try:
        client3 = Client(f"http://127.0.0.1:{desktop.server_address[1]}")
        assert client3.ok("GET", "/api/info")["can_open_folders"] is True
        assert client3.ok("POST", f"/api/projects/{name}/kits/{third}/open", {})["kit"] == third
        assert [p.resolve() for p in opened] == [(kits / third).resolve()]
        assert client3.call("POST", f"/api/projects/{name}/kits/{first}/open", {})[0] == 404
    finally:
        desktop.shutdown()
        desktop.server_close()


def test_jpeg_extensions_accepted_by_content(tmp_path):
    from PIL import Image

    from layerforge_l1.app import Workspace

    ws = Workspace(tmp_path / "workspace")
    jpeg, gif = io.BytesIO(), io.BytesIO()
    Image.new("RGB", (32, 32), (200, 40, 40)).save(jpeg, "JPEG")
    Image.new("RGB", (32, 32), (200, 40, 40)).save(gif, "GIF")
    name = ws.create("turtle_on_leaf.jfif", jpeg.getvalue(), {})
    assert (ws.root / name / "source" / "turtle_on_leaf.jfif").read_bytes() == jpeg.getvalue()
    with pytest.raises(Exception, match="GIF image"):
        ws.create("renamed.png", gif.getvalue(), {})


def test_upscale_and_retry_after_a_failed_proposal(app, monkeypatch):
    """Failed proposal -> preview at 2x -> approval -> retry in the same project.

    The failure at the import scale is simulated (planning raises at scale 1 only) so the real
    retry at scale 2 runs on the sailboat in seconds; every other step is the real server.
    """

    import layerforge_l1.core.propose as core_propose
    from layerforge_l1.core.model import file_sha256
    from layerforge_l1.core.planning import PlanningError

    real = core_propose.plan_stack

    def fails_at_scale_1(target, config, name="stack"):
        if config.scale == 1:
            raise PlanningError("reveal closure is infeasible: some cells can never be supported (simulated)",
                                {"closure_feasible": False})
        return real(target, config, name)

    monkeypatch.setattr(core_propose, "plan_stack", fails_at_scale_1)
    # Pinned memory decision; the retry really plans at scale 2, so that needs real free memory.
    fixed_memory(monkeypatch, PLENTY)
    require_memory_for_planning(150.0, 2, "the real scale-2 retry")
    client, workspace = app
    name = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&outer_mm=150", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    state = client.state(name)
    source = workspace / name / state["record"]["source"]["file"]
    source_sha = file_sha256(source)
    assert state["proposal"]["status"] == "NEEDS_USER_DECISION" and state["upscale"]["available"]
    assert [o["factor"] for o in state["upscale"]["options"]] == [2, 4] and state["upscale"]["options"][0]["scale"] == 2

    project = f"/api/projects/{name}/upscale"
    assert client.call("POST", f"{project}/preview", {"factor": 3})[0] == 400
    assert client.call("POST", f"{project}/retry", {"factor": 2, "preview_id": "x"})[0] == 400  # no preview yet
    client.run(name, "upscale/preview", {"factor": 2})
    preview = client.state(name)["upscale"]["previews"]["2"]
    assert preview["current"] and preview["status"] == "READY_TO_RETRY" and preview["boundaries"]["pass"]
    assert client.ok("GET", preview["images"]["comparison"])[:4] == b"\x89PNG"
    assert client.call("POST", f"{project}/retry", {"factor": 2, "preview_id": "stale"})[0] == 400
    if preview["changes"]:
        status, error = client.call("POST", f"{project}/retry", {"factor": 2, "preview_id": preview["preview_id"]})
        assert status == 400 and "approve" in error["error"]["message"]
    # The proposal is unchanged until the retry finishes.
    assert client.state(name)["proposal"]["status"] == "NEEDS_USER_DECISION"
    client.run(name, "upscale/retry", {"factor": 2, "preview_id": preview["preview_id"], "approve_changes": True})

    state = client.state(name)
    assert state["proposal"]["status"] == "PROPOSAL_READY_FOR_REVIEW"
    assert state["proposal"]["planning_config"]["scale"] == 2 and state["proposal"]["planning_config"]["outer_sheet_mm"] == 150.0
    assert state["record"]["processing_scale"] == 2 and state["record"]["proposal_settings"]["scale"] == 1
    assert file_sha256(source) == source_sha  # the original image is untouched
    # The failed proposal was never accepted: it is discarded, no attempt accumulates.
    assert state["upscale"]["attempts"] == [] and not (workspace / name / "attempts").exists()
    assert not (workspace / name / "replaced.tmp").exists()
    assert not state["upscale"]["available"] and not (workspace / name / "upscale").exists()
    assert client.call("POST", f"{project}/preview", {"factor": 2})[0] == 400  # only after a failure
    # The retried proposal continues through the unchanged L2 workflow at the new resolution.
    client.run(name, "review", {"candidate": state["proposal"]["selected"]})
    assert client.state(name)["draft"]["status"]["status"] == "REVIEWED_VALID"


def test_upscale_is_refused_when_memory_is_insufficient(app, monkeypatch):
    import layerforge_l1.core.propose as core_propose
    import layerforge_l1.core.upscale as core_upscale
    from layerforge_l1.core.planning import PlanningError

    def fails(target, config, name="stack"):
        raise PlanningError("simulated failure", {"closure_feasible": False})

    monkeypatch.setattr(core_propose, "plan_stack", fails)
    fixed_memory(monkeypatch, PLENTY)  # the preview passes on any machine; then memory drops
    require_free_memory(0.5, "the real 2x intake preview")
    client, _ = app
    name = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    client.run(name, "upscale/preview", {"factor": 2})
    preview = client.state(name)["upscale"]["previews"]["2"]
    monkeypatch.setattr(core_upscale, "available_memory_bytes", lambda: 64 * 2**20)
    assert not client.state(name)["upscale"]["options"][0]["fits"]
    status, error = client.call("POST", f"/api/projects/{name}/upscale/retry",
                                {"factor": 2, "preview_id": preview["preview_id"], "approve_changes": True})
    assert status == 400 and "not enough memory" in error["error"]["message"]
    assert client.state(name)["proposal"]["status"] == "NEEDS_USER_DECISION"


def test_physical_resize_after_printability_findings(app):
    """Sailboat on a small 60 mm sheet: findings -> smallest useful size -> preview -> approve -> retry."""

    from layerforge_l1.core.model import file_sha256

    client, workspace = app
    name = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&outer_mm=60", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    state = client.state(name)
    source = workspace / name / state["record"]["source"]["file"]
    sha = file_sha256(source)
    assert state["proposal"]["status"] == "PROPOSAL_READY_FOR_REVIEW" and state["resize"]["available"]
    assert not state["upscale"]["available"]  # pixel upscaling is only offered after a failed proposal
    project = f"/api/projects/{name}/resize"
    assert client.call("POST", f"{project}/preview", {"size_mm": 80})[0] == 400  # analyse first
    client.run(name, "resize/analyze", {"process": None, "bambu_printer": None})
    analysis = client.state(name)["resize"]["analysis"]
    assert analysis["current"] and analysis["measured"]["counts"]["RISK"] + analysis["measured"]["counts"]["UNSUPPORTED"] > 0
    rec = analysis["options"]["recommended"]
    assert rec and 60.0 < rec["sheet_mm"] <= analysis["limit"]["max_sheet_mm"]
    for bad in (60, 99, 1000, "big"):  # the current size, below 100 mm, beyond the printer, not a number
        assert client.call("POST", f"{project}/preview", {"size_mm": bad})[0] == 400, bad
    size = max(100.0, rec["sheet_mm"])
    client.run(name, "resize/preview", {"size_mm": size})
    preview = client.state(name)["resize"]["preview"]
    assert preview["current"] and preview["status"] == "READY_TO_RESIZE" and preview["boundaries"]["pass"]
    assert preview["dimensions_new"]["sheet_mm"] == [size] * 2 and preview["dimensions_now"]["sheet_mm"] == [60.0, 60.0]
    # Never applied without explicit approval of the size (and of visible changes, if any).
    status, error = client.call("POST", f"{project}/retry", {"preview_id": preview["preview_id"]})
    assert status == 400 and "approve changing the sheet" in error["error"]["message"]
    assert client.call("POST", f"{project}/retry", {"preview_id": "old", "approve_size": True, "approve_changes": True})[0] == 400
    assert client.state(name)["record"].get("sheet_outer_mm") is None
    client.run(name, "resize/retry", {"preview_id": preview["preview_id"], "approve_size": True, "approve_changes": True})

    state = client.state(name)
    assert state["proposal"]["planning_config"]["outer_sheet_mm"] == size
    assert state["record"]["sheet_outer_mm"] == size and state["record"]["proposal_settings"]["outer_mm"] == 60.0
    assert file_sha256(source) == sha
    check = state["resize"]["last_check"]
    assert check["from_mm"] == 60.0 and check["to_mm"] == size and "after" in check
    assert check["after"]["counts"]["UNSUPPORTED"] + check["after"]["counts"]["RISK"] < \
        check["before"]["counts"]["UNSUPPORTED"] + check["before"]["counts"]["RISK"]
    assert state["upscale"]["attempts"] == [] and not (workspace / name / "attempts").exists()  # nothing accepted, nothing kept
    # The new proposal continues through the unchanged L2 workflow.
    client.run(name, "review", {"candidate": state["proposal"]["selected"]})
    assert client.state(name)["draft"]["status"]["status"] == "REVIEWED_VALID"


def test_physical_resize_width_and_height_in_the_same_project(app):
    """Width x height through the one resize workflow, from an edited review of a switched candidate:
    200 x 200 -> 200 x 150 -> 150 x 250 -> 200 x 150 -> 200 x 200 -> 100 x 200 mm.  Each size is re-planned
    from the artwork (other proportions: the source fitted again, never stretched); the sheets are exactly
    the requested outer size; the current size is offered for editing, also after a restart; each side is
    at least 100 mm and the sheet must fit the printer as placed or turned; replaced plans are discarded (none
    was accepted), kits kept."""

    import numpy as np

    from layerforge_l1.core.cad import export_stack, revalidate_cad
    from layerforge_l1.core.model import file_sha256, load_stack
    from layerforge_l1.core.validation import validate_stack

    client, workspace = app
    name = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&outer_mm=200", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    project, base = workspace / name, f"/api/projects/{name}/resize"
    proposal = client.state(name)["proposal"]
    others = [c["name"] for c in proposal["candidates"] if c["status"] == "VALID" and c["name"] != proposal["selected"]]
    client.run(name, "review", {"candidate": proposal["selected"], "widen_openings": False})
    if others:  # switch candidates, then edit the switched-to review
        client.run(name, "review", {"candidate": others[0], "widen_openings": False})
    back = client.state(name)["draft"]["sheets"][0]["id"]
    client.run(name, "edit", {"op": {"op": "rim", "sheet": back, "enabled": True, "width_mm": 6.0, "total_height_mm": 2.4}})
    kit = client.run(name, "kits", {})["result"]["kit"]
    source = SAILBOAT.read_bytes()
    from PIL import Image
    import io

    src_w, src_h = Image.open(io.BytesIO(source)).size
    bed = {"process": {"bed_x_mm": 320.0, "bed_y_mm": 260.0}, "bambu_printer": None}

    def resize(width: float, height: float) -> dict:
        before = client.state(name)["resize"]["sheet_size_mm"]
        client.run(name, "resize/analyze", bed)
        analysis = client.state(name)["resize"]["analysis"]
        current = analysis["basis"].get("sheet_size_mm") or [analysis["basis"]["sheet_mm"]] * 2
        assert analysis["current"] and current == before  # what the width and height fields show
        for bad in ([99, height], [width, 99], [330, 200], [200, 330], before):  # minimum, printer, current
            assert client.call("POST", f"{base}/preview", {"width_mm": bad[0], "height_mm": bad[1]})[0] == 400, bad
        client.run(name, "resize/preview", {"width_mm": width, "height_mm": height})
        preview = client.state(name)["resize"]["preview"]
        assert preview["status"] == "READY_TO_RESIZE" and preview["current"], preview["boundaries"]
        assert preview["dimensions_new"]["sheet_mm"] == [width, height]
        refit = (height / width) != (before[1] / before[0])
        assert preview["refit"] == refit
        if refit:  # a resampled artwork is never planned without approving it
            status, error = client.call("POST", f"{base}/retry", {"preview_id": preview["preview_id"], "approve_size": True})
            assert status == 400 and "fitted into the new proportions" in error["error"]["message"]
        client.run(name, "resize/retry", {"preview_id": preview["preview_id"], "approve_size": True, "approve_changes": True})
        state = client.state(name)
        assert state["resize"]["sheet_size_mm"] == [width, height]
        cfg = state["proposal"]["planning_config"]
        assert [cfg["outer_sheet_mm"], cfg.get("outer_sheet_height_mm") or cfg["outer_sheet_mm"]] == [width, height]
        assert state["resize"]["last_check"]["to_mm"] == width and state["proposal"]["status"] == "PROPOSAL_READY_FOR_REVIEW"
        # Planned again at this size: masks on the new grid, the artwork not distorted, every check passes.
        stack = load_stack(project / "proposal" / "candidates" / state["proposal"]["selected"] / "stack")
        assert stack.grid.outer_mm == (width, height)
        assert validate_stack(stack)["status"] == "PASS"
        intake = json.loads((project / "proposal" / "proposal.json").read_text(encoding="utf-8"))["intake"]
        placed_w, placed_h = intake["placement"]["placed_px"]
        assert abs(placed_w / placed_h - src_w / src_h) <= (src_w / src_h) * (1 / placed_w + 1 / placed_h)
        return state

    state = resize(200.0, 150.0)
    assert [p.parent.name for p in workspace.glob("*/project.json")] == [name]  # the same project, no copy
    assert state["upscale"]["attempts"] == [] and not (project / "attempts").exists()
    assert [k["id"] for k in state["kits"]] == [kit] and (project / "kits" / kit).is_dir()
    resize(150.0, 250.0)
    # CAD at 150 x 250: every sheet exported at exactly that outer size and read back against its masks.
    stack = load_stack(project / "proposal" / "candidates" / client.state(name)["proposal"]["selected"] / "stack")
    manifest = export_stack(stack, project / "cadcheck", step=False)
    check, _ = revalidate_cad(stack, project / "cadcheck")
    assert check["pass"] and all(r["stl_outline_mm"] == [150.0, 250.0] for r in check["layers"])
    assert all(np.allclose(r["stl_bounds_mm"][1][:2], [150.0, 250.0], atol=1e-3) for r in manifest["layers"])
    resize(200.0, 150.0)
    resize(200.0, 200.0)
    state = resize(100.0, 200.0)
    assert state["upscale"]["attempts"] == [] and not (project / "attempts").exists()  # five re-plans, none kept
    assert [k["id"] for k in state["kits"]] == [kit]
    # A sheet that fits only turned 90 degrees is allowed (the user turns it in the slicer).
    client.run(name, "resize/analyze", bed)
    client.run(name, "resize/preview", {"width_mm": 150, "height_mm": 300})
    turned = client.state(name)["resize"]["preview"]
    fit = next(c for c in turned["boundaries"]["checks"] if c["check"] == "sheet fits the printer's usable area")
    assert turned["status"] == "READY_TO_RESIZE" and fit["pass"] and fit.get("turn_90_degrees_in_the_slicer")


def test_browser_rectangular_review_inspector_fill_and_reload(app, tmp_path):
    """A 150 x 250 mm (portrait, with an edge correction) review in a real browser (``drive_rect_review.mjs``).
    Sheet Cut and Fill go through the edit API (Review layers has no sheet-geometry tools); their masks are
    checked here at the rows and columns of the rectangular grid.  In the browser: the Issue inspector selects
    the cut's narrow opening from the list and by a real click on it, draws the highlight on it (not 1.4 mm
    beside it) and states its position in the artwork; the artwork editor's Fill tool recolors the region under
    a real click; Save; after a reload everything is still there."""

    import subprocess

    import numpy as np

    from layerforge_l1.core import artwork as art
    from layerforge_l1.core import review

    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    client, workspace = app
    name = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&outer_mm=200", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    client.run(name, "resize/analyze", {"process": None, "bambu_printer": None})
    client.run(name, "resize/preview", {"width_mm": 150, "height_mm": 250})
    preview = client.state(name)["resize"]["preview"]
    client.run(name, "resize/retry", {"preview_id": preview["preview_id"], "approve_size": True, "approve_changes": True})
    state = client.state(name)
    client.run(name, "review", {"candidate": state["proposal"]["selected"], "widen_openings": False})
    project = workspace / name
    base = review.load_project(project / "review")
    grid = base.grid
    assert grid.outer_mm == (150.0, 250.0) and grid.shape == (576, 346) and grid.edge_mm[0] < 0
    front = base.sheets[-1].id
    before = base.sheets[-1].retained.copy()

    def pixel(x: float, y: float) -> tuple[int, int]:  # sheet mm (y up) -> (row, column) of the grid
        return grid.rows - 1 - int(np.floor(y / grid.mm_per_pixel)), int(np.floor(x / grid.mm_per_pixel))

    # Cut a 0.6 mm slot and a 4 mm bar in the solid top padding of the front sheet, then fill the bar again.
    client.run(name, "edit", {"op": {"op": "subtract", "sheet": front, "shape": {"type": "bar", "from_mm": [40.0, 210.0],
                                                                                 "to_mm": [100.0, 210.0], "width_mm": 0.6}}})
    client.run(name, "edit", {"op": {"op": "subtract", "sheet": front, "shape": {"type": "bar", "from_mm": [40.0, 226.0],
                                                                                 "to_mm": [60.0, 226.0], "width_mm": 4.0}}})
    cut = next(s for s in review.load_project(project / "review").sheets if s.id == front).retained
    assert not cut[pixel(50.0, 226.0)] and not cut[pixel(50.0, 225.0)] and cut[pixel(50.0, 229.0)]
    client.run(name, "edit", {"op": {"op": "fill_hole", "sheet": front, "at_mm": [50.0, 226.0]}})
    after = next(s for s in review.load_project(project / "review").sheets if s.id == front).retained
    assert not after[pixel(70.0, 210.0)] and after[pixel(70.0, 211.7)] and after[pixel(70.0, 208.3)]
    assert after[pixel(50.0, 226.0)] and np.array_equal(after[pixel(0, 230)[0] - 20:pixel(0, 222)[0] + 20],
                                                         before[pixel(0, 230)[0] - 20:pixel(0, 222)[0] + 20])
    rows, cols = np.nonzero(after != before)  # only the slot changed: at the slot's rows and columns
    assert rows.min() >= pixel(0, 210.3 + grid.mm_per_pixel)[0] and rows.max() <= pixel(0, 209.7 - grid.mm_per_pixel)[0]
    assert cols.min() >= pixel(40.0 - 0.3 - grid.mm_per_pixel, 0)[1] and cols.max() <= pixel(100.0 + 0.3 + grid.mm_per_pixel, 0)[1]

    # The sun on the rectangular artwork canvas: the Fill click target.
    target = art.load_rgb(project / "review" / "target.png")
    sun = (target == np.array([0xF9, 0xC8, 0x32], np.uint8)).all(-1)
    r, c = (int(round(v)) for v in np.argwhere(sun).mean(0))
    assert sun[r, c] and target.shape[:2] == (grid.artwork_rows, grid.artwork_pixels)
    hull = (target == np.array([0x77, 0x46, 0x27], np.uint8)).all(-1)
    hr, hc = (int(round(v)) for v in np.argwhere(hull).mean(0))
    assert hull[hr, hc]
    points = {"sheet": front, "slot_in": [70.0, 210.0], "slot_out": [70.0, 211.7], "fill_px": [r, c], "pick_px": [hr, hc],
              "color": "#774627"}
    shots = tmp_path / "shots"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_rect_review.mjs"), client.base + "/", str(browser), "-",
                          str(shots), name, json.dumps(points)], capture_output=True, text=True, encoding="utf-8", timeout=900)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    result = json.loads(lines[-1])
    (shots / "drive_result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    assert result["ok"], result.get("error", "") + "\n" + run.stderr[-3000:]
    assert result["console_errors"] == []
    # The images span the pixel grid (150.17 x 250 mm); the issue is the slot.
    assert result["extent"] == pytest.approx([0.0, 0.0, *grid.raster_mm], abs=1e-6)
    x0, y0, x1, y1 = result["issue"]["bbox_mm"]
    assert x0 <= 70.0 <= x1 and y0 <= 210.0 <= y1 and 0.3 < y1 - y0 < 1.2 and 55.0 < x1 - x0 < 65.0
    band = grid.perimeter_pixels * grid.mm_per_pixel
    where = (f"{x0 - band:.2f} mm from the left, {grid.raster_mm[1] - band - y1:.2f} mm from the top of the "
             f"{grid.artwork_mm:.1f} × {grid.artwork_height_mm:.1f} mm aperture")
    for key in ("from_list", "from_click", "after_reload"):
        assert result[key]["title"] == "Opening may close", key
        assert result[key]["aperture"] == where, (key, result[key]["aperture"], where)
        assert result["opaque"][{"from_list": "list", "from_click": "click", "after_reload": "reload"}[key]] == [True, False], key
    # The Fill click landed on the clicked pixel of the rectangular canvas and recolored the whole sun.
    assert result["art_image"] == [grid.artwork_pixels, grid.artwork_rows]
    assert len(result["saved_ops"]) == 1 and result["saved_ops"][0]["at_px"] == [r, c]
    assert result["after_reload"]["ops"] == result["saved_ops"]
    edited = art.load_rgb(project / "artwork" / "current.png")
    brown = (edited == np.array([0x77, 0x46, 0x27], np.uint8)).all(-1)
    assert brown[sun].all() and not (edited == np.array([0xF9, 0xC8, 0x32], np.uint8)).all(-1).any()
    assert np.array_equal(edited[~sun], target[~sun])
    # Saved: the three sheet edits and the masks are unchanged by the reload.
    assert result["after_reload"]["history"] == len(client.state(name)["draft"]["edits"]) == 3
    assert np.array_equal(next(s for s in review.load_project(project / "review").sheets if s.id == front).retained, after)


def test_rectangular_size_survives_a_restart(tmp_path):
    """The current width and height are read from the project after the app restarts."""

    server, client = start(tmp_path / "workspace")
    try:
        name = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&outer_mm=200",
                         raw=SAILBOAT.read_bytes())["project"]
        client.wait({"id": client.state(name)["job"]["id"]})
        client.run(name, "resize/analyze", {"process": None, "bambu_printer": None})
        client.run(name, "resize/preview", {"width_mm": 200, "height_mm": 150})
        preview = client.state(name)["resize"]["preview"]
        client.run(name, "resize/retry", {"preview_id": preview["preview_id"], "approve_size": True, "approve_changes": True})
    finally:
        server.shutdown()
        server.server_close()
    server, client = start(tmp_path / "workspace")
    try:
        state = client.state(name)
        assert state["resize"]["sheet_size_mm"] == [200.0, 150.0]
        assert state["record"]["sheet_outer_mm"] == 200.0 and state["record"]["sheet_outer_height_mm"] == 150.0
        client.run(name, "resize/analyze", {"process": None, "bambu_printer": None})
        assert client.state(name)["resize"]["analysis"]["basis"]["sheet_size_mm"] == [200.0, 150.0]
        client.run(name, "review", {"candidate": state["proposal"]["selected"], "widen_openings": True})
        draft = client.state(name)["draft"]
        assert draft["status"]["status"] == "REVIEWED_VALID" and draft["validation"]["outer_mm"] == [200.0, 150.0]
    finally:
        server.shutdown()
        server.server_close()


def test_trial_planning_finds_a_size_for_a_failed_proposal(app, monkeypatch):
    """A search-limit failure (simulated below 80 mm) has no width prediction: trial planning finds the size."""

    import layerforge_l1.core.planning as core_planning
    import layerforge_l1.core.propose as core_propose
    from layerforge_l1.core.planning import PlanningError

    real = core_planning.plan_stack

    def small_fails(target, config, name="stack"):
        if config.outer_sheet_mm < 130:
            raise PlanningError("beam search ended INCOMPLETE within 24 sheets (simulated)", {"closure_feasible": True})
        return real(target, config, name)

    monkeypatch.setattr(core_propose, "plan_stack", small_fails)
    monkeypatch.setattr(core_planning, "plan_stack", small_fails)  # trial planning imports it from planning
    client, _ = app
    name = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&outer_mm=60", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    assert client.state(name)["proposal"]["status"] == "NEEDS_USER_DECISION"
    process = {"name": "small_bed", "source": "test", "nozzle_diameter_mm": 0.4, "line_width_mm": 0.42, "layer_height_mm": 0.2,
               "first_layer_height_mm": 0.2, "min_opening_mm": 0.8, "min_positive_feature_mm": 0.84, "bed_x_mm": 200,
               "bed_y_mm": 200, "max_z_mm": 100, "filament_density_g_cm3": 1.24}
    client.run(name, "resize/analyze", {"process": process})
    analysis = client.state(name)["resize"]["analysis"]
    assert analysis["limit"]["max_sheet_mm"] == 200.0 and analysis["trial_search"]["available"]
    assert analysis["options"]["recommended"] is None  # nothing predicted from widths
    client.run(name, "resize/search", {})
    search = client.state(name)["resize"]["analysis"]["trial_search"]
    assert search["trials"][-1]["sheet_mm"] == 200.0 and search["trials"][-1]["plans"]
    assert 130.0 <= search["smallest_plannable_mm"] < 140.0
    assert all(not t["plans"] for t in search["trials"] if t["sheet_mm"] < 130)
    size = search["smallest_plannable_mm"]
    client.run(name, "resize/preview", {"size_mm": size})
    preview = client.state(name)["resize"]["preview"]
    client.run(name, "resize/retry", {"preview_id": preview["preview_id"], "approve_size": True, "approve_changes": True})
    state = client.state(name)
    assert state["proposal"]["status"] == "PROPOSAL_READY_FOR_REVIEW" and state["record"]["sheet_outer_mm"] == size
    assert state["upscale"]["attempts"] == []  # the failed proposal is discarded


def test_resize_from_review_keeps_the_original_and_locates_remaining_issues(app):
    """Resize from an open review: printer limit, approved artwork only, the accepted original kept, remaining
    findings located.

    The process is a deliberately coarse test process (2.0 mm nozzle, 104 mm bed) so that UNSUPPORTED
    findings remain after the largest fitting resize; it is not a real printer profile.
    """

    import numpy as np
    from PIL import Image

    from layerforge_l1.core.model import file_sha256

    client, workspace = app
    name = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&outer_mm=60", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    state = client.state(name)
    candidate = state["proposal"]["selected"]
    client.run(name, "review", {"candidate": candidate})
    review_sha = file_sha256(workspace / name / "review" / "sheet_stack.json")
    source_sha = file_sha256(workspace / name / state["record"]["source"]["file"])
    client.run(name, "plan/accept")  # the original is kept by accepting it
    process = {"name": "test_coarse_2.0mm", "source": "test only", "nozzle_diameter_mm": 2.0, "line_width_mm": 2.1,
               "layer_height_mm": 0.2, "first_layer_height_mm": 0.2, "min_opening_mm": 4.0, "min_positive_feature_mm": 4.2,
               "bed_x_mm": 104, "bed_y_mm": 130, "max_z_mm": 100, "filament_density_g_cm3": 1.24}
    client.run(name, "resize/analyze", {"process": process})
    analysis = client.state(name)["resize"]["analysis"]
    assert analysis["basis"]["stage"] == "review" and analysis["limit"]["max_sheet_mm"] == 104.0
    assert analysis["measured"]["counts"]["UNSUPPORTED"] > 0
    project = f"/api/projects/{name}/resize"
    assert client.call("POST", f"{project}/preview", {"size_mm": 105})[0] == 400  # beyond the printer
    client.run(name, "resize/preview", {"size_mm": 104})
    preview = client.state(name)["resize"]["preview"]
    checks = {c["check"]: c["pass"] for c in preview["boundaries"]["checks"]}
    assert all(checks.values()) and checks["sheet fits the printer's usable area"] and len(checks) == 9

    # Only the artwork approved in the preview can be planned: a different target refuses the retry.
    target = workspace / name / "resize" / "preview" / "target.png"
    original = target.read_bytes()
    pixels = np.array(Image.open(target).convert("RGB"))
    pixels[300, 300] = 255 - pixels[300, 300]
    Image.fromarray(pixels).save(target)
    body = {"preview_id": preview["preview_id"], "approve_size": True, "approve_changes": True}
    failed = client.run(name, "resize/retry", body, "failed")
    assert "differs from the approved preview" in failed["error"]["message"]
    after_refusal = client.state(name)
    assert after_refusal["record"].get("sheet_outer_mm") is None and after_refusal["draft"] is not None
    assert file_sha256(workspace / name / "review" / "sheet_stack.json") == review_sha
    target.write_bytes(original)

    client.run(name, "resize/retry", body)
    state = client.state(name)
    record = state["record"]
    assert record["sheet_outer_mm"] == 104.0 and record["proposal_settings"]["outer_mm"] == 60.0 and record["candidate"] is None
    assert "draft" not in state and file_sha256(workspace / name / state["record"]["source"]["file"]) == source_sha
    accepted = workspace / name / "attempts" / "accepted"
    assert file_sha256(accepted / "review" / "sheet_stack.json") == review_sha  # the original review is kept
    assert state["upscale"]["attempts"] == [] and [p.name for p in accepted.parent.iterdir()] == ["accepted"]
    assert state["checkpoint"]["summary"]["sheet_size_mm"] == [60.0, 60.0] and not state["checkpoint"]["working_is_accepted"]
    check = state["resize"]["last_check"]
    assert check["artwork_matches_approved_preview"] and "estimate" in check["predicted_basis"]
    assert check["more_unsupported_than_predicted"] == max(0, check["after"]["counts"]["UNSUPPORTED"] - check["predicted"]["UNSUPPORTED"])
    left = check["after"]["unsupported"]
    assert left and all(u["sheet"] and len(u["at_mm"]) == 2 and 0 <= u["at_mm"][0] <= 104 for u in left)
    assert check["after"]["map_urls"] and client.ok("GET", check["after"]["map_urls"][0])[:4] == b"\x89PNG"
    # The path to manual correction: reviewing the re-planned candidate gives the same sheets to Locate.
    client.run(name, "review", {"candidate": check["after"]["candidate"]})
    sheets = {s["id"] for s in client.state(name)["draft"]["sheets"]}
    assert {u["sheet"] for u in left} <= sheets


def _link_dir(target: Path, link: Path) -> bool:
    """A directory junction (Windows) or symlink to ``target``; False when neither can be made."""

    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return True
    except (ImportError, OSError):
        try:
            link.symlink_to(target, target_is_directory=True)
            return True
        except OSError:
            return False


def test_delete_project_successful_failed_partial_and_confined(app, tmp_path, monkeypatch):
    """Delete whole projects: complete (review + kit), failed, partially generated; confinement and restart."""

    import layerforge_l1.core.propose as core_propose
    from layerforge_l1.core.model import file_sha256
    from layerforge_l1.core.planning import PlanningError

    client, workspace = app
    fixture_sha = file_sha256(SAILBOAT)
    names = lambda c=client: [p["name"] for p in c.ok("GET", "/api/projects")["projects"]]  # noqa: E731

    # 1. A complete project: proposal, review with an edit, printability estimate, print kit.
    done, state = _propose(client)
    client.run(done, "review", {"candidate": state["proposal"]["selected"]})
    client.run(done, "printability", {})
    client.run(done, "kits", {})
    folder = workspace / done
    files = sum(1 for p in folder.rglob("*") if p.is_file())
    assert files > 50 and {"source", "proposal", "review", "draft", "printability", "kits"} <= {p.name for p in folder.iterdir()}
    # Refused while a job runs; other origins refused.
    job = client.ok("POST", f"/api/projects/{done}/verify", {})["job"]
    status, error = client.call("POST", f"/api/projects/{done}/delete", {})
    assert status == 409 and "still" in error["error"]["message"]
    client.wait(job)
    files = sum(1 for p in folder.rglob("*") if p.is_file())  # including what the verify job wrote
    assert client.call("POST", f"/api/projects/{done}/delete", {}, headers={"Origin": "http://evil.example"})[0] == 403
    result = client.ok("POST", f"/api/projects/{done}/delete", {})
    assert result["status"] == "DELETED" and result["removed_files"] == files and result["failed_count"] == 0
    assert not folder.exists() and done not in names()
    assert client.call("GET", f"/api/projects/{done}")[0] == 404
    assert client.call("POST", f"/api/projects/{done}/delete", {})[0] == 404
    assert file_sha256(SAILBOAT) == fixture_sha  # the original image elsewhere on disk is untouched

    # 2. A failed proposal (planning fails), with an archived attempt folder and a user-made link inside.
    def fails(target, config, name="stack"):
        raise PlanningError("simulated failure", {"closure_feasible": False})

    monkeypatch.setattr(core_propose, "plan_stack", fails)
    failed = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&name=failed_one", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(failed)["job"]["id"]})
    assert client.state(failed)["proposal"]["status"] == "NEEDS_USER_DECISION"
    monkeypatch.undo()
    (workspace / failed / "attempts" / "attempt_01_scale1").mkdir(parents=True)
    (workspace / failed / "attempts" / "attempt_01_scale1" / "note.txt").write_text("archived", encoding="utf-8")
    outside = tmp_path / "outside_user_data"
    outside.mkdir()
    (outside / "keep.txt").write_text("user data", encoding="utf-8")
    linked = _link_dir(outside, workspace / failed / "linked_folder")
    # A file held open by another program: partial delete, project still listed, retry succeeds.
    held = open(workspace / failed / "source" / "sailboat_512.png", "rb")  # noqa: SIM115
    try:
        partial = client.ok("POST", f"/api/projects/{failed}/delete", {})
    finally:
        held.close()
    if partial["status"] == "PARTIALLY_DELETED":  # Windows refuses to delete an open file
        assert any("sailboat_512.png" in f["path"] for f in partial["failed"])
        assert (workspace / failed / "project.json").is_file() and failed in names()
        partial = client.ok("POST", f"/api/projects/{failed}/delete", {})
    assert partial["status"] == "DELETED" and not (workspace / failed).exists() and failed not in names()
    if linked:
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "user data"

    # 3. A partially generated project: staging folder and half-written files left by an interrupted run.
    half = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&name=half_done", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(half)["job"]["id"]})
    (workspace / half / "proposal.next" / "candidates").mkdir(parents=True)
    (workspace / half / "proposal.next" / "candidates" / "partial.json.partial").write_text("{", encoding="utf-8")
    shutil.rmtree(workspace / half / "proposal" / "selected")
    assert client.ok("POST", f"/api/projects/{half}/delete", {})["status"] == "DELETED" and not (workspace / half).exists()

    # Confinement: path parts, non-project folders and a project folder that is a link are refused.
    notes = workspace / "my_notes"
    notes.mkdir()
    (notes / "todo.txt").write_text("keep", encoding="utf-8")
    for bad in ("..", "my_notes", "..%2F..%2Fx", "a%2F..%2Fmy_notes", "%2E%2E"):
        status, _ = client.call("POST", f"/api/projects/{bad}/delete", {})
        assert status in (400, 404), (bad, status)
    assert (notes / "todo.txt").read_text(encoding="utf-8") == "keep"
    target = tmp_path / "linked_project"
    target.mkdir()
    (target / "project.json").write_text(json.dumps({"schema": "layerforge.app_project", "name": "linked_project", "created_utc": "2026-01-01T00:00:00+00:00",
                                                           "source": {"file": "source/x.png"}}), encoding="utf-8")
    if _link_dir(target, workspace / "linked_project"):
        status, error = client.call("POST", "/api/projects/linked_project/delete", {})
        assert status in (400, 404) and (target / "project.json").is_file()

    # A new project can be created at once, even with a deleted project's name; restart keeps deletions.
    again = client.ok("POST", f"/api/projects?filename=sailboat_512.png&scale=1&name={done}", raw=SAILBOAT.read_bytes())["project"]
    assert again == done
    client.wait({"id": client.state(again)["job"]["id"]})
    server2, client2 = start(workspace)
    try:
        listed = names(client2)
        assert again in listed and failed not in listed and half not in listed and "my_notes" not in listed
    finally:
        server2.shutdown()
        server2.server_close()


def test_a_client_that_leaves_before_the_reply_is_not_a_server_error(app, monkeypatch, capfd):
    """A page closed or reloaded while a request runs (e.g. a connector check in flight) resets the
    connection; the server must not report that as a 500 or print a traceback, and keeps serving."""

    import socket
    import struct

    from layerforge_l1.app import server as server_module
    from layerforge_l1.app.workspace import Workspace

    client, _ = app
    left, done = threading.Event(), threading.Event()

    def slow_check(self, name, op):
        assert left.wait(10)
        return {"pad": "x" * 4_000_000}  # larger than any socket buffer: the write meets the reset

    dispatch = server_module.Handler._dispatch

    def tracked(self, route):
        try:
            dispatch(self, route)
        finally:
            done.set()

    monkeypatch.setattr(Workspace, "check_edit", slow_check)
    monkeypatch.setattr(server_module.Handler, "_dispatch", tracked)
    host, port = client.base.split("//")[1].split(":")
    body = b'{"op": {}}'
    with socket.create_connection((host, int(port))) as sock:
        sock.sendall(b"POST /api/projects/any/edit/check HTTP/1.1\r\nHost: %s:%s\r\nContent-Type: application/json\r\n"
                     b"Content-Length: %d\r\n\r\n%s" % (host.encode(), port.encode(), len(body), body))
        time.sleep(0.3)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # close with a reset
    left.set()
    assert done.wait(10)
    monkeypatch.undo()
    assert client.ok("GET", "/api/info")["physical"]["status"] == "NOT_VERIFIED"  # still serving
    err = capfd.readouterr().err
    assert "Traceback" not in err and "Exception occurred" not in err and " 500 " not in err, err


def test_a_refused_request_with_an_unread_body_still_gets_its_reply(app):
    """A request refused before its body is read (another origin, an unknown project) must still reach the
    client: closing a socket with unread data resets it on Windows, which lost the 403 intermittently."""

    client, _ = app
    for i in range(60):
        status, error = client.call("POST", "/api/projects/missing/kits/kit_1/delete", {"pad": "x" * (i * 97)},
                                    headers={"Origin": "http://evil.example"})
        assert status == 403 and "cross-origin" in error["error"]["message"]
        status, error = client.call("POST", "/api/projects/missing/review", {"candidate": "x" * (i * 97)})
        assert status == 404 and "no " in error["error"]["message"]


def test_artwork_palette_is_shown_on_plan_and_review_alike(tmp_path):
    """Regression: once a review existed, the artwork editor showed "0 colors · Shown after the first selection"
    until the first click, while the Plan tab listed every color.  The review's artwork palette is listed at once
    and matches what the edit log reports after the first selection."""

    server, client = start(tmp_path / "workspace")
    try:
        name, state = _propose(client)
        before = {c["hex"] for c in state["artwork"]["palette"]}
        client.run(name, "review", {"candidate": state["proposal"]["selected"]})
        review = client.state(name)["artwork"]
        assert review["stage"] == "review" and review["palette"], review
        assert {c["hex"] for c in review["palette"]} == before
        assert all(c["area_mm2"] > 0 and c["regions_4"] >= 1 for c in review["palette"])
        from layerforge_l1.core import artwork as art

        rows = art.palette_summary(art.load_rgb(server.app.workspace.path(name) / "review" / "target.png"),
                                   client.state(name)["artwork"]["frame_color"],
                                   review["mm_per_pixel"])
        assert review["palette"] == [{k: r[k] for k in ("hex", "area_mm2", "regions_4")} for r in rows]
    finally:
        server.shutdown()
        server.server_close()


def test_state_is_read_again_when_a_job_moves_a_folder_meanwhile(monkeypatch):
    """Regression (UX Phase 4 tour): GET /api/projects/<p> answered 500 when a candidate switch moved ``review/``
    aside while the state was being read.  A missing file is read again; a lasting one is still an error."""

    from layerforge_l1.app import server as server_module

    calls = []

    class FakeWorkspace:
        def __init__(self, failures):
            self.failures = failures

        def state(self, name):
            calls.append(name)
            if len(calls) <= self.failures:
                raise FileNotFoundError("review/target.png")
            return {"name": name}

    monkeypatch.setattr(server_module.time, "sleep", lambda s: None)
    assert server_module._state_settled(FakeWorkspace(1), "p") == {"name": "p"} and len(calls) == 2
    calls.clear()
    with pytest.raises(FileNotFoundError):
        server_module._state_settled(FakeWorkspace(10), "p")
    assert len(calls) == 4
