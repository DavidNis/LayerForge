"""L2 reviewed layer editor: explicit edit operations on a loaded ``SheetStack``.

An edit is a JSON-serializable dict ``{"op": <name>, ...}`` (see ``EDIT_OPS``).
``apply_edit`` returns a *new* stack plus a change record; it never modifies its
input, never re-runs the planner and never repairs anything.  Geometric edits
that break a physical rule (floating material, sub-width necks, a cut into the
shared perimeter) are applied as asked and left for ``validate_stack`` to
report.  Only requests that cannot be represented at all (unknown sheet, rim
outside the perimeter band, a point outside the sheet) raise ``EditError``.

Geometry follows the existing Boolean model on the stack grid: ``add`` is a
union and ``subtract`` a difference with a region, where a region is a
``geometry.shape_from_spec`` primitive in sheet millimetres (CAD x right, y up,
origin at the outer sheet's bottom-left corner), a ``bar`` (bridge), or a
binary mask PNG.

Hybrid precision.  ``"precision": "raster"`` (the default when the key is absent,
which is how every pre-vector log replays) rasterizes a shape onto the stack
grid, as before.  ``"precision": "vector"`` gives the sheet an exact planar
``geometry`` in mm (the traced mask, exactly as CAD traces it, then the Boolean
operation with the exact shape); from then on every geometric edit of that
sheet (add, subtract, bridge, fill_hole, remove_island, merge, split) is done on
the geometry, mask operands entering as exact pixel-edge polygons.  The grid
raster of the geometry is kept as ``retained`` for the raster validators and
the front view; CAD extrudes the geometry itself.  The planner is untouched.

After every edit the per-sheet ``visible`` masks are re-derived with the rule
the planner output satisfies (frontmost material inside the artwork, frame
excluded), so they always describe what the edited stack actually shows.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .model import Sheet, SheetStack, StackError, _read_mask

PRECISIONS = ("raster", "vector")
EDIT_OPS = (
    "reorder", "move", "recolor", "thickness", "rim", "add", "subtract", "fill_hole",
    "remove_island", "bridge", "merge", "split", "accept_artwork",
)
NO_MATERIAL = -1


class EditError(StackError):
    """The edit request cannot be applied to this stack as specified."""


# -- derived views ------------------------------------------------------------------
def artwork_mask(stack: SheetStack) -> np.ndarray:
    """Artwork pixels that are compared with the target (perimeter and frame excluded)."""

    return ~stack.grid.perimeter_mask() & ~stack.frame_mask()


def visible_masks(stack: SheetStack) -> list[np.ndarray]:
    shown = stack.composite_sheet_index()
    art = artwork_mask(stack)
    return [(shown == index) & art for index in range(len(stack.sheets))]


def front_color_codes(stack: SheetStack) -> np.ndarray:
    """24-bit color of the frontmost material per pixel (``NO_MATERIAL`` where none)."""

    codes = np.array([int(s.color[1:], 16) for s in stack.sheets] + [NO_MATERIAL], dtype=np.int64)
    return codes[stack.composite_sheet_index()]


def target_color_codes(stack: SheetStack, target_rgb: np.ndarray) -> np.ndarray:
    """``target_rgb`` resized to the artwork grid and embedded on the full grid."""

    grid = stack.grid
    p = grid.perimeter_pixels
    art = cv2.resize(target_rgb, (grid.artwork_pixels,) * 2, interpolation=cv2.INTER_NEAREST).astype(np.int64)
    codes = np.full((grid.outer_pixels,) * 2, NO_MATERIAL, dtype=np.int64)
    codes[p:-p, p:-p] = (art[..., 0] << 16) | (art[..., 1] << 8) | art[..., 2]
    return codes


def artwork_deviation_px(stack: SheetStack, target_rgb: np.ndarray | None) -> int | None:
    """Artwork pixels whose front color differs from ``target_rgb``."""

    if target_rgb is None:
        return None
    art = artwork_mask(stack)
    return int((front_color_codes(stack)[art] != target_color_codes(stack, target_rgb)[art]).sum())


def islands(stack: SheetStack, index: int) -> list[dict[str, Any]]:
    """Material components of one sheet that do not reach the shared perimeter.

    Raster sheets: 4-neighbor pixel components.  Vector sheets: exact polygons.
    """

    sheet = stack.sheets[index]
    if sheet.geometry is not None:
        from .cad import perimeter_band, polygons, rasterize_geometry

        band = perimeter_band(stack)
        found = []
        for poly in polygons(sheet.geometry):
            if poly.intersects(band):
                continue
            pixels = rasterize_geometry(poly, stack.grid)
            found.append({
                "sheet": sheet.id,
                "area_px": int(pixels.sum()),
                "area_mm2": round(float(poly.area), 3),
                "point_on_island_mm": [round(v, 3) for v in poly.representative_point().coords[0]],
                "bbox_mm": [round(v, 3) for v in poly.bounds],
                "visible_px": int((sheet.visible & pixels).sum()),
            })
        return found
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(sheet.retained.astype(np.uint8), connectivity=4)
    anchored = set(np.unique(labels[stack.grid.perimeter_mask() & sheet.retained]).tolist())
    mm, n = stack.grid.mm_per_pixel, stack.grid.outer_pixels
    found = []
    for label in range(1, count):
        if label in anchored:
            continue
        x, y, w, h, area = (int(v) for v in stats[label])
        cx, cy = centroids[label]
        # Report a point that lies on the island (centroids of rings may not).
        rows, cols = np.nonzero(labels[y:y + h, x:x + w] == label)
        pick = int(np.argmin((rows + y - cy) ** 2 + (cols + x - cx) ** 2))
        row, col = rows[pick] + y, cols[pick] + x
        found.append({
            "sheet": sheet.id,
            "area_px": area,
            "area_mm2": round(area * mm * mm, 3),
            "point_on_island_mm": [round((col + 0.5) * mm, 3), round((n - row - 0.5) * mm, 3)],
            "bbox_mm": [round(x * mm, 3), round((n - y - h) * mm, 3), round((x + w) * mm, 3), round((n - y) * mm, 3)],
            "visible_px": int((sheet.visible & (labels == label)).sum()),
        })
    return found


# -- operands -----------------------------------------------------------------------
def point_to_pixel(stack: SheetStack, point_mm: Any) -> tuple[int, int]:
    x, y = (float(v) for v in point_mm)
    mm, n = stack.grid.mm_per_pixel, stack.grid.outer_pixels
    row, col = n - 1 - int(np.floor(y / mm)), int(np.floor(x / mm))
    if not (0 <= row < n and 0 <= col < n):
        raise EditError(f"point {point_mm} mm lies outside the {stack.grid.outer_sheet_mm:g} mm sheet")
    return row, col


def shape_geometry(spec: dict[str, Any]):
    """``geometry.shape_from_spec`` primitives plus ``bar`` (from_mm, to_mm, width_mm)."""

    from shapely.geometry import LineString

    from ..geometry import shape_from_spec

    if not isinstance(spec, dict):
        # Typical cause: Windows PowerShell 5.1 strips the quotes of JSON arguments.
        raise EditError(f"a shape must be a JSON object, got {spec!r}; pass structured edits with --op-file")
    try:
        if spec.get("type") == "bar":
            width = float(spec["width_mm"])
            if width <= 0:
                raise EditError("bar width must be positive")
            return LineString([spec["from_mm"], spec["to_mm"]]).buffer(width / 2, cap_style=2)
        return shape_from_spec(spec)
    except (KeyError, TypeError, ValueError) as error:
        raise EditError(f"invalid shape {spec}: {error}") from error


def region_mask(stack: SheetStack, region: dict[str, Any], root: Path | None = None) -> np.ndarray:
    """Boolean full-grid mask of ``{"shape": spec}`` or ``{"mask": png}``."""

    from .cad import rasterize_geometry

    if not isinstance(region, dict):
        raise EditError(f"a region must be a JSON object, got {region!r}; pass structured edits with --op-file")
    if "shape" in region:
        mask = rasterize_geometry(shape_geometry(region["shape"]), stack.grid)
    elif "mask" in region:
        path = Path(region["mask"])
        if not path.is_absolute() and root is not None:
            path = Path(root) / path
        mask = _read_mask(path)
        grid = stack.grid
        if mask.shape == (grid.artwork_pixels,) * 2:
            full = np.zeros((grid.outer_pixels,) * 2, dtype=bool)
            p = grid.perimeter_pixels
            full[p:-p, p:-p] = mask
            mask = full
        elif mask.shape != (grid.outer_pixels,) * 2:
            raise EditError(f"mask {path} is {mask.shape}; expected the outer or artwork grid of the stack")
    else:
        raise EditError("a region needs 'shape' or 'mask'")
    if not mask.any():
        raise EditError(f"region {region} covers no pixel of the sheet grid")
    return mask


def sheet_vector(stack: SheetStack, sheet: Sheet):
    """The sheet's exact geometry, or its mask traced exactly as CAD traces it."""

    from .cad import DEFAULT_SIMPLIFY_MM, mask_to_geometry

    if sheet.geometry is not None:
        return sheet.geometry
    return mask_to_geometry(sheet.retained, stack.grid.mm_per_pixel, DEFAULT_SIMPLIFY_MM)


def mask_geometry(stack: SheetStack, mask: np.ndarray):
    """Exact pixel-edge polygons of a grid mask (no simplification)."""

    from shapely.geometry import Polygon

    from .cad import mask_to_geometry

    return mask_to_geometry(mask, stack.grid.mm_per_pixel, 0.0) if mask.any() else Polygon()


def _set_geometry(stack: SheetStack, sheet: Sheet, geometry: Any) -> None:
    from shapely.geometry import MultiPolygon, Polygon

    from .cad import polygons, rasterize_geometry

    parts = polygons(geometry.buffer(0))
    sheet.geometry = parts[0] if len(parts) == 1 else MultiPolygon(parts) if parts else Polygon()
    sheet.retained = rasterize_geometry(sheet.geometry, stack.grid) if parts else np.zeros_like(sheet.retained)


def _precision(op: dict[str, Any]) -> str:
    precision = op.get("precision", "raster")
    if precision not in PRECISIONS:
        raise EditError(f"precision must be one of {PRECISIONS}, got {precision!r}")
    return precision


def _operand_geometry(stack: SheetStack, region: dict[str, Any], precision: str, root: Path | None):
    """Region as exact geometry: the shape itself (vector) or the pixels it covers."""

    if precision == "vector" and isinstance(region, dict) and "shape" in region:
        geometry = shape_geometry(region["shape"])
        if geometry.is_empty:
            raise EditError(f"region {region} is empty")
        return geometry
    return mask_geometry(stack, region_mask(stack, region, root))


def _component_at(mask: np.ndarray, pixel: tuple[int, int]) -> np.ndarray:
    _, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=4)
    return labels == labels[pixel]


# -- edits --------------------------------------------------------------------------
def _sheet_index(stack: SheetStack, sheet_id: Any) -> int:
    for index, sheet in enumerate(stack.sheets):
        if sheet.id == sheet_id:
            return index
    raise EditError(f"no sheet {sheet_id!r}; sheets are {[s.id for s in stack.sheets]}")


def _targets(stack: SheetStack, sheet_id: Any) -> list[int]:
    return list(range(len(stack.sheets))) if sheet_id == "*" else [_sheet_index(stack, sheet_id)]


def _copy_sheet(sheet: Sheet, **changes: Any) -> Sheet:
    fields = dict(retained=sheet.retained.copy(), visible=sheet.visible.copy(),
                  print_placement=dict(sheet.print_placement), rim=dict(sheet.rim) if sheet.rim else None)
    fields.update(changes)
    return replace(sheet, **fields)


def _rebuild(stack: SheetStack, sheets: list[Sheet], target_rgb: np.ndarray | None = None) -> SheetStack:
    try:
        edited = SheetStack(
            name=stack.name, grid=stack.grid, sheets=sheets,
            target_rgb=stack.target_rgb if target_rgb is None else target_rgb,
            frame_color=stack.frame_color, provenance=stack.provenance, review=stack.review,
        )
    except StackError as error:
        raise EditError(str(error)) from error
    for sheet, visible in zip(edited.sheets, visible_masks(edited)):
        sheet.visible = visible
    return edited


def _new_id(stack: SheetStack, base: str) -> str:
    ids = {s.id for s in stack.sheets}
    for n in range(1, 1000):
        candidate = f"{base}_split{n}"
        if candidate not in ids:
            return candidate
    raise EditError(f"no free split id for {base}")


def _apply(stack: SheetStack, op: dict[str, Any], root: Path | None) -> SheetStack:
    name = op.get("op")
    sheets = [_copy_sheet(s) for s in stack.sheets]
    perimeter = stack.grid.perimeter_mask()

    if name == "reorder":
        order = list(op["order"])
        if sorted(order) != sorted(s.id for s in sheets):
            raise EditError(f"reorder must list every sheet id exactly once (back to front), got {order}")
        by_id = {s.id: s for s in sheets}
        return _rebuild(stack, [by_id[i] for i in order])

    if name == "move":
        index = _sheet_index(stack, op["sheet"])
        to = int(op["to"])
        if not 0 <= to < len(sheets):
            raise EditError(f"move target index must be 0..{len(sheets) - 1} (back to front), got {to}")
        sheets.insert(to, sheets.pop(index))
        return _rebuild(stack, sheets)

    if name == "recolor":
        color = str(op["color"]).upper()
        if stack.frame_color and color == stack.frame_color:
            raise EditError(f"{color} is the frame color; a sheet cannot take it without becoming frame")
        index = _sheet_index(stack, op["sheet"])
        sheets[index] = _copy_sheet(sheets[index], color=color)
        return _rebuild(stack, sheets)

    if name == "thickness":
        for index in _targets(stack, op["sheet"]):
            sheets[index] = _copy_sheet(sheets[index], thickness_mm=float(op["mm"]))
        return _rebuild(stack, sheets)

    if name == "rim":
        # Optional per sheet: {"enabled": false} (or legacy "remove": true) disables it;
        # otherwise the given dimensions update the sheet's rim and both must end up set.
        dims = {k: float(op[k]) for k in ("width_mm", "total_height_mm") if k in op}
        disable = op.get("remove") or op.get("enabled") is False
        for index in _targets(stack, op["sheet"]):
            rim = None if disable else {**(sheets[index].rim or {}), **dims}
            if rim is not None and len(rim) != 2:
                raise EditError(f"{sheets[index].id} has no rim yet: give width_mm and total_height_mm")
            sheets[index] = _copy_sheet(sheets[index], rim=rim)
        return _rebuild(stack, sheets)

    if name in ("add", "subtract", "bridge"):
        index = _sheet_index(stack, op["sheet"])
        if name == "bridge":
            region = {"shape": {"type": "bar", "from_mm": op["from_mm"], "to_mm": op["to_mm"], "width_mm": op["width_mm"]}}
        else:
            region = {k: op[k] for k in ("shape", "mask") if k in op}
        sheet = sheets[index]
        if _precision(op) == "vector" or sheet.geometry is not None:
            operand = _operand_geometry(stack, region, _precision(op), root)
            current = sheet_vector(stack, sheet)
            _set_geometry(stack, sheet, current.difference(operand) if name == "subtract" else current.union(operand))
            return _rebuild(stack, sheets)
        mask = region_mask(stack, region, root)
        sheet.retained = sheet.retained & ~mask if name == "subtract" else sheet.retained | mask
        return _rebuild(stack, sheets)

    if name == "fill_hole":
        index = _sheet_index(stack, op["sheet"])
        pixel = point_to_pixel(stack, op["at_mm"])
        if sheets[index].geometry is not None:
            from shapely.geometry import Point, Polygon

            from .cad import polygons

            point = Point(*map(float, op["at_mm"]))
            geometry = sheets[index].geometry
            if geometry.covers(point):
                raise EditError(f"{op['sheet']} has material at {op['at_mm']} mm; there is no cutout to fill")
            rings = [Polygon(r) for poly in polygons(geometry) for r in poly.interiors if Polygon(r).contains(point)]
            if not rings:
                raise EditError(f"no enclosed cutout of {op['sheet']} contains {op['at_mm']} mm")
            _set_geometry(stack, sheets[index], geometry.union(min(rings, key=lambda r: r.area)))
            return _rebuild(stack, sheets)
        retained = sheets[index].retained
        if retained[pixel]:
            raise EditError(f"{op['sheet']} has material at {op['at_mm']} mm; there is no cutout to fill")
        sheets[index].retained = retained | _component_at(~retained, pixel)
        return _rebuild(stack, sheets)

    if name == "remove_island":
        index = _sheet_index(stack, op["sheet"])
        pixel = point_to_pixel(stack, op["at_mm"])
        if sheets[index].geometry is not None:
            from shapely.geometry import Point
            from shapely.ops import unary_union

            from .cad import perimeter_band, polygons

            point = Point(*map(float, op["at_mm"]))
            parts = polygons(sheets[index].geometry)
            hit = [part for part in parts if part.covers(point)]
            if not hit:
                raise EditError(f"{op['sheet']} has no material at {op['at_mm']} mm")
            if hit[0].intersects(perimeter_band(stack)):
                raise EditError(f"material at {op['at_mm']} mm on {op['sheet']} reaches the perimeter; it is not an island")
            _set_geometry(stack, sheets[index], unary_union([part for part in parts if part is not hit[0]]))
            return _rebuild(stack, sheets)
        retained = sheets[index].retained
        if not retained[pixel]:
            raise EditError(f"{op['sheet']} has no material at {op['at_mm']} mm")
        component = _component_at(retained, pixel)
        if (component & perimeter).any():
            raise EditError(f"material at {op['at_mm']} mm on {op['sheet']} reaches the perimeter; it is not an island")
        sheets[index].retained = retained & ~component
        return _rebuild(stack, sheets)

    if name == "merge":
        keep, other = _sheet_index(stack, op["sheet"]), _sheet_index(stack, op["other"])
        if keep == other:
            raise EditError("merge needs two different sheets")
        if sheets[keep].geometry is not None or sheets[other].geometry is not None:
            union = sheet_vector(stack, sheets[keep]).union(sheet_vector(stack, sheets[other]))
            _set_geometry(stack, sheets[keep], union)
            del sheets[other]
            return _rebuild(stack, sheets)
        sheets[keep].retained = sheets[keep].retained | sheets[other].retained
        del sheets[other]
        return _rebuild(stack, sheets)

    if name == "split":
        index = _sheet_index(stack, op["sheet"])
        source = sheets[index]
        region = op["region"]
        if isinstance(region, dict) and "region_at_mm" in region:
            pixel = point_to_pixel(stack, region["region_at_mm"])
            if not source.visible[pixel]:
                raise EditError(f"{source.id} shows no region at {region['region_at_mm']} mm")
            selector = _component_at(source.visible, pixel)
        else:
            selector = None
        if op.get("place", "front") not in ("front", "back"):
            raise EditError("split place must be 'front' or 'back'")
        new_id = op.get("new_id") or _new_id(stack, source.id)
        fixed = perimeter | stack.frame_mask()
        if _precision(op) == "vector" or source.geometry is not None:
            from .validation import VECTOR_AREA_TOLERANCE_MM2

            region_geometry = (mask_geometry(stack, selector) if selector is not None
                               else _operand_geometry(stack, region, _precision(op), root))
            fixed_geometry = mask_geometry(stack, fixed)
            current = sheet_vector(stack, source)
            selected = current.intersection(region_geometry).difference(fixed_geometry)
            if selected.area <= VECTOR_AREA_TOLERANCE_MM2:
                raise EditError(f"the split region selects no artwork material of {source.id}")
            part = _copy_sheet(source, id=new_id, rim=None)
            _set_geometry(stack, part, current.intersection(fixed_geometry).union(selected))
            _set_geometry(stack, source, current.difference(selected))
            sheets.insert(index + 1 if op.get("place", "front") == "front" else index, part)
            return _rebuild(stack, sheets)
        if selector is None:
            selector = region_mask(stack, region, root)
        selected = source.retained & selector & ~fixed
        if not selected.any():
            raise EditError(f"the split region selects no artwork material of {source.id}")
        part = _copy_sheet(source, id=new_id, retained=fixed | selected, rim=None)
        source.retained = source.retained & ~selected
        sheets.insert(index + 1 if op.get("place", "front") == "front" else index, part)
        return _rebuild(stack, sheets)

    if name == "accept_artwork":
        front = front_color_codes(stack)
        art = ~perimeter
        if (front[art] == NO_MATERIAL).any():
            raise EditError(f"{int((front[art] == NO_MATERIAL).sum())} artwork px have no material in any sheet")
        p = stack.grid.perimeter_pixels
        codes = front[p:-p, p:-p]
        target = np.stack([(codes >> 16) & 255, (codes >> 8) & 255, codes & 255], axis=-1).astype(np.uint8)
        frame = stack.frame_mask()[p:-p, p:-p]
        if stack.frame_color:
            target[frame] = [int(stack.frame_color[i:i + 2], 16) for i in (1, 3, 5)]
        return _rebuild(stack, sheets, target_rgb=target)

    raise EditError(f"unknown edit op {name!r}; supported: {', '.join(EDIT_OPS)}")


SUBGRID_REPORT_MM2 = 1e-6


def exact_changes(before: SheetStack, after: SheetStack) -> tuple[dict[str, float], dict[str, float]]:
    """For sheets with vector geometry: the exact changed area, and the part of it not
    covered by the sheets in front (visible from the front, even below grid size)."""

    from shapely.ops import unary_union

    old = {s.id: s for s in before.sheets}
    changed, visible = {}, {}
    for index, sheet in enumerate(after.sheets):
        prior = old.get(sheet.id)
        if prior is None or (sheet.geometry is None and prior.geometry is None) or sheet.geometry is prior.geometry:
            continue
        diff = sheet_vector(after, sheet).symmetric_difference(sheet_vector(before, prior))
        if diff.area <= SUBGRID_REPORT_MM2:
            continue
        changed[sheet.id] = round(float(diff.area), 6)
        front = unary_union([sheet_vector(after, s) for s in after.sheets[index + 1:]])
        area = float(diff.difference(front).area) if not front.is_empty else float(diff.area)
        if area > SUBGRID_REPORT_MM2:
            visible[sheet.id] = round(area, 6)
    return changed, visible


def change_summary(before: SheetStack, after: SheetStack) -> dict[str, Any]:
    """Classify an edit: visible artwork change versus hidden geometry or parameters only."""

    art = artwork_mask(before)
    visible_px = int((front_color_codes(before)[art] != front_color_codes(after)[art]).sum())
    old = {s.id: s for s in before.sheets}
    new = {s.id: s for s in after.sheets}
    retained_px = {i: int((old[i].retained != new[i].retained).sum()) for i in old.keys() & new.keys()}
    retained_px = {i: n for i, n in sorted(retained_px.items()) if n}
    param = sorted(i for i in old.keys() & new.keys()
                   if (old[i].color, old[i].thickness_mm, old[i].rim) != (new[i].color, new[i].thickness_mm, new[i].rim))
    added, removed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
    order_changed = [s.id for s in before.sheets if s.id in new] != [s.id for s in after.sheets if s.id in old]
    target_changed = (before.target_rgb is None) != (after.target_rgb is None) or (
        before.target_rgb is not None and not np.array_equal(before.target_rgb, after.target_rgb))
    geometry_mm2, visible_mm2 = exact_changes(before, after)
    if visible_px:
        kind = "visible_artwork"
    elif visible_mm2:
        kind = "visible_subgrid_geometry"
    elif target_changed:
        kind = "artwork_reference"
    elif retained_px or geometry_mm2 or added or removed or order_changed:
        kind = "hidden_geometry"
    elif param:
        kind = "physical_parameters"
    else:
        kind = "no_change"
    return {
        "kind": kind,
        "visible_changed_px": visible_px,
        "retained_changed_px": retained_px,
        "exact_geometry_changed_mm2": geometry_mm2,
        "exact_visible_change_mm2": visible_mm2,
        "parameters_changed": param,
        "sheets_added": added,
        "sheets_removed": removed,
        "order_changed": order_changed,
        "reviewed_target_changed": bool(target_changed),
    }


def apply_edit(stack: SheetStack, op: dict[str, Any], root: Path | None = None) -> tuple[SheetStack, dict[str, Any]]:
    """Apply one edit; return the new stack and its change summary (input untouched)."""

    if not isinstance(op, dict) or "op" not in op:
        raise EditError(f"an edit is a dict with an 'op' key, got {op!r}")
    try:
        edited = _apply(stack, op, root)
    except KeyError as error:
        raise EditError(f"{op.get('op')}: missing parameter {error}") from error
    except (TypeError, ValueError) as error:
        if isinstance(error, StackError):
            raise
        raise EditError(f"{op.get('op')}: invalid parameter: {error}") from error
    return edited, change_summary(stack, edited)


def write_change_image(before: SheetStack, after: SheetStack, path: Path) -> dict[str, int]:
    """Front view of ``after``, dimmed; red = visible change, blue = hidden material change."""

    rgb = front_color_codes(after)
    image = np.stack([(rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255], axis=-1).astype(np.float32)
    image[rgb == NO_MATERIAL] = 255
    image = 0.35 * image + 0.65 * 255
    visible = front_color_codes(before) != rgb
    old = {s.id: s.retained for s in before.sheets}
    hidden = np.zeros(visible.shape, dtype=bool)
    for sheet in after.sheets:
        hidden |= sheet.retained != old.get(sheet.id, np.zeros_like(sheet.retained))
    for sheet_id in old.keys() - {s.id for s in after.sheets}:
        hidden |= old[sheet_id]
    hidden &= ~visible
    image[hidden] = (40, 90, 255)
    image[visible] = (255, 0, 0)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image.astype(np.uint8)).save(path)
    return {"visible_changed_px": int(visible.sum()), "hidden_changed_px": int(hidden.sum())}


def describe(stack: SheetStack) -> list[dict[str, Any]]:
    """Per-sheet summary for inspection (back to front)."""

    from .validation import cutout_hole_count

    rows = []
    for index, (sheet, z) in enumerate(zip(stack.sheets, stack.assembly_z_mm())):
        rows.append({
            "order_back_to_front": index,
            "id": sheet.id,
            "color": sheet.color,
            "thickness_mm": sheet.thickness_mm,
            "rim": sheet.rim,
            "rim_enabled": sheet.rim is not None,
            "geometry": "exact vector" if sheet.geometry is not None else "raster mask",
            "export_name": stack.export_names()[index],
            "assembly_z_mm": round(z, 6),
            "retained_fraction": round(float(sheet.retained.mean()), 4),
            "visible_px": int(sheet.visible.sum()),
            "cutout_holes": cutout_hole_count(sheet.retained),
            "islands": len(islands(stack, index)),
        })
    return rows


__all__ = [
    "EDIT_OPS", "EditError", "apply_edit", "artwork_deviation_px", "change_summary", "describe",
    "front_color_codes", "islands", "region_mask", "visible_masks", "write_change_image",
]
