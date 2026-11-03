"""Issue inspector: where each diagnostic is, from the existing checks only.

Nothing here decides PASS/FAIL or changes geometry.  Every issue comes from a check that already
runs, evaluated with the same functions and the same thresholds:

* sheet validation (``validation.validate_sheet_mask``): floating material (4-neighbor components
  that do not reach the perimeter), material whose structural core is not connected to the
  perimeter at the required width (``audit_connection_width``: the affected material and the
  discrete width measurement of that audit), thin decorative visible material (warning only),
  gaps in the shared perimeter band, visible pixels outside the retained material and front-view
  pixels that differ from the artwork;
* the FDM geometry check (``printkit.assess_printability``, the function of the printability
  estimate): openings that may close, narrow parts or necks of openings and thin material, with
  the process thresholds that were used.  It runs on the shown sheets whenever no current saved
  estimate exists, so the sheets are always checked;
* the proposal: blocked features of a failed reveal closure (the planner's obstruction map),
  intake regions that match no palette color and details the intake changed (artwork only; never
  placed on a sheet).

Locations are exact: outlines follow the pixels (raster checks) or the polygons (vector checks
and the FDM estimate) the check found.  A diagnostic without a reliable location is listed as
unlocalized instead of being placed approximately.  Coordinates are sheet mm (x right, y up, the
outer sheet including the perimeter band), the convention of the stack and the edit canvas.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .model import SheetStack, StackGrid

SEVERITY_ORDER = ("FAIL", "BLOCKER", "UNSUPPORTED", "RISK", "WARN", "INFO")
MAX_OUTLINE_RUNS = 6000  # larger pixel sets are outlined by their bounding box only (stated in the issue)

# kind -> (title, category, where the fix is made)
KINDS: dict[str, tuple[str, str, str]] = {
    "floating_material": ("Floating material", "disconnected", "layer"),
    "narrow_connection": ("Material held by a too-narrow connection", "narrow_material", "layer"),
    "thin_visible_detail": ("Thin visible detail", "narrow_material", "layer"),
    "perimeter_gap": ("Gap in the shared perimeter", "other", "layer"),
    "visible_outside_material": ("Visible area without material", "other", "layer"),
    "front_view_mismatch": ("Front view differs from the artwork", "other", "layer"),
    "opening_may_close": ("Opening may close", "narrow_opening", "layer"),
    "narrow_slot_or_gap": ("Narrow part of an opening", "narrow_opening", "layer"),
    "narrow_neck_in_opening": ("Opening with a narrow neck", "narrow_opening", "layer"),
    "thin_material": ("Thin material", "narrow_material", "layer"),
    "planner_blocked_feature": ("Feature the planner cannot support", "artwork", "artwork"),
    "unmatched_color_region": ("Region matching no palette color", "artwork", "artwork"),
    "intake_detail_changed": ("Detail changed by intake", "artwork", "artwork"),
}
FDM_THRESHOLDS = {  # kind -> (hard, soft) process fields, as in core/resize.py
    "opening_may_close": ("nozzle_diameter_mm", "min_opening_mm"),
    "narrow_slot_or_gap": ("nozzle_diameter_mm", "min_opening_mm"),
    "narrow_neck_in_opening": ("nozzle_diameter_mm", "min_opening_mm"),
    "thin_material": ("line_width_mm", "min_positive_feature_mm"),
}


# -- outlines -------------------------------------------------------------------------------------
def _ring(coords: Any) -> list[list[float]]:
    return [[round(float(x), 6), round(float(y), 6)] for x, y, *_ in coords]


def geometry_outline(geometry: Any) -> list[list[list[list[float]]]]:
    """Polygons (exterior ring, then holes) of a shapely geometry, in its own mm coordinates."""

    from .cad import polygons

    return [[_ring(p.exterior.coords), *[_ring(i.coords) for i in p.interiors]] for p in polygons(geometry)
            if not p.is_empty]


def mask_outline(mask: np.ndarray, grid: StackGrid) -> tuple[list, list[float], bool]:
    """Exact pixel-edge outline of an outer-grid mask: (polygons, bbox_mm, exact).

    Built from horizontal pixel runs, so every edge lies on a pixel edge.  Very large pixel sets
    get their bounding box only (``exact`` False)."""

    from shapely.geometry import box
    from shapely.ops import unary_union

    rows, cols = np.nonzero(mask)
    mm, n = grid.mm_per_pixel, grid.outer_pixels
    bbox = [round(cols.min() * mm, 6), round((n - rows.max() - 1) * mm, 6),
            round((cols.max() + 1) * mm, 6), round((n - rows.min()) * mm, 6)]
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    crop = mask[r0:r1, c0:c1].astype(np.int8)
    edges = np.diff(np.pad(crop, ((0, 0), (1, 1))), axis=1)
    starts, ends = np.nonzero(edges == 1), np.nonzero(edges == -1)
    if len(starts[0]) > MAX_OUTLINE_RUNS:
        x0, y0, x1, y1 = bbox
        return [[[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]], bbox, False
    boxes = [box((c0 + s) * mm, (n - r0 - r - 1) * mm, (c0 + e) * mm, (n - r0 - r) * mm)
             for r, s, e in zip(starts[0], starts[1], ends[1])]
    return geometry_outline(unary_union(boxes).simplify(0)), bbox, True


def _centre(bbox: list[float]) -> list[float]:
    return [round((bbox[0] + bbox[2]) / 2, 3), round((bbox[1] + bbox[3]) / 2, 3)]


def _issue(kind: str, source: str, severity: str, sheet: str | None, outline: list | None, bbox: list[float] | None,
           measured: dict[str, Any] | None, threshold: dict[str, Any] | None, details: dict[str, Any],
           exact: bool = True, at: list[float] | None = None, alt_artwork: bool = False) -> dict[str, Any]:
    title, category, destination = KINDS[kind]
    return {"kind": kind, "title": title, "category": category, "source": source, "severity": severity,
            "sheet": sheet, "outline_mm": outline, "bbox_mm": bbox, "at_mm": at or (_centre(bbox) if bbox else None),
            "outline_exact": exact, "measured": measured, "threshold": threshold, "destination": destination,
            "also_artwork": alt_artwork and destination == "layer", "details": details}


def _mask_issue(kind: str, source: str, severity: str, sheet: str | None, mask: np.ndarray, grid: StackGrid,
                measured: dict[str, Any] | None, threshold: dict[str, Any] | None, details: dict[str, Any],
                alt_artwork: bool = False) -> dict[str, Any]:
    outline, bbox, exact = mask_outline(mask, grid)
    return _issue(kind, source, severity, sheet, outline, bbox, measured, threshold,
                  {**details, "pixels": int(mask.sum())}, exact, alt_artwork=alt_artwork)


# -- sheet validation ------------------------------------------------------------------------------
def _neck(retained: np.ndarray, core: np.ndarray, held: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """The narrow material joining ``held`` (material around a cut-off structural core) to the rest.

    Material the structural opening does not reach (``retained`` minus the dilated cores of the same
    audit) is narrower than the required width; its 8-connected pieces that touch both the held
    material and other material are the necks."""

    reached = cv2.dilate(core.astype(np.uint8), kernel).astype(bool) & retained
    thin = retained & ~reached
    if not thin.any():
        return thin
    ring = np.ones((3, 3), np.uint8)
    near_held = cv2.dilate(held.astype(np.uint8), ring).astype(bool)
    near_rest = cv2.dilate((reached & ~held).astype(np.uint8), ring).astype(bool)
    count, labels = cv2.connectedComponents(thin.astype(np.uint8), connectivity=8)
    neck = np.zeros(retained.shape, bool)
    for label in range(1, count):
        piece = labels == label
        if (piece & near_held).any() and (piece & near_rest).any():
            neck |= piece
    return neck


def sheet_issues(stack: SheetStack) -> list[dict[str, Any]]:
    """Validation findings of every sheet, located (the same audits as ``validate_sheet_mask``)."""

    from ..connectivity_repair import audit_connection_width
    from ..layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
    from .validation import minimum_width_mm

    grid = stack.grid
    poc = grid.poc_config()
    p, mm = grid.perimeter_pixels, grid.mm_per_pixel
    perimeter = grid.perimeter_mask()
    nominal = minimum_width_mm()
    issues: list[dict[str, Any]] = []
    for sheet in stack.sheets:
        retained, visible = sheet.retained, sheet.visible
        count, labels = cv2.connectedComponents(retained.astype(np.uint8), connectivity=4)
        anchored = set(np.unique(labels[perimeter & retained]).tolist())
        floating = np.zeros(retained.shape, bool)
        for label in range(1, count):
            if label in anchored:
                continue
            piece = labels == label
            floating |= piece
            issues.append(_mask_issue(
                "floating_material", "validation", "FAIL", sheet.id, piece, grid,
                {"label": "area", "value": round(float(piece.sum()) * mm * mm, 3), "unit": "mm²"},
                {"label": "rule", "text": "every material piece must reach the shared perimeter"},
                {"check": "four_neighbor (audit_edge_connectivity)", "visible_pixels": int((visible & piece).sum())},
                alt_artwork=bool((visible & piece).any())))
        report, maps = audit_connection_width(retained, visible[p:-p, p:-p], poc, DEFAULT_OPTIMIZATION_CONFIG)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (report["morphology_kernel_size_pixels"],) * 2)
        for row in report["orphan_structural_core_details"]:
            core = maps["core_labels"] == row["component"]
            if (core & floating).any():
                continue  # inside floating material: reported above
            affected = cv2.dilate(core.astype(np.uint8), kernel).astype(bool) & retained
            visible_core = row.get("visible_core_overlap_pixels", 0) > 0
            neck = _neck(retained, maps["core"], affected, kernel)
            issues.append(_mask_issue(
                "narrow_connection", "validation", "FAIL", sheet.id, neck if neck.any() else affected, grid,
                {"label": "widest passing connection test", "value": round(row["largest_passing_kernel_mm"], 3), "unit": "mm"},
                {"label": "required (on this grid)", "value": round(row["required_kernel_mm"], 3), "unit": "mm",
                 "nominal_mm": nominal},
                {"check": "width_status (audit_connection_width)", "classification": row.get("classification"),
                 "measurement_scope": row["measurement_scope"],
                 "highlight": ("the too-narrow material joining the held piece to the rest (material no disk of the "
                               "required width reaches)") if neck.any() else
                              "the material whose structural core is cut off from the perimeter (no neck pixels found)",
                 "held_material_outline_mm": mask_outline(affected, grid)[0] if neck.any() else None},
                alt_artwork=visible_core))
        thin = maps["thin_decorative_visible"]
        if thin.any():
            count_t, labels_t = cv2.connectedComponents(thin.astype(np.uint8), connectivity=4)
            for label in range(1, count_t):
                piece = labels_t == label
                issues.append(_mask_issue(
                    "thin_visible_detail", "validation", "INFO", sheet.id, piece, grid,
                    {"label": "area", "value": round(float(piece.sum()) * mm * mm, 3), "unit": "mm²"},
                    {"label": "structural width", "value": round(report["morphology_kernel_size_pixels"] * mm, 3),
                     "unit": "mm", "nominal_mm": nominal},
                    {"check": "thin_decorative_warnings (warning only; not a failure)"}, alt_artwork=True))
        gap = perimeter & ~retained
        if gap.any():
            issues.append(_mask_issue("perimeter_gap", "validation", "FAIL", sheet.id, gap, grid,
                                      {"label": "missing", "value": round(float(gap.sum()) * mm * mm, 3), "unit": "mm²"},
                                      {"label": "rule", "text": "the perimeter band is complete on every sheet"},
                                      {"check": "shared_perimeter_complete"}))
        outside = visible & ~retained
        if outside.any():
            issues.append(_mask_issue("visible_outside_material", "validation", "FAIL", sheet.id, outside, grid,
                                      {"label": "pixels", "value": int(outside.sum()), "unit": "px"},
                                      {"label": "rule", "text": "visible pixels lie inside the retained material"},
                                      {"check": "visible_inside_retained"}))
    found = stack.target_labels()
    if found is not None:
        target, palette = found
        shown = stack.composite_sheet_index()
        label_of = np.array([palette.index(s.color) if s.color in palette else -2 for s in stack.sheets] + [-1])
        inside = ~perimeter & ~stack.frame_mask()
        differs = inside & (label_of[shown] != target)
        if differs.any():
            issues.append(_mask_issue("front_view_mismatch", "validation", "FAIL", None, differs, grid,
                                      {"label": "pixels", "value": int(differs.sum()), "unit": "px"},
                                      {"label": "allowed", "value": 0, "unit": "px"},
                                      {"check": "reconstruction (front view vs artwork)"}))
    return issues


# -- FDM estimate ----------------------------------------------------------------------------------
def fdm_issues(report: dict[str, Any], source: str = "fdm_estimate") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(located, unlocalized) findings of an FDM estimate (saved, or computed now by
    ``printkit.assess_printability`` with ``source`` "geometry_check") with its own process thresholds."""

    process = report.get("process", {})
    located, other = [], []
    for f in report.get("features", []):
        kind = f["kind"]
        if kind not in KINDS or not f.get("bbox_mm"):
            other.append({"source": source, "code": kind, "severity": f["severity"], "sheet": f.get("sheet_id"),
                          "text": f.get("note", "")})
            continue
        if f.get("outline_mm") is None and f.get("_geometry") is not None:
            f = {**f, "outline_mm": geometry_outline(f["_geometry"])}
        hard_name, soft_name = FDM_THRESHOLDS[kind]
        threshold = {"label": "process", "value": process.get(soft_name), "unit": "mm", "field": soft_name,
                     "unsupported_below_mm": process.get(hard_name), "unsupported_field": hard_name}
        measured = ({"label": "narrowest width", "value": f["width_mm"], "unit": "mm"} if f.get("width_mm") is not None
                    else {"label": "width", "text": "not measured (a neck)"})
        outline = f.get("outline_mm")
        exact = outline is not None
        if outline is None:  # saved before outlines were recorded: the bounding box the check reported
            x0, y0, x1, y1 = f["bbox_mm"]
            outline = [[[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]]
        located.append(_issue(kind, source, f["severity"], f.get("sheet_id"), outline, f["bbox_mm"], measured,
                              threshold, {"note": f.get("note"), "file": f.get("file"), "area_mm2": f.get("area_mm2"),
                                          "visible_area_mm2": f.get("visible_area_mm2"), "process": process.get("name")},
                              exact, at=f.get("at_mm"), alt_artwork=(f.get("visible_area_mm2") or 0) > 0))
    return located, other


# -- proposal (artwork level) ----------------------------------------------------------------------
def embed_artwork(mask: np.ndarray, grid: StackGrid) -> np.ndarray:
    full = np.zeros((grid.outer_pixels,) * 2, bool)
    p = grid.perimeter_pixels
    full[p:p + mask.shape[0], p:p + mask.shape[1]] = mask
    return full


def obstruction_issues(obstruction: np.ndarray, target: np.ndarray, grid: StackGrid, structural_mm: float,
                       candidate: str) -> list[dict[str, Any]]:
    """Blocked pieces of a failed reveal closure (artwork grid masks), measured as in core/resize.py."""

    from scipy import ndimage

    from .resize import obstruction_issues as measure

    measured = measure(obstruction, target, grid.mm_per_pixel, structural_mm)
    pieces, _ = ndimage.label(obstruction, structure=np.ones((3, 3), bool))
    issues = []
    for index, row in enumerate(measured, start=1):
        piece = embed_artwork(pieces == index, grid)
        issues.append(_mask_issue(
            "planner_blocked_feature", "planner", "BLOCKER", None, piece, grid,
            {"label": "width (estimate)", "value": row["width_mm"], "unit": "mm"},
            {"label": "structural width", "value": structural_mm, "unit": "mm"},
            {"candidate": candidate, "map": "obstruction map of the failed closure",
             "note": row.get("note", "the reveal closure was infeasible around this feature")}))
    return issues


def unmatched_issues(unmatched: np.ndarray, grid: StackGrid, min_area_mm2: float) -> list[dict[str, Any]]:
    """Intake regions matching no palette color, at least ``IntakeConfig.unmatched_min_area_mm2`` (the
    saved map holds only those; 8-connected, as intake counts them)."""

    mm = grid.mm_per_pixel
    count, labels = cv2.connectedComponents(unmatched.astype(np.uint8), connectivity=8)
    issues = []
    for label in range(1, count):
        piece = labels == label
        area = float(piece.sum()) * mm * mm
        if area < min_area_mm2:
            continue
        issues.append(_mask_issue(
            "unmatched_color_region", "intake", "WARN", None, embed_artwork(piece, grid), grid,
            {"label": "area", "value": round(area, 3), "unit": "mm²"},
            {"label": "reported from", "value": min_area_mm2, "unit": "mm²"},
            {"map": "intake/unmatched_regions.png"}))
    return issues


def intake_detail_issues(details: list[dict[str, Any]], decisions: dict[str, Any], grid: StackGrid) -> list[dict[str, Any]]:
    """Recognizable details the intake recolored or removed (``INTAKE_DETAILS_CHANGED``), on the artwork.

    These are artwork/intake findings, not sheet defects.  Intake saves each detail's bounding box
    (artwork pixels) and a crop image, not its pixel mask, so the outline is that box (stated)."""

    mm, p, n = grid.mm_per_pixel, grid.perimeter_pixels, grid.outer_pixels
    issues = []
    for d in details:
        r0, c0, r1, c1 = d["bbox_px"]
        x0, x1 = round((c0 + p) * mm, 6), round((c1 + p) * mm, 6)
        y0, y1 = round((n - p - r1) * mm, 6), round((n - p - r0) * mm, 6)
        outline = [[[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]]
        decided = d["id"] in decisions
        what = ("color missing from the palette" if d["kind"] == "color_not_in_palette"
                else "detail erased by the intake cleanup")
        issues.append(_issue(
            "intake_detail_changed", "intake", "INFO" if decided else "WARN", None, outline, [x0, y0, x1, y1],
            {"label": "width", "value": d["width_mm"], "unit": "mm"} if d.get("width_mm") is not None else None,
            {"label": "rule", "text": "intake reports recognizable details it would recolor or erase (not a sheet check)"},
            {"detail": d["id"], "what": what, "original_hex": d.get("original_hex"),
             "result_hex": d.get("result_hex") or d.get("processed_hex"), "area_mm2": d.get("area_mm2"),
             "action": (d.get("action") or {}).get("action"), "decided": decided, "image": d.get("image"),
             "size_notes": d.get("size_notes", [])},
            exact=False))
    return issues


# -- artwork pixels for "Edit artwork" ---------------------------------------------------------------
def artwork_box(bbox_mm: list[float], grid: StackGrid) -> list[int]:
    """[row0, col0, row1, col1] (end exclusive) of a sheet-mm box on the artwork grid."""

    mm, n, p = grid.mm_per_pixel, grid.outer_pixels, grid.perimeter_pixels
    x0, y0, x1, y1 = bbox_mm
    eps = 1e-6
    c0, c1 = int(np.floor(x0 / mm + eps)) - p, int(np.ceil(x1 / mm - eps)) - p
    r0, r1 = n - int(np.ceil(y1 / mm - eps)) - p, n - int(np.floor(y0 / mm + eps)) - p
    a = grid.artwork_pixels
    clamp = lambda v: max(0, min(a, v))  # noqa: E731
    return [clamp(r0), clamp(c0), max(clamp(r1), clamp(r0) + 1), max(clamp(c1), clamp(c0) + 1)]


def number(issues: list[dict[str, Any]], grid: StackGrid) -> list[dict[str, Any]]:
    """Stable ids, severity order, and the artwork pixels for issues that can be edited there."""

    issues.sort(key=lambda i: (SEVERITY_ORDER.index(i["severity"]) if i["severity"] in SEVERITY_ORDER else 9,
                               i["sheet"] or "", i["kind"], i["bbox_mm"] or [0]))
    for n, issue in enumerate(issues, start=1):
        issue["id"] = f"issue_{n:03d}"
        if issue["bbox_mm"] and (issue["destination"] == "artwork" or issue["also_artwork"]):
            issue["artwork_box_px"] = artwork_box(issue["bbox_mm"], grid)
    return issues
