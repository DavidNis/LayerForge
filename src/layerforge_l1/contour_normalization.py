"""Deterministic normalization of visible indexed-art contour strokes.

This module operates on the approved visual target, before physical-layer
planning.  It deliberately does not inspect or mutate retained-material masks.
The input palette is preserved: normalization can only replace source pixels
with the already-present contour color.

The implementation is conservative.  It thins the contour mask to a
centerline, splits that centerline at endpoints and junctions, and widens a
complete graph segment only when representative interior samples are below the
configured physical minimum.  Filled dark regions and unsafe proposals that
would join distinct original dark components are left unchanged and reported.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import cv2
import numpy as np


Pixel = tuple[int, int]


@dataclass(frozen=True)
class ContourNormalizationConfig:
    print_width_mm: float = 200.0
    print_height_mm: float = 200.0
    minimum_contour_width_mm: float = 1.2
    internal_scale: int = 4
    minimum_segment_length_pixels: int = 6
    thin_fraction_trigger: float = 0.10
    endpoint_guard_pixels: int = 2
    contour_color: tuple[int, int, int] = (14, 15, 13)


@dataclass(frozen=True)
class ContourSegment:
    segment_id: int
    component_id: int
    points: tuple[Pixel, ...]
    closed: bool
    endpoint_degrees: tuple[int, int]
    topology: str
    adjacent_colors: tuple[str, ...]
    before_min_mm: float
    before_p10_mm: float
    before_median_mm: float
    before_p90_mm: float
    intended_nominal_mm: float
    thin_fraction: float
    requires_normalization: bool
    disposition: str
    reason: str


def _neighbors(pixel: Pixel, shape: tuple[int, int]) -> list[Pixel]:
    y, x = pixel
    height, width = shape
    result: list[Pixel] = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if not (dy or dx):
                continue
            yy, xx = y + dy, x + dx
            if 0 <= yy < height and 0 <= xx < width:
                result.append((yy, xx))
    return result


def zhang_suen_thinning(mask: np.ndarray) -> np.ndarray:
    """Return a one-pixel 8-connected skeleton using Zhang-Suen thinning."""

    image = mask.astype(np.uint8, copy=True)
    if image.ndim != 2:
        raise ValueError("mask must be two-dimensional")
    image[[0, -1], :] = 0
    image[:, [0, -1]] = 0

    while True:
        changed = False
        for first_pass in (True, False):
            padded = np.pad(image, 1, mode="constant")
            p2 = padded[:-2, 1:-1]
            p3 = padded[:-2, 2:]
            p4 = padded[1:-1, 2:]
            p5 = padded[2:, 2:]
            p6 = padded[2:, 1:-1]
            p7 = padded[2:, :-2]
            p8 = padded[1:-1, :-2]
            p9 = padded[:-2, :-2]
            neighbours = (p2, p3, p4, p5, p6, p7, p8, p9)
            count = sum(neighbours)
            transitions = sum(
                ((neighbours[index] == 0) & (neighbours[(index + 1) % 8] == 1))
                for index in range(8)
            )
            if first_pass:
                guard_a = p2 * p4 * p6 == 0
                guard_b = p4 * p6 * p8 == 0
            else:
                guard_a = p2 * p4 * p8 == 0
                guard_b = p2 * p6 * p8 == 0
            remove = (
                (image == 1)
                & (count >= 2)
                & (count <= 6)
                & (transitions == 1)
                & guard_a
                & guard_b
            )
            if np.any(remove):
                image[remove] = 0
                changed = True
        if not changed:
            break
    return image.astype(bool)


def _edge_key(left: Pixel, right: Pixel) -> tuple[Pixel, Pixel]:
    return (left, right) if left <= right else (right, left)


def trace_skeleton_segments(skeleton: np.ndarray) -> list[tuple[list[Pixel], bool, tuple[int, int]]]:
    """Split an 8-connected skeleton at all endpoints and junctions."""

    points = {
        (int(value[0]), int(value[1])) for value in np.argwhere(skeleton)
    }
    adjacency: dict[Pixel, list[Pixel]] = {}
    for point in points:
        linked: list[Pixel] = []
        y, x = point
        for candidate in _neighbors(point, skeleton.shape):
            if candidate not in points:
                continue
            yy, xx = candidate
            if yy != y and xx != x:
                # A diagonal edge is redundant when the same local movement is
                # already represented by an orthogonal skeleton pixel.  Removing
                # that lattice shortcut prevents curved one-pixel paths from
                # being falsely split into many junctions.
                if (y, xx) in points or (yy, x) in points:
                    continue
            linked.append(candidate)
        adjacency[point] = linked
    nodes = sorted(point for point, linked in adjacency.items() if len(linked) != 2)
    visited: set[tuple[Pixel, Pixel]] = set()
    paths: list[tuple[list[Pixel], bool, tuple[int, int]]] = []

    def walk(start: Pixel, second: Pixel) -> list[Pixel]:
        path = [start, second]
        previous, current = start, second
        visited.add(_edge_key(previous, current))
        while len(adjacency[current]) == 2:
            following = adjacency[current][0] if adjacency[current][1] == previous else adjacency[current][1]
            edge = _edge_key(current, following)
            if edge in visited:
                break
            path.append(following)
            visited.add(edge)
            previous, current = current, following
        return path

    for node in nodes:
        for linked in sorted(adjacency[node]):
            if _edge_key(node, linked) in visited:
                continue
            path = walk(node, linked)
            paths.append((path, False, (len(adjacency[path[0]]), len(adjacency[path[-1]]))))

    # Every unvisited edge belongs to a closed cycle or to a rare residual
    # created by a one-pixel skeleton lattice.  Trace it deterministically.
    for start in sorted(points):
        for linked in sorted(adjacency[start]):
            if _edge_key(start, linked) in visited:
                continue
            path = [start, linked]
            visited.add(_edge_key(start, linked))
            previous, current = start, linked
            while True:
                options = [
                    candidate
                    for candidate in adjacency[current]
                    if candidate != previous and _edge_key(current, candidate) not in visited
                ]
                if not options:
                    break
                following = sorted(options)[0]
                path.append(following)
                visited.add(_edge_key(current, following))
                previous, current = current, following
                if current == start:
                    break
            closed = path[-1] == path[0]
            paths.append((path, closed, (len(adjacency[path[0]]), len(adjacency[path[-1]]))))
    return paths


def _component_is_filled(stats_row: np.ndarray, area: int) -> bool:
    width = int(stats_row[cv2.CC_STAT_WIDTH])
    height = int(stats_row[cv2.CC_STAT_HEIGHT])
    fill_ratio = area / max(1, width * height)
    aspect = max(width, height) / max(1, min(width, height))
    # Eyes and small solid facial marks are regions, not contour strokes.  An
    # elongated mouth remains eligible even when its fill ratio is moderately
    # high.
    return (fill_ratio >= 0.45 and aspect < 2.5) or (
        area < 100 and fill_ratio >= 0.35 and aspect < 2.5
    )


def _high_resolution_distance(mask: np.ndarray, scale: int) -> np.ndarray:
    enlarged = cv2.resize(
        mask.astype(np.uint8),
        (mask.shape[1] * scale, mask.shape[0] * scale),
        interpolation=cv2.INTER_NEAREST,
    )
    return cv2.distanceTransform(enlarged, cv2.DIST_L2, 5)


def _effective_widths(
    distance_high: np.ndarray,
    points: Iterable[Pixel],
    mm_per_pixel: float,
    scale: int,
) -> np.ndarray:
    """Measure raster-square width at centerline samples on a finer grid.

    Sampling the maximum distance in the native pixel's high-resolution block
    avoids the odd/even bias of ``2*d-1`` on the native grid and treats curved
    and diagonal strokes consistently.
    """

    values: list[float] = []
    for y, x in points:
        block = distance_high[y * scale : (y + 1) * scale, x * scale : (x + 1) * scale]
        values.append(float(block.max(initial=0.0)))
    return 2.0 * np.asarray(values, dtype=np.float32) * (mm_per_pixel / scale)


def _hex(color: Iterable[int]) -> str:
    return "#" + "".join(f"{int(value):02X}" for value in color)


def _adjacent_colors(image: np.ndarray, path: list[Pixel], contour_color: tuple[int, int, int]) -> tuple[str, ...]:
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    for y, x in path:
        mask[y, x] = 1
    ring = cv2.dilate(mask, np.ones((5, 5), dtype=np.uint8)) > 0
    ring &= mask == 0
    pixels = image[ring]
    if not len(pixels):
        return ()
    unique, counts = np.unique(pixels, axis=0, return_counts=True)
    pairs = sorted(
        ((_hex(color), int(count)) for color, count in zip(unique, counts, strict=True) if tuple(color) != contour_color),
        key=lambda value: (-value[1], value[0]),
    )
    return tuple(color for color, _ in pairs[:4])


def _classify_topology(closed: bool, endpoint_degrees: tuple[int, int], adjacent_colors: tuple[str, ...]) -> str:
    if closed:
        return "CLOSED_OUTLINE"
    if max(endpoint_degrees) > 2:
        return "INTERSECTING_OR_BRANCHING_CONTOUR"
    if endpoint_degrees == (1, 1):
        return "OPEN_DECORATIVE_STROKE"
    if len(adjacent_colors) >= 2:
        return "SHARED_COLOR_BOUNDARY"
    return "OPEN_CONTOUR"


def _draw_path_high_resolution(
    path: tuple[Pixel, ...],
    shape: tuple[int, int],
    width_mm: float,
    config: ContourNormalizationConfig,
    closed: bool,
) -> np.ndarray:
    scale = config.internal_scale
    height, width = shape
    mm_per_high_x = config.print_width_mm / (width * scale)
    mm_per_high_y = config.print_height_mm / (height * scale)
    if abs(mm_per_high_x - mm_per_high_y) > 1e-9:
        raise ValueError("anisotropic contour normalization is not supported")
    thickness = max(1, int(np.ceil(width_mm / mm_per_high_x)))
    canvas = np.zeros((height * scale, width * scale), dtype=np.uint8)
    points = np.asarray(
        [[x * scale + scale // 2, y * scale + scale // 2] for y, x in path],
        dtype=np.int32,
    ).reshape((-1, 1, 2))
    cv2.polylines(canvas, [points], closed, 1, thickness=thickness, lineType=cv2.LINE_8)
    radius = max(1, thickness // 2)
    if not closed:
        cv2.circle(canvas, tuple(points[0, 0]), radius, 1, thickness=-1, lineType=cv2.LINE_8)
        cv2.circle(canvas, tuple(points[-1, 0]), radius, 1, thickness=-1, lineType=cv2.LINE_8)
    coverage = canvas.reshape(height, scale, width, scale).mean(axis=(1, 3))
    return coverage >= 0.5


def _color_component_counts(
    image: np.ndarray,
    colors: Iterable[tuple[int, int, int]],
) -> dict[tuple[int, int, int], int]:
    return {
        color: int(
            cv2.connectedComponents(
                np.all(image == np.asarray(color, dtype=np.uint8), axis=2).astype(np.uint8),
                connectivity=4,
            )[0]
            - 1
        )
        for color in colors
    }


def normalize_contours(
    image: np.ndarray,
    config: ContourNormalizationConfig = ContourNormalizationConfig(),
) -> tuple[np.ndarray, dict[str, Any]]:
    """Return one conservative normalized candidate and detailed evidence."""

    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be an HxWx3 RGB array")
    height, width = image.shape[:2]
    mm_per_x = config.print_width_mm / width
    mm_per_y = config.print_height_mm / height
    if abs(mm_per_x - mm_per_y) > 1e-9:
        raise ValueError("pixels must have the same physical width and height")
    original_palette = {_hex(color) for color in np.unique(image.reshape(-1, 3), axis=0)}
    palette_tuples = [
        tuple(int(value) for value in color)
        for color in np.unique(image.reshape(-1, 3), axis=0)
    ]
    non_contour_colors = [color for color in palette_tuples if color != config.contour_color]
    original_color_components = _color_component_counts(image, non_contour_colors)
    contour_mask = np.all(image == config.contour_color, axis=2)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        contour_mask.astype(np.uint8), connectivity=8
    )
    skeleton = zhang_suen_thinning(contour_mask)
    distance_high = _high_resolution_distance(contour_mask, config.internal_scale)
    paths = trace_skeleton_segments(skeleton)
    segments: list[ContourSegment] = []

    for segment_id, (path, closed, endpoint_degrees) in enumerate(paths):
        component_ids = [int(labels[y, x]) for y, x in path if labels[y, x] > 0]
        component_id = max(set(component_ids), key=component_ids.count) if component_ids else 0
        component_area = int(stats[component_id, cv2.CC_STAT_AREA]) if component_id else 0
        filled = bool(component_id and _component_is_filled(stats[component_id], component_area))
        guard = min(config.endpoint_guard_pixels, max(0, (len(path) - 1) // 4))
        core_path = path[guard : len(path) - guard] if guard and len(path) > 2 * guard else path
        widths = _effective_widths(distance_high, core_path, mm_per_x, config.internal_scale)
        if len(widths):
            minimum = float(widths.min())
            p10 = float(np.percentile(widths, 10))
            median = float(np.median(widths))
            p90 = float(np.percentile(widths, 90))
            nominal = float(max(median, np.percentile(widths, 75)))
            thin_fraction = float(np.mean(widths < config.minimum_contour_width_mm))
        else:
            minimum = p10 = median = p90 = nominal = thin_fraction = 0.0
        adjacent = _adjacent_colors(image, path, config.contour_color)
        topology = _classify_topology(closed, endpoint_degrees, adjacent)
        requires = bool(
            len(path) >= config.minimum_segment_length_pixels
            and thin_fraction >= config.thin_fraction_trigger
            and p10 < config.minimum_contour_width_mm
        )
        if filled:
            disposition = "PRESERVE_FILLED_DARK_REGION"
            reason = "component shape is a filled dark region rather than a stroke"
            requires = False
        elif len(path) < config.minimum_segment_length_pixels:
            disposition = "UNRESOLVED_SHORT_SEGMENT"
            reason = "segment is too short for stable centerline normalization"
        elif requires:
            disposition = "PROPOSE_NORMALIZATION"
            reason = "representative centerline samples fall below the physical minimum"
        else:
            disposition = "PRESERVE_ALREADY_ACCEPTABLE"
            reason = "representative centerline samples meet the minimum"
        segments.append(
            ContourSegment(
                segment_id=segment_id,
                component_id=component_id,
                points=tuple(path),
                closed=closed,
                endpoint_degrees=endpoint_degrees,
                topology=topology,
                adjacent_colors=adjacent,
                before_min_mm=minimum,
                before_p10_mm=p10,
                before_median_mm=median,
                before_p90_mm=p90,
                intended_nominal_mm=nominal,
                thin_fraction=thin_fraction,
                requires_normalization=requires,
                disposition=disposition,
                reason=reason,
            )
        )

    candidate_mask = contour_mask.copy()
    accepted_ids: list[int] = []
    rejected_ids: list[int] = []
    component_merges: list[dict[str, Any]] = []
    color_topology_rejections: list[dict[str, Any]] = []
    for segment in segments:
        if not segment.requires_normalization:
            continue
        target_width = max(config.minimum_contour_width_mm, segment.intended_nominal_mm)
        proposed = _draw_path_high_resolution(
            segment.points, contour_mask.shape, target_width, config, segment.closed
        )
        additions = proposed & ~candidate_mask
        if not np.any(additions):
            accepted_ids.append(segment.segment_id)
            continue
        # A widened segment may overlap its own component, but it may not join
        # any distinct original dark component (for example an eye to a head
        # outline).  Such a change is review-only and is not applied.
        contact = cv2.dilate(additions.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        touched = sorted(int(value) for value in np.unique(labels[contact]) if int(value) > 0)
        foreign = [value for value in touched if value != segment.component_id]
        if foreign:
            rejected_ids.append(segment.segment_id)
            component_merges.append(
                {
                    "segment_id": segment.segment_id,
                    "source_component": segment.component_id,
                    "would_touch_components": foreign,
                }
            )
            continue
        tentative_mask = candidate_mask | proposed
        tentative_image = image.copy()
        tentative_image[tentative_mask & ~contour_mask] = np.asarray(
            config.contour_color, dtype=np.uint8
        )
        tentative_counts = _color_component_counts(tentative_image, non_contour_colors)
        changed_topology = {
            _hex(color): {
                "before_components": original_color_components[color],
                "proposed_components": tentative_counts[color],
            }
            for color in non_contour_colors
            if tentative_counts[color] != original_color_components[color]
        }
        if changed_topology:
            rejected_ids.append(segment.segment_id)
            color_topology_rejections.append(
                {
                    "segment_id": segment.segment_id,
                    "changed_color_topology": changed_topology,
                }
            )
            continue
        candidate_mask |= proposed
        accepted_ids.append(segment.segment_id)

    result = image.copy()
    result[candidate_mask & ~contour_mask] = np.asarray(config.contour_color, dtype=np.uint8)
    changed = np.any(result != image, axis=2)
    after_distance_high = _high_resolution_distance(candidate_mask, config.internal_scale)
    after_rows: list[dict[str, Any]] = []
    unresolved_ids: list[int] = []
    for segment in segments:
        guard = min(config.endpoint_guard_pixels, max(0, (len(segment.points) - 1) // 4))
        core = segment.points[guard : len(segment.points) - guard] if guard and len(segment.points) > 2 * guard else segment.points
        widths = _effective_widths(
            after_distance_high, core, mm_per_x, config.internal_scale
        )
        p10 = float(np.percentile(widths, 10)) if len(widths) else 0.0
        minimum = float(widths.min()) if len(widths) else 0.0
        median = float(np.median(widths)) if len(widths) else 0.0
        if segment.requires_normalization and (
            segment.segment_id in rejected_ids or p10 < config.minimum_contour_width_mm
        ):
            unresolved_ids.append(segment.segment_id)
        after_rows.append(
            {
                "segment_id": segment.segment_id,
                "after_min_mm": minimum,
                "after_p10_mm": p10,
                "after_median_mm": median,
                "passes_representative_width": p10 >= config.minimum_contour_width_mm,
            }
        )

    after_palette = {_hex(color) for color in np.unique(result.reshape(-1, 3), axis=0)}
    black_before_count = cv2.connectedComponents(contour_mask.astype(np.uint8), connectivity=8)[0] - 1
    black_after_count = cv2.connectedComponents(candidate_mask.astype(np.uint8), connectivity=8)[0] - 1
    report = {
        "config": asdict(config),
        "image_pixels": [width, height],
        "mm_per_pixel": mm_per_x,
        "raster_measurement_uncertainty_mm": mm_per_x / 2.0,
        "required_native_pixels": config.minimum_contour_width_mm / mm_per_x,
        "internal_required_pixels": config.minimum_contour_width_mm / (mm_per_x / config.internal_scale),
        "identified_contour_segments": len(segments),
        "original_dark_components": component_count - 1,
        "filled_dark_components_preserved": sorted(
            {
                segment.component_id
                for segment in segments
                if segment.disposition == "PRESERVE_FILLED_DARK_REGION"
            }
        ),
        "segments_requiring_normalization": sum(segment.requires_normalization for segment in segments),
        "segments_normalized": len(accepted_ids),
        "segments_rejected_to_avoid_merge": rejected_ids,
        "segments_unresolved_after_validation": sorted(set(unresolved_ids)),
        "accepted_segment_ids": accepted_ids,
        "changed_visible_pixels": int(np.count_nonzero(changed)),
        "changed_visible_percent": float(100.0 * np.mean(changed)),
        "all_changes_are_additions_of_existing_contour_color": bool(
            np.all(result[changed] == np.asarray(config.contour_color))
        ),
        "palette_before": sorted(original_palette),
        "palette_after": sorted(after_palette),
        "palette_preserved": original_palette == after_palette,
        "dark_components_before": int(black_before_count),
        "dark_components_after": int(black_after_count),
        "distinct_dark_components_merged": int(black_after_count) < int(black_before_count),
        "component_merge_rejections": component_merges,
        "color_topology_rejections": color_topology_rejections,
        "segments": [
            {
                **{key: value for key, value in asdict(segment).items() if key != "points"},
                "point_count": len(segment.points),
                "bounds_pixels": [
                    min(point[1] for point in segment.points),
                    min(point[0] for point in segment.points),
                    max(point[1] for point in segment.points) + 1,
                    max(point[0] for point in segment.points) + 1,
                ],
            }
            for segment in segments
        ],
        "after_measurements": after_rows,
    }
    return result, report

