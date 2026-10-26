"""L3 print kit: final exports, assembly metadata, printability estimate, reproducibility.

Inputs are project-owned: the bow-face fixture of ``test_l0_core`` and
``fixtures/l3_print_kit/sailboat_512.png`` (original; 0.39 mm and 0.78 mm wave
slits at scale 1).  All stacks are planned at scale 1 (225 mm sheet) for speed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from layerforge_l1 import core
from layerforge_l1.core import review
from layerforge_l1.core.editing import apply_edit
from layerforge_l1.core.model import save_stack
from layerforge_l1.core.printkit import (
    PrintKitError,
    PrintProcess,
    assess_printability,
    build_print_kit,
    step_body,
    verify_checksums,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_l0_core import CONFIG, PALETTE  # noqa: E402
from test_reveal_planner import _fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SAILBOAT = ROOT / "fixtures" / "l3_print_kit" / "sailboat_512.png"
THIN_SLIT_MM = [153.125, 34.668]  # 1 px slit in the front sea sheet
SLIT_MM = [63.281, 48.047]  # 2 px slit
SLIT_Y_MM = 48.046875


@pytest.fixture(scope="module")
def sail_dir(tmp_path_factory) -> Path:
    stack, _ = core.plan_stack(np.asarray(Image.open(SAILBOAT).convert("RGB")), CONFIG, name="sailboat")
    path = tmp_path_factory.mktemp("sail") / "stack"
    save_stack(stack, path)
    return path


@pytest.fixture(scope="module")
def sail_kit(sail_dir, tmp_path_factory):
    out = tmp_path_factory.mktemp("kit") / "kit"
    return out, build_print_kit(sail_dir, out)


@pytest.fixture(scope="module")
def bow() -> core.SheetStack:
    return core.plan_stack(PALETTE[_fixture()], CONFIG, name="bow_face")[0]


def _findings(report, severity=None):
    return [f for f in report["features"] if severity is None or f["severity"] == severity]


# -- process parameters ----------------------------------------------------------------------
def test_process_defaults_are_labelled_and_json_is_validated(tmp_path) -> None:
    default = PrintProcess()
    assert default.source.startswith("ASSUMPTION")
    preset = PrintProcess.from_json(ROOT / "fixtures" / "print_processes" / "generic_fdm_0.4mm.json")
    assert preset.min_opening_mm == default.min_opening_mm and preset.nozzle_diameter_mm == 0.4
    for data, message in [
        ({"nozle_diameter_mm": 0.4}, "unknown keys"),
        ({"layer_height_mm": 0}, "positive"),
        ({"min_opening_mm": 0.2}, "at least nozzle"),
        ({"min_positive_feature_mm": 0.3}, "at least line_width"),
    ]:
        path = tmp_path / "process.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(PrintKitError, match=message):
            PrintProcess.from_json(path)


# -- printability estimate -------------------------------------------------------------------
def test_negative_and_thin_features_are_classified_on_exact_geometry(bow) -> None:
    front, back = bow.sheets[-1].id, bow.sheets[0].id
    vector = {"precision": "vector"}
    edited = bow
    for y, width in ((20.0, 0.3), (24.0, 0.6), (28.0, 2.0)):  # visible slots in the front sheet
        edited, _ = apply_edit(edited, {"op": "subtract", "sheet": front, "shape": {
            "type": "bar", "from_mm": [30.0, y], "to_mm": [60.0, y], "width_mm": width}, **vector})
    edited, _ = apply_edit(edited, {"op": "subtract", "sheet": back, "shape": {  # hidden behind the front sheets
        "type": "bar", "from_mm": [150.0, 30.0], "to_mm": [180.0, 30.0], "width_mm": 0.3}, **vector})
    before = review.geometry_fingerprint(edited)
    report = assess_printability(edited, PrintProcess())
    assert review.geometry_fingerprint(edited) == before  # reported, never changed
    slots = {round(f["at_mm"][1]): f for f in report["features"] if f["sheet_id"] == front}
    assert slots[20]["severity"] == "UNSUPPORTED" and slots[20]["width_mm"] == pytest.approx(0.3, abs=0.011)
    assert slots[24]["severity"] == "RISK" and slots[24]["width_mm"] == pytest.approx(0.6, abs=0.011)
    assert 28 not in slots  # 2 mm opening: fine
    hidden = [f for f in report["features"] if f["sheet_id"] == back]
    assert len(hidden) == 1 and hidden[0]["severity"] == "INFO" and hidden[0]["visible_area_mm2"] == 0
    assert report["status"] == "UNSUPPORTED_FEATURES" and report["counts"]["RISK"] == 1


def test_thin_material_build_volume_and_layer_quantization(bow) -> None:
    front = bow.sheets[-1].id
    edited, _ = apply_edit(bow, {"op": "add", "sheet": front, "precision": "vector", "shape": {
        "type": "bar", "from_mm": [90.0, 95.0], "to_mm": [135.0, 95.0], "width_mm": 0.3}})  # wire across the face window
    edited, _ = apply_edit(edited, {"op": "thickness", "sheet": front, "mm": 1.5})
    report = assess_printability(edited, PrintProcess(bed_x_mm=220.0, bed_y_mm=220.0))
    kinds = {(f["kind"], f["severity"]) for f in report["features"]}
    assert ("thin_material", "UNSUPPORTED") in kinds
    assert ("exceeds_build_volume", "UNSUPPORTED") in kinds
    assert ("height_not_layer_multiple", "INFO") in kinds
    assert not any(s["fits_build_volume"] for s in report["sheets"])


def test_process_parameters_change_the_verdict(sail_dir) -> None:
    stack = core.load_stack(sail_dir)
    default = assess_printability(stack, PrintProcess())
    widths = sorted((f["width_mm"], f["severity"]) for f in _findings(default) if f["kind"] == "opening_may_close")
    assert widths[0][1] == "UNSUPPORTED" and widths[0][0] == pytest.approx(0.39, abs=0.011)
    assert widths[1][1] == "RISK" and widths[1][0] == pytest.approx(0.78, abs=0.011)
    fine = PrintProcess(name="fine_0.25mm", nozzle_diameter_mm=0.25, line_width_mm=0.27,
                        min_opening_mm=0.5, min_positive_feature_mm=0.54)
    finer = assess_printability(stack, fine)
    assert [f["severity"] for f in _findings(finer) if f["kind"] == "opening_may_close"] == ["RISK"]
    assert finer["status"] == "RISKS_REPORTED"


# -- the kit ---------------------------------------------------------------------------------
def test_kit_contents_status_levels_and_comparisons(sail_kit, sail_dir) -> None:
    out, kit = sail_kit
    assert kit["status"] == {"cad": "CAD_VALID", "fdm_estimate": "UNSUPPORTED_FEATURES", "physical": "NOT_VERIFIED"}
    assert kit["cad_problems"] == []
    stack = core.load_stack(sail_dir)
    names = stack.export_names()
    rows = kit["assembly_back_to_front"]
    assert [Path(r["file_stl"]).stem for r in rows] == names
    assert [r["order_front_to_back"] for r in rows] == list(range(len(rows) - 1, -1, -1))
    assert [r["color_hex"] for r in rows] == [s.color for s in stack.sheets]
    assert rows[1]["assembly_z0_mm"] == pytest.approx(1.6) and kit["total_stack_height_mm"] == pytest.approx(1.6 * len(rows))
    for row in rows:
        assert (out / row["file_step"]).is_file() and (out / row["file_stl"]).is_file()
        assert row["stl_bounds_mm"][0][2] == pytest.approx(0.0) and row["print_placement"]["as_exported"]
    repeated = [c for c, e in kit["filament_by_color"].items() if len(e["sheets"]) > 1]
    assert repeated == ["#AAD7F5"]  # sky on two depths
    cmp = kit["comparisons"]
    assert cmp["approved_artwork_vs_reviewed_stack"]["front_view_mismatched_px"] == 0
    assert cmp["approved_artwork_vs_reopened_cad_front_view"] == {
        "mismatched_px": 0, "stack_mismatch_px": 0, "preview_vs_reviewed_px": 0, "source_comparison_mismatched_px": 0}
    for row in cmp["exported_vs_reopened_cad"].values():
        assert row["step_solids"] == 1 and row["step_valid"] and row["stl_watertight"] and row["stl_px_diff"] == 0
        assert abs(row["step_volume_vs_expected_pct"]) <= 0.01
    for name in ("assembled_front.png", "assembled_side.png", "exploded_labelled.png", "source_comparison.png"):
        assert (out / "previews" / name).is_file()
    assert sorted(p.stem for p in (out / "previews" / "printability").iterdir()) == sorted(names)
    guide = (out / "PRINT_AND_STACK_GUIDE.md").read_text(encoding="utf-8")
    assert "Physical: **NOT_VERIFIED**" in guide and "UNSUPPORTED" in guide and "no frame, housing or glue" in guide
    assert kit["not_included"]["3mf"].startswith("not produced")
    assert verify_checksums(out) == []
    (out / "print_kit.json").write_text("{}", encoding="utf-8")
    assert verify_checksums(out) == ["print_kit.json"]
    (out / "print_kit.json").write_text(json.dumps(kit, indent=2) + "\n", encoding="utf-8")


def test_kit_export_is_reproducible(sail_kit, sail_dir, tmp_path) -> None:
    out, kit = sail_kit
    again = build_print_kit(sail_dir, tmp_path / "again")
    assert again == kit
    for row in kit["assembly_back_to_front"]:
        assert (out / row["file_stl"]).read_bytes() == (tmp_path / "again" / row["file_stl"]).read_bytes()
        assert step_body(out / row["file_step"]) == step_body(tmp_path / "again" / row["file_step"])
    # The kit's own stack copy rebuilds the same solids.
    third = build_print_kit(out / "stack", tmp_path / "third")
    assert third["assembly_back_to_front"] == kit["assembly_back_to_front"]


def test_invalid_or_unsafe_requests_are_refused(sail_dir, sail_kit, tmp_path, bow) -> None:
    with pytest.raises(PrintKitError, match="not empty"):
        build_print_kit(sail_dir, sail_kit[0])
    broken, _ = apply_edit(bow, {"op": "subtract", "sheet": bow.sheets[0].id,
                                 "shape": {"type": "circle", "center_mm": [2.0, 100.0], "radius_mm": 3.0}})
    save_stack(broken, tmp_path / "broken")
    with pytest.raises(PrintKitError, match="not valid"):
        build_print_kit(tmp_path / "broken", tmp_path / "kit")
    assert not (tmp_path / "kit").exists()


def test_edited_sailboat_with_rim_resolves_findings_by_explicit_edits(sail_dir, tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    stack, _ = review.start_review(sail_dir, project)
    front, back = stack.sheets[-1].id, stack.sheets[0].id
    x0, x1 = 60 + 32, 200 + 32  # slit pixel columns 60..199 on the 32 px perimeter offset
    mm = stack.grid.mm_per_pixel
    for op in (
        {"op": "fill_hole", "sheet": front, "at_mm": THIN_SLIT_MM},  # user removes the 0.39 mm slit
        {"op": "subtract", "sheet": front, "shape": {  # and widens the 0.78 mm slit to exactly 1.0 mm
            "type": "bar", "from_mm": [x0 * mm, SLIT_Y_MM], "to_mm": [x1 * mm, SLIT_Y_MM], "width_mm": 1.0}},
        {"op": "accept_artwork"},
        {"op": "rim", "sheet": back, "enabled": True, "width_mm": 6.0, "total_height_mm": 2.4},
    ):
        _, entry = review.apply_to_project(project, op)
    assert entry["validation_status"] == "PASS"
    from layerforge_l1 import cli

    monkeypatch.setattr(sys, "argv", ["layerforge_l1", "stack-kit", "--stack", str(project), "--output",
                                      str(tmp_path / "kit"), "--process",
                                      str(ROOT / "fixtures" / "print_processes" / "generic_fdm_0.4mm.json")])
    assert cli.main() == 0
    kit = json.loads((tmp_path / "kit" / "print_kit.json").read_text(encoding="utf-8"))
    assert kit["status"] == {"cad": "CAD_VALID", "fdm_estimate": "NO_RISKS_FOUND", "physical": "NOT_VERIFIED"}
    assert kit["review"] == {"edits": 4, "status": "REVIEWED_VALID"}
    assert kit["comparisons"]["approved_artwork_vs_reviewed_stack"]["deviation_from_source_artwork_px"] > 0
    rows = kit["assembly_back_to_front"]
    assert rows[0]["rim"] == {"width_mm": 6.0, "total_height_mm": 2.4} and rows[1]["assembly_z0_mm"] == pytest.approx(2.4)
    assert rows[0]["stl_bounds_mm"][1][2] == pytest.approx(2.4, abs=1e-3)
    assert kit["previews"]["side_section"]["total_height_mm"] == pytest.approx(2.4 + 1.6 * (len(rows) - 1))
    exact = kit["comparisons"]["reviewed_stack_vs_exported_geometry"][rows[-1]["file_stl"].split("/")[-1][:-4]]
    assert exact["stl_vs_exact_geometry_area_mm2"] < 0.01
