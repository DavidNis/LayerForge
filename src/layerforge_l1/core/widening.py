"""Cutout widening into hidden material (post-planning, optional; D-V3-042).

A narrow cutout in an intermediate sheet often lies next to material that no
pixel of the front view comes from: material of sheet ``k`` at pixels where a
sheet in front of it (index > k) already has material.  Removing that hidden
material cannot change the front view, so the opening can be widened there.

For every sheet (back to front, raster sheets only)::

    opening   = no material, outside the shared perimeter band
    narrow    = opening minus its morphological opening by a min_opening_mm disk
    near      = narrow dilated by (grow + margin) pixels
    hidden    = material, not visible on this sheet, not perimeter, not frame,
                and covered by a sheet in front
    candidate = (opening dilated by grow pixels) & hidden & near

``candidate`` splits into spots (8-connected).  Spots are removed one at a
time, largest first, and each removal must keep the sheet structurally valid:
one 4-neighbour component and every structural core (erosion by the 1.2 mm
kernel of the sheet validator) connected to the perimeter.  A spot is first
checked in a window around it (``local_change_is_safe``, a sufficient
condition); when the window cannot decide, the whole sheet is checked with the
validator's own criterion.  A spot must also leave no new thin material: no
region of material narrower than that 1.2 mm width (what a 1.2 mm disk cannot
reach) may appear or grow beyond the corner-rounding allowance the
printability checker itself ignores (w**2 / 2), and no visible part may lose
its structural core.  Spots that fail are kept as material.

Removed material always joins an existing opening edge to edge, so no new
separate cutout appears.  Nothing visible changes: visible masks, the front
view, sheet order, colors, thicknesses and the sheet count are identical by
construction and are asserted again at the end (with "no new separate cutout"),
together with the full ``validate_stack`` (connectivity, 1.2 mm width,
perimeter, reconstruction) and "no new thin material".  If any of that fails, the input
stack is returned unchanged with ``status`` ``RESTORED_ORIGINAL``.

The planner, its candidate selection, the validators and the printability
checker are not changed or called differently.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, replace
from typing import Any

import cv2
import numpy as np

from ..connectivity_repair import _core_connectivity, _width_parameters
from ..layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from ..subtractive_poc import _outer_and_perimeter
from .model import SheetStack
from .validation import OPENING_DIAGNOSTIC_MM, minimum_width_mm, validate_stack

APPLIED = "APPLIED"
NO_CHANGE = "NO_CHANGE"
SKIPPED_INVALID = "SKIPPED_INVALID_PLAN"
RESTORED = "RESTORED_ORIGINAL"


@dataclass(frozen=True)
class WideningConfig:
    # Growth of each opening into hidden material, per side (0.35 mm = 2 px at 200 mm / scale 2).
    grow_mm: float = 0.35
    # Openings narrower than this are the ones widened (the 0.8 mm cutout threshold).
    min_opening_mm: float = OPENING_DIAGNOSTIC_MM
    # Candidate pixels lie within grow + margin pixels of a narrow part of an opening.
    margin_px: int = 3
    # Do not remove added pixels that would form a new narrow sliver of the opening, away from
    # its existing narrow part (the sharp tips the plain growth leaves against visible material).
    avoid_new_slivers: bool = True


def _disk(diameter_px: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (diameter_px, diameter_px))


def _morph(op: int, mask: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    return cv2.morphologyEx(mask.astype(np.uint8), op, kernel).astype(bool)


def _dilate(mask: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    return cv2.dilate(mask.astype(np.uint8), kernel).astype(bool)


class _Structure:
    """The sheet validator's pass criterion on one retained mask (``validate_sheet_mask``):
    one 4-neighbour component, no orphan structural core.  Perimeter completeness and visible
    inside retained hold by construction here (neither is ever removed)."""

    def __init__(self, stack: SheetStack) -> None:
        poc = stack.grid.poc_config()
        self.kernel_size = int(_width_parameters(poc, DEFAULT_OPTIMIZATION_CONFIG)["morphology_kernel_size_pixels"])
        self.radius = (self.kernel_size - 1) // 2
        self.kernel = _disk(self.kernel_size)
        _, self.audit_perimeter = _outer_and_perimeter(poc)

    def sheet_ok(self, retained: np.ndarray) -> bool:
        count = cv2.connectedComponents(retained.astype(np.uint8), connectivity=4)[0] - 1
        if count != 1:
            return False
        return not _core_connectivity(retained.astype(np.uint8), self.audit_perimeter, self.kernel_size)[4]

    def local_change_is_safe(self, retained: np.ndarray, box: tuple[int, int, int, int], spot: np.ndarray) -> bool:
        """True when removing ``spot`` (a bool mask over ``box`` = rows y0:y1, cols x0:x1) provably
        keeps ``sheet_ok``; False when this window cannot tell (the caller checks the whole sheet).

        Removing pixels changes the structural core only within the kernel radius of the spot.  In a
        window that holds that neighbourhood with a margin, every component of the new core and of the
        new material (4-neighbour) must reach an anchor - the window's border ring, plus the perimeter
        band for the core - anchors connected inside the window before must still be, and no anchor
        pixel may change.  Any path to the perimeter or the sheet border that crossed the window then
        still exists, so the global connectivity of both is unchanged."""

        y0, y1, x0, x1 = box
        n_rows, n_cols = retained.shape
        pad = self.radius + 3
        r0, r1, c0, c1 = max(0, y0 - pad), min(n_rows, y1 + pad), max(0, x0 - pad), min(n_cols, x1 + pad)
        ext = self.radius + 1
        e0, e1, f0, f1 = max(0, r0 - ext), min(n_rows, r1 + ext), max(0, c0 - ext), min(n_cols, c1 + ext)
        old_ext = retained[e0:e1, f0:f1]
        new_ext = old_ext.copy()
        new_ext[y0 - e0:y1 - e0, x0 - f0:x1 - f0] &= ~spot
        inner = (slice(r0 - e0, r1 - e0), slice(c0 - f0, c1 - f0))

        def core(m: np.ndarray) -> np.ndarray:
            # Constant border 1, as cv2.erode's default border on the whole sheet; the extension
            # keeps the fake border out of the window except at the real sheet edge.
            return cv2.erode(m.astype(np.uint8), self.kernel, borderType=cv2.BORDER_CONSTANT,
                             borderValue=1)[inner].astype(bool)

        ring = np.zeros((r1 - r0, c1 - c0), dtype=bool)
        ring[0, :] = ring[-1, :] = ring[:, 0] = ring[:, -1] = True
        checks = ((core(old_ext), core(new_ext), ring | self.audit_perimeter[r0:r1, c0:c1]),
                  (old_ext[inner], new_ext[inner], ring))
        for old, new, anchors in checks:
            if (anchors & old & ~new).any():
                return False  # an anchor pixel changed: the window is too small to decide
            count, new_labels = cv2.connectedComponents(new.astype(np.uint8), connectivity=4)
            reached = np.unique(new_labels[anchors & new])
            if np.count_nonzero(reached) != count - 1:
                return False  # a piece of the new mask reaches no anchor in the window
            _, old_labels = cv2.connectedComponents(old.astype(np.uint8), connectivity=4)
            both = anchors & old  # == anchors & old & new (no anchor changed)
            pairs = np.unique(np.stack([old_labels[both], new_labels[both]]), axis=1)
            if len(np.unique(pairs[0])) != pairs.shape[1]:
                return False  # anchors connected before are split now
        return True


def _regions(thin: np.ndarray) -> tuple[int, np.ndarray]:
    """Regions of thin material: pixels within one pixel of each other belong together."""

    count, labels = cv2.connectedComponents(_dilate(thin, np.ones((3, 3), np.uint8)).astype(np.uint8), connectivity=8)
    return count, np.where(thin, labels, 0)


class _ThinGuard:
    """No new thin material on one sheet.  Thin material is what a disk of the structural width cannot
    reach (``material - opening(material)``, the raster counterpart of the checker's thin parts); no
    region of it may appear or grow by ``limit`` pixels or more compared with the sheet before widening,
    and every visible component (4-neighbour) that had structural core keeps some (the validator's
    "thin decorative" warning may not appear).  ``limit`` is half the checker's own allowance for
    corner rounding (w**2 / 2, w the structural width): the traced CAD outline of a small thin part
    measured up to ~1.5 times its raster area (Chupa, Oscariko, US), and thin pixels within one pixel
    of each other count as one region (tracing joins them)."""

    def __init__(self, structure: _Structure, sheet: Any, band: np.ndarray, mpp: float) -> None:
        self.kernel, self.radius = structure.kernel, structure.radius
        width_mm = minimum_width_mm()
        self.limit = max(1, int(np.ceil(width_mm * width_mm / 4 / (mpp * mpp))))
        self.band = band  # the perimeter band is not counted as material, as in the checker
        self.thin_before = self.thin(sheet.retained)
        self.visible_labels = cv2.connectedComponents(sheet.visible.astype(np.uint8), connectivity=4)[1]
        self.core_count = self._core_counts(self.core(sheet.retained), self.visible_labels)

    def core(self, m: np.ndarray) -> np.ndarray:
        return cv2.erode(m.astype(np.uint8), self.kernel, borderType=cv2.BORDER_CONSTANT, borderValue=1).astype(bool)

    def thin(self, m: np.ndarray, band: np.ndarray | None = None) -> np.ndarray:
        m = m & ~(self.band if band is None else band)
        opened = cv2.dilate(self.core(m).astype(np.uint8), self.kernel).astype(bool)
        return m & ~opened

    def _core_counts(self, core: np.ndarray, labels: np.ndarray) -> np.ndarray:
        return np.bincount(labels[core], minlength=int(self.visible_labels.max()) + 1)

    @staticmethod
    def _windows(shape: tuple[int, int], box: tuple[int, int, int, int], pad: int, ext: int) -> tuple[Any, ...]:
        y0, y1, x0, x1 = box
        n_rows, n_cols = shape
        r0, r1, c0, c1 = max(0, y0 - pad), min(n_rows, y1 + pad), max(0, x0 - pad), min(n_cols, x1 + pad)
        e0, e1, f0, f1 = max(0, r0 - ext), min(n_rows, r1 + ext), max(0, c0 - ext), min(n_cols, c1 + ext)
        return (r0, r1, c0, c1), (e0, e1, f0, f1), (slice(r0 - e0, r1 - e0), slice(c0 - f0, c1 - f0))

    def _lost_core(self, retained: np.ndarray, box: tuple[int, int, int, int], spot: np.ndarray) -> np.ndarray:
        """Per visible component, the core pixels removing ``spot`` takes away (core changes only
        within the kernel radius of the spot)."""

        y0, y1, x0, x1 = box
        (r0, r1, c0, c1), (e0, e1, f0, f1), inner = self._windows(retained.shape, box, self.radius + 1, self.radius + 1)
        old_ext = retained[e0:e1, f0:f1]
        new_ext = old_ext.copy()
        new_ext[y0 - e0:y1 - e0, x0 - f0:x1 - f0] &= ~spot
        labels = self.visible_labels[r0:r1, c0:c1]
        return self._core_counts(self.core(old_ext)[inner], labels) - self._core_counts(self.core(new_ext)[inner], labels)

    def _too_much_new_thin(self, thin: np.ndarray, fresh: np.ndarray, closed: bool) -> bool | None:
        """True / False; None when a region holding new thin pixels reaches the window edge (undecided)."""

        count, labels = _regions(thin)
        if count <= 1:
            return False
        if not closed:
            # two pixels deep: a region may continue past the window through a one-pixel gap
            edge = np.unique(np.concatenate([labels[:2].ravel(), labels[-2:].ravel(), labels[:, :2].ravel(),
                                             labels[:, -2:].ravel()]))
            if np.isin(np.unique(labels[fresh]), edge[edge > 0]).any():
                return None
        return bool(np.bincount(labels[fresh], minlength=count)[1:].max(initial=0) >= self.limit)

    def allows(self, retained: np.ndarray, box: tuple[int, int, int, int], spot: np.ndarray) -> bool:
        lost = self._lost_core(retained, box, spot)
        if ((self.core_count > 0) & (self.core_count - lost <= 0))[1:].any():
            return False
        y0, y1, x0, x1 = box
        # thin pixels change only within the opening's reach (two kernel radii) of the spot
        (r0, r1, c0, c1), (e0, e1, f0, f1), inner = self._windows(retained.shape, box, 2 * self.radius + 3,
                                                                  2 * self.radius + 2)
        new_ext = retained[e0:e1, f0:f1].copy()
        new_ext[y0 - e0:y1 - e0, x0 - f0:x1 - f0] &= ~spot
        thin = self.thin(new_ext, self.band[e0:e1, f0:f1])[inner]
        fresh = thin & ~self.thin_before[r0:r1, c0:c1]
        if not fresh.any():
            return True
        verdict = self._too_much_new_thin(thin, fresh, closed=False)
        if verdict is None:  # decide on the whole sheet
            trial = retained.copy()
            trial[y0:y1, x0:x1] &= ~spot
            whole = self.thin(trial)
            verdict = self._too_much_new_thin(whole, whole & ~self.thin_before, closed=True)
        return not verdict

    def accept(self, retained: np.ndarray, box: tuple[int, int, int, int], spot: np.ndarray) -> None:
        """Record a removal (call before ``retained`` changes)."""

        self.core_count = self.core_count - self._lost_core(retained, box, spot)


def new_thin_material(structure: _Structure, original: Any, widened: Any, band: np.ndarray,
                      mpp: float) -> dict[str, int]:
    """Whole-sheet count for the final gate: regions of new thin material at or above the allowance,
    and visible components that lost all their structural core."""

    guard = _ThinGuard(structure, original, band, mpp)
    thin = guard.thin(widened.retained)
    fresh = thin & ~guard.thin_before
    regions = 0
    if fresh.any():
        count, labels = _regions(thin)
        regions = int((np.bincount(labels[fresh], minlength=count)[1:] >= guard.limit).sum())
    after = guard._core_counts(guard.core(widened.retained), guard.visible_labels)
    lost = int(((guard.core_count > 0) & (after == 0))[1:].sum())
    return {"new_thin_regions": regions, "visible_parts_losing_core": lost}


def candidate_pixels(stack: SheetStack, index: int, shown: np.ndarray, config: WideningConfig,
                     outside: np.ndarray, previous: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Hidden material of sheet ``index`` the narrow openings may grow into, and those narrow parts.

    ``previous``: pixels earlier widenings removed from this sheet.  Openings are measured and grown as
    they were before those widenings (still-open previous pixels count as material), and previous pixels
    are never candidates again (open ones are done; ones that are material again were put back by an
    edit).  So a repeated run finds exactly what is new: nothing when the sheet has not changed, and
    only around what an edit changed otherwise; a widened opening is never grown a second time."""

    mpp = stack.grid.mm_per_pixel
    sheet = stack.sheets[index]
    grow = max(1, int(round(config.grow_mm / mpp)))
    disk = _disk(int(round(config.min_opening_mm / mpp)) | 1)
    previous = np.zeros_like(sheet.retained) if previous is None else previous
    done = previous & ~sheet.retained  # removed by an earlier widening and still open
    opening = ~(sheet.retained | done) & ~outside
    narrow = opening & ~_morph(cv2.MORPH_OPEN, opening, disk)
    if not narrow.any():
        return np.zeros_like(opening), narrow
    near = _dilate(narrow, _disk(2 * (grow + config.margin_px) + 1))
    hidden = sheet.retained & ~previous & ~sheet.visible & ~outside & (shown > index)
    add = _dilate(opening, _disk(2 * grow + 1)) & hidden & near
    if config.avoid_new_slivers and add.any():
        # Added pixels no min_opening disk reaches in the widened opening, and not next to the
        # existing narrow part (which they would widen), only make a new narrow tip: keep them.
        widens = _dilate(narrow, _disk(2 * grow + 1))
        for _ in range(4):
            sliver = add & ~widens & ~_morph(cv2.MORPH_OPEN, opening | done | add, disk)
            if not sliver.any():
                break
            add &= ~sliver
    if add.any():
        # Only material that joins an opening edge to edge: a pixel the growth reaches across a corner
        # or past visible material would become a new, separate (sub-nozzle) hole.
        _, labels = cv2.connectedComponents((opening | done | add).astype(np.uint8), connectivity=4)
        attached = np.unique(labels[opening | done])
        add &= np.isin(labels, attached[attached > 0])
    return add, narrow


def widen_openings(stack: SheetStack, config: WideningConfig = WideningConfig(),
                   previous: dict[str, np.ndarray] | None = None) -> tuple[SheetStack, dict[str, Any]]:
    """Widen narrow cutouts into hidden material; returns (stack, report).

    ``previous`` (sheet id -> mask): material earlier widenings of this stack removed (see
    ``candidate_pixels``); with it a repeated run is idempotent.  The returned stack is a new stack when
    ``status`` is ``APPLIED``; otherwise it is the input."""

    started = time.perf_counter()
    grid = stack.grid
    mpp = grid.mm_per_pixel
    count = len(stack.sheets)
    report: dict[str, Any] = {"config": asdict(config), "grow_px": max(1, int(round(config.grow_mm / mpp))),
                              "mm_per_pixel": mpp, "sheets": [], "rejected_spots": []}

    def done(status: str, result: SheetStack, **extra: Any) -> tuple[SheetStack, dict[str, Any]]:
        report.update(status=status, seconds=round(time.perf_counter() - started, 3), **extra)
        return result, report

    before = validate_stack(stack)
    if before["status"] != "PASS":
        return done(SKIPPED_INVALID, stack, reason="the sheets do not pass validation; nothing was changed",
                    failures=before["failures"])
    structure = _Structure(stack)
    shown = stack.composite_sheet_index()
    outside = grid.perimeter_mask() | stack.frame_mask()  # frame material stays solid on every sheet
    sheets = list(stack.sheets)
    local_checks = fallbacks = 0
    t_search = t_check = 0.0
    for index, sheet in enumerate(stack.sheets):
        row = {"sheet": sheet.id, "layer_from_front": count - index, "removed_px": 0, "spots": 0,
               "rejected": 0}
        report["sheets"].append(row)
        if sheet.geometry is not None:
            row["skipped"] = "exact vector geometry (edited); not widened"
            continue
        t = time.perf_counter()
        add, _ = candidate_pixels(stack, index, shown, config, outside, (previous or {}).get(sheet.id))
        t_search += time.perf_counter() - t
        if not add.any():
            continue
        t = time.perf_counter()
        n, labels, stats, _ = cv2.connectedComponentsWithStats(add.astype(np.uint8), connectivity=8)
        order = sorted(range(1, n), key=lambda c: (-stats[c, cv2.CC_STAT_AREA], c))
        retained = sheet.retained.copy()
        guard = _ThinGuard(structure, sheet, grid.perimeter_mask(), mpp)
        row["spots"] = len(order)
        # Passes until one accepts nothing: a spot refused early can pass once later spots are removed
        # (thinning hidden material until its structural core vanishes removes an orphan core).  The
        # result is then a fixed point, so a repeated run on the same sheets finds nothing new.
        pending = order
        thin_refused: set[int] = set()
        while pending:
            refused = []
            for c in pending:
                x0, y0 = int(stats[c, cv2.CC_STAT_LEFT]), int(stats[c, cv2.CC_STAT_TOP])
                x1, y1 = x0 + int(stats[c, cv2.CC_STAT_WIDTH]), y0 + int(stats[c, cv2.CC_STAT_HEIGHT])
                spot = labels[y0:y1, x0:x1] == c
                local_checks += 1
                ok = structure.local_change_is_safe(retained, (y0, y1, x0, x1), spot)
                if not ok:
                    fallbacks += 1
                    trial = retained.copy()
                    trial[y0:y1, x0:x1] &= ~spot
                    ok = structure.sheet_ok(trial)
                thin_refused.discard(c)
                if ok and not guard.allows(retained, (y0, y1, x0, x1), spot):
                    ok = False
                    thin_refused.add(c)
                if ok:
                    guard.accept(retained, (y0, y1, x0, x1), spot)
                    retained[y0:y1, x0:x1] &= ~spot
                    row["removed_px"] += int(spot.sum())
                else:
                    refused.append(c)
            if len(refused) == len(pending):
                break
            pending = refused
        for c in pending if pending else []:
            x0, y0 = int(stats[c, cv2.CC_STAT_LEFT]), int(stats[c, cv2.CC_STAT_TOP])
            ys, xs = np.nonzero(labels[y0:y0 + int(stats[c, cv2.CC_STAT_HEIGHT]), x0:x0 + int(stats[c, cv2.CC_STAT_WIDTH])] == c)
            row["rejected"] += 1
            report["rejected_spots"].append({
                "sheet": sheet.id, "layer_from_front": count - index, "pixels": int(stats[c, cv2.CC_STAT_AREA]),
                "at_mm": [round((x0 + xs.mean() + 0.5) * mpp, 2), round((grid.outer_pixels - y0 - ys.mean() - 0.5) * mpp, 2)],
                "reason": ("would leave new material narrower than the structural width" if c in thin_refused
                           else "would disconnect material or leave it on a neck narrower than the structural width")})
        t_check += time.perf_counter() - t
        if row["removed_px"]:
            sheets[index] = replace(sheet, retained=retained, geometry=None)
            row["removed_mm2"] = round(row["removed_px"] * mpp * mpp, 3)
    removed = sum(r["removed_px"] for r in report["sheets"])
    report.update(removed_px=removed, removed_mm2=round(removed * mpp * mpp, 3),
                  spots=sum(r["spots"] for r in report["sheets"]),
                  rejected=len(report["rejected_spots"]), local_checks=local_checks,
                  full_sheet_fallbacks=fallbacks, search_seconds=round(t_search, 3), check_seconds=round(t_check, 3))
    if not removed:
        return done(NO_CHANGE, stack)
    widened = SheetStack(stack.name, grid, sheets, target_rgb=stack.target_rgb, frame_color=stack.frame_color,
                         provenance=stack.provenance, review=stack.review)
    t = time.perf_counter()
    gate = gate_checks(stack, widened)
    report["gate_seconds"] = round(time.perf_counter() - t, 3)
    report["gate"] = gate
    if not gate["pass"]:
        return done(RESTORED, stack, reason="the widened sheets failed the final checks; the original sheets are kept")
    return done(APPLIED, widened)


def gate_checks(original: SheetStack, widened: SheetStack) -> dict[str, Any]:
    """Final gate: the full stack validation plus the invariants the widening must keep."""

    validation = validate_stack(widened)
    same_layout = [(s.id, s.color, s.thickness_mm, s.rim) for s in original.sheets] == \
                  [(s.id, s.color, s.thickness_mm, s.rim) for s in widened.sheets]
    visible_same = same_layout and all(np.array_equal(a.visible, b.visible)
                                       for a, b in zip(original.sheets, widened.sheets))
    front_same = bool(np.array_equal(original.composite_sheet_index(), widened.composite_sheet_index()))
    perimeter = original.grid.perimeter_mask()
    only_removed = same_layout and all(not (b.retained & ~a.retained).any() for a, b in zip(original.sheets, widened.sheets))
    perimeter_kept = all(b.retained[perimeter].all() for b in widened.sheets)

    def cutouts(sheet: Any) -> int:  # 4-connected openings inside the perimeter band
        return cv2.connectedComponents((~sheet.retained & ~perimeter).astype(np.uint8), connectivity=4)[0] - 1

    no_new_cutouts = same_layout and all(cutouts(b) <= cutouts(a) for a, b in zip(original.sheets, widened.sheets)
                                         if not np.array_equal(a.retained, b.retained))
    structure = _Structure(original)
    thin = {"new_thin_regions": 0, "visible_parts_losing_core": 0}
    for a, b in zip(original.sheets, widened.sheets) if same_layout else ():
        if not np.array_equal(a.retained, b.retained):
            for key, value in new_thin_material(structure, a, b, perimeter, original.grid.mm_per_pixel).items():
                thin[key] += value
    no_new_thin = same_layout and not thin["new_thin_regions"] and not thin["visible_parts_losing_core"]
    recon = validation["reconstruction"]
    result = {
        "validation_status": validation["status"],
        "validation_failures": validation["failures"],
        "reconstruction_mismatched_pixels": recon.get("mismatched_pixels"),
        "visible_mask_disagreement_pixels": recon["visible_mask_disagreement_pixels"],
        "sheet_count_order_colors_unchanged": same_layout,
        "visible_masks_unchanged": visible_same,
        "front_view_unchanged": front_same,
        "material_only_removed": only_removed,
        "perimeter_complete": perimeter_kept,
        "no_new_separate_cutouts": no_new_cutouts,
        "no_new_thin_material": no_new_thin,
        **thin,
    }
    result["pass"] = (validation["status"] == "PASS" and not recon.get("mismatched_pixels")
                      and same_layout and visible_same and front_same and only_removed and perimeter_kept
                      and no_new_cutouts and no_new_thin)
    return result


def removed_masks(original: SheetStack, widened: SheetStack) -> dict[str, np.ndarray]:
    """Per sheet id, the material the widening removed (only sheets that changed)."""

    return {a.id: a.retained & ~b.retained for a, b in zip(original.sheets, widened.sheets)
            if not np.array_equal(a.retained, b.retained)}


__all__ = ["APPLIED", "NO_CHANGE", "RESTORED", "SKIPPED_INVALID", "WideningConfig", "candidate_pixels",
           "gate_checks", "removed_masks", "widen_openings"]
