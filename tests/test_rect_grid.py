"""Rectangular sheets, step 1: grid, model, planner and validators (square sheets are unchanged).

A rectangular grid keeps square pixels: the longer side has the square grid's pixels, the shorter
side whole native pixels, and the perimeter band is the same width on all four sides.
"""

from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pytest

from layerforge_l1.core.model import StackError, StackGrid, load_stack, save_stack
from layerforge_l1.core.planning import PlanningConfig, plan_stack
from layerforge_l1.core.validation import validate_stack
from layerforge_l1.reveal_planner import RevealConfig, physical_grid
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG


def _artwork(rows: int, cols: int) -> np.ndarray:
    """Original flat-color test art (no source image): a sky, a sun with a ring, a hill and posts."""

    yy, xx = np.mgrid[0:rows, 0:cols]
    rgb = np.empty((rows, cols, 3), np.uint8)
    rgb[:] = (40, 90, 160)
    sun = (yy - rows * 0.35) ** 2 + (xx - cols * 0.3) ** 2
    r = min(rows, cols) * 0.2
    rgb[sun < r * r] = (250, 200, 40)
    rgb[(sun < (r * 0.55) ** 2) & (sun > (r * 0.35) ** 2)] = (220, 60, 40)
    rgb[yy > rows * 0.75 - 0.15 * rows * np.sin(xx / cols * np.pi)] = (50, 140, 60)
    for k in range(3):
        x0 = int(cols * (0.55 + 0.12 * k))
        rgb[int(rows * 0.45):int(rows * 0.7), x0:x0 + max(8, cols // 40)] = (120, 70, 30)
    return rgb


def test_square_grids_are_unchanged() -> None:
    for mm, scale in ((60.0, 1), (225.0, 4)):
        grid = StackGrid.for_planner(mm, scale)
        assert grid.square and grid.outer_rows is None and grid.shape == (576 * scale,) * 2
        assert grid.outer_mm == (mm, mm) and grid.artwork_height_mm == grid.artwork_mm
        assert set(grid.to_json()) == {"outer_pixels", "perimeter_pixels", "outer_sheet_mm", "scale", "mm_per_pixel",
                                       "artwork_pixels", "artwork_mm", "raster_origin"}
        # A height equal to the width is a square sheet.
        assert StackGrid.for_planner(mm, scale, mm) == grid
        assert type(physical_grid(RevealConfig(outer_sheet_mm=mm, scale=scale))) is type(DEFAULT_CONFIG)
    assert "outer_rows" not in asdict(DEFAULT_CONFIG)
    assert "outer_sheet_height_mm" not in plan_stack.__globals__["_without_square_height"](
        asdict(PlanningConfig()))


@pytest.mark.parametrize("width, height, shape", [(200.0, 150.0, (432, 576)), (100.0, 200.0, (576, 288)),
                                                  (150.0, 250.0, (576, 346))])
def test_rectangular_grid_dimensions(width: float, height: float, shape: tuple[int, int]) -> None:
    grid = StackGrid.for_planner(width, 1, height)
    assert not grid.square and grid.shape == shape
    native_mm = max(width, height) / DEFAULT_CONFIG.outer_pixels
    # The outer sheet is the requested size; the raster's longer side is exact and its shorter side
    # rounded to a native pixel (square pixels; the same band on all sides); the edge correction is the
    # difference, split over the shorter side's two edges.
    assert grid.outer_mm == (width, height)
    w, h = grid.raster_mm
    assert max(w, h) == pytest.approx(max(width, height), abs=1e-9)
    assert abs(min(w, h) - min(width, height)) <= native_mm / 2 + 1e-9
    assert grid.mm_per_pixel == pytest.approx(native_mm)
    for edge, outer, raster in zip(grid.edge_mm, grid.outer_mm, grid.raster_mm):
        assert edge == pytest.approx((outer - raster) / 2, abs=1e-9) and abs(edge) <= native_mm / 4 + 1e-9
    assert grid.perimeter_mask().shape == shape and grid.artwork_rows == shape[0] - 64
    # Scale 2: twice the pixels per axis, the same millimetres.
    fine = StackGrid.for_planner(width, 2, height)
    assert fine.shape == (2 * shape[0], 2 * shape[1]) and fine.outer_mm == pytest.approx(grid.outer_mm)
    # Round trip through the manifest block, and the planner grid agrees.
    assert StackGrid.from_json(grid.to_json()) == grid
    assert grid.poc_config().outer_shape == shape


def test_old_square_manifest_blocks_still_load() -> None:
    block = {"outer_pixels": 576, "perimeter_pixels": 32, "outer_sheet_mm": 60.0, "scale": 1}
    assert StackGrid.from_json(block) == StackGrid(576, 32, 60.0, 1)


@pytest.mark.parametrize("width, height", [(200.0, 150.0), (100.0, 200.0)])
def test_rectangular_stack_plans_validates_and_round_trips(tmp_path, width: float, height: float) -> None:
    grid = StackGrid.for_planner(width, 1, height)
    target = _artwork(grid.artwork_rows, grid.artwork_pixels)
    stack, report = plan_stack(target, PlanningConfig(outer_sheet_mm=width, outer_sheet_height_mm=height, scale=1))
    assert stack.grid == grid and report["planning_config"]["outer_sheet_height_mm"] == height
    assert all(s.retained.shape == grid.shape and s.visible.shape == grid.shape for s in stack.sheets)
    result = validate_stack(stack)
    assert result["status"] == "PASS", result["failures"]
    assert result["reconstruction"]["mismatched_pixels"] == 0
    assert result["outer_sheet_mm"] == pytest.approx(list(grid.outer_mm))
    assert result["artwork_grid_mm"] == pytest.approx([grid.artwork_mm, grid.artwork_height_mm])
    for row in result["sheets"]:
        assert row["shared_perimeter_complete"] and row["width_status"] == "PASS" and row["four_neighbor"] == "PASS"
    assert any(row["cutout_holes"] for row in result["sheets"])  # real through-cutouts
    save_stack(stack, tmp_path / "s")
    back = load_stack(tmp_path / "s")
    assert back.grid == grid and np.array_equal(back.target_rgb, target)
    assert all(np.array_equal(a.retained, b.retained) for a, b in zip(stack.sheets, back.sheets))
    assert validate_stack(back)["status"] == "PASS"


def test_targets_must_fit_the_grid_without_stretching() -> None:
    grid = StackGrid.for_planner(200.0, 1, 150.0)  # 512 x 368 px artwork
    config = PlanningConfig(outer_sheet_mm=200.0, outer_sheet_height_mm=150.0, scale=1)
    for rows, cols in ((368, 368), (512, 512), (368, 500), (200, 256)):  # wrong aspect or not a divisor
        with pytest.raises(StackError):
            plan_stack(_artwork(rows, cols), config)
    # Half resolution in both axes is the same aspect (pixels stay square).
    half = _artwork(grid.artwork_rows // 2, grid.artwork_pixels // 2)
    stack, _ = plan_stack(half, config)
    assert stack.grid == grid
