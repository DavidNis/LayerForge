"""Record the accepted L-1 reference stack before the L0 refactor.

Read-only.  Uses only modules that existed at commit 789b02a (no L0 code), so
the recorded properties are an independent baseline for the regenerated stack.
Reads the committed van_gogh option-B planner masks and CAD manifest and
writes one JSON file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from layerforge_l1.connectivity_repair import audit_connection_width, audit_edge_connectivity
from layerforge_l1.experimental_global_planner import exact_index
from layerforge_l1.layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from layerforge_l1.reveal_planner import RevealConfig, embed, physical_grid

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "artifacts" / "l1_van_gogh_v1"


def _bits_sha256(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(mask.astype(bool)).tobytes()).hexdigest()


def _holes(mask: np.ndarray) -> int:
    contours, hierarchy = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    return 0 if hierarchy is None else int(sum(1 for h in hierarchy[0] if h[3] != -1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = json.loads((CASE / "planner_optB" / "poc_report.json").read_text(encoding="utf-8"))
    variant = report["variants"][0]
    config = RevealConfig(**{k: v for k, v in report["config"].items()})
    grid = physical_grid(config)
    p = grid.perimeter_pixels
    target_rgb = np.asarray(Image.open(CASE / "flat_2048_framed_optB.png").convert("RGB"))
    labels, palette = exact_index(target_rgb)
    hexes = ["#" + "".join(f"{int(c):02X}" for c in color) for color in palette]

    layer_dirs = sorted((CASE / "planner_optB" / "per_cell" / "layers").iterdir())
    sheets = []
    composite = np.full((grid.outer_pixels, grid.outer_pixels), -1, dtype=np.int16)
    for layer_dir in reversed(layer_dirs):  # front first
        physical = np.asarray(Image.open(layer_dir / "retained_material_mask.png")) > 0
        visible = np.asarray(Image.open(layer_dir / "source_visible_region_mask.png")) > 0
        color = "#" + layer_dir.name.split("_")[-1].upper()
        composite[(composite < 0) & physical] = hexes.index(color)
        edge, _ = audit_edge_connectivity(physical)
        width, _ = audit_connection_width(physical, visible[p:-p, p:-p], grid, DEFAULT_OPTIMIZATION_CONFIG)
        sheets.append(
            {
                "id": layer_dir.name,
                "order_back_to_front": int(layer_dir.name.split("_")[1]),
                "color": color,
                "shape": list(physical.shape),
                "retained_pixels": int(physical.sum()),
                "visible_pixels": int(visible.sum()),
                "retained_bits_sha256": _bits_sha256(physical),
                "visible_bits_sha256": _bits_sha256(visible),
                "retained_components_4": int(cv2.connectedComponents(physical.astype(np.uint8), connectivity=4)[0] - 1),
                "cutout_holes": _holes(physical),
                "shared_perimeter_complete": bool(physical[:p].all() and physical[-p:].all() and physical[:, :p].all() and physical[:, -p:].all()),
                "four_neighbor": edge["four_neighbor_status"],
                "width_status": width["status"],
                "orphan_structural_cores": width["orphan_structural_core_components"],
            }
        )
    target = embed(labels.astype(np.int16), grid, -1)
    inside = np.ones(composite.shape, bool)
    inside[:p] = inside[-p:] = False
    inside[:, :p] = inside[:, -p:] = False
    # As in run_turtle_reveal_planner_poc.py, frame cells join the perimeter.
    if config.frame_label is not None:
        inside &= target != config.frame_label
    cad =json.loads((CASE / "cad_optB" / "cad_manifest.json").read_text(encoding="utf-8"))
    baseline = {
        "captured_with": "scripts/capture_l0_reference_baseline.py (pre-L0 modules only)",
        "git_revision": "789b02af75f9a04b953f762a0c1e5247dff1517d",
        "target": "artifacts/l1_van_gogh_v1/flat_2048_framed_optB.png",
        "target_shape": list(target_rgb.shape),
        "palette": hexes,
        "planner": "layerforge_l1.reveal_planner.RevealPlanner (per-cell)",
        "planner_config": report["config"],
        "grid": {
            "outer_pixels": grid.outer_pixels,
            "perimeter_pixels": p,
            "analysis_pixels": grid.analysis_pixels,
            "outer_sheet_mm": grid.sheet_size_mm,
            "mm_per_pixel": grid.sheet_size_mm / grid.outer_pixels,
            "artwork_mm": grid.sheet_size_mm * grid.analysis_pixels / grid.outer_pixels,
        },
        "sheet_count": len(sheets),
        "front_to_back_colors": variant["front_to_back_colors"],
        "seams": variant["seams"],
        "sheets_front_to_back": sheets,
        "reconstruction_mismatched_pixels": int((composite[inside] != target[inside]).sum()),
        "composite_sha256": hashlib.sha256(composite.tobytes()).hexdigest(),
        "cad": {
            "outer_sheet_mm": cad["outer_sheet_mm"],
            "thickness_mm": cad["thickness_mm"],
            "layers": {
                row["layer"]: {k: row[k] for k in ("polygons", "holes", "vertices", "step_solids", "stl_watertight", "stl_components", "stl_bounds_mm")}
                for row in cad["layers"]
            },
            "revalidation": json.loads((CASE / "cad_optB" / "cad_revalidation.json").read_text(encoding="utf-8")),
        },
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: baseline[k] for k in ("sheet_count", "front_to_back_colors", "reconstruction_mismatched_pixels")}))
    for s in sheets:
        print(s["id"], s["four_neighbor"], s["width_status"], s["retained_components_4"], s["cutout_holes"])


if __name__ == "__main__":
    main()
