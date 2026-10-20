"""Create one review-only contour-normalized turtle target at 200 mm."""

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


ROOT = Path(__file__).resolve().parents[1]
INPUT_ROOT = ROOT / "artifacts" / "l1_turtle_width_repair_200mm_v1"
SOURCE = INPUT_ROOT / "source" / "indexed_target.png"
PREVIOUS_REJECTED = INPUT_ROOT / "previews" / "visible_change_proposal_reconstruction.png"
OUTPUT = ROOT / "artifacts" / "l1_turtle_contour_normalization_200mm_v1"
BLACK = (14, 15, 13)
CONFIG = ContourNormalizationConfig(
    print_width_mm=200.0,
    print_height_mm=200.0,
    minimum_contour_width_mm=1.2,
    internal_scale=4,
    minimum_segment_length_pixels=6,
    thin_fraction_trigger=0.10,
    endpoint_guard_pixels=2,
    contour_color=BLACK,
)

# These named boxes are review annotations only.  They never influence which
# pixels the normalizer changes.
REGIONS = {
    "sun": (330, 25, 480, 180),
    "turtle_face": (105, 90, 330, 275),
    "turtle_face_and_shell": (65, 85, 350, 410),
    "leaf": (10, 315, 465, 480),
    "mountain": (285, 85, 512, 270),
    "eyes": (155, 135, 305, 220),
    "mouth": (175, 200, 308, 250),
    "head_outline": (90, 85, 342, 282),
    "shell": (18, 215, 215, 395),
    "arms_and_feet": (112, 235, 355, 415),
    "tail": (10, 245, 158, 395),
    "clouds": (10, 25, 230, 155),
    "water": (0, 260, 512, 512),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hex(color: Any) -> str:
    return "#" + "".join(f"{int(value):02X}" for value in color)


def _palette(image: np.ndarray) -> list[dict[str, Any]]:
    unique, counts = np.unique(image.reshape(-1, 3), axis=0, return_counts=True)
    return [
        {"color": _hex(color), "pixels": int(count)}
        for color, count in zip(unique, counts, strict=True)
    ]


def _component_counts(image: np.ndarray) -> dict[str, int]:
    return {
        _hex(color): int(
            cv2.connectedComponents(
                np.all(image == color, axis=2).astype(np.uint8), connectivity=4
            )[0]
            - 1
        )
        for color in np.unique(image.reshape(-1, 3), axis=0)
    }


def _labelled_pair(before: np.ndarray, after: np.ndarray) -> Image.Image:
    height, width = before.shape[:2]
    canvas = Image.new("RGB", (2 * width, height + 40), "white")
    canvas.paste(Image.fromarray(before), (0, 40))
    canvas.paste(Image.fromarray(after), (width, 40))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 12), "BEFORE: original indexed target", fill="black")
    draw.text((width + 8, 12), "AFTER: contour-normalized candidate", fill="black")
    return canvas


def _change_overlay(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    changed = np.any(before != after, axis=2)
    overlay = ((before.astype(np.uint16) + 255) // 2).astype(np.uint8)
    overlay[changed] = (235, 32, 90)
    return overlay


def _crop_triptych(
    name: str,
    before: np.ndarray,
    after: np.ndarray,
    overlay: np.ndarray,
    bounds: tuple[int, int, int, int],
) -> None:
    x0, y0, x1, y1 = bounds
    panels = (before[y0:y1, x0:x1], after[y0:y1, x0:x1], overlay[y0:y1, x0:x1])
    source_width = x1 - x0
    source_height = y1 - y0
    scale = max(2, min(4, 1050 // max(1, source_width * 3)))
    panel_width = source_width * scale
    panel_height = source_height * scale
    canvas = Image.new("RGB", (panel_width * 3, panel_height + 38), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (label, panel) in enumerate(
        zip(("BEFORE", "NORMALIZED", "CHANGED PIXELS"), panels, strict=True)
    ):
        enlarged = Image.fromarray(panel).resize(
            (panel_width, panel_height), Image.Resampling.NEAREST
        )
        canvas.paste(enlarged, (index * panel_width, 38))
        draw.text((index * panel_width + 6, 10), f"{name}: {label}", fill="black")
    canvas.save(OUTPUT / "closeups" / f"{name}.png")


def _previous_comparison(
    before: np.ndarray, normalized: np.ndarray, previous: np.ndarray
) -> Image.Image:
    x0, y0, x1, y1 = REGIONS["sun"]
    panels = [before[y0:y1, x0:x1], normalized[y0:y1, x0:x1], previous[y0:y1, x0:x1]]
    scale = 3
    width, height = (x1 - x0) * scale, (y1 - y0) * scale
    canvas = Image.new("RGB", (width * 3, height + 42), "white")
    draw = ImageDraw.Draw(canvas)
    labels = ("ORIGINAL", "NEW CONTOUR STAGE", "REJECTED STRUCTURAL REPAIR")
    for index, (label, panel) in enumerate(zip(labels, panels, strict=True)):
        enlarged = Image.fromarray(panel).resize((width, height), Image.Resampling.NEAREST)
        canvas.paste(enlarged, (index * width, 42))
        draw.text((index * width + 5, 12), label, fill="black")
    return canvas


def _bounds_intersection_ratio(
    bounds: list[int], region: tuple[int, int, int, int]
) -> float:
    x0, y0, x1, y1 = bounds
    rx0, ry0, rx1, ry1 = region
    area = max(1, (x1 - x0) * (y1 - y0))
    overlap = max(0, min(x1, rx1) - max(x0, rx0)) * max(
        0, min(y1, ry1) - max(y0, ry0)
    )
    return overlap / area


def main() -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    (OUTPUT / "source").mkdir(parents=True)
    (OUTPUT / "previews").mkdir(parents=True)
    (OUTPUT / "closeups").mkdir(parents=True)
    (OUTPUT / "reports").mkdir(parents=True)

    before = np.asarray(Image.open(SOURCE).convert("RGB"))
    normalized, engine_report = normalize_contours(before, CONFIG)
    changed = np.any(before != normalized, axis=2)
    overlay = _change_overlay(before, normalized)

    shutil.copy2(SOURCE, OUTPUT / "source" / "original_indexed_target.png")
    Image.fromarray(before).save(OUTPUT / "previews" / "original_indexed.png")
    Image.fromarray(normalized).save(OUTPUT / "previews" / "normalized_flat_color.png")
    _labelled_pair(before, normalized).save(OUTPUT / "previews" / "before_after.png")
    Image.fromarray(overlay).save(OUTPUT / "previews" / "changed_pixel_overlay.png")

    for name in ("sun", "turtle_face_and_shell", "leaf", "mountain"):
        _crop_triptych(name, before, normalized, overlay, REGIONS[name])

    previous_metrics: dict[str, Any] | None = None
    if PREVIOUS_REJECTED.is_file():
        previous = np.asarray(Image.open(PREVIOUS_REJECTED).convert("RGB"))
        _previous_comparison(before, normalized, previous).save(
            OUTPUT / "closeups" / "sun_vs_rejected_structural_repair.png"
        )
        sx0, sy0, sx1, sy1 = REGIONS["sun"]
        previous_metrics = {
            "source": str(PREVIOUS_REJECTED.relative_to(ROOT)),
            "all_changed_pixels": int(np.count_nonzero(np.any(previous != before, axis=2))),
            "sun_crop_changed_pixels": int(
                np.count_nonzero(
                    np.any(previous[sy0:sy1, sx0:sx1] != before[sy0:sy1, sx0:sx1], axis=2)
                )
            ),
            "new_sun_crop_changed_pixels": int(
                np.count_nonzero(changed[sy0:sy1, sx0:sx1])
            ),
        }

    after_by_id = {
        int(row["segment_id"]): row for row in engine_report["after_measurements"]
    }
    contour_rows: list[dict[str, Any]] = []
    sun_segment_ids: list[int] = []
    for row in engine_report["segments"]:
        segment_id = int(row["segment_id"])
        combined = {**row, **after_by_id[segment_id]}
        contour_rows.append(combined)
        if (
            "#F1D748" in row["adjacent_colors"]
            and _bounds_intersection_ratio(row["bounds_pixels"], REGIONS["sun"]) > 0
        ):
            sun_segment_ids.append(segment_id)

    with (OUTPUT / "reports" / "per_contour_widths.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = [
            "segment_id",
            "component_id",
            "topology",
            "point_count",
            "bounds_pixels",
            "before_min_mm",
            "before_p10_mm",
            "before_median_mm",
            "before_p90_mm",
            "intended_nominal_mm",
            "thin_fraction",
            "requires_normalization",
            "disposition",
            "after_min_mm",
            "after_p10_mm",
            "after_median_mm",
            "passes_representative_width",
            "adjacent_colors",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(contour_rows)

    region_changes = {}
    for name, (x0, y0, x1, y1) in REGIONS.items():
        region_changes[name] = int(np.count_nonzero(changed[y0:y1, x0:x1]))

    palette_before = _palette(before)
    palette_after = _palette(normalized)
    components_before = _component_counts(before)
    components_after = _component_counts(normalized)
    component_deltas = {
        color: components_after[color] - count
        for color, count in components_before.items()
        if components_after[color] != count
    }
    sun_rows = [row for row in contour_rows if row["segment_id"] in sun_segment_ids]
    sun_modified = [
        row["segment_id"]
        for row in sun_rows
        if row["segment_id"] in engine_report["accepted_segment_ids"]
    ]
    sun_before_medians = [row["before_median_mm"] for row in sun_rows]
    sun_after_medians = [row["after_median_mm"] for row in sun_rows]
    sun = {
        "coherent_segment_ids": sun_segment_ids,
        "normalized_segment_ids": sun_modified,
        "before_median_range_mm": [
            min(sun_before_medians) if sun_before_medians else None,
            max(sun_before_medians) if sun_before_medians else None,
        ],
        "after_median_range_mm": [
            min(sun_after_medians) if sun_after_medians else None,
            max(sun_after_medians) if sun_after_medians else None,
        ],
        "all_modified_segments_pass": all(
            row["passes_representative_width"]
            for row in sun_rows
            if row["segment_id"] in sun_modified
        ),
        "changed_pixels_in_review_crop": region_changes["sun"],
        "curvature_method": (
            "centerline paths are preserved; complete deficient graph segments are "
            "stroked on a 4x grid rather than patch-dilated locally"
        ),
    }

    disposition_counts = Counter(row["disposition"] for row in contour_rows)
    normalized_but_failed = engine_report["segments_unresolved_after_validation"]
    unintended_shape_change = bool(component_deltas)
    if (
        normalized_but_failed
        or engine_report["segments_rejected_to_avoid_merge"]
        or engine_report["color_topology_rejections"]
        or unintended_shape_change
        or not sun["all_modified_segments_pass"]
    ):
        recommendation = "REVISE"
    else:
        recommendation = "ACCEPT"

    report = {
        "status": "READY_FOR_USER_TEST",
        "recommendation": recommendation,
        "milestone_approval": "L1_NOT_APPROVED",
        "source": {
            "path": str(SOURCE.relative_to(ROOT)),
            "sha256": _sha256(SOURCE),
            "dimensions_pixels": [int(before.shape[1]), int(before.shape[0])],
            "previous_structural_repair_used_as_source": False,
        },
        "physical_mapping": {
            "print_size_mm": [CONFIG.print_width_mm, CONFIG.print_height_mm],
            "mm_per_pixel": engine_report["mm_per_pixel"],
            "minimum_contour_width_mm": CONFIG.minimum_contour_width_mm,
            "minimum_width_in_native_pixels": engine_report["required_native_pixels"],
            "internal_scale": CONFIG.internal_scale,
            "measurement_uncertainty_mm": engine_report[
                "raster_measurement_uncertainty_mm"
            ],
        },
        "summary": {
            "identified_contour_segments": engine_report[
                "identified_contour_segments"
            ],
            "segments_requiring_modification": engine_report[
                "segments_requiring_normalization"
            ],
            "segments_normalized": engine_report["segments_normalized"],
            "segments_unresolved": len(
                engine_report["segments_unresolved_after_validation"]
            ),
            "short_segments_preserved_for_review": disposition_counts[
                "UNRESOLVED_SHORT_SEGMENT"
            ],
            "filled_dark_components_preserved": engine_report[
                "filled_dark_components_preserved"
            ],
            "changed_visible_pixels": engine_report["changed_visible_pixels"],
            "changed_visible_percent": engine_report["changed_visible_percent"],
            "all_changes_are_existing_black": engine_report[
                "all_changes_are_additions_of_existing_contour_color"
            ],
        },
        "palette": {
            "before": palette_before,
            "after": palette_after,
            "preserved": engine_report["palette_preserved"],
        },
        "topology": {
            "per_color_components_before": components_before,
            "per_color_components_after": components_after,
            "component_count_deltas": component_deltas,
            "dark_components_before": engine_report["dark_components_before"],
            "dark_components_after": engine_report["dark_components_after"],
            "distinct_dark_components_merged": engine_report[
                "distinct_dark_components_merged"
            ],
            "rejected_dark_component_merges": engine_report[
                "component_merge_rejections"
            ],
            "rejected_colored_region_topology_changes": engine_report[
                "color_topology_rejections"
            ],
        },
        "sun_acceptance": sun,
        "region_changed_pixels": region_changes,
        "previous_rejected_proposal_comparison": previous_metrics,
        "unresolved_segment_ids": engine_report[
            "segments_unresolved_after_validation"
        ],
        "validation_interpretation": {
            "unwanted_bulges": (
                "NOT_DETECTED_BY_TOPOLOGY_CHECKS"
                if not component_deltas
                else "POSSIBLE_COLORED_REGION_SPLIT_OR_LOSS"
            ),
            "objects_or_facial_features_changed": (
                "black contour additions only; filled eyes and solid facial marks are protected; "
                "mouth contour widening is intentional"
            ),
            "nearby_contours_merged": engine_report[
                "distinct_dark_components_merged"
            ],
            "important_limitation": (
                "A raster centerline and 4x distance field provide a controlled estimate, "
                "not a vector-stroke semantic proof. Short/junction fragments are left "
                "unresolved rather than forced."
            ),
        },
        "engine_report": engine_report,
    }
    (OUTPUT / "reports" / "validation.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    unresolved = report["summary"]["segments_unresolved"]
    markdown = [
        "# Turtle contour-normalization review",
        "",
        f"Recommendation: **{recommendation}**.",
        "",
        "This is one deterministic visual-target candidate generated from the original indexed image. It is not a physical-layer repair or an approved target.",
        "",
        f"- Identified contour segments: {report['summary']['identified_contour_segments']}",
        f"- Segments requiring normalization: {report['summary']['segments_requiring_modification']}",
        f"- Segments normalized: {report['summary']['segments_normalized']}",
        f"- Remaining unresolved normalized segments: {unresolved}",
        f"- Changed visible pixels: {report['summary']['changed_visible_pixels']} ({report['summary']['changed_visible_percent']:.3f}%)",
        f"- Exact nine-color palette preserved: {report['palette']['preserved']}",
        f"- Distinct black components merged: {report['topology']['distinct_dark_components_merged']}",
        f"- Colored-region component deltas: {report['topology']['component_count_deltas']}",
        f"- Sun segment IDs: {sun_segment_ids}; normalized: {sun_modified}",
        f"- Sun median-width range before: {sun['before_median_range_mm']}",
        f"- Sun median-width range after: {sun['after_median_range_mm']}",
        "",
        "Width values are raster-square estimates at a 4x analysis grid. The final 512-pixel target has ±0.195 mm half-pixel measurement uncertainty at 200 mm.",
    ]
    (OUTPUT / "reports" / "REVIEW.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], sort_keys=True), flush=True)
    print(json.dumps(report["sun_acceptance"], sort_keys=True), flush=True)
    print(f"RECOMMENDATION={recommendation}", flush=True)


if __name__ == "__main__":
    main()

