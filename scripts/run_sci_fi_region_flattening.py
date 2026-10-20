"""Isolated region-aware flat-color preparation for the sci-fi illustration.

This script does not import or invoke any layer-planning code.  It produces
one candidate by combining an identity-preserving raster edit with fixed-palette
spatial regularization behind strong artwork boundaries.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sci_fi_characters_shadowbox.png"
EXISTING = ROOT / "artifacts" / "l1_sci_fi_preprocessing_diagnostic_v1" / "indexed_target.png"
PREPROCESSING = ROOT / "artifacts" / "l1_sci_fi_preprocessing_diagnostic_v1" / "preprocessing_report.json"
IMAGE_EDIT_BASIS = Path(
    r"C:\Users\davni\.codex\generated_images\01a14557-3bdb-78f0-97ac-e2729b3dd8cc\exec-e894cc59-d8b8-4c3d-910f-aa89f2f368f1.png"
)
OUTPUT = ROOT / "artifacts" / "l1_sci_fi_region_aware_flat_color_v1"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rgb_hex(color: np.ndarray) -> str:
    return "#" + "".join(f"{int(value):02X}" for value in color)


def nearest_palette_labels(rgb: np.ndarray, palette: np.ndarray) -> np.ndarray:
    image_lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    palette_lab = cv2.cvtColor(
        palette.reshape(1, -1, 3), cv2.COLOR_RGB2LAB
    ).reshape(-1, 3).astype(np.float32)
    flat = image_lab.reshape(-1, 3)
    result = np.empty(len(flat), dtype=np.uint8)
    for start in range(0, len(flat), 65536):
        block = flat[start : start + 65536]
        distance = ((block[:, None, :] - palette_lab[None, :, :]) ** 2).sum(axis=2)
        result[start : start + len(block)] = np.argmin(distance, axis=1).astype(np.uint8)
    return result.reshape(rgb.shape[:2])


def strong_art_boundaries(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    boundary = np.zeros(rgb.shape[:2], dtype=bool)
    horizontal = np.linalg.norm(lab[:, 1:] - lab[:, :-1], axis=2) >= 24.0
    vertical = np.linalg.norm(lab[1:] - lab[:-1], axis=2) >= 24.0
    boundary[:, 1:] |= horizontal
    boundary[:, :-1] |= horizontal
    boundary[1:, :] |= vertical
    boundary[:-1, :] |= vertical

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    dark = gray < 72
    canny = cv2.Canny(gray, 58, 132) > 0
    near_ink = cv2.dilate(dark.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    boundary |= canny & near_ink
    boundary = cv2.dilate(boundary.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    return boundary, dark


def regularize_regions(
    basis: np.ndarray, palette: np.ndarray
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    # Mean shift supplies spatially coherent color basins; the following cell
    # vote is what prevents a global palette boundary from slicing a gradient.
    filtered_bgr = cv2.pyrMeanShiftFiltering(
        cv2.cvtColor(basis, cv2.COLOR_RGB2BGR), sp=11, sr=24, maxLevel=1
    )
    filtered = cv2.cvtColor(filtered_bgr, cv2.COLOR_BGR2RGB)
    raw_labels = nearest_palette_labels(filtered, palette)
    boundary, dark = strong_art_boundaries(basis)
    count, components, stats, _ = cv2.connectedComponentsWithStats(
        (~boundary).astype(np.uint8), connectivity=4
    )
    labels = raw_labels.copy()
    cells_flattened = 0
    cells_preserved = 0
    pixels_regularized = 0
    for component in range(1, count):
        mask = components == component
        area = int(stats[component, cv2.CC_STAT_AREA])
        if area < 18:
            cells_preserved += 1
            continue
        values, counts = np.unique(raw_labels[mask], return_counts=True)
        majority = int(values[np.argmax(counts)])
        old = labels[mask].copy()
        labels[mask] = majority
        changed = int(np.count_nonzero(old != majority))
        if changed:
            cells_flattened += 1
            pixels_regularized += changed
        else:
            cells_preserved += 1

    # Preserve all dark drawn structure as the palette's darkest material.
    palette_luma = cv2.cvtColor(
        palette.reshape(1, -1, 3), cv2.COLOR_RGB2GRAY
    ).reshape(-1)
    ink_index = int(np.argmin(palette_luma))
    labels[dark] = ink_index

    # One bounded cleanup pass removes non-ink specks only.  Dark components,
    # including facial marks, fingers, outlines, and costume lines, are locked.
    small_components_merged = 0
    small_pixels_merged = 0
    before_cleanup = labels.copy()
    kernel = np.ones((3, 3), dtype=np.uint8)
    for color in range(len(palette)):
        if color == ink_index:
            continue
        count_color, component_map, component_stats, _ = cv2.connectedComponentsWithStats(
            (before_cleanup == color).astype(np.uint8), connectivity=4
        )
        for component in range(1, count_color):
            area = int(component_stats[component, cv2.CC_STAT_AREA])
            if area >= 12:
                continue
            mask = component_map == component
            ring = cv2.dilate(mask.astype(np.uint8), kernel).astype(bool) & ~mask
            neighbours = labels[ring]
            if not neighbours.size:
                continue
            values, counts = np.unique(neighbours, return_counts=True)
            replacement = int(values[np.argmax(counts)])
            labels[mask] = replacement
            small_components_merged += 1
            small_pixels_merged += area

    candidate = palette[labels]
    return candidate, labels, {
        "method": "mean-shift spatial basins, strong-boundary connected art cells, fixed nine-color LAB assignment, one non-ink speck cleanup pass",
        "detected_art_cells": count - 1,
        "flattened_art_cells": cells_flattened,
        "preserved_art_cells": cells_preserved,
        "pixels_changed_by_cell_regularization": pixels_regularized,
        "locked_dark_ink_pixels": int(np.count_nonzero(dark)),
        "dark_ink_palette_index": ink_index,
        "small_non_ink_components_merged": small_components_merged,
        "small_non_ink_pixels_merged": small_pixels_merged,
        "boundary_pixels": int(np.count_nonzero(boundary)),
    }


def component_statistics(rgb: np.ndarray, small_limit: int = 20) -> dict[str, Any]:
    colors, inverse, counts = np.unique(
        rgb.reshape(-1, 3), axis=0, return_inverse=True, return_counts=True
    )
    labels = inverse.reshape(rgb.shape[:2])
    rows: list[dict[str, Any]] = []
    total_regions = 0
    small_components = 0
    small_pixels = 0
    for index, (color, pixels) in enumerate(zip(colors, counts)):
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            (labels == index).astype(np.uint8), connectivity=4
        )
        areas = stats[1:, cv2.CC_STAT_AREA] if count > 1 else np.empty(0, dtype=int)
        small = areas < small_limit
        total_regions += count - 1
        small_components += int(np.count_nonzero(small))
        small_pixels += int(areas[small].sum())
        rows.append(
            {
                "color": rgb_hex(color),
                "pixels": int(pixels),
                "connected_regions_4_neighbor": count - 1,
                "small_components_below_20_pixels": int(np.count_nonzero(small)),
                "small_component_pixels": int(areas[small].sum()),
            }
        )
    return {
        "unique_rgb_colors": len(colors),
        "connected_regions_4_neighbor": total_regions,
        "small_component_threshold_pixels": small_limit,
        "small_component_count": small_components,
        "small_component_pixels": small_pixels,
        "per_color": rows,
    }


def edge_comparison(original: np.ndarray, candidate: np.ndarray) -> tuple[dict[str, Any], np.ndarray]:
    original_gray = cv2.cvtColor(original, cv2.COLOR_RGB2GRAY)
    candidate_gray = cv2.cvtColor(candidate, cv2.COLOR_RGB2GRAY)
    original_edges = cv2.Canny(original_gray, 58, 132) > 0
    candidate_edges = cv2.Canny(candidate_gray, 58, 132) > 0
    near_candidate = cv2.dilate(candidate_edges.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    near_original = cv2.dilate(original_edges.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    retained = original_edges & near_candidate
    lost = original_edges & ~near_candidate
    added = candidate_edges & ~near_original
    image = np.full((*original_edges.shape, 3), 245, dtype=np.uint8)
    image[original_edges] = (80, 80, 80)
    image[lost] = (230, 55, 55)
    image[added] = (45, 105, 230)
    return {
        "original_edge_pixels": int(np.count_nonzero(original_edges)),
        "candidate_edge_pixels": int(np.count_nonzero(candidate_edges)),
        "original_edges_retained_within_2_pixels": int(np.count_nonzero(retained)),
        "original_edge_retention_fraction": float(
            np.count_nonzero(retained) / max(1, np.count_nonzero(original_edges))
        ),
        "original_edge_pixels_without_nearby_candidate_edge": int(np.count_nonzero(lost)),
        "candidate_edge_pixels_without_nearby_original_edge": int(np.count_nonzero(added)),
        "change_map_legend": "gray original edge; red original edge without candidate edge within 2px; blue candidate edge without original edge within 2px",
    }, image


def labelled_triptych(
    original: Image.Image,
    existing: Image.Image,
    candidate: Image.Image,
    path: Path,
    crop: tuple[int, int, int, int] | None = None,
    panel_width: int = 420,
) -> None:
    images = [original, existing, candidate]
    labels = ["ORIGINAL RGB", "EXISTING INDEXED", "REGION-AWARE FLAT COLOR"]
    if crop is not None:
        images = [image.crop(crop) for image in images]
    ratio = images[0].height / images[0].width
    panel_height = max(1, int(round(panel_width * ratio)))
    canvas = Image.new("RGB", (panel_width * 3, panel_height + 34), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (image, label) in enumerate(zip(images, labels)):
        resized = image.resize((panel_width, panel_height), Image.Resampling.NEAREST)
        canvas.paste(resized, (index * panel_width, 34))
        draw.text((index * panel_width + 8, 10), label, fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)


def main() -> int:
    required = [SOURCE, EXISTING, PREPROCESSING, IMAGE_EDIT_BASIS]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing required inputs: {missing}")
    resume_validation = OUTPUT.exists()
    if resume_validation and not (OUTPUT / "region_aware_flat_color.png").is_file():
        raise RuntimeError(f"partial output lacks a completed candidate: {OUTPUT}")
    (OUTPUT / "previews").mkdir(parents=True, exist_ok=True)
    (OUTPUT / "crops").mkdir(parents=True, exist_ok=True)
    (OUTPUT / "provenance").mkdir(parents=True, exist_ok=True)

    original_image = Image.open(SOURCE).convert("RGB")
    basis_image = Image.open(IMAGE_EDIT_BASIS).convert("RGB")
    if basis_image.size != original_image.size:
        basis_image = basis_image.resize(original_image.size, Image.Resampling.LANCZOS)
    existing_image = Image.open(EXISTING).convert("RGB").resize(
        original_image.size, Image.Resampling.NEAREST
    )
    original = np.asarray(original_image, dtype=np.uint8)
    basis = np.asarray(basis_image, dtype=np.uint8)
    preprocessing = json.loads(PREPROCESSING.read_text(encoding="utf-8"))
    palette = np.asarray(preprocessing["palette_rgb"], dtype=np.uint8)
    if len(palette) != 9:
        raise RuntimeError(f"expected preserved nine-color palette, got {len(palette)}")

    if resume_validation:
        candidate_image = Image.open(
            OUTPUT / "region_aware_flat_color.png"
        ).convert("RGB")
        candidate = np.asarray(candidate_image, dtype=np.uint8)
        method = {
            "method": "mean-shift spatial basins, strong-boundary connected art cells, fixed nine-color LAB assignment, one non-ink speck cleanup pass",
            "processing_dimensions_pixels": list(original_image.size),
            "validation_resumed_after_candidate_completion": True,
            "note": "The first process was interrupted only during metrics over the original high-cardinality RGB image; this candidate was not regenerated.",
        }
        candidate_512 = Image.open(
            OUTPUT / "region_aware_flat_color_512.png"
        ).convert("RGB")
    else:
        candidate, _, method = regularize_regions(basis, palette)
        method["processing_dimensions_pixels"] = list(original_image.size)
        method["validation_resumed_after_candidate_completion"] = False
        candidate_image = Image.fromarray(candidate, "RGB")
        original_image.save(OUTPUT / "original_source_preview.png")
        Image.open(EXISTING).convert("RGB").save(OUTPUT / "existing_indexed_target.png")
        candidate_image.save(OUTPUT / "region_aware_flat_color.png")
        candidate_512 = candidate_image.resize((512, 512), Image.Resampling.NEAREST)
        candidate_512.save(OUTPUT / "region_aware_flat_color_512.png")
        basis_image.save(OUTPUT / "provenance" / "image_edit_basis.png")

    labelled_triptych(
        original_image,
        existing_image,
        candidate_image,
        OUTPUT / "previews" / "side_by_side.png",
        panel_width=360,
    )
    crops = {
        "thin_outlines_and_faces": (150, 65, 840, 500),
        "costume_hands_and_fingers": (70, 330, 930, 1010),
        "gradient_spotlight_and_background": (300, 0, 770, 430),
    }
    for name, crop in crops.items():
        labelled_triptych(
            original_image,
            existing_image,
            candidate_image,
            OUTPUT / "crops" / f"{name}.png",
            crop=crop,
            panel_width=480,
        )

    edges, edge_map = edge_comparison(original, candidate)
    Image.fromarray(edge_map, "RGB").save(OUTPUT / "previews" / "boundary_change_map.png")
    pixel_difference = np.any(original != candidate, axis=2)
    heat = np.zeros_like(original)
    heat[pixel_difference] = (225, 45, 75)
    Image.fromarray(heat, "RGB").save(OUTPUT / "previews" / "changed_pixels.png")

    existing_native = np.asarray(Image.open(EXISTING).convert("RGB"), dtype=np.uint8)
    candidate_native_512 = np.asarray(candidate_512, dtype=np.uint8)
    original_colors = np.unique(original.reshape(-1, 3), axis=0)
    original_stats = {
        "unique_rgb_colors": len(original_colors),
        "connected_regions_4_neighbor": None,
        "small_component_count": None,
        "note": "Not computed per exact shade: continuous RGB artwork has too many colors for per-color connected-component counts to be meaningful or bounded.",
    }
    existing_stats = component_statistics(existing_native)
    candidate_stats = component_statistics(candidate)
    candidate_512_stats = component_statistics(candidate_native_512)
    candidate_colors = np.unique(candidate.reshape(-1, 3), axis=0)
    palette_exact = {
        tuple(int(channel) for channel in color) for color in candidate_colors
    } == {tuple(int(channel) for channel in color) for color in palette}

    report = {
        "status": "READY_FOR_USER_TEST",
        "planner_invoked": False,
        "candidate_count": 1,
        "inputs": {
            "source": SOURCE.name,
            "source_sha256": sha256(SOURCE),
            "existing_indexed_target": str(EXISTING.relative_to(ROOT)),
            "image_edit_basis_provenance": "one built-in image edit using the original as strict composition reference; no revision loop",
            "image_edit_basis_sha256": sha256(IMAGE_EDIT_BASIS),
        },
        "palette": {
            "requested_preference": 9,
            "actual": len(candidate_colors),
            "colors": [rgb_hex(color) for color in candidate_colors],
            "uses_exact_existing_nine_color_palette": palette_exact,
        },
        "method": method,
        "measurements": {
            "original_full_resolution": original_stats,
            "existing_indexed_512": existing_stats,
            "candidate_full_resolution": candidate_stats,
            "candidate_at_comparable_512": candidate_512_stats,
            "connected_region_delta_candidate_512_vs_existing": candidate_512_stats[
                "connected_regions_4_neighbor"
            ]
            - existing_stats["connected_regions_4_neighbor"],
            "small_component_delta_candidate_512_vs_existing": candidate_512_stats[
                "small_component_count"
            ]
            - existing_stats["small_component_count"],
            "changed_pixels_against_original_rgb": int(np.count_nonzero(pixel_difference)),
            "changed_fraction_against_original_rgb": float(np.mean(pixel_difference)),
            "mean_absolute_rgb_error_against_original_0_255": float(
                np.abs(candidate.astype(np.int16) - original.astype(np.int16)).mean()
            ),
            "boundaries": edges,
        },
        "region_changes": {
            "removed_or_merged_region_count_at_512": existing_stats[
                "connected_regions_4_neighbor"
            ]
            - candidate_512_stats["connected_regions_4_neighbor"],
            "interpretation": "Count difference only; disconnected meaningful regions can remain and this is not an object-deletion count.",
        },
        "known_limitations_requiring_visual_review": [
            "The candidate uses a generated identity-preserving edit as an edge-cleaned basis; exact pixel identity with the source is not assumed.",
            "Nine material colors necessarily alter RGB shading and may merge visually similar material fills.",
            "Boundary retention is measured geometrically, but facial expression, fingers, costume lines, and weapon details still require user visual approval.",
            "No layer-plan or structural-feasibility claim has been made for this candidate.",
        ],
        "artifacts": {
            "original": "original_source_preview.png",
            "existing_indexed": "existing_indexed_target.png",
            "candidate": "region_aware_flat_color.png",
            "candidate_512": "region_aware_flat_color_512.png",
            "side_by_side": "previews/side_by_side.png",
            "boundary_change_map": "previews/boundary_change_map.png",
            "changed_pixels": "previews/changed_pixels.png",
            "crops": [f"crops/{name}.png" for name in crops],
        },
    }
    write_json(OUTPUT / "validation_report.json", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
