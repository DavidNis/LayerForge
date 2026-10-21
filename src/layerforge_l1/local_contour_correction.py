"""Bounded correction of explicitly reviewed contour paths.

This module is intentionally not a detector or global preprocessing pipeline.
It matches one existing skeleton path to a reviewed bounding box, re-strokes
that complete path at a specified physical width, and permits changes only in a
small padded neighborhood of the reviewed contour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .contour_normalization import trace_skeleton_segments, zhang_suen_thinning


Pixel = tuple[int, int]


@dataclass(frozen=True)
class LocalContourCorrectionConfig:
    print_width_mm: float = 200.0
    print_height_mm: float = 200.0
    minimum_width_mm: float = 1.2
    internal_scale: int = 4
    contour_color: tuple[int, int, int] = (14, 15, 13)
    allowed_padding_pixels: int = 5


def _path_bounds(path: list[Pixel]) -> tuple[int, int, int, int]:
    ys = [point[0] for point in path]
    xs = [point[1] for point in path]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def _intersection_over_union(
    left: tuple[int, int, int, int], right: tuple[int, int, int, int]
) -> float:
    lx0, ly0, lx1, ly1 = left
    rx0, ry0, rx1, ry1 = right
    intersection = max(0, min(lx1, rx1) - max(lx0, rx0)) * max(
        0, min(ly1, ry1) - max(ly0, ry0)
    )
    union = (lx1 - lx0) * (ly1 - ly0) + (rx1 - rx0) * (ry1 - ry0) - intersection
    return intersection / max(1, union)


def match_reviewed_path(
    contour_mask: np.ndarray,
    expected_bounds: tuple[int, int, int, int],
) -> tuple[list[Pixel], bool, tuple[int, int], dict[str, Any]]:
    """Match one current centerline path to an already reviewed bounding box."""

    skeleton = zhang_suen_thinning(contour_mask)
    paths = trace_skeleton_segments(skeleton)
    x0, y0, x1, y1 = expected_bounds
    ranked: list[
        tuple[float, float, float, list[Pixel], bool, tuple[int, int], tuple[int, int, int, int]]
    ] = []
    for path, closed, endpoint_degrees in paths:
        bounds = _path_bounds(path)
        inside = sum(x0 <= x < x1 and y0 <= y < y1 for y, x in path) / max(
            1, len(path)
        )
        iou = _intersection_over_union(bounds, expected_bounds)
        if inside == 0 or iou == 0:
            continue
        score = (2.0 * inside) + iou
        ranked.append(
            (score, inside, iou, path, closed, endpoint_degrees, bounds)
        )
    if not ranked:
        raise ValueError(f"no contour path intersects reviewed bounds {expected_bounds}")
    ranked.sort(key=lambda item: (-item[0], -len(item[3]), item[6]))
    score, inside, iou, path, closed, endpoint_degrees, bounds = ranked[0]
    if inside < 0.70 or iou < 0.02:
        raise ValueError(
            "reviewed contour match is not sufficiently specific: "
            f"inside={inside:.3f}, iou={iou:.3f}, bounds={bounds}"
        )
    return path, closed, endpoint_degrees, {
        "matched_path_bounds_pixels": list(bounds),
        "expected_bounds_pixels": list(expected_bounds),
        "path_point_count": len(path),
        "path_closed": closed,
        "endpoint_degrees": list(endpoint_degrees),
        "match_inside_fraction": inside,
        "match_bbox_iou": iou,
        "match_score": score,
        "candidate_paths_considered": len(ranked),
    }


def _distance_high(mask: np.ndarray, scale: int) -> np.ndarray:
    enlarged = cv2.resize(
        mask.astype(np.uint8),
        (mask.shape[1] * scale, mask.shape[0] * scale),
        interpolation=cv2.INTER_NEAREST,
    )
    return cv2.distanceTransform(enlarged, cv2.DIST_L2, 5)


def _widths_mm(
    distance_high: np.ndarray,
    path: list[Pixel],
    mm_per_pixel: float,
    scale: int,
) -> np.ndarray:
    widths: list[float] = []
    for y, x in path:
        block = distance_high[
            y * scale : (y + 1) * scale,
            x * scale : (x + 1) * scale,
        ]
        widths.append(
            2.0 * float(block.max(initial=0.0)) * mm_per_pixel / scale
        )
    return np.asarray(widths, dtype=np.float64)


def _summarize_widths(widths: np.ndarray) -> dict[str, float]:
    return {
        "minimum_mm": float(widths.min()),
        "p10_mm": float(np.percentile(widths, 10)),
        "median_mm": float(np.median(widths)),
        "p90_mm": float(np.percentile(widths, 90)),
        "maximum_mm": float(widths.max()),
    }


def _render_path(
    path: list[Pixel],
    closed: bool,
    shape: tuple[int, int],
    target_width_mm: float,
    config: LocalContourCorrectionConfig,
) -> np.ndarray:
    height, width = shape
    scale = config.internal_scale
    mm_per_high_x = config.print_width_mm / (width * scale)
    mm_per_high_y = config.print_height_mm / (height * scale)
    if abs(mm_per_high_x - mm_per_high_y) > 1e-9:
        raise ValueError("anisotropic pixels are not supported")
    thickness = max(1, int(np.ceil(target_width_mm / mm_per_high_x)))
    canvas = np.zeros((height * scale, width * scale), dtype=np.uint8)
    points = np.asarray(
        [[x * scale + scale // 2, y * scale + scale // 2] for y, x in path],
        dtype=np.int32,
    ).reshape((-1, 1, 2))
    cv2.polylines(
        canvas,
        [points],
        closed,
        1,
        thickness=thickness,
        lineType=cv2.LINE_8,
    )
    if not closed:
        radius = max(1, thickness // 2)
        cv2.circle(canvas, tuple(points[0, 0]), radius, 1, thickness=-1)
        cv2.circle(canvas, tuple(points[-1, 0]), radius, 1, thickness=-1)
    coverage = canvas.reshape(height, scale, width, scale).mean(axis=(1, 3))
    return coverage >= 0.5


def correct_reviewed_contour(
    image: np.ndarray,
    expected_bounds: tuple[int, int, int, int],
    intended_local_width_mm: float,
    config: LocalContourCorrectionConfig = LocalContourCorrectionConfig(),
) -> tuple[np.ndarray, dict[str, Any], np.ndarray]:
    """Correct one reviewed contour without touching unrelated pixels."""

    height, width = image.shape[:2]
    mm_per_x = config.print_width_mm / width
    mm_per_y = config.print_height_mm / height
    if abs(mm_per_x - mm_per_y) > 1e-9:
        raise ValueError("pixels must have equal physical dimensions")
    contour_mask = np.all(image == config.contour_color, axis=2)
    path, closed, endpoint_degrees, match = match_reviewed_path(
        contour_mask, expected_bounds
    )
    before_widths = _widths_mm(
        _distance_high(contour_mask, config.internal_scale),
        path,
        mm_per_x,
        config.internal_scale,
    )
    target_width_mm = max(config.minimum_width_mm, intended_local_width_mm)
    # One high-resolution pixel on each side is a rasterization allowance, not
    # a relaxation of the requested minimum.  It prevents a mathematically
    # 1.2 mm stroke from rasterizing just below 1.2 mm on diagonal sections.
    mm_per_high = mm_per_x / config.internal_scale
    rasterized_stroke_width_mm = target_width_mm + (2.0 * mm_per_high)
    proposed = _render_path(
        path, closed, contour_mask.shape, rasterized_stroke_width_mm, config
    )
    x0, y0, x1, y1 = expected_bounds
    padding = config.allowed_padding_pixels
    allowed = np.zeros(contour_mask.shape, dtype=bool)
    allowed[
        max(0, y0 - padding) : min(height, y1 + padding),
        max(0, x0 - padding) : min(width, x1 + padding),
    ] = True
    additions = proposed & ~contour_mask & allowed
    corrected_mask = contour_mask | additions
    result = image.copy()
    result[additions] = np.asarray(config.contour_color, dtype=np.uint8)
    after_widths = _widths_mm(
        _distance_high(corrected_mask, config.internal_scale),
        path,
        mm_per_x,
        config.internal_scale,
    )
    outside_allowed_changed = np.any(result != image, axis=2) & ~allowed
    report = {
        **match,
        "intended_local_width_mm": intended_local_width_mm,
        "minimum_required_width_mm": config.minimum_width_mm,
        "target_width_mm": target_width_mm,
        "rasterized_stroke_width_mm": rasterized_stroke_width_mm,
        "mm_per_pixel": mm_per_x,
        "before": _summarize_widths(before_widths),
        "after": _summarize_widths(after_widths),
        "changed_pixels": int(np.count_nonzero(additions)),
        "all_changes_add_existing_contour_color": bool(
            np.all(result[additions] == np.asarray(config.contour_color))
        ),
        "outside_reviewed_neighborhood_changes": int(
            np.count_nonzero(outside_allowed_changed)
        ),
        "minimum_width_pass": float(after_widths.min()) >= config.minimum_width_mm,
        "centerline_preserved": True,
        "method": (
            "match reviewed skeleton path, re-stroke the complete path on a 4x "
            "grid, and clip additions to the reviewed padded neighborhood"
        ),
    }
    return result, report, additions

