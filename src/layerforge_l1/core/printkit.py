"""L3 hardened print kit: final exports, assembly metadata and a printability estimate.

``build_print_kit`` takes a saved sheet stack (an L1 candidate, a plain L0 stack
or an L2 review project) and writes one self-contained, checksummed kit with
the existing pipeline (``validate_stack`` -> ``export_stack`` ->
``revalidate_cad`` -> ``generate_previews``).  Nothing in the stack is altered.

The kit reports three separate levels and never merges them:

* ``cad``      - proven here: validators PASS, every STEP reopens as one valid
  solid, every STL is watertight, reopened geometry equals the reviewed masks /
  exact geometry, volumes match.  A stack that fails is refused (no kit).
* ``fdm_estimate`` - an *estimate* against a configurable ``PrintProcess``:
  bed envelope, layer quantization, and negative (holes, slots, gaps) and thin
  positive features measured on the exported planar geometry (exact for vector
  sheets).  Findings are reported as INFO / RISK / UNSUPPORTED; geometry is
  never changed to remove them.
* ``physical`` - always ``NOT_VERIFIED``; only a real user print (L-G10) can
  change that, and this module never does.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from PIL import Image

from .model import MANIFEST_NAME, SheetStack, StackError, file_sha256, load_stack, save_stack
from .validation import validate_stack

KIT_SCHEMA = "layerforge.print_kit"
KIT_SCHEMA_VERSION = "1.0.0"
SEVERITIES = ("INFO", "RISK", "UNSUPPORTED")
VOLUME_TOLERANCE_PCT = 0.01
_WIDTH_SEARCH_STEP_MM = 0.01


class PrintKitError(StackError):
    """The stack cannot be turned into a print kit (reasons listed)."""


@dataclass(frozen=True)
class PrintProcess:
    """Printer/process parameters for the FDM estimate (all lengths in mm).

    The defaults are a generic 0.4 mm-nozzle FDM assumption, not a measured
    profile; pass a JSON file with the user's printer values instead.
    """

    name: str = "generic_fdm_0.4mm_nozzle"
    source: str = "ASSUMPTION: generic 0.4 mm nozzle defaults, not a measured printer profile"
    nozzle_diameter_mm: float = 0.4
    line_width_mm: float = 0.42
    layer_height_mm: float = 0.2
    first_layer_height_mm: float = 0.2
    # Openings narrower than this may close up (RISK); narrower than the nozzle: UNSUPPORTED.
    min_opening_mm: float = 0.8
    # Material narrower than this may print poorly (RISK); narrower than one line: UNSUPPORTED.
    min_positive_feature_mm: float = 0.84
    bed_x_mm: float = 256.0
    bed_y_mm: float = 256.0
    max_z_mm: float = 256.0
    filament_density_g_cm3: float = 1.24

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.type in ("float", float) and not (isinstance(value, (int, float)) and value > 0):
                raise PrintKitError(f"process {item.name} must be a positive number, got {value!r}")
        if self.min_opening_mm < self.nozzle_diameter_mm:
            raise PrintKitError("process min_opening_mm must be at least nozzle_diameter_mm")
        if self.min_positive_feature_mm < self.line_width_mm:
            raise PrintKitError("process min_positive_feature_mm must be at least line_width_mm")

    @classmethod
    def from_json(cls, path: Path) -> PrintProcess:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PrintKitError(f"cannot read process file {path}: {error}") from error
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise PrintKitError(f"process file {path}: unknown keys {unknown}; known keys {sorted(known)}")
        data.setdefault("source", f"process file {Path(path).name}")
        return cls(**data)


# -- printability estimate -----------------------------------------------------------------
def inscribed_diameter_mm(geometry: Any, upper_mm: float) -> float:
    """Largest disk diameter (to 0.01 mm) that fits inside ``geometry``, capped at ``upper_mm``."""

    if not geometry.buffer(-upper_mm / 2).is_empty:
        return upper_mm
    low, high = 0.0, upper_mm
    while high - low > _WIDTH_SEARCH_STEP_MM:
        mid = (low + high) / 2
        if geometry.buffer(-mid / 2).is_empty:
            high = mid
        else:
            low = mid
    return round(low, 3)


def _severity(width: float, hard: float, soft: float) -> str:
    return "UNSUPPORTED" if width < hard else "RISK" if width < soft else "INFO"


def _narrow_parts(region: Any, width: float) -> tuple[list[Any], int]:
    """Parts of ``region`` no disk of ``width`` can reach, and the number of pieces the
    opening leaves (more than one means a neck narrower than ``width``).

    Residues smaller than ``width**2 / 2`` are corner rounding and pixel-staircase
    teeth of the opening, not features, and are ignored (heuristic, documented).
    """

    from .cad import polygons

    opened = region.buffer(-width / 2).buffer(width / 2)
    residue = [p for p in polygons(region.difference(opened)) if p.area >= width * width / 2]
    return residue, len(polygons(opened))


def _feature(sheet_id: str, name: str, kind: str, geometry: Any, width: float | None, severity: str,
             hidden_by_front: Any, note: str) -> dict[str, Any]:
    visible = geometry.difference(hidden_by_front).area if hidden_by_front is not None else geometry.area
    if visible <= 1e-6 and severity != "INFO":
        note += "; hidden behind the sheets in front, so it cannot change the front view"
        severity = "INFO"
    x, y = geometry.representative_point().coords[0]
    return {
        "sheet_id": sheet_id,
        "file": name,
        "kind": kind,
        "severity": severity,
        "width_mm": width,
        "area_mm2": round(float(geometry.area), 4),
        "visible_area_mm2": round(float(visible), 4),
        "at_mm": [round(x, 3), round(y, 3)],
        "bbox_mm": [round(v, 3) for v in geometry.bounds],
        "note": note,
        "_geometry": geometry,
    }


def assess_printability(stack: SheetStack, process: PrintProcess,
                        exported_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """FDM estimate from the exported planar geometry (exact geometry for vector sheets)."""

    from shapely.geometry import box
    from shapely.ops import unary_union

    from .cad import perimeter_band, polygons, sheet_geometry

    size = stack.grid.outer_sheet_mm
    names = stack.export_names()
    geometries = [sheet_geometry(stack, index) for index in range(len(stack.sheets))]
    front_of: list[Any] = [None] * len(geometries)
    union = None
    for index in range(len(geometries) - 1, -1, -1):
        front_of[index] = union
        union = geometries[index] if union is None else unary_union([union, geometries[index]])

    features: list[dict[str, Any]] = []
    sheets: list[dict[str, Any]] = []
    band = perimeter_band(stack)
    for index, (sheet, name, geometry) in enumerate(zip(stack.sheets, names, geometries)):
        hidden_by = front_of[index]
        found: list[dict[str, Any]] = []
        holes = [h for h in polygons(box(0, 0, size, size).difference(geometry)) if h.area > 1e-9]
        for hole in holes:
            width = inscribed_diameter_mm(hole, process.min_opening_mm)
            severity = _severity(width, process.nozzle_diameter_mm, process.min_opening_mm)
            if severity != "INFO":
                found.append(_feature(sheet.id, name, "opening_may_close", hole, width, severity, hidden_by,
                                      f"whole cutout is narrower than {process.min_opening_mm} mm"))
                continue
            residue, pieces = _narrow_parts(hole, process.min_opening_mm)
            for part in residue:
                part_width = inscribed_diameter_mm(part, process.min_opening_mm)
                found.append(_feature(sheet.id, name, "narrow_slot_or_gap", part, part_width,
                                      _severity(part_width, process.nozzle_diameter_mm, process.min_opening_mm),
                                      hidden_by, f"part of a cutout narrower than {process.min_opening_mm} mm"))
            if pieces > 1:
                found.append(_feature(sheet.id, name, "narrow_neck_in_opening", hole, None, "RISK", hidden_by,
                                      f"cutout has a neck narrower than {process.min_opening_mm} mm "
                                      f"(splits into {pieces} parts when it narrows)"))
        residue, _ = _narrow_parts(geometry.difference(band), process.min_positive_feature_mm)
        for part in residue:
            width = inscribed_diameter_mm(part, process.min_positive_feature_mm)
            found.append(_feature(sheet.id, name, "thin_material", part, width,
                                  _severity(width, process.line_width_mm, process.min_positive_feature_mm),
                                  hidden_by, f"material narrower than {process.min_positive_feature_mm} mm"))
        height = sheet.stack_height_mm
        layers = (height - process.first_layer_height_mm) / process.layer_height_mm + 1
        quantized = abs(layers - round(layers)) < 1e-6
        if not quantized:
            found.append({"sheet_id": sheet.id, "file": name, "kind": "height_not_layer_multiple", "severity": "INFO",
                          "note": f"{height} mm is {layers:.2f} layers of {process.layer_height_mm} mm "
                                  f"(first {process.first_layer_height_mm} mm); the slicer will round it"})
        if sheet.rim is not None:
            rim_layers = (sheet.rim["total_height_mm"] - sheet.thickness_mm) / process.layer_height_mm
            if abs(rim_layers - round(rim_layers)) > 1e-6:
                found.append({"sheet_id": sheet.id, "file": name, "kind": "rim_step_not_layer_multiple",
                              "severity": "INFO", "note": f"rim step {rim_layers:.2f} layers; the slicer will round it"})
        row = exported_rows[index] if exported_rows else None
        x0, y0, x1, y1 = geometry.bounds
        extent = (row["stl_bounds_mm"] if row else [[x0, y0, 0.0], [x1, y1, height]])
        dx, dy, dz = (extent[1][k] - extent[0][k] for k in range(3))
        fits = (dx <= process.bed_x_mm and dy <= process.bed_y_mm) or (dy <= process.bed_x_mm and dx <= process.bed_y_mm)
        if not fits or dz > process.max_z_mm:
            found.append({"sheet_id": sheet.id, "file": name, "kind": "exceeds_build_volume", "severity": "UNSUPPORTED",
                          "note": f"part {dx:.2f} x {dy:.2f} x {dz:.2f} mm, build volume "
                                  f"{process.bed_x_mm} x {process.bed_y_mm} x {process.max_z_mm} mm"})
        features += found
        worst = max((SEVERITIES.index(f["severity"]) for f in found), default=-1)
        sheets.append({
            "sheet_id": sheet.id,
            "file": name,
            "geometry_source": "exact vector geometry" if sheet.geometry is not None else "traced retained mask",
            "openings": len(holes),
            "fits_build_volume": fits and dz <= process.max_z_mm,
            "part_size_mm": [round(dx, 3), round(dy, 3), round(dz, 3)],
            "height_layers": round(layers, 3),
            "overhangs": "none: prismatic extrusion printed flat (rim on top of the base)",
            "worst_severity": SEVERITIES[worst] if worst >= 0 else "NONE",
        })
    counts = {s: sum(1 for f in features if f["severity"] == s) for s in SEVERITIES}
    status = ("UNSUPPORTED_FEATURES" if counts["UNSUPPORTED"] else "RISKS_REPORTED" if counts["RISK"]
              else "NO_RISKS_FOUND")
    return {
        "status": status,
        "basis": "estimate from exported planar geometry and the process parameters; not a slicer or a print",
        "process": asdict(process),
        "counts": counts,
        "sheets": sheets,
        "features": features,
    }


# -- views ---------------------------------------------------------------------------------
def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def side_section(stack: SheetStack, cad_dir: Path, path: Path, y_mm: float | None = None) -> dict[str, Any]:
    """True-scale section of the reopened STL solids at ``y_mm``, stacked at assembly z."""

    import trimesh
    from shapely.ops import polygonize, unary_union

    size = stack.grid.outer_sheet_mm
    y = size / 2 if y_mm is None else y_mm
    scale_full, scale_detail, detail_mm = 8, 40, 30.0
    total = sum(s.stack_height_mm for s in stack.sheets)
    sections = []
    for name, z0, sheet in zip(stack.export_names(), stack.assembly_z_mm(), stack.sheets):
        mesh = trimesh.load_mesh(Path(cad_dir) / "stl" / f"{name}.stl", file_type="stl", process=True)
        lines = trimesh.intersections.mesh_plane(mesh, [0, 1, 0], [0, y, 0])
        # Snap endpoints: neighbouring triangles compute the same crossing point separately.
        snap = lambda v: (round(float(v[0]), 5), round(float(v[2] + z0), 5))
        segments = [(snap(a), snap(b)) for a, b in lines if snap(a) != snap(b)]
        sections.append((sheet, unary_union(list(polygonize(segments)))))
    margin, label = 12, 40

    from .cad import polygons

    def panel(scale: int, width_mm: float) -> np.ndarray:
        height = int(total * scale) + 2 * margin
        image = np.full((height + label, int(width_mm * scale) + 2 * margin, 3), 255, np.uint8)
        for sheet, shape in sections:
            for poly in polygons(shape):
                pts = np.array([[margin + x * scale, margin + (total - z) * scale] for x, z in poly.exterior.coords])
                cv2.fillPoly(image, [np.round(pts).astype(np.int32)], _rgb(sheet.color), lineType=cv2.LINE_AA)
                cv2.polylines(image, [np.round(pts).astype(np.int32)], True, (60, 60, 60), 1, cv2.LINE_AA)
        return image

    full = panel(scale_full, size)
    detail = panel(scale_detail, detail_mm)
    cv2.putText(full, f"section at y = {y:g} mm, true scale ({scale_full} px/mm); front of the stack is up",
                (margin, full.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.putText(detail, f"left {detail_mm:g} mm, true scale ({scale_detail} px/mm)",
                (margin, detail.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    width = max(full.shape[1], detail.shape[1])
    pad = lambda im: np.pad(im, ((0, 0), (0, width - im.shape[1]), (0, 0)), constant_values=255)
    Image.fromarray(np.concatenate([pad(full), pad(detail)], axis=0)).save(path)
    return {"section_y_mm": y, "total_height_mm": round(total, 6), "source": "reopened STL, plane section"}


def labelled_exploded(stack: SheetStack, exploded: Path, path: Path) -> None:
    """The exploded preview with a legend mapping each sheet (front first) to its file."""

    image = np.asarray(Image.open(exploded).convert("RGB"))
    rows = list(zip(range(len(stack.sheets)), stack.export_names(), stack.sheets))[::-1]
    legend = np.full((max(image.shape[0], 30 + 26 * len(rows)), 470, 3), 255, np.uint8)
    cv2.putText(legend, "front", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    for line, (order, name, sheet) in enumerate(rows):
        top = 30 + 26 * line
        cv2.rectangle(legend, (10, top), (34, top + 18), _rgb(sheet.color), -1)
        cv2.rectangle(legend, (10, top), (34, top + 18), (60, 60, 60), 1)
        text = f"{order:02d}  {name}  {sheet.stack_height_mm:g} mm" + ("  rim" if sheet.rim else "")
        cv2.putText(legend, text, (44, top + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.putText(legend, "back (order 00 prints first in the stack)", (10, 30 + 26 * len(rows) + 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    image = np.pad(image, ((0, legend.shape[0] - image.shape[0]), (0, 0), (0, 0)), constant_values=255)
    Image.fromarray(np.concatenate([image, legend], axis=1)).save(path)


def printability_maps(stack: SheetStack, report: dict[str, Any], output: Path) -> list[str]:
    """Per-sheet image: sheet material, RISK features orange, UNSUPPORTED red."""

    from .cad import rasterize_geometry
    from .preview import sheet_image

    output.mkdir(parents=True, exist_ok=True)
    files = []
    for sheet, name in zip(stack.sheets, stack.export_names()):
        image = (0.5 * sheet_image(sheet.retained, sheet.color) + 0.5 * 255).astype(np.uint8)
        for feature in report["features"]:
            if feature.get("file") == name and "_geometry" in feature and feature["severity"] != "INFO":
                mask = rasterize_geometry(feature["_geometry"], stack.grid)
                if not mask.any():  # below one grid pixel: mark its location
                    mask = np.zeros_like(mask)
                    r, c = _pixel(stack, feature["at_mm"])
                    mask[max(r - 3, 0):r + 4, max(c - 3, 0):c + 4] = True
                image[mask] = (255, 0, 0) if feature["severity"] == "UNSUPPORTED" else (255, 140, 0)
        path = output / f"{name}.png"
        Image.fromarray(image).save(path)
        files.append(path.name)
    return files


def _pixel(stack: SheetStack, point: list[float]) -> tuple[int, int]:
    mm, n = stack.grid.mm_per_pixel, stack.grid.outer_pixels
    return min(n - 1, max(0, n - 1 - int(point[1] / mm))), min(n - 1, max(0, int(point[0] / mm)))


# -- kit -----------------------------------------------------------------------------------
def _comparisons(stack: SheetStack, validation: dict[str, Any], manifest: dict[str, Any],
                 check: dict[str, Any], previews: dict[str, Any]) -> dict[str, Any]:
    rows = manifest["layers"]
    review = stack.review or {}
    return {
        "approved_artwork_vs_reviewed_stack": {
            "front_view_mismatched_px": validation["reconstruction"].get("mismatched_pixels"),
            "compared_px": validation["reconstruction"].get("compared_pixels"),
            "artwork": "reviewed target (accept_artwork edits applied)" if review else "approved target",
            "deviation_from_source_artwork_px": (review.get("current") or {}).get("deviation_from_source_artwork_px"),
            "edits": len(review.get("edits", [])),
        },
        "reviewed_stack_vs_exported_geometry": {
            r["layer"]: {"geometry_px_diff": r.get("geometry_px_diff_from_mask"),
                         "stl_vs_exact_geometry_area_mm2": r.get("stl_vs_geometry_area_mm2")}
            for r in check["layers"]
        },
        "exported_vs_reopened_cad": {
            r["layer"]: {"step_solids": r["step_solids"], "step_valid": r["step_valid"],
                         "step_volume_vs_expected_pct": r["step_volume_vs_expected_pct"],
                         "stl_volume_vs_expected_pct": r["volume_vs_area_x_thickness_pct"],
                         "stl_watertight": r["stl_watertight"], "stl_components": r["stl_components"],
                         "stl_px_diff": next(c["stl_px_diff_from_mask"] for c in check["layers"] if c["layer"] == r["layer"])}
            for r in rows
        },
        "approved_artwork_vs_reopened_cad_front_view": {
            "mismatched_px": check["reconstruction_from_stl"].get("mismatched_pixels"),
            "stack_mismatch_px": check["stack_mismatch_px"],
            "preview_vs_reviewed_px": previews["assembled_px_diff_from_planned"],
            "source_comparison_mismatched_px": previews.get("source_comparison_mismatched_pixels"),
        },
    }


def _cad_verdict(validation: dict[str, Any], manifest: dict[str, Any], check: dict[str, Any],
                 previews: dict[str, Any]) -> tuple[bool, list[str]]:
    problems = []
    if validation["status"] != "PASS":
        problems += validation["failures"]
    if not check["pass"]:
        problems.append("reopened CAD failed revalidation")
    for r in manifest["layers"]:
        if not (r["step_valid"] and r["step_solids"] == 1):
            problems.append(f"{r['layer']}: STEP is not one valid solid")
        if not (r["stl_watertight"] and r["stl_components"] == 1):
            problems.append(f"{r['layer']}: STL is not one watertight component")
        for key in ("step_volume_vs_expected_pct", "volume_vs_area_x_thickness_pct"):
            if abs(r[key]) > VOLUME_TOLERANCE_PCT:
                problems.append(f"{r['layer']}: {key} = {r[key]} %")
        if "rim" in r and abs(r["stl_height_mm"] - r["expected_height_mm"]) > 1e-3:
            problems.append(f"{r['layer']}: rim height {r['stl_height_mm']} != {r['expected_height_mm']} mm")
    if check["reconstruction_from_stl"].get("mismatched_pixels"):
        problems.append("front view of the reopened STL differs from the artwork")
    if previews["assembled_px_diff_from_planned"]:
        problems.append("previews from the reopened STL differ from the reviewed stack")
    return not problems, problems


def _assembly(stack: SheetStack, manifest: dict[str, Any], process: PrintProcess) -> list[dict[str, Any]]:
    rows = {r["layer"]: r for r in manifest["layers"]}
    out = []
    count = len(stack.sheets)
    for order, (sheet, name, z0) in enumerate(zip(stack.sheets, stack.export_names(), stack.assembly_z_mm())):
        row = rows[name]
        volume = row["stl_volume_mm3"]
        out.append({
            "order_back_to_front": order,
            "order_front_to_back": count - 1 - order,
            "file_step": f"cad/step/{name}.step",
            "file_stl": f"cad/stl/{name}.stl",
            "sheet_id": sheet.id,
            "color_hex": sheet.color,
            "color_rgb": list(_rgb(sheet.color)),
            "filament": f"one filament per sheet; match color {sheet.color} (STL carries no color)",
            "thickness_mm": sheet.thickness_mm,
            "rim": sheet.rim,
            "stack_height_mm": sheet.stack_height_mm,
            "assembly_z0_mm": round(z0, 6),
            "assembly_z1_mm": round(z0 + sheet.stack_height_mm, 6),
            "print_placement": {"as_exported": True, "rotate_xyz_deg": [0, 0, 0],
                                "bed_face": "back face (z = 0) on the bed; top face faces the viewer"},
            "stl_bounds_mm": row["stl_bounds_mm"],
            "volume_mm3": volume,
            "estimated_filament_g": round(volume / 1000 * process.filament_density_g_cm3, 2),
        })
    return out


def _guide(stack: SheetStack, kit: dict[str, Any]) -> str:
    lines = [
        f"# Print and stack guide: {stack.name}",
        "",
        "Generated by `layerforge_l1 stack-kit`. Status levels are separate:",
        "",
        f"- CAD: **{kit['status']['cad']}** (proven by reopening every STEP/STL; see print_kit.json).",
        f"- FDM estimate: **{kit['status']['fdm_estimate']}** for process `{kit['printability']['process']['name']}` "
        f"({kit['printability']['process']['source']}). An estimate, not a slicer result.",
        "- Physical: **NOT_VERIFIED**. Only a real print and your review (L-G10) can change this.",
        "",
        "## Sheets (print each flat, as exported; one filament per sheet)",
        "",
        "| Order (back=00) | File | Color | Height mm | Rim | Assembly z mm | Est. filament g |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in kit["assembly_back_to_front"]:
        rim = (f"{row['rim']['width_mm']:g} mm wide, {row['rim']['total_height_mm']:g} mm total" if row["rim"] else "none")
        lines.append(f"| {row['order_back_to_front']:02d} | {Path(row['file_stl']).name} | {row['color_hex']} | "
                     f"{row['stack_height_mm']:g} | {rim} | {row['assembly_z0_mm']:g} to {row['assembly_z1_mm']:g} | "
                     f"{row['estimated_filament_g']:g} |")
    lines += [
        "",
        "## Stacking",
        "",
        "1. Print every file flat as exported (back face on the bed, no rotation). STL has no color: "
        "load the filament listed for each file.",
        "2. Stack from order 00 (back) upwards; the highest order is the front. Every sheet has the same "
        f"{stack.grid.outer_sheet_mm:g} mm square outline: align the outer edges (temporary clamps or tape "
        "are enough for a test; no frame, housing or glue is required by the design).",
        "3. A sheet with a rim carries the next sheet on its rim; the gap above its interior is intended.",
        "",
        "## Printability findings (estimate)",
        "",
    ]
    findings = [f for f in kit["printability"]["features"] if f["severity"] != "INFO"]
    if not findings:
        lines.append("No RISK or UNSUPPORTED features found for this process.")
    for f in findings:
        lines.append(f"- {f['severity']} `{f['file']}` {f['kind']} at {f.get('at_mm')} mm"
                     + (f", width {f['width_mm']} mm" if f.get("width_mm") is not None else "")
                     + f": {f['note']}.")
    lines += ["", "Maps: `previews/printability/` (orange = RISK, red = UNSUPPORTED).", ""]
    bambu = kit.get("bambu_3mf")
    if bambu:
        lines += ["## Bambu Studio project (optional)", ""]
        if bambu["status"] == "WRITTEN":
            check = bambu["bambu_studio_check"]
            lines += [
                f"`{bambu['file']}` for {bambu['printer']}: one plate per sheet, plate 1 = order 00 (back). Each sheet "
                "lies flat as exported (back face on the bed), placed clear of the printer's keep-out zones.",
                "",
                f"- Validation: **{bambu['validation']['level']}**"
                + (f" ({check['tool']}: loaded and sliced every plate)" if check["status"] == "PASS" else
                   f" (Bambu Studio check: {check['status']})") + ".",
                f"- Process preset `{bambu['process_preset']}`; filaments are Generic PLA slots carrying the sheet colors "
                "(choose your real filament in Bambu Studio; the color stays).",
                "- No AMS is assumed: every plate uses one filament. Without an AMS, load the spool shown for the plate "
                "before printing it.",
                "",
                "| Plate | Sheet | Filament slot | Color |",
                "|---|---|---|---|",
            ]
            lines += [f"| {p['plate']} | {p['plate_name']} | {p['filament_slot']} | {p['color_hex']} |"
                      for p in bambu["plates"]]
        else:
            lines += ["Not written: " + "; ".join(bambu.get("problems", []))]
        lines.append("")
    return "\n".join(lines)


def build_print_kit(source: Path, output: Path, process: PrintProcess | None = None,
                    progress: Callable[..., None] | None = None, bambu_printer: str | None = None) -> dict[str, Any]:
    """Validate, export, reopen and assess a saved stack; write the kit to ``output``.

    ``bambu_printer`` (a ``bambu3mf.BAMBU_PRINTERS`` preset) also writes the optional Bambu
    Studio multi-plate project from the reopened STL files; the STEP/STL files are unchanged."""

    from .cad import export_stack, revalidate_cad
    from .preview import generate_previews

    process = process or PrintProcess()
    source, output = Path(source), Path(output)
    if bambu_printer:
        from .bambu3mf import BAMBU_PRINTERS

        if bambu_printer not in BAMBU_PRINTERS:
            raise PrintKitError(f"unsupported Bambu printer {bambu_printer!r}; choose one of {sorted(BAMBU_PRINTERS)}")
    stack = load_stack(source)
    validation = validate_stack(stack)
    if validation["status"] != "PASS":
        raise PrintKitError(f"stack is not valid and is not exported: {validation['failures']}")
    if output.exists() and any(output.iterdir()):
        raise PrintKitError(f"{output} is not empty; choose a new kit directory")
    save_stack(stack, output / "stack")  # the exact reviewed stack the kit was built from
    manifest = export_stack(stack, output / "cad", progress=progress)
    check, stl_masks = revalidate_cad(stack, output / "cad", progress=progress)
    (output / "cad" / "cad_revalidation.json").write_text(json.dumps(check, indent=2) + "\n", encoding="utf-8")
    previews = generate_previews(stack, output / "previews", stl_masks, "reopened STL in cad/stl")
    cad_ok, problems = _cad_verdict(validation, manifest, check, previews)
    printability = assess_printability(stack, process, manifest["layers"])
    side = side_section(stack, output / "cad", output / "previews" / "assembled_side.png")
    labelled_exploded(stack, output / "previews" / "exploded.png", output / "previews" / "exploded_labelled.png")
    maps = printability_maps(stack, printability, output / "previews" / "printability")
    for feature in printability["features"]:
        feature.pop("_geometry", None)
    kit = {
        "schema": KIT_SCHEMA,
        "schema_version": KIT_SCHEMA_VERSION,
        "name": stack.name,
        "source_stack": {"path": source.as_posix(), "manifest_sha256": file_sha256(
            source / MANIFEST_NAME if source.is_dir() else source), "copy": "stack/"},
        "provenance": stack.provenance,
        "review": {"edits": len(stack.review["edits"]), "status": stack.review["current"]["status"]}
        if stack.review else None,
        "status": {
            "cad": "CAD_VALID" if cad_ok else "CAD_INVALID",
            "fdm_estimate": printability["status"],
            "physical": "NOT_VERIFIED",
        },
        "cad_problems": problems,
        "units": "mm",
        "outer_sheet_mm": stack.grid.outer_sheet_mm,
        "total_stack_height_mm": round(sum(s.stack_height_mm for s in stack.sheets), 6),
        "assembly_back_to_front": _assembly(stack, manifest, process),
        "filament_by_color": {},
        "comparisons": _comparisons(stack, validation, manifest, check, previews),
        "printability": printability,
        "previews": {"files": previews["files"] + ["assembled_side.png", "exploded_labelled.png"]
                     + [f"printability/{m}" for m in maps], "side_section": side},
        "not_included": {"3mf": "not produced: per-sheet STEP/STL plus this manifest carry order and color; "
                                "a 3MF would need slicer verification that is not available here"},
    }
    for row in kit["assembly_back_to_front"]:
        entry = kit["filament_by_color"].setdefault(row["color_hex"], {"sheets": [], "estimated_g": 0.0})
        entry["sheets"].append(Path(row["file_stl"]).stem)
        entry["estimated_g"] = round(entry["estimated_g"] + row["estimated_filament_g"], 2)
    (output / "print_kit.json").write_text(json.dumps(kit, indent=2) + "\n", encoding="utf-8")
    if bambu_printer:
        from .bambu3mf import write_bambu_project

        if progress:
            progress("BAMBU_3MF_STARTED", printer=bambu_printer)
        kit["bambu_3mf"] = write_bambu_project(output, bambu_printer)
        if kit["bambu_3mf"]["status"] == "WRITTEN":
            kit["not_included"].pop("3mf")
        else:
            kit["not_included"]["3mf"] = "Bambu Studio project not written: " + "; ".join(kit["bambu_3mf"]["problems"])
        (output / "print_kit.json").write_text(json.dumps(kit, indent=2) + "\n", encoding="utf-8")
    (output / "PRINT_AND_STACK_GUIDE.md").write_text(_guide(stack, kit), encoding="utf-8")
    write_checksums(output)
    return kit


def write_checksums(output: Path) -> Path:
    files = sorted(p for p in output.rglob("*") if p.is_file() and p.name != "checksums.sha256")
    lines = [f"{file_sha256(p)}  {p.relative_to(output).as_posix()}" for p in files]
    path = output / "checksums.sha256"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def verify_checksums(output: Path) -> list[str]:
    """Files whose content no longer matches ``checksums.sha256`` (or that are missing)."""

    bad = []
    for line in (Path(output) / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        path = Path(output) / name
        if not path.is_file() or file_sha256(path) != digest:
            bad.append(name)
    return bad


def step_body(path: Path) -> str:
    """STEP text for reproducibility checks: without the header (it carries a timestamp)
    and with OpenCASCADE's per-process product counter ("... translator 7.8 <n>") removed."""

    import re

    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return re.sub(r"(Open CASCADE STEP translator [\d.]+) \d+", r"\1", text[text.index("DATA;"):])


__all__ = [
    "PrintKitError", "PrintProcess", "assess_printability", "build_print_kit", "inscribed_diameter_mm",
    "step_body", "verify_checksums", "write_checksums",
]
