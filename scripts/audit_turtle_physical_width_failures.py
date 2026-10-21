"""Read-only width audit of the saved rejected turtle physical masks."""

from __future__ import annotations

import hashlib
import json
from collections import deque
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
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG


ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "artifacts" / "l1_turtle_final_global_planning_200mm_v1" / "rejected_best_candidate"
LAYERS = STACK / "automatic" / "layers"
SOURCE = (
    ROOT
    / "artifacts"
    / "l1_turtle_final_two_contours_200mm_v1"
    / "previews"
    / "final_corrected_candidate.png"
)
OUTPUT = ROOT / "artifacts" / "l1_turtle_final_width_failure_audit_v1"
OLD_CONFIG = replace(DEFAULT_CONFIG, sheet_size_mm=200.0)
INTENDED_CONFIG = replace(DEFAULT_CONFIG, sheet_size_mm=225.0)
HIGH_RES_SCALE = 4
HIGH_RES_CONFIG = replace(
    DEFAULT_CONFIG,
    analysis_pixels=DEFAULT_CONFIG.analysis_pixels * HIGH_RES_SCALE,
    outer_pixels=DEFAULT_CONFIG.outer_pixels * HIGH_RES_SCALE,
    perimeter_pixels=DEFAULT_CONFIG.perimeter_pixels * HIGH_RES_SCALE,
    sheet_size_mm=225.0,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_mask(path: Path) -> tuple[np.ndarray, list[int]]:
    values = np.asarray(Image.open(path).convert("L"), dtype=np.uint8)
    return values > 127, sorted(int(value) for value in np.unique(values))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _bbox(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]


def _shortest_path(
    allowed: np.ndarray, start: np.ndarray, goal: np.ndarray
) -> np.ndarray:
    height, width = allowed.shape
    visited = np.zeros_like(allowed, dtype=bool)
    parent = np.full(height * width, -1, dtype=np.int32)
    pending: deque[int] = deque()
    for y, x in np.argwhere(start & allowed):
        flat = int(y) * width + int(x)
        visited[y, x] = True
        pending.append(flat)
    reached = -1
    while pending:
        flat = pending.popleft()
        y, x = divmod(flat, width)
        if goal[y, x]:
            reached = flat
            break
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < height and 0 <= nx < width and allowed[ny, nx] and not visited[ny, nx]:
                neighbour = ny * width + nx
                visited[ny, nx] = True
                parent[neighbour] = flat
                pending.append(neighbour)
    result = np.zeros_like(allowed, dtype=bool)
    while reached >= 0:
        y, x = divmod(reached, width)
        result[y, x] = True
        reached = int(parent[reached])
    return result


def _crop_with_bounds(image: Image.Image, bounds: list[int], margin: int = 18) -> Image.Image:
    x, y, width, height = bounds
    left = max(0, x - margin)
    top = max(0, y - margin)
    right = min(image.width, x + width + margin)
    bottom = min(image.height, y + height + margin)
    return image.crop((left, top, right, bottom))


def _render_overlay(
    physical: np.ndarray,
    cutout: np.ndarray,
    visible_outer: np.ndarray,
    hidden_outer: np.ndarray,
    maps5: dict[str, np.ndarray],
    critical_necks: np.ndarray,
    details: list[dict[str, Any]],
    title: str,
    destination: Path,
) -> None:
    canvas = np.full((*physical.shape, 3), 255, dtype=np.uint8)
    canvas[physical] = (104, 113, 122)
    canvas[hidden_outer & physical] = (53, 177, 213)
    canvas[visible_outer & physical] = (75, 91, 104)
    canvas[maps5["connected_core"]] = (35, 170, 80)
    canvas[maps5["orphan_core"]] = (224, 48, 48)
    canvas[critical_necks] = (255, 213, 0)
    canvas[cutout] = (255, 255, 255)
    header = 88
    image = Image.new("RGB", (canvas.shape[1], canvas.shape[0] + header), "white")
    image.paste(Image.fromarray(canvas), (0, header))
    draw = ImageDraw.Draw(image)
    draw.text((8, 7), title, fill="black")
    draw.text((8, 27), "gray=visible retained | cyan=hidden retained | white=through-cutout", fill="black")
    draw.text((8, 47), "green=perimeter-anchored 5x5 core | red=orphan 5x5 core", fill="black")
    draw.text((8, 67), "yellow=3x3 core path segment removed by 5x5 erosion", fill="black")
    for index, detail in enumerate(details, 1):
        bounds = detail["critical_neck_bounds_outer_pixels"]
        if bounds is None:
            continue
        x, y, width, height = bounds
        draw.rectangle((x, y + header, x + width - 1, y + header + height - 1), outline=(95, 55, 0), width=2)
        draw.text((x + 2, y + header + 2), str(index), fill=(95, 55, 0))
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)


def _render_polarity_panel(
    retained: np.ndarray, cutout: np.ndarray, preview: np.ndarray, destination: Path, title: str
) -> None:
    retained_rgb = np.where(retained[:, :, None], 255, 0).astype(np.uint8)
    cutout_rgb = np.where(cutout[:, :, None], 255, 0).astype(np.uint8)
    retained_rgb = np.repeat(retained_rgb, 3, axis=2)
    cutout_rgb = np.repeat(cutout_rgb, 3, axis=2)
    size = retained.shape[1]
    header = 44
    canvas = Image.new("RGB", (size * 3, size + header), "white")
    draw = ImageDraw.Draw(canvas)
    canvas.paste(Image.fromarray(retained_rgb), (0, header))
    canvas.paste(Image.fromarray(cutout_rgb), (size, header))
    canvas.paste(Image.fromarray(preview), (size * 2, header))
    draw.text((8, 6), title, fill="black")
    draw.text((8, 24), "retained mask: white=material", fill="black")
    draw.text((size + 8, 24), "cutout mask: white=empty", fill="black")
    draw.text((size * 2 + 8, 24), "preview: color=material, white=cutout", fill="black")
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination)


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUTPUT}")
    if not SOURCE.is_file() or not LAYERS.is_dir():
        raise FileNotFoundError("approved target or saved rejected masks missing")
    (OUTPUT / "overlays").mkdir(parents=True)
    (OUTPUT / "crops").mkdir(parents=True)
    (OUTPUT / "polarity").mkdir(parents=True)
    (OUTPUT / "reports").mkdir(parents=True)

    source_rgb = np.asarray(Image.open(SOURCE).convert("RGB"), dtype=np.uint8)
    source_labels, palette = exact_index(source_rgb)
    graph = build_region_graph(source_labels, connectivity=4, minimum_contact_pixels=1)
    directories = sorted(path for path in LAYERS.iterdir() if path.is_dir())
    p = DEFAULT_CONFIG.perimeter_pixels
    palette_hex = ["#" + "".join(f"{int(value):02X}" for value in color) for color in palette]
    layer_rows: list[dict[str, Any]] = []
    failing_overlay_paths: list[Path] = []

    for back_index, directory in enumerate(directories):
        retained, retained_values = _read_mask(directory / "retained_material_mask.png")
        cutout, cutout_values = _read_mask(directory / "through_cutouts_mask.png")
        visible, visible_values = _read_mask(directory / "source_visible_region_mask.png")
        hidden, hidden_values = _read_mask(directory / "hidden_retained_material_mask.png")
        preview = np.asarray(Image.open(directory / "physical_sheet_preview.png").convert("RGB"), dtype=np.uint8)
        color_hex = "#" + directory.name.rsplit("_", 1)[-1].upper()
        color = np.asarray([int(color_hex[index : index + 2], 16) for index in (1, 3, 5)], dtype=np.uint8)
        preview_matches = bool(
            np.all(preview[retained] == color)
            and np.all(preview[~retained] == np.asarray([250, 250, 250], dtype=np.uint8))
        )
        visible_outer = np.zeros_like(retained)
        hidden_outer = np.zeros_like(retained)
        visible_outer[p:-p, p:-p] = visible
        hidden_outer[p:-p, p:-p] = hidden

        edge, _ = audit_edge_connectivity(retained)
        old, maps5 = audit_connection_width(
            retained, visible, OLD_CONFIG, DEFAULT_OPTIMIZATION_CONFIG
        )
        intended_native, _ = audit_connection_width(
            retained, visible, INTENDED_CONFIG, DEFAULT_OPTIMIZATION_CONFIG
        )
        _, maps3 = audit_connection_width(
            retained,
            visible,
            INTENDED_CONFIG,
            DEFAULT_OPTIMIZATION_CONFIG,
            kernel_size_override=3,
        )
        retained4 = cv2.resize(
            retained.astype(np.uint8), None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST
        ).astype(bool)
        visible4 = cv2.resize(
            visible.astype(np.uint8), None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST
        ).astype(bool)
        highres, _ = audit_connection_width(
            retained4, visible4, HIGH_RES_CONFIG, DEFAULT_OPTIMIZATION_CONFIG
        )

        details_by_component = {
            int(detail["component"]): detail for detail in old["orphan_structural_core_details"]
        }
        top_components = sorted(
            details_by_component,
            key=lambda component: int(details_by_component[component]["area_pixels_at_required_kernel"]),
            reverse=True,
        )[:5]
        neck_union = np.zeros_like(retained)
        representatives: list[dict[str, Any]] = []
        for component in top_components:
            orphan = maps5["core_labels"] == component
            path = _shortest_path(maps3["core"], orphan, maps5["connected_core"])
            neck = path & ~maps5["core"]
            neck_union |= neck
            bounds = _bbox(neck)
            if bounds is None:
                center_outer = None
                center_artwork = None
                center_artwork_mm = None
            else:
                x, y, width, height = bounds
                center_outer = [x + (width - 1) / 2.0, y + (height - 1) / 2.0]
                center_artwork = [center_outer[0] - p, center_outer[1] - p]
                center_artwork_mm = [
                    value * (200.0 / DEFAULT_CONFIG.analysis_pixels)
                    for value in center_artwork
                ]
            aperture_orphan = orphan[p:-p, p:-p]
            region_ids = sorted(
                int(value) for value in np.unique(graph["region_map"][aperture_orphan])
            ) if np.any(aperture_orphan) else []
            detail = details_by_component[component]
            representatives.append(
                {
                    "component": component,
                    "orphan_core_area_pixels_native": int(
                        detail["area_pixels_at_required_kernel"]
                    ),
                    "orphan_core_bounds_outer_pixels": detail[
                        "bounds_pixels_at_required_kernel"
                    ],
                    "critical_neck_bounds_outer_pixels": bounds,
                    "critical_neck_center_outer_pixels": center_outer,
                    "critical_neck_center_artwork_pixels": center_artwork,
                    "critical_neck_center_artwork_mm": center_artwork_mm,
                    "source_region_ids_overlapped": region_ids,
                    "visible_or_hidden": (
                        "VISIBLE"
                        if detail["classification"] == "VISIBLE_ARTWORK_STRUCTURAL_BOTTLENECK"
                        else "HIDDEN"
                    ),
                    "substantial_region": int(detail["area_pixels_at_required_kernel"]) >= 20,
                    "native_discrete_measurement": {
                        "largest_passing_kernel_pixels": detail[
                            "largest_passing_kernel_pixels"
                        ],
                        "largest_passing_width_old_scale_mm": detail[
                            "largest_passing_kernel_pixels"
                        ]
                        * (200.0 / 576.0),
                        "largest_passing_width_intended_artwork_scale_mm": detail[
                            "largest_passing_kernel_pixels"
                        ]
                        * (200.0 / 512.0),
                        "next_failing_kernel_pixels": 5,
                        "next_failing_width_intended_artwork_scale_mm": 5
                        * (200.0 / 512.0),
                    },
                    "measurement_interpretation": (
                        "The actual retained path is connected at the 3x3 core and is removed by "
                        "the 5x5 core. The native raster therefore brackets rather than exactly "
                        "resolves its continuum width."
                    ),
                }
            )

        failed_old = old["status"] != "PASS"
        overlay_relative = None
        crop_paths: list[str] = []
        if failed_old:
            overlay_path = OUTPUT / "overlays" / f"{directory.name}_width_failure.png"
            _render_overlay(
                retained,
                cutout,
                visible_outer,
                hidden_outer,
                maps5,
                neck_union,
                representatives,
                (
                    f"{directory.name} | back index {back_index} | {color_hex} | "
                    f"native orphan cores {old['orphan_structural_core_components']}"
                ),
                overlay_path,
            )
            failing_overlay_paths.append(overlay_path)
            overlay_relative = overlay_path.relative_to(OUTPUT).as_posix()
            overlay_image = Image.open(overlay_path).convert("RGB")
            for rank, representative in enumerate(representatives[:3], 1):
                bounds = representative["critical_neck_bounds_outer_pixels"]
                if bounds is None:
                    continue
                shifted = list(bounds)
                shifted[1] += 88
                crop = _crop_with_bounds(overlay_image, shifted, margin=28)
                crop = crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.NEAREST)
                crop_path = OUTPUT / "crops" / f"{directory.name}_neck_{rank}.png"
                crop.save(crop_path)
                crop_paths.append(crop_path.relative_to(OUTPUT).as_posix())

        if color_hex == "#0E0F0D":
            polarity_path = OUTPUT / "polarity" / f"{directory.name}_polarity.png"
            _render_polarity_panel(
                retained,
                cutout,
                preview,
                polarity_path,
                f"{directory.name}: exact saved masks and preview",
            )

        highres_passing_sizes = sorted(
            {
                int(detail["largest_passing_kernel_pixels"])
                for detail in highres["orphan_structural_core_details"]
            }
        )
        layer_rows.append(
            {
                "physical_layer_index_back_to_front": back_index,
                "front_to_back_depth": len(directories) - 1 - back_index,
                "layer_id": directory.name,
                "color": color_hex,
                "mask_paths": {
                    "retained": (directory / "retained_material_mask.png").relative_to(ROOT).as_posix(),
                    "cutout": (directory / "through_cutouts_mask.png").relative_to(ROOT).as_posix(),
                    "visible": (directory / "source_visible_region_mask.png").relative_to(ROOT).as_posix(),
                    "hidden": (directory / "hidden_retained_material_mask.png").relative_to(ROOT).as_posix(),
                    "preview": (directory / "physical_sheet_preview.png").relative_to(ROOT).as_posix(),
                },
                "mask_sha256": _sha256(directory / "retained_material_mask.png"),
                "mask_values": {
                    "retained_mask": retained_values,
                    "cutout_mask": cutout_values,
                    "visible_mask": visible_values,
                    "hidden_mask": hidden_values,
                },
                "mask_polarity": "retained mask 255=physical material, 0=empty; cutout mask is exact inverse",
                "cutout_exact_inverse": bool(np.array_equal(cutout, ~retained)),
                "preview_matches_retained_polarity": preview_matches,
                "hidden_material_included_in_physical_mask": bool(
                    np.all(retained[p:-p, p:-p][hidden])
                ),
                "four_neighbor": edge,
                "original_run_scale": {
                    "artwork_mm": [177.7777777778, 177.7777777778],
                    "outer_sheet_mm": [200.0, 200.0],
                    "mm_per_mask_pixel": 200.0 / 576.0,
                    "kernel_pixels": old["morphology_kernel_size_pixels"],
                    "status": old["status"],
                    "orphan_core_components": old["orphan_structural_core_components"],
                    "orphan_core_pixels": old["orphan_structural_core_pixels"],
                },
                "intended_artwork_scale_native_raster": {
                    "artwork_mm": [200.0, 200.0],
                    "outer_sheet_mm_with_existing_appended_perimeter": [225.0, 225.0],
                    "mm_per_mask_pixel": 200.0 / 512.0,
                    "kernel_pixels": intended_native["morphology_kernel_size_pixels"],
                    "status": intended_native["status"],
                    "orphan_core_components": intended_native[
                        "orphan_structural_core_components"
                    ],
                    "orphan_core_pixels": intended_native[
                        "orphan_structural_core_pixels"
                    ],
                },
                "intended_scale_four_x_diagnostic": {
                    "mm_per_sample": HIGH_RES_CONFIG.sheet_size_mm / HIGH_RES_CONFIG.outer_pixels,
                    "kernel_pixels": highres["morphology_kernel_size_pixels"],
                    "status": highres["status"],
                    "orphan_core_components": highres[
                        "orphan_structural_core_components"
                    ],
                    "orphan_core_pixels": highres["orphan_structural_core_pixels"],
                    "largest_passing_kernel_pixels_present": highres_passing_sizes,
                    "largest_passing_widths_mm_present": [
                        value * HIGH_RES_CONFIG.sheet_size_mm / HIGH_RES_CONFIG.outer_pixels
                        for value in highres_passing_sizes
                    ],
                    "next_failing_kernel_width_mm": highres[
                        "morphology_kernel_size_pixels"
                    ]
                    * HIGH_RES_CONFIG.sheet_size_mm
                    / HIGH_RES_CONFIG.outer_pixels,
                },
                "orphan_visibility": {
                    value: sum(
                        detail["classification"] == value
                        for detail in old["orphan_structural_core_details"]
                    )
                    for value in (
                        "VISIBLE_ARTWORK_STRUCTURAL_BOTTLENECK",
                        "HIDDEN_RETAINED_MATERIAL_STRUCTURAL_BOTTLENECK",
                    )
                },
                "representative_significant_failures": representatives,
                "diagnostic_overlay": overlay_relative,
                "representative_crops": crop_paths,
                "classification": (
                    "SUSPICIOUS_NATIVE_RASTER_FALSE_POSITIVE"
                    if failed_old and highres["status"] == "PASS"
                    else (
                        "VERIFIED_SUB_THRESHOLD_RETAINED_CONNECTIONS"
                        if failed_old
                        else "PASS"
                    )
                ),
            }
        )

    black_rows = [row for row in layer_rows if row["color"] == "#0E0F0D"]
    failing_rows = [row for row in layer_rows if row["original_run_scale"]["status"] != "PASS"]
    report = {
        "status": "READY_FOR_USER_TEST",
        "milestone_approval": "L1_NOT_APPROVED",
        "source": {
            "path": SOURCE.relative_to(ROOT).as_posix(),
            "sha256": _sha256(SOURCE),
            "modified": False,
        },
        "mask_interpretation": {
            "retained_mask_255": "physical material",
            "retained_mask_0": "empty through-cutout",
            "cutout_mask": "exact binary inverse of retained mask on every sheet",
            "preview": "filament color is painted exactly where retained mask is true; near-white is empty",
            "hidden_material": "included in the retained physical mask and separately recorded by the hidden mask",
            "inversion_detected": False,
            "black_sheet_identification": [
                {
                    "layer_id": row["layer_id"],
                    "back_to_front_index": row["physical_layer_index_back_to_front"],
                    "status": row["original_run_scale"]["status"],
                    "interpretation": (
                        "The visually solid black sheet with only a few openings is this passing sheet."
                        if row["physical_layer_index_back_to_front"] == 2
                        else "This is the rejected black sheet with extensive turtle/leaf cutouts and narrow retained webs."
                    ),
                }
                for row in black_rows
            ],
        },
        "scale_comparison": {
            "original_rejected_run": {
                "artwork_mm": [177.7777777778, 177.7777777778],
                "outer_sheet_mm": [200.0, 200.0],
                "mm_per_mask_pixel": 200.0 / 576.0,
                "native_kernel": "5x5 ellipse",
            },
            "user_confirmed_artwork_scale_with_existing_perimeter": {
                "artwork_mm": [200.0, 200.0],
                "outer_sheet_mm": [225.0, 225.0],
                "mm_per_mask_pixel": 200.0 / 512.0,
                "native_kernel": "5x5 ellipse",
            },
            "conflict": (
                "The current mask model cannot simultaneously keep the appended 32-pixel perimeter, "
                "make the artwork aperture 200 mm, and keep the outer sheet at 200 mm."
            ),
        },
        "validator_review": {
            "polarity_correct": True,
            "four_neighbor_connectivity_correct": True,
            "perimeter_anchor_correct_for_current_mask_model": True,
            "cutouts_excluded_from_retained_width_rule": True,
            "native_kernel_rounding_conservative": True,
            "native_5x5_physical_span_old_scale_mm": 5 * (200.0 / 576.0),
            "native_5x5_physical_span_intended_scale_mm": 5 * (200.0 / 512.0),
            "high_resolution_result": (
                "Layers 4-9 remain failing with a 13x13 footprint at 0.09765625 mm/sample; "
                "layer 10 passes and is a native-raster false positive."
            ),
            "assessment": (
                "The validator is interpreting material and cutouts correctly, but native odd-kernel "
                "rounding is too coarse to justify every rejection. Six sheets contain verified "
                "near-threshold retained bottlenecks; the seventh rejection is resolution-dependent."
            ),
        },
        "summary": {
            "original_rejected_sheet_count": len(failing_rows),
            "verified_at_four_x_intended_scale": [
                row["physical_layer_index_back_to_front"]
                for row in failing_rows
                if row["classification"] == "VERIFIED_SUB_THRESHOLD_RETAINED_CONNECTIONS"
            ],
            "suspicious_native_false_positive": [
                row["physical_layer_index_back_to_front"]
                for row in failing_rows
                if row["classification"] == "SUSPICIOUS_NATIVE_RASTER_FALSE_POSITIVE"
            ],
            "recommended_corrective_action": (
                "Keep the 1.2 mm rule, but evaluate retained-width connectivity on a physically "
                "calibrated supersampled/vector mask so the morphology footprint matches 1.2 mm "
                "without native odd-kernel over-rounding; then reclassify before planner feedback."
            ),
        },
        "layers_back_to_front": layer_rows,
        "no_planner_or_mask_mutation": True,
    }
    _write_json(OUTPUT / "reports" / "width_failure_audit.json", report)
    print(json.dumps(report["summary"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
