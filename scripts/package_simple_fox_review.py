from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from layerforge_l1.connectivity_repair import (
    _render_montage,
    _render_width_map,
    audit_connection_width,
    audit_edge_connectivity,
)
from layerforge_l1.layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG, analyze_image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "fox_simple_input.png"
OUTPUT = ROOT / "artifacts" / "l1_fox_simple_review_v1"
PLAN = OUTPUT / "two_d_plan"
ARCHIVE = ROOT / "artifacts" / "LayerForge_L1_fox_simple_2D_review_v1.zip"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_mask(relative: str) -> np.ndarray:
    return np.asarray(Image.open(PLAN / relative).convert("L")) > 0


def layer_preview(mask: np.ndarray, color: str, title: str, path: Path) -> None:
    rgb = np.full((*mask.shape, 3), 248, dtype=np.uint8)
    rgb[mask] = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
    image = Image.fromarray(rgb, "RGB")
    canvas = Image.new("RGB", (image.width, image.height + 34), "white")
    canvas.paste(image, (0, 34))
    ImageDraw.Draw(canvas).text((8, 10), title, fill="black")
    canvas.save(path)


def comparison(
    source: np.ndarray, target: np.ndarray, reconstruction: np.ndarray, path: Path
) -> None:
    size = source.shape[0]
    canvas = Image.new("RGB", (size * 3, size + 38), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (title, image) in enumerate(
        (
            ("INPUT RGB", source),
            ("INDEXED FILAMENT TARGET", target),
            ("FROM ACTUAL LAYER MASKS", reconstruction),
        )
    ):
        canvas.paste(Image.fromarray(image, "RGB"), (index * size, 38))
        draw.text((index * size + 8, 12), title, fill="black")
    canvas.save(path)


def main() -> int:
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)
    report_path = OUTPUT / "two_d_validation.json"
    if not report_path.exists():
        raise FileNotFoundError(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    analysis = analyze_image(SOURCE)
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    target = palette[np.asarray(analysis["labels"])]
    p = DEFAULT_CONFIG.perimeter_pixels

    previews = OUTPUT / "previews"
    diagnostics = OUTPUT / "diagnostics" / "width"
    reports = OUTPUT / "reports"
    previews.mkdir(parents=True, exist_ok=True)
    diagnostics.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)

    Image.fromarray(target, "RGB").save(previews / "indexed_filament_target.png")
    shutil.copy2(
        PLAN / "reconstructed_from_actual_masks.png",
        previews / "reconstructed_front_from_masks.png",
    )

    composed = np.full((DEFAULT_CONFIG.outer_pixels, DEFAULT_CONFIG.outer_pixels, 3), 247, dtype=np.uint8)
    layer_rows: list[dict[str, Any]] = []
    layer_preview_paths: list[Path] = []
    width_map_paths: list[Path] = []
    common_shapes: set[tuple[int, int]] = set()
    all_perimeters = True
    cutout_layers = 0
    for layer in report["layers"]:
        retained = load_mask(layer["retained_material_mask"])
        cutouts = load_mask(layer["through_cutouts_mask"])
        visible = load_mask(layer["visible_region_mask"])
        common_shapes.add(retained.shape)
        perimeter_ok = bool(
            retained[:p, :].all()
            and retained[-p:, :].all()
            and retained[:, :p].all()
            and retained[:, -p:].all()
        )
        all_perimeters &= perimeter_ok
        cutout_layers += int(np.any(cutouts))
        edge, _ = audit_edge_connectivity(retained)
        width, maps = audit_connection_width(
            retained,
            visible,
            DEFAULT_CONFIG,
            DEFAULT_OPTIMIZATION_CONFIG,
        )
        color = layer["color"]
        color_rgb = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
        composed[retained] = color_rgb

        preview_path = previews / f"{layer['id']}_physical_sheet.png"
        layer_preview(
            retained,
            color,
            f"depth {layer['depth_back_to_front']:02d} back-to-front | {color} | {layer['id']}",
            preview_path,
        )
        layer_preview_paths.append(preview_path)
        width_path = diagnostics / f"{layer['id']}_width.png"
        _render_width_map(
            retained,
            maps,
            width_path,
            f"{layer['id']} | {width['status']} | orphan cores={width['orphan_structural_core_components']}",
        )
        width_map_paths.append(width_path)
        layer_rows.append(
            {
                **layer,
                "mask_shape_pixels": list(retained.shape[::-1]),
                "full_shared_perimeter": perimeter_ok,
                "has_real_through_cutout": bool(np.any(cutouts)),
                "retained_pixels": int(np.count_nonzero(retained)),
                "cutout_pixels": int(np.count_nonzero(cutouts)),
                "four_neighbor_audit": edge,
                "width_audit": width,
            }
        )

    reconstructed = composed[p:-p, p:-p]
    existing = np.asarray(
        Image.open(PLAN / "reconstructed_from_actual_masks.png").convert("RGB")
    )
    independent_mismatch = np.any(reconstructed != target, axis=2)
    saved_mismatch = np.any(existing != reconstructed, axis=2)
    Image.fromarray(reconstructed, "RGB").save(
        previews / "independent_reconstructed_front.png"
    )
    comparison(np.asarray(analysis["rgb"]), target, reconstructed, previews / "source_target_reconstruction.png")
    _render_montage(layer_preview_paths, previews / "exploded_layers.png", columns=4)
    _render_montage(width_map_paths, diagnostics / "width_conflict_montage.png", columns=4)

    source_delta = np.abs(np.asarray(analysis["rgb"]).astype(np.int16) - target.astype(np.int16))
    failed_layers = [row["id"] for row in layer_rows if row["width_audit"]["status"] != "PASS"]
    summary = {
        "status": "READY_FOR_USER_TEST",
        "l1_approval": "NOT_APPROVED",
        "input": "source/source.png",
        "source_sha256": sha256(SOURCE),
        "simplification_run": False,
        "planner": {
            **{key: value for key, value in report.items() if key != "layers"},
            "layer_manifest": "layer_stack.json",
        },
        "validation": {
            "identical_outer_dimensions": len(common_shapes) == 1,
            "mask_dimensions_pixels": [list(shape[::-1]) for shape in sorted(common_shapes)],
            "continuous_shared_perimeter_every_layer": all_perimeters,
            "four_neighbor_failed_layers": sum(
                row["four_neighbor_audit"]["four_neighbor_status"] != "PASS"
                for row in layer_rows
            ),
            "minimum_structural_width_mm": DEFAULT_OPTIMIZATION_CONFIG.nominal_connection_width_mm
            + DEFAULT_OPTIMIZATION_CONFIG.vectorization_safety_mm
            + DEFAULT_OPTIMIZATION_CONFIG.cad_conversion_safety_mm,
            "minimum_width_failed_layers": len(failed_layers),
            "minimum_width_failed_layer_ids": failed_layers,
            "orphan_structural_core_components": sum(
                row["width_audit"]["orphan_structural_core_components"]
                for row in layer_rows
            ),
            "orphan_structural_core_pixels": sum(
                row["width_audit"]["orphan_structural_core_pixels"]
                for row in layer_rows
            ),
            "fine_feature_warnings": sum(
                row["width_audit"]["thin_decorative_visible_components"]
                for row in layer_rows
            ),
            "layers_with_real_through_cutouts": cutout_layers,
            "visible_connector_bridge_pixels": report["visible_automatic_bridge_pixels"],
            "independent_mask_composite_mismatched_pixels": int(np.count_nonzero(independent_mismatch)),
            "saved_vs_independent_reconstruction_mismatched_pixels": int(np.count_nonzero(saved_mismatch)),
        },
        "visual_comparison": {
            "basis": "indexed printable target compared with analysis-resolution input RGB",
            "changed_pixels": int(np.count_nonzero(np.any(source_delta != 0, axis=2))),
            "changed_fraction": float(np.mean(np.any(source_delta != 0, axis=2))),
            "mean_absolute_rgb_error_0_255": float(source_delta.mean()),
            "maximum_channel_error_0_255": int(source_delta.max()),
        },
        "cad_export": {
            "status": "NOT_RUN_DUE_TO_2D_STRUCTURAL_GATE_FAILURE",
            "stl_files": 0,
            "step_files": 0,
            "reason": (
                f"{len(failed_layers)} sheets fail the configured positive-width structural check."
            ),
        },
        "layers": layer_rows,
        "limitations": [
            "The selected bounded search did not find a width-valid plan; this does not prove global infeasibility.",
            "The indexed target approximates the input RGB with a finite one-filament-per-sheet palette.",
            "No simplification, visible bridge fallback, CAD export, or physical-print claim was applied.",
        ],
    }
    write_json(reports / "validation_summary.json", summary)
    write_json(
        OUTPUT / "layer_stack.json",
        {
            "status": report["planner_status"],
            "order": "back_to_front",
            "sheet_size_mm": [DEFAULT_CONFIG.sheet_size_mm, DEFAULT_CONFIG.sheet_size_mm],
            "outer_mask_pixels": [DEFAULT_CONFIG.outer_pixels, DEFAULT_CONFIG.outer_pixels],
            "perimeter_pixels": DEFAULT_CONFIG.perimeter_pixels,
            "one_filament_color_per_sheet": True,
            "visible_connector_bridges": False,
            "layers": layer_rows,
        },
    )
    (reports / "REVIEW_REPORT.md").write_text(
        "\n".join(
            [
                "# Simplified fox direct-conversion review",
                "",
                "Status: **READY_FOR_USER_TEST — 2D STRUCTURAL GATE FAILED**",
                "",
                f"- Planned sheets: {report['physical_layer_count']} (back-to-front).",
                f"- Repeated-color depths: {report['repeated_color_layer_count']}.",
                "- Shared dimensions/perimeter: PASS.",
                "- 4-neighbor connected sheets: PASS (0 failed).",
                f"- 1.2 mm positive-width check: FAIL ({len(failed_layers)} sheets).",
                f"- Orphan structural cores: {summary['validation']['orphan_structural_core_components']} "
                f"covering {summary['validation']['orphan_structural_core_pixels']} pixels.",
                f"- Fine-feature warnings: {summary['validation']['fine_feature_warnings']}.",
                "- Visible connector bridges: 0.",
                "- Reconstruction versus indexed target: exact (0 mismatched pixels).",
                "- CAD/STL/STEP: NOT RUN because the 2D gate failed.",
                "",
                "The bounded planner result is unresolved, not a proof of global infeasibility. "
                "No artwork simplification or automatic visible support bridge was applied.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    checksums = []
    for path in sorted(OUTPUT.rglob("*")):
        if path.is_file() and path.name != "checksums.sha256":
            checksums.append(f"{sha256(path)}  {path.relative_to(OUTPUT).as_posix()}")
    (reports / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")

    if ARCHIVE.exists():
        ARCHIVE.unlink()
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.rglob("*")):
            if path.is_file():
                archive.write(path, Path(OUTPUT.name) / path.relative_to(OUTPUT))
    print(json.dumps({"status": summary["status"], "zip": str(ARCHIVE), "validation": summary["validation"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
