"""Uniform-width reconstruction for existing indexed contour artwork.

Unlike structural-neck repair, this module works from complete visible
centerline paths.  It redraws each coherent contour at its own stable nominal
width (never below the configured physical minimum), then adds only the pixels
needed to remove local narrowing.  It never dilates an entire color component.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np

from .contour_normalization import trace_skeleton_segments, zhang_suen_thinning


Pixel = tuple[int, int]


@dataclass(frozen=True)
class ContourConsistencyConfig:
    print_width_mm: float = 200.0
    print_height_mm: float = 200.0
    minimum_width_mm: float = 1.2
    internal_scale: int = 4
    minimum_path_pixels: int = 6
    endpoint_guard_pixels: int = 2
    contour_color: tuple[int, int, int] = (14, 15, 13)


def _distance(mask: np.ndarray, scale: int) -> np.ndarray:
    high = cv2.resize(
        mask.astype(np.uint8),
        (mask.shape[1] * scale, mask.shape[0] * scale),
        interpolation=cv2.INTER_NEAREST,
    )
    return cv2.distanceTransform(high, cv2.DIST_L2, 5)


def _widths(
    distance: np.ndarray,
    path: list[Pixel],
    mm_per_pixel: float,
    scale: int,
) -> np.ndarray:
    values: list[float] = []
    for y, x in path:
        block = distance[y * scale : (y + 1) * scale, x * scale : (x + 1) * scale]
        values.append(2.0 * float(block.max(initial=0.0)) * mm_per_pixel / scale)
    return np.asarray(values, dtype=np.float64)


def _draw_path(
    path: list[Pixel],
    closed: bool,
    shape: tuple[int, int],
    width_mm: float,
    config: ContourConsistencyConfig,
) -> np.ndarray:
    height, width = shape
    scale = config.internal_scale
    mm_per_high_x = config.print_width_mm / (width * scale)
    mm_per_high_y = config.print_height_mm / (height * scale)
    if abs(mm_per_high_x - mm_per_high_y) > 1e-9:
        raise ValueError("anisotropic contour pixels are not supported")
    # One high-resolution pixel of allowance on both sides avoids a 1.2 mm
    # centerline rasterizing just below the requirement on curves/diagonals.
    raster_width_mm = width_mm + (2.0 * mm_per_high_x)
    thickness = max(1, int(np.ceil(raster_width_mm / mm_per_high_x)))
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


def normalize_contour_consistency(
    image: np.ndarray,
    config: ContourConsistencyConfig = ContourConsistencyConfig(),
) -> tuple[np.ndarray, dict[str, Any], np.ndarray]:
    """Return a contour-consistent image, report, and added contour pixels."""

    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be HxWx3 RGB")
    height, width = image.shape[:2]
    mm_per_x = config.print_width_mm / width
    mm_per_y = config.print_height_mm / height
    if abs(mm_per_x - mm_per_y) > 1e-9:
        raise ValueError("anisotropic contour pixels are not supported")

    color = np.asarray(config.contour_color, dtype=np.uint8)
    original = np.all(image == color, axis=2)
    skeleton = zhang_suen_thinning(original)
    paths = trace_skeleton_segments(skeleton)
    before_distance = _distance(original, config.internal_scale)
    component_count, component_labels = cv2.connectedComponents(
        original.astype(np.uint8), connectivity=8
    )
    candidate = original.copy()
    rows: list[dict[str, Any]] = []

    for path_id, (path, closed, endpoint_degrees) in enumerate(paths):
        if len(path) < config.minimum_path_pixels:
            continue
        guard = min(config.endpoint_guard_pixels, max(0, (len(path) - 1) // 4))
        core = path[guard:-guard] if guard and len(path) > 2 * guard else path
        values = _widths(
            before_distance,
            core,
            mm_per_x,
            config.internal_scale,
        )
        intended = max(config.minimum_width_mm, float(np.median(values)))
        proposed = _draw_path(path, closed, original.shape, intended, config)
        additions = proposed & ~candidate

        source_ids = [int(component_labels[y, x]) for y, x in path]
        source_id = max(set(source_ids), key=source_ids.count)
        if np.any(additions):
            contact = cv2.dilate(
                additions.astype(np.uint8), np.ones((3, 3), np.uint8)
            ).astype(bool)
            foreign = (component_labels > 0) & (component_labels != source_id)
            # Clip only the pixels that would close a deliberate gap to another
            # original contour component; do not reject the complete stroke.
            unsafe = cv2.dilate(foreign.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
            additions &= ~unsafe
        candidate |= additions
        rows.append(
            {
                "path_id": path_id,
                "component_id": source_id,
                "closed": closed,
                "endpoint_degrees": list(endpoint_degrees),
                "point_count": len(path),
                "bounds_pixels": [
                    min(x for y, x in path),
                    min(y for y, x in path),
                    max(x for y, x in path) + 1,
                    max(y for y, x in path) + 1,
                ],
                "before_min_mm": float(values.min()),
                "before_median_mm": float(np.median(values)),
                "before_p90_mm": float(np.percentile(values, 90)),
                "target_width_mm": intended,
                "added_pixels": int(np.count_nonzero(additions)),
            }
        )

    result = image.copy()
    additions = candidate & ~original
    result[additions] = color
    after_distance = _distance(candidate, config.internal_scale)
    unresolved = 0
    for row in rows:
        path, _, _ = paths[int(row["path_id"])]
        guard = min(config.endpoint_guard_pixels, max(0, (len(path) - 1) // 4))
        core = path[guard:-guard] if guard and len(path) > 2 * guard else path
        values = _widths(after_distance, core, mm_per_x, config.internal_scale)
        row["after_min_mm"] = float(values.min())
        row["after_p10_mm"] = float(np.percentile(values, 10))
        row["after_median_mm"] = float(np.median(values))
        row["after_p90_mm"] = float(np.percentile(values, 90))
        row["minimum_pass"] = row["after_p10_mm"] >= config.minimum_width_mm
        row["width_spread_mm"] = row["after_p90_mm"] - row["after_p10_mm"]
        unresolved += not row["minimum_pass"]

    before_palette = set(map(tuple, np.unique(image.reshape(-1, 3), axis=0)))
    after_palette = set(map(tuple, np.unique(result.reshape(-1, 3), axis=0)))
    report = {
        "config": asdict(config),
        "mm_per_pixel": mm_per_x,
        "required_native_pixels": config.minimum_width_mm / mm_per_x,
        "original_contour_components": component_count - 1,
        "centerline_paths_found": len(paths),
        "paths_processed": len(rows),
        "paths_with_added_pixels": sum(row["added_pixels"] > 0 for row in rows),
        "added_contour_pixels": int(np.count_nonzero(additions)),
        "unresolved_paths": int(unresolved),
        "palette_preserved": before_palette == after_palette,
        "method": (
            "skeletonize existing contour components, estimate each path's stable median "
            "width, and redraw that full path at max(1.2 mm, median) on a 4x grid"
        ),
        "paths": rows,
    }
    return result, report, additions

