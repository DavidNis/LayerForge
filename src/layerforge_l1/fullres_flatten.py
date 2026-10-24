"""Full-resolution flat-color snapping for gradient illustrations.

The earlier preprocessing worked on a 512 px raster (0.39 mm/px at 200 mm),
where a 1.4 mm stroke is ~3.5 px and every rounding step changes its width by
~25%.  This module keeps the source resolution and only snaps colors:

1. edge-preserving smoothing (bilateral) removes JPEG noise and shallow
   gradients without moving stroke edges;
2. every pixel takes the nearest palette color in CIELAB;
3. a majority filter removes speckle and anti-aliasing halos;
4. fragments below a minimum area merge into their dominant neighbor.

No stroke is thickened or re-drawn, so stroke width follows the source.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy import ndimage


@dataclass(frozen=True)
class FlattenConfig:
    bilateral_diameter: int = 9
    bilateral_sigma_color: float = 24.0
    bilateral_sigma_space: float = 5.0
    majority_window: int = 5
    min_fragment_pixels: int = 40  # ~0.4 mm2 at 0.098 mm/px


def snap_to_palette(rgb: np.ndarray, palette: np.ndarray, config: FlattenConfig = FlattenConfig()) -> np.ndarray:
    smooth = cv2.bilateralFilter(
        rgb, config.bilateral_diameter, config.bilateral_sigma_color, config.bilateral_sigma_space
    )
    lab = cv2.cvtColor(smooth, cv2.COLOR_RGB2LAB).astype(np.float32)
    palette_lab = cv2.cvtColor(palette[None].astype(np.uint8), cv2.COLOR_RGB2LAB)[0].astype(np.float32)
    distance = ((lab[:, :, None, :] - palette_lab[None, None]) ** 2).sum(-1)
    labels = distance.argmin(-1).astype(np.uint8)
    labels = majority_filter(labels, len(palette), config.majority_window)
    return merge_small_fragments(labels, config.min_fragment_pixels)


def majority_filter(labels: np.ndarray, count: int, window: int) -> np.ndarray:
    votes = np.stack(
        [cv2.boxFilter((labels == i).astype(np.float32), -1, (window, window)) for i in range(count)]
    )
    return votes.argmax(0).astype(np.uint8)


def merge_small_fragments(labels: np.ndarray, min_pixels: int) -> np.ndarray:
    """Relabel each 4-connected fragment below ``min_pixels`` to its dominant neighbor."""

    labels = labels.copy()
    for color in np.unique(labels):
        components, count = ndimage.label(labels == color)
        if not count:
            continue
        areas = np.bincount(components.ravel())
        for index, region in enumerate(ndimage.find_objects(components), start=1):
            if areas[index] >= min_pixels:
                continue
            y0, y1 = max(region[0].start - 1, 0), region[0].stop + 1
            x0, x1 = max(region[1].start - 1, 0), region[1].stop + 1
            window = labels[y0:y1, x0:x1]
            mask = components[y0:y1, x0:x1] == index
            ring = ndimage.binary_dilation(mask) & ~mask
            neighbors = window[ring]
            if neighbors.size:
                window[mask] = np.bincount(neighbors).argmax()
    return labels


def extract_palette(
    rgb: np.ndarray,
    max_colors: int = 12,
    merge_delta: float = 14.0,
    min_share: float = 0.001,
    config: FlattenConfig = FlattenConfig(),
) -> np.ndarray:
    """Automatic palette: k-means in CIELAB on smoothed, non-edge pixels.

    Edge pixels (anti-aliasing) are excluded so blends between two colors do
    not become palette entries.  Clusters closer than ``merge_delta`` (OpenCV
    8-bit Lab units) are merged; clusters under ``min_share`` of the sampled
    pixels are dropped.
    """

    smooth = cv2.bilateralFilter(
        rgb, config.bilateral_diameter, config.bilateral_sigma_color, config.bilateral_sigma_space
    )
    lab = cv2.cvtColor(smooth, cv2.COLOR_RGB2LAB).astype(np.float32)
    gray = cv2.cvtColor(smooth, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gradient = np.hypot(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    interior = cv2.erode((gradient < 40).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    samples = lab[interior][:: max(1, interior.sum() // 200_000)]
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5)
    _, assignment, centers = cv2.kmeans(samples, max_colors, None, criteria, 4, cv2.KMEANS_PP_CENTERS)
    shares = np.bincount(assignment.ravel(), minlength=max_colors) / len(samples)
    clusters = [(centers[i], shares[i]) for i in np.argsort(-shares) if shares[i] >= min_share]
    merged: list[tuple[np.ndarray, float]] = []
    for center, share in clusters:
        for index, (kept, kept_share) in enumerate(merged):
            if np.linalg.norm(center - kept) < merge_delta:
                total = kept_share + share
                merged[index] = ((kept * kept_share + center * share) / total, total)
                break
        else:
            merged.append((center, share))
    lab_centers = np.clip(np.array([c for c, _ in merged]), 0, 255).astype(np.uint8)
    return cv2.cvtColor(lab_centers[None], cv2.COLOR_LAB2RGB)[0]
