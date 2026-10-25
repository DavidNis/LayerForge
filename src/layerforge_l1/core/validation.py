"""Whole-stack validation with the existing, unchanged validators.

Per sheet (same checks as the accepted L-1 run):

* ``connectivity_repair.audit_edge_connectivity`` - one 4-neighbor component
  touching the border (no floating material);
* ``connectivity_repair.audit_connection_width`` - 1.2 mm retained-width /
  orphan-structural-core rule on the stack's calibrated grid;
* complete shared perimeter band;
* visible region lies inside the retained material.

Sheets that carry exact vector geometry (L2) must also pass
``vector_sheet_checks`` on that geometry, with the same 1.2 mm width.

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


VECTOR_AREA_TOLERANCE_MM2 = 1e-6


def minimum_width_mm() -> float:
    """The approved 1.2 mm connection width (nominal + vectorization + CAD safety)."""

    c = DEFAULT_OPTIMIZATION_CONFIG
    return c.nominal_connection_width_mm + c.vectorization_safety_mm + c.cad_conversion_safety_mm


def vector_sheet_checks(stack: SheetStack, geometry: Any) -> dict[str, Any]:
    """The sheet rules evaluated on exact planar geometry in sheet mm.

    Continuous counterpart of the raster audits, with the same width: one connected
    part; the shared perimeter band fully present; nothing outside the sheet; every
    structural core (erosion by half the minimum width) connected to the perimeter.
    Material that vanishes under the erosion is reported as thin, as in the raster audit.
    """

    from shapely.geometry import box

    from .cad import perimeter_band, polygons

    size = stack.grid.outer_sheet_mm
    band = perimeter_band(stack)
    width = minimum_width_mm()
    parts = polygons(geometry)
    core = geometry.buffer(-width / 2)
    cores = polygons(core)
    orphans = [c for c in cores if not c.intersects(band)]
    thin = geometry.difference(core.buffer(width / 2))
    missing = band.difference(geometry).area
    outside = geometry.difference(box(0.0, 0.0, size, size)).area
    result = {
        "valid_geometry": bool(geometry.is_valid),
        "connected_parts": len(parts),
        "perimeter_missing_mm2": round(float(missing), 9),
        "outside_sheet_mm2": round(float(outside), 9),
        "minimum_width_mm": width,
        "structural_cores": len(cores),
        "orphan_structural_cores": len(orphans),
        "orphan_core_points_mm": [[round(c, 3) for c in o.representative_point().coords[0]] for o in orphans],
        "thin_material_mm2": round(float(thin.area), 6),
        "holes": sum(len(p.interiors) for p in parts),
        "area_mm2": round(float(geometry.area), 6),
    }
    result["pass"] = (
        result["valid_geometry"]
        and len(parts) == 1
        and missing <= VECTOR_AREA_TOLERANCE_MM2
        and outside <= VECTOR_AREA_TOLERANCE_MM2
        and not orphans
    )
    return result


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
        if sheet.geometry is not None:
            # Vector sheet: the raster checks above run on its grid raster; the exact
            # geometry that CAD will extrude must pass as well.
            row["vector_checks"] = vector_sheet_checks(stack, sheet.geometry)
            row["pass"] = row["pass"] and row["vector_checks"]["pass"]
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
