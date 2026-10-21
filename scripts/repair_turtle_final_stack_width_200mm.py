"""Widen sub-1.2 mm retained connections in the saved final turtle stack.

This is a post-planning operation on the actual physical masks.  The artwork
aperture is 200 mm square; the existing 32 px shared perimeter therefore makes
the 576 px outer sheet 225 mm square.  The script first uses material hidden by
more-forward sheets, then permits the smallest remaining visible additions.
It never changes layer order, palette, or cutout polarity.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from layerforge_l1.hidden_width_repair import (
    WidthRepairConfig,
    audit_mask_at_physical_scale,
    hidden_addition_domain,
    repair_one_mask,
)
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG


ROOT = Path(__file__).resolve().parents[1]
INPUT = (
    ROOT
    / "artifacts"
    / "l1_turtle_final_global_planning_200mm_v1"
    / "rejected_best_candidate"
)
TARGET = (
    ROOT
    / "artifacts"
    / "l1_turtle_final_two_contours_200mm_v1"
    / "previews"
    / "final_corrected_candidate.png"
)
OUTPUT = ROOT / "artifacts" / "l1_turtle_post_layer_width_repair_200mm_v1"

# 512 px artwork = 200 mm.  The actual physical masks are 576 px because they
# include a 32 px perimeter on every side, so their outer size is 225 mm.
ARTWORK_SIZE_MM = 200.0
OUTER_SHEET_SIZE_MM = 225.0
MINIMUM_RETAINED_WIDTH_MM = 1.2
REPAIR = WidthRepairConfig(
    physical_size_mm=OUTER_SHEET_SIZE_MM,
    validation_scale=4,
    # A dense cumulative black sheet needs more than eight sequential neck
    # repairs.  Sixteen is still bounded and lets the deterministic repair run
    # to PASS instead of stopping with reachable orphan cores outstanding.
    max_iterations=16,
)


def _load_mask(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L")) > 0


def _save_mask(mask: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255, "L").save(path)


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _compose(masks: list[np.ndarray], colors: list[str]) -> np.ndarray:
    image = np.full((*masks[0].shape, 3), 247, dtype=np.uint8)
    for mask, color in zip(masks, colors, strict=True):
        image[mask] = _rgb(color)
    p = DEFAULT_CONFIG.perimeter_pixels
    return image[p:-p, p:-p]


def _sheet_preview(mask: np.ndarray, color: str, path: Path) -> None:
    image = np.full((*mask.shape, 3), 250, dtype=np.uint8)
    image[mask] = _rgb(color)
    Image.fromarray(image, "RGB").save(path)


def _change_overlay(before: np.ndarray, after: np.ndarray, path: Path) -> int:
    changed = np.any(before != after, axis=2)
    image = before.copy()
    image[~changed] = (244, 244, 244)
    image[changed] = (230, 45, 52)
    Image.fromarray(image, "RGB").save(path)
    return int(np.count_nonzero(changed))


def _repair_overlay(
    before: np.ndarray,
    hidden_added: np.ndarray,
    visible_added: np.ndarray,
    path: Path,
) -> None:
    image = np.full((*before.shape, 3), 248, dtype=np.uint8)
    image[before] = (70, 83, 95)
    image[hidden_added] = (47, 170, 92)
    image[visible_added] = (230, 65, 52)
    Image.fromarray(image, "RGB").save(path)


def _exploded(masks: list[np.ndarray], colors: list[str], path: Path) -> None:
    thumb = 240
    margin = 18
    label_h = 28
    columns = 3
    rows = (len(masks) + columns - 1) // columns
    canvas = Image.new(
        "RGB",
        (columns * (thumb + margin) + margin, rows * (thumb + label_h + margin) + margin),
        "white",
    )
    draw = ImageDraw.Draw(canvas)
    for display, index in enumerate(reversed(range(len(masks)))):
        row, column = divmod(display, columns)
        x = margin + column * (thumb + margin)
        y = margin + row * (thumb + label_h + margin)
        preview = np.full((*masks[index].shape, 3), 250, dtype=np.uint8)
        preview[masks[index]] = _rgb(colors[index])
        tile = Image.fromarray(preview, "RGB").resize(
            (thumb, thumb), Image.Resampling.NEAREST
        )
        canvas.paste(tile, (x, y + label_h))
        draw.text(
            (x, y),
            f"front depth {len(masks) - 1 - index:02d} | layer {index:02d} | {colors[index]}",
            fill="black",
        )
    canvas.save(path)


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _pool_any(mask: np.ndarray, scale: int) -> np.ndarray:
    return mask.reshape(
        mask.shape[0] // scale,
        scale,
        mask.shape[1] // scale,
        scale,
    ).any(axis=(1, 3))


def main() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    (OUTPUT / "layers").mkdir(parents=True)
    (OUTPUT / "previews").mkdir(parents=True)
    (OUTPUT / "reports").mkdir(parents=True)
    (OUTPUT / "source").mkdir(parents=True)
    shutil.copy2(TARGET, OUTPUT / "source" / TARGET.name)

    source_report = json.loads(
        (INPUT / "rejected_validation_report.json").read_text(encoding="utf-8")
    )
    source_layers = sorted(
        source_report["layers"],
        key=lambda row: int(row["physical_layer_index_back_to_front"]),
    )
    layer_root = INPUT / "automatic" / "layers"
    masks: list[np.ndarray] = []
    visible: list[np.ndarray] = []
    colors: list[str] = []
    layer_ids: list[str] = []
    for row in source_layers:
        index = int(row["physical_layer_index_back_to_front"])
        directory = next(layer_root.glob(f"layer_{index:02d}_*"))
        masks.append(_load_mask(directory / "retained_material_mask.png"))
        visible.append(_load_mask(directory / "source_visible_region_mask.png"))
        colors.append(str(row["color"]))
        layer_ids.append(directory.name)

    target = np.asarray(Image.open(TARGET).convert("RGB"))
    before_composite = _compose(masks, colors)
    if not np.array_equal(before_composite, target):
        raise RuntimeError("saved physical masks do not reconstruct the locked target")

    repaired = [mask.copy() for mask in masks]
    per_layer_work: dict[int, dict[str, Any]] = {}
    # Work from the front sheet toward the back so newly widened foreground
    # material becomes valid hiding capacity for sheets behind it.
    for index in reversed(range(len(repaired))):
        print(f"repair layer {index:02d} {colors[index]}", flush=True)
        original = repaired[index].copy()
        before, _ = audit_mask_at_physical_scale(
            original, visible[index], repair=REPAIR
        )
        safe_domain = hidden_addition_domain(repaired, index)
        hidden_mask, hidden_result, _ = repair_one_mask(
            original,
            visible[index],
            safe_domain,
            repair=REPAIR,
        )
        final_mask = hidden_mask
        visible_result: dict[str, Any] | None = None
        if hidden_result["status"] != "PASS":
            final_mask, visible_result, _ = repair_one_mask(
                hidden_mask,
                visible[index],
                ~hidden_mask,
                repair=REPAIR,
            )
        # The structural validator deliberately reports terminal decorative
        # strokes separately.  The user's post-layer rule applies to these
        # physical lines as well.  Widen only the reported components: at the
        # native 0.390625 mm grid, a 3 px stroke is 1.171875 mm, so the next
        # symmetric discrete footprint is a 5 px ellipse (1.953125 mm).
        decorative_audit, decorative_maps = audit_mask_at_physical_scale(
            final_mask, visible[index], repair=REPAIR
        )
        decorative_added = np.zeros_like(final_mask)
        if decorative_audit["width"]["thin_decorative_visible_components"]:
            thin_native = _pool_any(
                decorative_maps["thin_decorative_visible"],
                REPAIR.validation_scale,
            )
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            widened = cv2.dilate(
                thin_native.astype(np.uint8), kernel, iterations=1
            ).astype(bool)
            decorative_added = widened & ~final_mask
            final_mask |= decorative_added
        repaired[index] = final_mask
        per_layer_work[index] = {
            "before": before,
            "hidden_result": hidden_result,
            "visible_result": visible_result,
            "hidden_added": hidden_mask & ~original,
            "visible_added": final_mask & ~hidden_mask,
            "decorative_added": decorative_added,
            "decorative_warnings_before": decorative_audit["width"][
                "thin_decorative_visible_components"
            ],
        }

    layer_rows: list[dict[str, Any]] = []
    p = DEFAULT_CONFIG.perimeter_pixels
    perimeter = np.ones_like(repaired[0], dtype=bool)
    perimeter[p:-p, p:-p] = False
    all_valid = True
    for index, final_mask in enumerate(repaired):
        audit, _ = audit_mask_at_physical_scale(
            final_mask, visible[index], repair=REPAIR
        )
        layer_dir = OUTPUT / "layers" / layer_ids[index]
        layer_dir.mkdir(parents=True)
        _save_mask(masks[index], layer_dir / "before_retained_material_mask.png")
        _save_mask(final_mask, layer_dir / "retained_material_mask.png")
        _save_mask(~final_mask, layer_dir / "through_cutouts_mask.png")
        _save_mask(
            per_layer_work[index]["hidden_added"],
            layer_dir / "hidden_width_additions.png",
        )
        _save_mask(
            per_layer_work[index]["visible_added"],
            layer_dir / "visible_width_additions.png",
        )
        _save_mask(
            per_layer_work[index]["decorative_added"],
            layer_dir / "decorative_line_width_additions.png",
        )
        _sheet_preview(final_mask, colors[index], layer_dir / "physical_sheet_preview.png")
        _repair_overlay(
            masks[index],
            per_layer_work[index]["hidden_added"],
            per_layer_work[index]["visible_added"],
            layer_dir / "width_repair_overlay.png",
        )
        width = audit["width"]
        edge = audit["edge"]
        valid = (
            edge["four_neighbor_status"] == "PASS"
            and width["status"] == "PASS"
            and not np.any(perimeter & ~final_mask)
        )
        all_valid &= valid
        before_width = per_layer_work[index]["before"]["width"]
        layer_rows.append(
            {
                "index_back_to_front": index,
                "front_to_back_depth": len(repaired) - 1 - index,
                "id": layer_ids[index],
                "color": colors[index],
                "before_width_status": before_width["status"],
                "before_orphan_cores": before_width[
                    "orphan_structural_core_components"
                ],
                "after_width_status": width["status"],
                "after_orphan_cores": width["orphan_structural_core_components"],
                "four_neighbor_status": edge["four_neighbor_status"],
                "missing_perimeter_pixels": int(np.count_nonzero(perimeter & ~final_mask)),
                "hidden_added_pixels": int(
                    np.count_nonzero(per_layer_work[index]["hidden_added"])
                ),
                "visible_added_pixels": int(
                    np.count_nonzero(per_layer_work[index]["visible_added"])
                ),
                "hidden_repair_iterations": len(
                    per_layer_work[index]["hidden_result"]["iterations"]
                ),
                "visible_repair_iterations": len(
                    (per_layer_work[index]["visible_result"] or {}).get(
                        "iterations", []
                    )
                ),
                "thin_decorative_visible_component_warnings": width[
                    "thin_decorative_visible_components"
                ],
                "thin_decorative_warnings_before_targeted_widening": per_layer_work[
                    index
                ]["decorative_warnings_before"],
                "decorative_line_added_pixels": int(
                    np.count_nonzero(per_layer_work[index]["decorative_added"])
                ),
                "minimum_width_mm": MINIMUM_RETAINED_WIDTH_MM,
                "validation_kernel_pixels_at_4x": width[
                    "morphology_kernel_size_pixels"
                ],
                "validation_mm_per_sample": width["mm_per_pixel"],
                "valid": valid,
            }
        )

    repaired_composite = _compose(repaired, colors)
    Image.fromarray(target, "RGB").save(OUTPUT / "previews" / "locked_target.png")
    Image.fromarray(before_composite, "RGB").save(
        OUTPUT / "previews" / "before_stack.png"
    )
    Image.fromarray(repaired_composite, "RGB").save(
        OUTPUT / "previews" / "repaired_stack.png"
    )
    changed_pixels = _change_overlay(
        target,
        repaired_composite,
        OUTPUT / "previews" / "visible_change_overlay.png",
    )
    _exploded(repaired, colors, OUTPUT / "previews" / "exploded_layers.png")

    report = {
        "status": "READY_FOR_USER_TEST" if all_valid else "REPAIR_INCOMPLETE",
        "source_stack_status": "REJECTED_STRUCTURAL_CANDIDATE",
        "physical_layer_count": len(repaired),
        "colors_back_to_front": colors,
        "scale": {
            "artwork_pixels": [512, 512],
            "artwork_mm": [ARTWORK_SIZE_MM, ARTWORK_SIZE_MM],
            "outer_mask_pixels": [576, 576],
            "outer_sheet_mm": [OUTER_SHEET_SIZE_MM, OUTER_SHEET_SIZE_MM],
            "native_mm_per_pixel": ARTWORK_SIZE_MM / 512.0,
            "validation_scale": REPAIR.validation_scale,
            "minimum_retained_width_mm": MINIMUM_RETAINED_WIDTH_MM,
        },
        "validation": {
            "all_sheets_four_neighbor_connected": all(
                row["four_neighbor_status"] == "PASS" for row in layer_rows
            ),
            "all_sheets_pass_retained_width": all(
                row["after_width_status"] == "PASS" for row in layer_rows
            ),
            "all_sheets_preserve_shared_perimeter": all(
                row["missing_perimeter_pixels"] == 0 for row in layer_rows
            ),
            "remaining_orphan_cores": sum(
                int(row["after_orphan_cores"]) for row in layer_rows
            ),
            "thin_decorative_visible_component_warnings": sum(
                int(row["thin_decorative_visible_component_warnings"])
                for row in layer_rows
            ),
        },
        "visible_result": {
            "changed_pixels": changed_pixels,
            "changed_percent": changed_pixels * 100.0 / (512 * 512),
            "exact_original_reconstruction": changed_pixels == 0,
            "authorization_basis": (
                "User requested post-layer widening of retained lines below 1.2 mm."
            ),
        },
        "repair": {
            "hidden_added_pixels": sum(row["hidden_added_pixels"] for row in layer_rows),
            "visible_added_pixels": sum(row["visible_added_pixels"] for row in layer_rows),
            "method": (
                "Follow existing retained paths from perimeter-supported cores to orphan "
                "cores; widen hidden portions first, then the minimum remaining visible "
                "portions. No global dilation and no layer-order or palette change."
            ),
        },
        "layers": layer_rows,
        "limitations": [
            "The unchanged validator certifies load-bearing retained connections. Thin terminal decorative components are reported separately.",
            "The widened composite is a review candidate because visible additions change the locked source artwork.",
            "No CAD/STL/STEP or physical print validation was performed.",
        ],
    }
    _json(OUTPUT / "reports" / "width_repair_report.json", report)
    lines = [
        "# Turtle post-layer retained-width repair",
        "",
        f"Status: {report['status']}",
        f"Artwork: 200 x 200 mm; outer sheet: 225 x 225 mm; minimum retained width: 1.2 mm.",
        f"Changed visible pixels: {changed_pixels} ({report['visible_result']['changed_percent']:.4f}%).",
        f"Hidden additions: {report['repair']['hidden_added_pixels']} px; visible additions: {report['repair']['visible_added_pixels']} px.",
        f"Remaining orphan cores: {report['validation']['remaining_orphan_cores']}.",
        f"Thin decorative warnings: {report['validation']['thin_decorative_visible_component_warnings']}.",
        "",
        "| Layer back-to-front | Color | Before width | Before orphans | Hidden px | Visible px | After width | 4-neighbor |",
        "|---:|---|---|---:|---:|---:|---|---|",
    ]
    for row in layer_rows:
        lines.append(
            f"| {row['index_back_to_front']} | {row['color']} | {row['before_width_status']} | "
            f"{row['before_orphan_cores']} | {row['hidden_added_pixels']} | "
            f"{row['visible_added_pixels']} | {row['after_width_status']} | "
            f"{row['four_neighbor_status']} |"
        )
    (OUTPUT / "reports" / "REVIEW_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": report["status"],
        "changed_pixels": changed_pixels,
        "validation": report["validation"],
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
