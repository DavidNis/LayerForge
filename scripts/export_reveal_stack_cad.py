"""Export reveal-planner sheet masks as STEP + STL and read them back.

Each retained-material mask is traced into polygons with holes (pixel edges,
then Douglas-Peucker within ``--simplify-mm``), extruded with the existing
``geometry.extrude_geometry`` (CadQuery), exported to STEP and STL, then
reopened: STEP solid count, STL watertightness, mesh component count, volume
versus traced area, and footprint area versus the mask.  Every STL is placed
for printing at z = 0; assembly order is recorded separately in the manifest.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cadquery as cq
import cv2
import numpy as np
import trimesh
from PIL import Image
from shapely.geometry import Polygon
from shapely.ops import unary_union

from layerforge_l1.geometry import extrude_geometry, polygons


def mask_to_geometry(mask: np.ndarray, mm_per_pixel: float, simplify_mm: float):
    """Polygons whose edges follow pixel edges (not pixel centres)."""

    contours, hierarchy = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    height = mask.shape[0]
    to_mm = lambda c: [((x + 0.5) * mm_per_pixel, (height - y - 0.5) * mm_per_pixel) for x, y in c[:, 0]]
    shells = []
    for index, contour in enumerate(contours):
        if hierarchy[0][index][3] != -1 or len(contour) < 3:
            continue
        holes = []
        child = hierarchy[0][index][2]
        while child != -1:
            if len(contours[child]) >= 3:
                holes.append(to_mm(contours[child]))
            child = hierarchy[0][child][0]
        shells.append(Polygon(to_mm(contour), holes))
    half = 0.5 * mm_per_pixel
    geometry = unary_union([s.buffer(0) for s in shells]).buffer(half, join_style=2, mitre_limit=2.0)
    return geometry.simplify(simplify_mm, preserve_topology=True).buffer(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layers", required=True, help="per_cell/layers directory")
    parser.add_argument("--output", required=True)
    parser.add_argument("--outer-mm", type=float, default=225.0)
    parser.add_argument("--thickness-mm", type=float, default=1.6)
    parser.add_argument("--simplify-mm", type=float, default=0.03)
    args = parser.parse_args()
    out = Path(args.output)
    (out / "stl").mkdir(parents=True, exist_ok=True)
    (out / "step").mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows = []
    for layer_dir in sorted(Path(args.layers).iterdir()):
        t0 = time.perf_counter()
        mask = np.asarray(Image.open(layer_dir / "retained_material_mask.png")) > 0
        mm_px = args.outer_mm / mask.shape[0]
        geometry = mask_to_geometry(mask, mm_px, args.simplify_mm)
        t_trace = time.perf_counter()
        solid = extrude_geometry(geometry, args.thickness_mm)
        stem = layer_dir.name
        step_path, stl_path = out / "step" / f"{stem}.step", out / "stl" / f"{stem}.stl"
        cq.exporters.export(solid, str(step_path), exportType="STEP")
        cq.exporters.export(solid, str(stl_path), exportType="STL", tolerance=0.02, angularTolerance=0.2)
        t_export = time.perf_counter()
        step_solids = len(cq.importers.importStep(str(step_path)).val().Solids())
        mesh = trimesh.load_mesh(stl_path, file_type="stl", process=True)
        components = len(mesh.split(only_watertight=False))
        mask_area = float(mask.sum()) * mm_px**2
        row = {
            "layer": stem,
            "color": "#" + stem.split("_")[-1].upper(),
            "polygons": len(polygons(geometry)),
            "holes": sum(len(p.interiors) for p in polygons(geometry)),
            "vertices": sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors) for p in polygons(geometry)),
            "step_solids": step_solids,
            "stl_triangles": int(len(mesh.faces)),
            "stl_watertight": bool(mesh.is_watertight),
            "stl_components": components,
            "stl_bounds_mm": np.round(mesh.bounds, 3).tolist(),
            "traced_area_vs_mask_pct": round(100 * (geometry.area - mask_area) / mask_area, 4),
            "volume_vs_area_x_thickness_pct": round(100 * (mesh.volume - geometry.area * args.thickness_mm) / (geometry.area * args.thickness_mm), 4),
            "seconds_trace": round(t_trace - t0, 2),
            "seconds_cad_export": round(t_export - t_trace, 2),
            "seconds_readback": round(time.perf_counter() - t_export, 2),
        }
        rows.append(row)
        print(json.dumps(row), flush=True)
    order = sorted(rows, key=lambda r: r["layer"])  # layer_00 = back
    manifest = {
        "outer_sheet_mm": args.outer_mm,
        "thickness_mm": args.thickness_mm,
        "assembly_back_to_front": [
            {
                "layer": r["layer"],
                "color": r["color"],
                "stl": f"stl/{r['layer']}.stl",
                "step": f"step/{r['layer']}.step",
                "assembled_z0_mm": i * args.thickness_mm,
            }
            for i, r in enumerate(order)
        ],
        "print_placement": "every STL at z = 0, broad face on the bed",
        "layers": rows,
        "total_seconds": round(time.perf_counter() - started, 1),
    }
    (out / "cad_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("TOTAL_SECONDS", manifest["total_seconds"])


if __name__ == "__main__":
    main()
