"""Issue inspector, second part: what an issue shows in the processed image, where the same problem
repeats across layers, and the user's review decisions.

Nothing here changes geometry, severities or which issues exist.

* Attribution (``FeatureMap.attribute``): width-type sheet findings (narrow openings, narrow
  material) are traced back to the processed flat-color image the sheets were planned from
  (``stack.target_rgb``).  An opening in layer k shows the layers behind it; the image pixels those
  layers show inside the issue are what the opening reveals.  Material shows the layer's own visible
  pixels.  Each revealed image region (8-connected pixels of one color) is measured where the issue
  is; a region narrower there than the issue's own threshold is a small detail of the image.  When
  every region is wide enough, or nothing is visible there at all, the narrow geometry comes from how
  the planner split the image into layers.
* Grouping (``group_by_location``): sheet issues whose footprints touch (within a few pixels) are
  the same physical place; each member keeps its own layer and measurement.
* Review (``apply_decisions``): Needs review / Fix later / Accept risk, saved per stable issue key
  with fingerprints of the processed image and layer geometry around the issue, the print settings
  and the measurement.  A decision applies only while all of them still match.

Coordinates are sheet mm (x right, y up, the outer sheet including the perimeter band), as in
``core/issues.py``; widths are measured on the planning grid, whose pixel pitch is the artwork
aperture's (aperture mm / artwork pixels), never the outer sheet scaled.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import cv2
import numpy as np

from .issues import mask_outline
from .model import SheetStack, StackGrid

ROLES = {"opening_may_close": "opening", "narrow_slot_or_gap": "opening", "narrow_neck_in_opening": "opening",
         "thin_material": "material", "narrow_connection": "material", "thin_visible_detail": "material"}
LOCAL_WINDOW_MM = 3.0  # revealed regions are outlined within this distance of the issue (a large region stays local)


# -- rasters ---------------------------------------------------------------------------------------
Window = tuple[slice, slice]


def bbox_window(bbox_mm: list[float], grid: StackGrid, pad_px: int) -> Window:
    """Outer-grid rows/cols of a sheet-mm box plus ``pad_px`` pixels, clipped to the grid."""

    n, mm = grid.outer_pixels, grid.mm_per_pixel
    x0, y0, x1, y1 = bbox_mm
    return (slice(max(0, int(np.floor(n - y1 / mm)) - pad_px), min(n, int(np.ceil(n - y0 / mm)) + pad_px)),
            slice(max(0, int(np.floor(x0 / mm)) - pad_px), min(n, int(np.ceil(x1 / mm)) + pad_px)))


def outline_mask(outline: list | None, grid: StackGrid, window: Window | None = None) -> np.ndarray:
    """Pixels whose centre lies inside sheet-mm polygons (the inverse of ``mask_outline``), on the
    outer grid or on ``window`` of it.  Even-odd scanlines through the pixel centres (exact; holes
    and several polygons in one pass)."""

    n, mm = grid.outer_pixels, grid.mm_per_pixel
    rs, cs = window or (slice(0, n), slice(0, n))
    h, w = rs.stop - rs.start, cs.stop - cs.start
    mask = np.zeros((h, w), bool)
    edges = []
    for poly in outline or []:
        for ring in poly:
            a = np.asarray(ring, float)[:, :2]
            if len(a) < 3:
                continue
            if not np.array_equal(a[0], a[-1]):
                a = np.vstack([a, a[:1]])
            x, y = a[:, 0] / mm - cs.start, n - a[:, 1] / mm - rs.start  # pixel (r, c) spans [c, c+1] x [r, r+1]
            edges.append(np.stack([x[:-1], y[:-1], x[1:], y[1:]], axis=1))
    if not edges:
        return mask
    e = np.concatenate(edges)
    e = e[e[:, 1] != e[:, 3]]  # horizontal edges never cross a row of centres
    if not len(e):
        return mask
    low, high = np.minimum(e[:, 1], e[:, 3]), np.maximum(e[:, 1], e[:, 3])
    for r in range(max(0, int(np.floor(low.min()))), min(h, int(np.ceil(high.max())))):
        yc = r + 0.5
        hit = e[(low <= yc) & (yc < high)]
        if not len(hit):
            continue
        xs = np.sort(hit[:, 0] + (yc - hit[:, 1]) * (hit[:, 2] - hit[:, 0]) / (hit[:, 3] - hit[:, 1]))
        for a, b in zip(xs[0::2], xs[1::2]):  # centres c + 0.5 in [a, b)
            mask[r, max(0, int(np.ceil(a - 0.5))):min(w, int(np.ceil(b - 0.5)))] = True
    return mask


def footprint(issue: dict[str, Any], grid: StackGrid, window: Window | None = None) -> np.ndarray:
    """The issue's pixels; a feature thinner than a pixel is the pixel at its centre point."""

    mask = outline_mask(issue.get("outline_mm"), grid, window)
    if not mask.any() and issue.get("at_mm"):
        n, mm = grid.outer_pixels, grid.mm_per_pixel
        rs, cs = window or (slice(0, n), slice(0, n))
        r, c = int(n - issue["at_mm"][1] / mm) - rs.start, int(issue["at_mm"][0] / mm) - cs.start
        if 0 <= r < mask.shape[0] and 0 <= c < mask.shape[1]:
            mask[r, c] = True
    return mask


def _disk(radius: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1,) * 2)


def local_thickness_px(piece: np.ndarray, cap: int) -> np.ndarray:
    """Per pixel of ``piece``: the diameter (odd px, 2t - 1) of the largest disk inside ``piece`` that
    covers it, up to ``2 * cap - 1``.  A 1 px line is 1, a 5 px bar 5; an even width reads one pixel
    less (4 px reads 3).  Outside the array counts as ``piece`` continuing."""

    dist = cv2.distanceTransform(piece.astype(np.uint8), cv2.DIST_L2, 5)
    out = piece.astype(np.int16)
    for t in range(2, cap + 1):
        core = (dist >= t).astype(np.uint8)
        if not core.any():
            break
        out[cv2.dilate(core, _disk(t - 1)).astype(bool) & piece] = 2 * t - 1
    return out


def _embed(mask: np.ndarray, window: Window, grid: StackGrid) -> np.ndarray:
    full = np.zeros((grid.outer_pixels,) * 2, bool)
    full[window] = mask
    return full


# -- attribution -----------------------------------------------------------------------------------
class FeatureMap:
    """The processed image of a sheet stack as one-color regions, with its front view."""

    def __init__(self, stack: SheetStack) -> None:
        found = stack.target_labels()
        if found is None:
            raise ValueError("the stack has no processed image (target)")
        self.stack, self.grid = stack, stack.grid
        self.labels, self.palette = found
        self.shown = stack.composite_sheet_index()
        self.inside = ~self.grid.perimeter_mask() & ~stack.frame_mask() & (self.labels >= 0)
        self.index = {s.id: k for k, s in enumerate(stack.sheets)}
        self.regions = np.zeros(self.labels.shape, np.int32)  # 8-connected one-color regions, ids from 1
        start = 0
        for color in range(len(self.palette)):
            m = (self.labels == color) & self.inside
            count, lab = cv2.connectedComponents(m.astype(np.uint8), connectivity=8)
            self.regions[m] = lab[m] + start
            start += count - 1
        self.region_pixels = np.bincount(self.regions.ravel())
        # Sheets of a review may also carry the user's layer edits: then the layout is not the planner's alone.
        self.by = "the layer planner or a layer edit" if (stack.review or {}).get("edits") else "the layer planner"

    def _sheets_at(self, shown: np.ndarray) -> list[str]:
        return sorted({self.stack.sheets[int(s)].id for s in np.unique(shown) if s >= 0},
                      key=lambda i: -self.index[i])  # front first

    def attribute(self, issue: dict[str, Any]) -> dict[str, Any] | None:
        """What a width-type sheet issue shows in the processed image, and whether the image or the
        planner made it narrow (None for other kinds).

        The width of each revealed image region is its local thickness (``local_thickness_px``) at
        the issue: the widest disk inside the region that covers any of its revealed pixels there."""

        role = ROLES.get(issue["kind"])
        k = self.index.get(issue.get("sheet"))
        th = issue.get("threshold") or {}
        if role is None or k is None or not issue.get("outline_mm") or not issue.get("bbox_mm") or th.get("value") is None:
            return None
        grid, sheet, mm = self.grid, self.stack.sheets[k], self.grid.mm_per_pixel
        threshold = float(th["value"])
        cap = int(np.ceil(threshold / mm / 2)) + 2           # thicknesses are measured up to 2 * cap - 1 px
        local_px = int(np.ceil(LOCAL_WINDOW_MM / mm))
        win = bbox_window(issue["bbox_mm"], grid, local_px + 2 * cap + 2)
        foot = footprint(issue, grid, win) & self.inside[win]
        shown, regs = self.shown[win], self.regions[win]
        if role == "opening":
            opening = foot & ~sheet.retained[win]
            revealed = opening & (shown >= 0) & (shown < k)    # shown by the layers behind
            hidden = opening & (shown > k)                      # under the layers in front
        else:
            revealed = foot & sheet.visible[win]                # this layer's own visible pixels
            hidden = foot & sheet.retained[win] & ~sheet.visible[win]
        regions = []
        for region in np.unique(regs[revealed]):
            if region <= 0:
                continue
            piece = regs == region
            here = revealed & piece
            thick = int(local_thickness_px(piece, cap)[here].max())
            rows, cols = np.nonzero(here)
            near = np.zeros_like(piece)
            near[max(0, rows.min() - local_px):rows.max() + 1 + local_px,
                 max(0, cols.min() - local_px):cols.max() + 1 + local_px] = True
            local = piece & near
            regions.append({"color": self.palette[int(self.labels[win][here][0])], "width_mm": round(thick * mm, 3),
                            "width_at_least": thick >= 2 * cap - 1, "narrow": thick * mm < threshold,
                            "area_mm2": round(float(self.region_pixels[region]) * mm * mm, 3),
                            "revealed_pixels": int(here.sum()), "shown_by": self._sheets_at(shown[here]),
                            "outline_mm": mask_outline(_embed(local, win, grid), grid)[0],
                            "outline_clipped": bool(self.region_pixels[region] > local.sum())})
        regions.sort(key=lambda r: (not r["narrow"], r["width_mm"]))
        what = "opening" if role == "opening" else "material"
        if not regions:
            cause = "planner"
            text = (f"Nothing of the image shows through this {what} in the front view"
                    + (": it lies under the layers in front" if hidden.any() else "")
                    + f". It is not a visible image feature; it was made narrow by {self.by}.")
        elif any(r["narrow"] for r in regions):
            cause = "image"
            r = next(r for r in regions if r["narrow"])
            text = (f"Small detail in the processed image: the {r['color']} region "
                    f"{'revealed here' if role == 'opening' else 'shown by this material'} is only about "
                    f"{r['width_mm']:.2f} mm wide in the image here, below the {threshold:.2f} mm threshold.")
        else:
            cause = "planner"
            r = regions[0]
            text = (f"Introduced by {self.by}: the {r['color']} region is "
                    f"{'at least ' if r['width_at_least'] else 'about '}{r['width_mm']:.2f} mm wide in the image here "
                    f"(threshold {threshold:.2f} mm), but this layer's {what} there is narrower.")
        return {"role": role, "cause": cause, "cause_text": text, "threshold_mm": threshold, "regions": regions,
                "revealed_outline_mm": mask_outline(_embed(revealed, win, grid), grid)[0] if revealed.any() else None,
                "revealed_pixels": int(revealed.sum()), "hidden_pixels": int(hidden.sum()),
                "covered_by": self._sheets_at(shown[hidden]), "footprint_pixels": int(foot.sum()),
                "width_precision_mm": round(mm, 3)}


def artwork_cause(issue: dict[str, Any]) -> dict[str, Any] | None:
    """Cause of an artwork-level finding (no layer): the intake or the planner, as reported."""

    if issue["source"] == "intake":
        return {"cause": "image", "cause_text": "Found in the processed image by the intake (not a sheet defect)."}
    if issue["kind"] == "planner_blocked_feature":
        return {"cause": "planner", "cause_text": "The planner could not support this image feature with any layer order."}
    return None


# -- stable identity, fingerprints, grouping ------------------------------------------------------------
def _sha(*parts: Any) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.tobytes() if isinstance(part, np.ndarray) else
                 json.dumps(part, sort_keys=True, default=str).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def issue_key(issue: dict[str, Any]) -> str:
    """Identity of an issue across recomputations: its kind, layer (stable sheet id) and place (bounding
    box to 0.25 mm).  Whether a decision about it still applies is the fingerprint's job."""

    box = [round(v * 4) / 4 for v in issue["bbox_mm"]] if issue.get("bbox_mm") else None
    detail = issue.get("details", {}).get("detail")
    return "k" + _sha(issue["kind"], issue.get("sheet"), box, detail)


def assign_keys(issues: list[dict[str, Any]]) -> None:
    seen: dict[str, int] = {}
    for issue in issues:
        key = issue_key(issue)
        seen[key] = seen.get(key, 0) + 1
        issue["key"] = key if seen[key] == 1 else f"{key}-{seen[key]}"


def fingerprint(issue: dict[str, Any], grid: StackGrid, stack: SheetStack | None, artwork: np.ndarray | None,
                settings: dict[str, Any]) -> dict[str, str]:
    """Hashes of what a review decision depends on, each separately so a lapse can say what changed:
    the processed image and the layer geometry around the issue (bounding box plus 2 mm), the print
    settings and grid, and the issue's own measurement."""

    out = {"measurement": _sha(issue["kind"], issue["severity"], issue.get("measured"), issue.get("threshold")),
           "settings": _sha(settings, grid.outer_sheet_mm, grid.outer_pixels, grid.perimeter_pixels)}
    if not issue.get("bbox_mm"):
        return out
    mm, n, p = grid.mm_per_pixel, grid.outer_pixels, grid.perimeter_pixels
    x0, y0, x1, y1 = issue["bbox_mm"]
    pad = 2.0
    rs = slice(max(0, int(n - (y1 + pad) / mm)), min(n, int(np.ceil(n - (y0 - pad) / mm))))
    cs = slice(max(0, int((x0 - pad) / mm)), min(n, int(np.ceil((x1 + pad) / mm))))
    if artwork is not None:
        out["artwork"] = _sha(np.ascontiguousarray(artwork[max(0, rs.start - p):max(0, rs.stop - p),
                                                           max(0, cs.start - p):max(0, cs.stop - p)]))
    if stack is not None:
        sheets = [s for s in stack.sheets if s.id == issue.get("sheet")] or stack.sheets
        out["geometry"] = _sha(*[np.packbits(m[rs, cs]) for s in sheets for m in (s.retained, s.visible)],
                               [(s.color, s.thickness_mm, s.rim) for s in sheets])
    return out


def group_by_location(issues: list[dict[str, Any]], grid: StackGrid, tolerance_px: int = 3) -> list[dict[str, Any]]:
    """Sheet issues at the same physical place (footprints within ``tolerance_px``), across layers.

    Sets ``issue["group"]`` on every located sheet issue and returns the groups (largest first); each
    member keeps its own layer and measurement."""

    located = [i for i in issues if i.get("sheet") and i.get("outline_mm") and i.get("bbox_mm")]
    tol = tolerance_px * grid.mm_per_pixel
    feet = {}
    for i in located:
        win = bbox_window(i["bbox_mm"], grid, tolerance_px + 1)
        crop = cv2.dilate(footprint(i, grid, win).astype(np.uint8), _disk(tolerance_px)).astype(bool)
        feet[i["id"]] = (*win, crop)
    parent = {i["id"]: i["id"] for i in located}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def touch(a: str, b: str) -> bool:
        (ar, ac, am), (br, bc, bm) = feet[a], feet[b]
        r0, r1 = max(ar.start, br.start), min(ar.stop, br.stop)
        c0, c1 = max(ac.start, bc.start), min(ac.stop, bc.stop)
        if r0 >= r1 or c0 >= c1:
            return False
        return bool((am[r0 - ar.start:r1 - ar.start, c0 - ac.start:c1 - ac.start]
                     & bm[r0 - br.start:r1 - br.start, c0 - bc.start:c1 - bc.start]).any())

    for n_a, a in enumerate(located):
        ax0, ay0, ax1, ay1 = a["bbox_mm"]
        for b in located[n_a + 1:]:
            bx0, by0, bx1, by1 = b["bbox_mm"]
            if bx0 > ax1 + 2 * tol or ax0 > bx1 + 2 * tol or by0 > ay1 + 2 * tol or ay0 > by1 + 2 * tol:
                continue
            if root(a["id"]) != root(b["id"]) and touch(a["id"], b["id"]):
                parent[root(a["id"])] = root(b["id"])
    members: dict[str, list[dict[str, Any]]] = {}
    for i in located:
        members.setdefault(root(i["id"]), []).append(i)
    groups = []
    for items in members.values():
        gid = "g" + _sha(sorted(i["key"] for i in items))[:12]
        boxes = np.array([i["bbox_mm"] for i in items])
        for i in items:
            i["group"] = gid
        groups.append({"id": gid, "members": [i["id"] for i in items], "sheets": sorted({i["sheet"] for i in items}),
                       "bbox_mm": [round(float(boxes[:, 0].min()), 6), round(float(boxes[:, 1].min()), 6),
                                   round(float(boxes[:, 2].max()), 6), round(float(boxes[:, 3].max()), 6)]})
    groups.sort(key=lambda g: (-len(g["sheets"]), g["members"][0]))
    return groups


# -- review decisions --------------------------------------------------------------------------------
REVIEW_STATES = ("needs_review", "fix_later", "accept_risk")
CHANGE_NAMES = {"artwork": "the processed image", "geometry": "the layer geometry", "settings": "the print settings",
                "measurement": "the measurement"}


def _overlap(a: list[float], b: list[float]) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def apply_decisions(issues: list[dict[str, Any]], decisions: dict[str, Any]) -> dict[str, Any]:
    """Each issue's review state (``issue["review"]``) from the saved decisions.

    A decision applies only while every fingerprint it was made with still matches; otherwise the issue
    is back to Needs review and the decision is reported as lapsed, naming what changed.  A decision
    whose key is gone lapses onto an issue of the same kind and layer at an overlapping place (it moved
    or changed shape); otherwise it is counted as matching no current issue.  Decisions never change an
    issue's severity, geometry or presence."""

    keys = {i["key"] for i in issues}
    used: set[str] = set()
    for issue in issues:
        saved = decisions.get(issue["key"])
        review: dict[str, Any] = {"state": "needs_review"}
        if saved is None:
            moved = next((k for k, s in decisions.items() if k not in keys and k not in used
                          and s.get("kind") == issue["kind"] and s.get("sheet") == issue.get("sheet")
                          and s.get("bbox_mm") and issue.get("bbox_mm") and _overlap(s["bbox_mm"], issue["bbox_mm"])), None)
            if moved is not None:
                used.add(moved)
                saved = decisions[moved]
                review["lapsed"] = {"state": saved["state"], "changed": ["location"], "decided_utc": saved.get("decided_utc"),
                                    "reason": "the issue moved or changed shape", "key": moved}
        else:
            used.add(issue["key"])
            changed = [name for name, value in issue["fingerprint"].items()
                       if saved.get("fingerprint", {}).get(name) != value]
            if not changed:
                review = {"state": saved["state"], "decided_utc": saved.get("decided_utc")}
            else:
                review["lapsed"] = {"state": saved["state"], "changed": changed, "decided_utc": saved.get("decided_utc"),
                                    "reason": " and ".join(CHANGE_NAMES.get(c, c) for c in changed) + " changed"}
        issue["review"] = review
    return {"counts": {s: sum(i["review"]["state"] == s for i in issues) for s in REVIEW_STATES},
            "lapsed": sum("lapsed" in i["review"] for i in issues),
            "unmatched": sum(k not in used for k in decisions)}


def decision_record(issue: dict[str, Any], state: str, when: str) -> dict[str, Any]:
    if state not in REVIEW_STATES:
        raise ValueError(f"unknown review state {state!r}")
    return {"state": state, "decided_utc": when, "kind": issue["kind"], "sheet": issue.get("sheet"),
            "bbox_mm": issue.get("bbox_mm"), "measured": issue.get("measured"), "fingerprint": issue["fingerprint"]}
