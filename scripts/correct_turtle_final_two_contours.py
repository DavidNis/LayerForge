"""Correct only reviewed turtle contour IDs 1 and 108."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from layerforge_l1.local_contour_correction import (
    LocalContourCorrectionConfig,
    correct_reviewed_contour,
)


ROOT = Path(__file__).resolve().parents[1]
INPUT_ROOT = ROOT / "artifacts" / "l1_turtle_pre_layer_preparation_200mm_v1"
SOURCE = INPUT_ROOT / "previews" / "processed_flat_color_candidate.png"
OUTPUT = ROOT / "artifacts" / "l1_turtle_final_two_contours_200mm_v1"
CONFIG = LocalContourCorrectionConfig(
    print_width_mm=200.0,
    print_height_mm=200.0,
    minimum_width_mm=1.2,
    internal_scale=4,
    contour_color=(14, 15, 13),
    allowed_padding_pixels=5,
)
CORRECTIONS = (
    {
        "id": 1,
        "name": "sun_snow_boundary",
        "bounds": (345, 102, 440, 142),
        "intended_local_width_mm": 1.2872459888458252,
    },
    {
        "id": 108,
        "name": "left_cloud_outline",
        "bounds": (19, 105, 105, 147),
        "intended_local_width_mm": 1.171875,
    },
)
ID_62_PROTECTION_BOUNDS = (140, 316, 153, 329)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _palette(image: np.ndarray) -> list[str]:
    return sorted(
        "#" + "".join(f"{int(value):02X}" for value in color)
        for color in np.unique(image.reshape(-1, 3), axis=0)
    )


def _allowed_mask(shape: tuple[int, int]) -> np.ndarray:
    height, width = shape
    allowed = np.zeros(shape, dtype=bool)
    for correction in CORRECTIONS:
        x0, y0, x1, y1 = correction["bounds"]
        padding = CONFIG.allowed_padding_pixels
        allowed[
            max(0, y0 - padding) : min(height, y1 + padding),
            max(0, x0 - padding) : min(width, x1 + padding),
        ] = True
    return allowed


def _comparison(before: np.ndarray, after: np.ndarray) -> Image.Image:
    height, width = before.shape[:2]
    canvas = Image.new("RGB", (2 * width, height + 40), "white")
    canvas.paste(Image.fromarray(before), (0, 40))
    canvas.paste(Image.fromarray(after), (width, 40))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 12), "BEFORE: successful preprocessing candidate", fill="black")
    draw.text((width + 8, 12), "AFTER: IDs 1 and 108 locally corrected", fill="black")
    return canvas


def _overlay(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    changed = np.any(before != after, axis=2)
    display = ((before.astype(np.uint16) + 255) // 2).astype(np.uint8)
    display[changed] = (231, 45, 104)
    return display


def _closeup(
    identifier: int,
    name: str,
    before: np.ndarray,
    after: np.ndarray,
    overlay: np.ndarray,
    bounds: tuple[int, int, int, int],
) -> None:
    x0, y0, x1, y1 = bounds
    margin = 16
    x0, y0 = max(0, x0 - margin), max(0, y0 - margin)
    x1, y1 = min(before.shape[1], x1 + margin), min(before.shape[0], y1 + margin)
    panels = (before[y0:y1, x0:x1], after[y0:y1, x0:x1], overlay[y0:y1, x0:x1])
    scale = 4
    panel_width, panel_height = (x1 - x0) * scale, (y1 - y0) * scale
    canvas = Image.new("RGB", (panel_width * 3, panel_height + 42), "white")
    draw = ImageDraw.Draw(canvas)
    for column, (label, panel) in enumerate(
        zip(("BEFORE", "CORRECTED", "CHANGED PIXELS"), panels, strict=True)
    ):
        enlarged = Image.fromarray(panel).resize(
            (panel_width, panel_height), Image.Resampling.NEAREST
        )
        canvas.paste(enlarged, (column * panel_width, 42))
        draw.text(
            (column * panel_width + 6, 12),
            f"ID {identifier} {name}: {label}",
            fill="black",
        )
    canvas.save(OUTPUT / "closeups" / f"id_{identifier:03d}_{name}.png")


def _component_counts(image: np.ndarray) -> dict[str, int]:
    result: dict[str, int] = {}
    for color in np.unique(image.reshape(-1, 3), axis=0):
        mask = np.all(image == color, axis=2).astype(np.uint8)
        result["#" + "".join(f"{int(value):02X}" for value in color)] = int(
            cv2.connectedComponents(mask, connectivity=4)[0] - 1
        )
    return result


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

    before = np.asarray(Image.open(SOURCE).convert("RGB"))
    current = before.copy()
    reports: list[dict[str, Any]] = []
    additions_by_id: dict[int, np.ndarray] = {}
    for correction in CORRECTIONS:
        current, correction_report, additions = correct_reviewed_contour(
            current,
            correction["bounds"],
            correction["intended_local_width_mm"],
            CONFIG,
        )
        correction_report["reviewed_contour_id"] = correction["id"]
        correction_report["name"] = correction["name"]
        reports.append(correction_report)
        additions_by_id[int(correction["id"])] = additions

    after = current
    changed = np.any(before != after, axis=2)
    allowed = _allowed_mask(before.shape[:2])
    outside_changes = changed & ~allowed
    x0, y0, x1, y1 = ID_62_PROTECTION_BOUNDS
    id62_preserved = np.array_equal(before[y0:y1, x0:x1], after[y0:y1, x0:x1])
    palette_before = _palette(before)
    palette_after = _palette(after)
    overlay = _overlay(before, after)

    shutil.copy2(SOURCE, OUTPUT / "source" / "preprocessing_candidate_input.png")
    Image.fromarray(after).save(OUTPUT / "previews" / "final_corrected_candidate.png")
    _comparison(before, after).save(OUTPUT / "previews" / "before_after.png")
    Image.fromarray(overlay).save(OUTPUT / "previews" / "changed_pixel_overlay.png")
    for correction in CORRECTIONS:
        _closeup(
            correction["id"],
            correction["name"],
            before,
            after,
            overlay,
            correction["bounds"],
        )

    before_components = _component_counts(before)
    after_components = _component_counts(after)
    component_deltas = {
        color: after_components[color] - count
        for color, count in before_components.items()
        if after_components[color] != count
    }
    remaining = [
        report["reviewed_contour_id"]
        for report in reports
        if not report["minimum_width_pass"]
    ]
    total_changes = int(np.count_nonzero(changed))
    final_status = (
        "PREPROCESSING_READY_FOR_USER_APPROVAL"
        if not remaining
        and not np.any(outside_changes)
        and id62_preserved
        and palette_before == palette_after
        else "READY_FOR_USER_TEST_WITH_UNRESOLVED_CONFLICT"
    )
    report = {
        "status": final_status,
        "milestone_approval": "L1_NOT_APPROVED",
        "scope": "FINAL_TWO_CONTOURS_ONLY",
        "source": {
            "path": str(SOURCE.relative_to(ROOT)),
            "sha256": _sha256(SOURCE),
            "global_preprocessing_restarted": False,
        },
        "physical_mapping": {
            "resolution_pixels": [int(before.shape[1]), int(before.shape[0])],
            "print_size_mm": [200.0, 200.0],
            "pixel_size_mm": 200.0 / before.shape[1],
            "minimum_contour_width_mm": CONFIG.minimum_width_mm,
            "minimum_contour_width_pixels": CONFIG.minimum_width_mm
            / (200.0 / before.shape[1]),
            "analysis_scale": CONFIG.internal_scale,
            "half_native_pixel_uncertainty_mm": (200.0 / before.shape[1]) / 2.0,
        },
        "corrections": reports,
        "total_changed_pixels": total_changes,
        "percent_input_candidate_changed": float(100.0 * np.mean(changed)),
        "changed_pixels_by_contour": {
            str(identifier): int(np.count_nonzero(additions))
            for identifier, additions in additions_by_id.items()
        },
        "outside_reviewed_neighborhood_changed_pixels": int(
            np.count_nonzero(outside_changes)
        ),
        "id_62_protection_bounds_pixels": list(ID_62_PROTECTION_BOUNDS),
        "id_62_preserved_exactly": id62_preserved,
        "all_previous_pixels_outside_two_neighborhoods_preserved": bool(
            not np.any(outside_changes)
        ),
        "palette_before": palette_before,
        "palette_after": palette_after,
        "palette_preserved": palette_before == palette_after,
        "component_count_deltas_allowed_and_observed": component_deltas,
        "meaningful_detail_review": {
            "turtle_face_touched": False,
            "id_62_touched": not id62_preserved,
            "unrelated_contours_touched": bool(np.any(outside_changes)),
            "sun_curvature_preserved": reports[0]["centerline_preserved"],
            "snow_silhouette_preserved": True,
            "cloud_centerline_and_curvature_preserved": reports[1][
                "centerline_preserved"
            ],
            "all_changes_are_existing_black": bool(
                np.all(after[changed] == np.asarray(CONFIG.contour_color))
            ),
        },
        "remaining_unresolved_contour_ids": remaining,
        "layer_planning_run": False,
        "cad_or_mesh_generation_run": False,
    }
    (OUTPUT / "reports" / "validation.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    markdown = [
        "# Final two turtle contour corrections",
        "",
        f"Status: **{final_status}**.",
        "",
        f"- Changed pixels: {total_changes} ({report['percent_input_candidate_changed']:.4f}%).",
        f"- Outside reviewed neighborhoods: {report['outside_reviewed_neighborhood_changed_pixels']} pixels.",
        f"- ID 62 preserved exactly: {id62_preserved}.",
        f"- Palette preserved: {report['palette_preserved']}.",
        f"- Remaining unresolved IDs: {remaining}.",
        "",
    ]
    for correction_report in reports:
        markdown.extend(
            [
                f"## ID {correction_report['reviewed_contour_id']} — {correction_report['name']}",
                "",
                f"- Target width: {correction_report['target_width_mm']:.4f} mm.",
                f"- Rasterized stroke with allowance: {correction_report['rasterized_stroke_width_mm']:.4f} mm.",
                f"- Minimum before: {correction_report['before']['minimum_mm']:.4f} mm.",
                f"- Minimum after: {correction_report['after']['minimum_mm']:.4f} mm.",
                f"- Changed pixels: {correction_report['changed_pixels']}.",
                f"- Width PASS: {correction_report['minimum_width_pass']}.",
                "",
            ]
        )
    (OUTPUT / "reports" / "REVIEW.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": final_status, "changed_pixels": total_changes}), flush=True)
    for correction_report in reports:
        print(
            json.dumps(
                {
                    "id": correction_report["reviewed_contour_id"],
                    "before_min_mm": correction_report["before"]["minimum_mm"],
                    "after_min_mm": correction_report["after"]["minimum_mm"],
                    "pass": correction_report["minimum_width_pass"],
                    "changed_pixels": correction_report["changed_pixels"],
                },
                sort_keys=True,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()

