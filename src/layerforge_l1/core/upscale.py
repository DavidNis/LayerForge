"""Upscale & Retry: re-run intake and planning of a failed proposal at a higher processing resolution.

"Upscaling" here is the planner's own ``scale``: the same physical sheet (``outer_sheet_mm``),
the same artwork aperture and perimeter, sampled with 2x or 4x more pixels per millimetre.
The source image is resampled into the finer grid by the existing intake (``fit_into_grid``:
area averaging when shrinking, bicubic interpolation when enlarging; no AI, no invented
detail).  Every physical rule is in millimetres and unchanged, so a finer grid can only
represent the artwork more faithfully; it does not by itself remove physical or topological
obstacles (sub-width openings, unsupportable islands).

``preview_upscale`` prepares the target at the new resolution without planning and writes:

* boundary checks: sheet size, aperture, perimeter, artwork position/size/aspect, and that the
  artwork stays inside the aperture (any failure blocks the retry);
* a comparison with the previous target: coherent regions whose color changed are listed with
  crops (previous | resampled source | new) and need the user's approval before retrying;
* the previous intake details (e.g. preserved eye whites) re-checked in the new target, and the
  user's detail decisions carried to the details found at the new resolution;
* a memory check from a measured per-pixel model; a retry that does not fit is refused.

Physical printability is not judged here; it stays in the proposal flags and the L3 estimate.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

from .intake import DETAIL_MIN_AREA_MM2, IntakeConfig, load_image, prepare_target
from .model import StackError, StackGrid, hex_color
from .planning import PlanningConfig

UPSCALE_FACTORS = (2, 4)
MAX_PROCESSING_SCALE = 16
PREVIEW_SCHEMA = "layerforge.upscale_preview"
READY = "READY_TO_RETRY"
BOUNDARY_FAILED = "BOUNDARY_CHECK_FAILED"
INSUFFICIENT_MEMORY = "INSUFFICIENT_MEMORY"
NO_BENEFIT = "NO_DEMONSTRATED_BENEFIT"
# Peak memory of a full proposal (intake, four candidates, CAD export and reopening), linear in
# the outer-grid pixels: measured 1.03 GB at scale 2 (1152 px) on the couple illustration, peak
# during CAD re-validation; larger scales are extrapolated (D-V3-036).
PEAK_BYTES_PER_PIXEL = 755.0
PEAK_BASE_BYTES = 100 * 2**20
# Never start a run expected to use more than this share of the currently available memory.
MEMORY_HEADROOM = 0.8
# Proposal time per outer-grid pixel, same measurement (706 s at scale 2); varies with the artwork.
SECONDS_PER_PIXEL = 706.0 / 1152**2


# -- resources ---------------------------------------------------------------------------------
def available_memory_bytes() -> int | None:
    """Physical memory currently available to new allocations (None if unknown)."""

    if sys.platform == "win32":
        class Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return int(status.ullAvailPhys)
        return None
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    try:
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (ValueError, OSError, AttributeError):
        return None


def estimate_peak_bytes(grid: StackGrid) -> int:
    return int(PEAK_BASE_BYTES + PEAK_BYTES_PER_PIXEL * grid.outer_pixels**2)


def resource_check(outer_mm: float, scale: int, available: int | None = None) -> dict[str, Any]:
    """Whether a proposal at ``scale`` is expected to fit in the available memory."""

    grid = StackGrid.for_planner(outer_mm, scale)
    peak = estimate_peak_bytes(grid)
    available = available_memory_bytes() if available is None else available
    gib = 2.0**30
    out: dict[str, Any] = {
        "scale": scale, "outer_pixels": grid.outer_pixels, "mm_per_pixel": round(grid.mm_per_pixel, 5),
        "estimated_peak_gb": round(peak / gib, 2),
        "available_gb": round(available / gib, 2) if available is not None else None,
        "fits": available is None or peak <= MEMORY_HEADROOM * available,
        "basis": "measured per-pixel memory model (estimate); a fresh check runs again right before the retry",
    }
    if scale > MAX_PROCESSING_SCALE:
        out.update(fits=False, reason=f"processing scale above {MAX_PROCESSING_SCALE} is not supported")
    elif not out["fits"]:
        out["reason"] = (f"estimated peak {out['estimated_peak_gb']} GB exceeds {int(MEMORY_HEADROOM * 100)} % of the "
                         f"{out['available_gb']} GB available now; close other programs or choose a smaller factor")
    # A failed proposal stops early, so its own time says little about a full run.
    out["estimated_minutes"] = round(SECONDS_PER_PIXEL * grid.outer_pixels**2 / 60, 1)
    out["time_basis"] = "measured on one 5-color illustration; varies with the artwork"
    return out


# -- boundaries ----------------------------------------------------------------------------------
def _placement_mm(measurements: dict[str, Any]) -> dict[str, list[float]]:
    mm = measurements["artwork_mm_per_pixel"]
    p = measurements["placement"]
    return {"offset_mm": [p["offset_px"][0] * mm, p["offset_px"][1] * mm],
            "size_mm": [p["placed_px"][0] * mm, p["placed_px"][1] * mm]}


def boundary_check(old_grid: StackGrid, old: dict[str, Any], old_inside: np.ndarray,
                   new_grid: StackGrid, new: dict[str, Any], new_inside: np.ndarray,
                   source_size: tuple[int, int]) -> dict[str, Any]:
    """Physical dimensions and artwork placement of the new target versus the previous one.

    Tolerances are one pixel of the previous grid: the previous placement was itself rounded
    to whole pixels, so nothing finer can be compared.
    """

    factor = new_grid.scale // old_grid.scale
    old_px = old["artwork_mm_per_pixel"]
    checks = []

    def check(name: str, ok: bool, **values: Any) -> None:
        checks.append({"check": name, "pass": bool(ok), **values})

    check("sheet size unchanged", abs(new_grid.outer_sheet_mm - old_grid.outer_sheet_mm) < 1e-9,
          previous_mm=old_grid.outer_sheet_mm, new_mm=new_grid.outer_sheet_mm)
    check("artwork aperture unchanged", abs(new_grid.artwork_mm - old_grid.artwork_mm) < 1e-6,
          previous_mm=round(old_grid.artwork_mm, 4), new_mm=round(new_grid.artwork_mm, 4))
    old_perimeter = old_grid.perimeter_pixels * old_grid.mm_per_pixel
    new_perimeter = new_grid.perimeter_pixels * new_grid.mm_per_pixel
    check("shared outer perimeter unchanged", abs(new_perimeter - old_perimeter) < 1e-6,
          previous_mm=round(old_perimeter, 4), new_mm=round(new_perimeter, 4))
    check("pixel resolution only", new_grid.outer_pixels == factor * old_grid.outer_pixels
          and new_grid.scale == factor * old_grid.scale,
          previous_px=old_grid.outer_pixels, new_px=new_grid.outer_pixels, factor=factor)

    a, b = _placement_mm(old), _placement_mm(new)
    shift = max(abs(x - y) for x, y in zip(a["offset_mm"], b["offset_mm"]))
    size = max(abs(x - y) for x, y in zip(a["size_mm"], b["size_mm"]))
    check("artwork position unchanged", shift <= old_px + 1e-9, max_shift_mm=round(shift, 4), tolerance_mm=round(old_px, 4))
    check("artwork size unchanged (no stretch or crop)", size <= old_px + 1e-9, max_size_change_mm=round(size, 4),
          previous_mm=[round(v, 3) for v in a["size_mm"]], new_mm=[round(v, 3) for v in b["size_mm"]])
    source_aspect = source_size[0] / source_size[1]
    new_w, new_h = new["placement"]["placed_px"]
    aspect = new_w / new_h
    check("aspect ratio preserved", abs(aspect - source_aspect) <= source_aspect * (1 / new_w + 1 / new_h) + 1e-9,
          source=round(source_aspect, 5), new=round(aspect, 5))
    side = new["artwork_grid_px"]
    x0, y0 = new["placement"]["offset_px"]
    rect = np.zeros_like(new_inside)
    rect[max(y0, 0):y0 + new_h, max(x0, 0):x0 + new_w] = True
    inside_rect = x0 >= 0 and y0 >= 0 and x0 + new_w <= side and y0 + new_h <= side and not (new_inside & ~rect).any()
    check("complete artwork inside the aperture", inside_rect, placed_px=[new_w, new_h], aperture_px=side)
    # The opaque area may move only along its outline (one previous pixel), never elsewhere.
    old_up = np.kron(old_inside, np.ones((factor, factor), bool))
    xor = old_up ^ new_inside
    edge = old_up ^ ndimage.binary_erosion(old_up, iterations=1, border_value=1)
    band = ndimage.binary_dilation(edge, iterations=factor + 1)
    outside_band = int((xor & ~band).sum())
    check("artwork outline unchanged (within one previous pixel)", outside_band == 0,
          pixels_moved_beyond_tolerance=outside_band)
    return {"pass": all(c["pass"] for c in checks), "checks": checks}


# -- visible changes -------------------------------------------------------------------------------
def _hex_of(rgb: np.ndarray, mask: np.ndarray) -> str:
    colors, counts = np.unique(rgb[mask].reshape(-1, 3), axis=0, return_counts=True)
    return hex_color(tuple(int(v) for v in colors[counts.argmax()]))


def _crop(images: list[np.ndarray], box: tuple[int, int, int, int], height: int = 140) -> np.ndarray:
    r0, c0, r1, c1 = box
    side = images[0].shape[0]
    pad = max(8, (r1 - r0 + c1 - c0) // 4)
    ys = slice(max(r0 - pad, 0), min(r1 + pad, side))
    xs = slice(max(c0 - pad, 0), min(c1 + pad, side))
    zoom = max(1, height // max(ys.stop - ys.start, 1))
    tiles = []
    for image in images:
        tile = np.repeat(np.repeat(image[ys, xs], zoom, axis=0), zoom, axis=1)
        tiles.append(np.pad(tile, ((4, 4), (4, 4), (0, 0)), constant_values=255))
    return np.concatenate(tiles, axis=1)


def compare_targets(previous: np.ndarray, new: np.ndarray, inside: np.ndarray, factor: int,
                    mm_per_pixel: float) -> tuple[list[dict[str, Any]], np.ndarray, dict[str, Any]]:
    """Coherent regions whose color differs between the previous target and the new one.

    Outline jitter of up to one previous pixel is the expected effect of a finer grid and is
    measured but not listed; a region is listed when a core wider than that survives and it
    covers at least the intake detail area.  Returns (changes, mask of listed changes, summary).
    """

    previous_up = np.repeat(np.repeat(previous, factor, axis=0), factor, axis=1)
    diff = (previous_up != new).any(-1) & inside
    core = ndimage.binary_opening(diff, structure=np.ones((factor + 1, factor + 1), bool))
    pieces, count = ndimage.label(diff, structure=np.ones((3, 3), bool))
    min_px = max(3, round(DETAIL_MIN_AREA_MM2 / mm_per_pixel**2))
    listed = np.zeros(diff.shape, bool)
    changes = []
    for index, box in enumerate(ndimage.find_objects(pieces), start=1):
        local = pieces[box] == index
        if not (core[box] & local).any() or local.sum() < min_px:
            continue
        mask = np.zeros(diff.shape, bool)
        mask[box] = local
        listed |= mask
        rows, cols = box
        changes.append({
            "id": f"change_{rows.start:05d}_{cols.start:05d}",
            "bbox_px": [rows.start, cols.start, rows.stop, cols.stop],
            "center_mm": [round((cols.start + cols.stop) / 2 * mm_per_pixel, 2), round((rows.start + rows.stop) / 2 * mm_per_pixel, 2)],
            "area_mm2": round(float(local.sum()) * mm_per_pixel**2, 3),
            "previous_hex": _hex_of(previous_up, mask), "new_hex": _hex_of(new, mask),
        })
    changes.sort(key=lambda c: -c["area_mm2"])
    summary = {"changed_pixels": int(diff.sum()), "changed_fraction": round(float(diff.sum()) / max(int(inside.sum()), 1), 5),
               "outline_jitter_pixels": int((diff & ~listed).sum()), "listed_changes": len(changes),
               "listed_area_mm2": round(float(listed.sum()) * mm_per_pixel**2, 3)}
    return changes, listed, summary


def recheck_previous_details(details: list[dict[str, Any]], previous: np.ndarray, new: np.ndarray,
                             factor: int) -> list[dict[str, Any]]:
    """Each detail of the previous proposal: is its planned color still where it was?"""

    rows = []
    for detail in details:
        r0, c0, r1, c1 = detail["bbox_px"]
        want = np.array([int(detail["result_hex"][i:i + 2], 16) for i in (1, 3, 5)], np.uint8)
        before = (previous[r0:r1, c0:c1] == want).all(-1)
        after = (new[r0 * factor:r1 * factor, c0 * factor:c1 * factor] == want).all(-1)
        old_px = float(before.sum()) * factor * factor
        kept = float(after.sum()) / old_px if old_px else 1.0
        rows.append({"id": detail["id"], "kind": detail["kind"], "planned_hex": detail["result_hex"],
                     "action": detail.get("action", {}).get("action"), "kept_fraction": round(kept, 3),
                     "kept": kept >= 0.5})
    return rows


# -- detail decisions across resolutions -------------------------------------------------------------
def carry_decisions(previous: list[dict[str, Any]], decisions: dict[str, Any] | None,
                    new: list[dict[str, Any]], factor: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Map the user's decisions on the previous details to the details found at the new resolution.

    A new detail inherits a decision when it is of the same kind and its box centre lies inside
    the previous detail's box (scaled, one previous pixel of slack).  Conflicting or missing
    matches are reported, never guessed: such details keep the default action.
    """

    decisions = decisions or {}
    by_id = {d["id"]: d for d in previous}
    claims: dict[str, list[str]] = {}
    for old_id, choice in decisions.items():
        old = by_id.get(old_id)
        if old is None:
            continue
        r0, c0, r1, c1 = (v * factor for v in old["bbox_px"])
        for detail in new:
            if detail["kind"] != old["kind"]:
                continue
            nr0, nc0, nr1, nc1 = detail["bbox_px"]
            cy, cx = (nr0 + nr1) / 2, (nc0 + nc1) / 2
            if r0 - factor <= cy <= r1 + factor and c0 - factor <= cx <= c1 + factor:
                claims.setdefault(detail["id"], []).append(old_id)
    carried: dict[str, Any] = {}
    conflicts = []
    for new_id, olds in claims.items():
        choices = {json.dumps(decisions[o], sort_keys=True) for o in olds}
        if len(choices) == 1:
            carried[new_id] = dict(decisions[olds[0]])
        else:
            conflicts.append({"detail": new_id, "previous": olds})
    used = {o for new_id, olds in claims.items() if new_id in carried for o in olds}
    report = {
        "carried": [{"previous": olds, "detail": new_id, "decision": carried[new_id]}
                    for new_id, olds in sorted(claims.items()) if new_id in carried],
        "conflicts": conflicts,
        "not_found_at_new_resolution": sorted(set(decisions) - used - {o for c in conflicts for o in c["previous"]}),
        "new_details_with_default_action": sorted(d["id"] for d in new if d["id"] not in carried),
    }
    return carried, report


# -- preview ---------------------------------------------------------------------------------------
def _save(array: np.ndarray, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array).save(path)
    return path.as_posix()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=lambda v: v.tolist() if hasattr(v, "tolist") else str(v)),
                    encoding="utf-8")


def preview_id(source_sha256: str, outer_mm: float, from_scale: int, factor: int, max_colors: int,
               decisions: dict[str, Any] | None) -> str:
    key = json.dumps([source_sha256, outer_mm, from_scale, factor, max_colors, decisions or {}], sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def preview_upscale(image_path: Path, previous_dir: Path, output: Path, factor: int,
                    intake: IntakeConfig = IntakeConfig(), previous_decisions: dict[str, Any] | None = None,
                    progress: Callable[..., None] | None = None) -> dict[str, Any]:
    """Prepare the target of a failed proposal (``previous_dir``) at ``factor`` x its resolution.

    Writes ``output/preview.json`` and its images; plans nothing and changes nothing else.
    """

    progress = progress or (lambda *a, **k: None)
    started = time.perf_counter()
    if factor not in UPSCALE_FACTORS:
        raise StackError(f"upscale factor must be one of {UPSCALE_FACTORS}, got {factor!r}")
    previous = json.loads((Path(previous_dir) / "proposal.json").read_text(encoding="utf-8"))
    planning = PlanningConfig(**{k: v for k, v in previous["planning_config"].items()
                                 if k in PlanningConfig.__dataclass_fields__})
    old_grid = StackGrid.for_planner(planning.outer_sheet_mm, planning.scale)
    new_scale = planning.scale * factor
    resources = resource_check(planning.outer_sheet_mm, new_scale)
    output = Path(output)
    result: dict[str, Any] = {
        "schema": PREVIEW_SCHEMA, "factor": factor, "from_scale": planning.scale, "to_scale": new_scale,
        "outer_sheet_mm": planning.outer_sheet_mm, "previous_status": previous["status"], "resources": resources,
        "method": "existing intake resampling into the finer grid (area averaging when shrinking, bicubic when "
                  "enlarging); no AI upscaling, no invented detail",
        "physical_rules": "unchanged: every width/area threshold is in millimetres; only pixels per millimetre change",
        "limitation": "A finer processing grid represents the artwork more faithfully; it does not necessarily "
                      "resolve physical or topological constraints (sub-width openings, unsupportable islands).",
    }
    if new_scale > MAX_PROCESSING_SCALE:
        result.update(status=INSUFFICIENT_MEMORY, seconds=round(time.perf_counter() - started, 1))
        _write(output / "preview.json", result)
        return result

    rgb, opaque, source = load_image(image_path)
    if source["sha256"] != previous["source"]["sha256"]:
        raise StackError("the source image differs from the one of the previous proposal")
    new_grid = StackGrid.for_planner(planning.outer_sheet_mm, new_scale)
    first = prepare_target(rgb, opaque, new_grid, intake)  # default actions: find the new details
    carried, carry_report = carry_decisions(previous.get("intake_details", []), previous_decisions,
                                            first["details"], factor)
    prepared = prepare_target(rgb, opaque, new_grid, intake, carried) if carried else first
    del first
    progress("UPSCALE_INTAKE_DONE", scale=new_scale, details=len(prepared["details"]))

    old_target = np.asarray(Image.open(Path(previous_dir) / "intake" / "target.png").convert("RGB"))
    frame = previous.get("frame_color")
    if frame:
        old_inside = (old_target != np.array([int(frame[i:i + 2], 16) for i in (1, 3, 5)], np.uint8)).any(-1)
    else:
        old_inside = np.ones(old_target.shape[:2], bool)
    m = prepared["measurements"]
    boundaries = boundary_check(old_grid, previous["intake"], old_inside, new_grid, m, prepared["artwork_mask"],
                                tuple(source["size_px"]))
    new_target = prepared["target_rgb"]
    changes, listed, summary = compare_targets(old_target, new_target, prepared["artwork_mask"], factor,
                                               m["artwork_mm_per_pixel"])
    source_up = prepared["source_canvas"]
    old_up = np.repeat(np.repeat(old_target, factor, axis=0), factor, axis=1)
    for number, change in enumerate(changes, start=1):
        change["number"] = number
        change["image"] = f"changes/{change['id']}.png"
        _save(_crop([old_up, source_up, new_target], tuple(change["bbox_px"])), output / change["image"])
    highlight = new_target.copy()
    outline = ndimage.binary_dilation(listed, iterations=max(2, factor)) & ~listed
    highlight[outline] = (255, 0, 255)
    for change in changes:
        r0, c0, _, _ = change["bbox_px"]
        cv2.putText(highlight, str(change["number"]), (c0, max(14 * factor, r0 - 4 * factor)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.4 * factor, (255, 0, 255), max(1, factor // 2), cv2.LINE_AA)
    _save(new_target, output / "target.png")
    _save(highlight, output / "changes_highlighted.png")
    shrink = max(1, new_target.shape[0] // 1024)
    side_by_side = np.concatenate([old_up[::shrink, ::shrink], np.full((old_up[::shrink].shape[0], 8, 3), 255, np.uint8),
                                   highlight[::shrink, ::shrink]], axis=1)
    _save(side_by_side, output / "comparison.png")
    details = prepared["details"]
    if details:
        _save(prepared["detail_overview"], output / "details_overview.png")
        for detail in details:
            detail["image"] = f"details/{detail['id']}.png"
            _save(prepared["detail_crops"][detail["id"]], output / detail["image"])

    from .propose import intake_flags

    placed = m["placement"]["placed_px"]
    source_px_per_processing_px = source["size_px"][0] / placed[0]
    result.update(
        status=BOUNDARY_FAILED if not boundaries["pass"] else INSUFFICIENT_MEMORY if not resources["fits"]
        else READY if changes else NO_BENEFIT,
        source={**source, "source_px_per_processing_px": round(source_px_per_processing_px, 3),
                "resampling": "downsampled (area average)" if source_px_per_processing_px > 1
                else "interpolated (bicubic): finer grid, no new source detail"},
        previous_scale_mm_per_pixel=previous["intake"]["artwork_mm_per_pixel"],
        artwork_mm_per_pixel=m["artwork_mm_per_pixel"],
        boundaries=boundaries,
        changes=changes, change_summary=summary,
        previous_details=recheck_previous_details(previous.get("intake_details", []), old_target, new_target, factor),
        details=details, decisions=carried, decision_carry=carry_report,
        palette=[c["hex"] for c in m["colors"]], previous_palette=previous.get("palette", []),
        physical_notes=[f for f in intake_flags(m, intake) if f["code"] in ("SUB_WIDTH_FEATURES", "SMALL_COLOR")],
        intake_config=asdict(intake),
        seconds=round(time.perf_counter() - started, 1),
    )
    _write(output / "preview.json", result)
    progress("UPSCALE_PREVIEW_DONE", status=result["status"], changes=len(changes))
    return result

