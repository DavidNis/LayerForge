"""Physical resizing: would a larger sheet make the undersized features printable?

Every printability threshold is in millimetres (the 1.2 mm structural width of the planner,
the process's nozzle, line width, minimum opening and minimum material width).  Scaling the
whole artwork by ``f`` scales every feature width by ``f``, so a feature of width ``w`` that
needs ``t`` is cleared from ``f >= t / w``.  This module:

* turns the existing findings into per-issue requirements (``issue_requirements``):
  - FDM estimate features (``printkit.assess_printability``) of a planned stack;
  - for a failed plan, the planner's obstruction map: the local width of each blocked feature
    against the structural width (an estimate; a closure can also fail for non-size reasons);
* finds the usable square on the selected printer (``usable_sheet_mm``): the process bed, and the
  Bambu printable area minus keep-out zones when a Bambu printer is chosen;
* tabulates the remaining issues per size and suggests the smallest size that clears the most
  UNSUPPORTED findings that any fitting size can clear (``size_options``);
* previews a size (``preview_resize``): intake at the new size with the same pixel grid,
  proportional boundary checks, visible-change comparison, details re-checked, prediction.

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

from .intake import IntakeConfig, load_image, prepare_target
from .model import StackError, StackGrid
from .upscale import _crop, _save, _write, carry_decisions, compare_targets, recheck_previous_details

PREVIEW_SCHEMA = "layerforge.resize_preview"
ANALYSIS_SCHEMA = "layerforge.resize_analysis"
READY = "READY_TO_RESIZE"
BOUNDARY_FAILED = "BOUNDARY_CHECK_FAILED"
SIZE_STEP_MM = 1.0
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
def usable_sheet_mm(process: Any, bambu_printer: str | None = None) -> dict[str, Any]:
    """Largest square sheet the selected printer can take (whole mm, rounded down)."""

    limit = min(process.bed_x_mm, process.bed_y_mm)
    basis = [f"process bed {process.bed_x_mm:g} x {process.bed_y_mm:g} mm ({process.name})"]
    if bambu_printer:
        from . import bambu3mf

        if bambu_printer not in bambu3mf.BAMBU_PRINTERS:
            raise StackError(f"unsupported Bambu printer {bambu_printer!r}")
        printer = bambu3mf.BAMBU_PRINTERS[bambu_printer]
        profiles = bambu3mf.find_profiles()
        if profiles is not None:
            printer = bambu3mf.printer_from_profiles(printer, profiles.resolve(printer.preset))
        low, high = 0.0, min(printer.bed_mm)
        while high - low > 0.5:
            mid = (low + high) / 2
            low, high = (mid, high) if bambu3mf.place_on_bed((mid, mid), printer) else (low, mid)
        limit = min(limit, low)
        basis.append(f"{printer.preset}: largest square clear of the keep-out zones "
                     f"({bambu3mf.KEEP_OUT_CLEARANCE_MM:g} mm clearance) {low:.1f} mm"
                     + ("" if profiles else " (built-in bed table; Bambu Studio presets not found)"))
    return {"max_sheet_mm": float(math.floor(limit + 1e-9)), "basis": basis}


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


def size_options(issues: list[dict[str, Any]], current_mm: float, max_mm: float) -> dict[str, Any]:
    """Remaining issues at every size where one changes, and the smallest useful size.

    ``recommended``: the smallest size (whole mm) that clears the most UNSUPPORTED findings any
    size up to ``max_mm`` can clear; then, with that, the fewest RISK findings is offered as
    ``clears_most_risks`` when it is a different, still fitting size.  None when nothing helps.
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
    rows = [{"sheet_mm": s, "factor": round(s / current_mm, 4), **remaining_at(issues, s / current_mm)} for s in sorted(sizes)]
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


def predict(issues: list[dict[str, Any]], factor: float) -> dict[str, list[dict[str, Any]]]:
    """Which issues improve at ``factor`` and which remain (estimate)."""

    out: dict[str, list[dict[str, Any]]] = {"cleared": [], "improved": [], "remaining": []}
    for issue in issues:
        state = _state_at(issue, factor)
        row = {k: issue.get(k) for k in ("id", "source", "kind", "severity", "sheet", "width_mm", "at_mm", "note")}
        row["predicted"] = state
        if issue.get("size_related") and issue.get("width_mm") is not None:
            row["predicted_width_mm"] = round(issue["width_mm"] * factor, 3)
        key = "cleared" if state == "CLEARED" else "improved" if state != issue["severity"] else "remaining"
        out[key].append(row)
    return out


def analysis_id(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


# -- preview ---------------------------------------------------------------------------------------------
def boundary_check(old_grid: StackGrid, old: dict[str, Any], old_inside: np.ndarray, new_grid: StackGrid,
                   new: dict[str, Any], new_inside: np.ndarray, source_size: tuple[int, int],
                   max_sheet_mm: float) -> dict[str, Any]:
    """The new size is the old artwork scaled uniformly: same pixels, every length times the ratio."""

    ratio = new_grid.outer_sheet_mm / old_grid.outer_sheet_mm
    checks = []

    def check(name: str, ok: bool, **values: Any) -> None:
        checks.append({"check": name, "pass": bool(ok), **values})

    check("larger than the current size", ratio > 1.0, current_mm=old_grid.outer_sheet_mm, new_mm=new_grid.outer_sheet_mm)
    check("sheet fits the printer's usable area", new_grid.outer_sheet_mm <= max_sheet_mm + 1e-9,
          new_mm=new_grid.outer_sheet_mm, max_mm=max_sheet_mm)
    check("artwork aperture scaled uniformly", abs(new_grid.artwork_mm - old_grid.artwork_mm * ratio) < 1e-6,
          previous_mm=round(old_grid.artwork_mm, 3), new_mm=round(new_grid.artwork_mm, 3))
    old_band = old_grid.perimeter_pixels * old_grid.mm_per_pixel
    new_band = new_grid.perimeter_pixels * new_grid.mm_per_pixel
    check("shared perimeter scaled with the sheet", abs(new_band - old_band * ratio) < 1e-6,
          previous_mm=round(old_band, 3), new_mm=round(new_band, 3))
    check("same pixel grid (no resampling of the artwork)", new_grid.outer_pixels == old_grid.outer_pixels
          and new["artwork_grid_px"] == old["artwork_grid_px"], pixels=new_grid.outer_pixels)
    check("artwork position and size unchanged relative to the sheet",
          new["placement"]["placed_px"] == old["placement"]["placed_px"]
          and new["placement"]["offset_px"] == old["placement"]["offset_px"],
          placed_px=new["placement"]["placed_px"], offset_px=new["placement"]["offset_px"])
    new_w, new_h = new["placement"]["placed_px"]
    aspect = source_size[0] / source_size[1]
    check("aspect ratio preserved", abs(new_w / new_h - aspect) <= aspect * (1 / new_w + 1 / new_h) + 1e-9,
          source=round(aspect, 5), new=round(new_w / new_h, 5))
    x0, y0 = new["placement"]["offset_px"]
    side = new["artwork_grid_px"]
    rect = np.zeros_like(new_inside)
    rect[max(y0, 0):y0 + new_h, max(x0, 0):x0 + new_w] = True
    check("complete artwork inside the aperture", x0 >= 0 and y0 >= 0 and x0 + new_w <= side and y0 + new_h <= side
          and not (new_inside & ~rect).any(), placed_px=[new_w, new_h], aperture_px=side)
    check("artwork outline unchanged", bool(np.array_equal(old_inside, new_inside)),
          pixels_changed=int((old_inside ^ new_inside).sum()))
    return {"pass": all(c["pass"] for c in checks), "ratio": round(ratio, 5), "checks": checks}


def dimensions(grid: StackGrid, measurements: dict[str, Any], thickness_mm: float) -> dict[str, Any]:
    return {"sheet_mm": [grid.outer_sheet_mm, grid.outer_sheet_mm],
            "artwork_aperture_mm": round(grid.artwork_mm, 2),
            "perimeter_band_mm": round(grid.perimeter_pixels * grid.mm_per_pixel, 2),
            "artwork_mm": measurements["artwork_size_mm"], "mm_per_pixel": round(grid.mm_per_pixel, 4),
            "sheet_thickness_mm": thickness_mm}


def preview_resize(image_path: Path, previous_dir: Path, output: Path, new_mm: float, analysis: dict[str, Any],
                   intake: IntakeConfig = IntakeConfig(), previous_decisions: dict[str, Any] | None = None,
                   progress: Callable[..., None] | None = None) -> dict[str, Any]:
    """Intake of the previous proposal's image on a ``new_mm`` sheet (same pixels); no planning."""

    progress = progress or (lambda *a, **k: None)
    started = time.perf_counter()
    previous = json.loads((Path(previous_dir) / "proposal.json").read_text(encoding="utf-8"))
    planning = previous["planning_config"]
    old_grid = StackGrid.for_planner(planning["outer_sheet_mm"], planning["scale"])
    new_grid = StackGrid.for_planner(float(new_mm), planning["scale"])
    rgb, opaque, source = load_image(image_path)
    if source["sha256"] != previous["source"]["sha256"]:
        raise StackError("the source image differs from the one of the previous proposal")
    first = prepare_target(rgb, opaque, new_grid, intake)
    carried, carry_report = carry_decisions(previous.get("intake_details", []), previous_decisions, first["details"], 1)
    prepared = prepare_target(rgb, opaque, new_grid, intake, carried) if carried else first
    del first
    m = prepared["measurements"]
    old_target = np.asarray(Image.open(Path(previous_dir) / "intake" / "target.png").convert("RGB"))
    frame = previous.get("frame_color")
    old_inside = ((old_target != np.array([int(frame[i:i + 2], 16) for i in (1, 3, 5)], np.uint8)).any(-1)
                  if frame else np.ones(old_target.shape[:2], bool))
    boundaries = boundary_check(old_grid, previous["intake"], old_inside, new_grid, m, prepared["artwork_mask"],
                                tuple(source["size_px"]), analysis["limit"]["max_sheet_mm"])
    new_target = prepared["target_rgb"]
    output = Path(output)
    changes, listed, summary = compare_targets(old_target, new_target, prepared["artwork_mask"], 1, m["artwork_mm_per_pixel"])
    for number, change in enumerate(changes, start=1):
        change["number"] = number
        change["image"] = f"changes/{change['id']}.png"
        _save(_crop([old_target, prepared["source_canvas"], new_target], tuple(change["bbox_px"])), output / change["image"])
    highlight = new_target.copy()
    highlight[ndimage.binary_dilation(listed, iterations=2) & ~listed] = (255, 0, 255)
    _save(new_target, output / "target.png")
    _save(np.concatenate([old_target, np.full((old_target.shape[0], 8, 3), 255, np.uint8), highlight], axis=1),
          output / "comparison.png")
    details = prepared["details"]
    for detail in details:
        detail["image"] = f"details/{detail['id']}.png"
        _save(prepared["detail_crops"][detail["id"]], output / detail["image"])
    ratio = new_grid.outer_sheet_mm / old_grid.outer_sheet_mm
    thickness = planning.get("thickness_mm", 1.6)
    result = {
        "schema": PREVIEW_SCHEMA, "status": READY if boundaries["pass"] else BOUNDARY_FAILED,
        "analysis_id": analysis["analysis_id"], "from_mm": old_grid.outer_sheet_mm, "to_mm": new_grid.outer_sheet_mm,
        "ratio": round(ratio, 5), "scale": planning["scale"],
        "dimensions_now": dimensions(old_grid, previous["intake"], thickness),
        "dimensions_new": dimensions(new_grid, m, thickness),
        "boundaries": boundaries,
        "changes": changes, "change_summary": summary,
        "previous_details": recheck_previous_details(previous.get("intake_details", []), old_target, new_target, 1),
        "details": details, "decisions": carried, "decision_carry": carry_report,
        "palette": [c["hex"] for c in m["colors"]], "previous_palette": previous.get("palette", []),
        "prediction": predict(analysis["issues"], ratio),
        "prediction_basis": "estimate: each finding's width times the size ratio; the retry re-plans (cells, the "
                            "structural width and intake filters are in millimetres) and re-measures",
        "seconds": round(time.perf_counter() - started, 1),
    }
    _write(output / "preview.json", result)
    progress("RESIZE_PREVIEW_DONE", status=result["status"], changes=len(changes))
    return result


# -- trial planning (failures that widths cannot predict) -------------------------------------------
def trial_plan(image_path: Path, previous: dict[str, Any], sheet_mm: float, intake: IntakeConfig,
               decisions: dict[str, Any] | None) -> dict[str, Any]:
    """Plan once (first beam score, no CAD) on a ``sheet_mm`` sheet; the planner and validators are unchanged."""

    from dataclasses import replace

    from .planning import PlanningConfig, PlanningError, plan_stack
    from .validation import validate_stack

    started = time.perf_counter()
    settings = {k: v for k, v in previous["planning_config"].items() if k in PlanningConfig.__dataclass_fields__}
    planning = replace(PlanningConfig(**settings), outer_sheet_mm=float(sheet_mm), beam_score=previous.get("beam_scores", ["area"])[0])
    rgb, opaque, _ = load_image(image_path)
    prepared = prepare_target(rgb, opaque, StackGrid.for_planner(planning.outer_sheet_mm, planning.scale), intake, decisions)
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
