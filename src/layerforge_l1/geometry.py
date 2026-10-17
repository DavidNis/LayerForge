"""Planar Boolean geometry and CadQuery solid construction."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import cadquery as cq
from shapely import affinity
from shapely.geometry import LinearRing, MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


CIRCLE_QUADRANT_SEGMENTS = 64


def _rounded_rectangle(spec: dict[str, Any]) -> BaseGeometry:
    x0, y0, x1, y1 = map(float, spec["bounds_mm"])
    radius = float(spec["radius_mm"])
    if radius <= 0:
        return box(x0, y0, x1, y1)
    if 2 * radius > min(x1 - x0, y1 - y0):
        raise ValueError(f"rounded rectangle radius {radius} exceeds its bounds")
    core = box(x0 + radius, y0 + radius, x1 - radius, y1 - radius)
    return core.buffer(radius, quad_segs=CIRCLE_QUADRANT_SEGMENTS)


def shape_from_spec(spec: dict[str, Any]) -> BaseGeometry:
    """Create a Shapely geometry from a manifest primitive in millimetres."""
    kind = spec["type"]
    if kind == "circle":
        return Point(*spec["center_mm"]).buffer(
            float(spec["radius_mm"]), quad_segs=CIRCLE_QUADRANT_SEGMENTS
        )
    if kind == "ellipse":
        unit = Point(*spec["center_mm"]).buffer(1.0, quad_segs=CIRCLE_QUADRANT_SEGMENTS)
        return affinity.scale(
            unit, *map(float, spec["radii_mm"]), origin=tuple(spec["center_mm"])
        )
    if kind == "polygon":
        return Polygon(spec["points_mm"])
    if kind == "rounded_rectangle":
        return _rounded_rectangle(spec)
    if kind == "rounded_rectangle_ring":
        outer = _rounded_rectangle(spec)
        width = float(spec["width_mm"])
        if width <= 0:
            raise ValueError("rounded rectangle ring width must be positive")
        inner = outer.buffer(-width, quad_segs=CIRCLE_QUADRANT_SEGMENTS)
        if inner.is_empty:
            raise ValueError("rounded rectangle ring width consumed the whole outline")
        return outer.difference(inner).buffer(0)
    raise ValueError(f"unsupported shape primitive: {kind}")


def region_union(regions: Iterable[dict[str, Any]]) -> BaseGeometry:
    shapes = [shape_from_spec(region["shape"]) for region in regions]
    return unary_union(shapes) if shapes else Polygon()


def layer_area(layer: dict[str, Any]) -> BaseGeometry:
    geometry = layer["geometry"]
    additive = region_union(geometry["additive_regions"])
    cutouts = region_union(geometry["cutout_regions"])
    result = additive.difference(cutouts).buffer(0)
    if result.is_empty or not result.is_valid:
        raise ValueError(f"{layer['id']}: empty or invalid planar Boolean result")
    return result


def polygons(geometry: BaseGeometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    return [part for part in getattr(geometry, "geoms", []) if isinstance(part, Polygon)]


def connected_component_count(geometry: BaseGeometry) -> int:
    return len(polygons(geometry))


def _wire(coords: Iterable[tuple[float, float]]) -> cq.Wire:
    points = [cq.Vector(float(x), float(y), 0.0) for x, y in coords]
    if points[0].toTuple() != points[-1].toTuple():
        points.append(points[0])
    return cq.Wire.makePolygon(points)


def _face(poly: Polygon) -> cq.Face:
    exterior = _wire(poly.exterior.coords)
    interiors = [_wire(ring.coords) for ring in poly.interiors]
    return cq.Face.makeFromWires(exterior, interiors)


def extrude_geometry(geometry: BaseGeometry, height_mm: float, z0_mm: float = 0.0) -> cq.Shape:
    solids: list[cq.Shape] = []
    for poly in polygons(geometry):
        solid = cq.Solid.extrudeLinear(_face(poly), cq.Vector(0.0, 0.0, height_mm))
        if z0_mm:
            solid = solid.translate(cq.Vector(0.0, 0.0, z0_mm))
        solids.append(solid)
    if not solids:
        raise ValueError("cannot extrude geometry without polygonal area")
    result = solids[0]
    for solid in solids[1:]:
        result = result.fuse(solid)
    return result


def build_layer_solid(layer: dict[str, Any]) -> tuple[cq.Shape, BaseGeometry, BaseGeometry | None]:
    """Build a base extrusion and optional same-layer raised rim."""
    area = layer_area(layer)
    nominal = float(layer["nominal_thickness_mm"])
    solid = extrude_geometry(area, nominal)
    rim_area: BaseGeometry | None = None
    rim = layer["geometry"].get("rim", {"enabled": False})
    if rim.get("enabled", False):
        width = float(rim["width_mm"])
        total_height = float(rim["total_height_mm"])
        if total_height <= nominal:
            raise ValueError(f"{layer['id']}: rim total height must exceed nominal thickness")
        inset = area.buffer(-width)
        rim_area = area.difference(inset).buffer(0)
        if rim_area.is_empty:
            raise ValueError(f"{layer['id']}: rim width consumed the whole layer")
        raised = extrude_geometry(rim_area, total_height - nominal, nominal)
        solid = solid.fuse(raised)
    return solid, area, rim_area


def hole_count(geometry: BaseGeometry) -> int:
    return sum(len(poly.interiors) for poly in polygons(geometry))


def narrow_feature_survives(geometry: BaseGeometry, minimum_feature_mm: float) -> bool:
    """Conservative morphology check: erosion must not delete or split a component."""
    original_count = connected_component_count(geometry)
    eroded = geometry.buffer(-minimum_feature_mm / 2.0)
    return not eroded.is_empty and connected_component_count(eroded) <= original_count


def ring_is_valid(ring: LinearRing) -> bool:
    return ring.is_simple and ring.is_valid


def validate_shared_area(
    manifest: dict[str, Any], area: BaseGeometry, tolerance_mm: float | None = None
) -> dict[str, Any]:
    """Strictly validate planar geometry against the common slot-compatible carrier."""
    shared = manifest["construction"]["shared_sheet"]
    expected_outer = shape_from_spec(shared["outer_outline"])
    expected_perimeter = shape_from_spec(shared["outer_perimeter"])
    parts = polygons(area)
    expected_parts = polygons(expected_outer)
    if len(parts) != 1 or len(expected_parts) != 1:
        outline_distance = float("inf")
    else:
        outline_distance = parts[0].exterior.hausdorff_distance(expected_parts[0].exterior)
    bounds_delta = max(abs(a - b) for a, b in zip(area.bounds, expected_outer.bounds))
    missing_perimeter = expected_perimeter.difference(area).area
    outside_area = area.difference(expected_outer).area
    tolerance = (
        float(tolerance_mm)
        if tolerance_mm is not None
        else float(shared.get("validation_tolerance_mm", 1e-5))
    )
    area_tolerance = tolerance * tolerance
    checks = {
        "connected_component_count": connected_component_count(area),
        "outer_bounds_mm": list(area.bounds),
        "expected_outer_bounds_mm": list(expected_outer.bounds),
        "outer_bounds_max_delta_mm": bounds_delta,
        "outer_outline_hausdorff_mm": outline_distance,
        "missing_shared_perimeter_area_mm2": missing_perimeter,
        "area_outside_shared_outline_mm2": outside_area,
        "shared_perimeter_width_mm": float(shared["outer_perimeter"]["width_mm"]),
    }
    checks["pass"] = all(
        [
            checks["connected_component_count"] == 1,
            bounds_delta <= tolerance,
            outline_distance <= tolerance,
            missing_perimeter <= area_tolerance,
            outside_area <= area_tolerance,
        ]
    )
    return checks


def validate_shared_sheet(manifest: dict[str, Any], layer: dict[str, Any]) -> dict[str, Any]:
    """Strictly validate one manifest layer against the common carrier."""
    return validate_shared_area(manifest, layer_area(layer))

