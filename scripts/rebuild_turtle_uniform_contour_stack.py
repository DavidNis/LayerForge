"""Rebuild the turtle stack from uniformly re-stroked visible contours.

The pre-repair masks are the only physical source.  Visible black additions are
assigned to the nearest existing black provider depth, propagated through the
cumulative masks behind that provider, and cut out of every sheet in front.
No structural-neck dilation is used.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from layerforge_l1.contour_consistency import (
    ContourConsistencyConfig,
    normalize_contour_consistency,
)
from layerforge_l1.hidden_width_repair import (
    WidthRepairConfig,
    audit_mask_at_physical_scale,
)
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG


ROOT = Path(__file__).resolve().parents[1]
SOURCE_STACK = (
    ROOT
    / "artifacts"
    / "l1_turtle_final_global_planning_200mm_v1"
    / "rejected_best_candidate"
)
SOURCE_TARGET = (
    ROOT
    / "artifacts"
    / "l1_turtle_final_two_contours_200mm_v1"
    / "previews"
    / "final_corrected_candidate.png"
)
BLOBBED_PREVIEW = (
    ROOT
    / "artifacts"
    / "l1_turtle_post_layer_width_repair_200mm_v1"
    / "previews"
    / "repaired_stack.png"
)
OUTPUT = ROOT / "artifacts" / "l1_turtle_uniform_contour_stack_200mm_v1"
CONTOUR = ContourConsistencyConfig()
NAVY_CONTOUR = ContourConsistencyConfig(contour_color=(23, 51, 75))
WIDTH = WidthRepairConfig(
    physical_size_mm=225.0,
    validation_scale=4,
    max_iterations=16,
)


def _mask(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L")) > 0


def _save_mask(mask: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255, "L").save(path)


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _compose(masks: list[np.ndarray], colors: list[str]) -> tuple[np.ndarray, np.ndarray]:
    providers = np.full((512, 512), -1, dtype=np.int16)
    image = np.full((576, 576, 3), 247, dtype=np.uint8)
    p = DEFAULT_CONFIG.perimeter_pixels
    for index, (mask, color) in enumerate(zip(masks, colors, strict=True)):
        image[mask] = _rgb(color)
        providers[mask[p:-p, p:-p]] = index
    return image[p:-p, p:-p], providers


def _provider_assignment(
    original_color: np.ndarray,
    providers: np.ndarray,
    additions: np.ndarray,
    black_depths: list[int],
) -> np.ndarray:
    distances: list[np.ndarray] = []
    for depth in black_depths:
        supplied = original_color & (providers == depth)
        if not np.any(supplied):
            raise RuntimeError(f"black provider depth {depth} supplies no source pixels")
        distances.append(
            cv2.distanceTransform((~supplied).astype(np.uint8), cv2.DIST_L2, 5)
        )
    stacked = np.stack(distances)
    nearest = np.argmin(stacked, axis=0)
    assignment = np.full(original_color.shape, -1, dtype=np.int16)
    for position, depth in enumerate(black_depths):
        assignment[additions & (nearest == position)] = depth
    return assignment


def _tiny_contour_gap_cleanup(
    source: np.ndarray,
    candidate: np.ndarray,
    contour_colors: set[tuple[int, int, int]],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Replace only tiny fragments overwhelmingly enclosed by one contour color."""

    result = candidate.copy()
    decisions: list[dict[str, Any]] = []
    for color_array in np.unique(candidate.reshape(-1, 3), axis=0):
        color = tuple(int(value) for value in color_array)
        if color in contour_colors:
            continue
        mask = np.all(result == color_array, axis=2).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
        for component in range(1, count):
            area = int(stats[component, cv2.CC_STAT_AREA])
            width = int(stats[component, cv2.CC_STAT_WIDTH])
            height = int(stats[component, cv2.CC_STAT_HEIGHT])
            if area > 16 or max(width, height) > 4:
                continue
            component_mask = labels == component
            ring = cv2.dilate(component_mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
            ring &= ~component_mask
            values, counts = np.unique(result[ring], axis=0, return_counts=True)
            ranked = sorted(
                ((tuple(int(v) for v in value), int(n)) for value, n in zip(values, counts, strict=True)),
                key=lambda item: (-item[1], item[0]),
            )
            if not ranked or ranked[0][0] not in contour_colors:
                continue
            fraction = ranked[0][1] / max(1, sum(n for _, n in ranked))
            # A 3x1 boundary remnant can have five contour neighbours and four
            # pixels from the adjacent fill.  That 5/9 majority is sufficient
            # when the component is already constrained to <=16 px and <=4 px
            # extent.
            if fraction < 0.55:
                continue
            replacement = ranked[0][0]
            result[component_mask] = replacement
            x = int(stats[component, cv2.CC_STAT_LEFT])
            y = int(stats[component, cv2.CC_STAT_TOP])
            decisions.append(
                {
                    "original_color": "#" + "".join(f"{v:02X}" for v in color),
                    "replacement_color": "#" + "".join(f"{v:02X}" for v in replacement),
                    "area_pixels": area,
                    "bounds_pixels": [x, y, width, height],
                    "dominant_contour_neighbor_fraction": fraction,
                    "source_pixels_changed": int(
                        np.count_nonzero(np.any(source[component_mask] != replacement, axis=1))
                    ),
                }
            )
    return result, decisions


def _sheet_preview(mask: np.ndarray, color: str, path: Path) -> None:
    image = np.full((*mask.shape, 3), 250, dtype=np.uint8)
    image[mask] = _rgb(color)
    Image.fromarray(image, "RGB").save(path)


def _comparison(images: list[np.ndarray], labels: list[str], path: Path) -> None:
    canvas = Image.new("RGB", (512 * len(images), 552), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (image, label) in enumerate(zip(images, labels, strict=True)):
        canvas.paste(Image.fromarray(image, "RGB"), (index * 512, 40))
        draw.text((index * 512 + 8, 12), label, fill="black")
    canvas.save(path)


def _overlay(before: np.ndarray, after: np.ndarray, path: Path) -> int:
    changed = np.any(before != after, axis=2)
    image = ((before.astype(np.uint16) + 255) // 2).astype(np.uint8)
    image[changed] = (235, 32, 90)
    Image.fromarray(image, "RGB").save(path)
    return int(np.count_nonzero(changed))


def _exploded(masks: list[np.ndarray], colors: list[str], path: Path) -> None:
    thumb, margin, label = 240, 18, 28
    columns = 3
    rows = (len(masks) + columns - 1) // columns
    canvas = Image.new(
        "RGB",
        (columns * (thumb + margin) + margin, rows * (thumb + label + margin) + margin),
        "white",
    )
    draw = ImageDraw.Draw(canvas)
    for display, index in enumerate(reversed(range(len(masks)))):
        row, column = divmod(display, columns)
        x = margin + column * (thumb + margin)
        y = margin + row * (thumb + label + margin)
        preview = np.full((*masks[index].shape, 3), 250, dtype=np.uint8)
        preview[masks[index]] = _rgb(colors[index])
        tile = Image.fromarray(preview, "RGB").resize(
            (thumb, thumb), Image.Resampling.NEAREST
        )
        canvas.paste(tile, (x, y + label))
        draw.text(
            (x, y),
            f"front {len(masks)-1-index:02d} | layer {index:02d} | {colors[index]}",
            fill="black",
        )
    canvas.save(path)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    for name in ("source", "layers", "previews", "reports"):
        (OUTPUT / name).mkdir(parents=True)

    source_report = json.loads(
        (SOURCE_STACK / "rejected_validation_report.json").read_text(encoding="utf-8")
    )
    rows = sorted(
        source_report["layers"],
        key=lambda row: int(row["physical_layer_index_back_to_front"]),
    )
    masks: list[np.ndarray] = []
    visible: list[np.ndarray] = []
    ids: list[str] = []
    colors: list[str] = []
    layer_root = SOURCE_STACK / "automatic" / "layers"
    for row in rows:
        index = int(row["physical_layer_index_back_to_front"])
        directory = next(layer_root.glob(f"layer_{index:02d}_*"))
        masks.append(_mask(directory / "retained_material_mask.png"))
        visible.append(_mask(directory / "source_visible_region_mask.png"))
        ids.append(directory.name)
        colors.append(str(row["color"]))

    source = np.asarray(Image.open(SOURCE_TARGET).convert("RGB"))
    original_composite, providers = _compose(masks, colors)
    if not np.array_equal(source, original_composite):
        raise RuntimeError("pre-repair masks do not reconstruct their source target")

    black_candidate, black_report, black_add = normalize_contour_consistency(
        source, CONTOUR
    )
    navy_candidate, navy_report, navy_add = normalize_contour_consistency(
        source, NAVY_CONTOUR
    )
    # Combine additions without allowing one contour color to overwrite an
    # existing pixel of the other.  Rare addition/addition conflicts go to the
    # nearest original centerline color.
    original_black = np.all(source == np.asarray(CONTOUR.contour_color), axis=2)
    original_navy = np.all(source == np.asarray(NAVY_CONTOUR.contour_color), axis=2)
    black_add &= ~original_navy
    navy_add &= ~original_black
    conflict = black_add & navy_add
    if np.any(conflict):
        black_distance = cv2.distanceTransform((~original_black).astype(np.uint8), cv2.DIST_L2, 5)
        navy_distance = cv2.distanceTransform((~original_navy).astype(np.uint8), cv2.DIST_L2, 5)
        black_wins = conflict & (black_distance <= navy_distance)
        navy_wins = conflict & ~black_wins
        black_add &= ~conflict
        navy_add &= ~conflict
        black_add |= black_wins
        navy_add |= navy_wins
    normalized = source.copy()
    normalized[black_add] = CONTOUR.contour_color
    normalized[navy_add] = NAVY_CONTOUR.contour_color
    normalized, cleanup_decisions = _tiny_contour_gap_cleanup(
        source,
        normalized,
        {CONTOUR.contour_color, NAVY_CONTOUR.contour_color},
    )

    contour_runs = [
        ("#0E0F0D", CONTOUR.contour_color, black_report),
        ("#17334B", NAVY_CONTOUR.contour_color, navy_report),
    ]

    rebuilt = [mask.copy() for mask in masks]
    p = DEFAULT_CONFIG.perimeter_pixels
    propagation: list[dict[str, Any]] = []
    for color_hex, color_tuple, _ in contour_runs:
        original_color = np.all(source == np.asarray(color_tuple), axis=2)
        additions = np.all(normalized == np.asarray(color_tuple), axis=2) & ~original_color
        depths = [index for index, value in enumerate(colors) if value == color_hex]
        assignment = _provider_assignment(
            original_color,
            providers,
            additions,
            depths,
        )
        for depth in depths:
            local = assignment == depth
            outer = np.zeros_like(rebuilt[0])
            outer[p:-p, p:-p] = local
            for index in range(len(rebuilt)):
                if index <= depth:
                    rebuilt[index] |= outer
                else:
                    rebuilt[index] &= ~outer
            propagation.append(
                {
                    "provider_depth_back_to_front": depth,
                    "color": colors[depth],
                    "added_visible_contour_pixels": int(np.count_nonzero(local)),
                    "propagated_to_cumulative_layers": list(range(depth + 1)),
                    "cut_from_foreground_layers": list(range(depth + 1, len(rebuilt))),
                }
            )

    rebuilt_composite, rebuilt_providers = _compose(rebuilt, colors)
    mismatch = int(np.count_nonzero(np.any(rebuilt_composite != normalized, axis=2)))
    if mismatch:
        raise RuntimeError(f"rebuilt masks miss normalized target at {mismatch} pixels")

    perimeter = np.ones_like(rebuilt[0])
    perimeter[p:-p, p:-p] = False
    layer_reports: list[dict[str, Any]] = []
    valid = True
    for index, mask in enumerate(rebuilt):
        visible_now = rebuilt_providers == index
        audit, _ = audit_mask_at_physical_scale(mask, visible_now, repair=WIDTH)
        edge = audit["edge"]
        width = audit["width"]
        missing = int(np.count_nonzero(perimeter & ~mask))
        layer_valid = (
            edge["four_neighbor_status"] == "PASS"
            and width["status"] == "PASS"
            and width["thin_decorative_visible_components"] == 0
            and missing == 0
        )
        valid &= layer_valid
        directory = OUTPUT / "layers" / ids[index]
        directory.mkdir(parents=True)
        _save_mask(mask, directory / "retained_material_mask.png")
        _save_mask(~mask, directory / "through_cutouts_mask.png")
        _save_mask(visible_now, directory / "source_visible_region_mask.png")
        _save_mask(mask ^ masks[index], directory / "changed_mask_pixels.png")
        _sheet_preview(mask, colors[index], directory / "physical_sheet_preview.png")
        layer_reports.append(
            {
                "index_back_to_front": index,
                "front_to_back_depth": len(rebuilt) - 1 - index,
                "id": ids[index],
                "color": colors[index],
                "four_neighbor_status": edge["four_neighbor_status"],
                "width_status": width["status"],
                "orphan_structural_core_components": width[
                    "orphan_structural_core_components"
                ],
                "thin_decorative_visible_components": width[
                    "thin_decorative_visible_components"
                ],
                "missing_perimeter_pixels": missing,
                "changed_mask_pixels": int(np.count_nonzero(mask ^ masks[index])),
                "valid": layer_valid,
            }
        )

    shutil.copy2(SOURCE_TARGET, OUTPUT / "source" / SOURCE_TARGET.name)
    Image.fromarray(source, "RGB").save(OUTPUT / "previews" / "approved_before.png")
    Image.fromarray(normalized, "RGB").save(
        OUTPUT / "previews" / "uniform_contour_target.png"
    )
    Image.fromarray(rebuilt_composite, "RGB").save(
        OUTPUT / "previews" / "assembled_from_actual_masks.png"
    )
    changed = _overlay(
        source,
        normalized,
        OUTPUT / "previews" / "changed_pixel_overlay.png",
    )
    compare_images = [source, normalized]
    compare_labels = ["APPROVED PRE-REPAIR", "UNIFORM-CONTOUR REBUILD"]
    if BLOBBED_PREVIEW.is_file():
        compare_images.append(np.asarray(Image.open(BLOBBED_PREVIEW).convert("RGB")))
        compare_labels.append("REJECTED BLOB REPAIR")
    _comparison(
        compare_images,
        compare_labels,
        OUTPUT / "previews" / "before_after_vs_rejected.png",
    )
    _exploded(rebuilt, colors, OUTPUT / "previews" / "exploded_layers.png")

    report = {
        "status": "READY_FOR_USER_TEST" if valid else "REJECTED_GEOMETRY",
        "accepted": False,
        "source": str(SOURCE_TARGET.relative_to(ROOT)),
        "source_masks": str(SOURCE_STACK.relative_to(ROOT)),
        "physical_layer_count": len(rebuilt),
        "colors_back_to_front": colors,
        "physical_mapping": {
            "artwork_pixels": [512, 512],
            "artwork_mm": [200.0, 200.0],
            "outer_mask_pixels": [576, 576],
            "outer_sheet_mm": [225.0, 225.0],
            "minimum_visible_and_retained_width_mm": 1.2,
            "validation_scale": 4,
        },
        "contour_normalization": {
            "black": black_report,
            "navy": navy_report,
            "tiny_gap_cleanup": cleanup_decisions,
            "total_paths_processed": black_report["paths_processed"]
            + navy_report["paths_processed"],
            "total_paths_with_added_pixels": black_report["paths_with_added_pixels"]
            + navy_report["paths_with_added_pixels"],
            "total_unresolved_paths": black_report["unresolved_paths"]
            + navy_report["unresolved_paths"],
        },
        "provider_propagation": propagation,
        "reconstruction": {
            "matches_uniform_contour_target": mismatch == 0,
            "mismatched_pixels": mismatch,
            "changed_from_approved_pixels": changed,
            "changed_from_approved_percent": changed * 100.0 / (512 * 512),
            "all_visual_changes_are_existing_contour_colors": bool(
                all(
                    tuple(int(value) for value in pixel)
                    in {CONTOUR.contour_color, NAVY_CONTOUR.contour_color}
                    for pixel in normalized[np.any(normalized != source, axis=2)]
                )
            ),
            "palette_before": sorted(
                "#" + "".join(f"{int(value):02X}" for value in color)
                for color in np.unique(source.reshape(-1, 3), axis=0)
            ),
            "palette_after": sorted(
                "#" + "".join(f"{int(value):02X}" for value in color)
                for color in np.unique(normalized.reshape(-1, 3), axis=0)
            ),
            "visible_connector_pixels": 0,
        },
        "validation": {
            "all_layers_valid": valid,
            "all_four_neighbor_connected": all(
                row["four_neighbor_status"] == "PASS" for row in layer_reports
            ),
            "all_retained_width_pass": all(
                row["width_status"] == "PASS" for row in layer_reports
            ),
            "total_orphan_cores": sum(
                row["orphan_structural_core_components"] for row in layer_reports
            ),
            "thin_visible_retained_components": sum(
                row["thin_decorative_visible_components"] for row in layer_reports
            ),
            "shared_perimeter_pass": all(
                row["missing_perimeter_pixels"] == 0 for row in layer_reports
            ),
        },
        "layers": layer_reports,
        "method": (
            "The complete visible dark centerline paths are re-stroked uniformly. "
            "New dark pixels are supplied by the nearest existing black depth, "
            "added to cumulative sheets behind it, and cut from sheets in front. "
            "No local structural-neck dilation or visible bridge is used."
        ),
        "limitations": [
            "The revised artwork requires user visual approval.",
            "This fixed stack is not proven layer-count minimal.",
            "No CAD, STL, STEP, or physical print validation was performed.",
        ],
    }
    _write_json(OUTPUT / "reports" / "uniform_contour_stack_report.json", report)
    lines = [
        "# Turtle uniform-contour physical stack",
        "",
        f"Status: {report['status']}",
        f"Changed visible pixels: {changed} ({report['reconstruction']['changed_from_approved_percent']:.4f}%).",
        f"Centerline paths processed: {report['contour_normalization']['total_paths_processed']}.",
        f"Paths changed: {report['contour_normalization']['total_paths_with_added_pixels']}.",
        f"Unresolved contour paths: {report['contour_normalization']['total_unresolved_paths']}.",
        f"Physical layers: {len(rebuilt)}.",
        f"Orphan cores: {report['validation']['total_orphan_cores']}.",
        f"Thin visible retained components: {report['validation']['thin_visible_retained_components']}.",
        "",
        "| Layer | Color | 4-neighbor | Retained width | Orphans | Thin visible | Mask changes |",
        "|---:|---|---|---|---:|---:|---:|",
    ]
    for row in layer_reports:
        lines.append(
            f"| {row['index_back_to_front']} | {row['color']} | {row['four_neighbor_status']} | "
            f"{row['width_status']} | {row['orphan_structural_core_components']} | "
            f"{row['thin_decorative_visible_components']} | {row['changed_mask_pixels']} |"
        )
    (OUTPUT / "reports" / "REVIEW.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": report["status"],
        "changed_pixels": changed,
        "contours": {
            "processed": report["contour_normalization"]["total_paths_processed"],
            "changed": report["contour_normalization"]["total_paths_with_added_pixels"],
            "unresolved": report["contour_normalization"]["total_unresolved_paths"],
        },
        "validation": report["validation"],
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
