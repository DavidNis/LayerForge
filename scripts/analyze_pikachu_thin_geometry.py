"""Reference-based thin-feature audit for Pikachu and saved turtle masks.

This diagnostic does not edit the production validator.  It extracts the
actual top-surface triangles from the nine artwork meshes in the supplied 3MF,
rasterizes them at 0.05 mm/pixel, calls the existing validators, and produces
annotated evidence.  The external box plate is intentionally excluded.
"""

from __future__ import annotations

import hashlib
import json
import math
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import cv2
import numpy as np
from PIL import Image, ImageDraw

from layerforge_l1.connectivity_repair import (
    audit_connection_width,
    audit_edge_connectivity,
)
from layerforge_l1.layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG, PocConfig


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "Pikachu_scream_original.3mf"
TURTLE_ROOT = (
    ROOT
    / "artifacts"
    / "l1_turtle_on_leaf_global_planner_v2"
    / "rejected_candidate"
)
TURTLE_REPORT = TURTLE_ROOT / "rejected_candidate_report.json"
OUTPUT = ROOT / "artifacts" / "l1_pikachu_thin_geometry_reference_v1"

MODEL_NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
RASTER_MM_PER_PIXEL = 0.05
CANVAS_MM = 144.0
CANVAS_PIXELS = int(round(CANVAS_MM / RASTER_MM_PER_PIXEL))
REFERENCE_CONFIG = replace(
    DEFAULT_CONFIG,
    analysis_pixels=CANVAS_PIXELS - 64,
    outer_pixels=CANVAS_PIXELS,
    perimeter_pixels=32,
    sheet_size_mm=CANVAS_MM,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _parse_transform(value: str | None) -> np.ndarray:
    if not value:
        return np.eye(4, dtype=np.float64)
    numbers = np.asarray([float(part) for part in value.split()], dtype=np.float64)
    if numbers.size != 12:
        raise ValueError(f"unexpected 3MF transform: {value}")
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = numbers[:9].reshape(3, 3)
    matrix[:3, 3] = numbers[9:12]
    return matrix


def _metadata_map(element: ET.Element) -> dict[str, str]:
    return {
        child.get("key", ""): child.get("value", "")
        for child in element.findall("metadata")
    }


def _load_package_index(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    root = ET.fromstring(archive.read("3D/3dmodel.model"))
    resources = root.find("m:resources", MODEL_NS)
    if resources is None:
        raise RuntimeError("3MF has no resources")
    component_by_root: dict[int, tuple[str, int]] = {}
    for obj in resources.findall("m:object", MODEL_NS):
        component = obj.find("m:components/m:component", MODEL_NS)
        if component is None:
            continue
        path = next(value for key, value in component.attrib.items() if key.endswith("path"))
        component_by_root[int(obj.get("id"))] = (path.lstrip("/"), int(component.get("objectid")))

    build_transforms = {
        int(item.get("objectid")): _parse_transform(item.get("transform"))
        for item in root.findall("m:build/m:item", MODEL_NS)
    }
    settings = ET.fromstring(archive.read("Metadata/model_settings.config"))
    object_meta = {
        int(obj.get("id")): _metadata_map(obj)
        for obj in settings.findall("object")
    }
    plates = []
    for plate in settings.findall("plate"):
        meta = _metadata_map(plate)
        instance = plate.find("model_instance")
        if instance is None:
            continue
        instance_meta = _metadata_map(instance)
        root_id = int(instance_meta["object_id"])
        name = meta.get("plater_name", f"plate-{meta.get('plater_id', '?')}")
        if name == "box":
            continue
        path, child_id = component_by_root[root_id]
        plates.append(
            {
                "plate_id": int(meta["plater_id"]),
                "plate_name": name,
                "root_object_id": root_id,
                "child_object_id": child_id,
                "object_path": path,
                "extruder": int(object_meta[root_id].get("extruder", "0")),
                "build_transform": build_transforms[root_id],
            }
        )
    return sorted(plates, key=lambda row: row["plate_id"])


def _load_mesh(
    archive: zipfile.ZipFile, path: str, object_id: int
) -> tuple[np.ndarray, np.ndarray]:
    root = ET.fromstring(archive.read(path))
    selected = None
    for obj in root.findall(".//m:object", MODEL_NS):
        if int(obj.get("id")) == object_id:
            selected = obj
            break
    if selected is None:
        raise RuntimeError(f"object {object_id} absent from {path}")
    mesh = selected.find("m:mesh", MODEL_NS)
    if mesh is None:
        raise RuntimeError(f"object {object_id} contains no mesh")
    vertices = np.asarray(
        [
            [float(vertex.get(axis)) for axis in ("x", "y", "z")]
            for vertex in mesh.findall("m:vertices/m:vertex", MODEL_NS)
        ],
        dtype=np.float64,
    )
    faces = np.asarray(
        [
            [int(triangle.get(index)) for index in ("v1", "v2", "v3")]
            for triangle in mesh.findall("m:triangles/m:triangle", MODEL_NS)
        ],
        dtype=np.int32,
    )
    return vertices, faces


def _mesh_integrity(vertices: np.ndarray, faces: np.ndarray) -> dict[str, Any]:
    triangles = vertices[faces]
    areas = np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]),
        axis=1,
    ) / 2.0
    edges = np.sort(
        np.concatenate(
            (faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]), axis=0
        ),
        axis=1,
    )
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return {
        "vertices": int(len(vertices)),
        "triangles": int(len(faces)),
        "degenerate_triangles": int(np.count_nonzero(areas <= 1e-12)),
        "boundary_edges": int(np.count_nonzero(counts == 1)),
        "nonmanifold_edges": int(np.count_nonzero(counts > 2)),
        "edge_incidence_watertight": bool(np.all(counts == 2)),
    }


def _to_pixel(xy: np.ndarray) -> np.ndarray:
    pixels = np.empty_like(xy, dtype=np.float64)
    pixels[:, 0] = (xy[:, 0] + CANVAS_MM / 2.0) / RASTER_MM_PER_PIXEL
    pixels[:, 1] = (CANVAS_MM / 2.0 - xy[:, 1]) / RASTER_MM_PER_PIXEL
    return np.clip(np.rint(pixels), 0, CANVAS_PIXELS - 1).astype(np.int32)


def _rasterize_top(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, int]:
    # The artwork plates have lower interior top surfaces and a taller rim.
    # Union every triangle with non-zero XY projection so the mask represents
    # the physical footprint at any Z, rather than only the maximum-Z rim.
    triangles_xy = vertices[faces, :2]
    projected_twice_area = np.abs(
        (triangles_xy[:, 1, 0] - triangles_xy[:, 0, 0])
        * (triangles_xy[:, 2, 1] - triangles_xy[:, 0, 1])
        - (triangles_xy[:, 1, 1] - triangles_xy[:, 0, 1])
        * (triangles_xy[:, 2, 0] - triangles_xy[:, 0, 0])
    )
    projected = faces[projected_twice_area > 1e-12]
    mask = np.zeros((CANVAS_PIXELS, CANVAS_PIXELS), dtype=np.uint8)
    for face in projected:
        cv2.fillConvexPoly(mask, _to_pixel(vertices[face, :2]), 1, lineType=cv2.LINE_8)
    return mask.astype(bool), int(len(projected))


def _hole_components(material: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
    blank = (~material).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(blank, connectivity=4)
    exterior = set(
        int(value)
        for value in np.unique(
            np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1]))
        )
        if value
    )
    hole_ids = [component for component in range(1, count) if component not in exterior]
    holes = np.isin(labels, hole_ids)
    details = []
    for component in hole_ids:
        component_mask = labels == component
        distance = cv2.distanceTransform(component_mask.astype(np.uint8), cv2.DIST_L2, 5)
        ridge = component_mask & (
            distance >= cv2.dilate(distance, np.ones((3, 3), np.uint8)) - 1e-6
        )
        widths = 2.0 * distance[ridge] * RASTER_MM_PER_PIXEL
        x, y, width, height, area = stats[component].tolist()
        details.append(
            {
                "component": component,
                "area_mm2_raster": float(area * RASTER_MM_PER_PIXEL**2),
                "bounds_pixels": [int(x), int(y), int(width), int(height)],
                "bounds_mm": [
                    round((x - CANVAS_PIXELS / 2) * RASTER_MM_PER_PIXEL, 3),
                    round((CANVAS_PIXELS / 2 - (y + height)) * RASTER_MM_PER_PIXEL, 3),
                    round(width * RASTER_MM_PER_PIXEL, 3),
                    round(height * RASTER_MM_PER_PIXEL, 3),
                ],
                "medial_width_min_mm": float(widths.min()) if len(widths) else None,
                "medial_width_p10_mm": float(np.percentile(widths, 10)) if len(widths) else None,
                "medial_width_median_mm": float(np.median(widths)) if len(widths) else None,
                "medial_width_max_mm": float(widths.max()) if len(widths) else None,
            }
        )
    return holes, details


def _thin_positive_features(
    material: np.ndarray,
    width_report: dict[str, Any],
    maps: dict[str, np.ndarray],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    distance = cv2.distanceTransform(material.astype(np.uint8), cv2.DIST_L2, 5)
    diameter_mm = 2.0 * distance * RASTER_MM_PER_PIXEL
    ridge = material & (
        distance >= cv2.dilate(distance, np.ones((3, 3), np.uint8)) - 1e-6
    )
    thin_ridge = ridge & (diameter_mm < 1.2)
    joined = cv2.dilate(thin_ridge.astype(np.uint8), np.ones((3, 3), np.uint8))
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(joined, connectivity=8)
    core_labels = maps["core_labels"]
    anchored_ids = set(int(value) for value in np.unique(core_labels[maps["connected_core"]]) if value)
    orphan_ids = set(int(detail["component"]) for detail in width_report["orphan_structural_core_details"])
    features = []
    for component in range(1, count):
        source = thin_ridge & (labels == component)
        if np.count_nonzero(source) < 3:
            continue
        expanded = cv2.dilate((labels == component).astype(np.uint8), np.ones((25, 25), np.uint8))
        adjacent = set(int(value) for value in np.unique(core_labels[expanded.astype(bool)]) if value)
        if adjacent & orphan_ids and adjacent & anchored_ids:
            connection_class = "CRITICAL_ONLY_ATTACHMENT_CANDIDATE"
        elif len(adjacent) <= 1:
            connection_class = "TERMINAL_DECORATIVE_CANDIDATE"
        elif adjacent and adjacent.issubset(anchored_ids):
            connection_class = "REDUNDANT_OR_SECONDARY_CONNECTION_CANDIDATE"
        else:
            connection_class = "AMBIGUOUS"
        widths = diameter_mm[source]
        x, y, width, height, _ = stats[component].tolist()
        features.append(
            {
                "component": component,
                "center_pixels": [round(float(value), 2) for value in centroids[component]],
                "bounds_pixels": [int(x), int(y), int(width), int(height)],
                "ridge_pixels": int(np.count_nonzero(source)),
                "medial_width_min_mm": float(widths.min()),
                "medial_width_median_mm": float(np.median(widths)),
                "medial_width_max_mm": float(widths.max()),
                "adjacent_structural_core_ids": sorted(adjacent),
                "connection_class": connection_class,
                "classification_limit": (
                    "Raster topology candidate; the mesh proves geometry, not real-world strength."
                ),
            }
        )
    return thin_ridge, sorted(features, key=lambda row: row["ridge_pixels"], reverse=True)


def _annotate_reference(
    plate: str,
    material: np.ndarray,
    holes: np.ndarray,
    thin_ridge: np.ndarray,
    width_maps: dict[str, np.ndarray],
    positives: list[dict[str, Any]],
    openings: list[dict[str, Any]],
    destination: Path,
) -> None:
    display_size = 720
    x0 = int(round((-54.5 + CANVAS_MM / 2) / RASTER_MM_PER_PIXEL))
    x1 = int(round((54.5 + CANVAS_MM / 2) / RASTER_MM_PER_PIXEL)) + 1
    crop = np.full((CANVAS_PIXELS, x1 - x0, 3), 236, dtype=np.uint8)
    local_material = material[:, x0:x1]
    crop[local_material] = (45, 52, 62)
    crop[holes[:, x0:x1]] = (255, 255, 255)
    crop[thin_ridge[:, x0:x1]] = (245, 158, 11)
    crop[width_maps["orphan_core"][:, x0:x1]] = (220, 45, 45)
    image = Image.fromarray(crop).resize(
        (round(display_size * crop.shape[1] / crop.shape[0]), display_size),
        Image.Resampling.NEAREST,
    )
    header = 92
    final = Image.new("RGB", (image.width, image.height + header), "white")
    final.paste(image, (0, header))
    draw = ImageDraw.Draw(final)
    draw.text((8, 8), f"{plate} — actual 3MF top-surface geometry", fill="black")
    draw.text((8, 28), "charcoal=retained | white=cutout | orange=thin retained medial line", fill="black")
    draw.text((8, 48), "red=orphan structural core under unchanged 1.2 mm validator", fill="black")
    draw.text((8, 68), "magenta marker=narrow opening measurement", fill="black")
    scale = display_size / CANVAS_PIXELS
    shown = 0
    for feature in positives:
        if shown >= 3:
            break
        cx, cy = feature["center_pixels"]
        px = (cx - x0) * scale
        py = cy * scale + header
        if 0 <= px < final.width:
            draw.ellipse((px - 5, py - 5, px + 5, py + 5), outline=(255, 183, 0), width=2)
            draw.text(
                (px + 7, py - 8),
                f"P {feature['medial_width_median_mm']:.2f}mm",
                fill=(160, 90, 0),
            )
            shown += 1
    shown = 0
    for opening in sorted(openings, key=lambda row: row["medial_width_p10_mm"] or math.inf):
        if shown >= 3 or opening["medial_width_p10_mm"] is None:
            break
        x, y, width, height = opening["bounds_pixels"]
        px = (x + width / 2 - x0) * scale
        py = (y + height / 2) * scale + header
        if 0 <= px < final.width:
            draw.ellipse((px - 5, py - 5, px + 5, py + 5), outline=(210, 0, 170), width=2)
            draw.text(
                (px + 7, py - 8),
                f"N {opening['medial_width_p10_mm']:.2f}mm",
                fill=(150, 0, 120),
            )
            shown += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    final.save(destination)


def _make_montage(paths: list[Path], destination: Path, columns: int = 3) -> None:
    images = [Image.open(path).convert("RGB") for path in paths]
    thumb_width = 300
    thumbs = []
    for image in images:
        thumbs.append(
            image.resize(
                (thumb_width, round(image.height * thumb_width / image.width)),
                Image.Resampling.NEAREST,
            )
        )
    cell_height = max(image.height for image in thumbs)
    rows = (len(thumbs) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * thumb_width, rows * cell_height), "white")
    for index, image in enumerate(thumbs):
        canvas.paste(image, ((index % columns) * thumb_width, (index // columns) * cell_height))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination)


def _reference_analysis() -> dict[str, Any]:
    annotated_paths = []
    rows = []
    with zipfile.ZipFile(REFERENCE) as archive:
        plates = _load_package_index(archive)
        for plate in plates:
            vertices, faces = _load_mesh(
                archive, plate["object_path"], plate["child_object_id"]
            )
            material, top_faces = _rasterize_top(vertices, faces)
            holes, opening_details = _hole_components(material)
            edge, _ = audit_edge_connectivity(material)
            p = REFERENCE_CONFIG.perimeter_pixels
            width, width_maps = audit_connection_width(
                material,
                material[p:-p, p:-p],
                REFERENCE_CONFIG,
                DEFAULT_OPTIMIZATION_CONFIG,
            )
            thin_ridge, positive_details = _thin_positive_features(
                material, width, width_maps
            )
            name = plate["plate_name"].replace("-", "_")
            mask_root = OUTPUT / "reference" / "masks" / name
            mask_root.mkdir(parents=True, exist_ok=True)
            Image.fromarray(material.astype(np.uint8) * 255, "L").save(
                mask_root / "retained_material_mask_0p05mm.png"
            )
            Image.fromarray(holes.astype(np.uint8) * 255, "L").save(
                mask_root / "through_cutouts_mask_0p05mm.png"
            )
            annotated = OUTPUT / "reference" / "annotated" / f"{name}_annotated.png"
            _annotate_reference(
                plate["plate_name"],
                material,
                holes,
                thin_ridge,
                width_maps,
                positive_details,
                opening_details,
                annotated,
            )
            annotated_paths.append(annotated)
            raw_bounds = np.vstack((vertices.min(axis=0), vertices.max(axis=0)))
            transform = plate["build_transform"]
            z_scale = float(np.linalg.norm(transform[:3, 2]))
            rows.append(
                {
                    "plate_id": plate["plate_id"],
                    "plate_name": plate["plate_name"],
                    "root_object_id": plate["root_object_id"],
                    "mesh_path": plate["object_path"],
                    "extruder": plate["extruder"],
                    "raw_bounds_mm": raw_bounds.tolist(),
                    "xy_dimensions_mm": [
                        float(raw_bounds[1, 0] - raw_bounds[0, 0]),
                        float(raw_bounds[1, 1] - raw_bounds[0, 1]),
                    ],
                    "raw_thickness_mm": float(raw_bounds[1, 2] - raw_bounds[0, 2]),
                    "build_z_scale": z_scale,
                    "finished_thickness_mm": float(
                        (raw_bounds[1, 2] - raw_bounds[0, 2]) * z_scale
                    ),
                    "mesh_integrity": _mesh_integrity(vertices, faces),
                    "nonzero_xy_projection_triangles_rasterized": top_faces,
                    "raster": {
                        "resolution_mm_per_pixel": RASTER_MM_PER_PIXEL,
                        "retained_area_mm2": float(
                            np.count_nonzero(material) * RASTER_MM_PER_PIXEL**2
                        ),
                        "retained_4_neighbor_components": edge["four_neighbor_components"],
                        "through_cutout_components": len(opening_details),
                    },
                    "existing_validator": width,
                    "thin_positive_representatives": positive_details[:20],
                    "narrow_negative_opening_representatives": sorted(
                        opening_details,
                        key=lambda detail: detail["medial_width_p10_mm"]
                        if detail["medial_width_p10_mm"] is not None
                        else math.inf,
                    )[:20],
                    "annotated_image": str(annotated.relative_to(OUTPUT)).replace("\\", "/"),
                    "comparison_limit": (
                        "The 109x144 mm plate is isotropically embedded in a 144x144 mm "
                        "diagnostic canvas. Its connected top/bottom rim contacts the unchanged "
                        "validator anchor band; physical feature scale is preserved."
                    ),
                }
            )
    _make_montage(
        annotated_paths,
        OUTPUT / "reference" / "annotated" / "all_reference_plates_montage.png",
    )
    return {
        "source_3mf": str(REFERENCE.relative_to(ROOT)),
        "source_sha256": _sha256(REFERENCE),
        "package_unit": "millimeter",
        "artwork_plate_count": len(rows),
        "external_box_excluded": True,
        "raster_resolution_mm_per_pixel": RASTER_MM_PER_PIXEL,
        "plates": rows,
    }


def _read_mask(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(path)
    return image > 0


def _opening_examples(mask: np.ndarray, mm_per_pixel: float) -> list[dict[str, Any]]:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=4)
    rows = []
    for component in range(1, count):
        component_mask = labels == component
        distance = cv2.distanceTransform(component_mask.astype(np.uint8), cv2.DIST_L2, 5)
        ridge = component_mask & (
            distance >= cv2.dilate(distance, np.ones((3, 3), np.uint8)) - 1e-6
        )
        widths = 2 * distance[ridge] * mm_per_pixel
        x, y, width, height, area = stats[component].tolist()
        rows.append(
            {
                "component": component,
                "bounds_outer_pixels": [int(x), int(y), int(width), int(height)],
                "area_pixels": int(area),
                "medial_width_p10_mm": float(np.percentile(widths, 10)) if len(widths) else None,
                "medial_width_median_mm": float(np.median(widths)) if len(widths) else None,
            }
        )
    return sorted(rows, key=lambda row: row["medial_width_p10_mm"] or math.inf)


def _annotate_turtle(
    layer_id: str,
    physical: np.ndarray,
    visible: np.ndarray,
    edge_labels: np.ndarray,
    edge_report: dict[str, Any],
    width_maps: dict[str, np.ndarray],
    width_report: dict[str, Any],
    openings: list[dict[str, Any]],
    destination: Path,
) -> None:
    rgb = np.full((*physical.shape, 3), 240, dtype=np.uint8)
    rgb[physical] = (54, 69, 79)
    rgb[~physical] = (255, 255, 255)
    p = DEFAULT_CONFIG.perimeter_pixels
    visible_outer = np.zeros_like(physical)
    visible_outer[p:-p, p:-p] = visible
    hidden_orphan = width_maps["orphan_core"] & ~visible_outer
    visible_orphan = width_maps["orphan_core"] & visible_outer
    rgb[width_maps["thin_decorative_visible"]] = (245, 158, 11)
    rgb[hidden_orphan] = (246, 196, 52)
    rgb[visible_orphan] = (220, 45, 45)
    orphan_edge_ids = [row["component"] for row in edge_report["orphan_details"]]
    rgb[np.isin(edge_labels, orphan_edge_ids)] = (115, 45, 170)
    scale = 1.0
    header = 92
    image = Image.fromarray(rgb)
    final = Image.new("RGB", (image.width, image.height + header), "white")
    final.paste(image, (0, header))
    draw = ImageDraw.Draw(final)
    draw.text((8, 8), f"{layer_id} — actual saved turtle retained mask", fill="black")
    draw.text((8, 28), "orange=A decorative warning | red=B visible orphan core", fill="black")
    draw.text((8, 48), "yellow=D hidden orphan core | purple=C disconnected material", fill="black")
    draw.text((8, 68), "cyan marker=narrow cutout; cutouts do not use retained-width rule", fill="black")
    for opening in openings[:3]:
        if opening["medial_width_p10_mm"] is None:
            continue
        x, y, width, height = opening["bounds_outer_pixels"]
        cx = x + width / 2
        cy = y + height / 2 + header
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), outline=(0, 155, 190), width=2)
        draw.text(
            (cx + 7, cy - 8),
            f"opening {opening['medial_width_p10_mm']:.2f}mm",
            fill=(0, 105, 135),
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    final.save(destination)


def _turtle_analysis() -> dict[str, Any]:
    report = json.loads(TURTLE_REPORT.read_text(encoding="utf-8"))
    rows = []
    annotated_paths = []
    for record in report["layer_files"]:
        physical = _read_mask(TURTLE_ROOT / record["retained_material_mask"])
        visible = _read_mask(TURTLE_ROOT / record["source_visible_region_mask"])
        edge, edge_labels = audit_edge_connectivity(physical)
        width, maps = audit_connection_width(
            physical,
            visible,
            DEFAULT_CONFIG,
            DEFAULT_OPTIMIZATION_CONFIG,
        )
        if width["status"] == "PASS":
            continue
        cutout = _read_mask(TURTLE_ROOT / record["through_cutouts_mask"])
        openings = _opening_examples(
            cutout, DEFAULT_CONFIG.sheet_size_mm / DEFAULT_CONFIG.outer_pixels
        )
        annotated = OUTPUT / "turtle" / "annotated" / f"{record['id']}_classified.png"
        _annotate_turtle(
            record["id"],
            physical,
            visible,
            edge_labels,
            edge,
            maps,
            width,
            openings,
            annotated,
        )
        annotated_paths.append(annotated)
        rows.append(
            {
                "physical_layer_index_back_to_front": record["depth_back_to_front"],
                "front_to_back_depth": record["provider_index"],
                "layer_id": record["id"],
                "color": record["color"],
                "category_A_thin_decorative_retained": {
                    "count": width["thin_decorative_visible_components"],
                    "examples": width["thin_decorative_visible_details"],
                    "pass_fail_effect": "warning only",
                },
                "category_B_structural_bottleneck": {
                    "orphan_core_count": width["orphan_structural_core_components"],
                    "orphan_core_pixels": width["orphan_structural_core_pixels"],
                    "visible_bottleneck_components": width[
                        "structural_bottleneck_visible_components"
                    ],
                    "largest_examples": sorted(
                        width["orphan_structural_core_details"],
                        key=lambda detail: detail["area_pixels_at_required_kernel"],
                        reverse=True,
                    )[:10],
                },
                "category_C_disconnected_or_unsupported": {
                    "four_neighbor_components": edge["four_neighbor_components"],
                    "four_neighbor_orphan_components": edge[
                        "four_neighbor_orphan_components"
                    ],
                    "examples": edge["orphan_details"],
                },
                "category_D_ambiguous": {
                    "hidden_orphan_core_count": sum(
                        detail["classification"]
                        == "HIDDEN_RETAINED_MATERIAL_STRUCTURAL_BOTTLENECK"
                        for detail in width["orphan_structural_core_details"]
                    ),
                    "reason": (
                        "Hidden orphan cores are definite failures of the configured raster "
                        "design rule, but their real load and print survival are not established."
                    ),
                },
                "narrow_openings_separate_rule_examples": openings[:10],
                "annotated_image": str(annotated.relative_to(OUTPUT)).replace("\\", "/"),
            }
        )
    _make_montage(
        annotated_paths,
        OUTPUT / "turtle" / "annotated" / "all_turtle_failures_classified.png",
        columns=2,
    )
    return {
        "source_masks": str(TURTLE_ROOT.relative_to(ROOT)).replace("\\", "/"),
        "planning_rerun": False,
        "failing_layers": rows,
    }


def main() -> int:
    if not REFERENCE.is_file():
        raise FileNotFoundError(REFERENCE)
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUTPUT}")
    OUTPUT.mkdir(parents=True)
    reference = _reference_analysis()
    turtle = _turtle_analysis()
    report = {
        "status": "READY_FOR_USER_TEST",
        "milestone": "L1_NOT_APPROVED",
        "reference": reference,
        "turtle": turtle,
        "production_validator_modified": False,
        "planner_run": False,
        "cad_stl_step_generated": False,
    }
    _write_json(OUTPUT / "reports" / "geometry_analysis.json", report)
    print(
        json.dumps(
            {
                "reference_plates": reference["artwork_plate_count"],
                "reference_width_failures": sum(
                    plate["existing_validator"]["status"] != "PASS"
                    for plate in reference["plates"]
                ),
                "turtle_failing_layers": len(turtle["failing_layers"]),
                "report": str(OUTPUT / "reports" / "geometry_analysis.json"),
            },
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
