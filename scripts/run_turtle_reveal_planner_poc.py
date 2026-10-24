"""Reveal-closure planner PoC on the locked turtle target (no artwork edits).

Runs the per-cell planner and a region-indivisible ablation of the same
planner on the identical indexed target, then validates every produced sheet
with the existing validators (4-neighbor connectivity, 1.2 mm orphan-core
width rule, shared perimeter) at 4x and again at 8x, and checks exact
mask-derived reconstruction.  Nothing is exported as CAD.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from layerforge_l1.connectivity_repair import audit_connection_width, audit_edge_connectivity
from layerforge_l1.experimental_global_planner import exact_index
from layerforge_l1.hidden_width_repair import WidthRepairConfig, audit_mask_at_physical_scale
from layerforge_l1.layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from layerforge_l1.reveal_planner import RevealConfig, RevealPlanner, embed

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "artifacts" / "l1_turtle_final_global_planning_200mm_v1" / "source" / "final_corrected_candidate.png"
OUTPUT = ROOT / "artifacts" / "l1_turtle_reveal_planner_poc_v1"
OPENING_MM = 0.8  # diagnostic only
SKIP_X8 = False


def _hex(color: np.ndarray) -> str:
    return "#" + "".join(f"{int(c):02X}" for c in color)


def _save(mask: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255).save(path)


def _narrow_openings(physical: np.ndarray, mm_per_pixel: float) -> int:
    holes = (~physical).astype(np.uint8)
    size = int(np.ceil(OPENING_MM / mm_per_pixel))
    size += 1 - size % 2
    opened = cv2.morphologyEx(holes, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    count, labels = cv2.connectedComponents(holes, connectivity=4)
    return int(sum(1 for c in range(1, count) if not opened[labels == c].any()))


def run_variant(name: str, labels: np.ndarray, palette: np.ndarray, config: RevealConfig) -> dict:
    out = OUTPUT / name
    started = time.perf_counter()
    planner = RevealPlanner(labels, config)
    row: dict = {"variant": name, "cells": planner.cell_count}
    closure = planner.closure()
    row["closure_feasible"] = closure["feasible"]
    row["closure_round_robin_sheets"] = closure["round_robin_sheet_count"]
    row["closure_seconds"] = round(time.perf_counter() - started, 1)
    print(name, "closure", row, flush=True)
    if not closure["feasible"]:
        stuck = np.zeros(planner.cell_count, dtype=bool)
        stuck[closure["obstruction_cells"]] = True
        mask = stuck[planner.native_cells]
        row["obstruction_pixels_native"] = int(mask.sum())
        row["obstruction_by_color"] = {
            _hex(palette[c]): int((mask & (labels == c)).sum()) for c in np.unique(labels[mask])
        }
        overlay = (np.asarray(palette)[labels] * 0.35 + 160).astype(np.uint8)
        overlay[mask] = (255, 0, 0)
        out.mkdir(parents=True, exist_ok=True)
        Image.fromarray(overlay).resize((1024, 1024), Image.NEAREST).save(out / "obstruction_overlay.png")
        return row

    plan = planner.beam_search()
    row["search_status"] = plan["status"]
    row["search_reveal_evaluations"] = plan["evaluations"]
    row["beam_sheet_count"] = len(plan["front_to_back_colors"])
    colors, reveals, eliminated = planner.eliminate_sheets(plan["front_to_back_colors"], plan["reveals"])
    plan["reveals"] = reveals
    row["eliminated_sheets"] = eliminated
    print(name, "beam", row["beam_sheet_count"], "after elimination", len(colors), flush=True)
    row["sheet_count"] = len(colors)
    row["front_to_back_colors"] = [_hex(palette[c]) for c in colors]
    sheets = planner.materialize(plan["reveals"])
    p = planner.grid.perimeter_pixels
    mm_px = planner.width_parameters["mm_per_pixel"]
    composite = np.full(planner.cell_map.shape, -1, dtype=np.int16)
    sheet_rows = []
    for depth, (sheet, color) in enumerate(zip(sheets, colors)):
        physical = sheet["physical_mask"]
        composite[(composite < 0) & physical] = color
        edge, _ = audit_edge_connectivity(physical)
        width, _ = audit_connection_width(
            physical, sheet["visible_mask"][p:-p, p:-p], planner.grid, DEFAULT_OPTIMIZATION_CONFIG
        )
        # 8x revalidation from the same mask (nearest 2x of the 4x grid).
        if SKIP_X8:
            audit8 = {"edge": {"four_neighbor_status": "NOT_RUN"}, "width": {"status": "NOT_RUN", "orphan_structural_core_components": None}}
        else:
            audit8, _ = audit_mask_at_physical_scale(
                physical,
                sheet["visible_mask"][p:-p, p:-p],
                native=planner.grid,
                repair=WidthRepairConfig(physical_size_mm=config.outer_sheet_mm, validation_scale=2),
            )
        back_index = len(colors) - 1 - depth
        _save(physical, out / "layers" / f"layer_{back_index:02d}_{_hex(palette[color])[1:].lower()}" / "retained_material_mask.png")
        preview = np.full(physical.shape + (3,), 255, dtype=np.uint8)
        preview[physical] = palette[color]
        Image.fromarray(preview).resize((1152, 1152), Image.NEAREST).save(
            out / "layers" / f"layer_{back_index:02d}_{_hex(palette[color])[1:].lower()}" / "physical_sheet_preview.png"
        )
        _save(sheet["visible_mask"], out / "layers" / f"layer_{back_index:02d}_{_hex(palette[color])[1:].lower()}" / "source_visible_region_mask.png")
        sheet_rows.append(
            {
                "front_to_back_depth": depth,
                "physical_layer_index_back_to_front": back_index,
                "color": _hex(palette[color]),
                "four_neighbor_x4": edge["four_neighbor_status"],
                "width_x4": width["status"],
                "orphan_cores_x4": width["orphan_structural_core_components"],
                "thin_decorative_warnings_x4": width["thin_decorative_visible_components"],
                "four_neighbor_x8": audit8["edge"]["four_neighbor_status"],
                "width_x8": audit8["width"]["status"],
                "orphan_cores_x8": audit8["width"]["orphan_structural_core_components"],
                "shared_perimeter_complete": bool(physical[planner.perimeter].all()),
                "narrow_openings_under_0_8mm": _narrow_openings(physical, mm_px),
                "retained_fraction": float(physical.mean()),
            }
        )
        print(name, sheet_rows[-1], flush=True)
    target = embed(labels.astype(np.int16), planner.grid, -1)
    inside = ~planner.perimeter  # includes any frame padding
    row["reconstruction_mismatched_pixels_x4"] = int((composite[inside] != target[inside]).sum())
    row["all_sheets_pass_x4"] = all(
        r["four_neighbor_x4"] == "PASS" and r["width_x4"] == "PASS" and r["shared_perimeter_complete"]
        for r in sheet_rows
    )
    row["all_sheets_pass_x8"] = all(
        r["four_neighbor_x8"] == "PASS" and r["width_x8"] == "PASS" for r in sheet_rows
    ) if not SKIP_X8 else "NOT_RUN"
    row["seams"] = planner.seam_report(plan["reveals"])
    row["sheets"] = sheet_rows
    row["total_seconds"] = round(time.perf_counter() - started, 1)
    rgb = np.asarray(palette)[np.clip(composite, 0, None)].astype(np.uint8)
    rgb[composite < 0] = 255
    Image.fromarray(rgb).save(out / "mask_derived_composite.png")
    depth_map = np.zeros(planner.native_cells.shape, dtype=np.int32)
    for depth, reveal in enumerate(plan["reveals"]):
        depth_map[reveal[planner.native_cells]] = depth
    shade = (255 * depth_map / max(1, len(colors) - 1)).astype(np.uint8)
    Image.fromarray(cv2.applyColorMap(shade, cv2.COLORMAP_VIRIDIS)[:, :, ::-1]).resize((1024, 1024), Image.NEAREST).save(out / "visible_depth_map.png")
    return row


def main() -> None:
    global OUTPUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", default="per_cell,region_indivisible")
    parser.add_argument("--beam", type=int, default=6)
    parser.add_argument("--margin", type=int, default=0)
    parser.add_argument("--skip-x8", action="store_true")
    parser.add_argument("--target", default=str(TARGET))
    parser.add_argument("--tile-mm", type=float, default=3.9)
    parser.add_argument("--frame-hex", default="", help="target color that marks frame padding")
    parser.add_argument("--score", default="area", choices=["area", "colors"])
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    OUTPUT = Path(args.output) if args.output else OUTPUT
    global SKIP_X8
    SKIP_X8 = args.skip_x8
    target = Path(args.target)
    rgb = np.asarray(Image.open(target).convert("RGB"))
    labels, palette = exact_index(rgb)
    frame_label = None
    if args.frame_hex:
        frame_label = [_hex(c) for c in palette].index(args.frame_hex.upper())
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = {"target": str(target.resolve().relative_to(ROOT)), "palette": [_hex(c) for c in palette], "variants": []}
    for name in args.variants.split(","):
        config = RevealConfig(
            split_regions=name == "per_cell", beam_width=args.beam, planning_margin_pixels=args.margin,
            cell_tile_mm=args.tile_mm, beam_score=args.score, frame_label=frame_label
        )
        results["config"] = config.__dict__
        results["variants"].append(run_variant(name, labels, palette, config))
        (OUTPUT / "poc_report.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
