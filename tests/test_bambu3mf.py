"""Optional Bambu Studio multi-plate project for an L3 kit (``core.bambu3mf``).

The kit is the project-owned sailboat fixture (``fixtures/l3_print_kit/sailboat_512.png``,
planned at scale 1: seven 225 mm sheets, one color repeated).  Format and placement
tests use a small synthetic Bambu preset set written here, so they run without Bambu
Studio.  The tests marked ``bambu_studio`` use the installed Bambu Studio (its system
presets and its command line, which loads and slices every plate); they are skipped
with that reason on computers without it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from layerforge_l1 import core
from layerforge_l1.core import bambu3mf
from layerforge_l1.core.bambu3mf import (
    BAMBU_PRINTERS,
    BambuProfiles,
    place_on_bed,
    plate_columns,
    plate_origin,
    read_project,
    write_bambu_project,
)
from layerforge_l1.core.model import save_stack
from layerforge_l1.core.printkit import PrintKitError, build_print_kit, step_body, verify_checksums

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_l0_core import CONFIG  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SAILBOAT = ROOT / "fixtures" / "l3_print_kit" / "sailboat_512.png"
P1S = "Bambu Lab P1S 0.4 nozzle"
REAL_PROFILES = bambu3mf.find_profiles()
REAL_STUDIO = bambu3mf.find_bambu_studio()
needs_profiles = pytest.mark.skipif(REAL_PROFILES is None, reason="Bambu Studio system presets not installed")
needs_studio = pytest.mark.skipif(REAL_PROFILES is None or REAL_STUDIO is None,
                                  reason="Bambu Studio (bambu-studio executable and presets) not installed")


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture(scope="module")
def fake_profiles(tmp_path_factory) -> BambuProfiles:
    """A minimal preset set in Bambu's layout: index, inherits chains, an include template."""

    root = tmp_path_factory.mktemp("profiles")
    entries = {"machine_list": [], "process_list": [], "filament_list": []}

    def add(kind: str, name: str, data: dict) -> None:
        sub = f"{kind}/{name}.json"
        _write(root / "BBL" / sub, {"name": name, **data})
        entries[{"machine": "machine_list", "process": "process_list", "filament": "filament_list"}[kind]].append(
            {"name": name, "sub_path": sub})

    add("machine", "fdm_common", {"printer_technology": "FFF", "nozzle_diameter": ["0.4"], "printable_height": "250",
                                  "printable_area": ["0x0", "256x0", "256x256", "0x256"], "instantiation": "false"})
    add("machine", "P1S start gcode", {"machine_start_gcode": ";P1S start"})
    add("machine", P1S, {"inherits": "fdm_common", "include": ["P1S start gcode"], "printer_model": "Bambu Lab P1S",
                         "bed_exclude_area": ["0x0", "18x0", "18x28", "0x28"], "best_object_pos": "0.5x0.5",
                         "printer_extruder_variant": ["Direct Drive Standard", "Direct Drive High Flow"]})
    add("machine", "Bambu Lab A1 mini 0.4 nozzle", {
        "inherits": "fdm_common", "printer_model": "Bambu Lab A1 mini", "printable_height": "180",
        "printable_area": ["0x0", "180x0", "180x180", "0x180"], "head_wrap_detect_zone": ["156x152", "180x152", "180x180", "156x180"],
        "best_object_pos": "0.7x0.5"})
    add("process", "fdm_process_common", {"wall_loops": "2", "layer_height": "0.2", "instantiation": "false"})
    add("process", "0.20mm Standard @BBL X1C", {"inherits": "fdm_process_common", "compatible_printers": [P1S],
                                                  "print_extruder_variant": ["Direct Drive Standard", "Direct Drive High Flow"]})
    add("process", "0.20mm Standard @BBL A1M", {"inherits": "fdm_process_common"})
    add("filament", "Generic PLA @base", {"filament_type": ["PLA"], "filament_id": "GFL99", "instantiation": "false",
                                          "nozzle_temperature": ["220"], "filament_extruder_variant": ["Direct Drive Standard"]})
    add("filament", "Generic PLA", {"inherits": "Generic PLA @base", "compatible_printers": [P1S]})
    add("filament", "Generic PLA @BBL A1M", {"inherits": "Generic PLA @base"})
    _write(root / "BBL.json", {"name": "Bambulab", "version": "00.00.00.01", **entries})
    return BambuProfiles(root)


@pytest.fixture(scope="module")
def sail_kit(tmp_path_factory) -> Path:
    stack, _ = core.plan_stack(np.asarray(Image.open(SAILBOAT).convert("RGB")), CONFIG, name="sailboat")
    stack_dir = tmp_path_factory.mktemp("sail") / "stack"
    save_stack(stack, stack_dir)
    out = tmp_path_factory.mktemp("kit") / "kit"
    build_print_kit(stack_dir, out)
    return out


def _cad_hashes(kit: Path) -> dict[str, str]:
    """STL bytes and STEP bodies (STEP headers carry a timestamp) of a kit's CAD files."""

    return {p.relative_to(kit).as_posix(): hashlib.sha256(
        step_body(p).encode() if p.suffix == ".step" else p.read_bytes()).hexdigest()
        for p in sorted((kit / "cad").rglob("*")) if p.suffix in (".stl", ".step")}


# -- layout ----------------------------------------------------------------------------------
def test_plate_grid_matches_bambu_studio_layout() -> None:
    # Bambu Studio's column count: rounded square root, plus one when rounded down.
    assert [plate_columns(n) for n in (1, 2, 3, 4, 5, 7, 9, 10)] == [1, 2, 2, 2, 3, 3, 3, 4]
    printer = BAMBU_PRINTERS[P1S]
    # Observed in Bambu Studio 02.08.02.61 (CLI slice report of a 7-plate project): plates are
    # 256 mm + 20 % apart, rows go towards negative y.
    assert plate_origin(1, 7, printer) == pytest.approx((307.2, 0.0))
    assert plate_origin(6, 7, printer) == pytest.approx((0.0, -614.4))


def test_placement_avoids_keep_out_zones_and_reports_misfits() -> None:
    from shapely.geometry import Polygon, box

    p1s = BAMBU_PRINTERS[P1S]
    x, y = place_on_bed((225.0, 225.0), p1s)
    footprint = box(x, y, x + 225, y + 225)
    assert Polygon(p1s.printable_area).contains(footprint)
    assert footprint.distance(Polygon(p1s.keep_out[0])) >= bambu3mf.KEEP_OUT_CLEARANCE_MM - 1e-9
    assert place_on_bed((100.0, 100.0), p1s) == (78.0, 78.0)  # centered when nothing is in the way
    assert place_on_bed((257.0, 100.0), p1s) is None
    # A1 mini: 180 mm bed with a wrap-detection corner; a 175 mm square no longer fits.
    assert place_on_bed((150.0, 150.0), BAMBU_PRINTERS["Bambu Lab A1 mini 0.4 nozzle"]) is not None
    assert place_on_bed((175.0, 175.0), BAMBU_PRINTERS["Bambu Lab A1 mini 0.4 nozzle"]) is None


# -- package (synthetic presets) ---------------------------------------------------------------
def test_project_has_one_plate_per_sheet_with_unchanged_geometry(sail_kit, fake_profiles, tmp_path) -> None:
    kit = json.loads((sail_kit / "print_kit.json").read_text(encoding="utf-8"))
    rows = kit["assembly_back_to_front"]
    before = _cad_hashes(sail_kit)
    out = tmp_path / "sail.3mf"
    report = write_bambu_project(sail_kit, P1S, out, profiles=fake_profiles, studio=False)
    assert report["status"] == "WRITTEN" and report["self_check"]["pass"], report.get("self_check")
    assert _cad_hashes(sail_kit) == before  # STEP/STL untouched
    # Without Bambu Studio's own check the export is reported as experimental, never verified.
    assert report["bambu_studio_check"]["status"] == "NOT_RUN"
    assert report["validation"]["level"] == "EXPERIMENTAL_NOT_OPENED_IN_BAMBU_STUDIO"

    project = read_project(out)
    assert project["application"].startswith("BambuStudio-")
    with zipfile.ZipFile(out) as archive:
        description = archive.read("3D/3dmodel.model").decode()
    assert "Written by LayerForge" in description
    assert len(project["plates"]) == len(rows) and all(len(p["objects"]) == 1 for p in project["plates"])
    colors = []
    for row in rows:
        if row["color_hex"].upper() not in colors:
            colors.append(row["color_hex"].upper())
    settings = project["project"]
    assert settings["filament_colour"] == colors and len(colors) < len(rows)  # a repeated color shares a slot
    assert settings["filament_multi_colour"] == colors and settings["filament_colour_type"] == ["0"] * len(colors)
    assert settings["filament_settings_id"] == ["Generic PLA"] * len(colors)
    assert settings["filament_map"] == ["1"] * len(colors)  # one extruder, no AMS mapping
    assert settings["printer_settings_id"] == P1S and settings["print_settings_id"] == "0.20mm Standard @BBL X1C"
    # inherits and include are resolved, preset bookkeeping is not copied into the project
    assert settings["machine_start_gcode"] == ";P1S start" and settings["wall_loops"] == "2"
    assert settings["nozzle_temperature"] == ["220"] * len(colors)
    assert settings["filament_self_index"] == [str(i + 1) for i in range(len(colors))]
    assert "inherits" not in settings and "instantiation" not in settings and "compatible_printers" not in settings

    for index, (row, sheet, plate) in enumerate(zip(rows, report["self_check"]["sheets"], project["plates"])):
        assert plate["id"] == index + 1 and plate["name"].startswith(f"{row['order_back_to_front']:02d} ")
        assert sheet["same_triangles_as_stl"] and sheet["watertight"] and sheet["no_rotation_or_scale"]
        assert sheet["on_bed_z_mm"] == 0 and sheet["inside_printable_area"] and sheet["clear_of_keep_out"]
        assert sheet["size_mm"][2] == pytest.approx(row["stack_height_mm"], abs=1e-6)  # rim included
        assert sheet["filament_color"] == row["color_hex"].upper()
    assert project["plates"][0]["name"].startswith("00 back") and "front" in project["plates"][-1]["name"]

    with zipfile.ZipFile(out) as archive:
        model_settings = archive.read("Metadata/model_settings.config").decode()
    z_values = [float(m.split()[-1]) for m in re.findall(r'assemble_item[^>]*transform="([^"]+)"', model_settings)]
    assert z_values == pytest.approx([r["assembly_z0_mm"] for r in rows])


def test_project_is_reproducible(sail_kit, fake_profiles, tmp_path) -> None:
    a = write_bambu_project(sail_kit, P1S, tmp_path / "a.3mf", profiles=fake_profiles, studio=False)
    b = write_bambu_project(sail_kit, P1S, tmp_path / "b.3mf", profiles=fake_profiles, studio=False)
    assert a["status"] == b["status"] == "WRITTEN"
    with zipfile.ZipFile(tmp_path / "a.3mf") as one, zipfile.ZipFile(tmp_path / "b.3mf") as two:
        for name in one.namelist():
            if name != "3D/3dmodel.model":  # carries today's date
                assert one.read(name) == two.read(name), name


def test_refusals_are_reported_not_hidden(sail_kit, fake_profiles, tmp_path, monkeypatch) -> None:
    mini = write_bambu_project(sail_kit, "Bambu Lab A1 mini 0.4 nozzle", tmp_path / "mini.3mf",
                               profiles=fake_profiles, studio=False)
    assert mini["status"] == "NOT_WRITTEN" and not (tmp_path / "mini.3mf").exists()
    kit = json.loads((sail_kit / "print_kit.json").read_text(encoding="utf-8"))
    assert len(mini["problems"]) == len(kit["assembly_back_to_front"])
    assert all("does not fit the printable area" in p for p in mini["problems"])

    monkeypatch.setattr(bambu3mf, "find_profiles", lambda: None)
    none = write_bambu_project(sail_kit, P1S, tmp_path / "none.3mf", studio=False)
    assert none["status"] == "NOT_WRITTEN" and "presets not found" in none["problems"][0]
    with pytest.raises(bambu3mf.BambuExportError, match="unsupported Bambu printer"):
        write_bambu_project(sail_kit, "Bambu Lab H2D 0.4 nozzle", profiles=fake_profiles, studio=False)

    invalid = tmp_path / "invalid_kit"
    invalid.mkdir()
    (invalid / "print_kit.json").write_text(json.dumps({**kit, "status": {**kit["status"], "cad": "CAD_INVALID"}}),
                                            encoding="utf-8")
    assert write_bambu_project(invalid, P1S, profiles=fake_profiles, studio=False)["status"] == "NOT_WRITTEN"


def test_kit_option_adds_project_guide_and_checksums(sail_kit, fake_profiles, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bambu3mf, "find_profiles", lambda: fake_profiles)
    monkeypatch.setattr(bambu3mf, "find_bambu_studio", lambda: None)
    stack_dir = sail_kit / "stack"
    out = tmp_path / "kit"
    kit = build_print_kit(stack_dir, out, bambu_printer=P1S)
    assert kit["bambu_3mf"]["status"] == "WRITTEN" and "3mf" not in kit["not_included"]
    assert (out / kit["bambu_3mf"]["file"]).is_file() and not verify_checksums(out)
    assert kit["bambu_3mf"]["file"] in (out / "checksums.sha256").read_text(encoding="utf-8")
    assert "## Bambu Studio project" in (out / "PRINT_AND_STACK_GUIDE.md").read_text(encoding="utf-8")
    assert _cad_hashes(out) == _cad_hashes(sail_kit)  # same STEP/STL as the kit without the option
    with pytest.raises(PrintKitError, match="unsupported Bambu printer"):
        build_print_kit(stack_dir, tmp_path / "other", bambu_printer="Bambu Lab H2D 0.4 nozzle")


# -- the installed Bambu Studio ------------------------------------------------------------------
@needs_profiles
def test_every_offered_printer_resolves_from_the_installed_presets() -> None:
    for printer in BAMBU_PRINTERS.values():
        machine = REAL_PROFILES.resolve(printer.preset)
        resolved = bambu3mf.printer_from_profiles(printer, machine)
        assert resolved.printable_area == printer.printable_area, printer.preset
        assert resolved.printable_height == printer.printable_height, printer.preset
        from shapely.geometry import Polygon

        assert len(resolved.keep_out) == len(printer.keep_out), printer.preset
        assert all(Polygon(a).equals(Polygon(b)) for a, b in zip(resolved.keep_out, printer.keep_out)), printer.preset
        assert printer.preset in REAL_PROFILES.resolve(printer.filament_preset).get("compatible_printers", [])
        for _, process in printer.process_by_layer_height:
            assert printer.preset in REAL_PROFILES.resolve(process).get("compatible_printers", []), process


@needs_studio
@pytest.mark.parametrize("printer", [P1S, "Bambu Lab X1 Carbon 0.4 nozzle", "Bambu Lab P2S 0.4 nozzle",
                                     "Bambu Lab H2S 0.4 nozzle"])
def test_bambu_studio_loads_and_slices_every_plate(sail_kit, tmp_path, printer) -> None:
    out = tmp_path / "sail.3mf"
    report = write_bambu_project(sail_kit, printer, out)
    check = report["bambu_studio_check"]
    assert report["status"] == "WRITTEN" and check["status"] == "PASS", check
    assert report["validation"]["level"] == "BAMBU_STUDIO_CLI_VERIFIED"
    kit = json.loads((sail_kit / "print_kit.json").read_text(encoding="utf-8"))
    assert len(check["plates"]) == len(kit["assembly_back_to_front"]) == check["gcode_files"]
    assert check["layer_height_mm"] == pytest.approx(kit["printability"]["process"]["layer_height_mm"])
    for plate, row in zip(check["plates"], kit["assembly_back_to_front"]):
        assert plate["size_matches"] and plate["position_matches"] and plate["filament_changes"] == 0
        assert plate["bbox_mm"]["height"] == pytest.approx(row["stack_height_mm"], abs=0.01)
        # Bambu Studio's own per-plate record after slicing: this sheet only, in its exact reviewed color.
        assert plate["bambu_plate_objects"] == [Path(row["file_stl"]).stem]
        assert plate["bambu_colors"] == [row["color_hex"].upper()]


@needs_profiles
def test_a1_wrap_detection_corner_leaves_no_room_for_a_225_mm_sheet(sail_kit, tmp_path) -> None:
    # A1: 256 mm bed, wrap-detection zone x >= 226, y >= 224; with the 1 mm clearance a 225 mm
    # square cannot be placed, so the export reports every sheet instead of writing a project.
    report = write_bambu_project(sail_kit, "Bambu Lab A1 0.4 nozzle", tmp_path / "a1.3mf", studio=False)
    assert report["status"] == "NOT_WRITTEN" and not (tmp_path / "a1.3mf").exists()
    assert all("does not fit" in p for p in report["problems"])
