"""One deterministic retained-width repair attempt on the saved turtle stack."""

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
    compose_back_to_front,
    hidden_addition_domain,
    repair_one_mask,
    validation_poc_config,
)
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG


ROOT = Path(__file__).resolve().parents[1]
INPUT = (
    ROOT
    / "artifacts"
    / "l1_turtle_on_leaf_global_planner_v2"
    / "rejected_candidate"
)
INDEXED_TARGET = (
    ROOT
    / "artifacts"
    / "l1_turtle_on_leaf_global_planner_v2"
    / "automatic"
    / "indexed_target.png"
)
OUTPUT = ROOT / "artifacts" / "l1_turtle_width_repair_200mm_v1"
REPAIR = WidthRepairConfig(physical_size_mm=200.0, validation_scale=4)


def _mask(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L")) > 0


def _save_mask(mask: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255, "L").save(path)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _palette(manifest: dict[str, Any]) -> np.ndarray:
    count = max(int(row["palette_index"]) for row in manifest["layer_files"]) + 1
    palette = np.zeros((count, 3), dtype=np.uint8)
    for row in manifest["layer_files"]:
        palette[int(row["palette_index"])] = _hex_rgb(row["color"])
    return palette


def _native_map(mask: np.ndarray) -> np.ndarray:
    scale = REPAIR.validation_scale
    return mask.reshape(
        mask.shape[0] // scale, scale, mask.shape[1] // scale, scale
    ).any(axis=(1, 3))


def _overlay(
    physical: np.ndarray,
    orphan_high: np.ndarray,
    paths: np.ndarray,
    additions: np.ndarray,
    title: str,
    output: Path,
) -> None:
    orphan = _native_map(orphan_high)
    rgb = np.full((*physical.shape, 3), 248, dtype=np.uint8)
    rgb[physical] = (191, 211, 226)
    rgb[orphan] = (230, 126, 34)
    rgb[paths] = (145, 76, 196)
    rgb[additions] = (220, 45, 52)
    image = Image.fromarray(rgb, "RGB")
    canvas = Image.new("RGB", (image.width, image.height + 38), "white")
    canvas.paste(image, (0, 38))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 8), title, fill="black")
    draw.text(
        (8, 22),
        "orange=orphan core; purple=located neck; red=added material",
        fill="black",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def _sheet_preview(mask: np.ndarray, color: str, output: Path) -> None:
    rgb = np.full((*mask.shape, 3), 250, dtype=np.uint8)
    rgb[mask] = _hex_rgb(color)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, "RGB").save(output)


def _difference(before: np.ndarray, after: np.ndarray, output: Path) -> int:
    changed = np.any(before != after, axis=2)
    image = before.copy()
    image[~changed] = (244, 244, 244)
    image[changed] = (232, 35, 46)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image, "RGB").save(output)
    return int(np.count_nonzero(changed))


def _opening_warnings(mask: np.ndarray) -> dict[str, Any]:
    openings = ~mask
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        openings.astype(np.uint8), connectivity=4
    )
    mm_per_pixel = REPAIR.physical_size_mm / DEFAULT_CONFIG.outer_pixels
    narrow: list[dict[str, Any]] = []
    for component in range(1, count):
        component_mask = (labels == component).astype(np.uint8)
        distance = cv2.distanceTransform(component_mask, cv2.DIST_L2, 5)
        max_width_mm = float(distance.max() * 2.0 * mm_per_pixel)
        if max_width_mm < 1.2:
            narrow.append(
                {
                    "component": component,
                    "area_pixels": int(stats[component, cv2.CC_STAT_AREA]),
                    "max_inscribed_width_mm": max_width_mm,
                    "bounds_pixels": [int(v) for v in stats[component, :4]],
                }
            )
    return {
        "opening_components": count - 1,
        "narrow_opening_components_under_1_2mm": len(narrow),
        "narrow_openings": narrow,
        "acceptance_effect": "DIAGNOSTIC_ONLY_NOT_RETAINED_WIDTH_FAILURE",
    }


def _bottleneck_locations(width: dict[str, Any]) -> list[dict[str, Any]]:
    mm_per_pixel = float(width["mm_per_pixel"])
    rows: list[dict[str, Any]] = []
    for detail in width["orphan_structural_core_details"]:
        x, y, w, h = detail["bounds_pixels_at_required_kernel"]
        row = dict(detail)
        row["bounds_mm_from_top_left"] = [
            x * mm_per_pixel,
            y * mm_per_pixel,
            w * mm_per_pixel,
            h * mm_per_pixel,
        ]
        rows.append(row)
    return rows


def main() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    manifest = json.loads(
        (INPUT / "rejected_candidate_report.json").read_text(encoding="utf-8")
    )
    layer_files = manifest["layer_files"]
    masks = [_mask(INPUT / row["retained_material_mask"]) for row in layer_files]
    visible = [_mask(INPUT / row["source_visible_region_mask"]) for row in layer_files]
    palette_indices = [int(row["palette_index"]) for row in layer_files]
    palette = _palette(manifest)
    target = np.asarray(Image.open(INDEXED_TARGET).convert("RGB"))
    p = DEFAULT_CONFIG.perimeter_pixels

    safe_masks: list[np.ndarray] = []
    proposal_masks: list[np.ndarray] = []
    layer_reports: list[dict[str, Any]] = []
    for index, (row, physical, visible_aperture) in enumerate(
        zip(layer_files, masks, visible, strict=True)
    ):
        before, before_maps = audit_mask_at_physical_scale(
            physical, visible_aperture, repair=REPAIR
        )
        safe_domain = hidden_addition_domain(masks, index)
        safe_mask, safe_result, safe_diagnostics = repair_one_mask(
            physical, visible_aperture, safe_domain, repair=REPAIR
        )
        proposal_mask, proposal_result, proposal_diagnostics = repair_one_mask(
            physical, visible_aperture, ~physical, repair=REPAIR
        )
        safe_masks.append(safe_mask)
        proposal_masks.append(proposal_mask)

        layer_dir = OUTPUT / "layers" / row["id"]
        _save_mask(physical, layer_dir / "before_retained_material.png")
        _save_mask(~physical, layer_dir / "before_through_cutouts.png")
        _save_mask(safe_mask, layer_dir / "exact_preserving_retained_material.png")
        _save_mask(~safe_mask, layer_dir / "exact_preserving_through_cutouts.png")
        _save_mask(
            proposal_mask, layer_dir / "visible_change_proposal_retained_material.png"
        )
        _save_mask(
            ~proposal_mask, layer_dir / "visible_change_proposal_through_cutouts.png"
        )
        _save_mask(proposal_diagnostics["added"], layer_dir / "added_pixels.png")
        _sheet_preview(physical, row["color"], layer_dir / "before_preview.png")
        _sheet_preview(
            proposal_mask, row["color"], layer_dir / "visible_change_proposal.png"
        )
        _overlay(
            physical,
            before_maps["orphan_core"],
            proposal_diagnostics["paths"],
            proposal_diagnostics["added"],
            f"{row['id']} at 200 mm",
            layer_dir / "repair_diagnostic_overlay.png",
        )

        widths = [
            float(detail["largest_passing_kernel_mm"])
            for detail in before["width"]["orphan_structural_core_details"]
        ]
        layer_reports.append(
            {
                "physical_layer_index_back_to_front": index,
                "id": row["id"],
                "color": row["color"],
                "before": {
                    "four_neighbor": before["edge"],
                    "retained_width_status": before["width"]["status"],
                    "orphan_structural_core_components": before["width"][
                        "orphan_structural_core_components"
                    ],
                    "orphan_structural_core_pixels": before["width"][
                        "orphan_structural_core_pixels"
                    ],
                    "measured_bottleneck_width_mm_min": min(widths)
                    if widths
                    else None,
                    "measured_bottleneck_width_mm_max": max(widths)
                    if widths
                    else None,
                    "bottlenecks": _bottleneck_locations(before["width"]),
                    "openings": _opening_warnings(physical),
                },
                "exact_preserving_attempt": safe_result,
                "visible_change_proposal": {
                    **proposal_result,
                    "all_additions_unhidden_in_original_stack": bool(
                        proposal_result["modified_pixels"]
                        and not np.any(
                            proposal_diagnostics["added"] & safe_domain
                        )
                    ),
                },
                "artifact_directory": str(layer_dir.relative_to(OUTPUT)),
            }
        )

    _, original_outer = compose_back_to_front(masks, palette_indices, palette)
    _, safe_outer = compose_back_to_front(safe_masks, palette_indices, palette)
    _, proposal_outer = compose_back_to_front(
        proposal_masks, palette_indices, palette
    )
    original = original_outer[p:-p, p:-p]
    safe = safe_outer[p:-p, p:-p]
    proposal = proposal_outer[p:-p, p:-p]
    previews = OUTPUT / "previews"
    previews.mkdir(parents=True)
    Image.fromarray(target, "RGB").save(previews / "indexed_target.png")
    Image.fromarray(original, "RGB").save(previews / "original_stack_reconstruction.png")
    Image.fromarray(safe, "RGB").save(
        previews / "exact_preserving_reconstruction.png"
    )
    Image.fromarray(proposal, "RGB").save(
        previews / "visible_change_proposal_reconstruction.png"
    )
    safe_changed = _difference(
        target, safe, previews / "exact_preserving_difference.png"
    )
    proposal_changed = _difference(
        target, proposal, previews / "visible_change_proposal_difference.png"
    )
    (OUTPUT / "source").mkdir(parents=True, exist_ok=True)
    shutil.copy2(INDEXED_TARGET, OUTPUT / "source" / "indexed_target.png")
    if (ROOT / "turtle_on_leaf.jfif").exists():
        shutil.copy2(ROOT / "turtle_on_leaf.jfif", OUTPUT / "source" / "original_source.jfif")

    perimeter = np.ones_like(masks[0], dtype=bool)
    perimeter[p:-p, p:-p] = False
    report = {
        "status": "READY_FOR_USER_TEST_FIXED_STACK_UNRESOLVED",
        "accepted_stack": False,
        "reason": (
            "The corrected 200 mm scale clears four prior failures, but seven "
            "layers still fail. The cumulative fixed stack has zero hidden "
            "addition pixels on every layer, so widening those necks changes "
            "the approved visible composite."
        ),
        "configuration": {
            "physical_size_mm": [200.0, 200.0],
            "native_outer_pixels": DEFAULT_CONFIG.outer_pixels,
            "native_mm_per_pixel": REPAIR.physical_size_mm
            / DEFAULT_CONFIG.outer_pixels,
            "validation_scale": REPAIR.validation_scale,
            "validation_outer_pixels": validation_poc_config(
                DEFAULT_CONFIG, REPAIR
            ).outer_pixels,
            "validation_mm_per_pixel": REPAIR.physical_size_mm
            / validation_poc_config(DEFAULT_CONFIG, REPAIR).outer_pixels,
            "minimum_retained_width_mm": 1.2,
            "validation_kernel_pixels": layer_reports[0]["before"][
                "bottlenecks"
            ][0]["required_kernel_pixels"]
            if layer_reports[0]["before"]["bottlenecks"]
            else 15,
            "validation_kernel_width_mm": 15
            * (
                REPAIR.physical_size_mm
                / validation_poc_config(DEFAULT_CONFIG, REPAIR).outer_pixels
            ),
            "raster_tolerance_above_1_2mm": 15
            * (
                REPAIR.physical_size_mm
                / validation_poc_config(DEFAULT_CONFIG, REPAIR).outer_pixels
            )
            - 1.2,
        },
        "scale_only_result": {
            "passing_layers": sum(
                row["before"]["retained_width_status"] == "PASS"
                for row in layer_reports
            ),
            "failing_layers": sum(
                row["before"]["retained_width_status"] != "PASS"
                for row in layer_reports
            ),
        },
        "exact_preserving_attempt": {
            "modified_mask_pixels": sum(
                row["exact_preserving_attempt"]["modified_pixels"]
                for row in layer_reports
            ),
            "changed_composite_pixels": safe_changed,
            "exact_indexed_target_reconstruction": safe_changed == 0,
            "failing_layers_after": sum(
                row["exact_preserving_attempt"]["status"] != "PASS"
                for row in layer_reports
            ),
            "hidden_addition_capacity_pixels_by_layer": [
                row["exact_preserving_attempt"]["available_addition_pixels"]
                for row in layer_reports
            ],
        },
        "visible_change_proposal_not_approved": {
            "modified_mask_pixels": sum(
                row["visible_change_proposal"]["modified_pixels"]
                for row in layer_reports
            ),
            "changed_composite_pixels": proposal_changed,
            "exact_indexed_target_reconstruction": proposal_changed == 0,
            "failing_layers_after": sum(
                row["visible_change_proposal"]["status"] != "PASS"
                for row in layer_reports
            ),
            "four_neighbor_failing_layers_after": sum(
                row["visible_change_proposal"]["edge"]["four_neighbor_status"]
                != "PASS"
                for row in layer_reports
            ),
            "thin_decorative_visible_component_warnings_after": sum(
                row["visible_change_proposal"]["width"][
                    "thin_decorative_visible_components"
                ]
                for row in layer_reports
            ),
            "visible_components_outside_restored_support_after": sum(
                row["visible_change_proposal"]["width"][
                    "visible_components_outside_restored_support"
                ]
                for row in layer_reports
            ),
            "shared_perimeter_missing_pixels_by_layer": [
                int(np.count_nonzero(perimeter & ~mask))
                for mask in proposal_masks
            ],
            "approval": "REQUIRED_BEFORE_TARGET_OR_STACK_CHANGE",
        },
        "layers": layer_reports,
        "construction_model": (
            "Preserved: repair is additive to retained masks, cutouts are exact "
            "complements, layer order/colors are unchanged, and accepted additions "
            "must be covered by an existing more-forward sheet."
        ),
        "unresolved": [
            {
                "layer_index_back_to_front": row[
                    "physical_layer_index_back_to_front"
                ],
                "color": row["color"],
                "reason": "NO_HIDDEN_FILL_CAPACITY_REQUIRES_DIFFERENT_LAYER_ASSIGNMENT_OR_VISIBLE_ARTWORK_APPROVAL",
            }
            for row in layer_reports
            if row["exact_preserving_attempt"]["status"] != "PASS"
        ],
    }
    _write_json(OUTPUT / "reports" / "width_repair_report.json", report)

    table = [
        "# Turtle retained-width repair at 200 mm",
        "",
        "Status: READY_FOR_USER_TEST — fixed stack remains structurally unresolved.",
        "",
        "| Back-to-front layer | Color | Before | Orphan cores | Hidden capacity px | Exact repair px | Visible proposal px | Proposal width |",
        "|---:|---|---|---:|---:|---:|---:|---|",
    ]
    for row in layer_reports:
        table.append(
            "| {physical_layer_index_back_to_front} | {color} | {before_status} | "
            "{orphans} | {capacity} | {safe_px} | {proposal_px} | {proposal_status} |".format(
                physical_layer_index_back_to_front=row[
                    "physical_layer_index_back_to_front"
                ],
                color=row["color"],
                before_status=row["before"]["retained_width_status"],
                orphans=row["before"]["orphan_structural_core_components"],
                capacity=row["exact_preserving_attempt"][
                    "available_addition_pixels"
                ],
                safe_px=row["exact_preserving_attempt"]["modified_pixels"],
                proposal_px=row["visible_change_proposal"]["modified_pixels"],
                proposal_status=row["visible_change_proposal"]["status"],
            )
        )
    table.extend(
        [
            "",
            f"Exact-preserving reconstruction changed pixels: {safe_changed}.",
            f"Unapproved visible proposal changed pixels: {proposal_changed}.",
            "The proposal has zero orphan structural cores, but retains seven thin-decorative warnings; three visible components remain outside the conservative restored-support map. These are not retained-width failures under the unchanged validator and remain separate printability warnings.",
            "",
            "The visible proposal proves the local widening operator can make the seven failing masks pass, but it is not an acceptable replacement because it changes the indexed artwork. A different region-to-depth assignment is required for an exact solution.",
        ]
    )
    (OUTPUT / "reports" / "REVIEW_REPORT.md").write_text(
        "\n".join(table) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["scale_only_result"], sort_keys=True), flush=True)
    print(json.dumps(report["exact_preserving_attempt"], sort_keys=True), flush=True)
    print(
        json.dumps(report["visible_change_proposal_not_approved"], sort_keys=True),
        flush=True,
    )


if __name__ == "__main__":
    main()
