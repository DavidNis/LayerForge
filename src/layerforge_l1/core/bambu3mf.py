"""Optional Bambu Studio multi-plate project (.3mf) for an L3 print kit.

``write_bambu_project`` reads a finished kit (``print_kit.json`` plus the
validated, reopened ``cad/stl`` meshes) and writes one Bambu Studio project:

* one plate per physical sheet, plate 1 = order 00 (back) ... last plate = front,
  each plate named with its stack position and file;
* each sheet exactly as exported (same triangles, no rotation, no scaling): back
  face (z = 0) on the bed, the optional rim on top, translated only, into a free
  spot of the selected printer's printable area that avoids its keep-out zones;
* one filament slot per sheet color (repeated colors share a slot) carrying that
  color.  No AMS is assumed: only the single-extruder filament map is written,
  never an AMS slot mapping, and every plate uses exactly one filament;
* Bambu's "assembly" placement of every sheet at its stack height.

Package layout (OBSERVED_IN_FILE in projects saved by Bambu Studio 02.05-02.08):
``3D/3dmodel.model`` with production-extension components pointing at
``3D/Objects/object_<n>.model``, ``Metadata/model_settings.config`` (objects,
plates, assembly) and ``Metadata/project_settings.config``.

Project settings.  Bambu Studio crashes on a project whose settings are partial
(observed with 02.08.02.61, also with ``--uptodate``), and its command line does
not resolve preset inheritance, so the settings are built here from the system
profiles of the *installed* Bambu Studio (``BBL.json`` index, ``inherits`` and
``include`` resolved): the selected printer, its 0.2 mm (or matching) system
process and "Generic PLA" for every filament slot, unmodified, so Bambu Studio
shows them as its own system presets.  No Bambu profile data is stored in this
repository; without a Bambu Studio installation no project is written.

When ``bambu-studio`` itself is found, the written project is loaded and sliced
with its command line and the result (plates, objects, positions, sizes,
filaments, filament changes) is compared with the kit.  The STEP/STL exports are
never touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

import numpy as np

# Bambu Studio treats a 3MF as one of its projects (plates, presets) only when the
# Application metadata starts with this prefix; other generators are loaded as
# geometry only (observed: the CLI then reports every plate empty).  It is a format
# marker: the description inside the file and the kit report say LayerForge wrote it.
BAMBU_APPLICATION_PREFIX = "BambuStudio-"
DEVELOPED_WITH = "02.08.02.61"
PLATE_GAP = 0.2            # Bambu lays plates out in a grid, one plate size + 20 % apart
PLACEMENT_STEP_MM = 0.5
KEEP_OUT_CLEARANCE_MM = 1.0
POSITION_TOLERANCE_MM = 0.01
CLI_TIMEOUT_S = 600
# Preset keys that describe the preset file itself, not a setting of the project.
_PRESET_META = {"name", "inherits", "include", "from", "type", "instantiation", "setting_id", "description",
                "compatible_printers", "compatible_printers_condition", "filament_id", "version", "renamed_from",
                "is_custom_defined", "base_id", "user_id", "updated_time"}


class BambuExportError(ValueError):
    """The kit cannot be written as a Bambu Studio project (reasons listed)."""


@dataclass(frozen=True)
class BambuPrinter:
    preset: str                      # printer_settings_id
    model: str                       # printer_model
    printable_area: tuple[tuple[float, float], ...]
    printable_height: float
    keep_out: tuple[tuple[tuple[float, float], ...], ...]   # bed exclude + wrap-detection zones
    process_by_layer_height: tuple[tuple[float, str], ...]
    filament_preset: str
    best_object_pos: tuple[float, float] = (0.5, 0.5)

    @property
    def bed_mm(self) -> tuple[float, float]:
        xs, ys = zip(*self.printable_area)
        return max(xs) - min(xs), max(ys) - min(ys)


def _square(x0: float, y0: float, x1: float, y1: float) -> tuple[tuple[float, float], ...]:
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def _x1c_processes(suffix: str) -> tuple[tuple[float, str], ...]:
    return ((0.08, f"0.08mm Extra Fine @BBL {suffix}"), (0.12, f"0.12mm Fine @BBL {suffix}"),
            (0.16, f"0.16mm Optimal @BBL {suffix}"), (0.20, f"0.20mm Standard @BBL {suffix}"),
            (0.24, f"0.24mm Draft @BBL {suffix}"), (0.28, f"0.28mm Extra Draft @BBL {suffix}"))


def _new_processes(suffix: str) -> tuple[tuple[float, str], ...]:
    return ((0.08, f"0.08mm High Quality @BBL {suffix}"), (0.12, f"0.12mm High Quality @BBL {suffix}"),
            (0.16, f"0.16mm Standard @BBL {suffix}"), (0.20, f"0.20mm Standard @BBL {suffix}"),
            (0.24, f"0.24mm Standard @BBL {suffix}"))


_BED_256 = _square(0, 0, 256, 256)
_X1_EXCLUDE = (_square(0, 0, 18, 28),)
# Single-nozzle Bambu printers with a 0.4 mm nozzle; bed values as read from the Bambu
# Studio 02.08 system profiles (re-read from the installed profiles at export time).
# Dual-nozzle models (H2D, X2D, H2C) have per-extruder printable areas and are not offered.
BAMBU_PRINTERS: dict[str, BambuPrinter] = {p.preset: p for p in (
    BambuPrinter("Bambu Lab P1S 0.4 nozzle", "Bambu Lab P1S", _BED_256, 250, _X1_EXCLUDE,
                 _x1c_processes("X1C"), "Generic PLA"),
    BambuPrinter("Bambu Lab P1P 0.4 nozzle", "Bambu Lab P1P", _BED_256, 250, _X1_EXCLUDE,
                 _x1c_processes("P1P"), "Generic PLA @BBL P1P"),
    BambuPrinter("Bambu Lab X1 Carbon 0.4 nozzle", "Bambu Lab X1 Carbon", _BED_256, 250, _X1_EXCLUDE,
                 _x1c_processes("X1C"), "Generic PLA"),
    BambuPrinter("Bambu Lab X1 0.4 nozzle", "Bambu Lab X1", _BED_256, 250, _X1_EXCLUDE,
                 _x1c_processes("X1C"), "Generic PLA"),
    BambuPrinter("Bambu Lab X1E 0.4 nozzle", "Bambu Lab X1E", _BED_256, 250, _X1_EXCLUDE,
                 _x1c_processes("X1C"), "Generic PLA"),
    BambuPrinter("Bambu Lab A1 0.4 nozzle", "Bambu Lab A1", _BED_256, 256, (_square(226, 224, 256, 256),),
                 _x1c_processes("A1"), "Generic PLA @BBL A1"),
    BambuPrinter("Bambu Lab A1 mini 0.4 nozzle", "Bambu Lab A1 mini", _square(0, 0, 180, 180), 180,
                 (_square(156, 152, 180, 180),), _x1c_processes("A1M"), "Generic PLA @BBL A1M", (0.7, 0.5)),
    BambuPrinter("Bambu Lab P2S 0.4 nozzle", "Bambu Lab P2S", _BED_256, 256, (_square(153, 235, 216, 256),),
                 _new_processes("P2S"), "Generic PLA @BBL P2S"),
    BambuPrinter("Bambu Lab H2S 0.4 nozzle", "Bambu Lab H2S", _square(0, 0, 340, 320), 340,
                 (_square(172.3, 302, 232.5, 322),), _new_processes("H2S"), "Generic PLA @BBL H2S"),
)}
DEFAULT_PRINTER = "Bambu Lab P1S 0.4 nozzle"


# -- the installed Bambu Studio ------------------------------------------------------------
def _version_key(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text or "0"))


class BambuProfiles:
    """System presets of an installed Bambu Studio (``<root>/BBL.json`` and ``<root>/BBL``)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        vendor = json.loads((self.root / "BBL.json").read_text(encoding="utf-8"))
        self.version = vendor.get("version", "")
        self._paths: dict[str, Path] = {}
        for key in ("machine_model_list", "machine_list", "process_list", "filament_list"):
            for entry in vendor.get(key, []):
                self._paths[entry["name"]] = self.root / "BBL" / entry["sub_path"]

    def raw(self, name: str) -> dict[str, Any]:
        if name not in self._paths:
            raise BambuExportError(f"Bambu Studio preset {name!r} not found in {self.root}")
        return json.loads(self._paths[name].read_text(encoding="utf-8"))

    def resolve(self, name: str) -> dict[str, Any]:
        """The preset with its ``inherits`` chain and ``include`` templates applied."""

        data = self.raw(name)
        out = self.resolve(data["inherits"]) if data.get("inherits") else {}
        for included in data.get("include") or []:
            out.update(self.raw(included))
        out.update(data)
        return out


def _profile_roots() -> list[Path]:
    env = os.environ.get("LAYERFORGE_BAMBU_PROFILES")
    roots = [Path(env)] if env else []
    home = Path.home()
    if sys.platform == "win32":
        roots += [Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "BambuStudio" / "system",
                  Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Bambu Studio" / "resources" / "profiles"]
    elif sys.platform == "darwin":
        roots += [home / "Library" / "Application Support" / "BambuStudio" / "system",
                  Path("/Applications/BambuStudio.app/Contents/Resources/profiles")]
    else:
        roots += [home / ".config" / "BambuStudio" / "system"]
    return roots


def find_profiles() -> BambuProfiles | None:
    """The newest system preset set of the installed Bambu Studio (the GUI uses its updated
    copy in the user profile when there is one)."""

    found = []
    for root in _profile_roots():
        try:
            found.append(BambuProfiles(root))
        except (OSError, ValueError, KeyError):
            continue
    return max(found, key=lambda p: _version_key(p.version)) if found else None


def find_bambu_studio() -> Path | None:
    env = os.environ.get("LAYERFORGE_BAMBU_STUDIO")
    candidates = [Path(env)] if env else []
    if sys.platform == "win32":
        candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Bambu Studio" / "bambu-studio.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"))
    found = shutil.which("bambu-studio")
    if found:
        candidates.append(Path(found))
    return next((c for c in candidates if c.is_file()), None)


def bambu_studio_version(exe: Path | None) -> str | None:
    """``02.08.02.61``-style version of the executable (Windows file version), if readable."""

    if exe is None or sys.platform != "win32":
        return None
    import ctypes

    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(exe), None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(str(exe), 0, size, buffer):
        return None
    pointer, length = ctypes.c_void_p(), ctypes.c_uint()
    if not ctypes.windll.version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return None
    words = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
    ms, ls = words[4], words[5]
    return f"{ms >> 16:02d}.{ms & 0xFFFF:02d}.{ls >> 16:02d}.{ls & 0xFFFF:02d}"


def printer_choices() -> list[dict[str, Any]]:
    return [{"preset": p.preset, "model": p.model, "bed_mm": list(p.bed_mm), "max_z_mm": p.printable_height}
            for p in BAMBU_PRINTERS.values()]


def environment() -> dict[str, Any]:
    """What the exporter can use on this computer (for the UI)."""

    profiles, exe = find_profiles(), find_bambu_studio()
    return {"printers": printer_choices(), "default_printer": DEFAULT_PRINTER,
            "profiles": profiles.root.as_posix() if profiles else None,
            "profiles_version": profiles.version if profiles else None,
            "bambu_studio": exe.as_posix() if exe else None, "bambu_studio_version": bambu_studio_version(exe)}


# -- planning ------------------------------------------------------------------------------
def _points(values: list[str]) -> tuple[tuple[float, float], ...]:
    return tuple(tuple(float(v) for v in item.split("x")) for item in values)


def printer_from_profiles(printer: BambuPrinter, machine: dict[str, Any]) -> BambuPrinter:
    """``printer`` with the bed geometry of the installed machine profile."""

    zones = [_points(machine[key]) for key in ("bed_exclude_area", "head_wrap_detect_zone", "wrapping_exclude_area")
             if machine.get(key)]
    best = tuple(float(v) for v in machine.get("best_object_pos", "0.5x0.5").split("x"))
    return BambuPrinter(printer.preset, machine.get("printer_model", printer.model), _points(machine["printable_area"]),
                        float(machine["printable_height"]), tuple(zones), printer.process_by_layer_height,
                        printer.filament_preset, best)


def plate_columns(count: int) -> int:
    """Bambu Studio's plate grid width for ``count`` plates (rounded square root)."""

    value = count ** 0.5
    rounded = round(value)
    return int(rounded + 1 if value > rounded else rounded)


def plate_origin(index: int, count: int, printer: BambuPrinter) -> tuple[float, float]:
    width, depth = printer.bed_mm
    columns = plate_columns(count)
    return (index % columns) * width * (1 + PLATE_GAP), -(index // columns) * depth * (1 + PLATE_GAP)


def place_on_bed(size_xy: tuple[float, float], printer: BambuPrinter) -> tuple[float, float] | None:
    """Lower-left corner for a footprint of ``size_xy`` inside the printable area, clear of every
    keep-out zone, as close as possible to Bambu's preferred object position; None if none exists."""

    from shapely.geometry import Polygon, box

    area = Polygon(printer.printable_area)
    zones = [Polygon(z).buffer(KEEP_OUT_CLEARANCE_MM, join_style=2) for z in printer.keep_out]
    w, d = size_xy
    x0, y0, x1, y1 = area.bounds
    target = (x0 + (x1 - x0) * printer.best_object_pos[0] - w / 2, y0 + (y1 - y0) * printer.best_object_pos[1] - d / 2)
    xs = np.arange(x0, x1 - w + 1e-9, PLACEMENT_STEP_MM)
    ys = np.arange(y0, y1 - d + 1e-9, PLACEMENT_STEP_MM)
    candidates = sorted(((x - target[0]) ** 2 + (y - target[1]) ** 2, x, y) for x in xs for y in ys)
    candidates.insert(0, (0.0, target[0], target[1]))
    for _, x, y in candidates:
        footprint = box(x, y, x + w, y + d)
        if area.buffer(1e-6).contains(footprint) and not any(footprint.intersects(z) for z in zones):
            return round(float(x), 4), round(float(y), 4)
    return None


def _process_for(printer: BambuPrinter, layer_height_mm: float) -> tuple[str, bool]:
    for height, name in printer.process_by_layer_height:
        if abs(height - layer_height_mm) < 1e-6:
            return name, True
    return dict(printer.process_by_layer_height)[0.20], False


def plan_project(kit: dict[str, Any], kit_dir: Path, printer: BambuPrinter) -> dict[str, Any]:
    """Plates, filament slots and placements for a kit, plus every sheet that does not fit."""

    import trimesh

    rows = kit["assembly_back_to_front"]
    colors: list[str] = []
    for row in rows:
        if row["color_hex"].upper() not in colors:
            colors.append(row["color_hex"].upper())
    plates, problems = [], []
    for index, row in enumerate(rows):
        mesh = trimesh.load_mesh(Path(kit_dir) / row["file_stl"], file_type="stl", process=True)
        lo, hi = mesh.bounds
        size = (float(hi[0] - lo[0]), float(hi[1] - lo[1]), float(hi[2] - lo[2]))
        spot = place_on_bed(size[:2], printer)
        name = Path(row["file_stl"]).stem
        if spot is None:
            problems.append(f"{name}: {size[0]:.2f} x {size[1]:.2f} mm does not fit the printable area of "
                            f"{printer.preset} ({printer.bed_mm[0]:g} x {printer.bed_mm[1]:g} mm, keep-out zones "
                            f"and {KEEP_OUT_CLEARANCE_MM:g} mm clearance excluded)")
        if size[2] > printer.printable_height + 1e-6:
            problems.append(f"{name}: {size[2]:.2f} mm is taller than {printer.printable_height:g} mm")
        position = "back" if index == 0 else "front" if index == len(rows) - 1 else "middle"
        plates.append({
            "plate": index + 1,
            "plate_name": f"{row['order_back_to_front']:02d} {position} - {name}",
            "order_back_to_front": row["order_back_to_front"],
            "object": name,
            "file_stl": row["file_stl"],
            "color_hex": row["color_hex"].upper(),
            "filament_slot": colors.index(row["color_hex"].upper()) + 1,
            "rim": row["rim"],
            "size_mm": [round(v, 4) for v in size],
            "triangles": int(len(mesh.faces)),
            "mesh_min_mm": [round(float(v), 6) for v in lo],
            "bed_position_mm": list(spot) if spot else None,
            "assembly_z0_mm": row["assembly_z0_mm"],
            "_mesh": mesh,
        })
    return {"colors": colors, "plates": plates, "problems": problems}


# -- writing -------------------------------------------------------------------------------
def _uuid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"layerforge:{seed}"))


def _num(value: float) -> str:
    return f"{float(value):.9g}"


def _object_model(object_id: int, seed: str, mesh: Any) -> str:
    vertices = "\n".join(f'     <vertex x="{_num(x)}" y="{_num(y)}" z="{_num(z)}"/>' for x, y, z in mesh.vertices)
    triangles = "\n".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
        'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">\n'
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
        ' <resources>\n'
        f'  <object id="{object_id}" p:UUID="{_uuid(seed + ":mesh")}" type="model">\n'
        '   <mesh>\n    <vertices>\n' + vertices + '\n    </vertices>\n    <triangles>\n' + triangles +
        '\n    </triangles>\n   </mesh>\n  </object>\n </resources>\n <build/>\n</model>\n'
    )


def _transform(x: float, y: float, z: float) -> str:
    return f"1 0 0 0 1 0 0 0 1 {_num(x)} {_num(y)} {_num(z)}"


def _main_model(kit: dict[str, Any], plan: dict[str, Any], placements: list[tuple[float, float, float]],
                application: str) -> str:
    today = date.today().isoformat()
    title = quoteattr(kit["name"])[1:-1]
    objects, items = [], []
    for index, plate in enumerate(plan["plates"]):
        mesh_id, object_id = 2 * index + 1, 2 * index + 2
        seed = f"{kit['name']}:{plate['object']}"
        objects.append(
            f'  <object id="{object_id}" p:UUID="{_uuid(seed + ":object")}" type="model">\n   <components>\n'
            f'    <component p:path="/3D/Objects/object_{index + 1}.model" objectid="{mesh_id}" '
            f'p:UUID="{_uuid(seed + ":component")}" transform="{_transform(0, 0, 0)}"/>\n   </components>\n  </object>')
        items.append(f'  <item objectid="{object_id}" p:UUID="{_uuid(seed + ":item")}" '
                     f'transform="{_transform(*placements[index])}" printable="1"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
        'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">\n'
        f' <metadata name="Application">{application}</metadata>\n'
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
        f' <metadata name="CreationDate">{today}</metadata>\n'
        f' <metadata name="Description">LayerForge print kit {title}: one plate per layer sheet, plate 1 = back '
        'sheet. Written by LayerForge in the Bambu Studio project format.</metadata>\n'
        f' <metadata name="ModificationDate">{today}</metadata>\n'
        f' <metadata name="Title">{title}</metadata>\n'
        ' <resources>\n' + "\n".join(objects) + '\n </resources>\n'
        f' <build p:UUID="{_uuid(kit["name"] + ":build")}">\n' + "\n".join(items) + '\n </build>\n</model>\n'
    )


def _model_settings(plan: dict[str, Any], assembly_xy: tuple[float, float]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    for index, plate in enumerate(plan["plates"]):
        mesh_id, object_id = 2 * index + 1, 2 * index + 2
        faces = plate["triangles"]
        name = quoteattr(plate["object"])
        lines += [
            f'  <object id="{object_id}">',
            f'    <metadata key="name" value={name}/>',
            f'    <metadata key="extruder" value="{plate["filament_slot"]}"/>',
            f'    <metadata face_count="{faces}"/>',
            f'    <part id="{mesh_id}" subtype="normal_part">',
            f'      <metadata key="name" value={name}/>',
            '      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>',
            f'      <metadata key="extruder" value="{plate["filament_slot"]}"/>',
            f'      <mesh_stat face_count="{faces}" edges_fixed="0" degenerate_facets="0" facets_removed="0" '
            'facets_reversed="0" backwards_edges="0"/>',
            "    </part>",
            "  </object>",
        ]
    for index, plate in enumerate(plan["plates"]):
        lines += [
            "  <plate>",
            f'    <metadata key="plater_id" value="{plate["plate"]}"/>',
            f'    <metadata key="plater_name" value={quoteattr(plate["plate_name"])}/>',
            '    <metadata key="locked" value="false"/>',
            "    <model_instance>",
            f'      <metadata key="object_id" value="{2 * index + 2}"/>',
            '      <metadata key="instance_id" value="0"/>',
            f'      <metadata key="identify_id" value="{1000 + index}"/>',
            "    </model_instance>",
            "  </plate>",
        ]
    lines.append("  <assemble>")
    x, y = assembly_xy
    for index, plate in enumerate(plan["plates"]):
        lo = plate["mesh_min_mm"]
        lines.append(f'   <assemble_item object_id="{2 * index + 2}" instance_id="0" '
                     f'transform="{_transform(x - lo[0], y - lo[1], plate["assembly_z0_mm"] - lo[2])}" offset="0 0 0" />')
    lines += ["  </assemble>", "</config>", ""]
    return "\n".join(lines)


def project_settings(profiles: BambuProfiles, printer: BambuPrinter, process: str, colors: list[str],
                     version: str) -> dict[str, Any]:
    """Project settings = the unmodified system presets (printer, process, one "Generic PLA" per
    color slot) flattened the way Bambu Studio stores them, plus the slot colors."""

    machine, print_ = profiles.resolve(printer.preset), profiles.resolve(process)
    filament = profiles.resolve(printer.filament_preset)
    count = len(colors)
    config: dict[str, Any] = {}
    for preset in (machine, print_):
        config.update({k: v for k, v in preset.items() if k not in _PRESET_META})
    for key, value in filament.items():
        if key not in _PRESET_META:
            config[key] = value * count if isinstance(value, list) else value
    variants = len(filament.get("filament_extruder_variant") or [""])
    config.update({
        "name": "project_settings",
        "from": "project",
        "version": version,
        "printer_settings_id": printer.preset,
        "print_settings_id": process,
        "print_compatible_printers": print_.get("compatible_printers", []),
        "filament_settings_id": [printer.filament_preset] * count,
        # Project-level filament colors = the reviewed sheet colors; Bambu Studio 02.08 stores the
        # single-color value in both keys (observed in files it saved).  No AMS slot is read or mapped.
        "filament_colour": colors,
        "filament_multi_colour": colors,
        "filament_colour_type": ["0"] * count,
        "default_filament_colour": [""] * count,
        "filament_ids": [filament.get("filament_id", "")] * count,
        "filament_map": ["1"] * count,
        "filament_self_index": [str(i + 1) for i in range(count) for _ in range(variants)],
        "inherits_group": [""] * (count + 2),
        "different_settings_to_system": [""] * (count + 2),
    })
    return config


def _write_package(path: Path, members: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in members.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, text.encode("utf-8"))


def write_bambu_project(kit_dir: Path, printer_preset: str = DEFAULT_PRINTER, output: Path | None = None,
                        profiles: BambuProfiles | None = None, studio: Path | None | bool = None) -> dict[str, Any]:
    """Write the kit as a Bambu Studio multi-plate project; returns the report (also when nothing
    is written).  ``studio=False`` skips the Bambu Studio command-line check."""

    kit_dir = Path(kit_dir)
    if printer_preset not in BAMBU_PRINTERS:
        raise BambuExportError(f"unsupported Bambu printer {printer_preset!r}; choose one of {sorted(BAMBU_PRINTERS)}")
    printer = BAMBU_PRINTERS[printer_preset]
    kit = json.loads((kit_dir / "print_kit.json").read_text(encoding="utf-8"))
    profiles = profiles or find_profiles()
    exe = None if studio is False else (studio if isinstance(studio, Path) else find_bambu_studio())
    report: dict[str, Any] = {"printer": printer.preset, "ams": "not assumed: no AMS slot mapping is written; "
                              "every plate uses one filament, so no filament change is needed"}
    if kit["status"]["cad"] != "CAD_VALID":
        report.update(status="NOT_WRITTEN", problems=["kit CAD is not CAD_VALID"])
        return report
    if profiles is None:
        report.update(status="NOT_WRITTEN", problems=[
            "Bambu Studio system presets not found (install Bambu Studio or set LAYERFORGE_BAMBU_PROFILES); "
            "a project with incomplete settings crashes Bambu Studio, so none is written"])
        return report
    printer = printer_from_profiles(printer, profiles.resolve(printer.preset))
    report.update(profiles=profiles.root.as_posix(), profiles_version=profiles.version,
                  printable_area_mm=[list(p) for p in printer.printable_area],
                  keep_out_zones_mm=[[list(p) for p in zone] for zone in printer.keep_out],
                  printable_height_mm=printer.printable_height)
    plan = plan_project(kit, kit_dir, printer)
    process_values = kit["printability"]["process"]
    process, matched = _process_for(printer, process_values["layer_height_mm"])
    report["process_preset"] = process
    report["filaments"] = [{"slot": i + 1, "color_hex": c, "preset": printer.filament_preset,
                            "sheets": [p["object"] for p in plan["plates"] if p["color_hex"] == c]}
                           for i, c in enumerate(plan["colors"])]
    report["notes"] = ["filament presets are Generic PLA; pick your filament in Bambu Studio (the color stays)"]
    if not matched:
        report["notes"].append(f"no {printer.model} system process with {process_values['layer_height_mm']} mm "
                               f"layers; {process} selected, set the layer height in Bambu Studio")
    report["plates"] = [{k: v for k, v in p.items() if not k.startswith("_")} for p in plan["plates"]]
    if plan["problems"]:
        report.update(status="NOT_WRITTEN", problems=plan["problems"])
        return report

    version = bambu_studio_version(exe) or DEVELOPED_WITH
    count = len(plan["plates"])
    placements = []
    for index, plate in enumerate(plan["plates"]):
        ox, oy = plate_origin(index, count, printer)
        bx, by = plate["bed_position_mm"]
        lo = plate["mesh_min_mm"]
        placements.append((ox + bx - lo[0], oy + by - lo[1], -lo[2]))
        plate["plate_origin_mm"] = [round(ox, 4), round(oy, 4)]
    first = plan["plates"][0]["bed_position_mm"]
    members = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
            ' <Default Extension="png" ContentType="image/png"/>\n'
            ' <Default Extension="gcode" ContentType="text/x.gcode"/>\n'
            '</Types>\n'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
            '</Relationships>\n'),
        "3D/3dmodel.model": _main_model(kit, plan, placements, BAMBU_APPLICATION_PREFIX + version),
        "3D/_rels/3dmodel.model.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            + "".join(f' <Relationship Target="/3D/Objects/object_{i + 1}.model" Id="rel-{i + 1}" '
                      'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n' for i in range(count))
            + '</Relationships>\n'),
    }
    for index, plate in enumerate(plan["plates"]):
        members[f"3D/Objects/object_{index + 1}.model"] = _object_model(
            2 * index + 1, f"{kit['name']}:{plate['object']}", plate["_mesh"])
    members["Metadata/model_settings.config"] = _model_settings(plan, (first[0], first[1]))
    members["Metadata/project_settings.config"] = json.dumps(
        project_settings(profiles, printer, process, plan["colors"], version), indent=4) + "\n"

    printer_slug = printer.model.replace("Bambu Lab ", "").replace(" ", "_")
    output = Path(output) if output else kit_dir / "bambu" / f"{kit['name']}_{printer_slug}.3mf"
    _write_package(output, members)
    report["plates"] = [{k: v for k, v in p.items() if not k.startswith("_")} for p in plan["plates"]]
    report.update(status="WRITTEN", file=output.relative_to(kit_dir).as_posix() if output.is_relative_to(kit_dir)
                  else output.as_posix(), application_marker=BAMBU_APPLICATION_PREFIX + version,
                  self_check=check_project(output, kit_dir, printer))
    report["bambu_studio_check"] = (slice_check(output, exe, report["plates"], printer) if exe is not None else
                                    {"status": "NOT_RUN", "reason": "bambu-studio executable not found"})
    report["validation"] = _validation(report)
    return report


def _validation(report: dict[str, Any]) -> dict[str, Any]:
    check = report["bambu_studio_check"]
    if not report["self_check"]["pass"] or check["status"] == "FAIL":
        level = "FAILED"
    elif check["status"] == "PASS":
        level = "BAMBU_STUDIO_CLI_VERIFIED"
    else:
        level = "EXPERIMENTAL_NOT_OPENED_IN_BAMBU_STUDIO"
    return {"level": level, "physical": "NOT_VERIFIED",
            "note": "the command-line check loads and slices this file with Bambu Studio; it does not open the GUI"}


# -- reading back --------------------------------------------------------------------------
def read_project(path: Path) -> dict[str, Any]:
    """Objects (mesh, world transform, filament), plates and settings of a written project."""

    import xml.etree.ElementTree as ET

    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    p_path = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        main = ET.fromstring(archive.read("3D/3dmodel.model"))
        settings = ET.fromstring(archive.read("Metadata/model_settings.config"))
        project = json.loads(archive.read("Metadata/project_settings.config"))
        components: dict[int, tuple[str, int]] = {}
        for obj in main.findall("m:resources/m:object", ns):
            comp = obj.find("m:components/m:component", ns)
            components[int(obj.get("id"))] = (comp.get(p_path).lstrip("/"), int(comp.get("objectid")))
        meshes: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}
        for part in {p for p, _ in components.values()}:
            model = ET.fromstring(archive.read(part))
            for obj in model.findall("m:resources/m:object", ns):
                vertices = np.array([[float(v.get(a)) for a in "xyz"] for v in obj.iter(f"{{{ns['m']}}}vertex")])
                faces = np.array([[int(t.get(a)) for a in ("v1", "v2", "v3")] for t in obj.iter(f"{{{ns['m']}}}triangle")])
                meshes[(part, int(obj.get("id")))] = (vertices, faces)
    objects = {}
    for item in main.findall("m:build/m:item", ns):
        oid = int(item.get("objectid"))
        values = [float(v) for v in item.get("transform").split()]
        vertices, faces = meshes[components[oid]]
        objects[oid] = {"translation": values[9:12], "rotation": values[:9], "vertices": vertices, "faces": faces}
    for obj in settings.findall("object"):
        meta = {m.get("key"): m.get("value") for m in obj.findall("metadata") if m.get("key")}
        objects[int(obj.get("id"))].update(name=meta["name"], extruder=int(meta["extruder"]))
    plates = []
    for plate in settings.findall("plate"):
        meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
        ids = [int(next(m.get("value") for m in inst.findall("metadata") if m.get("key") == "object_id"))
               for inst in plate.findall("model_instance")]
        plates.append({"id": int(meta["plater_id"]), "name": meta.get("plater_name", ""), "objects": ids})
    return {"members": sorted(names), "objects": objects, "plates": plates, "project": project,
            "application": next((m.text for m in main.findall("m:metadata", ns) if m.get("name") == "Application"), None)}


def check_project(path: Path, kit_dir: Path, printer: BambuPrinter) -> dict[str, Any]:
    """Reopen the package: every sheet once, on its own plate, unchanged, on the bed, inside the
    printable area and clear of keep-out zones, with the filament of its color."""

    import trimesh
    from shapely.geometry import Polygon, box

    kit = json.loads((Path(kit_dir) / "print_kit.json").read_text(encoding="utf-8"))
    project = read_project(path)
    rows = kit["assembly_back_to_front"]
    colors = [c.upper() for c in project["project"]["filament_colour"]]
    problems, sheets = [], []
    if len(project["plates"]) != len(rows):
        problems.append(f"{len(project['plates'])} plates for {len(rows)} sheets")
    area = Polygon(printer.printable_area)
    for index, (row, plate) in enumerate(zip(rows, project["plates"])):
        name = Path(row["file_stl"]).stem
        if len(plate["objects"]) != 1:
            problems.append(f"plate {plate['id']} holds {len(plate['objects'])} objects")
            continue
        obj = project["objects"][plate["objects"][0]]
        stl = trimesh.load_mesh(Path(kit_dir) / row["file_stl"], file_type="stl", process=True)
        mesh = trimesh.Trimesh(obj["vertices"], obj["faces"], process=False)
        identity = obj["rotation"] == [1, 0, 0, 0, 1, 0, 0, 0, 1]
        world_lo = mesh.bounds[0] + obj["translation"]
        world_hi = mesh.bounds[1] + obj["translation"]
        ox, oy = plate_origin(index, len(rows), printer)
        bed_lo, bed_hi = world_lo[:2] - (ox, oy), world_hi[:2] - (ox, oy)
        footprint = box(*bed_lo, *bed_hi)
        same_mesh = (len(mesh.faces) == len(stl.faces) and np.allclose(mesh.bounds, stl.bounds, atol=1e-6)
                     and abs(mesh.volume - stl.volume) <= 1e-6 * max(1.0, abs(stl.volume)))
        slot = obj.get("extruder")
        row_check = {
            "plate": plate["id"], "plate_name": plate["name"], "object": obj.get("name"),
            "same_triangles_as_stl": bool(same_mesh), "triangles": int(len(mesh.faces)),
            "volume_mm3": round(float(mesh.volume), 4), "watertight": bool(mesh.is_watertight),
            "no_rotation_or_scale": identity, "on_bed_z_mm": round(float(world_lo[2]), 9),
            "bed_bbox_mm": [round(float(v), 4) for v in (*bed_lo, *bed_hi)],
            "size_mm": [round(float(v), 4) for v in (world_hi - world_lo)],
            "inside_printable_area": bool(area.buffer(1e-6).contains(footprint)),
            "clear_of_keep_out": not any(footprint.intersects(Polygon(z)) for z in printer.keep_out),
            "filament_slot": slot,
            "filament_color": colors[slot - 1] if slot and slot <= len(colors) else None,
        }
        for key, ok in (("object name", obj.get("name") == name), ("geometry", same_mesh), ("rotation", identity),
                        ("on bed", abs(world_lo[2]) < 1e-6), ("printable area", row_check["inside_printable_area"]),
                        ("keep-out", row_check["clear_of_keep_out"]),
                        ("filament color", row_check["filament_color"] == row["color_hex"].upper())):
            if not ok:
                problems.append(f"plate {plate['id']} ({name}): {key}")
        sheets.append(row_check)
    return {"pass": not problems, "problems": problems, "plates": len(project["plates"]),
            "filament_colors": colors, "sheets": sheets}


# -- Bambu Studio command line -------------------------------------------------------------
def run_bambu_cli(exe: Path, args: list[str], timeout: int = CLI_TIMEOUT_S) -> tuple[int | None, dict[str, Any] | None]:
    """Run ``bambu-studio`` with ``args`` (an ``--outputdir`` among them); exit code and result.json."""

    out = Path(args[args.index("--outputdir") + 1])
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        code = subprocess.run([str(exe), *args], capture_output=True, timeout=timeout, creationflags=flags).returncode
    except subprocess.TimeoutExpired:
        return None, None
    result = out / "result.json"
    return code, json.loads(result.read_text(encoding="utf-8")) if result.is_file() else None


def _resaved_plates(path: Path) -> dict[int, dict[str, Any]] | None:
    """Per plate, the filament colors and objects in ``Metadata/plate_<n>.json`` of a project that
    Bambu Studio sliced and saved (its own record of what each plate prints)."""

    if not path.is_file():
        return None
    plates = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            match = re.fullmatch(r"Metadata/plate_(\d+)\.json", name)
            if match:
                data = json.loads(archive.read(name))
                plates[int(match.group(1))] = {"colors": [c.upper() for c in data.get("filament_colors", [])],
                                               "objects": [o.get("name") for o in data.get("bbox_objects", [])]}
    return plates


def slice_check(path: Path, exe: Path, plates: list[dict[str, Any]], printer: BambuPrinter) -> dict[str, Any]:
    """Load and slice every plate with Bambu Studio's command line and compare its report with the plan."""

    with tempfile.TemporaryDirectory(prefix="layerforge_bambu_") as work:
        code, result = run_bambu_cli(exe, ["--slice", "0", "--export-3mf", "resaved.3mf", "--outputdir", work, str(path)])
        gcode = sorted(p.name for p in Path(work).glob("plate_*.gcode"))
        resaved = _resaved_plates(Path(work) / "resaved.3mf")
    base = {"tool": f"{exe.name} {bambu_studio_version(exe) or ''}".strip(),
            "command": "--slice 0 (all plates) --export-3mf (project as re-saved by Bambu Studio)"}
    if result is None:
        return {**base, "status": "FAIL", "problems": [f"no result from Bambu Studio (exit code {code})"]}
    problems = [] if result.get("return_code") == 0 else [f"Bambu Studio: {result.get('error_string')}"]
    if resaved is None:
        problems.append("Bambu Studio did not re-save the project")
    sliced = {p["id"]: p for p in result.get("sliced_plates", [])}
    if len(sliced) != len(plates):
        problems.append(f"Bambu Studio sliced {len(sliced)} plates, expected {len(plates)}")
    rows = []
    for plan in plates:
        got = sliced.get(plan["plate"])
        if got is None:
            continue
        objects, filaments = got.get("objects", []), got.get("filaments", [])
        row = {"plate": plan["plate"], "objects": [o["name"] for o in objects],
               "filaments": [f["id"] for f in filaments], "filament_changes": got.get("filament_change_times"),
               "filament_g": round(sum(f.get("total_used_g", 0) for f in filaments), 2),
               "warning": got.get("warning_message", "")}
        expected_xy = [plan["plate_origin_mm"][0] + plan["bed_position_mm"][0],
                       plan["plate_origin_mm"][1] + plan["bed_position_mm"][1]]
        if len(objects) == 1:
            b = objects[0]["bbox"]
            row["bbox_mm"] = {k: round(b[k], 4) for k in ("x", "y", "z", "width", "depth", "height")}
            size_ok = np.allclose([b["width"], b["depth"], b["height"]], plan["size_mm"], atol=POSITION_TOLERANCE_MM)
            place_ok = np.allclose([b["x"], b["y"], b["z"]], [*expected_xy, 0.0], atol=POSITION_TOLERANCE_MM)
            row.update(size_matches=bool(size_ok), position_matches=bool(place_ok),
                       triangles=objects[0].get("triangle_count"))
            if row["triangles"] != plan["triangles"]:
                problems.append(f"plate {plan['plate']}: Bambu Studio has {row['triangles']} triangles, "
                                f"the STL {plan['triangles']}")
            if not size_ok:
                problems.append(f"plate {plan['plate']}: size {row['bbox_mm']} != {plan['size_mm']}")
            if not place_ok:
                problems.append(f"plate {plan['plate']}: position {b['x']:.3f}, {b['y']:.3f} != {expected_xy}")
        if row["objects"] != [plan["object"]]:
            problems.append(f"plate {plan['plate']}: objects {row['objects']} != [{plan['object']}]")
        if row["filaments"] != [plan["filament_slot"]]:
            problems.append(f"plate {plan['plate']}: filaments {row['filaments']} != [{plan['filament_slot']}]")
        if row["filament_changes"]:
            problems.append(f"plate {plan['plate']}: {row['filament_changes']} filament changes")
        # The sheet color as Bambu Studio itself records it for the plate after loading and slicing.
        saved = (resaved or {}).get(plan["plate"], {})
        row["bambu_colors"], row["bambu_plate_objects"] = saved.get("colors"), saved.get("objects")
        if row["bambu_colors"] != [plan["color_hex"]]:
            problems.append(f"plate {plan['plate']}: Bambu Studio color {row['bambu_colors']} != [{plan['color_hex']}]")
        if row["bambu_plate_objects"] != [plan["object"]]:
            problems.append(f"plate {plan['plate']}: Bambu Studio plate objects {row['bambu_plate_objects']}")
        rows.append(row)
    return {**base, "status": "PASS" if not problems else "FAIL", "problems": problems,
            "layer_height_mm": round(result.get("layer_height", 0), 4), "gcode_files": len(gcode), "plates": rows}


__all__ = [
    "BAMBU_PRINTERS", "BambuExportError", "BambuPrinter", "BambuProfiles", "DEFAULT_PRINTER", "check_project",
    "environment", "find_bambu_studio", "find_profiles", "place_on_bed", "plate_columns", "plate_origin",
    "printer_choices", "project_settings", "read_project", "slice_check", "write_bambu_project",
]
