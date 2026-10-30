"""Sheet masks -> CadQuery solids -> STEP/STL, with read-back verification.

``mask_to_geometry`` is moved unchanged from ``scripts/export_reveal_stack_cad.py``
(the accepted L-1 exporter, which now imports it from here).  Solids are built
with the existing ``geometry.extrude_geometry``; cutouts are interior wires of
the extruded face, i.e. true through-holes.

``rasterize_geometry`` is the 4x supersampled re-rasterization that produced
``cad_revalidation.json`` for the reference (previously an ad hoc snippet,
documented in the technical PDF ch. 11.6).  ``rasterize_stl`` applies the same
rule to the upward faces of a *reopened STL*, so validation and previews can be
derived from the exported mesh itself.

Sheets with an exact vector ``geometry`` (L2 vector edits) are extruded from that
geometry directly, never from its grid raster; their reopened STL footprint is
additionally checked against the geometry and with ``validation.vector_sheet_checks``.
Files are named by ``SheetStack.export_names()`` (current order and color).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .model import SheetStack, StackGrid

SUPERSAMPLE = 4
DEFAULT_SIMPLIFY_MM = 0.03
# Mean distance between a vector sheet's geometry and its reopened STL footprint
# (symmetric-difference area / boundary length); STL stores float32 coordinates.
MAX_MEAN_BOUNDARY_DEVIATION_MM = 1e-3
STL_TOLERANCE_MM = 0.02
STL_ANGULAR_TOLERANCE = 0.2


def mask_to_geometry(mask: np.ndarray, mm_per_pixel: float, simplify_mm: float):
    """Polygons whose edges follow pixel edges (not pixel centres)."""

    contours, hierarchy = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    height = mask.shape[0]
    to_mm = lambda c: [((x + 0.5) * mm_per_pixel, (height - y - 0.5) * mm_per_pixel) for x, y in c[:, 0]]
    shells = []
    for index, contour in enumerate(contours):
        if hierarchy[0][index][3] != -1 or len(contour) < 3:
            continue
        holes = []
        child = hierarchy[0][index][2]
        while child != -1:
            if len(contours[child]) >= 3:
                holes.append(to_mm(contours[child]))
            child = hierarchy[0][child][0]
        shells.append(Polygon(to_mm(contour), holes))
    half = 0.5 * mm_per_pixel
    geometry = unary_union([s.buffer(0) for s in shells]).buffer(half, join_style=2, mitre_limit=2.0)
    return _match_mask(geometry.simplify(simplify_mm, preserve_topology=True).buffer(0), mask, mm_per_pixel)


def _match_mask(geometry: BaseGeometry, mask: np.ndarray, mm_per_pixel: float) -> BaseGeometry:
    """Pixel-exact correction of the traced geometry.

    Contours run through pixel centres, so a one-pixel-wide tail is a zero-width spike that
    ``buffer(0)`` drops.  The geometry is rasterized exactly as ``revalidate_cad`` does; only
    when that raster differs from the mask are the differing pixels added or cut as squares.
    Geometry that already matches is returned unchanged.
    """

    if mask.ndim != 2 or mask.shape[0] != mask.shape[1]:
        return geometry
    from types import SimpleNamespace

    from shapely.geometry import box

    grid = SimpleNamespace(outer_pixels=mask.shape[0], mm_per_pixel=mm_per_pixel)
    height = mask.shape[0]
    square = lambda y, x: box(x * mm_per_pixel, (height - y - 1) * mm_per_pixel,
                              (x + 1) * mm_per_pixel, (height - y) * mm_per_pixel)
    for _ in range(4):  # a correction can move a neighbouring diagonal edge; repeat until exact
        raster = rasterize_geometry(geometry, grid)
        missing, extra = mask & ~raster, raster & ~mask
        if not missing.any() and not extra.any():
            break
        if missing.any():
            geometry = unary_union([geometry, *(square(y, x) for y, x in zip(*np.nonzero(missing)))])
        if extra.any():
            geometry = geometry.difference(unary_union([square(y, x) for y, x in zip(*np.nonzero(extra))]))
        geometry = geometry.buffer(0)
    return geometry


def polygons(geometry: BaseGeometry) -> list:
    # geometry.py imports CadQuery at module level; load it only when needed.
    from ..geometry import polygons as _polygons

    return _polygons(geometry)


def _to_subpixels(coords: Any, grid: StackGrid) -> np.ndarray:
    # 1/16-subpixel fixed point (shift=4); -8 puts pixel centres at +0.5.
    xy = np.asarray(coords, dtype=np.float64)[..., :2]
    n, mm = grid.outer_pixels, grid.mm_per_pixel
    u = xy[..., 0] / mm * SUPERSAMPLE
    v = (n - xy[..., 1] / mm) * SUPERSAMPLE
    return np.round(np.stack([u, v], axis=-1) * 16 - 8).astype(np.int32)


def _downsample(image: np.ndarray, grid: StackGrid) -> np.ndarray:
    n, s = grid.outer_pixels, SUPERSAMPLE
    return image.reshape(n, s, n, s).sum((1, 3)) >= (s * s) / 2


def rasterize_geometry(geometry: BaseGeometry, grid: StackGrid) -> np.ndarray:
    """Supersampled raster of planar geometry (majority of 4x4 subpixels)."""

    n = grid.outer_pixels * SUPERSAMPLE
    image = np.zeros((n, n), np.uint8)
    for poly in polygons(geometry):
        cv2.fillPoly(image, [_to_subpixels(poly.exterior.coords, grid)], 1, shift=4)
        for ring in poly.interiors:
            cv2.fillPoly(image, [_to_subpixels(ring.coords, grid)], 0, shift=4)
    return _downsample(image, grid)


def stl_footprint(path: Path) -> BaseGeometry:
    """Planar union of the upward-facing triangles of a reopened STL."""

    import shapely
    import trimesh

    mesh = trimesh.load_mesh(path, file_type="stl", process=True)
    top = mesh.faces[mesh.face_normals[:, 2] > 0.5]
    # Filling triangles one by one is boundary-inclusive and over-covers
    # diagonal edges; the exact union is rasterized like any other geometry.
    return unary_union(shapely.polygons(mesh.vertices[top][:, :, :2]))


def rasterize_stl(path: Path, grid: StackGrid) -> np.ndarray:
    """Supersampled raster of the top-face footprint of a reopened STL."""

    return rasterize_geometry(stl_footprint(path), grid)


def sheet_geometry(stack: SheetStack, index: int, simplify_mm: float = DEFAULT_SIMPLIFY_MM) -> BaseGeometry:
    """Exact vector geometry if the sheet has one, else the traced retained mask."""

    sheet = stack.sheets[index]
    if sheet.geometry is not None:
        return sheet.geometry
    return mask_to_geometry(sheet.retained, stack.grid.mm_per_pixel, simplify_mm)


def perimeter_band(stack: SheetStack) -> BaseGeometry:
    """The shared solid perimeter band in sheet mm (same pixels as ``grid.perimeter_mask``)."""

    size = stack.grid.outer_sheet_mm
    band = stack.grid.perimeter_pixels * stack.grid.mm_per_pixel
    return box(0.0, 0.0, size, size).difference(box(band, band, size - band, size - band))


def rim_geometry(stack: SheetStack, index: int, geometry: BaseGeometry) -> BaseGeometry | None:
    """Raised-rim footprint: the outer band of the given width, on the sheet's material."""

    rim = stack.sheets[index].rim
    if rim is None:
        return None
    size = stack.grid.outer_sheet_mm
    width = float(rim["width_mm"])
    band = box(0.0, 0.0, size, size).difference(box(width, width, size - width, size - width))
    return geometry.intersection(band).buffer(0)


def build_sheet_solid(stack: SheetStack, index: int, simplify_mm: float = DEFAULT_SIMPLIFY_MM):
    """CadQuery solid of one sheet in print placement (broad face on z = 0).

    An optional rim is fused on top of the base like ``geometry.build_layer_solid``.
    """

    from ..geometry import extrude_geometry

    sheet = stack.sheets[index]
    geometry = sheet_geometry(stack, index, simplify_mm)
    solid = extrude_geometry(geometry, sheet.thickness_mm)
    rim_area = rim_geometry(stack, index, geometry)
    if rim_area is not None:
        raised = extrude_geometry(rim_area, float(sheet.rim["total_height_mm"]) - sheet.thickness_mm, sheet.thickness_mm)
        solid = solid.fuse(raised).clean()
    return solid, geometry


def export_stack(
    stack: SheetStack,
    output: Path,
    *,
    simplify_mm: float = DEFAULT_SIMPLIFY_MM,
    progress: Any = None,
) -> dict[str, Any]:
    """Write one STEP and one STL per sheet, reopen both, write ``cad_manifest.json``."""

    import cadquery as cq
    import trimesh

    output = Path(output)
    (output / "stl").mkdir(parents=True, exist_ok=True)
    (output / "step").mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows = []
    names = stack.export_names()
    for index, (sheet, name) in enumerate(zip(stack.sheets, names)):
        t0 = time.perf_counter()
        solid, geometry = build_sheet_solid(stack, index, simplify_mm)
        t_trace = time.perf_counter()
        step_path, stl_path = output / "step" / f"{name}.step", output / "stl" / f"{name}.stl"
        cq.exporters.export(solid, str(step_path), exportType="STEP")
        cq.exporters.export(solid, str(stl_path), exportType="STL",
                            tolerance=STL_TOLERANCE_MM, angularTolerance=STL_ANGULAR_TOLERANCE)
        t_export = time.perf_counter()
        step_shape = cq.importers.importStep(str(step_path)).val()
        mesh = trimesh.load_mesh(stl_path, file_type="stl", process=True)
        mm_px = stack.grid.mm_per_pixel
        mask_area = float(sheet.retained.sum()) * mm_px**2
        parts = polygons(geometry)
        thickness = sheet.thickness_mm
        rim_area = rim_geometry(stack, index, geometry)
        expected_volume = geometry.area * thickness
        if rim_area is not None:
            expected_volume += rim_area.area * (sheet.stack_height_mm - thickness)
        row = {
            "layer": name,
            "sheet_id": sheet.id,
            "color": sheet.color,
            "geometry_source": "exact vector geometry" if sheet.geometry is not None else "traced retained mask",
            "polygons": len(parts),
            "holes": sum(len(p.interiors) for p in parts),
            "vertices": sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors) for p in parts),
            "step_solids": len(step_shape.Solids()),
            "step_valid": bool(step_shape.isValid()),
            "step_volume_mm3": round(float(step_shape.Volume()), 4),
            "stl_triangles": int(len(mesh.faces)),
            "stl_watertight": bool(mesh.is_watertight),
            "stl_components": len(mesh.split(only_watertight=False)),
            "stl_volume_mm3": round(float(mesh.volume), 4),
            "stl_bounds_mm": np.round(mesh.bounds, 3).tolist(),
            "traced_area_vs_mask_pct": round(100 * (geometry.area - mask_area) / mask_area, 4),
            "volume_vs_area_x_thickness_pct": round(100 * (mesh.volume - expected_volume) / expected_volume, 4),
            "expected_volume_mm3": round(float(expected_volume), 4),
            "step_volume_vs_expected_pct": round(100 * (step_shape.Volume() - expected_volume) / expected_volume, 4),
            "seconds_trace": round(t_trace - t0, 2),
            "seconds_cad_export": round(t_export - t_trace, 2),
            "seconds_readback": round(time.perf_counter() - t_export, 2),
        }
        if sheet.rim is not None:
            row.update(rim=dict(sheet.rim), rim_area_mm2=round(float(rim_area.area), 4),
                       stl_height_mm=round(float(mesh.bounds[1][2] - mesh.bounds[0][2]), 4),
                       expected_height_mm=sheet.stack_height_mm)
        rows.append(row)
        if progress:
            progress("CAD_SHEET_EXPORTED", **{k: row[k] for k in ("layer", "step_solids", "stl_watertight", "stl_components")})
    manifest = {
        "stack_name": stack.name,
        "outer_sheet_mm": stack.grid.outer_sheet_mm,
        "simplify_mm": simplify_mm,
        "stl_tolerance_mm": STL_TOLERANCE_MM,
        "stl_angular_tolerance_rad": STL_ANGULAR_TOLERANCE,
        "units": "mm",
        "assembly_back_to_front": [
            {
                "layer": name,
                "sheet_id": sheet.id,
                "color": sheet.color,
                "stl": f"stl/{name}.stl",
                "step": f"step/{name}.step",
                "thickness_mm": sheet.thickness_mm,
                "assembled_z0_mm": z,
                **({"rim": dict(sheet.rim)} if sheet.rim is not None else {}),
            }
            for sheet, name, z in zip(stack.sheets, names, stack.assembly_z_mm())
        ],
        "print_placement": "every STL/STEP at z = 0, broad face on the bed; assembly z is separate",
        "layers": rows,
        "total_seconds": round(time.perf_counter() - started, 1),
    }
    (output / "cad_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def revalidate_cad(
    stack: SheetStack,
    cad_dir: Path,
    *,
    simplify_mm: float = DEFAULT_SIMPLIFY_MM,
    retrace: bool = True,
    progress: Any = None,
) -> tuple[dict[str, Any], list[np.ndarray]]:
    """Re-rasterize the exported CAD and re-run the structural validators.

    Returns the report and the per-sheet (back to front) masks rasterized from
    the reopened STL files.  With ``retrace`` the planar geometry is also
    re-traced and rasterized (the original reference revalidation).
    """

    from .validation import reconstruction, validate_sheet_mask, vector_sheet_checks

    cad_dir = Path(cad_dir)
    rows, stl_masks = [], []
    for index, (sheet, name) in enumerate(zip(stack.sheets, stack.export_names())):
        stl_path = cad_dir / "stl" / f"{name}.stl"
        if not stl_path.is_file():
            raise FileNotFoundError(f"missing exported STL for {sheet.id}: {stl_path}")
        footprint = stl_footprint(stl_path)
        from_stl = rasterize_geometry(footprint, stack.grid)
        stl_masks.append(from_stl)
        row: dict[str, Any] = {"layer": name, "stl_px_diff_from_mask": int((from_stl != sheet.retained).sum())}
        if sheet.geometry is not None:
            # Vector sheet: check the exported footprint itself, at full precision.
            deviation = footprint.symmetric_difference(sheet.geometry).area
            exact = vector_sheet_checks(stack, footprint)
            row.update(
                sheet_id=sheet.id,
                stl_vs_geometry_area_mm2=round(float(deviation), 6),
                stl_mean_boundary_deviation_mm=float(deviation / max(sheet.geometry.boundary.length, 1e-9)),
                vector_checks_on_stl=exact,
            )
        if retrace:
            traced = rasterize_geometry(sheet_geometry(stack, index, simplify_mm), stack.grid)
            row["geometry_px_diff_from_mask"] = int((traced != sheet.retained).sum())
        checks = validate_sheet_mask(stack, from_stl, sheet.visible & from_stl)
        row.update({
            "four_neighbor": checks["four_neighbor"],
            "width_1_2mm": checks["width_status"],
            "perimeter_complete": checks["shared_perimeter_complete"],
            "cutout_holes": checks["cutout_holes"],
        })
        rows.append(row)
        if progress:
            progress("CAD_SHEET_REVALIDATED", **row)
    recon = reconstruction(stack, stl_masks)
    planned = stack.composite_sheet_index()
    shown = np.full(planned.shape, -1, dtype=np.int16)
    for index in range(len(stl_masks) - 1, -1, -1):
        shown[(shown < 0) & stl_masks[index]] = index
    report = {
        "source": "reopened STL files, upward faces, 4x supersampled majority raster",
        "layers": rows,
        "stack_mismatch_px": int((shown != planned).sum()),
        "reconstruction_from_stl": recon,
        "pass": all(
            r["stl_px_diff_from_mask"] == 0 and r.get("geometry_px_diff_from_mask", 0) == 0
            and r["four_neighbor"] == "PASS" and r["width_1_2mm"] == "PASS" and r["perimeter_complete"]
            and ("vector_checks_on_stl" not in r or (
                r["vector_checks_on_stl"]["pass"]
                and r["stl_mean_boundary_deviation_mm"] <= MAX_MEAN_BOUNDARY_DEVIATION_MM))
            for r in rows
        ) and int((shown != planned).sum()) == 0,
    }
    return report, stl_masks
