"""Review the existing turtle width-repair proposal without modifying it."""

from __future__ import annotations

import json
import shutil
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "artifacts" / "l1_turtle_width_repair_200mm_v1"
OUTPUT = ROOT / "artifacts" / "l1_turtle_width_repair_200mm_review_v1"
BEFORE = INPUT / "previews" / "indexed_target.png"
AFTER = INPUT / "previews" / "visible_change_proposal_reconstruction.png"
MM_PER_PIXEL = 200.0 / 576.0
MINIMUM_MM = 1.2
PERIMETER_PIXELS = 32
DARK_COLORS = {(14, 15, 13), (23, 51, 75)}

# Review regions only; they do not affect geometry or validation.
REGIONS = {
    "eyes": (150, 135, 305, 220),
    "mouth": (175, 202, 307, 250),
    "head_outline": (92, 85, 340, 280),
    "shell": (18, 215, 210, 392),
    "arms": (115, 235, 350, 372),
    "feet": (125, 295, 360, 412),
    "tail": (12, 245, 155, 392),
    "leaf": (22, 325, 478, 474),
}


def _hex(pixel: tuple[int, ...]) -> str:
    return "#" + "".join(f"{int(value):02X}" for value in pixel)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dark_mask(image: np.ndarray) -> np.ndarray:
    result = np.zeros(image.shape[:2], dtype=bool)
    for color in DARK_COLORS:
        result |= np.all(image == color, axis=2)
    return result


def _contour_measurements(mask: np.ndarray) -> dict[str, Any]:
    """Measure medial-ridge raster thickness, not a vector/CAD width claim."""

    distance = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    local_maximum = (distance > 0) & (
        distance
        >= cv2.dilate(distance, np.ones((3, 3), dtype=np.uint8)) - 1e-5
    )
    # Correct the centre-to-background distance to the occupied pixel-square
    # span: a one-pixel axial stroke is one pixel wide, not two.
    widths = np.maximum(1.0, (2.0 * distance[local_maximum]) - 1.0) * MM_PER_PIXEL
    if not len(widths):
        return {
            "medial_samples": 0,
            "samples_below_1_2mm": 0,
            "percent_below_1_2mm": 0.0,
            "minimum_mm": None,
            "p10_mm": None,
            "median_mm": None,
            "p90_mm": None,
            "maximum_mm": None,
        }
    return {
        "medial_samples": int(len(widths)),
        "samples_below_1_2mm": int(np.count_nonzero(widths < MINIMUM_MM)),
        "percent_below_1_2mm": float(100.0 * np.mean(widths < MINIMUM_MM)),
        "minimum_mm": float(widths.min()),
        "p10_mm": float(np.percentile(widths, 10)),
        "median_mm": float(np.median(widths)),
        "p90_mm": float(np.percentile(widths, 90)),
        "maximum_mm": float(widths.max()),
        "method": (
            "local maxima of an L2 distance transform; occupied raster width "
            "estimated as max(1, 2*radius-1) pixels at 200/576 mm per pixel"
        ),
    }


def _labelled_pair(before: Image.Image, after: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (1024, 548), "white")
    canvas.paste(before, (0, 36))
    canvas.paste(after, (512, 36))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 10), "BEFORE: approved indexed target", fill="black")
    draw.text((520, 10), "AFTER: existing visible-change proposal", fill="black")
    return canvas


def _overlay(before: np.ndarray, after: np.ndarray) -> tuple[np.ndarray, dict[str, int]]:
    changed = np.any(before != after, axis=2)
    before_dark = _dark_mask(before)
    after_dark = _dark_mask(after)
    contour_widening = changed & after_dark & ~before_dark
    dark_substitution = changed & after_dark & before_dark
    non_dark_structure = changed & ~after_dark

    display = ((before.astype(np.uint16) + 255) // 2).astype(np.uint8)
    display[contour_widening] = (28, 188, 76)
    display[non_dark_structure] = (244, 145, 32)
    display[dark_substitution] = (220, 39, 162)
    counts = {
        "changed_pixels": int(np.count_nonzero(changed)),
        "positive_dark_contour_widening_pixels": int(
            np.count_nonzero(contour_widening)
        ),
        "non_dark_structural_widening_pixels": int(
            np.count_nonzero(non_dark_structure)
        ),
        "dark_color_substitution_pixels": int(
            np.count_nonzero(dark_substitution)
        ),
        "unrelated_by_operation_provenance": 0,
    }
    return display, counts


def _crop_panel(
    name: str,
    before: np.ndarray,
    after: np.ndarray,
    overlay: np.ndarray,
    bounds: tuple[int, int, int, int],
) -> None:
    x0, y0, x1, y1 = bounds
    panels = [before[y0:y1, x0:x1], after[y0:y1, x0:x1], overlay[y0:y1, x0:x1]]
    scale = max(2, min(4, 900 // max(1, 3 * (x1 - x0))))
    width = (x1 - x0) * scale
    height = (y1 - y0) * scale
    canvas = Image.new("RGB", (width * 3, height + 38), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (label, panel) in enumerate(
        zip(("BEFORE", "AFTER", "CHANGES"), panels, strict=True)
    ):
        image = Image.fromarray(panel, "RGB").resize(
            (width, height), Image.Resampling.NEAREST
        )
        canvas.paste(image, (index * width, 38))
        draw.text((index * width + 6, 10), f"{name}: {label}", fill="black")
    canvas.save(OUTPUT / "closeups" / f"{name}.png")


def _opening_review(target: np.ndarray, repair_report: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for layer in repair_report["layers"]:
        directory = INPUT / layer["artifact_directory"]
        before = np.asarray(
            Image.open(directory / "before_through_cutouts.png").convert("L")
        ) > 0
        after = np.asarray(
            Image.open(
                directory / "visible_change_proposal_through_cutouts.png"
            ).convert("L")
        ) > 0
        before_count, before_labels, before_stats, _ = cv2.connectedComponentsWithStats(
            before.astype(np.uint8), connectivity=4
        )
        after_count, after_labels, after_stats, _ = cv2.connectedComponentsWithStats(
            after.astype(np.uint8), connectivity=4
        )
        del before_count, after_count
        for warning in layer["before"]["openings"]["narrow_openings"]:
            component = int(warning["component"])
            original = before_labels == component
            ys, xs = np.where(original)
            inside = (
                (ys >= PERIMETER_PIXELS)
                & (ys < PERIMETER_PIXELS + 512)
                & (xs >= PERIMETER_PIXELS)
                & (xs < PERIMETER_PIXELS + 512)
            )
            colors = Counter(
                _hex(tuple(pixel))
                for pixel in target[
                    ys[inside] - PERIMETER_PIXELS,
                    xs[inside] - PERIMETER_PIXELS,
                ]
            )
            dark_exposure = any(color in {"#0E0F0D", "#17334B"} for color in colors)
            overlap_ids = [
                int(value)
                for value in np.unique(after_labels[original])
                if int(value) > 0
            ]
            after_widths: list[float] = []
            for after_id in overlap_ids:
                component_mask = (after_labels == after_id).astype(np.uint8)
                distance = cv2.distanceTransform(component_mask, cv2.DIST_L2, 5)
                after_widths.append(
                    float(max(1.0, (2.0 * distance.max()) - 1.0) * MM_PER_PIXEL)
                )
            x, y, width, height = warning["bounds_pixels"]
            record = {
                "layer_index_back_to_front": layer[
                    "physical_layer_index_back_to_front"
                ],
                "layer_color": layer["color"],
                "component": component,
                "bounds_outer_pixels": warning["bounds_pixels"],
                "bounds_artwork_pixels": [
                    x - PERIMETER_PIXELS,
                    y - PERIMETER_PIXELS,
                    width,
                    height,
                ],
                "area_pixels": warning["area_pixels"],
                "exposed_target_colors": dict(colors),
                "classification": (
                    "VISIBLE_DARK_CONTOUR_OR_DETAIL_OPENING"
                    if dark_exposure
                    else (
                        "UNRELATED_SINGLE_PIXEL_HOLE"
                        if warning["area_pixels"] == 1
                        else "NARROW_NON_DARK_COLOR_OPENING"
                    )
                ),
                "before_effective_raster_width_mm": float(
                    max(
                        MM_PER_PIXEL,
                        warning["max_inscribed_width_mm"] - MM_PER_PIXEL,
                    )
                ),
                "after_effective_raster_width_mm": max(after_widths)
                if after_widths
                else 0.0,
                "proposal_changed_this_opening": not np.array_equal(
                    before[original], after[original]
                ),
            }
            records.append(record)
    counts = Counter(record["classification"] for record in records)
    unique_bounds = {
        tuple(record["bounds_artwork_pixels"])
        for record in records
    }
    unique_by_class: dict[str, set[tuple[int, ...]]] = {}
    for record in records:
        unique_by_class.setdefault(record["classification"], set()).add(
            tuple(record["bounds_artwork_pixels"])
        )
    return {
        "warning_count_by_physical_sheet": len(records),
        "unique_warning_geometries": len(unique_bounds),
        "classification_counts": dict(counts),
        "unique_geometry_counts_by_classification": {
            key: len(value) for key, value in unique_by_class.items()
        },
        "records": records,
        "note": (
            "The same physical opening can recur in several cumulative sheets; "
            "warning count and unique geometry count are therefore different."
        ),
    }


def main() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    (OUTPUT / "closeups").mkdir(parents=True)
    (OUTPUT / "previews").mkdir(parents=True)
    (OUTPUT / "reports").mkdir(parents=True)

    before = np.asarray(Image.open(BEFORE).convert("RGB"))
    after = np.asarray(Image.open(AFTER).convert("RGB"))
    before_image = Image.fromarray(before, "RGB")
    after_image = Image.fromarray(after, "RGB")
    before_image.save(OUTPUT / "previews" / "original_front.png")
    after_image.save(OUTPUT / "previews" / "repaired_front_existing_proposal.png")
    _labelled_pair(before_image, after_image).save(
        OUTPUT / "previews" / "full_resolution_side_by_side.png"
    )

    overlay, change_counts = _overlay(before, after)
    Image.fromarray(overlay, "RGB").save(
        OUTPUT / "previews" / "classified_changed_pixel_overlay.png"
    )
    overlay_image = Image.fromarray(overlay, "RGB")
    labelled_overlay = Image.new("RGB", (512, 550), "white")
    labelled_overlay.paste(overlay_image, (0, 38))
    overlay_draw = ImageDraw.Draw(labelled_overlay)
    overlay_draw.text(
        (7, 8),
        "green=dark contour widening; orange=non-dark structural change; magenta=dark substitution",
        fill="black",
    )
    labelled_overlay.save(
        OUTPUT / "previews" / "classified_changed_pixel_overlay_labelled.png"
    )
    raw = before.copy()
    raw[np.any(before != after, axis=2)] = (232, 35, 46)
    Image.fromarray(raw, "RGB").save(
        OUTPUT / "previews" / "all_changed_pixels_red.png"
    )

    region_rows: list[dict[str, Any]] = []
    before_dark = _dark_mask(before)
    after_dark = _dark_mask(after)
    changed = np.any(before != after, axis=2)
    for name, bounds in REGIONS.items():
        x0, y0, x1, y1 = bounds
        _crop_panel(name, before, after, overlay, bounds)
        region_rows.append(
            {
                "region": name,
                "bounds_pixels": list(bounds),
                "changed_pixels": int(np.count_nonzero(changed[y0:y1, x0:x1])),
                "before_positive_dark_contours": _contour_measurements(
                    before_dark[y0:y1, x0:x1]
                ),
                "after_positive_dark_contours": _contour_measurements(
                    after_dark[y0:y1, x0:x1]
                ),
            }
        )

    repair_report = json.loads(
        (INPUT / "reports" / "width_repair_report.json").read_text(
            encoding="utf-8"
        )
    )
    opening_review = _opening_review(before, repair_report)
    transitions = Counter(
        (_hex(tuple(left)), _hex(tuple(right)))
        for left, right in zip(before[changed], after[changed], strict=True)
    )
    report = {
        "status": "READY_FOR_USER_TEST",
        "recommendation": "REVISE",
        "proposal_modified": False,
        "reviewed_input_sha256": {
            "original_front": _sha256(BEFORE),
            "existing_repair_proposal": _sha256(AFTER),
        },
        "physical_size_mm": [200.0, 200.0],
        "outer_mask_pixels": 576,
        "artwork_preview_pixels": 512,
        "mm_per_outer_mask_pixel": MM_PER_PIXEL,
        "minimum_visible_contour_width_mm": MINIMUM_MM,
        "change_classification": change_counts,
        "color_transitions": [
            {"before": key[0], "after": key[1], "pixels": value}
            for key, value in transitions.most_common()
        ],
        "positive_visible_contours": {
            "before": _contour_measurements(before_dark),
            "after": _contour_measurements(after_dark),
            "interpretation": (
                "The proposal improves many structural/outline paths but does "
                "not normalize every visible contour to 1.2 mm. Endpoint and "
                "diagonal raster samples are included and require vector-stage "
                "confirmation before manufacturing claims."
            ),
        },
        "regions": region_rows,
        "negative_openings": opening_review,
        "structural_validation": {
            "four_neighbor_failing_layers": repair_report[
                "visible_change_proposal_not_approved"
            ]["four_neighbor_failing_layers_after"],
            "orphan_structural_core_failing_layers": repair_report[
                "visible_change_proposal_not_approved"
            ]["failing_layers_after"],
            "missing_shared_perimeter_pixels": repair_report[
                "visible_change_proposal_not_approved"
            ]["shared_perimeter_missing_pixels_by_layer"],
            "thin_decorative_warnings": repair_report[
                "visible_change_proposal_not_approved"
            ]["thin_decorative_visible_component_warnings_after"],
            "visible_components_outside_restored_support": repair_report[
                "visible_change_proposal_not_approved"
            ]["visible_components_outside_restored_support_after"],
        },
        "visual_review": {
            "preserved": [
                "overall turtle and leaf silhouette",
                "eye shapes and highlights",
                "facial expression at normal viewing size",
                "shell, limbs, tail, leaf, mountain, clouds, and water remain recognizable",
                "palette and layer color order are unchanged",
            ],
            "unacceptable_or_incomplete": [
                "mouth and several internal dark strokes remain below 1.2 mm",
                "the dark facial opening warning remains unchanged below 1.2 mm",
                "404 non-dark pixels alter colored boundaries for structural repair rather than contour normalization",
                "localized black widening is asymmetric/jagged at the mountain, shoreline, limbs, shell, and leaf veins",
                "seven thin-decorative warnings remain after the proposal",
            ],
        },
        "recommendation_reason": (
            "REVISE: the proposal preserves identity and fixes orphan structural "
            "cores, but it neither enforces 1.2 mm on all visible positive and "
            "negative contours nor limits changes to clean contour normalization."
        ),
    }
    (OUTPUT / "reports" / "review.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    markdown = [
        "# Turtle 200 mm width-repair visual review",
        "",
        "Recommendation: **REVISE**.",
        "",
        f"- Changed visible pixels: {change_counts['changed_pixels']}.",
        f"- Positive dark-contour additions: {change_counts['positive_dark_contour_widening_pixels']}.",
        f"- Non-dark structural changes: {change_counts['non_dark_structural_widening_pixels']}.",
        f"- Dark color substitutions: {change_counts['dark_color_substitution_pixels']}.",
        f"- Positive-contour medial samples below 1.2 mm: {report['positive_visible_contours']['before']['samples_below_1_2mm']} before, {report['positive_visible_contours']['after']['samples_below_1_2mm']} after.",
        "- Opening warnings: 29 sheet-level warnings across 9 unique geometries; only one exposes a dark visible detail, and it remains unchanged below 1.2 mm.",
        "",
        "The proposal retains the scene and character identity and clears orphan structural cores. It is incomplete as visible-contour normalization and contains non-dark boundary changes and locally rough/asymmetric widening. It should not become the new target without revision.",
    ]
    (OUTPUT / "reports" / "REVIEW.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    print(json.dumps(change_counts, sort_keys=True), flush=True)
    print(json.dumps(opening_review["classification_counts"], sort_keys=True), flush=True)
    print("RECOMMENDATION=REVISE", flush=True)


if __name__ == "__main__":
    main()
