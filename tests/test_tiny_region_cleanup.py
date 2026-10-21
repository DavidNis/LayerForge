from __future__ import annotations

import numpy as np

from layerforge_l1.tiny_region_cleanup import clean_tiny_regions


BLACK = (14, 15, 13)
BLUE = (75, 152, 207)
GOLD = (241, 215, 72)
WHITE = (246, 246, 246)


def _canvas() -> np.ndarray:
    image = np.empty((24, 24, 3), dtype=np.uint8)
    image[:] = BLUE
    return image


def test_merges_single_pixel_hole_embedded_in_existing_contour() -> None:
    image = _canvas()
    image[8:17, 8:17] = BLACK
    image[12, 12] = GOLD

    result, report = clean_tiny_regions(image)

    assert tuple(result[12, 12]) == BLACK
    assert report["tiny_regions_merged"] == 1
    assert report["decisions"][0]["dominant_neighbor_color"] == "#0E0F0D"


def test_preserves_small_dark_facial_or_decorative_detail() -> None:
    image = _canvas()
    image[12, 12] = BLACK

    result, report = clean_tiny_regions(image)

    assert np.array_equal(result, image)
    assert report["tiny_dark_details_preserved"] == 1


def test_preserves_ambiguous_tiny_boundary_region() -> None:
    image = _canvas()
    image[:, 12:] = WHITE
    image[11:13, 11:13] = GOLD

    result, report = clean_tiny_regions(image)

    assert np.array_equal(result, image)
    assert report["tiny_regions_preserved_ambiguous"] == 1


def test_cleanup_never_invents_a_palette_color() -> None:
    image = _canvas()
    image[8:17, 8:17] = BLACK
    image[12, 12] = GOLD
    before_palette = {tuple(color) for color in np.unique(image.reshape(-1, 3), axis=0)}

    result, _ = clean_tiny_regions(image)
    after_palette = {tuple(color) for color in np.unique(result.reshape(-1, 3), axis=0)}

    assert after_palette <= before_palette

