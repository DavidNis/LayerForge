from __future__ import annotations

import cv2
import numpy as np

from layerforge_l1.local_contour_correction import (
    LocalContourCorrectionConfig,
    correct_reviewed_contour,
)


BLACK = (14, 15, 13)
WHITE = (246, 246, 246)


def _canvas() -> np.ndarray:
    image = np.empty((128, 128, 3), dtype=np.uint8)
    image[:] = WHITE
    return image


def _config() -> LocalContourCorrectionConfig:
    return LocalContourCorrectionConfig(
        print_width_mm=64.0,
        print_height_mm=64.0,
        minimum_width_mm=1.2,
        internal_scale=4,
        allowed_padding_pixels=5,
    )


def test_corrects_only_reviewed_open_path_neighborhood() -> None:
    image = _canvas()
    cv2.line(image, (15, 40), (110, 40), BLACK, thickness=1, lineType=cv2.LINE_8)
    cv2.line(image, (15, 90), (110, 90), BLACK, thickness=1, lineType=cv2.LINE_8)

    result, report, additions = correct_reviewed_contour(
        image, (10, 35, 116, 46), 1.0, _config()
    )

    assert report["minimum_width_pass"] is True
    assert report["outside_reviewed_neighborhood_changes"] == 0
    assert np.count_nonzero(additions) > 0
    assert np.array_equal(result[84:97], image[84:97])


def test_corrects_closed_curve_while_preserving_centerline_and_palette() -> None:
    image = _canvas()
    cv2.circle(image, (64, 64), 30, BLACK, thickness=1, lineType=cv2.LINE_8)
    palette_before = {tuple(color) for color in np.unique(image.reshape(-1, 3), axis=0)}

    result, report, additions = correct_reviewed_contour(
        image, (30, 30, 99, 99), 1.0, _config()
    )
    palette_after = {tuple(color) for color in np.unique(result.reshape(-1, 3), axis=0)}

    assert report["path_closed"] is True
    assert report["minimum_width_pass"] is True
    assert report["centerline_preserved"] is True
    assert palette_after == palette_before
    assert np.count_nonzero(additions) > 0

