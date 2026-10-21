from __future__ import annotations

import cv2
import numpy as np

from layerforge_l1.contour_consistency import (
    ContourConsistencyConfig,
    normalize_contour_consistency,
)
from layerforge_l1.contour_normalization import zhang_suen_thinning


BLACK = (14, 15, 13)
WHITE = (246, 246, 246)


def _widths(mask: np.ndarray, mm_per_pixel: float, scale: int = 4) -> np.ndarray:
    skeleton = zhang_suen_thinning(mask)
    high = cv2.resize(
        mask.astype(np.uint8),
        (mask.shape[1] * scale, mask.shape[0] * scale),
        interpolation=cv2.INTER_NEAREST,
    )
    distance = cv2.distanceTransform(high, cv2.DIST_L2, 5)
    return np.asarray(
        [
            2.0
            * float(
                distance[
                    y * scale : (y + 1) * scale,
                    x * scale : (x + 1) * scale,
                ].max()
            )
            * mm_per_pixel
            / scale
            for y, x in np.argwhere(skeleton)
        ]
    )


def test_repairs_a_thin_section_by_restoking_the_complete_centerline() -> None:
    image = np.full((128, 128, 3), WHITE, dtype=np.uint8)
    cv2.line(image, (15, 64), (112, 64), BLACK, thickness=3, lineType=cv2.LINE_8)
    image[63, 52:77] = WHITE
    image[65, 52:77] = WHITE
    config = ContourConsistencyConfig(
        print_width_mm=64.0,
        print_height_mm=64.0,
        minimum_width_mm=1.2,
        internal_scale=4,
    )

    result, report, additions = normalize_contour_consistency(image, config)

    mask = np.all(result == BLACK, axis=2)
    assert report["unresolved_paths"] == 0
    assert np.count_nonzero(additions) > 0
    assert np.percentile(_widths(mask, 0.5), 10) >= 1.2
    assert np.all(mask[63:66, 52:77])


def test_does_not_move_a_curved_centerline_or_create_detached_blobs() -> None:
    image = np.full((128, 128, 3), WHITE, dtype=np.uint8)
    cv2.ellipse(image, (64, 64), (42, 30), 0, 190, 350, BLACK, thickness=2)
    before = np.all(image == BLACK, axis=2)
    config = ContourConsistencyConfig(
        print_width_mm=64.0,
        print_height_mm=64.0,
        minimum_width_mm=1.2,
        internal_scale=4,
    )

    result, report, _ = normalize_contour_consistency(image, config)

    after = np.all(result == BLACK, axis=2)
    assert np.all(after[before])
    assert cv2.connectedComponents(before.astype(np.uint8), 8)[0] == cv2.connectedComponents(
        after.astype(np.uint8), 8
    )[0]
    assert report["unresolved_paths"] == 0

