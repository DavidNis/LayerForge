"""Physical resizing: would a larger sheet make the undersized features printable?  A smaller sheet can
also be chosen (every width shrinks by the same ratio, so findings get worse; see ``predict``), and so can
a rectangular one: width and height are separate, each at least ``MIN_SHEET_MM``.

Every printability threshold is in millimetres (the 1.2 mm structural width of the planner,
the process's nozzle, line width, minimum opening and minimum material width).  Scaling the
whole artwork by ``f`` scales every feature width by ``f``, so a feature of width ``w`` that
needs ``t`` is cleared from ``f >= t / w``.  This module:

* turns the existing findings into per-issue requirements (``issue_requirements``):
  - FDM estimate features (``printkit.assess_printability``) of a planned stack;
  - for a failed plan, the planner's obstruction map: the local width of each blocked feature
    against the structural width (an estimate; a closure can also fail for non-size reasons);
* finds the usable size on the selected printer (``usable_sheet_mm``, ``sheet_fits``): the process bed,
  and the Bambu printable area minus keep-out zones when a Bambu printer is chosen; a sheet fits if its
  full outer size fits in either orientation (the user turns it in the slicer; nothing is rotated here);
* tabulates the remaining issues per size and suggests the smallest size that clears the most
  UNSUPPORTED findings that any fitting size can clear (``size_options``);
* previews a size (``preview_resize``): intake at the new size.  With the same proportions the pixel
  grid is the same (proportional boundary checks, visible-change comparison, details re-checked); with
  other proportions the source is fitted into the new aperture again (aspect kept, padded with the frame
  color, never stretched or cropped), which resamples the artwork; then the prediction.

Predictions assume the plan scales with the artwork.  It does not exactly: cells, the structural
width and the intake filters are in millimetres, so the retry re-plans and re-measures; only that
measurement counts.  No size is ever applied without the user's approval (``app.workspace``).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image
from scipy import ndimage

from .intake import IntakeConfig, load_image, prepare_target, use_edited_target
from .model import StackError, StackGrid
from .upscale import _crop, _save, _write, carry_decisions, compare_targets, recheck_previous_details

PREVIEW_SCHEMA = "layerforge.resize_preview"
ANALYSIS_SCHEMA = "layerforge.resize_analysis"
READY = "READY_TO_RESIZE"
BOUNDARY_FAILED = "BOUNDARY_CHECK_FAILED"
SIZE_STEP_MM = 1.0
# Smallest sheet width and height a resize may choose (USER_REQUIRED).
MIN_SHEET_MM = 100.0
# Findings are measured on a pixel grid: count only half a pixel of a measured width as reliable.
PIXEL_MARGIN = 0.5
# (hard, soft) thresholds per finding kind, as attributes of PrintProcess.
FDM_THRESHOLDS = {
    "opening_may_close": ("nozzle_diameter_mm", "min_opening_mm"),
    "narrow_slot_or_gap": ("nozzle_diameter_mm", "min_opening_mm"),
    "thin_material": ("line_width_mm", "min_positive_feature_mm"),
}
NOT_SIZE_RELATED = {
    "narrow_neck_in_opening": "neck width not measured; may improve with size, not predicted",
    "exceeds_build_volume": "a larger sheet makes this worse",
}


# -- issues --------------------------------------------------------------------------------------
def _factor(threshold: float, width: float, mm_per_pixel: float) -> float:
    reliable = width - PIXEL_MARGIN * mm_per_pixel
    return threshold / reliable if reliable > 0 else math.inf


def issue_requirements(features: list[dict[str, Any]], process: Any, mm_per_pixel: float) -> list[dict[str, Any]]:
    """UNSUPPORTED/RISK FDM findings with the scale factor that makes each supported / clear."""

    issues = []
    for number, f in enumerate(x for x in features if x.get("severity") in ("UNSUPPORTED", "RISK")):
        row = {"id": f"fdm_{number:04d}", "source": "fdm_estimate", "kind": f["kind"], "severity": f["severity"],
               "sheet": f.get("sheet_id"), "width_mm": f.get("width_mm"), "at_mm": f.get("at_mm"),
               "visible_area_mm2": f.get("visible_area_mm2")}
        if f["kind"] in FDM_THRESHOLDS and f.get("width_mm") is not None:
            hard, soft = (getattr(process, name) for name in FDM_THRESHOLDS[f["kind"]])
            row.update(threshold_supported_mm=hard, threshold_clear_mm=soft, size_related=True,
                       factor_supported=1.0 if f["severity"] == "RISK" else _factor(hard, f["width_mm"], mm_per_pixel),
                       factor_clear=_factor(soft, f["width_mm"], mm_per_pixel))
        else:
            row.update(size_related=False, note=NOT_SIZE_RELATED.get(f["kind"], "not a width finding"))
        issues.append(row)
    return issues


def obstruction_issues(obstruction: np.ndarray, target: np.ndarray, mm_per_pixel: float,
                       structural_width_mm: float) -> list[dict[str, Any]]:
    """Blocked features of a failed closure, with the factor that brings them to the structural width.

    ``obstruction`` and ``target`` are on the artwork grid.  The width of a blocked piece is twice
    the largest distance to another color inside it.  Estimate only: a closure can also be
    infeasible for non-size reasons (then the factor is ~1 and resizing is not predicted to help).
    """

    colors = np.unique(target.reshape(-1, 3), axis=0, return_inverse=True)[1].reshape(target.shape[:2])
    depth = np.zeros(colors.shape, np.float32)
    for value in np.unique(colors[obstruction]):
        region = colors == value
        depth[region] = ndimage.distance_transform_edt(region)[region]
    pieces, count = ndimage.label(obstruction, structure=np.ones((3, 3), bool))
    issues = []
    for index, box in enumerate(ndimage.find_objects(pieces), start=1):
        local = pieces[box] == index
        width = round(2 * float(depth[box][local].max()) * mm_per_pixel, 3)
        rows, cols = box
        factor = _factor(structural_width_mm, width, mm_per_pixel)
        issues.append({
            "id": f"blocked_{index:04d}", "source": "planner_obstruction", "kind": "unsupportable_feature",
            "severity": "UNSUPPORTED", "width_mm": width, "size_related": factor > 1.0,
            "at_mm": [round((cols.start + cols.stop) / 2 * mm_per_pixel, 2), round((rows.start + rows.stop) / 2 * mm_per_pixel, 2)],
            "area_mm2": round(float(local.sum()) * mm_per_pixel**2, 3),
            "threshold_supported_mm": structural_width_mm, "threshold_clear_mm": structural_width_mm,
            "factor_supported": factor, "factor_clear": factor,
            **({} if factor > 1.0 else {"note": "already at the structural width: blocked for another reason"}),
        })
    return issues


# -- printer limit -----------------------------------------------------------------------------------
def _bambu_printer(preset: str) -> tuple[Any, bool]:
    from . import bambu3mf

    if preset not in bambu3mf.BAMBU_PRINTERS:
        raise StackError(f"unsupported Bambu printer {preset!r}")
    printer = bambu3mf.BAMBU_PRINTERS[preset]
    profiles = bambu3mf.find_profiles()
    if profiles is not None:
        printer = bambu3mf.printer_from_profiles(printer, profiles.resolve(printer.preset))
    return printer, profiles is not None


def _fits_bed(width: float, height: float, bed_x: float, bed_y: float) -> bool:
    return (width <= bed_x + 1e-9 and height <= bed_y + 1e-9) or (height <= bed_x + 1e-9 and width <= bed_y + 1e-9)


def _fits_bambu(width: float, height: float, printer: Any) -> bool:
    from . import bambu3mf

    return (bambu3mf.place_on_bed((width, height), printer) is not None
            or bambu3mf.place_on_bed((height, width), printer) is not None)


def usable_sheet_mm(process: Any, bambu_printer: str | None = None, aspect: float = 1.0) -> dict[str, Any]:
    """Largest sheet the selected printer can take (whole mm, rounded down): the square side, or for a
    rectangular sheet (``aspect`` = height / width) its largest width at those proportions; a sheet fits
    in either orientation.  ``bed_mm`` and ``bambu_printer`` are kept to check other sizes (``sheet_fits``)."""

    bed = [float(process.bed_x_mm), float(process.bed_y_mm)]
    extra = {"bed_mm": bed, "bambu_printer": bambu_printer or None}
    if aspect != 1.0:
        def fits(width: float) -> bool:
            return sheet_fits(width, width * aspect, extra)["fits"]

        low, high = 0.0, max(bed) / min(aspect, 1.0)
        while high - low > 0.25:
            mid = (low + high) / 2
            low, high = (mid, high) if fits(mid) else (low, mid)
        limit = math.floor(low + 1e-9)
        while limit > 0 and not fits(limit):  # whole mm that fit
            limit -= 1
        basis = [f"process bed {process.bed_x_mm:g} x {process.bed_y_mm:g} mm ({process.name})"]
        if bambu_printer:
            printer, _ = _bambu_printer(bambu_printer)
            basis.append(f"{printer.preset}: printable area clear of the keep-out zones")
        basis.append(f"either orientation; height / width {aspect:.4g}")
        return {"max_sheet_mm": float(limit), "max_sheet_height_mm": round(limit * aspect, 1), "basis": basis, **extra}
    limit = min(process.bed_x_mm, process.bed_y_mm)
    basis = [f"process bed {process.bed_x_mm:g} x {process.bed_y_mm:g} mm ({process.name})"]
    if bambu_printer:
        from . import bambu3mf

        printer, found = _bambu_printer(bambu_printer)
        low, high = 0.0, min(printer.bed_mm)
        while high - low > 0.5:
            mid = (low + high) / 2
            low, high = (mid, high) if bambu3mf.place_on_bed((mid, mid), printer) else (low, mid)
        limit = min(limit, low)
        basis.append(f"{printer.preset}: largest square clear of the keep-out zones "
                     f"({bambu3mf.KEEP_OUT_CLEARANCE_MM:g} mm clearance) {low:.1f} mm"
                     + ("" if found else " (built-in bed table; Bambu Studio presets not found)"))
    return {"max_sheet_mm": float(math.floor(limit + 1e-9)), "basis": basis, **extra}


def sheet_fits(width: float, height: float, limit: dict[str, Any]) -> dict[str, Any]:
    """Whether the full outer sheet ``width`` x ``height`` fits the printer of ``limit`` (``usable_sheet_mm``)
    as placed or turned 90 degrees.  Nothing is rotated here; ``turned`` says the user must turn it."""

    bed_x, bed_y = limit["bed_mm"]
    as_is = width <= bed_x + 1e-9 and height <= bed_y + 1e-9
    fits = _fits_bed(width, height, bed_x, bed_y)
    if fits and limit.get("bambu_printer"):
        printer, _ = _bambu_printer(limit["bambu_printer"])
        from . import bambu3mf

        as_is = as_is and bambu3mf.place_on_bed((width, height), printer) is not None
        fits = _fits_bambu(width, height, printer)
    return {"fits": fits, "turned": fits and not as_is}


# -- sizes ---------------------------------------------------------------------------------------------
def _state_at(issue: dict[str, Any], factor: float) -> str:
    if not issue.get("size_related"):
        return issue["severity"]
    if factor + 1e-9 >= issue["factor_clear"]:
        return "CLEARED"
    if factor + 1e-9 >= issue["factor_supported"]:
        return "RISK"
    return "UNSUPPORTED"


def remaining_at(issues: list[dict[str, Any]], factor: float) -> dict[str, int]:
    states = [_state_at(i, factor) for i in issues]
    return {"UNSUPPORTED": states.count("UNSUPPORTED"), "RISK": states.count("RISK"), "CLEARED": states.count("CLEARED")}


def size_options(issues: list[dict[str, Any]], current_mm: float, max_mm: float, aspect: float = 1.0) -> dict[str, Any]:
    """Remaining issues at every size where one changes, and the smallest useful size.

    ``recommended``: the smallest size (whole mm) that clears the most UNSUPPORTED findings any
    size up to ``max_mm`` can clear; then, with that, the fewest RISK findings is offered as
    ``clears_most_risks`` when it is a different, still fitting size.  None when nothing helps.
    Sizes are widths; a rectangular sheet (``aspect`` = height / width) keeps its proportions and each
    row also has its ``height_mm``.
    """

    sizes = {current_mm}
    if max_mm > current_mm:
        sizes.add(max_mm)
    for issue in issues:
        for key in ("factor_supported", "factor_clear"):
            value = issue.get(key)
            if value and math.isfinite(value) and value > 1.0:
                size = math.ceil(current_mm * value / SIZE_STEP_MM) * SIZE_STEP_MM
                if size <= max_mm:
                    sizes.add(float(size))
    rows = [{"sheet_mm": s, **({} if aspect == 1.0 else {"height_mm": round(s * aspect, 1)}),
             "factor": round(s / current_mm, 4), **remaining_at(issues, s / current_mm)} for s in sorted(sizes)]
    now = rows[0]
    best_unsupported = min(r["UNSUPPORTED"] for r in rows)
    best = [r for r in rows if r["UNSUPPORTED"] == best_unsupported]
    best_risk = min(r["RISK"] for r in best)
    if best_unsupported < now["UNSUPPORTED"]:
        recommended = best[0]  # the smallest size that clears every UNSUPPORTED finding any fitting size clears
    elif best_risk < now["RISK"]:
        recommended = next(r for r in best if r["RISK"] == best_risk)
    else:
        recommended = None
    most = next(r for r in best if r["RISK"] == best_risk) if recommended else None
    if most is recommended:
        most = None
    unresolvable = [i["id"] for i in issues if i.get("size_related")
                    and current_mm * i["factor_supported"] > max_mm and i["severity"] == "UNSUPPORTED"]
    return {"current_mm": current_mm, "max_mm": max_mm, "table": rows, "now": now,
            "recommended": recommended, "clears_most_risks": most,
            "unsupported_not_fixable_within_printer": unresolvable,
            "not_size_related": [i["id"] for i in issues if not i.get("size_related")]}


_RANK = {"CLEARED": 0, "INFO": 0, "RISK": 1, "UNSUPPORTED": 2}


def predict(issues: list[dict[str, Any]], factor: float) -> dict[str, list[dict[str, Any]]]:
    """Which current issues improve, worsen (a smaller sheet, ``factor`` < 1) or remain at ``factor`` (estimate).

    Only the current findings are predicted: on a smaller sheet, features that are wide enough now can become
    findings too; the re-plan measures them."""

    out: dict[str, list[dict[str, Any]]] = {"cleared": [], "improved": [], "worsened": [], "remaining": []}
    for issue in issues:
        state = _state_at(issue, factor)
        row = {k: issue.get(k) for k in ("id", "source", "kind", "severity", "sheet", "width_mm", "at_mm", "note")}
        row["predicted"] = state
        if issue.get("size_related") and issue.get("width_mm") is not None:
            row["predicted_width_mm"] = round(issue["width_mm"] * factor, 3)
        before, after = _RANK.get(issue["severity"], 0), _RANK.get(state, 0)
        key = ("cleared" if state == "CLEARED" else "improved" if after < before
               else "worsened" if after > before else "remaining")
        out[key].append(row)
    return out


def analysis_id(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


# -- preview ---------------------------------------------------------------------------------------------
def artwork_ratio(old_grid: StackGrid, old: dict[str, Any], new_grid: StackGrid, new: dict[str, Any]) -> float:
    """How much every artwork length changes: the sheet ratio on the same pixel grid; when the artwork is
    fitted again (other proportions), the ratio of its placed width in millimetres."""

    if new_grid.shape == old_grid.shape:
        if new_grid.square and old_grid.square:
            return new_grid.outer_sheet_mm / old_grid.outer_sheet_mm
        return new_grid.mm_per_pixel / old_grid.mm_per_pixel
    return (new["placement"]["placed_px"][0] * new["artwork_mm_per_pixel"]
            / (old["placement"]["placed_px"][0] * old["artwork_mm_per_pixel"]))


def boundary_check(old_grid: StackGrid, old: dict[str, Any], old_inside: np.ndarray, new_grid: StackGrid,
                   new: dict[str, Any], new_inside: np.ndarray, source_size: tuple[int, int],
                   limit: dict[str, Any]) -> dict[str, Any]:
    """With the same proportions the new size is the old artwork scaled uniformly: same pixels, every length
    times the ratio.  With other proportions the source is fitted into the new aperture again: aspect kept,
    complete, never stretched or cropped (``refit``)."""

    ratio = artwork_ratio(old_grid, old, new_grid, new)
    refit = new_grid.shape != old_grid.shape
    checks = []

    def check(name: str, ok: bool, **values: Any) -> None:
        checks.append({"check": name, "pass": bool(ok), **values})

    def size(grid: StackGrid) -> Any:
        return grid.outer_sheet_mm if grid.square else list(grid.outer_mm)

    new_w_mm, new_h_mm = new_grid.outer_mm
    check(f"a different size, at least {MIN_SHEET_MM:g} mm wide and high",
          new_grid.outer_mm != old_grid.outer_mm and min(new_w_mm, new_h_mm) >= MIN_SHEET_MM - 1e-9,
          current_mm=size(old_grid), new_mm=size(new_grid), min_mm=MIN_SHEET_MM)
    fit = sheet_fits(new_w_mm, new_h_mm, limit)
    check("sheet fits the printer's usable area", fit["fits"], new_mm=size(new_grid), max_mm=limit["max_sheet_mm"],
          **({"turn_90_degrees_in_the_slicer": True} if fit["turned"] else {}))
    old_band = old_grid.perimeter_pixels * old_grid.mm_per_pixel
    new_band = new_grid.perimeter_pixels * new_grid.mm_per_pixel
    if not refit:
        check("artwork aperture scaled uniformly",
              abs(new_grid.artwork_mm - old_grid.artwork_mm * ratio) < 1e-6
              and abs(new_grid.artwork_height_mm - old_grid.artwork_height_mm * ratio) < 1e-6,
              previous_mm=round(old_grid.artwork_mm, 3), new_mm=round(new_grid.artwork_mm, 3))
        check("shared perimeter scaled with the sheet", abs(new_band - old_band * ratio) < 1e-6,
              previous_mm=round(old_band, 3), new_mm=round(new_band, 3))
        check("same pixel grid (no resampling of the artwork)", new_grid.shape == old_grid.shape
              and new["artwork_grid_px"] == old["artwork_grid_px"], pixels=new_grid.outer_pixels)
        check("artwork position and size unchanged relative to the sheet",
              new["placement"]["placed_px"] == old["placement"]["placed_px"]
              and new["placement"]["offset_px"] == old["placement"]["offset_px"],
              placed_px=new["placement"]["placed_px"], offset_px=new["placement"]["offset_px"])
    else:
        check("shared perimeter: the same width on every side", new_grid.perimeter_band_mm > 0,
              band_mm=round(new_band, 3))
    new_w, new_h = new["placement"]["placed_px"]
    aspect = source_size[0] / source_size[1]
    check("aspect ratio preserved", abs(new_w / new_h - aspect) <= aspect * (1 / new_w + 1 / new_h) + 1e-9,
          source=round(aspect, 5), new=round(new_w / new_h, 5))
    x0, y0 = new["placement"]["offset_px"]
    side = new["artwork_grid_px"]
    side_w, side_h = (side, side) if isinstance(side, int) else side
    rect = np.zeros_like(new_inside)
    rect[max(y0, 0):y0 + new_h, max(x0, 0):x0 + new_w] = True
    check("complete artwork inside the aperture", x0 >= 0 and y0 >= 0 and x0 + new_w <= side_w and y0 + new_h <= side_h
          and not (new_inside & ~rect).any(), placed_px=[new_w, new_h], aperture_px=side)
    if not refit:
        check("artwork outline unchanged", bool(np.array_equal(old_inside, new_inside)),
              pixels_changed=int((old_inside ^ new_inside).sum()))
    return {"pass": all(c["pass"] for c in checks), "ratio": round(ratio, 5), "refit": refit, "checks": checks}


def dimensions(grid: StackGrid, measurements: dict[str, Any], thickness_mm: float) -> dict[str, Any]:
    return {"sheet_mm": list(grid.outer_mm),
            "artwork_aperture_mm": round(grid.artwork_mm, 2) if grid.square
            else [round(grid.artwork_mm, 2), round(grid.artwork_height_mm, 2)],
            "perimeter_band_mm": round(grid.perimeter_pixels * grid.mm_per_pixel, 2),
            "artwork_mm": measurements["artwork_size_mm"], "mm_per_pixel": round(grid.mm_per_pixel, 4),
            "sheet_thickness_mm": thickness_mm}


def _carry_edits_to_refit(prepared: dict[str, Any], previous: dict[str, Any], edited: dict[str, Any]) -> np.ndarray:
    """The edited artwork on the new aperture when only its padding changes: the artwork keeps its placed
    size in pixels, so the edited pixels move as they are.  Otherwise the artwork would be resampled and
    the edits cannot be carried; that is refused (never re-applied approximately)."""

    old, new = previous["intake"]["placement"], prepared["measurements"]["placement"]
    if edited.get("composition_ops") or old["placed_px"] != new["placed_px"]:
        raise StackError("the artwork edits cannot be carried to these proportions: the artwork would be fitted "
                         "again (resampled). Choose a size with the current proportions, or one where the artwork "
                         "keeps its size (only the padding around it changes).")
    (w, h), (x0, y0), (x1, y1) = old["placed_px"], old["offset_px"], new["offset_px"]
    crop = np.array(edited["target_rgb"], np.uint8)[y0:y0 + h, x0:x0 + w].copy()
    old_frame = _rgb_of(edited.get("frame_color") or previous["frame_color"])
    crop[(crop == old_frame).all(-1)] = _rgb_of(prepared["frame_color"])  # padding inside the box stays padding
    out = prepared["target_rgb"].copy()
    out[y1:y1 + h, x1:x1 + w] = crop
    return out


def _rgb_of(hex_value: str) -> np.ndarray:
    return np.array([int(hex_value[i:i + 2], 16) for i in (1, 3, 5)], np.uint8)


def _side_by_side(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    height = max(left.shape[0], right.shape[0])
    pad = lambda im: np.pad(im, ((0, height - im.shape[0]), (0, 0), (0, 0)), constant_values=255)  # noqa: E731
    return np.concatenate([pad(left), np.full((height, 8, 3), 255, np.uint8), pad(right)], axis=1)


def preview_resize(image_path: Path, previous_dir: Path, output: Path, new_mm: float, analysis: dict[str, Any],
                   intake: IntakeConfig = IntakeConfig(), previous_decisions: dict[str, Any] | None = None,
                   progress: Callable[..., None] | None = None, edited: dict[str, Any] | None = None,
                   new_height_mm: float | None = None) -> dict[str, Any]:
    """Intake of the previous proposal's image on a ``new_mm`` (x ``new_height_mm``) sheet; no planning.

    With the same proportions the pixels are the same.  With other proportions (``refit``) the source is
    fitted into the new aperture again (aspect kept, padded, never stretched or cropped): the artwork is
    resampled, so it is shown next to the current one and needs approval as a whole; detail choices are
    not carried (their positions change) and intake reports the details again.

    ``edited`` ({"target_rgb", "frame_color", "region_recolors", "pending"}): the artwork carries user
    region recolors, so it is kept pixel for pixel instead of re-deriving it from the source at the new
    size; intake still runs for the grid, placement and frame, which the boundary checks compare.  With
    other proportions that is possible only when the artwork keeps its size (``_carry_edits_to_refit``).
    """

    progress = progress or (lambda *a, **k: None)
    started = time.perf_counter()
    previous = json.loads((Path(previous_dir) / "proposal.json").read_text(encoding="utf-8"))
    planning = previous["planning_config"]
    old_grid = StackGrid.for_planner(planning["outer_sheet_mm"], planning["scale"], planning.get("outer_sheet_height_mm"))
    height = None if new_height_mm is None or float(new_height_mm) == float(new_mm) else float(new_height_mm)
    new_grid = StackGrid.for_planner(float(new_mm), planning["scale"], height)
    refit = new_grid.shape != old_grid.shape
    rgb, opaque, source = load_image(image_path)
    if source["sha256"] != previous["source"]["sha256"]:
        raise StackError("the source image differs from the one of the previous proposal")
    first = prepare_target(rgb, opaque, new_grid, intake)
    if refit:
        carried, carry_report = {}, {"not_carried": sorted(previous_decisions or {}),
                                     "reason": "the artwork is fitted again: detail positions change"}
    else:
        carried, carry_report = carry_decisions(previous.get("intake_details", []), previous_decisions, first["details"], 1)
    prepared = prepare_target(rgb, opaque, new_grid, intake, carried) if carried else first
    del first
    old_target = np.asarray(Image.open(Path(previous_dir) / "intake" / "target.png").convert("RGB"))
    intake_mask = prepared["artwork_mask"]
    if edited is not None:
        if refit:
            prepared = use_edited_target(prepared, _carry_edits_to_refit(prepared, previous, edited), intake,
                                         prepared["frame_color"])
        else:
            prepared = use_edited_target(prepared, edited["target_rgb"], intake, edited.get("frame_color"),
                                         edited.get("composition_ops"))
    m = prepared["measurements"]
    frame = previous.get("frame_color")
    old_inside = ((old_target != np.array([int(frame[i:i + 2], 16) for i in (1, 3, 5)], np.uint8)).any(-1)
                  if frame else np.ones(old_target.shape[:2], bool))
    boundaries = boundary_check(old_grid, previous["intake"], old_inside, new_grid, m, intake_mask,
                                tuple(source["size_px"]), analysis["limit"])
    new_target = prepared["target_rgb"]
    if edited is not None:  # visible changes are measured against the edited artwork the user sees now
        old_target = np.array(edited["target_rgb"], np.uint8)
    output = Path(output)
    if refit:  # different pixel grids: no pixel comparison; the whole artwork is shown and approved
        changes, summary = [], {"refit": True, "note": "fitted into the new aperture again; compare the two images"}
        _save(new_target, output / "target.png")
        _save(_side_by_side(old_target, new_target), output / "comparison.png")
    else:
        changes, listed, summary = compare_targets(old_target, new_target, prepared["artwork_mask"], 1,
                                                   m["artwork_mm_per_pixel"])
        for number, change in enumerate(changes, start=1):
            change["number"] = number
            change["image"] = f"changes/{change['id']}.png"
            _save(_crop([old_target, prepared["source_canvas"], new_target], tuple(change["bbox_px"])),
                  output / change["image"])
        highlight = new_target.copy()
        highlight[ndimage.binary_dilation(listed, iterations=2) & ~listed] = (255, 0, 255)
        _save(new_target, output / "target.png")
        _save(np.concatenate([old_target, np.full((old_target.shape[0], 8, 3), 255, np.uint8), highlight], axis=1),
              output / "comparison.png")
    details = prepared["details"] if edited is None else []  # intake is not re-applied to the edited artwork
    for detail in details:
        detail["image"] = f"details/{detail['id']}.png"
        _save(prepared["detail_crops"][detail["id"]], output / detail["image"])
    ratio = artwork_ratio(old_grid, previous["intake"], new_grid, m)
    thickness = planning.get("thickness_mm", 1.6)
    result = {
        "schema": PREVIEW_SCHEMA, "status": READY if boundaries["pass"] else BOUNDARY_FAILED,
        "analysis_id": analysis["analysis_id"], "from_mm": old_grid.outer_sheet_mm, "to_mm": new_grid.outer_sheet_mm,
        **({} if old_grid.square and new_grid.square else {"from_size_mm": list(old_grid.outer_mm),
                                                           "to_size_mm": list(new_grid.outer_mm)}),
        "to_height_mm": None if new_grid.square else new_grid.outer_sheet_height_mm, "refit": refit,
        "frame_color": prepared["frame_color"],
        "ratio": round(ratio, 5), "scale": planning["scale"],
        "dimensions_now": dimensions(old_grid, previous["intake"], thickness),
        "dimensions_new": dimensions(new_grid, m, thickness),
        "boundaries": boundaries,
        "changes": changes, "change_summary": summary,
        "previous_details": [] if refit else recheck_previous_details(previous.get("intake_details", []), old_target,
                                                                      new_target, 1),
        "details": details, "decisions": carried, "decision_carry": carry_report,
        "palette": [c["hex"] for c in m["colors"]], "previous_palette": previous.get("palette", []),
        "prediction": predict(analysis["issues"], ratio),
        "prediction_basis": "estimate: each finding's width times the artwork's size ratio; the retry re-plans "
                            "(cells, the structural width and intake filters are in millimetres) and re-measures",
        "seconds": round(time.perf_counter() - started, 1),
    }
    if edited is not None:
        result["artwork_edits"] = {
            "region_recolors": edited["region_recolors"], "pending": edited["pending"],
            "note": "The edited artwork is kept pixel for pixel (same pixel grid); intake is not re-run on it, so "
                    "details a larger sheet could keep from the source are not re-detected."}
    _write(output / "preview.json", result)
    progress("RESIZE_PREVIEW_DONE", status=result["status"], changes=len(changes))
    return result


# -- trial planning (failures that widths cannot predict) -------------------------------------------
def trial_plan(image_path: Path, previous: dict[str, Any], sheet_mm: float, intake: IntakeConfig,
               decisions: dict[str, Any] | None, edited: dict[str, Any] | None = None,
               height_mm: float | None = None) -> dict[str, Any]:
    """Plan once (first beam score, no CAD) on a ``sheet_mm`` sheet; the planner and validators are unchanged.

    ``edited`` (as in ``preview_resize``) plans the user-edited artwork instead of the source's intake."""

    from dataclasses import replace

    from .planning import PlanningConfig, PlanningError, plan_stack
    from .validation import validate_stack

    started = time.perf_counter()
    settings = {k: v for k, v in previous["planning_config"].items() if k in PlanningConfig.__dataclass_fields__}
    planning = replace(PlanningConfig(**settings), outer_sheet_mm=float(sheet_mm), outer_sheet_height_mm=height_mm,
                       beam_score=previous.get("beam_scores", ["area"])[0])
    rgb, opaque, _ = load_image(image_path)
    prepared = prepare_target(rgb, opaque, StackGrid.for_planner(planning.outer_sheet_mm, planning.scale, height_mm),
                              intake, decisions)
    if edited is not None:
        prepared = use_edited_target(prepared, edited["target_rgb"], intake, edited.get("frame_color"),
                                     edited.get("composition_ops"))
    planning = replace(planning, frame_color=prepared["frame_color"] if prepared["has_frame"] else None)
    row: dict[str, Any] = {"sheet_mm": float(sheet_mm), "beam_score": planning.beam_score}
    try:
        stack, report = plan_stack(prepared["target_rgb"], planning)
    except PlanningError as error:
        row.update(plans=False, reason=str(error))
    except StackError as error:  # e.g. detail decisions that no longer match at this size
        row.update(plans=False, reason=f"intake: {error}")
    else:
        validation = validate_stack(stack)
        row.update(plans=validation["status"] == "PASS", sheet_count=len(stack.sheets),
                   validation=validation["status"], refined=bool(report.get("cell_refinement")))
    row["seconds"] = round(time.perf_counter() - started, 1)
    return row


def search_plannable_size(plan: Callable[[float], dict[str, Any]], current_mm: float, max_mm: float,
                          step_mm: float = 10.0, progress: Callable[..., None] | None = None) -> dict[str, Any]:
    """Smallest size (to ``step_mm``) in (current, max] that plans, by bisection; the largest size first.

    Assumes larger sheets plan at least as easily (thresholds shrink relative to the artwork); the
    trials are reported so the assumption can be checked.
    """

    progress = progress or (lambda *a, **k: None)
    trials: list[dict[str, Any]] = []

    def attempt(size: float) -> bool:
        row = plan(size)
        trials.append(row)
        progress("RESIZE_TRIAL", sheet_mm=size, plans=row["plans"], seconds=row["seconds"])
        return row["plans"]

    if max_mm <= current_mm or not attempt(max_mm):
        return {"trials": trials, "smallest_plannable_mm": None, "step_mm": step_mm}
    low, high = current_mm, max_mm  # low fails (the current proposal), high plans
    while high - low > step_mm:
        mid = math.ceil(((low + high) / 2) / SIZE_STEP_MM) * SIZE_STEP_MM
        if mid >= high:
            break
        low, high = (low, mid) if attempt(mid) else (mid, high)
    return {"trials": sorted(trials, key=lambda r: r["sheet_mm"]), "smallest_plannable_mm": high, "step_mm": step_mm}
