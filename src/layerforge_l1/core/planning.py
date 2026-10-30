"""Stable entry point to the approved per-cell reveal planner.

The algorithm lives in ``layerforge_l1.reveal_planner``.  This module
reproduces the call sequence of the accepted L-1 run
(``scripts/run_turtle_reveal_planner_poc.py``, variant ``per_cell``):

    exact_index -> RevealPlanner -> closure -> beam_search
    -> eliminate_sheets -> materialize

with one addition: when the closure is infeasible, the blocked cells (then
their neighbors) are re-cut finer and the closure repeated
(``RevealPlanner.closure_with_refinement``, bounded).  Physical rules are
unchanged; a feasible first closure takes exactly the original path.

and packages the result as a ``SheetStack``.  Other planners in the package
(CP-SAT ``experimental_global_planner``, ``width_aware_planner``,
``subtractive_poc``, ``layer_optimization``) are experimental predecessors and
are not called here.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from ..experimental_global_planner import exact_index
from ..reveal_planner import RevealConfig, RevealPlanner
from .model import Sheet, SheetStack, StackError, StackGrid, hex_color

PLANNER_ID = "layerforge_l1.reveal_planner.RevealPlanner/per_cell"


class PlanningError(StackError):
    """The approved planner could not produce a complete stack."""

    def __init__(self, message: str, report: dict[str, Any], obstruction: np.ndarray | None = None):
        super().__init__(message)
        self.report = report
        # Artwork-grid map of cells that can never be supported (closure failures only).
        self.obstruction = obstruction


@dataclass(frozen=True)
class PlanningConfig:
    """Planner inputs exposed by the accepted run script (same defaults)."""

    outer_sheet_mm: float = 225.0
    scale: int = 4
    cell_tile_mm: float = 3.9
    beam_width: int = 6
    beam_score: str = "area"
    planning_margin_pixels: int = 0
    frame_color: str | None = None
    thickness_mm: float = 1.6
    # Only when the first closure is infeasible: widen the local refinement by this many
    # neighbor rings (-1: whole artwork) to compare sheets and seams; 0 = minimal refinement.
    refinement_extra_rings: int = 0

    def reveal_config(self, frame_label: int | None) -> RevealConfig:
        return RevealConfig(
            outer_sheet_mm=self.outer_sheet_mm,
            scale=self.scale,
            split_regions=True,
            beam_width=self.beam_width,
            planning_margin_pixels=self.planning_margin_pixels,
            cell_tile_mm=self.cell_tile_mm,
            beam_score=self.beam_score,
            frame_label=frame_label,
        )


def plan_stack(
    target_rgb: np.ndarray, config: PlanningConfig = PlanningConfig(), name: str = "stack"
) -> tuple[SheetStack, dict[str, Any]]:
    """Plan physical sheets for an exact flat-color target image.

    ``target_rgb`` must be a square uint8 RGB image whose side divides the
    planner's artwork grid (``512 * scale`` px); every distinct RGB triplet is a
    palette color.  Raises ``PlanningError`` (with ``report``) when closure is
    infeasible or the bounded beam does not cover every cell.
    """

    target_rgb = np.ascontiguousarray(target_rgb)
    if target_rgb.dtype != np.uint8 or target_rgb.ndim != 3 or target_rgb.shape[2] != 3:
        raise StackError(f"target must be uint8 RGB, got {target_rgb.dtype} {target_rgb.shape}")
    if config.beam_score not in ("area", "colors"):
        raise StackError(f"beam_score must be 'area' or 'colors', got {config.beam_score!r}")
    grid = StackGrid.for_planner(config.outer_sheet_mm, config.scale)
    analysis = grid.poc_config().analysis_pixels
    height, width = target_rgb.shape[:2]
    if height != width or analysis % height:
        raise StackError(f"target must be square with a side dividing {analysis} px, got {width}x{height}")
    if len(np.unique(target_rgb.reshape(-1, 3), axis=0)) > 255:
        raise StackError("target has more than 255 exact colors; flatten it to a palette first")

    labels, palette = exact_index(target_rgb)
    hexes = [hex_color(color) for color in palette]
    frame_label = None
    if config.frame_color:
        if config.frame_color.upper() not in hexes:
            raise StackError(f"frame color {config.frame_color} does not occur in the target")
        frame_label = hexes.index(config.frame_color.upper())

    reveal_config = config.reveal_config(frame_label)
    started = time.perf_counter()
    planner = RevealPlanner(labels, reveal_config)
    report: dict[str, Any] = {
        "planner": PLANNER_ID,
        "planning_config": asdict(config),
        "reveal_config": dict(reveal_config.__dict__),
        "palette": hexes,
        "cells": planner.cell_count,
    }
    # Unchanged closure for images that plan; on failure, bounded local cell refinement.
    planner, closure, refinement = planner.closure_with_refinement(config.refinement_extra_rings)
    if refinement:
        report["cell_refinement"] = refinement
        report["cells_after_refinement"] = planner.cell_count
    report["closure_feasible"] = closure["feasible"]
    report["closure_round_robin_sheets"] = closure["round_robin_sheet_count"]
    report["closure_seconds"] = round(time.perf_counter() - started, 1)
    if not closure["feasible"]:
        stuck = np.zeros(planner.cell_count, dtype=bool)
        stuck[closure["obstruction_cells"]] = True
        report["obstruction_pixels_native"] = int(stuck[planner.native_cells].sum())
        raise PlanningError(
            "reveal closure is infeasible: some cells can never be supported", report, stuck[planner.native_cells]
        )

    plan = planner.beam_search()
    report["search_status"] = plan["status"]
    report["search_reveal_evaluations"] = plan["evaluations"]
    report["beam_sheet_count"] = len(plan["front_to_back_colors"])
    if plan["status"] != "COMPLETE":
        raise PlanningError(f"beam search ended {plan['status']} within {reveal_config.max_sheets} sheets", report)
    colors, reveals, eliminated = planner.eliminate_sheets(plan["front_to_back_colors"], plan["reveals"])
    report["eliminated_sheets"] = eliminated
    report["sheet_count"] = len(colors)
    report["front_to_back_colors"] = [hexes[c] for c in colors]
    report["seams"] = planner.seam_report(reveals)
    materialized = planner.materialize(reveals)
    report["planning_seconds"] = round(time.perf_counter() - started, 1)

    sheets = []
    for depth in range(len(colors) - 1, -1, -1):  # back to front
        back_index = len(colors) - 1 - depth
        color = hexes[colors[depth]]
        sheets.append(
            Sheet(
                id=f"layer_{back_index:02d}_{color[1:].lower()}",
                color=color,
                retained=materialized[depth]["physical_mask"].astype(bool),
                visible=materialized[depth]["visible_mask"].astype(bool),
                thickness_mm=config.thickness_mm,
            )
        )
    stack = SheetStack(
        name=name,
        grid=grid,
        sheets=sheets,
        target_rgb=target_rgb,
        frame_color=hexes[frame_label] if frame_label is not None else None,
        provenance={"generated_by": PLANNER_ID, "planning": report},
    )
    return stack, report
