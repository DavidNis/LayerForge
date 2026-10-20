from __future__ import annotations

import cv2
import numpy as np

from layerforge_l1.contour_normalization import (
    ContourNormalizationConfig,
    normalize_contours,
    zhang_suen_thinning,
)


BLACK = (14, 15, 13)
WHITE = (246, 246, 246)


def _canvas(size: int = 128) -> np.ndarray:
    image = np.empty((size, size, 3), dtype=np.uint8)
    image[:] = WHITE
    return image


def _config() -> ContourNormalizationConfig:
    # 0.5 mm per raster pixel: 1.2 mm requires more than two pixels.
    return ContourNormalizationConfig(
        print_width_mm=64.0,
        print_height_mm=64.0,
        minimum_contour_width_mm=1.2,
        internal_scale=4,
        minimum_segment_length_pixels=5,
    )


def _mask(image: np.ndarray) -> np.ndarray:
    return np.all(image == BLACK, axis=2)


def _representative_widths_mm(mask: np.ndarray, mm_per_pixel: float) -> np.ndarray:
    skeleton = zhang_suen_thinning(mask)
    scale = 4
    enlarged = cv2.resize(
        mask.astype(np.uint8),
        (mask.shape[1] * scale, mask.shape[0] * scale),
        interpolation=cv2.INTER_NEAREST,
    )
    distance = cv2.distanceTransform(enlarged, cv2.DIST_L2, 5)
    widths = []
    for y, x in np.argwhere(skeleton):
        widths.append(
            2.0
            * float(distance[y * scale : (y + 1) * scale, x * scale : (x + 1) * scale].max())
            * mm_per_pixel
            / scale
        )
    widths = np.asarray(widths)
    return widths


def test_normalizes_straight_thin_contour_without_moving_its_centerline() -> None:
    source = _canvas()
    cv2.line(source, (16, 64), (112, 64), BLACK, thickness=1, lineType=cv2.LINE_8)

    result, report = normalize_contours(source, _config())

    assert report["changed_visible_pixels"] > 0
    assert report["palette_preserved"] is True
    assert report["distinct_dark_components_merged"] is False
    assert np.all(_mask(result)[64, 16:113])
    widths = _representative_widths_mm(_mask(result), 0.5)
    assert np.percentile(widths, 10) >= 1.2


def test_normalizes_curved_contour_as_a_complete_closed_segment() -> None:
    source = _canvas()
    cv2.circle(source, (64, 64), 35, BLACK, thickness=2, lineType=cv2.LINE_8)

    result, report = normalize_contours(source, _config())

    assert report["changed_visible_pixels"] > 0
    assert any(row["closed"] for row in report["segments"])
    widths = _representative_widths_mm(_mask(result), 0.5)
    assert np.percentile(widths, 10) >= 1.2
    # Uniform reconstruction should not create isolated radial bulges.
    ys, xs = np.where(_mask(result))
    radii = np.hypot(xs - 64, ys - 64)
    assert radii.max() - radii.min() <= 6.0


def test_diagonal_contour_uses_physical_width_not_axis_aligned_pixel_count() -> None:
    source = _canvas()
    cv2.line(source, (20, 104), (104, 20), BLACK, thickness=1, lineType=cv2.LINE_8)

    result, report = normalize_contours(source, _config())

    assert report["segments_normalized"] >= 1
    widths = _representative_widths_mm(_mask(result), 0.5)
    assert np.percentile(widths, 10) >= 1.2
    assert report["palette_preserved"] is True


def test_intersection_is_normalized_without_joining_a_nearby_component() -> None:
    source = _canvas()
    cv2.line(source, (20, 64), (108, 64), BLACK, thickness=2, lineType=cv2.LINE_8)
    cv2.line(source, (64, 20), (64, 108), BLACK, thickness=2, lineType=cv2.LINE_8)
    cv2.circle(source, (113, 64), 1, BLACK, thickness=-1, lineType=cv2.LINE_8)
    before_components = cv2.connectedComponents(_mask(source).astype(np.uint8), 8)[0] - 1

    result, report = normalize_contours(source, _config())
    after_components = cv2.connectedComponents(_mask(result).astype(np.uint8), 8)[0] - 1

    assert before_components == 2
    assert after_components == 2
    assert report["distinct_dark_components_merged"] is False
    assert np.all(_mask(result)[64, 20:109])
    assert np.all(_mask(result)[20:109, 64])

