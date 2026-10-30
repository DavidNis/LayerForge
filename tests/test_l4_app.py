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
            if current["status"] in ("done", "failed"):
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

    # Review the selected candidate.
    client.run(name, "review", {"candidate": proposal["selected"]})
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
    assert "NOT VERIFIED" in result["physical_after_proposal"] and "NOT VERIFIED" in result["physical_at_end"]
    assert "no cutout to fill" in result["error_banner"]
    assert "DRAFT_INVALID" in result["after_slot"]["validation"]
    assert "Visible change" in result["after_slot"]["change"]
    assert "REVIEWED_VALID" in result["after_accept"]
    assert "rim 6" in result["rim_layer"] and "rim" not in result["after_undo_layer"]
    assert result["rim_all_default"] is True and result["rim_single_rows"] == 1
    assert result["rim_all_rows"] == result["layer_rows"] > 1
    assert result["history_after_reload"] == 3 and result["rim_after_reload"] is True
    assert "Risks reported" in result["printability"] and any("RISK" in row for row in result["findings"])
    assert "Risks reported (estimate)" in result["fdm_card"]
    assert "CAD_VALID" in result["kit"]["cad"] and "NOT VERIFIED" in result["kit"]["physical"]
    assert "CAD verified by reopening" in result["kit"]["geometry_card"]
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
    attempt = state["upscale"]["attempts"][0]
    assert attempt["status"] == "NEEDS_USER_DECISION" and attempt["scale"] == 1
    assert attempt["replaced_by"] == {"upscale_factor": 2, "scale": 2, "status": "PROPOSAL_READY_FOR_REVIEW",
                                      "approved_visible_changes": len(preview["changes"]), "preview_id": preview["preview_id"]}
    archived = workspace / name / "attempts" / attempt["id"]
    old = json.loads((archived / "proposal" / "proposal.json").read_text(encoding="utf-8"))
    assert "simulated" in old["candidates"][0]["reason"]  # the failure diagnostics are kept
    assert (archived / "upscale_preview" / "preview.json").is_file()
    assert client.ok("GET", attempt["images"]["target"])[:4] == b"\x89PNG"
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
    for bad in (60, 59, 1000, "big"):
        assert client.call("POST", f"{project}/preview", {"size_mm": bad})[0] == 400, bad
    client.run(name, "resize/preview", {"size_mm": rec["sheet_mm"]})
    preview = client.state(name)["resize"]["preview"]
    assert preview["current"] and preview["status"] == "READY_TO_RESIZE" and preview["boundaries"]["pass"]
    assert preview["dimensions_new"]["sheet_mm"] == [rec["sheet_mm"]] * 2 and preview["dimensions_now"]["sheet_mm"] == [60.0, 60.0]
    # Never applied without explicit approval of the size (and of visible changes, if any).
    status, error = client.call("POST", f"{project}/retry", {"preview_id": preview["preview_id"]})
    assert status == 400 and "approve changing the sheet" in error["error"]["message"]
    assert client.call("POST", f"{project}/retry", {"preview_id": "old", "approve_size": True, "approve_changes": True})[0] == 400
    assert client.state(name)["record"].get("sheet_outer_mm") is None
    client.run(name, "resize/retry", {"preview_id": preview["preview_id"], "approve_size": True, "approve_changes": True})

    state = client.state(name)
    assert state["proposal"]["planning_config"]["outer_sheet_mm"] == rec["sheet_mm"]
    assert state["record"]["sheet_outer_mm"] == rec["sheet_mm"] and state["record"]["proposal_settings"]["outer_mm"] == 60.0
    assert file_sha256(source) == sha
    check = state["resize"]["last_check"]
    assert check["from_mm"] == 60.0 and check["to_mm"] == rec["sheet_mm"] and "after" in check
    assert check["after"]["counts"]["UNSUPPORTED"] + check["after"]["counts"]["RISK"] < \
        check["before"]["counts"]["UNSUPPORTED"] + check["before"]["counts"]["RISK"]
    attempt = state["upscale"]["attempts"][0]
    assert attempt["sheet_mm"] == 60.0 and attempt["replaced_by"]["kind"] == "physical_resize"
    archived = workspace / name / "attempts" / attempt["id"]
    assert (archived / "proposal" / "proposal.json").is_file() and (archived / "resize" / "analysis.json").is_file()
    assert json.loads((archived / "proposal" / "proposal.json").read_text(encoding="utf-8"))["planning_config"]["outer_sheet_mm"] == 60.0
    # The new proposal continues through the unchanged L2 workflow.
    client.run(name, "review", {"candidate": state["proposal"]["selected"]})
    assert client.state(name)["draft"]["status"]["status"] == "REVIEWED_VALID"


def test_trial_planning_finds_a_size_for_a_failed_proposal(app, monkeypatch):
    """A search-limit failure (simulated below 80 mm) has no width prediction: trial planning finds the size."""

    import layerforge_l1.core.planning as core_planning
    import layerforge_l1.core.propose as core_propose
    from layerforge_l1.core.planning import PlanningError

    real = core_planning.plan_stack

    def small_fails(target, config, name="stack"):
        if config.outer_sheet_mm < 80:
            raise PlanningError("beam search ended INCOMPLETE within 24 sheets (simulated)", {"closure_feasible": True})
        return real(target, config, name)

    monkeypatch.setattr(core_propose, "plan_stack", small_fails)
    monkeypatch.setattr(core_planning, "plan_stack", small_fails)  # trial planning imports it from planning
    client, _ = app
    name = client.ok("POST", "/api/projects?filename=sailboat_512.png&scale=1&outer_mm=60", raw=SAILBOAT.read_bytes())["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    assert client.state(name)["proposal"]["status"] == "NEEDS_USER_DECISION"
    process = {"name": "small_bed", "source": "test", "nozzle_diameter_mm": 0.4, "line_width_mm": 0.42, "layer_height_mm": 0.2,
               "first_layer_height_mm": 0.2, "min_opening_mm": 0.8, "min_positive_feature_mm": 0.84, "bed_x_mm": 120,
               "bed_y_mm": 120, "max_z_mm": 100, "filament_density_g_cm3": 1.24}
    client.run(name, "resize/analyze", {"process": process})
    analysis = client.state(name)["resize"]["analysis"]
    assert analysis["limit"]["max_sheet_mm"] == 120.0 and analysis["trial_search"]["available"]
    assert analysis["options"]["recommended"] is None  # nothing predicted from widths
    client.run(name, "resize/search", {})
    search = client.state(name)["resize"]["analysis"]["trial_search"]
    assert search["trials"][-1]["sheet_mm"] == 120.0 and search["trials"][-1]["plans"]
    assert 80.0 <= search["smallest_plannable_mm"] < 90.0
    assert all(not t["plans"] for t in search["trials"] if t["sheet_mm"] < 80)
    size = search["smallest_plannable_mm"]
    client.run(name, "resize/preview", {"size_mm": size})
    preview = client.state(name)["resize"]["preview"]
    client.run(name, "resize/retry", {"preview_id": preview["preview_id"], "approve_size": True, "approve_changes": True})
    state = client.state(name)
    assert state["proposal"]["status"] == "PROPOSAL_READY_FOR_REVIEW" and state["record"]["sheet_outer_mm"] == size
    assert state["upscale"]["attempts"][0]["status"] == "NEEDS_USER_DECISION"


def test_resize_from_review_keeps_the_original_and_locates_remaining_issues(app):
    """Resize from an open review: printer limit, approved artwork only, archive, remaining findings located.

    The process is a deliberately coarse test process (1.0 mm nozzle, 64 mm bed) so that UNSUPPORTED
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
    process = {"name": "test_coarse_1.0mm", "source": "test only", "nozzle_diameter_mm": 1.0, "line_width_mm": 1.05,
               "layer_height_mm": 0.2, "first_layer_height_mm": 0.2, "min_opening_mm": 2.0, "min_positive_feature_mm": 2.1,
               "bed_x_mm": 64, "bed_y_mm": 90, "max_z_mm": 100, "filament_density_g_cm3": 1.24}
    client.run(name, "resize/analyze", {"process": process})
    analysis = client.state(name)["resize"]["analysis"]
    assert analysis["basis"]["stage"] == "review" and analysis["limit"]["max_sheet_mm"] == 64.0
    assert analysis["measured"]["counts"]["UNSUPPORTED"] > 0
    project = f"/api/projects/{name}/resize"
    assert client.call("POST", f"{project}/preview", {"size_mm": 65})[0] == 400  # beyond the printer
    client.run(name, "resize/preview", {"size_mm": 64})
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
    assert record["sheet_outer_mm"] == 64.0 and record["proposal_settings"]["outer_mm"] == 60.0 and record["candidate"] is None
    assert "draft" not in state and file_sha256(workspace / name / state["record"]["source"]["file"]) == source_sha
    attempt = state["upscale"]["attempts"][0]
    archived = workspace / name / "attempts" / attempt["id"]
    assert file_sha256(archived / "review" / "sheet_stack.json") == review_sha  # the original review is kept
    assert attempt["stage"] == "review" and attempt["sheet_mm"] == 60.0
    check = state["resize"]["last_check"]
    assert check["artwork_matches_approved_preview"] and "estimate" in check["predicted_basis"]
    assert check["more_unsupported_than_predicted"] == max(0, check["after"]["counts"]["UNSUPPORTED"] - check["predicted"]["UNSUPPORTED"])
    left = check["after"]["unsupported"]
    assert left and all(u["sheet"] and len(u["at_mm"]) == 2 and 0 <= u["at_mm"][0] <= 64 for u in left)
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
