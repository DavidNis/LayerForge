"""One deterministic turtle image-preparation pass; no layer planning."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from layerforge_l1.contour_normalization import (
    ContourNormalizationConfig,
    normalize_contours,
)
from layerforge_l1.tiny_region_cleanup import (
    TinyRegionCleanupConfig,
    clean_tiny_regions,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "artifacts"
    / "l1_turtle_width_repair_200mm_v1"
    / "source"
    / "indexed_target.png"
)
OUTPUT = ROOT / "artifacts" / "l1_turtle_pre_layer_preparation_200mm_v1"
CONTOUR_COLOR = (14, 15, 13)
CONTOUR_CONFIG = ContourNormalizationConfig(
    print_width_mm=200.0,
    print_height_mm=200.0,
    minimum_contour_width_mm=1.2,
    internal_scale=4,
    minimum_segment_length_pixels=6,
    thin_fraction_trigger=0.10,
    endpoint_guard_pixels=2,
    contour_color=CONTOUR_COLOR,
)
CLEANUP_CONFIG = TinyRegionCleanupConfig(
    maximum_area_pixels=16,
    maximum_extent_pixels=6,
    dominant_neighbor_ratio=0.85,
    contour_fill_ratio=0.45,
    contour_colors=(CONTOUR_COLOR,),
)

# Crop coordinates document review areas only; they do not influence edits.
REGIONS = {
    "turtle_face": (145, 125, 310, 250),
    "shell": (18, 215, 275, 410),
    "sun": (330, 25, 480, 180),
    "leaf_ground_background": (0, 165, 512, 512),
}


def _hex(color: Any) -> str:
    return "#" + "".join(f"{int(value):02X}" for value in color)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _palette(image: np.ndarray) -> list[dict[str, Any]]:
    colors, counts = np.unique(image.reshape(-1, 3), axis=0, return_counts=True)
    return [
        {"color": _hex(color), "pixels": int(count)}
        for color, count in zip(colors, counts, strict=True)
    ]


def _component_summary(image: np.ndarray) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for color in np.unique(image.reshape(-1, 3), axis=0):
        mask = np.all(image == color, axis=2).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
        areas = [int(stats[index, cv2.CC_STAT_AREA]) for index in range(1, count)]
        summary[_hex(color)] = {
            "components": count - 1,
            "components_at_most_16_pixels": sum(area <= 16 for area in areas),
        }
    return summary


def _new_tiny_components(source: np.ndarray, result: np.ndarray) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for color in np.unique(result.reshape(-1, 3), axis=0):
        source_mask = np.all(source == color, axis=2).astype(np.uint8)
        result_mask = np.all(result == color, axis=2).astype(np.uint8)
        source_count, source_labels, source_stats, _ = cv2.connectedComponentsWithStats(
            source_mask, connectivity=4
        )
        result_count, result_labels, result_stats, _ = cv2.connectedComponentsWithStats(
            result_mask, connectivity=4
        )
        del source_count
        for component_id in range(1, result_count):
            area = int(result_stats[component_id, cv2.CC_STAT_AREA])
            if area > 16:
                continue
            component = result_labels == component_id
            predecessors = [
                int(value)
                for value in np.unique(source_labels[component])
                if int(value) > 0
            ]
            was_already_tiny = any(
                int(source_stats[value, cv2.CC_STAT_AREA]) <= 16
                for value in predecessors
            )
            if not was_already_tiny:
                x, y, width, height = (
                    int(value) for value in result_stats[component_id, :4]
                )
                records.append(
                    {
                        "color": _hex(color),
                        "area_pixels": area,
                        "bounds_pixels": [x, y, width, height],
                    }
                )
    return records


def _comparison(before: np.ndarray, after: np.ndarray) -> Image.Image:
    height, width = before.shape[:2]
    canvas = Image.new("RGB", (width * 2, height + 40), "white")
    canvas.paste(Image.fromarray(before), (0, 40))
    canvas.paste(Image.fromarray(after), (width, 40))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 12), "BEFORE: approved indexed turtle", fill="black")
    draw.text((width + 8, 12), "AFTER: pre-layer image candidate", fill="black")
    return canvas


def _overlay(
    source: np.ndarray,
    cleaned: np.ndarray,
    processed: np.ndarray,
) -> tuple[np.ndarray, dict[str, int]]:
    cleanup = np.any(source != cleaned, axis=2)
    contour = np.any(cleaned != processed, axis=2)
    display = ((source.astype(np.uint16) + 255) // 2).astype(np.uint8)
    display[contour] = (231, 45, 104)
    display[cleanup] = (255, 146, 24)
    return display, {
        "tiny_cleanup_pixels_orange": int(np.count_nonzero(cleanup)),
        "contour_widening_pixels_magenta": int(np.count_nonzero(contour)),
        "total_changed_pixels": int(np.count_nonzero(np.any(source != processed, axis=2))),
    }


def _crop_triptych(
    name: str,
    before: np.ndarray,
    after: np.ndarray,
    overlay: np.ndarray,
    bounds: tuple[int, int, int, int],
) -> None:
    x0, y0, x1, y1 = bounds
    panels = (before[y0:y1, x0:x1], after[y0:y1, x0:x1], overlay[y0:y1, x0:x1])
    source_width, source_height = x1 - x0, y1 - y0
    scale = max(2, min(4, 1100 // max(1, source_width * 3)))
    panel_width, panel_height = source_width * scale, source_height * scale
    canvas = Image.new("RGB", (panel_width * 3, panel_height + 38), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (label, panel) in enumerate(
        zip(("BEFORE", "PROCESSED", "CHANGES"), panels, strict=True)
    ):
        enlarged = Image.fromarray(panel).resize(
            (panel_width, panel_height), Image.Resampling.NEAREST
        )
        canvas.paste(enlarged, (index * panel_width, 38))
        draw.text((index * panel_width + 6, 10), f"{name}: {label}", fill="black")
    canvas.save(OUTPUT / "closeups" / f"{name}.png")


def _tiny_merge_montage(
    source: np.ndarray,
    processed: np.ndarray,
    overlay: np.ndarray,
    decisions: list[dict[str, Any]],
) -> None:
    merged = [row for row in decisions if row["decision"].startswith("MERGE_")]
    tile_size = 126
    padding = 12
    canvas = Image.new("RGB", (tile_size * 3, max(1, len(merged)) * tile_size), "white")
    draw = ImageDraw.Draw(canvas)
    for row_index, row in enumerate(merged):
        x, y, width, height = row["bounds_pixels"]
        radius = 9
        x0, y0 = max(0, x - radius), max(0, y - radius)
        x1, y1 = min(source.shape[1], x + width + radius), min(source.shape[0], y + height + radius)
        for column, (label, image) in enumerate(
            (("BEFORE", source), ("PROCESSED", processed), ("CHANGES", overlay))
        ):
            crop = Image.fromarray(image[y0:y1, x0:x1]).resize(
                (tile_size, tile_size), Image.Resampling.NEAREST
            )
            canvas.paste(crop, (column * tile_size, row_index * tile_size))
            draw.rectangle(
                (
                    column * tile_size + padding,
                    row_index * tile_size + padding,
                    column * tile_size + padding + 2,
                    row_index * tile_size + padding + 2,
                ),
                outline="red",
            )
            draw.text(
                (column * tile_size + 3, row_index * tile_size + 3),
                f"{label} {x},{y}",
                fill="black",
            )
    canvas.save(OUTPUT / "closeups" / "tiny_region_merges.png")


def main() -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    if OUTPUT.exists():
        raise FileExistsError(
            f"Refusing to overwrite the one-pass artifact directory: {OUTPUT}"
        )
    (OUTPUT / "source").mkdir(parents=True)
    (OUTPUT / "previews").mkdir(parents=True)
    (OUTPUT / "closeups").mkdir(parents=True)
    (OUTPUT / "reports").mkdir(parents=True)

    source = np.asarray(Image.open(SOURCE).convert("RGB"))
    cleaned, cleanup_report = clean_tiny_regions(source, CLEANUP_CONFIG)
    processed, contour_report = normalize_contours(cleaned, CONTOUR_CONFIG)
    overlay, change_counts = _overlay(source, cleaned, processed)
    changed = np.any(source != processed, axis=2)

    shutil.copy2(SOURCE, OUTPUT / "source" / "original_indexed_target.png")
    Image.fromarray(source).save(OUTPUT / "previews" / "original_input.png")
    Image.fromarray(processed).save(
        OUTPUT / "previews" / "processed_flat_color_candidate.png"
    )
    _comparison(source, processed).save(OUTPUT / "previews" / "before_after.png")
    Image.fromarray(overlay).save(OUTPUT / "previews" / "changed_pixel_overlay.png")

    for name, bounds in REGIONS.items():
        _crop_triptych(name, source, processed, overlay, bounds)
    _tiny_merge_montage(
        source, processed, overlay, cleanup_report["decisions"]
    )

    after_by_id = {
        int(row["segment_id"]): row
        for row in contour_report["after_measurements"]
    }
    accepted = set(contour_report["accepted_segment_ids"])
    rejected = set(contour_report["segments_rejected_to_avoid_merge"])
    thin_rows: list[dict[str, Any]] = []
    unresolved_ids: list[int] = []
    for segment in contour_report["segments"]:
        if float(segment["before_p10_mm"]) >= CONTOUR_CONFIG.minimum_contour_width_mm:
            continue
        segment_id = int(segment["segment_id"])
        after = after_by_id[segment_id]
        if segment_id in accepted and after["passes_representative_width"]:
            outcome = "WIDENED_PASS"
        elif segment_id in rejected:
            outcome = "UNRESOLVED_REJECTED_TO_PROTECT_GEOMETRY"
            unresolved_ids.append(segment_id)
        elif segment["disposition"] == "UNRESOLVED_SHORT_SEGMENT":
            outcome = "UNRESOLVED_SHORT_OR_JUNCTION_FRAGMENT"
            unresolved_ids.append(segment_id)
        elif segment["disposition"] == "PRESERVE_FILLED_DARK_REGION":
            # Filled regions are intentionally excluded from the contour rule.
            outcome = "FILLED_DARK_REGION_NOT_A_CONTOUR"
        else:
            outcome = "UNRESOLVED_WIDTH_VALIDATION"
            unresolved_ids.append(segment_id)
        thin_rows.append({**segment, **after, "outcome": outcome})

    (OUTPUT / "reports" / "thin_contour_segments.json").write_text(
        json.dumps(thin_rows, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "reports" / "tiny_region_cleanup.json").write_text(
        json.dumps(cleanup_report, indent=2) + "\n", encoding="utf-8"
    )
    with (OUTPUT / "reports" / "thin_contour_segments.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = [
            "segment_id",
            "component_id",
            "topology",
            "bounds_pixels",
            "point_count",
            "before_min_mm",
            "before_p10_mm",
            "before_median_mm",
            "after_min_mm",
            "after_p10_mm",
            "after_median_mm",
            "outcome",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(thin_rows)

    palette_before = _palette(source)
    palette_after = _palette(processed)
    components_before = _component_summary(source)
    components_after = _component_summary(processed)
    new_tiny = _new_tiny_components(source, processed)
    region_changed_pixels = {
        name: int(np.count_nonzero(changed[y0:y1, x0:x1]))
        for name, (x0, y0, x1, y1) in REGIONS.items()
    }
    cleanup_counts = Counter(
        row["decision"] for row in cleanup_report["decisions"]
    )
    filled_exclusions = sum(
        row["outcome"] == "FILLED_DARK_REGION_NOT_A_CONTOUR"
        for row in thin_rows
    )
    report = {
        "status": "READY_FOR_USER_TEST",
        "milestone_approval": "L1_NOT_APPROVED",
        "scope": "PRE_LAYER_IMAGE_PREPARATION_ONLY",
        "source": {
            "path": str(SOURCE.relative_to(ROOT)),
            "sha256": _sha256(SOURCE),
            "source_overwritten": False,
            "jpeg_recompression": False,
        },
        "physical_mapping": {
            "image_resolution_pixels": [int(source.shape[1]), int(source.shape[0])],
            "print_size_mm": [200.0, 200.0],
            "mm_per_pixel": 200.0 / source.shape[1],
            "minimum_contour_width_mm": 1.2,
            "minimum_contour_width_pixels": 1.2 / (200.0 / source.shape[1]),
            "analysis_scale": CONTOUR_CONFIG.internal_scale,
            "half_pixel_measurement_uncertainty_mm": (200.0 / source.shape[1]) / 2.0,
        },
        "metrics": {
            "contour_segments_inspected": contour_report[
                "identified_contour_segments"
            ],
            "contour_segments_below_threshold": len(thin_rows),
            "contour_segments_widened": sum(
                row["outcome"] == "WIDENED_PASS" for row in thin_rows
            ),
            "unresolved_thin_contour_cases": len(set(unresolved_ids)),
            "filled_dark_regions_excluded_from_contour_rule": filled_exclusions,
            "tiny_regions_inspected": cleanup_report["tiny_regions_inspected"],
            "tiny_regions_merged": cleanup_report["tiny_regions_merged"],
            "tiny_regions_preserved_ambiguous": cleanup_report[
                "tiny_regions_preserved_ambiguous"
            ],
            "tiny_dark_details_preserved": cleanup_report[
                "tiny_dark_details_preserved"
            ],
            "total_changed_pixels": change_counts["total_changed_pixels"],
            "percent_image_modified": float(100.0 * np.mean(changed)),
            "palette_changed": {
                row["color"] for row in palette_before
            }
            != {row["color"] for row in palette_after},
            "new_suspicious_tiny_regions_introduced": len(new_tiny),
        },
        "change_types": change_counts,
        "palette_before": palette_before,
        "palette_after": palette_after,
        "components_before": components_before,
        "components_after": components_after,
        "new_tiny_region_details": new_tiny,
        "tiny_cleanup_decision_counts": dict(cleanup_counts),
        "region_changed_pixels": region_changed_pixels,
        "unresolved_contour_segment_ids": sorted(set(unresolved_ids)),
        "meaningful_detail_review": {
            "eyes_or_filled_facial_marks_lost": False,
            "semantic_objects_removed": False,
            "mouth_stroke_widening_is_intentional": True,
            "palette_remains_flat_and_discrete": True,
            "anti_alias_or_gradient_colors_added": False,
            "basis": (
                "cleanup protects dark components; contour edits only add the existing "
                "dark contour color; no palette colors or source objects are invented"
            ),
        },
        "recommendation": (
            "REVISE"
            if unresolved_ids or new_tiny
            else "ACCEPT_FOR_USER_VISUAL_REVIEW"
        ),
        "limitations": [
            "Raster contour widths are measured on a 4x nearest-neighbor grid, not vector paths.",
            "Short junction fragments are preserved when a stable complete segment cannot be inferred.",
            "No physical sheet, opening, connectivity, or layer-plan validation was run.",
        ],
    }
    (OUTPUT / "reports" / "validation.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    markdown = [
        "# Turtle pre-layer image preparation",
        "",
        f"Status: **{report['status']}**. Recommendation: **{report['recommendation']}**.",
        "",
        "This is a visual-source preprocessing artifact only. No layer planning, CP-SAT, CAD, STL, or STEP operation was run.",
        "",
        f"- Resolution: {source.shape[1]} x {source.shape[0]} px",
        f"- Physical scale: {200.0 / source.shape[1]:.6f} mm/px",
        f"- 1.2 mm minimum: {1.2 / (200.0 / source.shape[1]):.3f} px",
        f"- Contour segments inspected: {report['metrics']['contour_segments_inspected']}",
        f"- Below-threshold contour segments: {report['metrics']['contour_segments_below_threshold']}",
        f"- Widened and verified: {report['metrics']['contour_segments_widened']}",
        f"- Unresolved thin contours: {report['metrics']['unresolved_thin_contour_cases']}",
        f"- Tiny regions inspected / merged / ambiguous: {report['metrics']['tiny_regions_inspected']} / {report['metrics']['tiny_regions_merged']} / {report['metrics']['tiny_regions_preserved_ambiguous']}",
        f"- Changed pixels: {report['metrics']['total_changed_pixels']} ({report['metrics']['percent_image_modified']:.3f}%)",
        f"- Palette changed: {report['metrics']['palette_changed']}",
        f"- New suspicious tiny regions: {report['metrics']['new_suspicious_tiny_regions_introduced']}",
        "",
        "Changed-pixel overlay legend: magenta = contour widening; orange = tiny-region cleanup.",
    ]
    (OUTPUT / "reports" / "REVIEW.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["metrics"], sort_keys=True), flush=True)
    print(json.dumps(report["tiny_cleanup_decision_counts"], sort_keys=True), flush=True)
    print(f"RECOMMENDATION={report['recommendation']}", flush=True)


if __name__ == "__main__":
    main()

