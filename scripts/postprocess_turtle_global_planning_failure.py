"""Validate and render the rejected masks left by the single bounded turtle run.

This script does not invoke CP-SAT or construct a new assignment. It only reads
the masks already materialized before the reporting-tail failure.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from layerforge_l1.connectivity_repair import (
    audit_connection_width,
    audit_edge_connectivity,
)
from layerforge_l1.experimental_global_planner import exact_index
from layerforge_l1.layer_optimization import (
    DEFAULT_OPTIMIZATION_CONFIG,
    build_region_graph,
)
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG, _render_exploded


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "artifacts" / "l1_turtle_final_global_planning_200mm_v1"
SOURCE = RUN / "source" / "final_corrected_candidate.png"
REJECTED = RUN / "rejected_best_candidate"
LAYERS = REJECTED / "automatic" / "layers"
CONFIG = replace(DEFAULT_CONFIG, sheet_size_mm=200.0)


def _read_mask(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"), dtype=np.uint8) > 127


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _opening_warnings(mask: np.ndarray) -> dict[str, Any]:
    openings = ~mask
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        openings.astype(np.uint8), connectivity=4
    )
    mm_per_pixel = CONFIG.sheet_size_mm / CONFIG.outer_pixels
    narrow: list[dict[str, Any]] = []
    for component in range(1, count):
        component_mask = (labels == component).astype(np.uint8)
        distance = cv2.distanceTransform(component_mask, cv2.DIST_L2, 5)
        width_mm = float(distance.max() * 2.0 * mm_per_pixel)
        if width_mm < 1.2:
            narrow.append(
                {
                    "component": component,
                    "area_pixels": int(stats[component, cv2.CC_STAT_AREA]),
                    "bounds_pixels": [int(value) for value in stats[component, :4]],
                    "maximum_inscribed_width_mm": width_mm,
                }
            )
    return {
        "opening_components": count - 1,
        "narrow_opening_components_under_1_2mm": len(narrow),
        "narrow_openings": narrow,
        "acceptance_effect": "DIAGNOSTIC_ONLY_NOT_RETAINED_WIDTH_FAILURE",
    }


def _contact_sheet(layers: list[dict[str, Any]], palette: np.ndarray, output: Path) -> None:
    tile = 240
    label_height = 34
    columns = 4
    rows = (len(layers) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * tile, rows * (tile + label_height)), "white")
    draw = ImageDraw.Draw(canvas)
    for index, layer in enumerate(reversed(layers)):
        rgb = np.full((*layer["physical_mask"].shape, 3), 250, dtype=np.uint8)
        rgb[layer["physical_mask"]] = palette[layer["palette_index"]]
        preview = Image.fromarray(rgb, "RGB").resize((tile, tile), Image.Resampling.NEAREST)
        x = (index % columns) * tile
        y = (index // columns) * (tile + label_height)
        canvas.paste(preview, (x, y + label_height))
        draw.text((x + 6, y + 8), f"front order {index:02d} {layer['color']}", fill="black")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def main() -> int:
    if not SOURCE.is_file() or not LAYERS.is_dir():
        raise FileNotFoundError("single-run source or rejected masks are missing")
    rgb = np.asarray(Image.open(SOURCE).convert("RGB"), dtype=np.uint8)
    labels, palette = exact_index(rgb)
    palette_hex = ["#" + "".join(f"{int(value):02X}" for value in color) for color in palette]
    graph = build_region_graph(labels, connectivity=4, minimum_contact_pixels=1)
    directories = sorted(LAYERS.iterdir())
    layers: list[dict[str, Any]] = []
    for back_depth, directory in enumerate(directories):
        color_hex = "#" + directory.name.rsplit("_", 1)[-1].upper()
        palette_index = palette_hex.index(color_hex)
        physical = _read_mask(directory / "retained_material_mask.png")
        cutout = _read_mask(directory / "through_cutouts_mask.png")
        visible = _read_mask(directory / "source_visible_region_mask.png")
        hidden = _read_mask(directory / "hidden_retained_material_mask.png")
        layers.append(
            {
                "depth": back_depth,
                "provider_index": len(directories) - 1 - back_depth,
                "palette_index": palette_index,
                "color": color_hex,
                "physical_mask": physical,
                "cutout_mask": cutout,
                "visible_aperture": visible,
                "hidden_aperture": hidden,
            }
        )

    composed = np.full((CONFIG.outer_pixels, CONFIG.outer_pixels, 3), 247, dtype=np.uint8)
    for layer in layers:
        composed[layer["physical_mask"]] = palette[layer["palette_index"]]
    p = CONFIG.perimeter_pixels
    aperture = composed[p:-p, p:-p]
    difference = np.any(aperture != rgb, axis=2)
    perimeter = np.ones((CONFIG.outer_pixels, CONFIG.outer_pixels), dtype=bool)
    perimeter[p:-p, p:-p] = False
    rows: list[dict[str, Any]] = []
    for index, layer in enumerate(layers):
        edge, _ = audit_edge_connectivity(layer["physical_mask"])
        width, _ = audit_connection_width(
            layer["physical_mask"],
            layer["visible_aperture"],
            CONFIG,
            DEFAULT_OPTIMIZATION_CONFIG,
        )
        cover = np.zeros_like(layer["hidden_aperture"])
        for front in layers[index + 1 :]:
            cover |= front["physical_mask"][p:-p, p:-p]
        exposed_hidden = int(np.count_nonzero(layer["hidden_aperture"] & ~cover))
        rows.append(
            {
                "physical_layer_index_back_to_front": index,
                "front_to_back_depth": len(layers) - 1 - index,
                "color": layer["color"],
                "four_neighbor_status": edge["four_neighbor_status"],
                "four_neighbor_components": edge["four_neighbor_components"],
                "four_neighbor_orphan_components": edge[
                    "four_neighbor_orphan_components"
                ],
                "width_status": width["status"],
                "orphan_structural_core_components": width[
                    "orphan_structural_core_components"
                ],
                "orphan_structural_core_pixels": width[
                    "orphan_structural_core_pixels"
                ],
                "thin_decorative_visible_components": width[
                    "thin_decorative_visible_components"
                ],
                "missing_perimeter_pixels": int(
                    np.count_nonzero(perimeter & ~layer["physical_mask"])
                ),
                "cutout_is_exact_complement": bool(
                    np.array_equal(layer["cutout_mask"], ~layer["physical_mask"])
                ),
                "exposed_hidden_material_pixels": exposed_hidden,
                "opening_printability": _opening_warnings(layer["physical_mask"]),
            }
        )

    region_to_front_depth: list[int] = []
    for region in range(len(graph["nodes"])):
        region_mask = graph["region_map"] == region
        matches = [
            layer["provider_index"]
            for layer in layers
            if np.all(layer["visible_aperture"][region_mask])
        ]
        region_to_front_depth.append(matches[0] if len(matches) == 1 else -1)

    front_to_back = list(reversed(layers))
    colors_front = [layer["color"] for layer in front_to_back]
    repeated = {
        color: [index for index, value in enumerate(colors_front) if value == color]
        for color in sorted(set(colors_front))
        if colors_front.count(color) > 1
    }
    rejected = {
        "status": "REJECTED_STRUCTURAL_CANDIDATE_NOT_A_VALID_STACK",
        "source_assignment": "best invalid assignment materialized by the single bounded run before its reporting-tail failure",
        "physical_layer_count": len(layers),
        "front_to_back_colors": colors_front,
        "repeated_color_depths": repeated,
        "region_to_front_depth": region_to_front_depth,
        "all_regions_recovered_from_visible_masks": all(value >= 0 for value in region_to_front_depth),
        "reconstruction": {
            "exact": not np.any(difference),
            "mismatched_pixels": int(np.count_nonzero(difference)),
            "pixel_accuracy": float(np.mean(~difference)),
            "visible_connector_pixels": 0,
        },
        "physical_mapping": {
            "outer_size_mm": [200.0, 200.0],
            "outer_pixels": [CONFIG.outer_pixels, CONFIG.outer_pixels],
            "aperture_pixels": [CONFIG.analysis_pixels, CONFIG.analysis_pixels],
            "aperture_size_mm": [
                CONFIG.sheet_size_mm * CONFIG.analysis_pixels / CONFIG.outer_pixels,
                CONFIG.sheet_size_mm * CONFIG.analysis_pixels / CONFIG.outer_pixels,
            ],
            "retained_width_requirement_mm": 1.2,
        },
        "layers": rows,
        "failing_back_to_front_layers": [
            row["physical_layer_index_back_to_front"]
            for row in rows
            if row["four_neighbor_status"] != "PASS" or row["width_status"] != "PASS"
        ],
        "reporting_failure": {
            "error": "FileNotFoundError while saving rejected preview; previews directory was absent",
            "effect": "solver per-K iteration records and exact rejected-prefix region IDs were not persisted",
            "planner_rerun_performed": False,
        },
        "search_evidence_recoverable_from_console": {
            "tested_fixed_layer_counts": list(range(9, 19)),
            "complete_assignments_evaluated": 35,
            "fixed_k_9_status": "CP_SAT_INFEASIBLE_WITHIN_CURRENT_MODEL",
            "fixed_k_10_status": "UNKNOWN_TIME_LIMIT",
            "fixed_k_11_through_18_status": "NO_VALID_STACK_FOUND_WITHIN_PER_K_BOUNDS",
            "total_elapsed_seconds_before_reporting_failure": 148.002,
        },
        "source_region_indivisibility": {
            "supported_by_backend": True,
            "demonstrated_as_root_cause": False,
        },
        "warning": "These masks reconstruct the artwork but fail retained-width validation and are not printable or approved.",
    }

    previews = REJECTED / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    Image.fromarray(aperture, "RGB").save(previews / "assembled_front_from_masks.png")
    Image.fromarray(difference.astype(np.uint8) * 255, "L").save(
        previews / "reconstruction_difference.png"
    )
    _render_exploded(layers, {"palette_rgb": palette}, previews / "exploded_front_to_back.png")
    _contact_sheet(layers, palette, previews / "individual_sheet_contact.png")
    _write_json(REJECTED / "rejected_validation_report.json", rejected)
    print(
        json.dumps(
            {
                "status": rejected["status"],
                "layers": len(layers),
                "failing_layers": rejected["failing_back_to_front_layers"],
                "exact_reconstruction": rejected["reconstruction"]["exact"],
                "report": str(REJECTED / "rejected_validation_report.json"),
            },
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
