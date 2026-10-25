"""Sheet-stack data model and versioned manifest serialization.

A ``SheetStack`` is the mask-based physical stack produced by the reveal
planner: every sheet is one filament color, all sheets share one square
raster grid (identical outer XY size and a solid shared perimeter), and the
order is back to front.  Masks are boolean arrays; ``True`` is retained
material and ``False`` is a through-cutout.

On disk a stack is a directory::

    sheet_stack.json
    target.png                                   (optional approved artwork)
    layers/<sheet id>/retained_material_mask.png (255 = material)
    layers/<sheet id>/source_visible_region_mask.png

The ``layers/`` layout is the one already written by
``scripts/run_turtle_reveal_planner_poc.py``; ``import_planner_output``
migrates such a directory (which has no manifest) into a versioned stack.
"""

from __future__ import annotations

import hashlib
import json
import re
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
SUPPORTED_SCHEMA_VERSIONS = ("1.0.0",)
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
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class StackGrid:
    """Shared raster: square outer sheet with a solid perimeter band."""

    outer_pixels: int
    perimeter_pixels: int
    outer_sheet_mm: float
    scale: int

    @classmethod
    def for_planner(cls, outer_sheet_mm: float, scale: int) -> StackGrid:
        poc = physical_grid(RevealConfig(outer_sheet_mm=outer_sheet_mm, scale=scale))
        return cls(poc.outer_pixels, poc.perimeter_pixels, poc.sheet_size_mm, scale)

    @property
    def mm_per_pixel(self) -> float:
        return self.outer_sheet_mm / self.outer_pixels

    @property
    def artwork_pixels(self) -> int:
        return self.outer_pixels - 2 * self.perimeter_pixels

    @property
    def artwork_mm(self) -> float:
        return self.artwork_pixels * self.mm_per_pixel

    def poc_config(self) -> PocConfig:
        """The validator grid (``PocConfig``) this stack was planned on."""

        poc = physical_grid(RevealConfig(outer_sheet_mm=self.outer_sheet_mm, scale=self.scale))
        if (poc.outer_pixels, poc.perimeter_pixels) != (self.outer_pixels, self.perimeter_pixels):
            raise ManifestError(
                f"grid {self.outer_pixels}/{self.perimeter_pixels} px does not match the planner grid "
                f"{poc.outer_pixels}/{poc.perimeter_pixels} px at scale {self.scale}"
            )
        return poc

    def perimeter_mask(self) -> np.ndarray:
        mask = np.ones((self.outer_pixels, self.outer_pixels), dtype=bool)
        p = self.perimeter_pixels
        mask[p:-p, p:-p] = False
        return mask

    def to_json(self) -> dict[str, Any]:
        return {
            "outer_pixels": self.outer_pixels,
            "perimeter_pixels": self.perimeter_pixels,
            "outer_sheet_mm": self.outer_sheet_mm,
            "scale": self.scale,
            "mm_per_pixel": self.mm_per_pixel,
            "artwork_pixels": self.artwork_pixels,
            "artwork_mm": self.artwork_mm,
            "raster_origin": "top-left pixel; CAD x right, y up, z toward viewer",
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> StackGrid:
        try:
            grid = cls(int(data["outer_pixels"]), int(data["perimeter_pixels"]),
                       float(data["outer_sheet_mm"]), int(data["scale"]))
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


@dataclass
class Sheet:
    """One printed sheet: a single filament color and its retained material."""

    id: str
    color: str
    retained: np.ndarray
    visible: np.ndarray
    thickness_mm: float
    print_placement: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(_IDENTITY_PLACEMENT)))


@dataclass
class SheetStack:
    """Ordered physical sheets (``sheets[0]`` is the back sheet)."""

    name: str
    grid: StackGrid
    sheets: list[Sheet]
    target_rgb: np.ndarray | None = None
    frame_color: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.check()

    # -- invariants -------------------------------------------------------
    def check(self) -> None:
        if not self.sheets:
            raise StackError("a stack needs at least one sheet")
        ids = [sheet.id for sheet in self.sheets]
        if len(set(ids)) != len(ids):
            raise StackError(f"duplicate sheet ids: {ids}")
        shape = (self.grid.outer_pixels, self.grid.outer_pixels)
        for sheet in self.sheets:
            if not _HEX.match(sheet.color):
                raise StackError(f"{sheet.id}: color must be '#RRGGBB' upper case, got {sheet.color!r}")
            for kind in ("retained", "visible"):
                mask = getattr(sheet, kind)
                if mask.shape != shape or mask.dtype != bool:
                    raise StackError(f"{sheet.id}: {kind} mask must be bool {shape}, got {mask.dtype} {mask.shape}")
            if not sheet.thickness_mm > 0:
                raise StackError(f"{sheet.id}: thickness must be positive")
        if self.frame_color is not None and not _HEX.match(self.frame_color):
            raise StackError(f"frame color must be '#RRGGBB' upper case, got {self.frame_color!r}")
        if self.target_rgb is not None:
            height, width = self.target_rgb.shape[:2]
            poc = self.grid.poc_config()
            if self.target_rgb.ndim != 3 or height != width or poc.analysis_pixels % height:
                raise StackError(
                    f"target must be a square RGB image whose size divides {poc.analysis_pixels} px, "
                    f"got {self.target_rgb.shape}"
                )

    # -- derived views ------------------------------------------------------
    @property
    def front_to_back(self) -> list[Sheet]:
        return list(reversed(self.sheets))

    def assembly_z_mm(self) -> list[float]:
        """Back face z of each sheet (back sheet at 0, front positive)."""

        z, out = 0.0, []
        for sheet in self.sheets:
            out.append(z)
            z += sheet.thickness_mm
        return out

    def target_labels(self) -> tuple[np.ndarray, list[str]] | None:
        """Exact-indexed target embedded on the full grid (-1 on the perimeter)."""

        if self.target_rgb is None:
            return None
        labels, palette = exact_index(self.target_rgb)
        return embed(labels.astype(np.int16), self.grid.poc_config(), -1), [hex_color(c) for c in palette]

    def frame_mask(self) -> np.ndarray:
        """Artwork-grid pixels of the frame color (solid on every sheet, never visible)."""

        found = self.target_labels()
        if found is None or self.frame_color is None:
            return np.zeros((self.grid.outer_pixels,) * 2, dtype=bool)
        labels, palette = found
        if self.frame_color not in palette:
            raise StackError(f"frame color {self.frame_color} is not in the target palette")
        return labels == palette.index(self.frame_color)

    def composite_sheet_index(self) -> np.ndarray:
        """Back-to-front index of the frontmost sheet with material (-1 = none)."""

        shown = np.full((self.grid.outer_pixels,) * 2, -1, dtype=np.int16)
        for index in range(len(self.sheets) - 1, -1, -1):
            shown[(shown < 0) & self.sheets[index].retained] = index
        return shown


# -- serialization -------------------------------------------------------------
def _write_mask(mask: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255).save(path)


def _read_mask(path: Path) -> np.ndarray:
    if not path.is_file():
        raise ManifestError(f"missing mask file: {path}")
    data = np.asarray(Image.open(path).convert("L"))
    if not np.isin(data, (0, 255)).all():
        raise ManifestError(f"mask is not binary 0/255: {path}")
    return data > 0


def save_stack(stack: SheetStack, directory: Path) -> Path:
    """Write the manifest, masks and target; return the manifest path."""

    stack.check()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    artwork: dict[str, Any] = {"target_image": None, "frame_color": stack.frame_color}
    if stack.target_rgb is not None:
        Image.fromarray(stack.target_rgb).save(directory / "target.png")
        artwork.update(
            target_image="target.png",
            target_pixel_size=[int(stack.target_rgb.shape[1]), int(stack.target_rgb.shape[0])],
            target_rgb_sha256=hashlib.sha256(np.ascontiguousarray(stack.target_rgb).tobytes()).hexdigest(),
        )
    rows = []
    for order, (sheet, z) in enumerate(zip(stack.sheets, stack.assembly_z_mm())):
        retained = f"layers/{sheet.id}/{RETAINED_MASK_NAME}"
        visible = f"layers/{sheet.id}/{VISIBLE_MASK_NAME}"
        _write_mask(sheet.retained, directory / retained)
        _write_mask(sheet.visible, directory / visible)
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
    manifest = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
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
    path = directory / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
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
        )
    except StackError as error:
        raise ManifestError(f"{path}: {error}") from error
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
