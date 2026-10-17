"""Rasterization helpers for source art and CAD-derived projections."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from .geometry import polygons


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _pixel_ring(
    coords: Iterable[tuple[float, float]], canvas_mm: tuple[float, float], pixel_size: tuple[int, int]
) -> list[tuple[int, int]]:
    canvas_w, canvas_h = canvas_mm
    width, height = pixel_size
    return [
        (round(x / canvas_w * width), round((canvas_h - y) / canvas_h * height))
        for x, y in coords
    ]


def geometry_mask(
    geometry: BaseGeometry, canvas_mm: tuple[float, float], pixel_size: tuple[int, int]
) -> Image.Image:
    mask = Image.new("L", pixel_size, 0)
    draw = ImageDraw.Draw(mask)
    for poly in polygons(geometry):
        draw.polygon(_pixel_ring(poly.exterior.coords, canvas_mm, pixel_size), fill=255)
        for interior in poly.interiors:
            draw.polygon(_pixel_ring(interior.coords, canvas_mm, pixel_size), fill=0)
    return mask


def composite_layers(
    layers: list[tuple[BaseGeometry, str]],
    canvas_mm: tuple[float, float],
    pixel_size: tuple[int, int],
    background: tuple[int, int, int, int] = (255, 255, 255, 0),
) -> Image.Image:
    image = Image.new("RGBA", pixel_size, background)
    for geometry, color in layers:
        mask = geometry_mask(geometry, canvas_mm, pixel_size)
        paint = Image.new("RGBA", pixel_size, (*hex_rgb(color), 255))
        image.alpha_composite(Image.composite(paint, Image.new("RGBA", pixel_size), mask))
    return image


def label_map(
    layers: list[tuple[BaseGeometry, int]], canvas_mm: tuple[float, float], pixel_size: tuple[int, int]
) -> np.ndarray:
    width, height = pixel_size
    labels = np.zeros((height, width), dtype=np.uint8)
    for geometry, label in layers:
        mask = np.asarray(geometry_mask(geometry, canvas_mm, pixel_size), dtype=np.uint8) > 0
        labels[mask] = label
    return labels


def save_layer_preview(
    geometry: BaseGeometry,
    color: str,
    canvas_mm: tuple[float, float],
    pixel_size: tuple[int, int],
    output: Path,
) -> None:
    image = composite_layers([(geometry, color)], canvas_mm, pixel_size, (248, 248, 248, 255))
    image.save(output)

