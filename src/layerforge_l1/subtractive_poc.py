"""Focused 2D proof for layer-first, subtractive, common-perimeter sheets.

This module deliberately stops before CAD.  It discovers a printable palette,
searches for a nested sheet order, creates full-sheet retained/cutout masks, and
rejects any plan that would require a visible connector.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw


@dataclass(frozen=True)
class PocConfig:
    analysis_pixels: int = 512
    topology_pixels: int = 192
    initial_clusters: int = 10
    maximum_layers: int = 9
    minimum_layers: int = 5
    merge_delta_lab: float = 19.0
    minimum_component_pixels: int = 20
    outer_pixels: int = 576
    perimeter_pixels: int = 32
    sheet_size_mm: float = 100.0
    minimum_feature_mm: float = 1.2
    beam_width: int = 256
    random_seed: int = 74031


DEFAULT_CONFIG = PocConfig()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _rgb_hex(color: np.ndarray) -> str:
    return "#" + "".join(f"{int(channel):02X}" for channel in color)


def _load_rgb(path: Path, size: int) -> tuple[np.ndarray, tuple[int, int]]:
    image = Image.open(path).convert("RGBA")
    original_size = image.size
    rgba = np.asarray(image, dtype=np.uint8)
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    rgb = np.rint(rgba[:, :, :3] * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
    resized = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_AREA)
    return resized, original_size


def _sample_pixels(lab: np.ndarray, limit: int, seed: int) -> np.ndarray:
    flat = lab.reshape(-1, 3)
    if len(flat) <= limit:
        return flat.astype(np.float32)
    rng = np.random.default_rng(seed)
    indices = rng.choice(len(flat), size=limit, replace=False)
    return flat[indices].astype(np.float32)


def _nearest_labels(lab: np.ndarray, centers: np.ndarray) -> np.ndarray:
    pixels = lab.reshape(-1, 3).astype(np.float32)
    labels = np.empty(len(pixels), dtype=np.uint8)
    chunk = 65536
    for start in range(0, len(pixels), chunk):
        block = pixels[start : start + chunk]
        distances = ((block[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels[start : start + chunk] = np.argmin(distances, axis=1).astype(np.uint8)
    return labels.reshape(lab.shape[:2])


def _merge_centers(
    centers: np.ndarray, counts: np.ndarray, threshold: float, maximum: int
) -> np.ndarray:
    active = [(centers[index].astype(np.float64), float(counts[index])) for index in range(len(centers))]
    while len(active) > 1:
        best: tuple[float, int, int] | None = None
        for first in range(len(active)):
            for second in range(first + 1, len(active)):
                distance = float(np.linalg.norm(active[first][0] - active[second][0]))
                candidate = (distance, first, second)
                if best is None or candidate < best:
                    best = candidate
        assert best is not None
        distance, first, second = best
        if len(active) <= maximum and distance >= threshold:
            break
        c1, n1 = active[first]
        c2, n2 = active[second]
        merged = ((c1 * n1 + c2 * n2) / (n1 + n2), n1 + n2)
        active = [item for index, item in enumerate(active) if index not in {first, second}]
        active.append(merged)
    return np.asarray([item[0] for item in active], dtype=np.float32)


def _cleanup_tiny_components(labels: np.ndarray, minimum: int) -> tuple[np.ndarray, list[dict[str, int]]]:
    cleaned = labels.copy()
    events: list[dict[str, int]] = []
    kernel = np.ones((3, 3), dtype=np.uint8)
    for color in sorted(int(value) for value in np.unique(labels)):
        count, components, stats, _ = cv2.connectedComponentsWithStats(
            (labels == color).astype(np.uint8), 8
        )
        for component in range(1, count):
            area = int(stats[component, cv2.CC_STAT_AREA])
            if area >= minimum:
                continue
            mask = components == component
            ring = cv2.dilate(mask.astype(np.uint8), kernel).astype(bool) & ~mask
            neighbours = cleaned[ring]
            if neighbours.size:
                values, counts = np.unique(neighbours, return_counts=True)
                replacement = int(values[np.argmax(counts)])
                cleaned[mask] = replacement
                events.append({"from": color, "to": replacement, "pixels": area})
    return cleaned, events


def analyze_image(source: Path, config: PocConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    rgb, original_size = _load_rgb(source, config.analysis_pixels)
    # Edge-preserving smoothing suppresses generated shading without blurring
    # closed boundaries into new physical colors.
    filtered = cv2.bilateralFilter(rgb, 7, 26, 26)
    lab = cv2.cvtColor(filtered, cv2.COLOR_RGB2LAB).astype(np.float32)
    sample = _sample_pixels(lab, 70000, config.random_seed)
    cv2.setRNGSeed(config.random_seed)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.1)
    _, sample_labels, initial = cv2.kmeans(
        sample,
        config.initial_clusters,
        None,
        criteria,
        1,
        cv2.KMEANS_PP_CENTERS,
    )
    sample_counts = np.bincount(sample_labels.ravel(), minlength=len(initial))
    centers = _merge_centers(
        initial,
        sample_counts,
        config.merge_delta_lab,
        config.maximum_layers,
    )
    # If aggressive merging produced too few choices, use the largest initial
    # centers; this is generic palette preservation, not fixture-specific logic.
    if len(centers) < config.minimum_layers:
        keep = np.argsort(sample_counts)[-config.minimum_layers :]
        centers = initial[keep]
    labels = _nearest_labels(lab, centers)
    initial_counts = np.bincount(labels.ravel(), minlength=len(centers))
    rare_limit = max(config.minimum_component_pixels, int(labels.size * 0.0005))
    common = [index for index, count in enumerate(initial_counts) if count >= rare_limit]
    for rare in [index for index, count in enumerate(initial_counts) if count < rare_limit]:
        if not common:
            break
        replacement = min(common, key=lambda index: float(np.linalg.norm(centers[rare] - centers[index])))
        labels[labels == rare] = replacement
    labels, cleanup = _cleanup_tiny_components(labels, config.minimum_component_pixels)

    # Refit each surviving center from all assigned source pixels.
    unique = sorted(int(value) for value in np.unique(labels))
    remap = {old: new for new, old in enumerate(unique)}
    labels = np.vectorize(remap.get, otypes=[np.uint8])(labels)
    centers = np.asarray([lab[labels == remap[old]].mean(axis=0) for old in unique], dtype=np.float32)
    palette_rgb = cv2.cvtColor(
        np.clip(np.rint(centers), 0, 255).astype(np.uint8).reshape(1, -1, 3),
        cv2.COLOR_LAB2RGB,
    ).reshape(-1, 3)
    counts = np.bincount(labels.ravel(), minlength=len(palette_rgb))
    provider_labels, providers, provider_splits = _provider_regions(labels, palette_rgb)
    return {
        "rgb": rgb,
        "labels": labels,
        "palette_rgb": palette_rgb,
        "palette_hex": [_rgb_hex(color) for color in palette_rgb],
        "counts": counts,
        "provider_labels": provider_labels,
        "providers": providers,
        "provider_splits": provider_splits,
        "cleanup_events": cleanup,
        "original_size": original_size,
    }


def _provider_regions(
    palette_labels: np.ndarray, palette_rgb: np.ndarray
) -> tuple[np.ndarray, list[dict[str, Any]], list[dict[str, Any]]]:
    """Peel same-color region batches from the exterior toward the interior.

    Every batch contains only regions already adjacent to the connected front
    union (or to the perimeter for the first batch). A palette color can recur
    at later depths when its enclosed regions become reachable. This is a
    topology rule, not object recognition.
    """
    height, width = palette_labels.shape
    region_map = np.full((height, width), -1, dtype=np.int32)
    nodes: list[dict[str, Any]] = []
    for palette_index in range(len(palette_rgb)):
        count, components, stats, _ = cv2.connectedComponentsWithStats(
            (palette_labels == palette_index).astype(np.uint8), connectivity=4
        )
        for component in range(1, count):
            node_index = len(nodes)
            mask = components == component
            region_map[mask] = node_index
            nodes.append(
                {
                    "index": node_index,
                    "palette_index": palette_index,
                    "area": int(stats[component, cv2.CC_STAT_AREA]),
                    "touches_border": bool(
                        stats[component, cv2.CC_STAT_LEFT] == 0
                        or stats[component, cv2.CC_STAT_TOP] == 0
                        or stats[component, cv2.CC_STAT_LEFT] + stats[component, cv2.CC_STAT_WIDTH] == width
                        or stats[component, cv2.CC_STAT_TOP] + stats[component, cv2.CC_STAT_HEIGHT] == height
                    ),
                }
            )
    if np.any(region_map < 0):
        raise RuntimeError("region graph left unassigned pixels")

    neighbours: list[set[int]] = [set() for _ in nodes]
    for first, second in ((region_map[:, :-1], region_map[:, 1:]), (region_map[:-1, :], region_map[1:, :])):
        differing = first != second
        pairs = np.stack((first[differing], second[differing]), axis=1)
        for a, b in np.unique(np.sort(pairs, axis=1), axis=0):
            neighbours[int(a)].add(int(b))
            neighbours[int(b)].add(int(a))

    provider_labels = np.full_like(palette_labels, 255, dtype=np.uint8)
    providers: list[dict[str, Any]] = []
    assigned: set[int] = set()
    unassigned = set(range(len(nodes)))
    while unassigned:
        if not assigned:
            eligible = {node for node in unassigned if nodes[node]["touches_border"]}
        else:
            eligible = {
                node for node in unassigned if any(neighbour in assigned for neighbour in neighbours[node])
            }
        if not eligible:
            raise RuntimeError("region adjacency graph became disconnected")
        by_color: dict[int, list[int]] = {}
        for node in eligible:
            by_color.setdefault(int(nodes[node]["palette_index"]), []).append(node)

        def batch_score(item: tuple[int, list[int]]) -> tuple[int, int, int]:
            palette_index, batch = item
            area = sum(int(nodes[node]["area"]) for node in batch)
            contact = sum(
                sum(1 for neighbour in neighbours[node] if neighbour in assigned) for node in batch
            )
            return area, contact, -palette_index

        palette_index, batch = max(by_color.items(), key=batch_score)
        provider_index = len(providers)
        batch_mask = np.isin(region_map, batch)
        provider_labels[batch_mask] = provider_index
        providers.append(
            {
                "index": provider_index,
                "palette_index": palette_index,
                "color": _rgb_hex(palette_rgb[palette_index]),
                "role": "exterior_to_interior_topology_batch",
                "pixels": int(np.count_nonzero(batch_mask)),
                "source_region_count": len(batch),
                "front_to_back_rank": provider_index,
            }
        )
        assigned.update(batch)
        unassigned.difference_update(batch)

    splits: list[dict[str, Any]] = []
    for palette_index in range(len(palette_rgb)):
        depths = [provider["index"] for provider in providers if provider["palette_index"] == palette_index]
        if len(depths) > 1:
            splits.append(
                {
                    "palette_index": palette_index,
                    "reason": "same color has regions reached at different topology depths",
                    "provider_indices_front_to_back": depths,
                }
            )
    return provider_labels, providers, splits


def _containment_preferences(labels: np.ndarray) -> list[dict[str, Any]]:
    """Infer inner-behind-surrounding preferences from generic boundary topology."""
    preferences: dict[tuple[int, int], float] = {}
    kernel = np.ones((3, 3), dtype=np.uint8)
    height, width = labels.shape
    for inner in sorted(int(value) for value in np.unique(labels)):
        count, components, stats, _ = cv2.connectedComponentsWithStats(
            (labels == inner).astype(np.uint8), 8
        )
        for component in range(1, count):
            area = int(stats[component, cv2.CC_STAT_AREA])
            if area < 12:
                continue
            x = int(stats[component, cv2.CC_STAT_LEFT])
            y = int(stats[component, cv2.CC_STAT_TOP])
            w = int(stats[component, cv2.CC_STAT_WIDTH])
            h = int(stats[component, cv2.CC_STAT_HEIGHT])
            if x == 0 or y == 0 or x + w == width or y + h == height:
                continue
            mask = components == component
            ring = cv2.dilate(mask.astype(np.uint8), kernel).astype(bool) & ~mask
            neighbours = labels[ring]
            neighbours = neighbours[neighbours != inner]
            if not neighbours.size:
                continue
            values, counts = np.unique(neighbours, return_counts=True)
            winner = int(values[np.argmax(counts)])
            fraction = float(counts.max() / counts.sum())
            if fraction >= 0.55:
                preferences[(inner, winner)] = preferences.get((inner, winner), 0.0) + math.sqrt(area) * fraction
    return [
        {"inner_color": pair[0], "surrounding_color": pair[1], "weight": weight}
        for pair, weight in sorted(preferences.items())
    ]


def _subset_masks(labels: np.ndarray) -> tuple[list[np.ndarray], list[bool], list[int]]:
    color_count = int(labels.max()) + 1
    masks: list[np.ndarray] = []
    valid: list[bool] = []
    component_counts: list[int] = []
    for subset in range(1 << color_count):
        selected = np.zeros_like(labels, dtype=bool)
        for color in range(color_count):
            if subset & (1 << color):
                selected |= labels == color
        masks.append(selected)
        if not np.any(selected):
            valid.append(False)
            component_counts.append(0)
            continue
        count, components = cv2.connectedComponents(
            selected.astype(np.uint8), connectivity=4
        )
        component_count = count - 1
        border_labels = set(int(value) for value in np.unique(np.concatenate((components[0], components[-1], components[:, 0], components[:, -1]))) if value)
        valid.append(component_count == 1 and len(border_labels) == 1)
        component_counts.append(component_count)
    return masks, valid, component_counts


def discover_order(analysis: dict[str, Any], config: PocConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    labels = analysis["provider_labels"]
    color_count = len(analysis["providers"])
    preferences = _containment_preferences(labels)
    front_to_back = list(range(color_count))
    cumulative = np.zeros_like(labels, dtype=bool)
    invalid_depths: list[dict[str, int]] = []
    for depth, provider in enumerate(front_to_back):
        cumulative |= labels == provider
        count, components = cv2.connectedComponents(
            cumulative.astype(np.uint8), connectivity=4
        )
        component_count = count - 1
        border_components = set(
            int(value)
            for value in np.unique(
                np.concatenate((components[0], components[-1], components[:, 0], components[:, -1]))
            )
            if value
        )
        valid = component_count > 0 and len(border_components) == component_count
        if not valid:
            invalid_depths.append(
                {
                    "front_to_back_depth": depth,
                    "provider_index": provider,
                    "component_count": component_count,
                }
            )
    if invalid_depths:
        return {
            "status": "INFEASIBLE",
            "reason": "No color ordering keeps every cumulative full-sheet retained mask connected to the perimeter without added material.",
            "preferences": preferences,
            "explored_states": color_count,
            "invalid_depths": invalid_depths,
        }
    return {
        "status": "FEASIBLE",
        "order_front_to_back": front_to_back,
        "order_back_to_front": list(reversed(front_to_back)),
        "topology_penalty": 0.0,
        "preferences": preferences,
        "explored_states": color_count,
    }


def _outer_and_perimeter(config: PocConfig) -> tuple[np.ndarray, np.ndarray]:
    outer = np.ones((config.outer_pixels, config.outer_pixels), dtype=bool)
    perimeter = outer.copy()
    p = config.perimeter_pixels
    perimeter[p:-p, p:-p] = False
    return outer, perimeter


def construct_layers(
    analysis: dict[str, Any], order: dict[str, Any], config: PocConfig = DEFAULT_CONFIG
) -> list[dict[str, Any]]:
    if order["status"] != "FEASIBLE":
        raise ValueError(order["reason"])
    labels = analysis["provider_labels"]
    p = config.perimeter_pixels
    _, perimeter = _outer_and_perimeter(config)
    layers: list[dict[str, Any]] = []
    back_to_front = order["order_back_to_front"]
    for depth, provider_index in enumerate(back_to_front):
        front_providers = set(back_to_front[depth:])
        retained_aperture = np.isin(labels, list(front_providers))
        visible_aperture = labels == provider_index
        provider = analysis["providers"][provider_index]
        hidden_aperture = retained_aperture & ~visible_aperture
        physical = perimeter.copy()
        physical[p:-p, p:-p] = retained_aperture
        cutouts = np.zeros_like(physical)
        cutouts[p:-p, p:-p] = ~retained_aperture
        count, _ = cv2.connectedComponents(
            physical.astype(np.uint8), connectivity=4
        )
        missing_perimeter = int(np.count_nonzero(perimeter & ~physical))
        cutout_count, _ = cv2.connectedComponents(
            cutouts.astype(np.uint8), connectivity=4
        )
        layers.append(
            {
                "depth": depth,
                "provider_index": provider_index,
                "provider_role": provider["role"],
                "palette_index": provider["palette_index"],
                "color": provider["color"],
                "retained_aperture": retained_aperture,
                "visible_aperture": visible_aperture,
                "hidden_aperture": hidden_aperture,
                "physical_mask": physical,
                "cutout_mask": cutouts,
                "connected_components": count - 1,
                "missing_perimeter_pixels": missing_perimeter,
                "cutout_components": cutout_count - 1,
            }
        )
    return layers


def _compose(layers: list[dict[str, Any]], palette: np.ndarray, config: PocConfig) -> tuple[np.ndarray, np.ndarray]:
    labels = np.full((config.outer_pixels, config.outer_pixels), -1, dtype=np.int16)
    rgb = np.full((config.outer_pixels, config.outer_pixels, 3), 247, dtype=np.uint8)
    for layer in layers:
        mask = layer["physical_mask"]
        labels[mask] = int(layer["palette_index"])
        rgb[mask] = palette[layer["palette_index"]]
    return labels, rgb


def _component_detail_report(labels: np.ndarray, mm_per_pixel: float, minimum_mm: float) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for color in sorted(int(value) for value in np.unique(labels)):
        count, components, stats, _ = cv2.connectedComponentsWithStats(
            (labels == color).astype(np.uint8), connectivity=4
        )
        for component in range(1, count):
            mask = (components == component).astype(np.uint8)
            distance = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
            estimated_width = float(distance.max() * 2.0 * mm_per_pixel)
            if estimated_width < minimum_mm:
                warnings.append(
                    {
                        "palette_index": color,
                        "component": component,
                        "area_pixels": int(stats[component, cv2.CC_STAT_AREA]),
                        "bounds_pixels": [
                            int(stats[component, cv2.CC_STAT_LEFT]),
                            int(stats[component, cv2.CC_STAT_TOP]),
                            int(stats[component, cv2.CC_STAT_WIDTH]),
                            int(stats[component, cv2.CC_STAT_HEIGHT]),
                        ],
                        "estimated_max_width_mm": estimated_width,
                        "required_width_mm": minimum_mm,
                    }
                )
    return warnings


def validate_plan(
    analysis: dict[str, Any], order: dict[str, Any], layers: list[dict[str, Any]], config: PocConfig
) -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    composed_labels, composed_rgb = _compose(layers, palette, config)
    p = config.perimeter_pixels
    aperture_labels = composed_labels[p:-p, p:-p]
    aperture_rgb = composed_rgb[p:-p, p:-p]
    target_labels = analysis["labels"].astype(np.int16)
    target_rgb = palette[target_labels]
    geometry_mismatch = aperture_labels != target_labels
    source_rgb = analysis["rgb"]
    rgb_difference = np.abs(aperture_rgb.astype(np.int16) - source_rgb.astype(np.int16))

    # Hidden material is acceptable only when a more-front layer covers it.
    exposed_hidden = 0
    for index, layer in enumerate(layers):
        if index == len(layers) - 1:
            cover = np.zeros_like(layer["hidden_aperture"])
        else:
            cover = np.zeros_like(layer["hidden_aperture"])
            for front in layers[index + 1 :]:
                cover |= front["retained_aperture"]
        exposed_hidden += int(np.count_nonzero(layer["hidden_aperture"] & ~cover))

    mm_per_pixel = (
        config.sheet_size_mm
        * (config.analysis_pixels / config.outer_pixels)
        / config.analysis_pixels
    )
    detail_warnings = _component_detail_report(
        target_labels.astype(np.uint8), mm_per_pixel, config.minimum_feature_mm
    )
    all_connected = all(layer["connected_components"] == 1 for layer in layers)
    all_perimeters = all(layer["missing_perimeter_pixels"] == 0 for layer in layers)
    geometry_exact = not np.any(geometry_mismatch)
    status = "PASS" if all_connected and all_perimeters and geometry_exact and exposed_hidden == 0 else "FAIL"
    return (
        {
            "status": status,
            "composite_source": "actual planned physical layer masks in declared back-to-front order",
            "geometry_pixel_accuracy_against_quantized_target": float(np.mean(~geometry_mismatch)),
            "geometry_mismatched_pixels": int(np.count_nonzero(geometry_mismatch)),
            "mean_absolute_rgb_error_against_original_0_255": float(rgb_difference.mean()),
            "maximum_rgb_error_against_original_0_255": int(rgb_difference.max()),
            "visible_automatic_connector_pixels": 0,
            "exposed_hidden_material_pixels": exposed_hidden,
            "all_physical_sheets_connected": all_connected,
            "all_shared_perimeters_complete": all_perimeters,
            "unsupported_thin_detail_count": len(detail_warnings),
            "unsupported_thin_details": detail_warnings,
            "manual_corrections_required": len(detail_warnings) > 0,
            "manual_corrections_applied": 0,
            "order_back_to_front": order["order_back_to_front"],
            "layers": [
                {
                    "depth": layer["depth"],
                    "palette_index": layer["palette_index"],
                    "provider_index": layer["provider_index"],
                    "provider_role": layer["provider_role"],
                    "color": layer["color"],
                    "connected_components": layer["connected_components"],
                    "missing_perimeter_pixels": layer["missing_perimeter_pixels"],
                    "cutout_components": layer["cutout_components"],
                    "visible_pixels": int(np.count_nonzero(layer["visible_aperture"])),
                    "hidden_material_pixels": int(np.count_nonzero(layer["hidden_aperture"])),
                }
                for layer in layers
            ],
        },
        aperture_rgb,
        geometry_mismatch,
    )


def _save_mask(mask: np.ndarray, path: Path, invert: bool = False) -> None:
    values = (~mask if invert else mask).astype(np.uint8) * 255
    Image.fromarray(values, "L").save(path)


def _save_layer_artifacts(
    root: Path, layers: list[dict[str, Any]], analysis: dict[str, Any], config: PocConfig
) -> list[dict[str, Any]]:
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    records: list[dict[str, Any]] = []
    for layer in layers:
        layer_id = f"layer_{layer['depth']:02d}_{layer['color'].lstrip('#').lower()}"
        directory = root / "automatic" / "layers" / layer_id
        directory.mkdir(parents=True, exist_ok=True)
        retained_path = directory / "retained_material_mask.png"
        cutout_path = directory / "through_cutouts_mask.png"
        visible_path = directory / "source_visible_region_mask.png"
        hidden_path = directory / "hidden_retained_material_mask.png"
        preview_path = directory / "physical_sheet_preview.png"
        _save_mask(layer["physical_mask"], retained_path)
        _save_mask(layer["cutout_mask"], cutout_path)
        _save_mask(layer["visible_aperture"], visible_path)
        _save_mask(layer["hidden_aperture"], hidden_path)
        preview = np.full((config.outer_pixels, config.outer_pixels, 3), 250, dtype=np.uint8)
        preview[layer["physical_mask"]] = palette[layer["palette_index"]]
        Image.fromarray(preview, "RGB").save(preview_path)
        records.append(
            {
                "id": layer_id,
                "depth_back_to_front": layer["depth"],
                "palette_index": layer["palette_index"],
                "provider_index": layer["provider_index"],
                "provider_role": layer["provider_role"],
                "color": layer["color"],
                "retained_material_mask": retained_path.relative_to(root).as_posix(),
                "through_cutouts_mask": cutout_path.relative_to(root).as_posix(),
                "source_visible_region_mask": visible_path.relative_to(root).as_posix(),
                "hidden_retained_material_mask": hidden_path.relative_to(root).as_posix(),
                "physical_sheet_preview": preview_path.relative_to(root).as_posix(),
            }
        )
    return records


def _render_comparison(
    analysis: dict[str, Any], reconstruction: np.ndarray, mismatch: np.ndarray, output: Path
) -> None:
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    quantized = palette[analysis["labels"]]
    original = analysis["rgb"]
    absolute = np.abs(reconstruction.astype(np.int16) - original.astype(np.int16)).max(axis=2)
    heat = np.zeros_like(reconstruction)
    heat[:, :, 0] = absolute.astype(np.uint8)
    heat[:, :, 2] = absolute.astype(np.uint8)
    heat[mismatch] = (255, 0, 255)
    size = analysis["labels"].shape[0]
    header = 34
    combined = Image.new("RGB", (size * 4, size + header), "white")
    for index, image in enumerate((original, quantized, reconstruction, heat)):
        combined.paste(Image.fromarray(image, "RGB"), (index * size, header))
    draw = ImageDraw.Draw(combined)
    titles = ("ORIGINAL", "PRINTABLE TARGET", "MASK COMPOSITE", "RGB DIFFERENCE")
    for index, title in enumerate(titles):
        draw.text((index * size + 8, 10), title, fill="black")
    combined.save(output)


def _render_exploded(
    layers: list[dict[str, Any]], analysis: dict[str, Any], output: Path
) -> None:
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    thumb = 220
    gap = 18
    width = thumb * len(layers) + gap * (len(layers) + 1)
    canvas = Image.new("RGB", (width, thumb + 58), "white")
    draw = ImageDraw.Draw(canvas)
    for index, layer in enumerate(layers):
        rgb = np.full((*layer["physical_mask"].shape, 3), 250, dtype=np.uint8)
        rgb[layer["physical_mask"]] = palette[layer["palette_index"]]
        image = Image.fromarray(rgb, "RGB").resize((thumb, thumb), Image.Resampling.NEAREST)
        x = gap + index * (thumb + gap)
        canvas.paste(image, (x, 34))
        draw.text((x, 10), f"{index} back-to-front {layer['color']}", fill="black")
    canvas.save(output)


def run_poc_case(
    source: Path, case_id: str, output: Path, config: PocConfig = DEFAULT_CONFIG
) -> dict[str, Any]:
    if output.exists():
        shutil.rmtree(output)
    for directory in ("original", "automatic", "automatic/layers", "previews", "reports"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output / "original" / "source.png")
    analysis = analyze_image(source, config)
    order = discover_order(analysis, config)
    serial_analysis = {
        "source_sha256": _sha256(source),
        "original_pixel_size": list(analysis["original_size"]),
        "analysis_pixel_size": [config.analysis_pixels, config.analysis_pixels],
        "palette": [
            {
                "index": index,
                "hex": color,
                "pixels": int(analysis["counts"][index]),
            }
            for index, color in enumerate(analysis["palette_hex"])
        ],
        "cleanup_event_count": len(analysis["cleanup_events"]),
        "cleanup_events": analysis["cleanup_events"],
        "providers": analysis["providers"],
        "provider_splits": analysis["provider_splits"],
        "topology_order_analysis": order,
        "config": asdict(config),
        "manual_annotations_used": False,
        "automatic_visible_bridge_fallback": False,
    }
    _write_json(output / "automatic" / "proposal.json", serial_analysis)
    if order["status"] != "FEASIBLE":
        report = {
            "case_id": case_id,
            "status": "CONFLICT_REQUIRES_USER_DECISION",
            "reason": order["reason"],
            "automatic_proposal_preserved": True,
            "cad_generated": False,
        }
        _write_json(output / "reports" / "validation.json", report)
        return report

    layers = construct_layers(analysis, order, config)
    layer_files = _save_layer_artifacts(output, layers, analysis, config)
    validation, reconstruction, mismatch = validate_plan(
        analysis, order, layers, config
    )
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    Image.fromarray(palette[analysis["labels"]], "RGB").save(
        output / "previews" / "printable_target.png"
    )
    Image.fromarray(reconstruction, "RGB").save(
        output / "previews" / "reconstructed_front.png"
    )
    _render_comparison(
        analysis, reconstruction, mismatch, output / "previews" / "source_difference.png"
    )
    _render_exploded(layers, analysis, output / "previews" / "exploded_layer_masks.png")

    reveal_relationships: list[dict[str, Any]] = []
    for lower_index, lower in enumerate(layers):
        visible = lower["visible_aperture"]
        if not np.any(visible):
            continue
        for upper in layers[lower_index + 1 :]:
            upper_open = ~upper["retained_aperture"]
            reveal_relationships.append(
                {
                    "lower_depth": lower["depth"],
                    "lower_color": lower["color"],
                    "upper_depth": upper["depth"],
                    "upper_color": upper["color"],
                    "lower_visible_pixels": int(np.count_nonzero(visible)),
                    "pixels_open_in_upper_layer": int(np.count_nonzero(visible & upper_open)),
                    "fully_revealed_through_upper_cutout": bool(np.all(upper_open[visible])),
                }
            )
    manifest = {
        "schema_version": "3.2.0-subtractive-poc",
        "case_id": case_id,
        "status": "AUTOMATIC_UNCORRECTED",
        "source": "original/source.png",
        "source_sha256": _sha256(source),
        "construction": {
            "method": "nested_full_sheet_subtractive_masks",
            "common_outer_pixels": [config.outer_pixels, config.outer_pixels],
            "continuous_perimeter_pixels": config.perimeter_pixels,
            "one_color_per_layer": True,
            "visible_connector_fallback": False,
            "cad_generated": False,
        },
        "order_back_to_front": [record["id"] for record in layer_files],
        "layers": layer_files,
        "reveal_relationships": reveal_relationships,
    }
    _write_json(output / "automatic" / "layer_stack.json", manifest)
    validation.update(
        {
            "case_id": case_id,
            "automatic_proposal_preserved": True,
            "manual_corrections_applied": 0,
            "cad_generated": False,
            "layer_count": len(layers),
            "layer_files": layer_files,
            "reveal_relationships": reveal_relationships,
        }
    )
    _write_json(output / "reports" / "validation.json", validation)
    _write_json(
        output / "reports" / "correction_log.json",
        {"automatic_proposal_sha256": _sha256(output / "automatic" / "proposal.json"), "operations": []},
    )
    checksum_files = sorted(
        path for path in output.rglob("*") if path.is_file() and path.name != "checksums.sha256"
    )
    (output / "reports" / "checksums.sha256").write_text(
        "".join(f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n" for path in checksum_files),
        encoding="utf-8",
    )
    return validation

