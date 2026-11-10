"""Rectangular sheets, step 2: STEP/STL export, read-back and the print kit.

The exported outline is exactly the requested width x height.  When the shorter side's raster differs from
it (less than half a native pixel), the two outer edges of that side move by ``grid.edge_mm`` each: solid
perimeter band only, never an artwork pixel, a cutout or visible material.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from shapely.affinity import translate

from layerforge_l1.core.bambu3mf import BAMBU_PRINTERS, place_on_bed
from layerforge_l1.core.cad import (export_stack, physical_outline, raster_outline, rasterize_geometry, revalidate_cad,
                                    sheet_geometry, stl_footprint)
from layerforge_l1.core.model import StackGrid, save_stack
from layerforge_l1.core.planning import PlanningConfig, plan_stack
from layerforge_l1.core.printkit import PrintProcess, assess_printability, build_print_kit
from layerforge_l1.core.validation import validate_stack

from test_rect_grid import _artwork

# (width, height): exact raster (landscape, portrait), trimmed edge (150 x 250), widened edge (200 x 150.1).
SIZES = [(200.0, 150.0), (100.0, 200.0), (150.0, 250.0), (200.0, 150.1)]
_stacks: dict = {}


def _stack(width: float, height: float):
    if (width, height) not in _stacks:
        grid = StackGrid.for_planner(width, 1, height)
        stack, _ = plan_stack(_artwork(grid.artwork_rows, grid.artwork_pixels),
                              PlanningConfig(outer_sheet_mm=width, outer_sheet_height_mm=height, scale=1))
        assert validate_stack(stack)["status"] == "PASS"
        _stacks[(width, height)] = stack
    return _stacks[(width, height)]


def test_edge_correction_is_sub_half_pixel_and_moves_only_the_solid_band() -> None:
    for width, height in SIZES:
        stack = _stack(width, height)
        grid = stack.grid
        dx, dy = grid.edge_mm
        assert (dx, dy) == (0.0, 0.0) or (width, height) in ((150.0, 250.0), (200.0, 150.1))
        for index, sheet in enumerate(stack.sheets):
            raster = sheet_geometry(stack, index)
            outline = physical_outline(grid, raster)
            assert outline.bounds == pytest.approx((0.0, 0.0, width, height), abs=1e-9)
            # Undone exactly, and on the pixel grid the correction is invisible: the shifted-back outline
            # without undoing it rasterizes to the same mask (it is under half a pixel, in the solid band).
            assert raster_outline(grid, outline).symmetric_difference(raster).area < 1e-9
            assert np.array_equal(rasterize_geometry(translate(outline, -dx, -dy), grid), sheet.retained)
            # Only the outer band differs.
            band = grid.perimeter_pixels * grid.mm_per_pixel
            inner = translate(raster, dx, dy).intersection(
                translate(raster.envelope.buffer(-band, join_style=2), dx, dy))
            assert outline.intersection(inner.envelope).symmetric_difference(inner).area < 1e-9
        assert grid.perimeter_band_mm >= grid.perimeter_pixels * grid.mm_per_pixel - grid.mm_per_pixel / 2


@pytest.mark.parametrize("width, height", SIZES)
def test_rectangular_export_reads_back_at_the_requested_size(tmp_path, width: float, height: float) -> None:
    import cadquery as cq

    stack = _stack(width, height)
    manifest = export_stack(stack, tmp_path / "cad")
    assert manifest["outer_sheet_mm"] == width and manifest["outer_sheet_height_mm"] == height
    assert manifest["edge_correction_mm_per_side"] == list(stack.grid.edge_mm)
    check, masks = revalidate_cad(stack, tmp_path / "cad")
    assert check["pass"], check
    assert check["stack_mismatch_px"] == 0 and check["reconstruction_from_stl"]["mismatched_pixels"] == 0
    validation = validate_stack(stack)
    for row, sheet, mask, vrow, layer in zip(check["layers"], stack.sheets, masks, validation["sheets"],
                                             manifest["layers"]):
        assert row["stl_px_diff_from_mask"] == 0 and np.array_equal(mask, sheet.retained)
        assert row["stl_outline_mm"] == pytest.approx([width, height], abs=1e-3)
        assert row["width_1_2mm"] == "PASS" and row["perimeter_complete"] and row["four_neighbor"] == "PASS"
        assert row["cutout_holes"] == vrow["cutout_holes"] == layer["holes"]  # true holes survive export
        assert layer["step_solids"] == 1 and layer["step_valid"] and layer["stl_watertight"]
        assert abs(layer["traced_area_vs_mask_pct"]) < 0.5 and abs(layer["step_volume_vs_expected_pct"]) < 0.1
        # Every layer: the same outer size, at z = 0, in STL and STEP.
        (x0, y0, z0), (x1, y1, _) = layer["stl_bounds_mm"]
        assert (x0, y0, z0) == pytest.approx((0, 0, 0), abs=1e-3) and (x1, y1) == pytest.approx((width, height), abs=1e-3)
        step = cq.importers.importStep(str(tmp_path / "cad" / "step" / f"{layer['layer']}.step")).val().BoundingBox()
        assert (step.xmin, step.ymin, step.xmax, step.ymax) == pytest.approx((0, 0, width, height), abs=1e-6)
    assert sum(r["cutout_holes"] for r in check["layers"]) > 0


def test_read_back_detects_a_wrong_outline(tmp_path) -> None:
    """A sheet exported at the raster size instead of the requested size fails the read-back."""

    stack = _stack(150.0, 250.0)
    export_stack(stack, tmp_path / "cad")
    name = stack.export_names()[0]
    stl = tmp_path / "cad" / "stl" / f"{name}.stl"
    import trimesh

    mesh = trimesh.load_mesh(stl, file_type="stl")
    mesh.apply_scale([stack.grid.raster_mm[0] / 150.0, 1.0, 1.0])
    mesh.export(stl)
    check, _ = revalidate_cad(stack, tmp_path / "cad")
    assert not check["pass"] and check["layers"][0]["stl_outline_max_error_mm"] > 0.1
    assert stl_footprint(stl).bounds[2] == pytest.approx(stack.grid.raster_mm[0], abs=1e-3)


def test_printability_and_build_volume_use_both_dimensions() -> None:
    stack = _stack(150.0, 250.0)
    fits = assess_printability(stack, PrintProcess(bed_x_mm=256.0, bed_y_mm=256.0))
    assert not [f for f in fits["features"] if f["kind"] == "exceeds_build_volume"]
    rotated = assess_printability(stack, PrintProcess(bed_x_mm=260.0, bed_y_mm=160.0))  # fits turned 90 degrees
    assert not [f for f in rotated["features"] if f["kind"] == "exceeds_build_volume"]
    for bed in ((200.0, 240.0), (149.0, 300.0), (300.0, 149.0)):  # too short in some dimension either way round
        report = assess_printability(stack, PrintProcess(bed_x_mm=bed[0], bed_y_mm=bed[1]))
        big = [f for f in report["features"] if f["kind"] == "exceeds_build_volume"]
        assert len(big) == len(stack.sheets) and all(f["severity"] == "UNSUPPORTED" for f in big)
        assert "150.00 x 250.00" in big[0]["note"]
    # Bambu placement takes the footprint as exported (both dimensions, no rotation).
    a1, mini = BAMBU_PRINTERS["Bambu Lab A1 0.4 nozzle"], BAMBU_PRINTERS["Bambu Lab A1 mini 0.4 nozzle"]
    assert place_on_bed((150.0, 250.0), a1) is not None and place_on_bed((250.0, 150.0), a1) is not None
    assert place_on_bed((150.0, 250.0), mini) is None and place_on_bed((100.0, 200.0), mini) is None
    assert place_on_bed((100.0, 170.0), mini) is not None


def test_rectangular_print_kit(tmp_path) -> None:
    stack = _stack(150.0, 250.0)
    save_stack(stack, tmp_path / "stack")
    kit = build_print_kit(tmp_path / "stack", tmp_path / "kit", bambu_printer="Bambu Lab A1 0.4 nozzle")
    assert kit["status"]["cad"] == "CAD_VALID", kit["cad_problems"]
    assert kit["outer_sheet_mm"] == 150.0 and kit["outer_sheet_height_mm"] == 250.0
    assert kit["bambu_3mf"]["status"] == "WRITTEN", kit["bambu_3mf"]
    assert "150 x 250 mm outline (width x height)" in (tmp_path / "kit" / "PRINT_AND_STACK_GUIDE.md").read_text(encoding="utf-8")
    assert not [f for f in kit["printability"]["features"] if f["kind"] == "exceeds_build_volume"]
    saved = json.loads((tmp_path / "kit" / "print_kit.json").read_text(encoding="utf-8"))
    assert saved["outer_sheet_height_mm"] == 250.0
    # Previews keep the sheet's aspect (no square stretching).
    from PIL import Image

    sheet_png = next((tmp_path / "kit" / "previews" / "sheets").glob("*.png"))
    w, h = Image.open(sheet_png).size
    assert (w, h) == (round(1152 * stack.grid.shape[1] / stack.grid.shape[0]), 1152)
    # Too large for the A1 mini: the kit is still built, the Bambu project is refused with the reason.
    small = build_print_kit(tmp_path / "stack", tmp_path / "kit_mini", bambu_printer="Bambu Lab A1 mini 0.4 nozzle")
    assert small["bambu_3mf"]["status"] != "WRITTEN"
    assert any("does not fit" in p for p in small["bambu_3mf"]["problems"])


@pytest.mark.skipif(__import__("layerforge_l1.core.bambu3mf", fromlist=["x"]).find_profiles() is None,
                    reason="Bambu Studio system presets not installed")
def test_sheet_that_fits_only_turned_is_printable_but_no_ready_plate_is_claimed(tmp_path) -> None:
    """200 x 330 mm on the H2S (340 x 320 mm printable): it fits turned 90 degrees.  The sheet is printable
    (no build-volume finding, CAD valid, STL/STEP written unrotated), the estimate says to turn it, and no
    Bambu Studio project claims a ready plate."""

    grid = StackGrid.for_planner(200.0, 1, 330.0)
    stack, _ = plan_stack(_artwork(grid.artwork_rows, grid.artwork_pixels),
                          PlanningConfig(outer_sheet_mm=200.0, outer_sheet_height_mm=330.0, scale=1))
    save_stack(stack, tmp_path / "stack")
    kit = build_print_kit(tmp_path / "stack", tmp_path / "kit", PrintProcess(bed_x_mm=340.0, bed_y_mm=320.0),
                          bambu_printer="Bambu Lab H2S 0.4 nozzle")
    assert kit["status"]["cad"] == "CAD_VALID"
    kinds = {f["kind"] for f in kit["printability"]["features"]}
    assert "exceeds_build_volume" not in kinds and "turn_on_the_bed" in kinds
    assert kit["bambu_3mf"]["status"] == "NEEDS_MANUAL_ROTATION"
    assert any("only turned 90 degrees" in p for p in kit["bambu_3mf"]["problems"])
    assert not list((tmp_path / "kit").glob("*.3mf"))
    for row in kit["assembly_back_to_front"]:  # exported as planned, not rotated
        (x0, y0, _), (x1, y1, _) = row["stl_bounds_mm"]
        assert (x1 - x0, y1 - y0) == pytest.approx((200.0, 330.0), abs=1e-3)


def test_rectangular_sheet_at_scale_2_plans_validates_and_exports_exactly(tmp_path) -> None:
    """150 x 250 mm at processing scale 2 (twice the pixels per mm, the same millimetres): planned, validated
    with exact reconstruction, exported, read back against the masks and at the requested outer size."""

    width, height = 150.0, 250.0
    coarse, grid = StackGrid.for_planner(width, 1, height), StackGrid.for_planner(width, 2, height)
    assert grid.shape == (2 * coarse.shape[0], 2 * coarse.shape[1]) and grid.outer_mm == (width, height)
    assert grid.raster_mm == pytest.approx(coarse.raster_mm) and grid.edge_mm == pytest.approx(coarse.edge_mm)
    assert grid.mm_per_pixel == pytest.approx(coarse.mm_per_pixel / 2)
    target = _artwork(grid.artwork_rows, grid.artwork_pixels)
    stack, _ = plan_stack(target, PlanningConfig(outer_sheet_mm=width, outer_sheet_height_mm=height, scale=2))
    assert stack.grid == grid and all(s.retained.shape == grid.shape for s in stack.sheets)
    result = validate_stack(stack)
    assert result["status"] == "PASS", result["failures"]
    assert result["reconstruction"]["mismatched_pixels"] == 0
    assert result["outer_sheet_mm"] == [width, height] and result["raster_mm"] == pytest.approx(list(grid.raster_mm))
    assert all(r["shared_perimeter_complete"] and r["width_status"] == "PASS" for r in result["sheets"])
    manifest = export_stack(stack, tmp_path / "cad", step=True)
    check, masks = revalidate_cad(stack, tmp_path / "cad")
    assert check["pass"] and check["stack_mismatch_px"] == 0 and check["reconstruction_from_stl"]["mismatched_pixels"] == 0
    for row, layer, sheet, mask, v in zip(check["layers"], manifest["layers"], stack.sheets, masks, result["sheets"]):
        assert np.array_equal(mask, sheet.retained) and row["stl_px_diff_from_mask"] == 0
        assert row["stl_outline_mm"] == pytest.approx([width, height], abs=1e-3)
        assert row["cutout_holes"] == v["cutout_holes"] == layer["holes"] and layer["step_solids"] == 1 and layer["step_valid"]
        (x0, y0, _), (x1, y1, _) = layer["stl_bounds_mm"]
        assert (x0, y0, x1, y1) == pytest.approx((0.0, 0.0, width, height), abs=1e-3)
    assert sum(r["cutout_holes"] for r in check["layers"]) > 0
