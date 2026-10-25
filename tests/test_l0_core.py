"""L0 core API: planner integration, manifest, validation, CAD, previews, CLI.

Uses the original synthetic bow-and-face fixture from ``test_reveal_planner``
(no third-party artwork) at scale 1 (576 px grid, 225 mm sheet) for speed.
The full-size accepted reference is checked by ``stack-reference``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from layerforge_l1 import core
from layerforge_l1.connectivity_repair import audit_edge_connectivity
from layerforge_l1.hidden_width_repair import WidthRepairConfig, audit_mask_at_physical_scale
from layerforge_l1.core import reference
from layerforge_l1.core.model import MANIFEST_NAME, import_planner_output
from layerforge_l1.core.validation import cutout_hole_count
from layerforge_l1.reveal_planner import RevealPlanner

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reveal_planner import _fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PALETTE = np.array([[150, 200, 235], [20, 20, 20], [240, 200, 40], [200, 50, 50], [250, 250, 250]], np.uint8)
CONFIG = core.PlanningConfig(scale=1)


@pytest.fixture(scope="module")
def target() -> np.ndarray:
    return PALETTE[_fixture()]


@pytest.fixture(scope="module")
def planned(target):
    return core.plan_stack(target, CONFIG, name="bow_face")


@pytest.fixture(scope="module")
def stack(planned) -> core.SheetStack:
    return planned[0]


@pytest.fixture(scope="module")
def exported(stack, tmp_path_factory):
    out = tmp_path_factory.mktemp("cad")
    manifest = core.export_stack(stack, out)
    check, stl_masks = core.revalidate_cad(stack, out)
    return out, manifest, check, stl_masks


def _copy(stack: core.SheetStack, **changes) -> core.SheetStack:
    sheets = [replace(s, retained=s.retained.copy(), visible=s.visible.copy()) for s in stack.sheets]
    fields = dict(name=stack.name, grid=stack.grid, sheets=sheets, target_rgb=stack.target_rgb,
                  frame_color=stack.frame_color, provenance=dict(stack.provenance))
    fields.update(changes)
    return core.SheetStack(**fields)


# -- planner integration ---------------------------------------------------------
def test_plan_stack_reproduces_the_direct_planner_sequence(target, stack) -> None:
    labels = _fixture()
    planner = RevealPlanner(labels, CONFIG.reveal_config(None))
    assert planner.closure()["feasible"]
    plan = planner.beam_search()
    colors, reveals, _ = planner.eliminate_sheets(plan["front_to_back_colors"], plan["reveals"])
    sheets = planner.materialize(reveals)
    assert len(stack.sheets) == len(sheets) == 6
    for mine, direct, color in zip(stack.front_to_back, sheets, colors):
        assert np.array_equal(mine.retained, direct["physical_mask"])
        assert np.array_equal(mine.visible, direct["visible_mask"])
        assert mine.color == core.model.hex_color(PALETTE[color])


def test_regeneration_is_deterministic(target, stack) -> None:
    again, _ = core.plan_stack(target, CONFIG, name="bow_face")
    assert reference.determinism(stack, again)


def test_layer_order_ids_z_and_repeated_colors(planned) -> None:
    stack, report = planned
    assert [s.color for s in stack.front_to_back] == report["front_to_back_colors"]
    assert [s.id for s in stack.sheets] == [f"layer_{i:02d}_{s.color[1:].lower()}" for i, s in enumerate(stack.sheets)]
    assert stack.assembly_z_mm() == pytest.approx([i * CONFIG.thickness_mm for i in range(6)])
    validation = core.validate_stack(stack)
    assert validation["repeated_colors"] == ["#141414"]
    assert [s.color for s in stack.sheets].count("#141414") == 2


# -- validation ------------------------------------------------------------------
def test_validation_passes_with_exact_reconstruction(stack) -> None:
    validation = core.validate_stack(stack)
    assert validation["status"] == "PASS", validation["failures"]
    assert validation["reconstruction"]["mismatched_pixels"] == 0
    assert validation["reconstruction"]["visible_mask_disagreement_pixels"] == 0
    for row in validation["sheets"]:
        assert row["four_neighbor"] == "PASS" and row["width_status"] == "PASS"
        assert row["retained_components_4"] == 1 and row["shared_perimeter_complete"]
    assert validation["outer_sheet_mm"] == [225.0, 225.0]
    assert validation["artwork_grid_mm"] == pytest.approx([200.0, 200.0])


def test_floating_island_is_detected(stack) -> None:
    broken = _copy(stack)
    back = broken.sheets[0]  # full sheet: carve a moat around a central island
    cv2.circle(back.retained.view(np.uint8), (288, 288), 60, 0, 6)
    validation = core.validate_stack(broken)
    row = validation["sheets"][0]
    assert row["four_neighbor"] == "FAIL" and row["retained_components_4"] == 2
    assert validation["status"] == "FAIL"


def test_sub_width_neck_is_detected() -> None:
    grid = core.StackGrid.for_planner(225.0, 1)
    retained = grid.perimeter_mask()
    retained[250:330, 250:330] = True  # 31 mm square blob
    retained[288, 32:250] = True  # joined to the perimeter by a 0.39 mm neck
    sheet = core.Sheet("layer_00_000000", "#000000", retained, np.zeros_like(retained), 1.6)
    validation = core.validate_stack(core.SheetStack("neck", grid, [sheet]))
    row = validation["sheets"][0]
    assert row["four_neighbor"] == "PASS"
    # The unchanged validator's non-PASS status for orphan cores.
    assert row["width_status"] == "CONFLICT_REQUIRES_USER_DECISION" and row["orphan_structural_cores"] >= 1
    assert not row["pass"] and validation["status"] == "FAIL"


def test_diagonal_only_contact_is_a_floating_island() -> None:
    # Replaces the deleted fox-report check (layer 12: one 8-neighbour component,
    # 15 four-neighbour orphans): a corner touch must not count as attachment.
    grid = core.StackGrid.for_planner(225.0, 1)
    p = grid.perimeter_pixels
    retained = grid.perimeter_mask()
    retained[p : p + 40, 100:110] = True  # 3.9 mm arm attached to the perimeter
    retained[p + 40 : p + 120, 110:190] = True  # touches the arm only at one corner
    edge, _ = audit_edge_connectivity(retained)
    assert edge["eight_neighbor_components"] == 1
    assert edge["four_neighbor_orphan_components"] == 1 and edge["four_neighbor_status"] == "FAIL"
    sheet = core.Sheet("layer_00_000000", "#000000", retained, np.zeros_like(retained), 1.6)
    validation = core.validate_stack(core.SheetStack("diagonal", grid, [sheet]))
    row = validation["sheets"][0]
    assert row["four_neighbor"] == "FAIL" and row["retained_components_4"] == 2
    assert validation["status"] == "FAIL"


def test_planned_sheets_pass_the_physical_scale_audit(stack) -> None:
    # Replaces the turtle stored-artifact tests: the same 4x physical-scale audit at
    # 225 mm, run on freshly planned sheets of the project-owned fixture.
    width = WidthRepairConfig(physical_size_mm=225.0, validation_scale=4)
    p = stack.grid.perimeter_pixels
    for sheet in stack.sheets:
        audit, _ = audit_mask_at_physical_scale(sheet.retained, sheet.visible[p:-p, p:-p], repair=width)
        assert audit["edge"]["four_neighbor_status"] == "PASS"
        assert audit["width"]["status"] == "PASS"
        assert audit["width"]["orphan_structural_core_components"] == 0
        assert audit["width"]["thin_decorative_visible_components"] == 0
    neck = stack.grid.perimeter_mask()
    neck[250:330, 250:330] = True
    neck[288, 32:250] = True
    audit, _ = audit_mask_at_physical_scale(neck, np.zeros_like(neck[p:-p, p:-p]), repair=width)
    assert audit["width"]["status"] != "PASS"


def test_broken_perimeter_and_reconstruction_errors_are_detected(stack) -> None:
    broken = _copy(stack)
    broken.sheets[-1].retained[0:5, 100:110] = False
    front = broken.front_to_back[1]
    ys, xs = np.nonzero(front.visible)
    front.retained[ys[:50], xs[:50]] = False
    validation = core.validate_stack(broken)
    assert not validation["sheets"][-1]["shared_perimeter_complete"]
    assert validation["reconstruction"]["mismatched_pixels"] > 0
    assert validation["status"] == "FAIL"


# -- manifest -------------------------------------------------------------------
def test_save_load_round_trip(stack, tmp_path) -> None:
    path = core.save_stack(stack, tmp_path / "project")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["schema"] == core.SCHEMA and manifest["schema_version"] == core.SCHEMA_VERSION
    assert [row["order_back_to_front"] for row in manifest["sheets"]] == list(range(6))
    loaded = core.load_stack(tmp_path / "project")
    assert reference.determinism(stack, loaded)
    assert [s.color for s in loaded.sheets] == [s.color for s in stack.sheets]
    assert np.array_equal(loaded.target_rgb, stack.target_rgb)
    assert loaded.grid == stack.grid and loaded.assembly_z_mm() == stack.assembly_z_mm()
    assert loaded.provenance["planning"]["planner"] == core.planning.PLANNER_ID


def _rewrite(path: Path, edit) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    edit(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize(
    "edit, message",
    [
        (lambda m: m.update(schema_version="2.0.0"), "unsupported schema_version"),
        (lambda m: m.update(schema="something.else"), "schema is"),
        (lambda m: m["sheets"].reverse(), "back to front"),
        (lambda m: m["sheets"][0].update(retained_bits_sha256="0" * 64), "recorded hash"),
        (lambda m: m["sheets"][1].update(assembly_z_mm=99.0), "assembly_z_mm"),
        (lambda m: m["sheets"][1].update(color="red"), "color must be"),
        (lambda m: m["grid"].update(perimeter_pixels=7), "does not match the planner grid"),
    ],
)
def test_invalid_manifests_are_rejected(stack, tmp_path, edit, message) -> None:
    path = core.save_stack(stack, tmp_path / "project")
    _rewrite(path, edit)
    with pytest.raises(core.ManifestError, match=message):
        core.load_stack(path)


def test_missing_mask_and_vector_manifest_errors(stack, tmp_path) -> None:
    path = core.save_stack(stack, tmp_path / "project")
    (tmp_path / "project" / "layers" / stack.sheets[2].id / "retained_material_mask.png").unlink()
    with pytest.raises(core.ManifestError, match="missing mask file"):
        core.load_stack(path)
    with pytest.raises(core.ManifestError, match="layerforge_l1 build"):
        core.load_stack(ROOT / "fixtures" / "orbit_bloom_v1" / "layer_stack.json")
    with pytest.raises(core.ManifestError, match="no sheet-stack manifest"):
        core.load_stack(tmp_path / "nowhere")


def test_legacy_planner_directory_migrates(stack, tmp_path) -> None:
    core.save_stack(stack, tmp_path / "legacy")
    (tmp_path / "legacy" / MANIFEST_NAME).unlink()  # pre-L0 runs wrote layers/ only
    Image.fromarray(stack.target_rgb).save(tmp_path / "target.png")
    migrated = import_planner_output(tmp_path / "legacy" / "layers", thickness_mm=1.6, target_image=tmp_path / "target.png")
    assert reference.determinism(stack, migrated)
    assert migrated.grid == stack.grid
    assert core.validate_stack(migrated)["status"] == "PASS"
    core.save_stack(migrated, tmp_path / "migrated")
    assert reference.determinism(stack, core.load_stack(tmp_path / "migrated"))
    (tmp_path / "legacy" / "layers" / stack.sheets[1].id).rename(tmp_path / "legacy" / "layers" / "layer_09_000000")
    with pytest.raises(core.ManifestError, match="indices"):
        import_planner_output(tmp_path / "legacy" / "layers")


# -- invalid inputs -------------------------------------------------------------
def test_invalid_planning_inputs(target) -> None:
    with pytest.raises(core.StackError, match="square"):
        core.plan_stack(target[:, :500], CONFIG)
    with pytest.raises(core.StackError, match="side dividing"):
        core.plan_stack(target[:500, :500], CONFIG)
    with pytest.raises(core.StackError, match="uint8 RGB"):
        core.plan_stack(target.astype(np.float32), CONFIG)
    with pytest.raises(core.StackError, match="more than 255"):
        noisy = np.random.default_rng(0).integers(0, 255, (512, 512, 3), dtype=np.uint8)
        core.plan_stack(noisy, CONFIG)
    with pytest.raises(core.StackError, match="frame color"):
        core.plan_stack(target, replace(CONFIG, frame_color="#123456"))
    with pytest.raises(core.StackError, match="beam_score"):
        core.plan_stack(target, replace(CONFIG, beam_score="best"))


def test_invalid_stack_construction(stack) -> None:
    with pytest.raises(core.StackError, match="duplicate"):
        _copy(stack, sheets=[stack.sheets[0], stack.sheets[0]])
    with pytest.raises(core.StackError, match="bool"):
        _copy(stack, sheets=[replace(stack.sheets[0], retained=np.ones((10, 10), bool))])
    with pytest.raises(core.StackError, match="at least one"):
        _copy(stack, sheets=[])


# -- CAD and previews -------------------------------------------------------------
def test_cad_export_read_back(stack, exported) -> None:
    out, manifest, _, _ = exported
    for sheet, row, entry in zip(stack.sheets, manifest["layers"], manifest["assembly_back_to_front"]):
        assert row["step_solids"] == 1 and row["step_valid"]
        assert row["stl_watertight"] and row["stl_components"] == 1
        assert row["stl_bounds_mm"] == [[0.0, 0.0, 0.0], [225.0, 225.0, 1.6]]
        assert row["holes"] == cutout_hole_count(sheet.retained)
        assert abs(row["volume_vs_area_x_thickness_pct"]) < 1e-6
        assert (out / entry["step"]).is_file() and (out / entry["stl"]).is_file()
    assert [e["assembled_z0_mm"] for e in manifest["assembly_back_to_front"]] == stack.assembly_z_mm()


def test_cutouts_are_through_holes_in_the_solid(stack, exported) -> None:
    out, manifest, _, _ = exported
    holed = [i for i, s in enumerate(stack.sheets) if cutout_hole_count(s.retained)]
    assert holed
    for index in holed:
        footprint = core.stl_footprint(out / "stl" / f"{stack.sheets[index].id}.stl")
        assert sum(len(p.interiors) for p in core.cad.polygons(footprint)) == cutout_hole_count(stack.sheets[index].retained)
        # Volume equals footprint area x thickness: holes run through the full sheet.
        row = manifest["layers"][index]
        assert row["stl_volume_mm3"] == pytest.approx(footprint.area * 1.6, rel=1e-6)
        assert row["step_volume_mm3"] == pytest.approx(row["stl_volume_mm3"], rel=1e-6)


def test_reopened_cad_matches_masks_and_passes_validators(exported) -> None:
    _, _, check, _ = exported
    assert check["pass"]
    assert check["stack_mismatch_px"] == 0
    assert check["reconstruction_from_stl"]["mismatched_pixels"] == 0
    for row in check["layers"]:
        assert row["stl_px_diff_from_mask"] == 0 and row["geometry_px_diff_from_mask"] == 0


def test_previews_from_reopened_stl_match_planned_geometry(stack, exported, tmp_path) -> None:
    _, _, _, stl_masks = exported
    report = core.generate_previews(stack, tmp_path, stl_masks, "reopened STL")
    assert report["assembled_px_diff_from_planned"] == 0
    assert set(report["sheet_mask_px_diff_from_planned"].values()) == {0}
    assert report["source_comparison_mismatched_pixels"] == 0
    for name in report["files"]:
        assert (tmp_path / name).is_file()
    front = np.asarray(Image.open(tmp_path / "assembled_front.png"))
    p = stack.grid.perimeter_pixels
    assert np.array_equal(front[p:-p, p:-p], stack.target_rgb)


def test_preview_detects_geometry_that_differs_from_plan(stack, tmp_path) -> None:
    masks = [s.retained.copy() for s in stack.sheets]
    masks[-1][200:220, 200:220] = ~masks[-1][200:220, 200:220]
    report = core.generate_previews(stack, tmp_path, masks, "tampered")
    assert report["assembled_px_diff_from_planned"] > 0


# -- CLI ------------------------------------------------------------------------
def test_cli_import_validate_preview(stack, tmp_path, monkeypatch, capsys) -> None:
    from layerforge_l1 import cli

    core.save_stack(stack, tmp_path / "legacy")
    (tmp_path / "legacy" / MANIFEST_NAME).unlink()
    Image.fromarray(stack.target_rgb).save(tmp_path / "target.png")

    def run(*argv: str) -> int:
        monkeypatch.setattr(sys, "argv", ["layerforge-l1", *argv])
        return cli.main()

    assert run("stack-import", "--layers", str(tmp_path / "legacy" / "layers"), "--output", str(tmp_path / "s"),
               "--target", str(tmp_path / "target.png")) == 0
    assert run("stack-validate", "--stack", str(tmp_path / "s"), "--report", str(tmp_path / "v.json")) == 0
    assert json.loads((tmp_path / "v.json").read_text(encoding="utf-8"))["status"] == "PASS"
    assert run("stack-preview", "--stack", str(tmp_path / "s"), "--output", str(tmp_path / "p")) == 0
    assert (tmp_path / "p" / "exploded.png").is_file()
    assert run("stack-validate", "--stack", str(tmp_path / "missing")) == 2
    assert "no sheet-stack manifest" in capsys.readouterr().err
