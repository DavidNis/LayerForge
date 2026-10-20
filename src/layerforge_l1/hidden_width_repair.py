"""Targeted retained-width repair for fixed 2D shadow-box stacks.

The repair is deliberately additive.  It follows the existing narrow material
path between a perimeter-connected structural core and each orphan core, then
widens only that path.  Accepted additions must be hidden by a more-forward
physical sheet.  If the hidden domain is insufficient, the same operation can
be rendered as a *proposal* with visible changes, but it is never reported as
an exact reconstruction.

This module does not change layer order, source colors, or the 1.2 mm rule.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Any

import cv2
import numpy as np

from .connectivity_repair import audit_connection_width, audit_edge_connectivity
from .layer_optimization import DEFAULT_OPTIMIZATION_CONFIG, OptimizationConfig
from .subtractive_poc import DEFAULT_CONFIG, PocConfig


@dataclass(frozen=True)
class WidthRepairConfig:
    """Physical and raster controls for one deterministic repair pass."""

    physical_size_mm: float = 200.0
    validation_scale: int = 4
    max_iterations: int = 6


def validation_poc_config(
    native: PocConfig = DEFAULT_CONFIG,
    repair: WidthRepairConfig = WidthRepairConfig(),
) -> PocConfig:
    """Return an isotropically upscaled validation grid at the final size."""

    if repair.validation_scale < 1:
        raise ValueError("validation_scale must be positive")
    scale = repair.validation_scale
    return replace(
        native,
        sheet_size_mm=repair.physical_size_mm,
        analysis_pixels=native.analysis_pixels * scale,
        outer_pixels=native.outer_pixels * scale,
        perimeter_pixels=native.perimeter_pixels * scale,
    )


def _nearest(mask: np.ndarray, scale: int) -> np.ndarray:
    if scale == 1:
        return mask.astype(bool, copy=True)
    height, width = mask.shape
    return cv2.resize(
        mask.astype(np.uint8),
        (width * scale, height * scale),
        interpolation=cv2.INTER_NEAREST,
    ).astype(bool)


def audit_mask_at_physical_scale(
    physical_mask: np.ndarray,
    visible_aperture: np.ndarray,
    native: PocConfig = DEFAULT_CONFIG,
    optimization: OptimizationConfig = DEFAULT_OPTIMIZATION_CONFIG,
    repair: WidthRepairConfig = WidthRepairConfig(),
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Run the unchanged validators on a finer raster at the final scale."""

    scale = repair.validation_scale
    physical_high = _nearest(physical_mask, scale)
    visible_high = _nearest(visible_aperture, scale)
    poc_high = validation_poc_config(native, repair)
    edge, _ = audit_edge_connectivity(physical_high)
    width, maps = audit_connection_width(
        physical_high, visible_high, poc_high, optimization
    )
    return {"edge": edge, "width": width}, maps


def hidden_addition_domain(
    masks_back_to_front: list[np.ndarray], layer_index: int
) -> np.ndarray:
    """Pixels an existing more-forward sheet guarantees will remain hidden."""

    current = masks_back_to_front[layer_index]
    if layer_index + 1 >= len(masks_back_to_front):
        return np.zeros_like(current, dtype=bool)
    front_cover = np.logical_or.reduce(masks_back_to_front[layer_index + 1 :])
    return front_cover & ~current


def _pool_any(mask: np.ndarray, scale: int) -> np.ndarray:
    if scale == 1:
        return mask.astype(bool, copy=True)
    height, width = mask.shape
    if height % scale or width % scale:
        raise ValueError("validation raster is not divisible by validation_scale")
    return mask.reshape(height // scale, scale, width // scale, scale).any(
        axis=(1, 3)
    )


def _shortest_existing_material_paths(
    material: np.ndarray, source: np.ndarray, goals: np.ndarray
) -> tuple[np.ndarray, int, int]:
    """Find deterministic shortest 4-neighbour paths through existing material.

    One path is returned for each connected goal component.  These paths locate
    the actual narrow necks; the algorithm does not invent a bridge across an
    opening or take a straight-line shortcut through empty space.
    """

    if not np.any(source) or not np.any(goals):
        return np.zeros_like(material, dtype=bool), 0, 0

    goal_count, goal_labels = cv2.connectedComponents(
        goals.astype(np.uint8), connectivity=4
    )
    required = goal_count - 1
    height, width = material.shape
    predecessor = np.full(height * width, -1, dtype=np.int32)
    queue: deque[int] = deque()
    for flat in np.flatnonzero(source & material):
        index = int(flat)
        predecessor[index] = index
        queue.append(index)

    endpoints: dict[int, int] = {}
    directions = ((-1, 0), (0, -1), (0, 1), (1, 0))
    while queue and len(endpoints) < required:
        current = queue.popleft()
        y, x = divmod(current, width)
        component = int(goal_labels[y, x])
        if component and component not in endpoints:
            endpoints[component] = current
        for dy, dx in directions:
            ny, nx = y + dy, x + dx
            if ny < 0 or nx < 0 or ny >= height or nx >= width:
                continue
            neighbor = ny * width + nx
            if predecessor[neighbor] != -1 or not material[ny, nx]:
                continue
            predecessor[neighbor] = current
            queue.append(neighbor)

    paths = np.zeros_like(material, dtype=bool)
    for endpoint in endpoints.values():
        current = endpoint
        while True:
            paths.flat[current] = True
            previous = int(predecessor[current])
            if previous == current:
                break
            if previous < 0:
                raise RuntimeError("broken predecessor chain")
            current = previous
    return paths, required, len(endpoints)


def repair_one_mask(
    physical_mask: np.ndarray,
    visible_aperture: np.ndarray,
    addition_domain: np.ndarray,
    native: PocConfig = DEFAULT_CONFIG,
    optimization: OptimizationConfig = DEFAULT_OPTIMIZATION_CONFIG,
    repair: WidthRepairConfig = WidthRepairConfig(),
) -> tuple[np.ndarray, dict[str, Any], dict[str, np.ndarray]]:
    """Widen structural necks, limited to an explicit permitted-addition mask."""

    if physical_mask.shape != addition_domain.shape:
        raise ValueError("physical mask and addition domain shapes differ")
    current = physical_mask.astype(bool, copy=True)
    addition_domain = addition_domain.astype(bool) & ~current
    added_total = np.zeros_like(current)
    path_total = np.zeros_like(current)
    iterations: list[dict[str, Any]] = []
    before, maps = audit_mask_at_physical_scale(
        current, visible_aperture, native, optimization, repair
    )
    initial = before

    requested_native = int(
        np.ceil(
            before["width"]["enforced_connection_width_mm"]
            / (repair.physical_size_mm / native.outer_pixels)
        )
    )
    # A centred OpenCV footprint must be odd.  This native-grid footprint is
    # used only to build material; acceptance is decided on the finer raster.
    native_kernel_size = (
        requested_native if requested_native % 2 else requested_native + 1
    )
    for iteration in range(repair.max_iterations):
        if before["width"]["status"] == "PASS":
            break
        source = _pool_any(maps["connected_core"], repair.validation_scale)
        goals = _pool_any(maps["orphan_core"], repair.validation_scale)
        core = _pool_any(maps["core"], repair.validation_scale)
        paths, required, reached = _shortest_existing_material_paths(
            current, source, goals
        )
        weak_paths = paths & ~core
        iteration_kernel_size = native_kernel_size + (2 * iteration)
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (iteration_kernel_size, iteration_kernel_size),
        )
        requested = (
            cv2.dilate(weak_paths.astype(np.uint8), kernel, iterations=1).astype(bool)
            & ~current
        )
        # Pooling a sub-pixel high-resolution core onto the native grid can
        # classify the last failing neck cell as an existing core.  If that
        # leaves no requested pixels, widen the complete located path by one
        # additional native radius rather than stopping on a raster artefact.
        if not np.any(requested) and np.any(paths):
            requested = (
                cv2.dilate(paths.astype(np.uint8), kernel, iterations=1).astype(bool)
                & ~current
            )
        permitted = requested & addition_domain
        iteration_row = {
            "iteration": iteration + 1,
            "orphan_goal_components": required,
            "orphan_goal_components_reached": reached,
            "path_pixels": int(np.count_nonzero(paths)),
            "weak_path_pixels": int(np.count_nonzero(weak_paths)),
            "repair_kernel_pixels": iteration_kernel_size,
            "requested_addition_pixels": int(np.count_nonzero(requested)),
            "permitted_addition_pixels": int(np.count_nonzero(permitted)),
        }
        iterations.append(iteration_row)
        path_total |= paths
        if reached != required or not np.any(permitted):
            break
        current |= permitted
        added_total |= permitted
        after, maps = audit_mask_at_physical_scale(
            current, visible_aperture, native, optimization, repair
        )
        iteration_row["resulting_width_status"] = after["width"]["status"]
        iteration_row["resulting_orphan_core_components"] = after["width"][
            "orphan_structural_core_components"
        ]
        before = after

    final, final_maps = audit_mask_at_physical_scale(
        current, visible_aperture, native, optimization, repair
    )
    result = {
        "status": final["width"]["status"],
        "modified_pixels": int(np.count_nonzero(added_total)),
        "available_addition_pixels": int(np.count_nonzero(addition_domain)),
        "native_mm_per_pixel": repair.physical_size_mm / native.outer_pixels,
        "native_repair_kernel_pixels": native_kernel_size,
        "iterations": iterations,
        "before_edge": initial["edge"],
        "before_width": initial["width"],
        "edge": final["edge"],
        "width": final["width"],
    }
    diagnostics = {
        "added": added_total,
        "paths": path_total,
        "orphan_core_after": final_maps["orphan_core"],
        "connected_core_after": final_maps["connected_core"],
    }
    return current, result, diagnostics


def compose_back_to_front(
    masks: list[np.ndarray], palette_indices: list[int], palette: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Independently composite actual physical masks, back to front."""

    if not masks:
        raise ValueError("at least one mask is required")
    labels = np.full(masks[0].shape, -1, dtype=np.int16)
    rgb = np.full((*masks[0].shape, 3), 247, dtype=np.uint8)
    for mask, palette_index in zip(masks, palette_indices, strict=True):
        labels[mask] = palette_index
        rgb[mask] = palette[palette_index]
    return labels, rgb
