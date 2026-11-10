"""Sheet-stack data model and versioned manifest serialization.

A ``SheetStack`` is the mask-based physical stack produced by the reveal
planner: every sheet is one filament color, all sheets share one raster grid
(identical outer XY size, square or rectangular, and a solid shared perimeter), and the
order is back to front.  Masks are boolean arrays; ``True`` is retained
material and ``False`` is a through-cutout.

On disk a stack is a directory::

    sheet_stack.json
    target.png                                   (optional approved artwork)
    layers/<sheet id>/retained_material_mask.png (255 = material)
    layers/<sheet id>/source_visible_region_mask.png
    layers/<sheet id>/geometry_mm.wkb            (optional exact vector geometry)

A sheet may carry an exact planar ``geometry`` in sheet millimetres (L2 vector
edits).  It is then authoritative for CAD, and ``retained`` is its raster on
the stack grid (``cad.rasterize_geometry``), used by the raster validators and
the front view.  Unedited sheets have no geometry and are traced from the mask
at export, exactly as before.

The ``layers/`` layout is the one already written by
``scripts/run_turtle_reveal_planner_poc.py``; ``import_planner_output``
migrates such a directory (which has no manifest) into a versioned stack.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..experimental_global_planner import exact_index
from ..reveal_planner import RevealConfig, embed, physical_grid
from ..subtractive_poc import DEFAULT_CONFIG, PocConfig

SCHEMA = "layerforge.sheet_stack"
SCHEMA_VERSION = "1.0.0"
# 1.1.0 adds the optional per-sheet ``rim`` and the top-level ``review`` block (L2
# editor); a stack that uses neither is still written as 1.0.0.
REVIEW_SCHEMA_VERSION = "1.1.0"
# 1.2.0 adds the optional per-sheet exact vector ``geometry`` (L2 hybrid vector edits).
VECTOR_SCHEMA_VERSION = "1.2.0"
SUPPORTED_SCHEMA_VERSIONS = ("1.0.0", "1.1.0", "1.2.0")
GEOMETRY_NAME = "geometry_mm.wkb"
MANIFEST_NAME = "sheet_stack.json"
RETAINED_MASK_NAME = "retained_material_mask.png"
VISIBLE_MASK_NAME = "source_visible_region_mask.png"
_HEX = re.compile(r"^#[0-9A-F]{6}$")
_IDENTITY_PLACEMENT = {"rotate_xyz_deg": [0.0, 0.0, 0.0], "translate_mm": [0.0, 0.0, 0.0]}


class StackError(ValueError):
    """Base class for invalid stack input."""


class ManifestError(StackError):
    """A manifest or its mask files cannot be read as a valid sheet stack."""


def hex_color(rgb: Any) -> str:
    return "#" + "".join(f"{int(c):02X}" for c in rgb)


def mask_bits_sha256(mask: np.ndarray) -> str:
    """Hash of the decoded mask bits (independent of PNG encoder settings)."""

    return hashlib.sha256(np.packbits(np.asarray(mask, dtype=bool)).tobytes()).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(read_bytes(path)).hexdigest()


@dataclass(frozen=True)
class StackGrid:
    """Shared raster: outer sheet (square, or rectangular) with a solid perimeter band.

    ``outer_pixels``, ``outer_sheet_mm`` and ``artwork_pixels`` are the width (x).  A rectangular grid
    also has ``outer_rows`` and the sheet height ``sheet_height_mm`` (y); both None for a square one.

    Pixels are square (``mm_per_pixel`` for both axes; the longer side has the square grid's pixels).
    A rectangular sheet's outer size is exactly the requested width x height, while its raster covers
    ``raster_mm``: the shorter side rounded to a native pixel.  The difference (``edge_mm``, less than a
    quarter of a native pixel per side) is added to or trimmed from the solid perimeter band in CAD only
    (``cad.physical_outline``): no artwork pixel, cutout or visible area is moved or scaled.
    """

    outer_pixels: int
    perimeter_pixels: int
    outer_sheet_mm: float
    scale: int
    outer_rows: int | None = None
    sheet_height_mm: float | None = None

    @classmethod
    def for_planner(cls, outer_sheet_mm: float, scale: int, outer_sheet_height_mm: float | None = None) -> StackGrid:
        poc = physical_grid(RevealConfig(outer_sheet_mm=outer_sheet_mm, scale=scale,
                                         outer_sheet_height_mm=outer_sheet_height_mm))
        if outer_sheet_height_mm is None or outer_sheet_height_mm == outer_sheet_mm:
            return cls(poc.outer_pixels, poc.perimeter_pixels, poc.sheet_size_mm, scale)
        return cls(poc.outer_pixels, poc.perimeter_pixels, float(outer_sheet_mm), scale, poc.outer_shape[0],
                   float(outer_sheet_height_mm))

    @property
    def square(self) -> bool:
        return self.outer_rows is None

    @property
    def rows(self) -> int:
        return self.outer_pixels if self.outer_rows is None else self.outer_rows

    @property
    def shape(self) -> tuple[int, int]:
        """(rows, columns) of every sheet mask."""

        return (self.rows, self.outer_pixels)

    @property
    def mm_per_pixel(self) -> float:
        if self.outer_rows is None:
            return self.outer_sheet_mm / self.outer_pixels
        # The longer side is exact: its pixel count is the square grid's (``reveal_planner.rect_grid``).
        return max(self.outer_sheet_mm, float(self.sheet_height_mm)) / max(self.outer_pixels, self.outer_rows)

    @property
    def outer_sheet_height_mm(self) -> float:
        return self.outer_sheet_mm if self.sheet_height_mm is None else self.sheet_height_mm

    @property
    def outer_mm(self) -> tuple[float, float]:
        """(width x, height y) of the physical outer sheet."""

        return (self.outer_sheet_mm, self.outer_sheet_height_mm)

    @property
    def raster_mm(self) -> tuple[float, float]:
        """(width x, height y) covered by the pixel grid; the outer size for a square sheet."""

        if self.outer_rows is None:
            return (self.outer_sheet_mm, self.outer_sheet_mm)
        return (self.outer_pixels * self.mm_per_pixel, self.outer_rows * self.mm_per_pixel)

    @property
    def edge_mm(self) -> tuple[float, float]:
        """Per side (x, y): the outer sheet edge beyond (+) or inside (-) the pixel grid's edge."""

        if self.outer_rows is None:
            return (0.0, 0.0)
        return tuple(0.0 if abs(o - r) < 1e-9 else (o - r) / 2 for o, r in zip(self.outer_mm, self.raster_mm))

    @property
    def perimeter_band_mm(self) -> float:
        """Narrowest physical width of the solid perimeter band."""

        return self.perimeter_pixels * self.mm_per_pixel + min(0.0, *self.edge_mm)

    @property
    def artwork_pixels(self) -> int:
        return self.outer_pixels - 2 * self.perimeter_pixels

    @property
    def artwork_rows(self) -> int:
        return self.rows - 2 * self.perimeter_pixels

    @property
    def artwork_mm(self) -> float:
        return self.artwork_pixels * self.mm_per_pixel

    @property
    def artwork_height_mm(self) -> float:
        return self.artwork_mm if self.outer_rows is None else self.artwork_rows * self.mm_per_pixel

    def poc_config(self) -> PocConfig:
        """The validator grid (``PocConfig``) this stack was planned on."""

        poc = physical_grid(RevealConfig(outer_sheet_mm=self.outer_sheet_mm, scale=self.scale,
                                         outer_sheet_height_mm=self.sheet_height_mm))
        if (poc.outer_shape, poc.perimeter_pixels) != (self.shape, self.perimeter_pixels):
            raise ManifestError(
                f"grid {self.shape}/{self.perimeter_pixels} px does not match the planner grid "
                f"{poc.outer_shape}/{poc.perimeter_pixels} px at scale {self.scale}"
            )
        return poc

    def perimeter_mask(self) -> np.ndarray:
        mask = np.ones(self.shape, dtype=bool)
        p = self.perimeter_pixels
        mask[p:-p, p:-p] = False
        return mask

    def to_json(self) -> dict[str, Any]:
        out = {
            "outer_pixels": self.outer_pixels,
            "perimeter_pixels": self.perimeter_pixels,
            "outer_sheet_mm": self.outer_sheet_mm,
            "scale": self.scale,
            "mm_per_pixel": self.mm_per_pixel,
            "artwork_pixels": self.artwork_pixels,
            "artwork_mm": self.artwork_mm,
            "raster_origin": "top-left pixel; CAD x right, y up, z toward viewer",
        }
        if self.outer_rows is not None:  # rectangular: the values above are x, these y
            out.update(outer_rows=self.outer_rows, outer_sheet_height_mm=self.outer_sheet_height_mm,
                       artwork_rows=self.artwork_rows, artwork_height_mm=self.artwork_height_mm,
                       raster_mm=list(self.raster_mm), edge_mm_per_side=list(self.edge_mm))
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> StackGrid:
        try:
            rows, height = data.get("outer_rows"), data.get("outer_sheet_height_mm")
            if (rows is None) != (height is None):
                raise ValueError("a rectangular grid needs both outer_rows and outer_sheet_height_mm")
            grid = cls(int(data["outer_pixels"]), int(data["perimeter_pixels"]), float(data["outer_sheet_mm"]),
                       int(data["scale"]), None if rows is None else int(rows), None if height is None else float(height))
        except (KeyError, TypeError, ValueError) as error:
            raise ManifestError(f"invalid grid block: {error}") from error
        grid.poc_config()
        return grid

    @classmethod
    def from_mask_shape(cls, outer_pixels: int, outer_sheet_mm: float) -> StackGrid:
        base = DEFAULT_CONFIG.outer_pixels
        if outer_pixels % base:
            raise ManifestError(f"mask size {outer_pixels} px is not a multiple of the planner grid {base} px")
        return cls.for_planner(outer_sheet_mm, outer_pixels // base)


def check_target_shape(target_rgb: np.ndarray, grid: StackGrid) -> None:
    """An artwork target fits the grid: RGB, and the artwork grid is the same whole multiple of it in both
    axes (so pixels stay square)."""

    rows, cols = grid.poc_config().analysis_shape
    if target_rgb.ndim != 3:
        raise StackError(f"target must be an RGB image, got {target_rgb.shape}")
    height, width = target_rgb.shape[:2]
    if cols % width or rows * width != cols * height:
        if grid.square:  # the message square stacks always had
            raise StackError(f"target must be a square RGB image whose size divides {cols} px, got {target_rgb.shape}")
        raise StackError(f"target must divide the {cols} x {rows} px artwork grid by the same factor in both axes, "
                         f"got {width} x {height} px")


_TARGET_LABELS: dict[tuple, tuple[np.ndarray, list[str]]] = {}
_TARGET_LABELS_LOCK = threading.Lock()


@dataclass
class Sheet:
    """One printed sheet: a single filament color and its retained material."""

    id: str
    color: str
    retained: np.ndarray
    visible: np.ndarray
    thickness_mm: float
    print_placement: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(_IDENTITY_PLACEMENT)))
    # Optional raised band on the shared outer perimeter: {"width_mm", "total_height_mm"}.
    # The next sheet rests on the rim, so it also sets the assembled spacing.
    rim: dict[str, float] | None = None
    # Optional exact shapely (Multi)Polygon in sheet mm; ``retained`` is then its grid raster.
    geometry: Any = None

    @property
    def stack_height_mm(self) -> float:
        return float(self.rim["total_height_mm"]) if self.rim else self.thickness_mm


@dataclass
class SheetStack:
    """Ordered physical sheets (``sheets[0]`` is the back sheet)."""

    name: str
    grid: StackGrid
    sheets: list[Sheet]
    target_rgb: np.ndarray | None = None
    frame_color: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    # L2 review record (base reference, edit log, validation); None for unedited stacks.
    review: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.check()

    # -- invariants -------------------------------------------------------
    def check(self) -> None:
        if not self.sheets:
            raise StackError("a stack needs at least one sheet")
        ids = [sheet.id for sheet in self.sheets]
        if len(set(ids)) != len(ids):
            raise StackError(f"duplicate sheet ids: {ids}")
        shape = self.grid.shape
        for sheet in self.sheets:
            if not _HEX.match(sheet.color):
                raise StackError(f"{sheet.id}: color must be '#RRGGBB' upper case, got {sheet.color!r}")
            for kind in ("retained", "visible"):
                mask = getattr(sheet, kind)
                if mask.shape != shape or mask.dtype != bool:
                    raise StackError(f"{sheet.id}: {kind} mask must be bool {shape}, got {mask.dtype} {mask.shape}")
            if not sheet.thickness_mm > 0:
                raise StackError(f"{sheet.id}: thickness must be positive")
            if sheet.rim is not None:
                self.check_rim(sheet.rim, sheet.thickness_mm, sheet.id)
        if self.frame_color is not None and not _HEX.match(self.frame_color):
            raise StackError(f"frame color must be '#RRGGBB' upper case, got {self.frame_color!r}")
        if self.target_rgb is not None:
            check_target_shape(self.target_rgb, self.grid)

    def check_rim(self, rim: dict[str, Any], thickness_mm: float, sheet_id: str) -> None:
        """A rim must lie on the solid shared perimeter and stand above the sheet."""

        if set(rim) != {"width_mm", "total_height_mm"}:
            raise StackError(f"{sheet_id}: rim needs exactly width_mm and total_height_mm, got {sorted(rim)}")
        band_mm = self.grid.perimeter_band_mm
        if not 0 < float(rim["width_mm"]) <= band_mm:
            raise StackError(f"{sheet_id}: rim width must be in (0, {band_mm:g}] mm (the shared perimeter band)")
        if not float(rim["total_height_mm"]) > thickness_mm:
            raise StackError(f"{sheet_id}: rim total height must exceed the sheet thickness {thickness_mm:g} mm")

    # -- derived views ------------------------------------------------------
    @property
    def front_to_back(self) -> list[Sheet]:
        return list(reversed(self.sheets))

    def export_names(self) -> list[str]:
        """File/assembly names from the current order and color (back to front).

        For planner output these equal the sheet ids; after reordering or recoloring
        they follow the edit while ``Sheet.id`` stays the stable internal identity.
        """

        return [f"layer_{order:02d}_{sheet.color[1:].lower()}" for order, sheet in enumerate(self.sheets)]

    def assembly_z_mm(self) -> list[float]:
        """Back face z of each sheet (back sheet at 0, front positive)."""

        z, out = 0.0, []
        for sheet in self.sheets:
            out.append(z)
            z += sheet.stack_height_mm
        return out

    def target_labels(self) -> tuple[np.ndarray, list[str]] | None:
        """Exact-indexed target embedded on the full grid (-1 on the perimeter)."""

        if self.target_rgb is None:
            return None
        # Memo by content (performance): the same reviewed target is indexed by every validation, edit
        # and inspector view of a project (~1.5 s at 1024 px).  Callers get their own copies.
        target = np.ascontiguousarray(self.target_rgb)
        key = (hashlib.sha256(target.tobytes()).hexdigest(), target.shape, target.dtype.str, self.grid.poc_config())
        with _TARGET_LABELS_LOCK:
            found = _TARGET_LABELS.get(key)
        if found is None:
            labels, palette = exact_index(self.target_rgb)
            found = (embed(labels.astype(np.int16), self.grid.poc_config(), -1), [hex_color(c) for c in palette])
            with _TARGET_LABELS_LOCK:
                _TARGET_LABELS[key] = found
                while len(_TARGET_LABELS) > 4:
                    _TARGET_LABELS.pop(next(iter(_TARGET_LABELS)))
        return found[0].copy(), list(found[1])

    def frame_mask(self) -> np.ndarray:
        """Artwork-grid pixels of the frame color (solid on every sheet, never visible)."""

        if self.frame_color is None:  # same result, without indexing the whole target (~1 s at 1024 px)
            return np.zeros(self.grid.shape, dtype=bool)
        found = self.target_labels()
        if found is None or self.frame_color is None:
            return np.zeros(self.grid.shape, dtype=bool)
        labels, palette = found
        if self.frame_color not in palette:
            raise StackError(f"frame color {self.frame_color} is not in the target palette")
        return labels == palette.index(self.frame_color)

    def composite_sheet_index(self) -> np.ndarray:
        """Back-to-front index of the frontmost sheet with material (-1 = none)."""

        shown = np.full(self.grid.shape, -1, dtype=np.int16)
        for index in range(len(self.sheets) - 1, -1, -1):
            shown[(shown < 0) & self.sheets[index].retained] = index
        return shown


# -- serialization -------------------------------------------------------------
# On Windows a file that another handle has open (a request reading it, a virus scanner) can be neither
# deleted nor replaced until it is closed (WinError 32 / 5); POSIX allows both.  Replacing is retried
# for at most REPLACE_WAIT_S; readers hold such files for milliseconds.
REPLACE_WAIT_S = 2.0
_IN_USE = (5, 32)  # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION


def _pause(seconds: float) -> None:  # the wait between attempts (replaced by tests)
    time.sleep(seconds)


def _retrying(action: Any, transient: Any, wait_s: float) -> Any:
    delay, waited = 0.005, 0.0
    while True:
        try:
            return action()
        except PermissionError as error:
            if os.name != "nt" or not transient(error) or waited >= wait_s:
                raise
        _pause(delay)
        waited += delay
        delay = min(delay * 2, 0.25)


def replace_path(source: Path, target: Path, wait_s: float = REPLACE_WAIT_S) -> None:
    """``os.replace``, retried while Windows reports the file (or a file in the folder) in use."""

    _retrying(lambda: os.replace(source, target), lambda e: getattr(e, "winerror", None) in _IN_USE, wait_s)


def read_bytes(path: Path, wait_s: float = REPLACE_WAIT_S) -> bytes:
    """``Path.read_bytes``, retried while Windows denies opening a file that is being replaced
    (the replaced file is briefly "delete pending"): for files read while a review is saved."""

    return _retrying(Path(path).read_bytes, lambda e: True, wait_s)


class _Replacing:
    """Files of one save, written beside the old ones (``stage``) and then swapped in (``commit``), in order.

    A reader sees each file whole, old or new.  A file with unchanged bytes is not touched.  If a swap
    fails, the files already swapped get their old bytes back and the partial files are removed, so the
    previous save stays complete and consistent."""

    def __init__(self) -> None:
        self.pending: list[tuple[Path, Path]] = []

    def stage(self, path: Path, save: Any) -> None:
        partial = path.with_name(path.name + ".partial" + path.suffix)  # same suffix: PIL picks the format
        self.pending.append((path, partial))
        save(partial)

    def discard(self) -> None:
        for _, partial in self.pending:
            partial.unlink(missing_ok=True)

    def commit(self) -> None:
        done: list[tuple[Path, bytes | None]] = []
        try:
            for path, partial in self.pending:
                old = path.read_bytes() if path.is_file() else None
                if old is not None and old == partial.read_bytes():
                    partial.unlink()
                    continue
                replace_path(partial, path)
                done.append((path, old))
        except BaseException:
            self.discard()
            for path, old in reversed(done):
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    restore = path.with_name(path.name + ".partial" + path.suffix)
                    restore.write_bytes(old)
                    replace_path(restore, path)
            raise


def _write_mask(mask: np.ndarray, path: Path, replacing: _Replacing | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.fromarray(mask.astype(np.uint8) * 255)
    if replacing is not None:
        replacing.stage(path, image.save)
    else:
        image.save(path)


def _read_mask(path: Path) -> np.ndarray:
    if not path.is_file():
        raise ManifestError(f"missing mask file: {path}")
    data = np.asarray(Image.open(path).convert("L"))
    if not np.isin(data, (0, 255)).all():
        raise ManifestError(f"mask is not binary 0/255: {path}")
    return data > 0


def _read_geometry(root: Path, entry: dict[str, Any], manifest: Path) -> Any:
    import shapely

    file = root / entry["file"]
    if not file.is_file():
        raise ManifestError(f"missing geometry file: {file}")
    data = file.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        raise ManifestError(f"{manifest}: {file.name} does not match its recorded hash")
    geometry = shapely.from_wkb(data)
    if geometry.geom_type not in ("Polygon", "MultiPolygon") or not geometry.is_valid:
        raise ManifestError(f"{manifest}: {file} is not a valid polygonal geometry")
    return geometry


def save_stack(stack: SheetStack, directory: Path, replacing: bool = False) -> Path:
    """Write the manifest, masks and target; return the manifest path.

    ``replacing``: all files are first written beside the old ones, then swapped in, the manifest
    last (``_Replacing``): files read concurrently are never missing or partial, and a failed save
    leaves the previous one intact (review projects are saved while the app reads them)."""

    stack.check()
    staged = _Replacing() if replacing else None

    def write(path: Path, save: Any) -> None:
        if staged is not None:
            staged.stage(path, save)
        else:
            save(path)

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    artwork: dict[str, Any] = {"target_image": None, "frame_color": stack.frame_color}
    if stack.target_rgb is not None:
        write(directory / "target.png", Image.fromarray(stack.target_rgb).save)
        artwork.update(
            target_image="target.png",
            target_pixel_size=[int(stack.target_rgb.shape[1]), int(stack.target_rgb.shape[0])],
            target_rgb_sha256=hashlib.sha256(np.ascontiguousarray(stack.target_rgb).tobytes()).hexdigest(),
        )
    rows = []
    for order, (sheet, z) in enumerate(zip(stack.sheets, stack.assembly_z_mm())):
        retained = f"layers/{sheet.id}/{RETAINED_MASK_NAME}"
        visible = f"layers/{sheet.id}/{VISIBLE_MASK_NAME}"
        _write_mask(sheet.retained, directory / retained, staged)
        _write_mask(sheet.visible, directory / visible, staged)
        rows.append(
            {
                "id": sheet.id,
                "order_back_to_front": order,
                "color": sheet.color,
                "thickness_mm": sheet.thickness_mm,
                "assembly_z_mm": z,
                "retained_mask": retained,
                "retained_bits_sha256": mask_bits_sha256(sheet.retained),
                "visible_mask": visible,
                "visible_bits_sha256": mask_bits_sha256(sheet.visible),
                "print_placement": sheet.print_placement,
            }
        )
        if sheet.rim is not None:
            rows[-1]["rim"] = dict(sheet.rim)
        if sheet.geometry is not None:
            import shapely

            relative = f"layers/{sheet.id}/{GEOMETRY_NAME}"
            data = shapely.to_wkb(sheet.geometry)
            write(directory / relative, lambda f, data=data: f.write_bytes(data))
            rows[-1]["geometry"] = {
                "file": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "units": "mm",
                "area_mm2": float(sheet.geometry.area),
                "retained_mask": "grid raster of this geometry (4x supersampled majority)",
            }
        rows[-1]["export_name"] = stack.export_names()[order]
    vector = any(sheet.geometry is not None for sheet in stack.sheets)
    reviewed = stack.review is not None or any(sheet.rim is not None for sheet in stack.sheets)
    manifest = {
        "schema": SCHEMA,
        "schema_version": VECTOR_SCHEMA_VERSION if vector else REVIEW_SCHEMA_VERSION if reviewed else SCHEMA_VERSION,
        "name": stack.name,
        "units": "mm",
        "grid": stack.grid.to_json(),
        "artwork": artwork,
        "construction": {
            "method": "registered_layer_sheets",
            "one_filament_per_sheet": True,
            "order": "back_to_front",
            "assembled_z_axis": "front_positive",
            "mask_polarity": "255 = retained material, 0 = through-cutout",
        },
        "sheets": rows,
        "provenance": stack.provenance,
    }
    if stack.review is not None:
        manifest["review"] = stack.review
    path = directory / MANIFEST_NAME
    text = json.dumps(manifest, indent=2) + "\n"
    try:
        write(path, lambda f: f.write_text(text, encoding="utf-8"))
        if staged is not None:
            staged.commit()
    except BaseException:
        if staged is not None:
            staged.discard()
        raise
    return path


def load_stack(path: Path) -> SheetStack:
    """Load a stack from its directory or ``sheet_stack.json``; verify mask hashes."""

    path = Path(path)
    if path.is_dir():
        path = path / MANIFEST_NAME
    if not path.is_file():
        raise ManifestError(f"no sheet-stack manifest at {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ManifestError(f"{path}: invalid JSON: {error}") from error
    if manifest.get("schema") != SCHEMA:
        hint = ""
        if "layers" in manifest and "construction" in manifest:
            hint = " (vector LayerStack manifests are built with `python -m layerforge_l1 build`)"
        raise ManifestError(f"{path}: schema is {manifest.get('schema')!r}, expected {SCHEMA!r}{hint}")
    version = manifest.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ManifestError(f"{path}: unsupported schema_version {version!r}; supported {SUPPORTED_SCHEMA_VERSIONS}")
    root = path.parent
    grid = StackGrid.from_json(manifest.get("grid", {}))
    rows = manifest.get("sheets")
    if not isinstance(rows, list) or not rows:
        raise ManifestError(f"{path}: 'sheets' must be a non-empty list")
    if [row.get("order_back_to_front") for row in rows] != list(range(len(rows))):
        raise ManifestError(f"{path}: sheets must be listed back to front with order 0..{len(rows) - 1}")
    sheets = []
    for row in rows:
        try:
            sheet = Sheet(
                id=str(row["id"]),
                color=str(row["color"]),
                retained=_read_mask(root / row["retained_mask"]),
                visible=_read_mask(root / row["visible_mask"]),
                thickness_mm=float(row["thickness_mm"]),
                print_placement=row.get("print_placement", dict(_IDENTITY_PLACEMENT)),
                rim={k: float(v) for k, v in row["rim"].items()} if row.get("rim") else None,
                geometry=_read_geometry(root, row["geometry"], path) if row.get("geometry") else None,
            )
        except KeyError as error:
            raise ManifestError(f"{path}: sheet entry missing {error}") from error
        for kind in ("retained", "visible"):
            expected = row.get(f"{kind}_bits_sha256")
            if expected and mask_bits_sha256(getattr(sheet, kind)) != expected:
                raise ManifestError(f"{path}: {sheet.id} {kind} mask does not match its recorded hash")
        sheets.append(sheet)
    artwork = manifest.get("artwork", {})
    target = None
    if artwork.get("target_image"):
        target_path = root / artwork["target_image"]
        if not target_path.is_file():
            raise ManifestError(f"missing target image: {target_path}")
        target = np.asarray(Image.open(target_path).convert("RGB"))
        expected = artwork.get("target_rgb_sha256")
        if expected and hashlib.sha256(np.ascontiguousarray(target).tobytes()).hexdigest() != expected:
            raise ManifestError(f"{path}: target image does not match its recorded hash")
    try:
        stack = SheetStack(
            name=str(manifest.get("name", root.name)),
            grid=grid,
            sheets=sheets,
            target_rgb=target,
            frame_color=artwork.get("frame_color"),
            provenance=manifest.get("provenance", {}),
            review=manifest.get("review"),
        )
    except StackError as error:
        raise ManifestError(f"{path}: {error}") from error
    for sheet in stack.sheets:
        if sheet.geometry is not None:
            from .cad import rasterize_geometry

            if not np.array_equal(rasterize_geometry(sheet.geometry, grid), sheet.retained):
                raise ManifestError(f"{path}: {sheet.id} retained mask is not the raster of its vector geometry")
    for row, z in zip(rows, stack.assembly_z_mm()):
        if "assembly_z_mm" in row and abs(float(row["assembly_z_mm"]) - z) > 1e-9:
            raise ManifestError(f"{path}: {row['id']} assembly_z_mm {row['assembly_z_mm']} != cumulative {z}")
    return stack


def import_planner_output(
    layers_dir: Path,
    *,
    outer_sheet_mm: float = 225.0,
    thickness_mm: float = 1.6,
    target_image: Path | None = None,
    frame_color: str | None = None,
    name: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> SheetStack:
    """Migrate a manifest-less planner ``layers/`` directory (pre-L0 format).

    Directory names are ``layer_<back index>_<rrggbb>`` as written by
    ``scripts/run_turtle_reveal_planner_poc.py``; ``outer_sheet_mm`` and
    ``thickness_mm`` were command-line values there and must be supplied.
    """

    layers_dir = Path(layers_dir)
    pattern = re.compile(r"^layer_(\d+)_([0-9a-fA-F]{6})$")
    entries = []
    for child in sorted(layers_dir.iterdir()) if layers_dir.is_dir() else []:
        match = pattern.match(child.name)
        if child.is_dir() and match:
            entries.append((int(match.group(1)), child, "#" + match.group(2).upper()))
    if not entries:
        raise ManifestError(f"no layer_<index>_<rrggbb> directories in {layers_dir}")
    entries.sort()
    if [index for index, _, _ in entries] != list(range(len(entries))):
        raise ManifestError(f"{layers_dir}: layer indices are not 0..{len(entries) - 1}")
    sheets = [
        Sheet(
            id=child.name,
            color=color,
            retained=_read_mask(child / RETAINED_MASK_NAME),
            visible=_read_mask(child / VISIBLE_MASK_NAME),
            thickness_mm=thickness_mm,
        )
        for _, child, color in entries
    ]
    grid = StackGrid.from_mask_shape(sheets[0].retained.shape[0], outer_sheet_mm)
    target = np.asarray(Image.open(target_image).convert("RGB")) if target_image else None
    record = {
        "migrated_from": "planner layers/ directory without manifest (pre-L0)",
        "source_layers_dir": layers_dir.as_posix(),
        "supplied_outer_sheet_mm": outer_sheet_mm,
        "supplied_thickness_mm": thickness_mm,
    }
    if target_image:
        record["source_target_image"] = Path(target_image).as_posix()
        record["source_target_file_sha256"] = file_sha256(Path(target_image))
    record.update(provenance or {})
    return SheetStack(
        name=name or layers_dir.parent.name,
        grid=grid,
        sheets=sheets,
        target_rgb=target,
        frame_color=frame_color.upper() if frame_color else None,
        provenance=record,
    )
