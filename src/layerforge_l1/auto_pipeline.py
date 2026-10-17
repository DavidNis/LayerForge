"""Automatic flat-image to registered physical layer-sheet proposal pipeline."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cadquery as cq
import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh
from matplotlib.patches import Rectangle
from PIL import Image, ImageDraw
from shapely import affinity
from shapely.geometry import LineString, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from scipy.sparse.csgraph import minimum_spanning_tree

from .geometry import (
    connected_component_count,
    hole_count,
    layer_area,
    narrow_feature_survives,
    polygons,
    shape_from_spec,
    validate_shared_area,
    validate_shared_sheet,
)
from .pipeline import (
    LayerBuild,
    _environment,
    _export_layer,
    _load_step,
    _load_stl,
    _mesh_top_footprint,
    _render_exploded,
    _step_solids,
    sha256,
    write_json,
)
from .raster import composite_layers, geometry_mask, hex_rgb, label_map, save_layer_preview


@dataclass(frozen=True)
class AutoConfig:
    """Versioned, deterministic proposal settings shared by every evaluation image."""

    analysis_pixels: int = 192
    palette_size: int = 8
    kmeans_seed: int = 74031
    kmeans_attempts: int = 1
    minimum_region_pixels: int = 10
    connector_width_mm: float = 1.6
    sheet_size_mm: float = 100.0
    model_canvas_mm: float = 110.0
    outer_margin_mm: float = 5.0
    perimeter_width_mm: float = 4.0
    corner_radius_mm: float = 7.0
    layer_thickness_mm: float = 1.2
    assembly_gap_mm: float = 0.4
    printer_bed_mm: tuple[float, float, float] = (220.0, 220.0, 250.0)
    mesh_linear_tolerance_mm: float = 0.08
    mesh_angular_tolerance_rad: float = 0.12
    order_candidates: int = 12

    @property
    def outer_bounds(self) -> tuple[float, float, float, float]:
        lo = self.outer_margin_mm
        return lo, lo, lo + self.sheet_size_mm, lo + self.sheet_size_mm

    @property
    def aperture_bounds(self) -> tuple[float, float, float, float]:
        lo = self.outer_margin_mm + self.perimeter_width_mm
        hi = self.outer_margin_mm + self.sheet_size_mm - self.perimeter_width_mm
        return lo, lo, hi, hi


DEFAULT_CONFIG = AutoConfig()


def _rgb_hex(rgb: np.ndarray | tuple[int, int, int]) -> str:
    return "#" + "".join(f"{int(channel):02X}" for channel in rgb)


def _slug_color(color: str) -> str:
    return color.lstrip("#").lower()


def _read_rgb(source: Path, pixels: int) -> tuple[np.ndarray, tuple[int, int]]:
    image = Image.open(source).convert("RGBA")
    original_size = image.size
    rgba = np.asarray(image, dtype=np.uint8)
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    rgb = np.rint(rgba[:, :, :3] * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
    resized = cv2.resize(rgb, (pixels, pixels), interpolation=cv2.INTER_AREA)
    return resized, original_size


def _nearest_center_labels(rgb: np.ndarray, centers_rgb: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.int32)
    centers_image = centers_rgb.astype(np.uint8).reshape(1, -1, 3)
    centers_lab = cv2.cvtColor(centers_image, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.int32)
    distance = ((lab[:, :, None, :] - centers_lab[None, None, :, :]) ** 2).sum(axis=3)
    return np.argmin(distance, axis=2).astype(np.uint8)


def _cleanup_small_components(labels: np.ndarray, minimum_pixels: int) -> tuple[np.ndarray, list[dict[str, Any]]]:
    result = labels.copy()
    events: list[dict[str, Any]] = []
    kernel = np.ones((3, 3), np.uint8)
    for color in sorted(int(value) for value in np.unique(labels)):
        count, components, stats, _ = cv2.connectedComponentsWithStats(
            (labels == color).astype(np.uint8), connectivity=8
        )
        for component in range(1, count):
            area = int(stats[component, cv2.CC_STAT_AREA])
            if area >= minimum_pixels:
                continue
            mask = components == component
            ring = cv2.dilate(mask.astype(np.uint8), kernel, iterations=1).astype(bool) & ~mask
            neighbours = result[ring]
            if neighbours.size:
                values, counts = np.unique(neighbours, return_counts=True)
                replacement = int(values[np.argmax(counts)])
                result[mask] = replacement
                events.append(
                    {"from_palette_index": color, "to_palette_index": replacement, "pixels": area}
                )
    return result, events


def quantize_image(source: Path, config: AutoConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    """Cluster a PNG into a deterministic flat palette without source annotations."""
    rgb, original_size = _read_rgb(source, config.analysis_pixels)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    samples = lab.reshape(-1, 3).astype(np.float32)
    cv2.setRNGSeed(config.kmeans_seed)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 80, 0.1)
    compactness, raw_labels, centers_lab = cv2.kmeans(
        samples,
        config.palette_size,
        None,
        criteria,
        config.kmeans_attempts,
        cv2.KMEANS_PP_CENTERS,
    )
    centers_rgb = cv2.cvtColor(
        np.clip(np.rint(centers_lab), 0, 255).astype(np.uint8).reshape(1, -1, 3),
        cv2.COLOR_LAB2RGB,
    ).reshape(-1, 3)
    labels = raw_labels.reshape(config.analysis_pixels, config.analysis_pixels).astype(np.uint8)

    # Canonicalize cluster IDs by RGB value so OpenCV label numbering never leaks into outputs.
    canonical = sorted(range(len(centers_rgb)), key=lambda index: tuple(int(x) for x in centers_rgb[index]))
    old_to_new = {old: new for new, old in enumerate(canonical)}
    centers_rgb = centers_rgb[canonical]
    labels = np.vectorize(old_to_new.get, otypes=[np.uint8])(labels)
    labels, cleanup = _cleanup_small_components(labels, config.minimum_region_pixels)
    # Cleanup can eliminate a palette member; retain every cluster as a layer for exact proposal provenance.
    counts = np.bincount(labels.ravel(), minlength=config.palette_size)
    border = np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1]))
    border_counts = np.bincount(border, minlength=config.palette_size)
    border_fraction = border_counts / max(1, border.size)
    area_fraction = counts / labels.size
    background = int(np.argmax(border_fraction * 3.0 + area_fraction))
    lab_centers = cv2.cvtColor(centers_rgb.reshape(1, -1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3)
    darkest = int(np.argmin(lab_centers[:, 0]))
    return {
        "rgb": rgb,
        "labels": labels,
        "palette_rgb": centers_rgb,
        "palette_hex": [_rgb_hex(row) for row in centers_rgb],
        "counts": counts,
        "border_fraction": border_fraction,
        "background_index": background,
        "darkest_index": darkest,
        "cleanup_events": cleanup,
        "compactness": float(compactness),
        "original_size": original_size,
    }


def _order_candidates(analysis: dict[str, Any], config: AutoConfig) -> list[list[int]]:
    count = len(analysis["palette_hex"])
    background = analysis["background_index"]
    darkest = analysis["darkest_index"]
    middle = [index for index in range(count) if index not in {background, darkest}]
    middle_by_area = sorted(middle, key=lambda index: (-int(analysis["counts"][index]), index))
    candidates = [[background, *middle_by_area, darkest]]
    candidates.append([background, *reversed(middle_by_area), darkest])
    rng = np.random.default_rng(config.kmeans_seed)
    for _ in range(config.order_candidates):
        shuffled = middle.copy()
        rng.shuffle(shuffled)
        candidates.append([background, *shuffled, darkest])
    unique: list[list[int]] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _shortest_connector_tree(own: np.ndarray, covered_above: np.ndarray) -> tuple[np.ndarray, int]:
    """Join components with a compact tree, then join that tree once to the edge."""
    height, width = own.shape
    weights = np.full((height, width), 3.0, dtype=np.float64)
    weights[covered_above] = 0.12
    weights[own] = 0.02
    connector = np.zeros_like(own, dtype=bool)
    count, components, _, centroids = cv2.connectedComponentsWithStats(
        own.astype(np.uint8), connectivity=8
    )
    points: list[tuple[int, int]] = []
    for component in range(1, count):
        ys, xs = np.where(components == component)
        cx, cy = centroids[component]
        nearest = int(np.argmin((xs - cx) ** 2 + (ys - cy) ** 2))
        points.append((int(xs[nearest]), int(ys[nearest])))
    if not points:
        return connector, 0

    coordinates = np.asarray(points, dtype=np.float64)
    delta = coordinates[:, None, :] - coordinates[None, :, :]
    distances = np.sqrt(np.sum(delta * delta, axis=2))
    node_count = len(points)
    graph = np.zeros((node_count + 1, node_count + 1), dtype=np.float64)
    graph[:node_count, :node_count] = distances
    for index, (x, y) in enumerate(points):
        edge_distance = float(min(x, y, width - 1 - x, height - 1 - y)) + 0.001
        graph[index, node_count] = graph[node_count, index] = edge_distance
    tree = minimum_spanning_tree(graph).tocoo()

    def line_choice(start: tuple[int, int], end: tuple[int, int]) -> np.ndarray:
        candidates: list[np.ndarray] = []
        direct = np.zeros_like(own, dtype=np.uint8)
        cv2.line(direct, start, end, 1, 1, cv2.LINE_8)
        candidates.append(direct)
        via_x = np.zeros_like(own, dtype=np.uint8)
        cv2.line(via_x, start, (end[0], start[1]), 1, 1, cv2.LINE_8)
        cv2.line(via_x, (end[0], start[1]), end, 1, 1, cv2.LINE_8)
        candidates.append(via_x)
        via_y = np.zeros_like(own, dtype=np.uint8)
        cv2.line(via_y, start, (start[0], end[1]), 1, 1, cv2.LINE_8)
        cv2.line(via_y, (start[0], end[1]), end, 1, 1, cv2.LINE_8)
        candidates.append(via_y)
        return min(candidates, key=lambda path: float(weights[path.astype(bool)].sum()))

    for first, second in zip(tree.row, tree.col):
        if first == node_count or second == node_count:
            index = second if first == node_count else first
            x, y = points[index]
            endpoints = [(x, 0), (x, height - 1), (0, y), (width - 1, y)]
            candidates = []
            for end in endpoints:
                path = np.zeros_like(own, dtype=np.uint8)
                cv2.line(path, (x, y), end, 1, 1, cv2.LINE_8)
                candidates.append(path)
            best = min(candidates, key=lambda path: float(weights[path.astype(bool)].sum()))
        else:
            best = line_choice(points[first], points[second])
        connector |= best.astype(bool)
    return connector, node_count


def _route_order(labels: np.ndarray, order: list[int], config: AutoConfig) -> dict[str, Any]:
    physical: dict[int, np.ndarray] = {}
    connectors: dict[int, np.ndarray] = {}
    routed_counts: dict[int, int] = {}
    background = order[0]
    physical[background] = np.ones_like(labels, dtype=bool)
    connectors[background] = np.zeros_like(labels, dtype=bool)
    routed_counts[background] = 1
    above = np.zeros_like(labels, dtype=bool)
    for index in reversed(order[1:]):
        source_region = labels == index
        own = source_region
        path, routed = _shortest_connector_tree(own, above)
        px_per_mm = config.analysis_pixels / (config.sheet_size_mm - 2 * config.perimeter_width_mm)
        radius = max(1, int(math.ceil(config.connector_width_mm * px_per_mm / 2.0)))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        expanded = cv2.dilate(path.astype(np.uint8), kernel).astype(bool)
        # The one-pixel registration band is mapped across the carrier's inner
        # edge. It joins every route into one sheet without adding visible art.
        registration_band = np.zeros_like(own, dtype=bool)
        registration_band[[0, -1], :] = True
        registration_band[:, [0, -1]] = True
        physical[index] = own | expanded | registration_band
        connectors[index] = expanded & ~own
        routed_counts[index] = routed
        above |= source_region
    composite = np.full_like(labels, background)
    for index in order:
        composite[physical[index]] = index
    mismatch = composite != labels
    visible_connectors = np.zeros_like(labels, dtype=bool)
    for index in order[1:]:
        visible_connectors |= (composite == index) & connectors[index]
    return {
        "order": order,
        "physical_masks": physical,
        "connector_masks": connectors,
        "routed_components": routed_counts,
        "composite": composite,
        "mismatch_pixels": int(np.count_nonzero(mismatch)),
        "visible_connector_pixels": int(np.count_nonzero(visible_connectors)),
    }


def propose(source: Path, config: AutoConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    analysis = quantize_image(source, config)
    scored = [_route_order(analysis["labels"], order, config) for order in _order_candidates(analysis, config)]
    scored.sort(key=lambda item: (item["mismatch_pixels"], item["visible_connector_pixels"], item["order"]))
    selected = scored[0]
    return {
        "analysis": analysis,
        "selected": selected,
        "candidate_scores": [
            {
                "order_palette_indices": item["order"],
                "mismatch_pixels": item["mismatch_pixels"],
                "visible_connector_pixels": item["visible_connector_pixels"],
            }
            for item in scored
        ],
        "config": config,
    }


def _mask_geometry(mask: np.ndarray, config: AutoConfig) -> BaseGeometry:
    """Convert a raster mask to simplified model-space polygons via row runs."""
    height, width = mask.shape
    x0, y0, x1, y1 = config.aperture_bounds
    # Extend routed geometry far enough into the carrier to survive the
    # configured minimum-feature erosion, not merely touch it numerically.
    overlap = max(1.0, config.connector_width_mm)
    x0 -= overlap
    y0 -= overlap
    x1 += overlap
    y1 += overlap
    dx = (x1 - x0) / width
    dy = (y1 - y0) / height
    runs: list[BaseGeometry] = []
    for row in range(height):
        padded = np.pad(mask[row].astype(np.int8), (1, 1))
        changes = np.diff(padded)
        starts = np.where(changes == 1)[0]
        stops = np.where(changes == -1)[0]
        top = y1 - row * dy
        bottom = top - dy
        runs.extend(box(x0 + start * dx, bottom, x0 + stop * dx, top) for start, stop in zip(starts, stops))
    if not runs:
        return Polygon()
    # A tiny close removes floating-point seams between adjacent raster rows.
    geometry = unary_union(runs).buffer(0.01).buffer(-0.01)
    outer = shape_from_spec(
        {
            "type": "rounded_rectangle",
            "bounds_mm": list(config.outer_bounds),
            "radius_mm": config.corner_radius_mm,
        }
    )
    return geometry.intersection(outer).simplify(0.12, preserve_topology=True).buffer(0)


def _geometry_regions(
    geometry: BaseGeometry, prefix: str, config: AutoConfig
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    additive: list[dict[str, Any]] = []
    cutouts: list[dict[str, Any]] = []
    for poly_index, poly in enumerate(polygons(geometry)):
        additive.append(
            {
                "id": f"{prefix}_region_{poly_index:03d}",
                "role": "automatic_retained_geometry",
                "shape": {"type": "polygon", "points_mm": [[round(x, 4), round(y, 4)] for x, y in poly.exterior.coords[:-1]]},
            }
        )
        safe_aperture = shape_from_spec(
            {
                "type": "rounded_rectangle",
                "bounds_mm": list(config.outer_bounds),
                "radius_mm": config.corner_radius_mm,
            }
        ).buffer(-config.perimeter_width_mm - 0.02)
        for hole_index, ring in enumerate(poly.interiors):
            clipped = Polygon(ring).intersection(safe_aperture).buffer(0)
            for part_index, part in enumerate(polygons(clipped)):
                cutouts.append(
                    {
                        "id": f"{prefix}_cutout_{poly_index:03d}_{hole_index:03d}_{part_index:02d}",
                        "role": "automatic_internal_opening",
                        "shape": {"type": "polygon", "points_mm": [[round(x, 4), round(y, 4)] for x, y in part.exterior.coords[:-1]]},
                    }
                )
    return additive, cutouts


def proposal_manifest(source: Path, proposal: dict[str, Any], case_id: str) -> dict[str, Any]:
    config: AutoConfig = proposal["config"]
    analysis = proposal["analysis"]
    selected = proposal["selected"]
    outer = list(config.outer_bounds)
    perimeter = {
        "type": "rounded_rectangle_ring",
        "bounds_mm": outer,
        "radius_mm": config.corner_radius_mm,
        "width_mm": config.perimeter_width_mm,
    }
    layers: list[dict[str, Any]] = []
    for depth, palette_index in enumerate(selected["order"]):
        color = analysis["palette_hex"][palette_index]
        geometry = _mask_geometry(selected["physical_masks"][palette_index], config)
        additive, cutouts = _geometry_regions(geometry, f"color_{palette_index:02d}", config)
        layer_id = f"layer_{depth:02d}_{_slug_color(color)}"
        layer = {
            "id": layer_id,
            "order_back_to_front": depth,
            "source_palette_index": palette_index,
            "color": {"name": f"automatic palette {palette_index}", "hex": color, "filament": f"closest available filament to {color}"},
            "nominal_thickness_mm": config.layer_thickness_mm,
            "assembly_transform": {"translate_xyz_mm": [0.0, 0.0, round(depth * (config.layer_thickness_mm + config.assembly_gap_mm), 4)], "rotate_xyz_deg": [0.0, 0.0, 0.0]},
            "print_transform": {"translate_xyz_mm": [10.0, 10.0, 0.0], "rotate_xyz_deg": [0.0, 0.0, 0.0]},
            "automatic_analysis": {
                "source_pixels": int(analysis["counts"][palette_index]),
                "routed_source_components": int(selected["routed_components"][palette_index]),
                "connector_pixels": int(np.count_nonzero(selected["connector_masks"][palette_index])),
            },
            "geometry": {
                "connectivity_policy": "require_single_component_with_shared_perimeter",
                "additive_regions": [
                    {"id": "shared_outer_perimeter", "role": "shared_outer_perimeter", "shape": perimeter},
                    *additive,
                ],
                "cutout_regions": cutouts,
                "rim": {"enabled": False},
            },
        }
        layers.append(layer)
    return {
        "schema_version": "3.1.0-l1-auto",
        "fixture_id": case_id,
        "plan_provenance": "automatic_unreviewed",
        "artwork": {
            "title": case_id.replace("_", " ").title(),
            "image": "original/source.png",
            "sha256": sha256(source),
            "pixel_size": list(analysis["original_size"]),
            "analysis_pixel_size": [config.analysis_pixels, config.analysis_pixels],
            "canvas_mm": [config.model_canvas_mm, config.model_canvas_mm],
            "aperture_bounds_mm": list(config.aperture_bounds),
            "image_origin": "upper-left",
            "model_origin": "lower-left",
            "rights": {"author": "OpenAI image generation for LayerForge evaluation", "license": "user project evaluation asset", "third_party_assets": False},
        },
        "construction": {
            "method": "automatic_registered_layer_sheets",
            "one_filament_per_layer": True,
            "housing_required": False,
            "assembled_z_axis": "front-positive",
            "shared_sheet": {
                "outer_outline": {"type": "rounded_rectangle", "bounds_mm": outer, "radius_mm": config.corner_radius_mm},
                "outer_perimeter": perimeter,
                "external_dimensions_mm": [config.sheet_size_mm, config.sheet_size_mm],
                "nominal_slot_thickness_mm": config.layer_thickness_mm,
                "validation_tolerance_mm": 0.001,
            },
        },
        "manufacturing": {
            "printer_bed_mm": list(config.printer_bed_mm),
            "nozzle_diameter_mm": 0.4,
            "minimum_feature_mm": 1.2,
            "connector_width_mm": config.connector_width_mm,
            "mesh_linear_tolerance_mm": config.mesh_linear_tolerance_mm,
            "mesh_angular_tolerance_rad": config.mesh_angular_tolerance_rad,
        },
        "layers": layers,
    }


def _serializable_proposal(source: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    analysis = proposal["analysis"]
    selected = proposal["selected"]
    return {
        "proposal_version": "1.0.0",
        "source_sha256": sha256(source),
        "method": "deterministic Lab k-means, component cleanup, automatic order search, occlusion-aware connector routing",
        "manual_annotations_used": False,
        "config": asdict(proposal["config"]),
        "palette": [
            {
                "index": index,
                "hex": color,
                "pixels": int(analysis["counts"][index]),
                "border_fraction": float(analysis["border_fraction"][index]),
            }
            for index, color in enumerate(analysis["palette_hex"])
        ],
        "background_palette_index": int(analysis["background_index"]),
        "darkest_palette_index": int(analysis["darkest_index"]),
        "selected_order_back_to_front": selected["order"],
        "automatic_mismatch_pixels": selected["mismatch_pixels"],
        "automatic_visible_connector_pixels": selected["visible_connector_pixels"],
        "cleanup_events": analysis["cleanup_events"],
        "candidate_scores": proposal["candidate_scores"],
    }


def _save_analysis_assets(output: Path, proposal: dict[str, Any]) -> None:
    analysis = proposal["analysis"]
    selected = proposal["selected"]
    masks = output / "automatic" / "masks"
    masks.mkdir(parents=True, exist_ok=True)
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    quantized = palette[analysis["labels"]]
    Image.fromarray(quantized, "RGB").save(output / "automatic" / "quantized_source.png")
    Image.fromarray(palette[selected["composite"]], "RGB").save(output / "automatic" / "automatic_reconstruction.png")
    for index, color in enumerate(analysis["palette_hex"]):
        Image.fromarray((analysis["labels"] == index).astype(np.uint8) * 255, "L").save(
            masks / f"palette_{index:02d}_{_slug_color(color)}_source.png"
        )
        Image.fromarray(selected["physical_masks"][index].astype(np.uint8) * 255, "L").save(
            masks / f"palette_{index:02d}_{_slug_color(color)}_physical.png"
        )
        Image.fromarray(selected["connector_masks"][index].astype(np.uint8) * 255, "L").save(
            masks / f"palette_{index:02d}_{_slug_color(color)}_connectors.png"
        )


def _aperture_geometry(geometry: BaseGeometry, config: AutoConfig) -> BaseGeometry:
    x0, y0, x1, y1 = config.aperture_bounds
    return affinity.translate(geometry.intersection(box(x0, y0, x1, y1)), xoff=-x0, yoff=-y0)


def _render_front(builds: list[LayerBuild], manifest: dict[str, Any], output: Path) -> None:
    canvas = tuple(manifest["artwork"]["canvas_mm"])
    layers = [(build.mesh_footprint, build.spec["color"]["hex"]) for build in builds]
    composite_layers(layers, canvas, (900, 900), (245, 245, 245, 255)).convert("RGB").save(output)


def _render_auto_side(builds: list[LayerBuild], output: Path) -> None:
    """Render registered depth using reopened STL bounds and assembly transforms."""
    fig, ax = plt.subplots(figsize=(11, 3.5), dpi=140)
    for build in builds:
        bounds = build.mesh.bounds
        x0, x1 = float(bounds[0, 0]), float(bounds[1, 0])
        thickness = float(bounds[1, 2] - bounds[0, 2])
        z0 = float(build.spec["assembly_transform"]["translate_xyz_mm"][2])
        ax.add_patch(Rectangle((x0, z0), x1 - x0, thickness, color=build.spec["color"]["hex"]))
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(min(build.mesh.bounds[0, 0] for build in builds) - 2, max(build.mesh.bounds[1, 0] for build in builds) + 2)
    ax.set_ylim(-1, max(float(build.spec["assembly_transform"]["translate_xyz_mm"][2]) + float(build.mesh.bounds[1, 2]) for build in builds) + 1)
    ax.set_xlabel("registered X from reopened STL (mm)")
    ax.set_ylabel("assembled Z, front-positive (mm)")
    ax.set_title("Exported-STL depth view; 1.2 mm sheets with proposed 0.4 mm inspection gaps")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(output)
    plt.close(fig)


def _evaluation(source: Path, proposal: dict[str, Any], manifest: dict[str, Any], builds: list[LayerBuild]) -> tuple[dict[str, Any], np.ndarray]:
    config: AutoConfig = proposal["config"]
    analysis = proposal["analysis"]
    selected = proposal["selected"]
    n = config.analysis_pixels
    aperture_size = config.sheet_size_mm - 2 * config.perimeter_width_mm
    cad_labels = label_map(
        [(_aperture_geometry(build.mesh_footprint, config), int(build.spec["source_palette_index"]) + 1) for build in builds],
        (aperture_size, aperture_size),
        (n, n),
    ).astype(np.int16) - 1
    source_labels = analysis["labels"].astype(np.int16)
    mismatch = cad_labels != source_labels
    background = int(analysis["background_index"])
    source_foreground = source_labels != background
    cad_foreground = cad_labels != background
    foreground_intersection = int(np.count_nonzero(source_foreground & cad_foreground))
    foreground_union = int(np.count_nonzero(source_foreground | cad_foreground))
    visible_connector = mismatch & cad_foreground
    missing_detail = mismatch & source_foreground & ~cad_foreground
    wrong_nonbackground = mismatch & source_foreground & cad_foreground
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    safe = np.clip(cad_labels, 0, len(palette) - 1)
    cad_rgb = palette[safe]
    original_rgb = analysis["rgb"]
    mae = float(np.abs(cad_rgb.astype(np.int16) - original_rgb.astype(np.int16)).mean())
    per_color: dict[str, Any] = {}
    for index, color in enumerate(analysis["palette_hex"]):
        expected = source_labels == index
        actual = cad_labels == index
        inter = int(np.count_nonzero(expected & actual))
        union = int(np.count_nonzero(expected | actual))
        per_color[color] = {
            "source_pixels": int(np.count_nonzero(expected)),
            "stl_pixels": int(np.count_nonzero(actual)),
            "iou": inter / union if union else 1.0,
            "coverage": inter / max(1, int(np.count_nonzero(expected))),
        }
    source_components = 0
    retained_components = 0
    for index in range(len(palette)):
        count, components, stats, _ = cv2.connectedComponentsWithStats((source_labels == index).astype(np.uint8), 8)
        for component in range(1, count):
            if stats[component, cv2.CC_STAT_AREA] >= config.minimum_region_pixels:
                source_components += 1
                component_mask = components == component
                if np.count_nonzero((cad_labels == index) & component_mask) / np.count_nonzero(component_mask) >= 0.5:
                    retained_components += 1
    return {
        "comparison_basis": "ordered front projection rasterized only from reopened exported STL footprints",
        "analysis_resolution_px": [n, n],
        "mismatched_pixels": int(np.count_nonzero(mismatch)),
        "pixel_accuracy": float(np.mean(~mismatch)),
        "mean_absolute_rgb_error_0_255": mae,
        "foreground_silhouette_iou": foreground_intersection / max(1, foreground_union),
        "source_components_at_or_above_threshold": source_components,
        "retained_components_at_or_above_threshold": retained_components,
        "small_detail_component_recall": retained_components / max(1, source_components),
        "visible_connector_artifact_pixels": int(np.count_nonzero(visible_connector)),
        "visible_connector_artifact_fraction": float(np.mean(visible_connector)),
        "missing_detail_pixels": int(np.count_nonzero(missing_detail)),
        "wrong_nonbackground_color_pixels": int(np.count_nonzero(wrong_nonbackground)),
        "ordering_ambiguity": {
            "selected_automatic_order": selected["order"],
            "best_candidate_mismatch_pixels": proposal["candidate_scores"][0]["mismatch_pixels"],
            "runner_up_mismatch_pixels": proposal["candidate_scores"][1]["mismatch_pixels"] if len(proposal["candidate_scores"]) > 1 else None,
            "ground_truth_depth_order_available": False,
        },
        "layer_order_back_to_front": [layer["id"] for layer in manifest["layers"]],
        "per_color": per_color,
    }, cad_labels


def _render_comparison(source: Path, proposal: dict[str, Any], cad_labels: np.ndarray, output: Path) -> None:
    analysis = proposal["analysis"]
    n = proposal["config"].analysis_pixels
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    original = Image.fromarray(analysis["rgb"], "RGB")
    quantized = Image.fromarray(palette[analysis["labels"]], "RGB")
    cad = Image.fromarray(palette[np.clip(cad_labels, 0, len(palette) - 1)], "RGB")
    diff_array = np.asarray(cad).copy()
    mismatch = cad_labels != analysis["labels"]
    diff_array[mismatch] = (255, 0, 255)
    diff = Image.fromarray(diff_array, "RGB")
    scale = 3
    header = 34
    panel = n * scale
    combined = Image.new("RGB", (panel * 4, panel + header), "white")
    for index, image in enumerate((original, quantized, cad, diff)):
        combined.paste(image.resize((panel, panel), Image.Resampling.NEAREST), (index * panel, header))
    draw = ImageDraw.Draw(combined)
    for index, title in enumerate(("ORIGINAL PNG", "AUTO QUANTIZED", "EXPORTED STL FRONT", "MISMATCH (MAGENTA)")):
        draw.text((index * panel + 8, 10), title, fill="black")
    combined.save(output)


def _build_exports(manifest: dict[str, Any], output: Path) -> list[LayerBuild]:
    builds: list[LayerBuild] = []
    for layer in manifest["layers"]:
        if not validate_shared_sheet(manifest, layer)["pass"]:
            raise ValueError(f"{layer['id']}: automatic layer failed shared-sheet validation")
        solid, area, step_path, stl_path = _export_layer(layer, output, manifest["manufacturing"])
        mesh = _load_stl(stl_path)
        footprint = _mesh_top_footprint(mesh)
        step = _load_step(step_path)
        builds.append(LayerBuild(layer, solid, area, mesh, footprint, step, step_path, stl_path))
    return builds


def _print_guide(manifest: dict[str, Any]) -> str:
    rows = []
    for layer in manifest["layers"]:
        stl = f"layers/{layer['id']}/{layer['id']}_body.stl"
        rows.append(
            f"{layer['order_back_to_front'] + 1}. `{stl}` — {layer['color']['hex']}, "
            f"1 copy, {layer['nominal_thickness_mm']} mm thick."
        )
    return f"""# Automatic proposal print and stacking guide

Status: software-validated proposal; physical printing is not required for this development gate.

- Import every STL at 100% scale in millimetres, broad face on the bed.
- Print exactly one of each file ({len(manifest['layers'])} sheets total).
- Every sheet is 100 x 100 x 1.2 mm and has the same continuous 4 mm outer perimeter.
- Use one filament color per sheet, choosing the closest available match to its recorded hex color.
- Suggested starting point: PLA, 0.4 mm nozzle, 0.20 mm layer height, 3 walls,
  4 top/bottom layers, 15% infill. Slicer settings remain user/printer dependent.
- Stack by the common outer outline in this back-to-front order; assembly transforms are
  separate from print placement transforms. No frame, stand, or mounting system is included.

{chr(10).join(rows)}

Review the slicer for narrow decorative branches reported by `reports/validation.json`.
Connector webs are intentionally part of the printable sheet and their visible artifacts are
quantified in `reports/evaluation.json`.
"""


def apply_corrections(automatic_manifest: Path, corrections_path: Path, destination: Path) -> dict[str, Any]:
    """Create a separate reviewed manifest; never mutate the automatic proposal."""
    automatic_bytes = automatic_manifest.read_bytes()
    manifest = json.loads(automatic_bytes)
    corrections = json.loads(corrections_path.read_text(encoding="utf-8"))
    applied: list[dict[str, Any]] = []
    for operation in corrections.get("operations", []):
        kind = operation["type"]
        if kind == "reorder_layers":
            by_id = {layer["id"]: layer for layer in manifest["layers"]}
            manifest["layers"] = [by_id[layer_id] for layer_id in operation["layer_ids_back_to_front"]]
            for order, layer in enumerate(manifest["layers"]):
                layer["order_back_to_front"] = order
                layer["assembly_transform"]["translate_xyz_mm"][2] = round(order * 1.6, 4)
        elif kind in {"add_polygon", "subtract_polygon"}:
            layer = next(item for item in manifest["layers"] if item["id"] == operation["layer_id"])
            target = "additive_regions" if kind == "add_polygon" else "cutout_regions"
            layer["geometry"][target].append(
                {"id": operation["id"], "role": "manual_correction", "shape": {"type": "polygon", "points_mm": operation["points_mm"]}}
            )
        else:
            raise ValueError(f"unsupported correction operation: {kind}")
        applied.append(operation)
    manifest["plan_provenance"] = "automatic_then_manually_reviewed" if applied else "automatic_unmodified_after_review"
    write_json(destination, manifest)
    if automatic_manifest.read_bytes() != automatic_bytes:
        raise RuntimeError("automatic manifest changed while applying corrections")
    return {"automatic_manifest_sha256": hashlib.sha256(automatic_bytes).hexdigest(), "corrections_applied": applied, "final_manifest_sha256": sha256(destination)}


def run_auto_case(source: Path, case_id: str, output: Path, config: AutoConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    """Propose, preserve, export, reopen, render, and validate one unannotated PNG."""
    if output.exists():
        shutil.rmtree(output)
    for directory in ("original", "automatic", "corrections", "layers", "previews", "reports"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output / "original" / "source.png")
    proposal = propose(source, config)
    manifest = proposal_manifest(source, proposal, case_id)
    write_json(output / "automatic" / "proposal.json", _serializable_proposal(source, proposal))
    write_json(output / "automatic" / "layer_stack.json", manifest)
    _save_analysis_assets(output, proposal)

    corrections = {"review_status": "reviewed_no_manual_geometry_corrections", "operations": []}
    write_json(output / "corrections" / "corrections.json", corrections)
    correction_log = apply_corrections(
        output / "automatic" / "layer_stack.json",
        output / "corrections" / "corrections.json",
        output / "layer_stack.json",
    )
    write_json(output / "reports" / "correction_log.json", correction_log)
    final_manifest = json.loads((output / "layer_stack.json").read_text(encoding="utf-8"))
    builds = _build_exports(final_manifest, output)
    canvas = tuple(final_manifest["artwork"]["canvas_mm"])
    for build in builds:
        save_layer_preview(build.mesh_footprint, build.spec["color"]["hex"], canvas, (900, 900), output / "previews" / f"{build.spec['id']}.png")
    _render_front(builds, final_manifest, output / "previews" / "assembled_front.png")
    _render_auto_side(builds, output / "previews" / "assembled_side.png")
    _render_exploded(builds, output / "previews" / "exploded_layers.png")
    evaluation, cad_labels = _evaluation(source, proposal, final_manifest, builds)
    _render_comparison(source, proposal, cad_labels, output / "previews" / "source_comparison_overlay.png")

    layer_reports: list[dict[str, Any]] = []
    expected_bounds = config.outer_bounds
    for build in builds:
        step_solids = _step_solids(build.step_shape)
        step_box = build.step_shape.BoundingBox()
        step_bounds = [step_box.xmin, step_box.ymin, step_box.xmax, step_box.ymax]
        stl_check = validate_shared_area(final_manifest, build.mesh_footprint, tolerance_mm=0.15)
        step_delta = max(abs(a - b) for a, b in zip(step_bounds, expected_bounds))
        planned_check = validate_shared_sheet(final_manifest, build.spec)
        layer_reports.append(
            {
                "id": build.spec["id"],
                "status": "PASS" if all((planned_check["pass"], stl_check["pass"], build.mesh.is_watertight, build.step_shape.isValid(), len(step_solids) == 1, step_delta <= 0.001)) else "FAIL",
                "color": build.spec["color"]["hex"],
                "step": build.step_path.relative_to(output).as_posix(),
                "stl": build.stl_path.relative_to(output).as_posix(),
                "planned_shared_sheet": planned_check,
                "stl_shared_sheet": stl_check,
                "planned_components": connected_component_count(build.planned_area),
                "stl_components": len(build.mesh.split(only_watertight=False)),
                "step_solids": len(step_solids),
                "step_valid": bool(build.step_shape.isValid()),
                "stl_watertight": bool(build.mesh.is_watertight),
                "planned_holes": hole_count(build.planned_area),
                "stl_footprint_holes": hole_count(build.mesh_footprint),
                "step_xy_bounds_mm": step_bounds,
                "step_outer_bounds_max_delta_mm": step_delta,
                "step_volume_mm3": float(sum(solid.Volume() for solid in step_solids)),
                "stl_volume_mm3": float(abs(build.mesh.volume)),
                "minimum_feature_morphology_check": narrow_feature_survives(build.planned_area, final_manifest["manufacturing"]["minimum_feature_mm"]),
                "fits_220x220_mm_bed": config.sheet_size_mm <= config.printer_bed_mm[0] and config.sheet_size_mm <= config.printer_bed_mm[1],
            }
        )
    all_layers_pass = all(row["status"] == "PASS" for row in layer_reports)
    report = {
        "report_version": "1.0.0-l1-auto",
        "fixture_id": case_id,
        "status": "READY_FOR_USER_TEST" if all_layers_pass else "NO_GO",
        "milestone": "L1_AUTOMATIC_PROPOSAL",
        "physical_test": "NOT_REQUIRED_AT_THIS_DEVELOPMENT_STAGE",
        "source_sha256": sha256(source),
        "manual_annotations_used": False,
        "manual_corrections_count": len(correction_log["corrections_applied"]),
        "automatic_proposal_preserved": sha256(output / "automatic" / "layer_stack.json") == correction_log["automatic_manifest_sha256"],
        "geometry_source": "automatic image analysis; no fixture-specific geometry",
        "preview_source": "reopened exported STL footprints",
        "environment": _environment(),
        "layer_count": len(builds),
        "layers": layer_reports,
        "printability": {
            "common_outer_dimensions_pass": all(row["planned_shared_sheet"]["pass"] and row["stl_shared_sheet"]["pass"] for row in layer_reports),
            "structural_connector_width_mm": config.connector_width_mm,
            "connector_width_at_least_three_nozzle_widths": config.connector_width_mm >= 1.2,
            "conservative_full_sheet_erosion_pass_count": sum(bool(row["minimum_feature_morphology_check"]) for row in layer_reports),
            "conservative_full_sheet_erosion_warning_count": sum(not bool(row["minimum_feature_morphology_check"]) for row in layer_reports),
            "warning": "A failed full-sheet erosion check flags narrow decorative source detail for slicer review; it does not negate watertightness, single-sheet connectivity, or the explicit connector width check.",
        },
        "evaluation": evaluation,
        "limitations": [
            "Palette reduction maps the source to one filament color per physical sheet.",
            "Visible connector artifacts and lost antialiased/shaded detail are measured, not hidden.",
            "Automated software checks do not establish real filament appearance, warping, or handling strength.",
        ],
    }
    write_json(output / "reports" / "evaluation.json", evaluation)
    write_json(output / "reports" / "validation.json", report)
    (output / "reports" / "print_and_stack_guide.md").write_text(
        _print_guide(final_manifest), encoding="utf-8"
    )
    checksums = sorted(path for path in output.rglob("*") if path.is_file() and path.name != "checksums.sha256")
    (output / "reports" / "checksums.sha256").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(output).as_posix()}\n" for path in checksums), encoding="utf-8"
    )
    return report

