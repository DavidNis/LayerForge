"""Conservative cleanup of tiny accidental regions in indexed artwork.

The cleanup is spatial and deterministic.  It never invents a color: a tiny
component may only be reassigned to a color touching its boundary.  Dark
contour components are protected as intentional details.  Weak or conflicting
neighbour evidence produces an AMBIGUOUS decision and leaves the source pixels
unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class TinyRegionCleanupConfig:
    maximum_area_pixels: int = 16
    maximum_extent_pixels: int = 6
    dominant_neighbor_ratio: float = 0.85
    contour_fill_ratio: float = 0.45
    contour_colors: tuple[tuple[int, int, int], ...] = ((14, 15, 13),)


def _hex(color: np.ndarray | tuple[int, int, int]) -> str:
    return "#" + "".join(f"{int(value):02X}" for value in color)


def _boundary_color_counts(
    image: np.ndarray,
    component: np.ndarray,
    own_color: tuple[int, int, int],
) -> dict[tuple[int, int, int], int]:
    ring = cv2.dilate(component.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    ring &= ~component
    colors, counts = np.unique(image[ring], axis=0, return_counts=True)
    return {
        tuple(int(value) for value in color): int(count)
        for color, count in zip(colors, counts, strict=True)
        if tuple(int(value) for value in color) != own_color
    }


def clean_tiny_regions(
    image: np.ndarray,
    config: TinyRegionCleanupConfig = TinyRegionCleanupConfig(),
) -> tuple[np.ndarray, dict[str, Any]]:
    """Apply one simultaneous high-confidence tiny-region cleanup pass."""

    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be an HxWx3 RGB array")
    height, width = image.shape[:2]
    contour_colors = set(config.contour_colors)
    decisions: list[dict[str, Any]] = []
    replacements: list[tuple[np.ndarray, tuple[int, int, int]]] = []

    for color_array in np.unique(image.reshape(-1, 3), axis=0):
        color = tuple(int(value) for value in color_array)
        mask = np.all(image == color_array, axis=2).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            mask, connectivity=4
        )
        for component_id in range(1, count):
            x, y, component_width, component_height, area = (
                int(value) for value in stats[component_id]
            )
            if area > config.maximum_area_pixels or max(
                component_width, component_height
            ) > config.maximum_extent_pixels:
                continue
            component = labels == component_id
            neighbors = _boundary_color_counts(image, component, color)
            neighbor_total = sum(neighbors.values())
            ordered = sorted(neighbors.items(), key=lambda item: (-item[1], item[0]))
            dominant_color, dominant_count = ordered[0] if ordered else (None, 0)
            dominant_ratio = dominant_count / max(1, neighbor_total)
            touches_border = bool(
                x == 0
                or y == 0
                or x + component_width == width
                or y + component_height == height
            )

            if color in contour_colors:
                decision = "PRESERVE_MEANINGFUL_DARK_DETAIL"
                reason = "dark contour/detail components are protected"
            elif touches_border:
                decision = "PRESERVE_AMBIGUOUS"
                reason = "component touches the image boundary"
            elif dominant_color in contour_colors and (
                dominant_ratio >= config.contour_fill_ratio
                and len(neighbors) <= 2
                and area <= 4
            ):
                decision = "MERGE_HIGH_CONFIDENCE_CONTOUR_HOLE"
                reason = (
                    "tiny non-contour island is embedded in a contour boundary "
                    "with dominant dark contact"
                )
            elif dominant_color is not None and dominant_ratio >= config.dominant_neighbor_ratio:
                decision = "MERGE_HIGH_CONFIDENCE_SURFACE_FRAGMENT"
                reason = "one touching region overwhelmingly dominates the boundary"
            else:
                decision = "PRESERVE_AMBIGUOUS"
                reason = "neighbor evidence is not strong enough for a safe reassignment"

            if decision.startswith("MERGE_") and dominant_color is not None:
                replacements.append((component, dominant_color))
            decisions.append(
                {
                    "component_id_within_color": component_id,
                    "source_color": _hex(color),
                    "area_pixels": area,
                    "bounds_pixels": [x, y, component_width, component_height],
                    "maximum_extent_pixels": max(component_width, component_height),
                    "touches_image_border": touches_border,
                    "neighbor_contacts": {
                        _hex(neighbor): value
                        for neighbor, value in ordered
                    },
                    "dominant_neighbor_color": _hex(dominant_color)
                    if dominant_color is not None
                    else None,
                    "dominant_neighbor_ratio": dominant_ratio,
                    "decision": decision,
                    "reason": reason,
                }
            )

    result = image.copy()
    for component, replacement in replacements:
        result[component] = np.asarray(replacement, dtype=np.uint8)

    source_palette = {
        _hex(color) for color in np.unique(image.reshape(-1, 3), axis=0)
    }
    result_palette = {
        _hex(color) for color in np.unique(result.reshape(-1, 3), axis=0)
    }
    changed = np.any(result != image, axis=2)
    report = {
        "config": asdict(config),
        "tiny_regions_inspected": len(decisions),
        "tiny_regions_merged": sum(
            decision["decision"].startswith("MERGE_") for decision in decisions
        ),
        "tiny_regions_preserved_ambiguous": sum(
            decision["decision"] == "PRESERVE_AMBIGUOUS" for decision in decisions
        ),
        "tiny_dark_details_preserved": sum(
            decision["decision"] == "PRESERVE_MEANINGFUL_DARK_DETAIL"
            for decision in decisions
        ),
        "changed_pixels": int(np.count_nonzero(changed)),
        "palette_before": sorted(source_palette),
        "palette_after": sorted(result_palette),
        "palette_entries_removed": sorted(source_palette - result_palette),
        "palette_entries_added": sorted(result_palette - source_palette),
        "decisions": decisions,
    }
    return result, report

