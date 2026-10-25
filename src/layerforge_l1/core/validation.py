"""Whole-stack validation with the existing, unchanged validators.

Per sheet (same checks as the accepted L-1 run):

* ``connectivity_repair.audit_edge_connectivity`` - one 4-neighbor component
  touching the border (no floating material);
* ``connectivity_repair.audit_connection_width`` - 1.2 mm retained-width /
  orphan-structural-core rule on the stack's calibrated grid;
* complete shared perimeter band;
* visible region lies inside the retained material.

Per stack: identical outer grid, front-view reconstruction of the target
(frame pixels excluded, as in the planner run), and agreement between each
sheet's visible mask and what the assembled stack actually shows.  Narrow
openings under 0.8 mm are counted as a diagnostic only, as before.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ..connectivity_repair import audit_connection_width, audit_edge_connectivity
from ..layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from .model import SheetStack

OPENING_DIAGNOSTIC_MM = 0.8


def narrow_openings(retained: np.ndarray, mm_per_pixel: float, opening_mm: float = OPENING_DIAGNOSTIC_MM) -> int:
    """Cutout components that vanish under an ``opening_mm`` morphological opening."""

    holes = (~retained).astype(np.uint8)
    size = int(np.ceil(opening_mm / mm_per_pixel))
    size += 1 - size % 2
    opened = cv2.morphologyEx(holes, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    count, labels = cv2.connectedComponents(holes, connectivity=4)
    return int(sum(1 for c in range(1, count) if not opened[labels == c].any()))


def cutout_hole_count(retained: np.ndarray) -> int:
    _, hierarchy = cv2.findContours(retained.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    return 0 if hierarchy is None else int(sum(1 for h in hierarchy[0] if h[3] != -1))


def validate_sheet_mask(stack: SheetStack, retained: np.ndarray, visible: np.ndarray) -> dict[str, Any]:
    """Structural checks for one retained mask on the stack grid."""

    grid = stack.grid
    poc = grid.poc_config()
    p = grid.perimeter_pixels
    edge, _ = audit_edge_connectivity(retained)
    width, _ = audit_connection_width(retained, visible[p:-p, p:-p], poc, DEFAULT_OPTIMIZATION_CONFIG)
    perimeter_complete = bool(retained[grid.perimeter_mask()].all())
    visible_inside = bool(not (visible & ~retained).any())
    return {
        "four_neighbor": edge["four_neighbor_status"],
        "retained_components_4": int(cv2.connectedComponents(retained.astype(np.uint8), connectivity=4)[0] - 1),
        "width_status": width["status"],
        "orphan_structural_cores": width["orphan_structural_core_components"],
        "thin_decorative_warnings": width["thin_decorative_visible_components"],
        "enforced_width_mm": width.get("enforced_connection_width_mm"),
        "shared_perimeter_complete": perimeter_complete,
        "visible_inside_retained": visible_inside,
        "cutout_holes": cutout_hole_count(retained),
        "narrow_openings_under_0_8mm": narrow_openings(retained, grid.mm_per_pixel),
        "retained_fraction": float(retained.mean()),
        "pass": edge["four_neighbor_status"] == "PASS"
        and width["status"] == "PASS"
        and perimeter_complete
        and visible_inside,
    }


def reconstruction(stack: SheetStack, retained_masks: list[np.ndarray] | None = None) -> dict[str, Any]:
    """Compare the front view of the retained masks with the target."""

    masks = retained_masks or [sheet.retained for sheet in stack.sheets]
    shown = np.full(masks[0].shape, -1, dtype=np.int16)
    for index in range(len(masks) - 1, -1, -1):
        shown[(shown < 0) & masks[index]] = index
    result: dict[str, Any] = {"evaluated": False}
    found = stack.target_labels()
    if found is not None:
        target, palette = found
        sheet_label = np.array([palette.index(s.color) if s.color in palette else -2 for s in stack.sheets] + [-1])
        composite = sheet_label[shown]  # index -1 -> trailing -1 (no material)
        inside = ~stack.grid.perimeter_mask() & ~stack.frame_mask()
        result = {
            "evaluated": True,
            "compared_pixels": int(inside.sum()),
            "mismatched_pixels": int((composite[inside] != target[inside]).sum()),
            "sheet_colors_missing_from_target": [s.color for s in stack.sheets if s.color not in palette],
        }
    visible_owner = np.full(shown.shape, -1, dtype=np.int16)
    for index, sheet in enumerate(stack.sheets):
        visible_owner[sheet.visible] = index
    any_visible = visible_owner >= 0
    result["visible_mask_disagreement_pixels"] = int((visible_owner[any_visible] != shown[any_visible]).sum())
    return result


def validate_stack(stack: SheetStack) -> dict[str, Any]:
    """Run every check; ``status`` is PASS only if all gate checks pass."""

    grid = stack.grid
    sheets = []
    for order, sheet in enumerate(stack.sheets):
        row = {"id": sheet.id, "order_back_to_front": order, "color": sheet.color}
        row.update(validate_sheet_mask(stack, sheet.retained, sheet.visible))
        sheets.append(row)
    recon = reconstruction(stack)
    frame = stack.frame_mask()
    artwork = ~grid.perimeter_mask() & ~frame
    ys, xs = np.nonzero(artwork)
    failures = [f"{row['id']}: sheet checks failed" for row in sheets if not row["pass"]]
    if recon["evaluated"] and recon["mismatched_pixels"]:
        failures.append(f"reconstruction differs from target by {recon['mismatched_pixels']} px")
    if recon["visible_mask_disagreement_pixels"]:
        failures.append(f"visible masks disagree with the front view by {recon['visible_mask_disagreement_pixels']} px")
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "sheet_count": len(stack.sheets),
        "colors_back_to_front": [s.color for s in stack.sheets],
        "repeated_colors": sorted({s.color for s in stack.sheets if [t.color for t in stack.sheets].count(s.color) > 1}),
        "outer_sheet_mm": [grid.outer_sheet_mm, grid.outer_sheet_mm],
        "artwork_grid_mm": [grid.artwork_mm, grid.artwork_mm],
        "visible_artwork_bbox_mm": [
            float((xs.max() - xs.min() + 1) * grid.mm_per_pixel),
            float((ys.max() - ys.min() + 1) * grid.mm_per_pixel),
        ] if len(xs) else [0.0, 0.0],
        "mm_per_pixel": grid.mm_per_pixel,
        "assembly_z_mm": stack.assembly_z_mm(),
        "reconstruction": recon,
        "sheets": sheets,
        "not_run": ["8x width re-audit (not run for the accepted reference either)"],
    }
