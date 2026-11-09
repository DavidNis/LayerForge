"""Simplify tiny tips: a conservative suggestion to shorten narrow pointed tips of color regions.

Input: the artwork and the places where the printability check of the current sheets reported a cutout
narrower than the process minimum *because of the image* (an issue's ``feature.cause == "image"``; the
revealed color region is that narrow in the artwork).  Only there (a window around each place) the part
of the revealed color's region that a morphological opening with a disc of the minimum width removes is
a candidate - the part narrower than the minimum.  A candidate becomes a proposed change only when it is

* a TIP: attached to a wider part of the same region (never a whole thin line);
* small: at least ``MIN_TIP_PX`` (smaller ones are stair-steps of an edge; removing them notches it)
  and at most ``MAX_TIP_MM2``; never part of a small shape (under ``SMALL_SHAPE_MM2``: pupils, small eyes,
  catchlights, freckles) and never a large part of its shape;
* surrounded by ONE other color (at least ``DOMINANT`` of its outside border): that color fills it.  A tip
  between two other colors (a three-color junction) is left unchanged - which color should extend is the
  user's choice;
* clear of every other proposed tip (each is computed on the unchanged artwork, so any of them can be
  excluded independently);
* safe: in a window around it, filling it creates no new region, removes none, and leaves no new part
  narrower than the minimum, and it never removes a color from the palette.

Nothing here is applied: the result is an op (``simplify_tips``, exact pixels per tip) that the artwork
edit log applies, checks and replays like any other edit.  Deterministic; no AI, blur or quantization.
"""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np
from scipy import ndimage

from .model import hex_color

DOMINANT = 0.85          # share of a tip's outside border that must be one color
MIN_TIP_PX = 3           # smaller: a stair-step of the edge, not a tip
MAX_TIP_MM2 = 1.8        # larger: not a "tiny" tip
SMALL_REGION_RATIO = 4   # a tip may be at most 1/4 of its shape
SMALL_SHAPE_MM2 = 10.0   # shapes smaller than this (pupils, small eyes, catchlights, freckles) are never changed
FOUR = ndimage.generate_binary_structure(2, 1)
EIGHT = np.ones((3, 3), bool)
RISKY_KINDS = ("narrow_slot_or_gap", "opening_may_close")


def _rgb(hex_text: str) -> np.ndarray:
    value = hex_text.lstrip("#")
    return np.array([int(value[i:i + 2], 16) for i in (0, 2, 4)], np.uint8)


def disc(min_width_mm: float, mm_per_pixel: float) -> np.ndarray:
    """The opening's structuring element: the smallest odd disc at least the minimum width wide."""

    size = max(3, math.ceil(min_width_mm / mm_per_pixel - 1e-9))
    size += 1 - size % 2
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def _narrow(mask: np.ndarray, element: np.ndarray) -> np.ndarray:
    """Pixels of ``mask`` in parts narrower than ``element`` (what an opening removes)."""

    return mask & ~cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, element).astype(bool)


def sites_from_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One site per issue group whose narrow cutout is caused by the image: its artwork box (union over
    the group's sheets), the narrow revealed colors, the issue ids and the process minimum width."""

    out: dict[str, dict[str, Any]] = {}
    for issue in issues:
        feature = issue.get("feature") or {}
        box = issue.get("artwork_box_px")
        if issue.get("kind") not in RISKY_KINDS or feature.get("cause") != "image" or not box:
            continue
        colors = [r["color"] for r in feature.get("regions", []) if r.get("narrow")]
        if not colors:
            continue
        site = out.setdefault(issue.get("group") or issue["id"], {"box": list(box), "colors": set(), "issues": [],
                                                                   "min_width_mm": feature.get("threshold_mm") or (issue.get("threshold") or {}).get("value")})
        site["box"] = [min(site["box"][0], box[0]), min(site["box"][1], box[1]), max(site["box"][2], box[2]), max(site["box"][3], box[3])]
        site["colors"].update(colors)
        site["issues"].append(issue["id"])
    return [{**s, "colors": sorted(s["colors"])} for s in sorted(out.values(), key=lambda s: (s["box"][0], s["box"][1]))]


def _window(box: list[int], pad: int, shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    return (max(box[0] - pad, 0), max(box[1] - pad, 0), min(box[2] + pad, shape[0]), min(box[3] + pad, shape[1]))


def _safe(image: np.ndarray, after: np.ndarray, box: list[int], element: np.ndarray) -> str | None:
    """Why the change around ``box`` is not safe (None: safe): a region added or removed, or a new narrow
    part.  Computed on a crop one disc wider than the area compared, so the crop's own edge (which cuts
    regions) never counts as a change."""

    reach = element.shape[0]
    inner = _window(box, reach + 3, image.shape)
    r0, c0, r1, c1 = _window(list(inner), reach, image.shape)
    before, now = image[r0:r1, c0:c1], after[r0:r1, c0:c1]
    keep = (slice(inner[0] - r0, inner[2] - r0), slice(inner[1] - c0, inner[3] - c0))
    for color in np.unique(np.concatenate([before.reshape(-1, 3), now.reshape(-1, 3)]), axis=0):
        b, a = np.all(before == color, axis=2), np.all(now == color, axis=2)
        if ndimage.label(b, structure=FOUR)[1] != ndimage.label(a, structure=FOUR)[1]:
            return "it would split, merge or remove a region"
        if (_narrow(a, element) & ~_narrow(b, element))[keep].any():
            return "it would leave a new narrow part"
    return None


def suggest(image: np.ndarray, sites: list[dict[str, Any]], mm_per_pixel: float, frame_hex: str | None = None,
            min_width_mm: float = 0.8) -> dict[str, Any]:
    """The proposed tips (each with its exact pixels and fill color) and the candidates left unchanged."""

    element = disc(min_width_mm, mm_per_pixel)
    reach = element.shape[0]
    pad = 2 * reach                     # window around a reported place
    context = reach + 3                 # extra rows so the opening and the checks are exact at the edges
    max_px = max(MIN_TIP_PX, int(MAX_TIP_MM2 / mm_per_pixel**2))
    frame = frame_hex.upper() if frame_hex else None
    found: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for number, site in enumerate(sites, 1):
        w = _window(site["box"], pad, image.shape)
        e = _window(list(w), context, image.shape)
        crop = image[e[0]:e[2], e[1]:e[3]]
        inside = np.zeros(crop.shape[:2], bool)
        inside[w[0] - e[0]:w[2] - e[0], w[1] - e[1]:w[3] - e[1]] = True
        for color in site["colors"]:
            if color == frame:
                continue
            mask = np.all(crop == _rgb(color), axis=2)
            opened = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, element).astype(bool)
            labels, count = ndimage.label(mask & ~opened & inside, structure=EIGHT)
            regions = ndimage.label(mask, structure=FOUR)[0]
            for k in range(1, count + 1):
                part = labels == k
                rows, cols = np.nonzero(part)
                anchor = (int(rows[0]) + e[0], int(cols[0]) + e[1])
                if anchor in seen:  # the same tip seen from an overlapping site
                    continue
                seen.add(anchor)
                px = int(part.sum())
                box = [int(rows.min()) + e[0], int(cols.min()) + e[1], int(rows.max()) + 1 + e[0], int(cols.max()) + 1 + e[1]]
                tip = {"site": number, "issues": site["issues"], "color": color, "pixels_count": px,
                       "area_mm2": round(px * mm_per_pixel**2, 3), "box": box}
                ring = ndimage.binary_dilation(part, structure=FOUR) & ~part
                outside = ring & ~mask
                why = None
                if (ndimage.binary_dilation(part, structure=EIGHT) & ~inside).any():
                    why = "cut by the search window, not a whole tip"
                elif px < MIN_TIP_PX:
                    why = "a stair-step of the edge, not a tip"
                elif px > max_px:
                    why = "larger than a tiny tip"
                elif not (ndimage.binary_dilation(part, structure=EIGHT) & opened).any():
                    why = "a whole thin feature, not a tip of a wider shape"
                else:
                    region = regions == regions[rows[0], cols[0]]
                    touches_edge = region[0].any() or region[-1].any() or region[:, 0].any() or region[:, -1].any()
                    area = int(region.sum())  # the whole shape when it lies inside the crop
                    if not touches_edge and (area * mm_per_pixel**2 < SMALL_SHAPE_MM2 or px * SMALL_REGION_RATIO > area):
                        why = "part of a small shape (kept as a detail)"
                fill = None
                if why is None:
                    colors, counts = np.unique(crop[outside].reshape(-1, 3), axis=0, return_counts=True)
                    best = int(np.argmax(counts))
                    fill = hex_color(tuple(int(v) for v in colors[best]))
                    if counts[best] < DOMINANT * counts.sum():
                        why = "between two other colors: which one should extend is your choice"
                    elif fill == frame:
                        why = "next to the frame padding"
                if why:
                    kept.append({**tip, "why_kept": why})
                    continue
                found.append({**tip, "fill": fill, "pixels": [[int(r) + e[0], int(c) + e[1]] for r, c in zip(rows, cols)]})
    # Each tip alone: clear of the others, safe in its surroundings, and never the last pixels of a color.
    occupied = np.zeros(image.shape[:2], np.int32)
    for n, tip in enumerate(found, 1):
        for r, c in tip["pixels"]:
            occupied[r, c] = n
    tips = []
    work = image.copy()
    for n, tip in enumerate(found, 1):
        r0, c0, r1, c1 = _window(tip["box"], 2, image.shape)
        near = set(np.unique(occupied[r0:r1, c0:c1])) - {0, n}
        why = "next to another proposed tip" if near else None
        if why is None:  # this tip alone on the unchanged artwork (set, checked, restored)
            rows, cols = np.array(tip["pixels"]).T
            work[rows, cols] = _rgb(tip["fill"])
            why = _safe(image, work, tip["box"], element)
            work[rows, cols] = image[rows, cols]
        if why:
            kept.append({k: v for k, v in tip.items() if k not in ("pixels", "fill")} | {"why_kept": why})
            continue
        tips.append(tip)
    for n, tip in enumerate(tips, 1):
        tip["id"] = f"t{n}"
    return {"tips": tips, "kept": kept, "min_width_mm": min_width_mm, "disc_px": int(reach),
            "sites": len(sites)}


def check_selection(image: np.ndarray, tips: list[dict[str, Any]], mm_per_pixel: float,
                    min_width_mm: float = 0.8) -> str | None:
    """Why the chosen tips together are not safe (None: safe).  Tips are chosen from one suggestion, so
    they are apart; this re-checks their surroundings with all of them applied, and the palette."""

    element = disc(min_width_mm, mm_per_pixel)
    after = image.copy()
    for tip in tips:
        for r, c in tip["pixels"]:
            after[r, c] = _rgb(tip["fill"])
    for tip in tips:
        why = _safe(image, after, tip["box"], element)
        if why:
            return f"tip {tip.get('id', '')}: {why}"
    packed = (after[..., 0].astype(np.uint32) << 16) | (after[..., 1].astype(np.uint32) << 8) | after[..., 2]
    for color in {t["color"] for t in tips}:
        r, g, b = (int(v) for v in _rgb(color))
        if not (packed == ((r << 16) | (g << 8) | b)).any():
            return f"{color} would disappear from the palette"
    return None
