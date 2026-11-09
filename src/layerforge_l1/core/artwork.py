"""Region recoloring of the processed flat-color artwork (the planner target), with a replayable log.

This edits the *artwork* (the exact-palette target that planning reproduces), never a physical
sheet and never the uploaded source image.  A region is one 4-connected component of a single
exact color, the same connectivity intake reports as ``source_regions_4``; clicking selects that
component only, not every pixel of the color.  An optional box limits the component to the part
inside the box (e.g. one trouser leg when both legs are one connected region).

An artwork edit directory holds::

    base.png      the artwork the edits started from (copy; never modified)
    current.png   the artwork after every edit in the log
    edits.json    schema, base hash/origin, ordered edits with their measured effect
    session/      unsaved working session (optional): edits made on top of current.png that are not
                  saved yet (session.json, current.png); Save appends them all to edits.json at once

Every edit records the color it replaced and a hash of the exact pixels it changed, so replaying
the log from ``base.png`` must reproduce ``current.png`` bit for bit (``verify``); undo/revert
replay a prefix.  Changing the artwork does not change any stack: a stack planned from another
artwork is not validated against this one (see ``app/workspace.py``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

from .model import StackError, hex_color

ARTWORK_SCHEMA = "layerforge.artwork_edits"
ARTWORK_VERSION = "1.0.0"
COLOR_SOURCES = ("palette", "picker", "eyedropper")
# Ops: recolor one region / several regions; paint a stroke (straight line, or freehand brush clipped to
# selected regions); close a contour gap (a straight stroke between two snapped contour ends); fit the
# composition (enlarge and move the subject inside the artwork aperture, ``fit_composition``).
OPS = ("recolor_region", "recolor_regions", "paint_stroke", "close_gap", "fit_composition")
MAX_STROKE_POINTS = 4000
GAP_SNAP_MM = 2.0
# A stroke path: "line" (straight segments through the points) or "cubic" Bezier (start, control 1,
# control 2, end), sampled with a fixed number of segments so a logged edit replays bit-exact.
STROKE_PATHS = ("line", "cubic")
CUBIC_SEGMENTS = 64
# Minimum stroke radius in pixels: sqrt(2)/2 (plus a margin against rounding) makes every stroke one
# 4-connected set of pixels, so a line or curve is one region and separates the regions on its two
# sides under the planner's 4-connectivity. Thicker strokes use width / 2.
MIN_STROKE_RADIUS_PX = 0.7072
# Stroke rasterization version, saved with every new stroke so a logged edit always replays with the
# geometry it was applied with. 1: minimum radius 0.5 px (strokes saved before the version was recorded;
# a 1 px diagonal was only corner-connected); 2: minimum radius MIN_STROKE_RADIUS_PX.
STROKE_RASTERS = {1: 0.5, 2: MIN_STROKE_RADIUS_PX}
STROKE_RASTER = 2
FOUR = ndimage.generate_binary_structure(2, 1)


class ArtworkError(StackError):
    """An artwork edit that cannot be carried out as given (message is shown to the user)."""


# -- images and colors ---------------------------------------------------------------------------
def load_rgb(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("RGB"), dtype=np.uint8)  # writable copy


def save_rgb(image: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial.png")
    Image.fromarray(image).save(partial)
    partial.replace(path)


def rgb_sha256(image: np.ndarray) -> str:
    """Hash of the decoded pixels (same convention as the stack manifest's ``target_rgb_sha256``)."""

    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def mask_sha256(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(mask.astype(bool)).tobytes() + str(mask.shape).encode()).hexdigest()


def parse_hex(text: Any) -> tuple[int, int, int]:
    value = str(text or "").strip()
    if not (len(value) == 7 and value.startswith("#")):
        raise ArtworkError(f"color must be #RRGGBB, got {text!r}")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    except ValueError:
        raise ArtworkError(f"color must be #RRGGBB, got {text!r}") from None


def _color_mask(image: np.ndarray, rgb: tuple[int, int, int]) -> np.ndarray:
    return np.all(image == np.asarray(rgb, np.uint8), axis=2)


def artwork_area(image: np.ndarray, frame_hex: str | None) -> np.ndarray:
    """Pixels that belong to the artwork (everything except the never-visible frame padding)."""

    if not frame_hex:
        return np.ones(image.shape[:2], bool)
    return ~_color_mask(image, parse_hex(frame_hex))


def region_count(mask: np.ndarray) -> int:
    return int(ndimage.label(mask, structure=FOUR)[1]) if mask.any() else 0


def palette_summary(image: np.ndarray, frame_hex: str | None, mm_per_pixel: float) -> list[dict[str, Any]]:
    """Every artwork color with its area and 4-connected region count, largest first."""

    inside = artwork_area(image, frame_hex)
    colors, counts = np.unique(image[inside].reshape(-1, 3), axis=0, return_counts=True)
    rows = []
    for rgb, count in zip(colors, counts):
        mask = _color_mask(image, tuple(int(v) for v in rgb)) & inside
        rows.append({"hex": hex_color(tuple(int(v) for v in rgb)), "pixels": int(count),
                     "area_mm2": round(float(count) * mm_per_pixel**2, 2), "regions_4": region_count(mask)})
    rows.sort(key=lambda r: (-r["pixels"], r["hex"]))
    return rows


# -- selection and recolor -----------------------------------------------------------------------
def check_point(image: np.ndarray, at_px: Any) -> tuple[int, int]:
    try:
        row, col = (int(v) for v in at_px)
    except (TypeError, ValueError):
        raise ArtworkError("at_px must be [row, column] in artwork pixels") from None
    height, width = image.shape[:2]
    if not (0 <= row < height and 0 <= col < width):
        raise ArtworkError(f"point {[row, col]} is outside the {height} x {width} artwork")
    return row, col


def check_box(image: np.ndarray, box_px: Any) -> tuple[int, int, int, int] | None:
    """``[row0, col0, row1, col1]`` (end exclusive), clipped to the image; ``None`` = no limit."""

    if box_px is None:
        return None
    try:
        r0, c0, r1, c1 = (int(v) for v in box_px)
    except (TypeError, ValueError):
        raise ArtworkError("box_px must be [row0, col0, row1, col1] in artwork pixels") from None
    height, width = image.shape[:2]
    r0, r1 = sorted((max(0, min(height, r0)), max(0, min(height, r1))))
    c0, c1 = sorted((max(0, min(width, c0)), max(0, min(width, c1))))
    if r1 - r0 < 1 or c1 - c0 < 1:
        raise ArtworkError("the limit box is empty")
    return r0, c0, r1, c1


def select_region(image: np.ndarray, frame_hex: str | None, at_px: Any, box_px: Any = None,
                  mm_per_pixel: float = 1.0) -> tuple[np.ndarray, dict[str, Any]]:
    """The 4-connected same-color component at ``at_px`` (inside ``box_px`` when given)."""

    row, col = check_point(image, at_px)
    box = check_box(image, box_px)
    rgb = tuple(int(v) for v in image[row, col])
    color = hex_color(rgb)
    if frame_hex and color == frame_hex.upper():
        raise ArtworkError("that point is in the frame padding around the artwork (never visible); click inside the artwork")
    same = _color_mask(image, rgb)
    candidates = same
    if box is not None:
        r0, c0, r1, c1 = box
        if not (r0 <= row < r1 and c0 <= col < c1):
            raise ArtworkError("the clicked point is outside the limit box")
        candidates = np.zeros_like(same)
        candidates[r0:r1, c0:c1] = same[r0:r1, c0:c1]
    labels, _ = ndimage.label(candidates, structure=FOUR)
    mask = labels == labels[row, col]
    rows, cols = np.nonzero(mask)
    full = ndimage.label(same, structure=FOUR)[0]
    whole = full == full[row, col]
    info = {
        "color": color, "at_px": [row, col], "box_px": list(box) if box else None,
        "pixels": int(mask.sum()), "area_mm2": round(float(mask.sum()) * mm_per_pixel**2, 3),
        "bbox_px": [int(rows.min()), int(cols.min()), int(rows.max()) + 1, int(cols.max()) + 1],
        "regions_of_color": region_count(same), "pixels_of_color": int(same.sum()),
        "limited_by_box": bool(box is not None and int(whole.sum()) != int(mask.sum())),
        "whole_region_pixels": int(whole.sum()),
        "region_sha256": mask_sha256(mask),
    }
    return mask, info


def recolor(image: np.ndarray, mask: np.ndarray, to_hex: str, frame_hex: str | None) -> np.ndarray:
    rgb = parse_hex(to_hex)
    if frame_hex and hex_color(rgb) == frame_hex.upper():
        raise ArtworkError(f"{frame_hex} is the frame color reserved for the padding; choose another color")
    out = image.copy()
    out[mask] = rgb
    return out


def change_summary(before: np.ndarray, after: np.ndarray, mask: np.ndarray, from_hex: str | list[str], to_hex: str,
                   frame_hex: str | None) -> dict[str, Any]:
    """What a recolor did to the palette and to the regions of every color involved.

    ``from_hex`` is the replaced color, or the list of colors of a multi-region selection."""

    inside = artwork_area(before, frame_hex)
    froms = sorted({from_hex} if isinstance(from_hex, str) else set(from_hex))
    to = to_hex.upper()
    to_rgb = parse_hex(to)
    ring = ndimage.binary_dilation(mask, structure=FOUR) & ~mask
    to_before = _color_mask(before, to_rgb) & inside
    touching = ndimage.label(to_before, structure=FOUR)[0][ring & to_before]
    before_colors = {hex_color(tuple(int(v) for v in c)) for c in np.unique(before[inside].reshape(-1, 3), axis=0)}
    after_colors = {hex_color(tuple(int(v) for v in c)) for c in np.unique(after[inside].reshape(-1, 3), axis=0)}
    involved = froms + ([to] if to not in froms else [])
    return {
        "from": froms[0] if len(froms) == 1 else "mixed", "from_colors": froms, "to": to,
        "pixels": int((mask & np.any(before != after, axis=2)).sum()),
        "palette_size_before": len(before_colors), "palette_size_after": len(after_colors),
        "colors_removed": sorted(before_colors - after_colors), "colors_added": sorted(after_colors - before_colors),
        "regions_before": {h: region_count(_color_mask(before, parse_hex(h)) & inside) for h in involved},
        "regions_after": {h: region_count(_color_mask(after, parse_hex(h)) & inside) for h in involved},
        "merged_with_adjacent_regions": int(np.unique(touching[touching > 0]).size),
    }


MAX_REGIONS = 500


def check_regions(regions: Any) -> list[dict[str, Any]]:
    """``[{"at_px": [row, col], "box_px": [r0, c0, r1, c1] (optional)}, ...]``, normalized."""

    if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
        raise ArtworkError(f"regions must be a list of at most {MAX_REGIONS} entries")
    out = []
    for item in regions:
        if not isinstance(item, dict):
            raise ArtworkError("each region needs at_px and optionally box_px")
        try:
            at = [int(v) for v in item.get("at_px") or []]
            box = None if item.get("box_px") is None else [int(v) for v in item["box_px"]]
        except (TypeError, ValueError):
            raise ArtworkError("at_px / box_px must be whole artwork pixel coordinates") from None
        if len(at) != 2:
            raise ArtworkError("at_px must be [row, column] in artwork pixels")
        out.append({"at_px": at, **({"box_px": box} if box is not None else {})})
    return out


def select_regions(image: np.ndarray, frame_hex: str | None, regions: list[dict[str, Any]],
                   mm_per_pixel: float = 1.0) -> tuple[np.ndarray, dict[str, Any]]:
    """Union of several ``select_region`` selections (any colors); a region listed twice counts once."""

    if not regions:
        raise ArtworkError("no region is selected")
    mask = np.zeros(image.shape[:2], bool)
    items, seen = [], set()
    for region in regions:
        part, info = select_region(image, frame_hex, region["at_px"], region.get("box_px"), mm_per_pixel)
        if info["region_sha256"] in seen:
            continue
        seen.add(info["region_sha256"])
        mask |= part
        items.append({k: info[k] for k in ("color", "at_px", "box_px", "pixels", "limited_by_box", "whole_region_pixels")})
    rows, cols = np.nonzero(mask)
    colors = sorted({i["color"] for i in items})
    single = len(items) == 1
    info = {
        "color": colors[0] if len(colors) == 1 else "mixed", "colors": colors, "regions": len(items), "items": items,
        "pixels": int(mask.sum()), "area_mm2": round(float(mask.sum()) * mm_per_pixel**2, 3),
        "bbox_px": [int(rows.min()), int(cols.min()), int(rows.max()) + 1, int(cols.max()) + 1],
        "regions_of_color": region_count(_color_mask(image, parse_hex(colors[0]))) if single else None,
        "limited_by_box": any(i["limited_by_box"] for i in items),
        "whole_region_pixels": sum(i["whole_region_pixels"] for i in items),
        "box_px": items[0]["box_px"] if single else None,
        "region_sha256": mask_sha256(mask),
    }
    return mask, info


def toggle_region(image: np.ndarray, frame_hex: str | None, regions: list[dict[str, Any]], at_px: Any,
                  box_px: Any = None) -> list[dict[str, Any]]:
    """Shift+click: remove the selected region that contains ``at_px``, else add the region there."""

    row, col = check_point(image, at_px)
    for index, region in enumerate(regions):
        part, _ = select_region(image, frame_hex, region["at_px"], region.get("box_px"))
        if part[row, col]:
            return regions[:index] + regions[index + 1:]
    _, info = select_region(image, frame_hex, [row, col], box_px)  # refuses the frame and points outside the box
    return regions + [{"at_px": [row, col], **({"box_px": info["box_px"]} if info["box_px"] else {})}]


def centre_line(points_px: list[list[int]], path: str = "line") -> list[list[float]]:
    """The stroke's centre line ([row, col] points): the points themselves, or a sampled cubic Bezier."""

    if path != "cubic":
        return [[float(v) for v in q] for q in points_px]
    p0, c1, c2, p3 = (np.array(q, float) for q in points_px)
    t = np.linspace(0.0, 1.0, CUBIC_SEGMENTS + 1)[:, None]
    curve = (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t**2 * c2 + t**3 * p3
    return curve.tolist()


def stroke_mask(shape: tuple[int, int], points_px: list[list[float]], width_px: float,
                min_radius_px: float = MIN_STROKE_RADIUS_PX) -> np.ndarray:
    """Pixels whose centre lies within ``width_px / 2`` of the polyline through ``points_px`` ([row, col]).

    Exact distance to each segment (round ends and joins), evaluated only in each segment's bounding box,
    so the result is deterministic and independent of any drawing library. Never anti-aliased: a pixel
    is painted or not. The radius is at least ``min_radius_px`` (by default ``MIN_STROKE_RADIUS_PX``: one
    4-connected stroke)."""

    out = np.zeros(shape, bool)
    r = max(float(width_px) / 2.0, min_radius_px)
    pts = [np.array(q, float) for q in points_px]
    if len(pts) == 1:
        pts = pts * 2
    for a, b in zip(pts[:-1], pts[1:]):
        r0, r1 = int(np.floor(min(a[0], b[0]) - r)), int(np.ceil(max(a[0], b[0]) + r)) + 1
        c0, c1 = int(np.floor(min(a[1], b[1]) - r)), int(np.ceil(max(a[1], b[1]) + r)) + 1
        r0, c0, r1, c1 = max(r0, 0), max(c0, 0), min(r1, shape[0]), min(c1, shape[1])
        if r0 >= r1 or c0 >= c1:
            continue
        rows, cols = np.mgrid[r0:r1, c0:c1].astype(float)
        d = b - a
        length2 = float(d @ d)
        t = np.zeros_like(rows) if length2 == 0 else np.clip(((rows - a[0]) * d[0] + (cols - a[1]) * d[1]) / length2, 0, 1)
        dist = np.hypot(rows - (a[0] + t * d[0]), cols - (a[1] + t * d[1]))
        out[r0:r1, c0:c1] |= dist <= r
    return out


def snap_to_color(image: np.ndarray, at: tuple[int, int], rgb: tuple[int, int, int], radius_px: float) -> tuple[int, int] | None:
    """The pixel of color ``rgb`` nearest to ``at`` within ``radius_px`` (ties: lowest row, then column)."""

    row, col = at
    r = int(np.ceil(radius_px))
    r0, c0 = max(row - r, 0), max(col - r, 0)
    window = _color_mask(image[r0:row + r + 1, c0:col + r + 1], rgb)
    if not window.any():
        return None
    ys, xs = np.nonzero(window)
    dist = np.hypot(ys + r0 - row, xs + c0 - col)
    best = np.lexsort((xs, ys, dist))[0]
    if dist[best] > radius_px:
        return None
    return int(ys[best] + r0), int(xs[best] + c0)


def check_op(op: Any) -> dict[str, Any]:
    """Normalized request: ``recolor_region`` (at_px, optional box_px) or ``recolor_regions`` (regions:
    a multi-region selection recolored at once as one edit), plus color, source and sampled_at_px."""

    if not isinstance(op, dict):
        raise ArtworkError(f"an artwork edit needs op one of {', '.join(OPS)}")
    kind = op.get("op") or ("recolor_regions" if "regions" in op else "recolor_region")
    if kind not in OPS:
        raise ArtworkError(f"an artwork edit needs op one of {', '.join(OPS)}")
    if kind == "fit_composition":
        return check_fit_op(op)
    source = op.get("source", "picker")
    if source not in COLOR_SOURCES:
        raise ArtworkError(f"source must be one of {', '.join(COLOR_SOURCES)}")
    out: dict[str, Any] = {"op": kind, "color": "#%02X%02X%02X" % parse_hex(op.get("color")), "source": source}
    if kind in ("paint_stroke", "close_gap"):
        try:
            width = float(op.get("width_mm"))
            points = [[int(round(float(v))) for v in q] for q in (op.get("points_px") or [])]
        except (TypeError, ValueError):
            raise ArtworkError("a stroke needs width_mm and points_px [[row, column], ...] in artwork pixels") from None
        if not (0 < width <= 50):
            raise ArtworkError("the thickness must be between 0 and 50 mm")
        path = op.get("path") or "line"
        if path not in STROKE_PATHS:
            raise ArtworkError(f"a stroke path must be one of {', '.join(STROKE_PATHS)}")
        need = (4, 4) if path == "cubic" else (2, 2) if kind == "close_gap" else (1, MAX_STROKE_POINTS)
        if not (need[0] <= len(points) <= need[1]) or any(len(q) != 2 for q in points):
            raise ArtworkError("a curve needs 4 points: start, control 1, control 2, end" if path == "cubic"
                               else "close_gap needs the two end points" if kind == "close_gap"
                               else f"a stroke needs 1 to {MAX_STROKE_POINTS} points [row, column]")
        raster = op.get("stroke_raster", STROKE_RASTER)
        if raster not in STROKE_RASTERS or isinstance(raster, bool):
            raise ArtworkError(f"stroke_raster must be one of {', '.join(map(str, STROKE_RASTERS))}")
        out.update(points_px=points, width_mm=round(width, 3), stroke_raster=raster)
        if path == "cubic":
            out["path"] = "cubic"  # absent for straight strokes, so earlier logged edits keep their form
        if kind == "paint_stroke" and op.get("clip_regions"):
            out["clip_regions"] = check_regions(op["clip_regions"])  # paint only inside these regions
        if kind == "close_gap":
            out["snap_color"] = "#%02X%02X%02X" % parse_hex(op.get("snap_color") or op.get("color"))
    elif kind == "recolor_regions":
        out["regions"] = check_regions(op.get("regions"))
        if not out["regions"]:
            raise ArtworkError("no region is selected")
    else:
        try:
            out["at_px"] = [int(v) for v in op.get("at_px") or []]
        except (TypeError, ValueError):
            raise ArtworkError("at_px must be [row, column] in artwork pixels") from None
        if len(out["at_px"]) != 2:
            raise ArtworkError("at_px must be [row, column] in artwork pixels")
        if op.get("box_px") is not None:
            out["box_px"] = [int(v) for v in op["box_px"]]
    if source == "eyedropper":
        if not op.get("sampled_at_px"):
            raise ArtworkError("an eyedropper color needs the sampled point (sampled_at_px)")
        out["sampled_at_px"] = [int(v) for v in op["sampled_at_px"]]
    return out


def preview_id(current_sha: str, op: dict[str, Any]) -> str:
    """Identifies one previewed edit on one artwork; Apply must name the preview the user saw."""

    return hashlib.sha256(json.dumps([current_sha, op], sort_keys=True).encode()).hexdigest()[:16]


def plan_edit(image: np.ndarray, op: dict[str, Any], frame_hex: str | None,
              mm_per_pixel: float) -> tuple[np.ndarray, np.ndarray, dict[str, Any], dict[str, Any]]:
    """(after, mask, selection, change) of one checked op on ``image``; nothing is saved."""

    if op["op"] == "fit_composition":
        return plan_fit(image, op, frame_hex, mm_per_pixel)
    if op["source"] == "eyedropper":
        row, col = check_point(image, op["sampled_at_px"])
        sampled = hex_color(tuple(int(v) for v in image[row, col]))
        if sampled != op["color"]:
            raise ArtworkError(f"the eyedropper point now has {sampled}, not {op['color']}; sample again")
    if op["op"] in ("paint_stroke", "close_gap"):
        mask, selection = _stroke_selection(image, op, frame_hex, mm_per_pixel)
        froms = selection["colors"]
    elif op["op"] == "recolor_regions":
        mask, selection = select_regions(image, frame_hex, op["regions"], mm_per_pixel)
        if selection["colors"] == [op["color"]]:
            raise ArtworkError(f"every selected region already has {op['color']}; nothing would change")
        froms = selection["colors"]
    else:
        mask, selection = select_region(image, frame_hex, op["at_px"], op.get("box_px"), mm_per_pixel)
        if selection["color"] == op["color"]:
            raise ArtworkError(f"the selected region already has {op['color']}; nothing would change")
        froms = selection["color"]
    after = recolor(image, mask, op["color"], frame_hex)  # all selected pixels at once, nothing else
    return after, mask, selection, change_summary(image, after, mask, froms, op["color"], frame_hex)


def _stroke_selection(image: np.ndarray, op: dict[str, Any], frame_hex: str | None,
                      mm_per_pixel: float) -> tuple[np.ndarray, dict[str, Any]]:
    """The pixels a line, brush stroke or gap closure paints, described like a selection."""

    for q in op["points_px"]:
        check_point(image, q)
    points = op["points_px"]
    if op["op"] == "close_gap":  # each end snaps to the nearest contour pixel, so the closure meets it
        rgb = parse_hex(op["snap_color"])
        snapped = []
        for q in (points[0], points[-1]):  # only the ends snap; control points stay where they are
            hit = snap_to_color(image, (q[0], q[1]), rgb, GAP_SNAP_MM / mm_per_pixel)
            if hit is None:
                raise ArtworkError(f"no {op['snap_color']} contour within {GAP_SNAP_MM:g} mm of {q}; click closer to its end")
            snapped.append(list(hit))
        if snapped[0] == snapped[1]:
            raise ArtworkError("both ends snapped to the same pixel; click the two ends of the gap")
        points = [snapped[0], *points[1:-1], snapped[1]]  # a curved closure keeps its control points
    line = centre_line(points, op.get("path", "line"))
    radius = STROKE_RASTERS[op.get("stroke_raster", STROKE_RASTER)]
    mask = stroke_mask(image.shape[:2], line, op["width_mm"] / mm_per_pixel, radius) & artwork_area(image, frame_hex)
    clip = None
    if op.get("clip_regions"):
        clip, _ = select_regions(image, frame_hex, op["clip_regions"], mm_per_pixel)
        mask &= clip
    changed = mask & np.any(image != np.asarray(parse_hex(op["color"]), np.uint8), axis=2)
    if not changed.any():
        raise ArtworkError("the stroke changes no pixel" + (" inside the selected region(s)" if clip is not None else ""))
    rows, cols = np.nonzero(mask)
    colors = sorted({hex_color(tuple(int(v) for v in c)) for c in np.unique(image[changed].reshape(-1, 3), axis=0)})
    selection = {
        "color": colors[0] if len(colors) == 1 else "mixed", "colors": colors, "regions": 1, "items": [],
        "pixels": int(mask.sum()), "area_mm2": round(float(mask.sum()) * mm_per_pixel**2, 3),
        "bbox_px": [int(rows.min()), int(cols.min()), int(rows.max()) + 1, int(cols.max()) + 1],
        "regions_of_color": None, "limited_by_box": clip is not None, "whole_region_pixels": int(mask.sum()),
        "box_px": None, "region_sha256": mask_sha256(mask), "stroke_points_px": points,
        "clipped_to_regions": len(op.get("clip_regions") or []),
    }
    return mask, selection


# -- composition: fit the subject to the artwork aperture -----------------------------------------
# The artwork aperture is the whole square artwork grid; the sheet, its shared perimeter and the grid
# are outside this edit and never change.  ``fit_composition`` keeps a source box of the current
# artwork, scales it by ONE factor (aspect ratio kept, never stretched) with nearest-neighbour sampling
# (every output pixel is a copy of one input pixel: no new color, no anti-aliasing) and places it at an
# offset on a canvas filled with the background (a palette color, or the never-visible frame).  With a
# factor >= 1 every input pixel of the box becomes a non-empty rectangular block, so the 4-connected
# regions of each color inside the box keep their topology; below 1 pixels can drop out (reported).
FIT_MARGIN = 0.08
FIT_MARGIN_MAX = 0.4
FIT_BACKGROUND_SHARE = 0.9   # a color covering this share of the artwork's outer border is the background
FIT_SCALE_RANGE = (0.05, 32.0)
FIT_MIN_GAIN = 1.0005        # an automatic fit must enlarge the subject at least this much
FIT_STATUSES = ("READY", "ALREADY_FITTED", "AMBIGUOUS_BACKGROUND", "MULTIPLE_FOREGROUND_REGIONS", "NO_FOREGROUND")
EDGES = ("top", "bottom", "left", "right")


def _frame_mask(image: np.ndarray, frame_hex: str | None) -> np.ndarray:
    return _color_mask(image, parse_hex(frame_hex)) if frame_hex else np.zeros(image.shape[:2], bool)


def _bbox(mask: np.ndarray) -> list[int]:
    rows, cols = np.nonzero(mask)
    return [int(rows.min()), int(cols.min()), int(rows.max()) + 1, int(cols.max()) + 1]


def detect_background(image: np.ndarray, frame_hex: str | None) -> dict[str, Any]:
    """The artwork's background, or None when it is ambiguous.

    The outer border of the artwork (its pixels on the image edge or next to the frame padding) is
    examined.  When the artwork has an irregular outline (transparent source: the frame surrounds the
    subject, so that border is the subject's outline), the never-visible frame is the background.
    Otherwise a single color covering at least ``FIT_BACKGROUND_SHARE`` of the border is the background.
    Anything else is ambiguous: no guess."""

    frame = _frame_mask(image, frame_hex)
    inside = ~frame
    if not inside.any():
        raise ArtworkError("the artwork has no pixels outside the frame")
    pad = np.pad(inside, 1, constant_values=False)
    border = inside & ~(pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:])
    colors, counts = np.unique(image[border].reshape(-1, 3), axis=0, return_counts=True)
    order = np.argsort(-counts, kind="stable")
    top = [{"hex": hex_color(tuple(int(v) for v in colors[i])), "share": round(float(counts[i] / counts.sum()), 4)}
           for i in order[:4]]
    r0, c0, r1, c1 = _bbox(inside)
    rectangular = int(inside.sum()) == (r1 - r0) * (c1 - c0)
    out = {"border_pixels": int(border.sum()), "border_colors": top, "artwork_box_px": [r0, c0, r1, c1],
           "artwork_is_rectangle": rectangular}
    if frame.any() and not rectangular:  # its border is the subject's own outline, not a background
        return {**out, "background": "frame", "kind": "frame", "share": None}
    if top[0]["share"] >= FIT_BACKGROUND_SHARE:
        return {**out, "background": top[0]["hex"], "kind": "color", "share": top[0]["share"]}
    return {**out, "background": None, "kind": "ambiguous", "share": top[0]["share"]}


def foreground_mask(image: np.ndarray, frame_hex: str | None, background: str) -> np.ndarray:
    """Every artwork pixel that is not the background (and not the frame).  Background-colored pixels
    inside the subject (e.g. eye whites of the background color) are kept by the box, not by this mask."""

    inside = ~_frame_mask(image, frame_hex)
    if background == "frame":
        return inside
    return inside & ~_color_mask(image, parse_hex(background))


def scaled_size(n: int, scale: float) -> int:
    return max(1, int(np.floor(n * scale + 1e-9)))


def fit_placement(shape: tuple[int, int], box: list[int], margin: float,
                  anchor: tuple[str, ...] = ()) -> tuple[float, list[int]]:
    """The largest single scale factor that fits ``box`` inside the canvas minus ``margin`` (a fraction
    of the canvas side, on each side) and the offset that centres it.  Sides in ``anchor`` get no
    margin and the subject is placed against them (a subject cut off by the original image edge)."""

    h, w = shape
    bh, bw = box[2] - box[0], box[3] - box[1]
    m = {"top": 0.0 if "top" in anchor else margin * h, "bottom": 0.0 if "bottom" in anchor else margin * h,
         "left": 0.0 if "left" in anchor else margin * w, "right": 0.0 if "right" in anchor else margin * w}
    avail_h, avail_w = h - m["top"] - m["bottom"], w - m["left"] - m["right"]
    if avail_h <= 0 or avail_w <= 0:
        raise ArtworkError("the margins leave no room for the artwork")
    scale = float(np.floor(min(avail_w / bw, avail_h / bh) * 1e6) / 1e6)
    sh, sw = scaled_size(bh, scale), scaled_size(bw, scale)

    def place(size: int, room: int, low: str, high: str, m_low: float, avail: float) -> int:
        if low in anchor and high not in anchor:
            return 0
        if high in anchor and low not in anchor:
            return room - size
        return int(round(m_low + (avail - size) / 2))

    return scale, [place(sh, h, "top", "bottom", m["top"], avail_h), place(sw, w, "left", "right", m["left"], avail_w)]


def _fit_op(box: list[int], scale: float, offset: list[int], background: str, mode: str,
            margin: float | None, accept_clipping: bool = False) -> dict[str, Any]:
    op = {"op": "fit_composition", "source_box_px": [int(v) for v in box], "scale": round(float(scale), 6),
          "offset_px": [int(v) for v in offset], "background": background, "color": background, "source": "fit",
          "mode": mode, "margin": None if margin is None else round(float(margin), 4)}
    if accept_clipping:  # only present when the user approved visible loss (older ops are unchanged)
        op["accept_clipping"] = True
    return op


def analyse_composition(image: np.ndarray, frame_hex: str | None, mm_per_pixel: float, margin: float = FIT_MARGIN,
                        anchor_cut_edges: bool = False) -> dict[str, Any]:
    """Where the subject is and the automatic fit, if one can be proposed without guessing.

    ``status``: READY (``op`` is the automatic fit), ALREADY_FITTED (no enlargement possible with these
    margins), AMBIGUOUS_BACKGROUND (no dominant background), MULTIPLE_FOREGROUND_REGIONS (separate
    pieces: frame them manually), NO_FOREGROUND.  ``manual`` is a starting point for manual framing."""

    if not (0.0 <= margin <= FIT_MARGIN_MAX):
        raise ArtworkError(f"the margin must be between 0 and {int(FIT_MARGIN_MAX * 100)} %")
    h, w = image.shape[:2]
    bg = detect_background(image, frame_hex)
    out: dict[str, Any] = {"aperture_px": [h, w], "aperture_mm": [round(h * mm_per_pixel, 2), round(w * mm_per_pixel, 2)],
                           "background": bg, "margin": margin, "anchor_cut_edges": bool(anchor_cut_edges), "op": None}
    art_box = bg["artwork_box_px"]
    if bg["background"] is None:
        out.update(status="AMBIGUOUS_BACKGROUND",
                   reason="No single color covers the artwork's border, so the background cannot be told from the "
                          "subject. Frame the subject manually: drag a Box around it and choose the background color.",
                   manual={"source_box_px": art_box, "background": None})
        return out
    fg = foreground_mask(image, frame_hex, bg["background"])
    if not fg.any():
        out.update(status="NO_FOREGROUND", reason="every artwork pixel is the background color", manual=None)
        return out
    box = _bbox(fg)
    labels, count = ndimage.label(fg, structure=np.ones((3, 3), bool))
    sizes = np.bincount(labels.ravel())[1:]
    pieces = [{"bbox_px": _bbox(labels == i + 1), "pixels": int(sizes[i])} for i in np.argsort(-sizes)[:12]]
    touches = [e for e, hit in zip(EDGES, (box[0] == art_box[0], box[2] == art_box[2], box[1] == art_box[1],
                                            box[3] == art_box[3])) if hit]
    bh, bw = box[2] - box[0], box[3] - box[1]
    out.update(foreground_box_px=box, foreground_px=int(fg.sum()), components=int(count), pieces=pieces,
               foreground_share=[round(bh / h, 4), round(bw / w, 4)],
               foreground_mm=[round(bh * mm_per_pixel, 2), round(bw * mm_per_pixel, 2)], touches_edges=touches)
    scale, offset = fit_placement((h, w), box, margin, tuple(touches) if anchor_cut_edges else ())
    out["manual"] = {"source_box_px": box, "background": bg["background"], "scale": scale, "offset_px": offset}
    if count > 1:
        out.update(status="MULTIPLE_FOREGROUND_REGIONS",
                   reason=f"The subject is {count} separate pieces; which of them belong to the subject is not "
                          "guessed. The box below covers all of them: check it (or drag a Box) and apply manually.")
        return out
    if scale < FIT_MIN_GAIN:
        out.update(status="ALREADY_FITTED",
                   reason="With these margins the subject cannot be enlarged; reduce the margin or frame manually.")
        return out
    out.update(status="READY", op=_fit_op(box, scale, offset, bg["background"], "auto", margin),
               reason=None)
    return out


def check_fit_op(op: dict[str, Any]) -> dict[str, Any]:
    try:
        box = [int(v) for v in op.get("source_box_px") or []]
        scale = float(op.get("scale"))
        offset = [int(round(float(v))) for v in op.get("offset_px") or []]
        margin = None if op.get("margin") is None else float(op["margin"])
    except (TypeError, ValueError):
        raise ArtworkError("a fit needs source_box_px [row0, col0, row1, col1], scale and offset_px [row, column]") from None
    if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1] or len(offset) != 2:
        raise ArtworkError("a fit needs source_box_px [row0, col0, row1, col1] (end exclusive) and offset_px [row, column]")
    if not (FIT_SCALE_RANGE[0] <= scale <= FIT_SCALE_RANGE[1]) or not np.isfinite(scale):
        raise ArtworkError(f"the scale must be between {FIT_SCALE_RANGE[0]} and {FIT_SCALE_RANGE[1]}")
    background = op.get("background")
    if background != "frame":
        background = "#%02X%02X%02X" % parse_hex(background)
    mode = op.get("mode", "manual")
    if mode not in ("auto", "manual"):
        raise ArtworkError("mode must be auto or manual")
    return _fit_op(box, scale, offset, background, mode, margin, op.get("accept_clipping") is True)


def _full_mapping(op: dict[str, Any], shape: tuple[int, int]) -> tuple[np.ndarray, ...]:
    """Every destination row / column of the scaled box (also those off the canvas), the source row /
    column each one copies, and which of them land inside the canvas."""

    r0, c0, r1, c1 = op["source_box_px"]
    s = float(op["scale"])
    oy, ox = op["offset_px"]
    sh, sw = scaled_size(r1 - r0, s), scaled_size(c1 - c0, s)
    dy, dx = np.arange(sh), np.arange(sw)
    src_r = np.minimum(r0 + np.floor((dy + 0.5) / s).astype(np.int64), r1 - 1)
    src_c = np.minimum(c0 + np.floor((dx + 0.5) / s).astype(np.int64), c1 - 1)
    keep_r, keep_c = (oy + dy >= 0) & (oy + dy < shape[0]), (ox + dx >= 0) & (ox + dx < shape[1])
    return oy + dy, ox + dx, src_r, src_c, keep_r, keep_c


def _mapping(op: dict[str, Any], shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Destination rows / columns inside the canvas and the source row / column each one copies."""

    rows, cols, src_r, src_c, keep_r, keep_c = _full_mapping(op, shape)
    return rows[keep_r], cols[keep_c], src_r[keep_r], src_c[keep_c]


def subject_loss(image: np.ndarray, frame_hex: str | None, op: dict[str, Any]) -> tuple[dict[str, Any], np.ndarray]:
    """What of the subject a fit loses, measured on the exact mapping ``compose`` uses.

    Three different things, never mixed:
    * new clipping: transformed subject pixels that land outside the canvas (``subject_clipped_px``;
      ``subject_clipped_source_px`` source pixels vanish entirely).  Returned as a source-grid mask.
    * downscale skipping: below 100 % nearest-neighbour sampling skips whole source rows / columns
      (``subject_skipped_px``); they are inside the canvas, not clipped.
    * cropped in the original: subject pixels on the artwork's own rectangular edge
      (``source_cropped_edges``); the original image already cut the subject there, the fit did not."""

    h, w = image.shape[:2]
    r0, c0, r1, c1 = op["source_box_px"]
    fg = foreground_mask(image, frame_hex, op["background"])
    in_box = np.zeros_like(fg)
    in_box[r0:r1, c0:c1] = fg[r0:r1, c0:c1]
    rows, cols, src_r, src_c, keep_r, keep_c = _full_mapping(op, (h, w))
    sampled_r, sampled_c, shown_r, shown_c = (np.zeros(n, bool) for n in (h, w, h, w))
    sampled_r[src_r], sampled_c[src_c] = True, True
    shown_r[src_r[keep_r]], shown_c[src_c[keep_c]] = True, True
    sampled = np.outer(sampled_r, sampled_c)
    clipped_mask = in_box & sampled & ~np.outer(shown_r, shown_c)
    block = fg[np.ix_(src_r, src_c)]
    cropped: dict[str, int] = {}
    if op["background"] != "frame":  # a rectangular artwork: its edges are the original image's edges
        a0, b0, a1, b1 = _bbox(~_frame_mask(image, frame_hex))
        lines = {"top": in_box[a0, b0:b1], "bottom": in_box[a1 - 1, b0:b1], "left": in_box[a0:a1, b0], "right": in_box[a0:a1, b1 - 1]}
        cropped = {e: int(v.sum()) for e, v in lines.items() if v.any()}
    info = {"subject_px": int(in_box.sum()),
            "foreground_outside_box_px": int(fg.sum() - in_box.sum()),
            "subject_clipped_px": int(block.sum() - block[np.ix_(keep_r, keep_c)].sum()),
            "subject_clipped_source_px": int(clipped_mask.sum()),
            "subject_skipped_px": int((in_box & ~sampled).sum()),
            "clipped_edges": [e for e, past in zip(EDGES, (block[rows < 0].any(), block[rows >= h].any(),
                                                             block[:, cols < 0].any(), block[:, cols >= w].any())) if past],
            "source_cropped_edges": cropped}
    return info, clipped_mask


def remap(array: np.ndarray, op: dict[str, Any], fill: Any) -> np.ndarray:
    """``array`` (an image or a mask on the artwork grid) moved by the fit; outside the box: ``fill``."""

    out = np.empty_like(array)
    out[...] = fill
    rows, cols, src_r, src_c = _mapping(op, array.shape[:2])
    if rows.size and cols.size:
        out[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1] = array[np.ix_(src_r, src_c)]
    return out


def remap_box(box: list[int], op: dict[str, Any], shape: tuple[int, int]) -> list[int] | None:
    """A [r0, c0, r1, c1] box after the fit (None when it ends up outside the canvas or the source box)."""

    mask = np.zeros(shape, bool)
    mask[box[0]:box[2], box[1]:box[3]] = True
    moved = remap(mask, op, False)
    return _bbox(moved) if moved.any() else None


def compose(image: np.ndarray, frame_hex: str | None, op: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    """The artwork after one checked fit, and what it kept, cut or left out."""

    h, w = image.shape[:2]
    r0, c0, r1, c1 = op["source_box_px"]
    if r0 < 0 or c0 < 0 or r1 > h or c1 > w:
        raise ArtworkError(f"the source box {op['source_box_px']} is outside the {h} x {w} artwork")
    background = op["background"]
    if background == "frame":
        if not frame_hex:
            raise ArtworkError("this artwork has no frame padding; choose a background color")
        fill = np.asarray(parse_hex(frame_hex), np.uint8)
    else:
        if frame_hex and background.upper() == frame_hex.upper():
            raise ArtworkError(f"{frame_hex} is the frame color; choose 'frame' as the background instead")
        fill = np.asarray(parse_hex(background), np.uint8)
    rows, cols, src_r, src_c = _mapping(op, (h, w))
    if not (rows.size and cols.size):
        raise ArtworkError("the scaled artwork does not reach the canvas; change the scale or position")
    frame = _frame_mask(image, frame_hex)
    block = image[np.ix_(src_r, src_c)].copy()
    block[frame[np.ix_(src_r, src_c)]] = fill  # frame inside the box becomes background
    after = np.empty_like(image)
    after[:] = fill
    after[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1] = block
    loss, _ = subject_loss(image, frame_hex, op)
    oy, ox = op["offset_px"]
    sh, sw = scaled_size(r1 - r0, float(op["scale"])), scaled_size(c1 - c0, float(op["scale"]))
    info = {"dest_box_px": [int(rows[0]), int(cols[0]), int(rows[-1]) + 1, int(cols[-1]) + 1],
            "scaled_size_px": [sh, sw], "transformed_box_px": [int(oy), int(ox), int(oy + sh), int(ox + sw)],
            "accept_clipping": bool(op.get("accept_clipping")), **loss}
    return after, info


def _region_counts(image: np.ndarray, frame_hex: str | None) -> dict[str, int]:
    inside = ~_frame_mask(image, frame_hex)
    colors = np.unique(image[inside].reshape(-1, 3), axis=0)
    return {hex_color(tuple(int(v) for v in c)): region_count(_color_mask(image, tuple(int(v) for v in c)) & inside)
            for c in colors}


def plan_fit(image: np.ndarray, op: dict[str, Any], frame_hex: str | None,
             mm_per_pixel: float) -> tuple[np.ndarray, np.ndarray, dict[str, Any], dict[str, Any]]:
    """(after, changed pixels, selection, change) of a fit, in the form every artwork edit has."""

    after, info = compose(image, frame_hex, op)
    mask = np.any(image != after, axis=2)
    if not mask.any():
        raise ArtworkError("this fit changes no pixel")
    background = op["background"]
    selection = {"color": "composition", "colors": [background], "regions": 1, "items": [],
                 "pixels": int(mask.sum()), "area_mm2": round(float(mask.sum()) * mm_per_pixel**2, 3),
                 "bbox_px": _bbox(mask), "regions_of_color": None, "limited_by_box": False,
                 "whole_region_pixels": int(mask.sum()), "box_px": op["source_box_px"], "region_sha256": mask_sha256(mask)}
    before_regions, after_regions = _region_counts(image, frame_hex), _region_counts(after, frame_hex)
    fg_after = foreground_mask(after, frame_hex, background)
    fg_before = foreground_mask(image, frame_hex, background)
    keep = [h for h in before_regions if h != background]
    changed_topology = {h: [before_regions[h], after_regions.get(h, 0)] for h in keep
                        if before_regions[h] != after_regions.get(h, 0)}
    box_before = _bbox(fg_before) if fg_before.any() else None
    box_after = _bbox(fg_after) if fg_after.any() else None
    size = lambda b: None if b is None else [round((b[2] - b[0]) * mm_per_pixel, 2), round((b[3] - b[1]) * mm_per_pixel, 2)]  # noqa: E731
    change = {
        "from": "composition", "from_colors": [background], "to": background, "pixels": int(mask.sum()),
        "scale": op["scale"], "source_box_px": op["source_box_px"], **info,
        "palette_size_before": len(before_regions), "palette_size_after": len(after_regions),
        "colors_removed": sorted(set(before_regions) - set(after_regions)),
        "colors_added": sorted(set(after_regions) - set(before_regions)),
        "regions_before": before_regions, "regions_after": after_regions,
        "subject_regions_changed": changed_topology,
        "foreground_box_before_px": box_before, "foreground_box_after_px": box_after,
        "foreground_mm_before": size(box_before), "foreground_mm_after": size(box_after),
        "frame_pixels_before": int(_frame_mask(image, frame_hex).sum()), "frame_pixels_after": int(_frame_mask(after, frame_hex).sum()),
        "merged_with_adjacent_regions": 0,
    }
    return after, mask, selection, change


def composition_ops(log: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The fits of an edit log, in order (recolors and strokes do not move pixels)."""

    return [e["op"] for e in (log or {}).get("edits", []) if e["op"].get("op") == "fit_composition"]


def fit_preview(before: np.ndarray, after: np.ndarray, op: dict[str, Any], change: dict[str, Any],
                size: int = 420) -> np.ndarray:
    """Before | after side by side at the same scale, with the source box (cyan) and where it went (magenta)."""

    def tile(image: np.ndarray, box: list[int] | None, color: tuple[int, int, int]) -> np.ndarray:
        h, w = image.shape[:2]
        f = size / max(h, w)
        small = np.asarray(Image.fromarray(image).resize((max(1, round(w * f)), max(1, round(h * f))), Image.NEAREST)).copy()
        if box:
            r0, c0 = int(box[0] * f), int(box[1] * f)
            r1, c1 = min(small.shape[0] - 1, int(box[2] * f)), min(small.shape[1] - 1, int(box[3] * f))
            small[r0, c0:c1 + 1] = small[r1, c0:c1 + 1] = color
            small[r0:r1 + 1, c0] = small[r0:r1 + 1, c1] = color
        return np.pad(small, ((6, 6), (6, 6), (0, 0)), constant_values=255)

    return np.concatenate([tile(before, op["source_box_px"], (0, 170, 255)),
                           tile(after, change.get("dest_box_px"), (255, 0, 255))], axis=1)


# -- previews ------------------------------------------------------------------------------------
def highlight(image: np.ndarray, mask: np.ndarray, box: list[int] | None = None) -> np.ndarray:
    """Artwork with everything but the region dimmed and the region outlined in magenta."""

    out = (image.astype(np.float32) * 0.35 + 255 * 0.65).astype(np.uint8)
    out[mask] = image[mask]
    # Outline just outside the region, so thin regions (a necklace chain) keep their color visible.
    edge = ndimage.binary_dilation(mask, iterations=max(1, image.shape[0] // 512)) & ~mask
    out[edge] = (255, 0, 255)
    if box:
        r0, c0, r1, c1 = box
        out[r0, c0:c1] = out[r1 - 1, c0:c1] = (0, 160, 255)
        out[r0:r1, c0] = out[r0:r1, c1 - 1] = (0, 160, 255)
    return out


def before_after(before: np.ndarray, after: np.ndarray, mask: np.ndarray, pad: int = 24, size: int = 360) -> np.ndarray:
    """Zoomed crops around the region: before | after, outlined, on one image."""

    rows, cols = np.nonzero(mask)
    r0, r1 = max(0, rows.min() - pad), min(mask.shape[0], rows.max() + 1 + pad)
    c0, c1 = max(0, cols.min() - pad), min(mask.shape[1], cols.max() + 1 + pad)
    tiles = []
    for image in (before, after):
        crop = highlight(image, mask)[r0:r1, c0:c1] if image is before else image[r0:r1, c0:c1]
        tile = Image.fromarray(np.ascontiguousarray(crop))
        scale = size / max(tile.size)
        tile = tile.resize((max(1, round(tile.size[0] * scale)), max(1, round(tile.size[1] * scale))), Image.NEAREST)
        canvas = Image.new("RGB", (size + 8, size + 8), (255, 255, 255))
        canvas.paste(tile, (4, 4))
        tiles.append(np.asarray(canvas))
    return np.concatenate(tiles, axis=1)


# -- the edit log --------------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_log(directory: Path, log: dict[str, Any]) -> None:
    partial = directory / "edits.json.partial"
    partial.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    partial.replace(directory / "edits.json")


def read_log(directory: Path) -> dict[str, Any] | None:
    path = Path(directory) / "edits.json"
    if not path.is_file():
        return None
    log = json.loads(path.read_text(encoding="utf-8"))
    if log.get("schema") != ARTWORK_SCHEMA or log.get("version") != ARTWORK_VERSION:
        raise ArtworkError(f"{path} is not a {ARTWORK_SCHEMA} {ARTWORK_VERSION} log")
    return log


def start(directory: Path, base: np.ndarray, origin: dict[str, Any], frame_hex: str | None,
          mm_per_pixel: float) -> dict[str, Any]:
    """A new, empty log on a copy of ``base``."""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    save_rgb(base, directory / "base.png")
    save_rgb(base, directory / "current.png")
    log = {"schema": ARTWORK_SCHEMA, "version": ARTWORK_VERSION, "created_utc": _now(),
           "base": {"file": "base.png", "sha256": rgb_sha256(base), "origin": origin, "frame_color": frame_hex,
                    "mm_per_pixel": mm_per_pixel, "palette": palette_summary(base, frame_hex, mm_per_pixel)},
           "edits": [], "current_sha256": rgb_sha256(base)}
    _write_log(directory, log)
    return log


def _as_logged(op: dict[str, Any]) -> dict[str, Any]:
    """A saved op as it was applied: a stroke saved without ``stroke_raster`` predates the version and
    was drawn with version 1, so it replays with that geometry rather than the current default."""

    if op.get("op") in ("paint_stroke", "close_gap") and "stroke_raster" not in op:
        return {**op, "stroke_raster": 1}
    return op


def replay(directory: Path, log: dict[str, Any], count: int | None = None) -> np.ndarray:
    """``base.png`` with the first ``count`` edits (all by default), each checked against its record."""

    image = load_rgb(Path(directory) / "base.png")
    if rgb_sha256(image) != log["base"]["sha256"]:
        raise ArtworkError("artwork base.png does not match its recorded hash; the edit log cannot be trusted")
    frame, mpp = log["base"]["frame_color"], log["base"]["mm_per_pixel"]
    for entry in log["edits"][:count]:
        after, mask, selection, _ = plan_edit(image, check_op(_as_logged(entry["op"])), frame, mpp)
        if selection["color"] != entry["from"] or mask_sha256(mask) != entry["region_sha256"]:
            raise ArtworkError(f"artwork edit #{entry['index']} no longer selects the same pixels; the log does not replay")
        image = after
    return image


def _entry(image: np.ndarray, op: dict[str, Any], frame: str | None, mpp: float,
           index: int) -> tuple[np.ndarray, dict[str, Any]]:
    """(after, log entry) of one checked op on ``image``: what it replaced and the exact pixels it changed."""

    after, mask, selection, change = plan_edit(image, op, frame, mpp)
    entry = {"index": index, "op": op, "from": selection["color"],
             "from_colors": selection.get("colors", [selection["color"]]), "to": op["color"],
             "region_sha256": mask_sha256(mask), "selection": {**{k: selection[k] for k in (
                 "pixels", "area_mm2", "bbox_px", "regions_of_color", "limited_by_box", "whole_region_pixels")},
                 "regions": selection.get("regions", 1)},
             "change": change, "palette_after": palette_summary(after, frame, mpp), "applied_utc": _now()}
    return after, entry


def refuse_unapproved_clipping(entry: dict[str, Any]) -> None:
    """A new fit that pushes subject pixels off the canvas needs the user's explicit approval
    (``accept_clipping``).  Only new edits are checked; replaying a saved log never refuses."""

    change = entry["change"]
    if entry["op"]["op"] == "fit_composition" and change.get("subject_clipped_px") and not entry["op"].get("accept_clipping"):
        raise ArtworkError(f"this fit pushes {change['subject_clipped_px']} subject pixels off the canvas "
                           f"({', '.join(change['clipped_edges'])}); move or shrink the subject, or approve the loss")


def apply(directory: Path, op: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    """Apply one checked op to ``current.png`` and append it to the log (saved)."""

    directory = Path(directory)
    log = read_log(directory)
    if log is None:
        raise ArtworkError("no artwork edit log; preview an edit first")
    image = load_rgb(directory / "current.png")
    if rgb_sha256(image) != log["current_sha256"]:
        raise ArtworkError("current.png does not match the edit log; verify or undo to rebuild it")
    frame, mpp = log["base"]["frame_color"], log["base"]["mm_per_pixel"]
    after, entry = _entry(image, op, frame, mpp, len(log["edits"]) + 1)
    refuse_unapproved_clipping(entry)
    save_rgb(after, directory / "current.png")
    log["edits"].append(entry)
    log["current_sha256"] = rgb_sha256(after)
    _write_log(directory, log)
    return after, entry


def revert(directory: Path, keep: int) -> tuple[np.ndarray, dict[str, Any]]:
    """Keep the first ``keep`` edits (replayed from the base) and drop the rest."""

    directory = Path(directory)
    log = read_log(directory)
    if log is None:
        raise ArtworkError("no artwork edits to undo")
    if not (0 <= keep <= len(log["edits"])):
        raise ArtworkError(f"keep must be between 0 and {len(log['edits'])}")
    image = replay(directory, log, keep)
    log["edits"] = log["edits"][:keep]
    log["current_sha256"] = rgb_sha256(image)
    save_rgb(image, directory / "current.png")
    _write_log(directory, log)
    return image, log


def verify(directory: Path) -> dict[str, Any]:
    """Reload the saved log and replay it from the base; the result must equal ``current.png``."""

    directory = Path(directory)
    log = read_log(directory)
    if log is None:
        raise ArtworkError("no artwork edit log")
    saved = load_rgb(directory / "current.png")
    problems = []
    try:
        replayed = replay(directory, log)
    except ArtworkError as error:
        replayed, problems = None, [str(error)]
    identical = replayed is not None and np.array_equal(replayed, saved)
    if replayed is not None and not identical:
        problems.append(f"{int(np.any(replayed != saved, axis=2).sum())} pixels differ between the replay and current.png")
    if rgb_sha256(saved) != log["current_sha256"]:
        problems.append("current.png does not match the hash recorded in edits.json")
    return {"edits": len(log["edits"]), "replay_identical": identical and not problems, "problems": problems,
            "current_sha256": rgb_sha256(saved), "checked_utc": _now()}


# -- working session: unsaved edits on top of the saved artwork ------------------------------------
# Edits are made one after another in a session (each on the result of the previous one) and saved
# together.  The session is kept on disk, so it survives a reload or a restart (recovery), but it is
# not part of the edit log: nothing reads it as saved or planned artwork until Save appends its edits
# to ``edits.json`` (the same entries ``apply`` writes, replayed and checked again first).
SESSION_SCHEMA = "layerforge.artwork_session"
SESSION_VERSION = "1.0.0"
SESSION_DIR = "session"


def read_session(directory: Path) -> dict[str, Any] | None:
    path = Path(directory) / SESSION_DIR / "session.json"
    if not path.is_file():
        return None
    session = json.loads(path.read_text(encoding="utf-8"))
    if session.get("schema") != SESSION_SCHEMA or session.get("version") != SESSION_VERSION:
        raise ArtworkError(f"{path} is not a {SESSION_SCHEMA} {SESSION_VERSION} session")
    return session


def _write_session(directory: Path, session: dict[str, Any], image: np.ndarray) -> None:
    folder = Path(directory) / SESSION_DIR
    folder.mkdir(parents=True, exist_ok=True)
    save_rgb(image, folder / "current.png")
    session["current_sha256"] = rgb_sha256(image)
    session["updated_utc"] = _now()
    partial = folder / "session.json.partial"
    partial.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")
    partial.replace(folder / "session.json")


def session_stale(log: dict[str, Any], session: dict[str, Any]) -> bool:
    """True when the saved artwork changed under the session (its edits were made on other pixels)."""

    return session["base_sha256"] != log["current_sha256"]


def _session_replay(directory: Path, log: dict[str, Any], entries: list[dict[str, Any]]) -> np.ndarray:
    """The saved artwork with ``entries`` replayed in order, each checked against its record."""

    image = load_rgb(Path(directory) / "current.png")
    if rgb_sha256(image) != log["current_sha256"]:
        raise ArtworkError("current.png does not match the edit log; verify or undo to rebuild it")
    frame, mpp = log["base"]["frame_color"], log["base"]["mm_per_pixel"]
    for number, entry in enumerate(entries, 1):
        after, mask, selection, _ = plan_edit(image, check_op(entry["op"]), frame, mpp)
        if selection["color"] != entry["from"] or mask_sha256(mask) != entry["region_sha256"]:
            raise ArtworkError(f"unsaved change {number} no longer selects the same pixels; discard the unsaved changes")
        image = after
    return image


def session_image(directory: Path, log: dict[str, Any], session: dict[str, Any] | None) -> np.ndarray:
    """The working artwork: the session's result (rebuilt by replay if its file does not match), else
    the saved artwork."""

    directory = Path(directory)
    if session is None:
        return load_rgb(directory / "current.png")
    if session_stale(log, session):
        raise ArtworkError("the saved artwork changed after these unsaved changes were made; discard them")
    file = directory / SESSION_DIR / "current.png"
    if file.is_file():
        image = load_rgb(file)
        if rgb_sha256(image) == session["current_sha256"]:
            return image
    return _session_replay(directory, log, session["ops"])


def _session_or_new(directory: Path, log: dict[str, Any]) -> dict[str, Any]:
    session = read_session(directory)
    if session is not None:
        return session
    return {"schema": SESSION_SCHEMA, "version": SESSION_VERSION, "started_utc": _now(),
            "base_sha256": log["current_sha256"], "ops": [], "redo": [], "current_sha256": log["current_sha256"]}


def _log_or_error(directory: Path) -> dict[str, Any]:
    log = read_log(directory)
    if log is None:
        raise ArtworkError("no artwork edit log")
    return log


def session_add(directory: Path, op: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any], dict[str, Any]]:
    """Make one checked op on the working artwork (unsaved). Returns (image, entry, session)."""

    directory = Path(directory)
    log = _log_or_error(directory)
    session = _session_or_new(directory, log)
    image = session_image(directory, log, session)
    after, entry = _entry(image, op, log["base"]["frame_color"], log["base"]["mm_per_pixel"],
                          len(log["edits"]) + len(session["ops"]) + 1)
    refuse_unapproved_clipping(entry)
    session["ops"].append(entry)
    session["redo"] = []  # a new edit ends the redo chain
    _write_session(directory, session, after)
    return after, entry, session


def session_undo(directory: Path) -> tuple[np.ndarray, dict[str, Any]]:
    """Take back the last unsaved edit (it can be redone)."""

    directory = Path(directory)
    log = _log_or_error(directory)
    session = read_session(directory)
    if session is None or not session["ops"]:
        raise ArtworkError("there is no unsaved artwork change to undo")
    session_image(directory, log, session)  # refuses a stale session
    session["redo"].append(session["ops"].pop())
    image = _session_replay(directory, log, session["ops"])
    _write_session(directory, session, image)
    return image, session


def session_redo(directory: Path) -> tuple[np.ndarray, dict[str, Any]]:
    """Make the last undone unsaved edit again; it must change exactly the same pixels."""

    directory = Path(directory)
    log = _log_or_error(directory)
    session = read_session(directory)
    if session is None or not session["redo"]:
        raise ArtworkError("there is no undone artwork change to redo")
    image = session_image(directory, log, session)
    entry = session["redo"].pop()
    frame, mpp = log["base"]["frame_color"], log["base"]["mm_per_pixel"]
    after, mask, selection, _ = plan_edit(image, check_op(entry["op"]), frame, mpp)
    if selection["color"] != entry["from"] or mask_sha256(mask) != entry["region_sha256"]:
        raise ArtworkError("the undone change no longer selects the same pixels; make it again")
    session["ops"].append(entry)
    _write_session(directory, session, after)
    return after, session


def session_discard(directory: Path) -> int:
    """Drop the session (its unsaved edits and redo steps); the saved artwork is not touched."""

    import shutil

    directory = Path(directory)
    try:
        session = read_session(directory)
    except (ArtworkError, json.JSONDecodeError):
        session = None  # an unreadable session is dropped too
    shutil.rmtree(directory / SESSION_DIR, ignore_errors=True)
    return len(session["ops"]) if session else 0


def session_save(directory: Path) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Save every unsaved edit together: replayed and checked on the saved artwork, then appended to
    the log as ordinary entries (one batch id) with one write of ``current.png`` and ``edits.json``."""

    directory = Path(directory)
    log = _log_or_error(directory)
    session = read_session(directory)
    if session is None or not session["ops"]:
        raise ArtworkError("there are no unsaved artwork changes to save")
    if session_stale(log, session):
        raise ArtworkError("the saved artwork changed after these unsaved changes were made; discard them")
    image = _session_replay(directory, log, session["ops"])
    if rgb_sha256(image) != session["current_sha256"]:
        raise ArtworkError("the unsaved changes do not reproduce the working artwork; nothing was saved")
    saved_utc = _now()
    batch = f"save_{len({e['batch'] for e in log['edits'] if e.get('batch')}) + 1:03d}"
    entries = [{**entry, "index": len(log["edits"]) + n, "batch": batch, "saved_utc": saved_utc}
               for n, entry in enumerate(session["ops"], 1)]
    save_rgb(image, directory / "current.png")
    log["edits"].extend(entries)
    log["current_sha256"] = rgb_sha256(image)
    _write_log(directory, log)
    session_discard(directory)
    return image, entries
