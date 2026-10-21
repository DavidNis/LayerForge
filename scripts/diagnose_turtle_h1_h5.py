"""H1-H5 failure diagnostics on the saved rejected 12-sheet turtle stack.

Reads existing masks only; changes no artwork, order or palette.  Uses the
existing validator (``audit_mask_at_physical_scale``) at 225 mm outer size.

H1  black contour network is few large connected regions with thin parts
H2  failures are carried by hidden material that the sheet does not need
H3  remaining failures are visible necks between later-revealed regions
H4  verdicts depend on raster scale
H5  target features below a printable opening width (image admissibility)
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from layerforge_l1.connectivity_repair import audit_connection_width
from layerforge_l1.hidden_width_repair import WidthRepairConfig, audit_mask_at_physical_scale
from layerforge_l1.layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from layerforge_l1.reveal_planner import (
    RevealConfig,
    _geodesic_reach,
    physical_grid,
    structural_support,
    width_kernel,
)
from layerforge_l1.subtractive_poc import _outer_and_perimeter

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "artifacts" / "l1_turtle_final_global_planning_200mm_v1" / "rejected_best_candidate"
TARGET = ROOT / "artifacts" / "l1_turtle_final_global_planning_200mm_v1" / "source" / "final_corrected_candidate.png"
OUTPUT = ROOT / "artifacts" / "l1_turtle_h1_h5_diagnostics_v1"
BLACK = "#0E0F0D"
OPENING_MM = 0.8  # DESIGN_CHOICE diagnostic threshold, not a gate


def _mask(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L")) > 127


def _up(mask: np.ndarray, scale: int) -> np.ndarray:
    return np.repeat(np.repeat(mask, scale, axis=0), scale, axis=1)


def _kernel(mm: float, mm_per_pixel: float) -> np.ndarray:
    size = int(np.ceil(mm / mm_per_pixel))
    size += 1 - size % 2
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = json.loads((CANDIDATE / "rejected_validation_report.json").read_text())
    layer_dirs = sorted((CANDIDATE / "automatic" / "layers").iterdir())
    rgb = np.asarray(Image.open(TARGET).convert("RGB"))
    hexes = np.array(["#%02X%02X%02X" % tuple(p) for p in rgb.reshape(-1, 3)]).reshape(512, 512)
    config = RevealConfig()
    grid = physical_grid(config)
    kernel, parameters = width_kernel(grid)
    mm_px = parameters["mm_per_pixel"]
    _, perimeter = _outer_and_perimeter(grid)
    p = grid.perimeter_pixels
    nub_steps = int(round(config.nub_tolerance_mm / mm_px))
    out: dict = {"width_kernel_mm": parameters["morphology_kernel_size_pixels"] * mm_px}

    # H1: black network structure in the approved target.
    black = (hexes == BLACK).astype(np.uint8)
    count, comp, stats, _ = cv2.connectedComponentsWithStats(black, connectivity=4)
    areas = sorted((int(a) for a in stats[1:, cv2.CC_STAT_AREA]), reverse=True)
    black_hi = _up(black.astype(bool), config.scale).astype(np.uint8)
    thin = black_hi.astype(bool) & ~cv2.morphologyEx(black_hi, cv2.MORPH_OPEN, kernel).astype(bool)
    out["H1"] = {
        "black_components_4conn": count - 1,
        "largest_component_share_of_black": areas[0] / sum(areas),
        "black_component_areas_px": areas,
        "black_area_in_parts_thinner_than_width_kernel_share": float(thin.sum() / black_hi.sum()),
        "rejected_stack_black_depths_front_to_back": report["repeated_color_depths"][BLACK],
    }

    # H2/H3/H4 per sheet.
    rows = []
    for layer in layer_dirs:
        retained = _mask(layer / "retained_material_mask.png")
        visible = _mask(layer / "source_visible_region_mask.png")
        if visible.shape != retained.shape:
            visible_full = np.zeros_like(retained)
            visible_full[32:-32, 32:-32] = visible
            visible = visible_full
        row = {"layer": layer.name}
        for scale in (1, 4, 8):
            audit, _ = audit_mask_at_physical_scale(
                retained,
                visible[32:-32, 32:-32],
                repair=WidthRepairConfig(physical_size_mm=225.0, validation_scale=scale),
            )
            row[f"x{scale}"] = {
                "four_neighbor": audit["edge"]["four_neighbor_status"],
                "width": audit["width"]["status"],
                "orphan_cores": audit["width"]["orphan_structural_core_components"],
                "orphan_classes": sorted(
                    {d["classification"] for d in audit["width"]["orphan_structural_core_details"]}
                ),
            }
        # H2: regularize -- keep only structurally reachable material plus visible.
        u = _up(retained, config.scale)
        v = _up(visible, config.scale)
        support = structural_support(u, perimeter, kernel)
        reach = _geodesic_reach(support, support | v, nub_steps)
        deficit = v & ~reach
        regularized = support | (v & reach)
        audit, _ = audit_connection_width(
            regularized, (v & reach)[p:-p, p:-p], grid, DEFAULT_OPTIMIZATION_CONFIG
        )
        row["regularized"] = {
            "hidden_pixels_removed_x4": int((u & ~regularized).sum()),
            "width": audit["status"],
            "orphan_cores": audit["orphan_structural_core_components"],
            "unsupported_visible_pixels_x4": int(deficit.sum()),
            "unsupported_visible_area_mm2": float(deficit.sum() * mm_px**2),
        }
        # H3: which source colors border the unsupported visible pixels from behind.
        if deficit.any():
            ring = cv2.dilate(deficit.astype(np.uint8), kernel).astype(bool) & ~u
            ring_native = ring[p:-p, p:-p].reshape(512, config.scale, 512, config.scale).any(axis=(1, 3))
            names, counts = np.unique(hexes[ring_native], return_counts=True)
            row["regularized"]["uncovered_colors_adjacent_to_deficit"] = dict(
                zip(names.tolist(), counts.tolist())
            )
        rows.append(row)
        print(row["layer"], row["x4"]["width"], "->", row["regularized"]["width"],
              row["regularized"]["unsupported_visible_pixels_x4"], flush=True)
    out["H2_H3_H4_sheets_back_to_front"] = rows

    # H5: target admissibility at the 0.8 mm opening diagnostic and 1.2 mm.
    h5 = {}
    for name in sorted(set(hexes.ravel())):
        region = _up(hexes == name, config.scale).astype(np.uint8)
        entry = {}
        for label, mm in (("below_0_8mm", OPENING_MM), ("below_1_2mm", 1.2)):
            opened = cv2.morphologyEx(region, cv2.MORPH_OPEN, _kernel(mm, mm_px)).astype(bool)
            entry[f"area_mm2_{label}"] = float((region.astype(bool) & ~opened).sum() * mm_px**2)
        entry["area_mm2"] = float(region.sum() * mm_px**2)
        h5[name] = entry
    out["H5_target_admissibility"] = h5
    (OUTPUT / "h1_h5_report.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "H2_H3_H4_sheets_back_to_front"}, indent=1))


if __name__ == "__main__":
    main()
