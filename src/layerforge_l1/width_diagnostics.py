"""Corrected 2D structural-width diagnostics for subtractive layer sheets.

This module does not generate CAD.  It preserves the reviewed masks, compares
the former 9x9 check with the physically derived 7x7 check, separates thin
decoration from load-bearing cores, and records bounded ordering experiments
without claiming global infeasibility.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .connectivity_repair import (
    _core_connectivity,
    _render_montage,
    _render_width_map,
    _sha256,
    _width_parameters,
    _write_json,
    audit_connection_width,
)
from .layer_optimization import (
    DEFAULT_OPTIMIZATION_CONFIG,
    OptimizationConfig,
    _replay_sequence,
    optimize_region_sequence,
)
from .subtractive_poc import (
    DEFAULT_CONFIG,
    PocConfig,
    _outer_and_perimeter,
    analyze_image,
    construct_layers,
    discover_order,
    validate_plan,
)


def _load_mask(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L")) > 0


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _compose_from_exported_masks(
    root: Path, manifest: dict[str, Any], poc: PocConfig
) -> np.ndarray:
    """Independently reconstruct the aperture from exported retained masks."""

    p = poc.perimeter_pixels
    image = np.zeros((poc.analysis_pixels, poc.analysis_pixels, 3), dtype=np.uint8)
    covered = np.zeros(image.shape[:2], dtype=bool)
    for layer in sorted(manifest["layers"], key=lambda row: row["depth_back_to_front"]):
        mask = _load_mask(root / layer["retained_material_mask"])[p:-p, p:-p]
        image[mask] = _hex_rgb(layer["color"])
        covered |= mask
    if not np.all(covered):
        raise RuntimeError("exported masks do not cover the complete artwork aperture")
    return image


def _render_bottleneck_comparison(
    physical: np.ndarray,
    maps: dict[str, np.ndarray],
    detail: dict[str, Any],
    perimeter: np.ndarray,
    output: Path,
) -> None:
    required = int(detail["required_kernel_pixels"])
    passing = int(detail["largest_passing_kernel_pixels"])
    target = maps["core_labels"] == int(detail["component"])

    def panel(kernel_size: int, required_panel: bool) -> Image.Image:
        _, connected, _, _, _ = _core_connectivity(
            physical.astype(np.uint8), perimeter, max(1, kernel_size)
        )
        rgb = np.full((*physical.shape, 3), 248, dtype=np.uint8)
        rgb[physical] = (190, 211, 226)
        rgb[connected] = (54, 153, 92)
        rgb[target] = (210, 45, 45) if required_panel else (245, 183, 49)
        return Image.fromarray(rgb, "RGB")

    left = panel(passing, False)
    right = panel(required, True)
    canvas = Image.new("RGB", (left.width * 2, left.height + 52), "white")
    canvas.paste(left, (0, 52))
    canvas.paste(right, (left.width, 52))
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (8, 8),
        f"Measured last pass: {passing}px / {detail['largest_passing_kernel_mm']:.3f}mm",
        fill="black",
    )
    draw.text(
        (left.width + 8, 8),
        f"Required failure: {required}px / {detail['required_kernel_mm']:.3f}mm",
        fill="black",
    )
    draw.text((8, 28), detail["classification"], fill="black")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def _summarize_width(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "layer_count": len(rows),
        "conflicting_layer_count": sum(row["status"] != "PASS" for row in rows),
        "orphan_structural_core_components": sum(
            row["orphan_structural_core_components"] for row in rows
        ),
        "structural_bottleneck_visible_components": sum(
            row["structural_bottleneck_visible_components"] for row in rows
        ),
        "thin_decorative_visible_components": sum(
            row["thin_decorative_visible_components"] for row in rows
        ),
        "visible_components_outside_restored_support": sum(
            row["visible_components_outside_restored_support"] for row in rows
        ),
    }


def _audit_plan_widths(
    plan_root: Path,
    manifest: dict[str, Any],
    output: Path,
    poc: PocConfig,
    optimization: OptimizationConfig,
    kernel_size: int,
    render_bottlenecks: bool,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    _, perimeter = _outer_and_perimeter(poc)
    rows: list[dict[str, Any]] = []
    montage_paths: list[Path] = []
    for layer in manifest["layers"]:
        physical = _load_mask(plan_root / layer["retained_material_mask"])
        visible = _load_mask(plan_root / layer["source_visible_region_mask"])
        audit, maps = audit_connection_width(
            physical,
            visible,
            poc,
            optimization,
            kernel_size_override=kernel_size,
        )
        row = {
            "id": layer["id"],
            "depth_back_to_front": layer["depth_back_to_front"],
            "color": layer["color"],
            **audit,
        }
        map_path = output / f"{layer['id']}_width_map.png"
        _render_width_map(
            physical,
            maps,
            map_path,
            f"depth {layer['depth_back_to_front']} {layer['color']} | kernel={kernel_size}px core-orphans={audit['orphan_structural_core_components']} decorative={audit['thin_decorative_visible_components']}",
        )
        montage_paths.append(map_path)
        if render_bottlenecks:
            for detail in row["orphan_structural_core_details"]:
                relative = Path("bottlenecks") / (
                    f"{layer['id']}_component_{detail['component']:03d}_measured_vs_required.png"
                )
                _render_bottleneck_comparison(
                    physical, maps, detail, perimeter, output / relative
                )
                detail["failure_map"] = relative.as_posix()
        rows.append(row)
    _render_montage(montage_paths, output / "width_montage.png")
    return {"summary": _summarize_width(rows), "layers": rows}


def _evaluate_sequence(
    sequence: tuple[int, ...],
    graph: dict[str, Any],
    analysis: dict[str, Any],
    poc: PocConfig,
    optimization: OptimizationConfig,
) -> dict[str, Any]:
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    provider_labels, providers, _ = _replay_sequence(sequence, graph, palette)
    planned = dict(analysis)
    planned["provider_labels"] = provider_labels
    planned["providers"] = providers
    order = discover_order(planned, poc)
    layers = construct_layers(planned, order, poc)
    validation, _, _ = validate_plan(planned, order, layers, poc)
    width_rows = [
        audit_connection_width(
            layer["physical_mask"], layer["visible_aperture"], poc, optimization
        )[0]
        for layer in layers
    ]
    return {
        "sequence_front_to_back": list(sequence),
        "layer_count": len(sequence),
        "exact_composite_changed_pixels": validation["geometry_mismatched_pixels"],
        "width_summary": _summarize_width(width_rows),
    }


def _run_search_experiment(
    analysis: dict[str, Any],
    current_sequence: tuple[int, ...],
    poc: PocConfig,
    optimization: OptimizationConfig,
) -> dict[str, Any]:
    try:
        sequence, graph, search = optimize_region_sequence(
            analysis["labels"], optimization
        )
    except RuntimeError as error:
        return {
            "status": "NO_PLAN_WITHIN_BOUNDS",
            "reason": str(error),
            "search": getattr(error, "search_report", {}),
            "global_infeasibility_proven": False,
        }
    evaluation = _evaluate_sequence(
        sequence, graph, analysis, poc, DEFAULT_OPTIMIZATION_CONFIG
    )
    return {
        "status": "CANDIDATE_FOUND",
        "same_sequence_as_current": sequence == current_sequence,
        "search": search,
        "evaluation": evaluation,
        "global_infeasibility_proven": False,
    }


def _fixed_order_hidden_capacity(
    plan_root: Path, manifest: dict[str, Any], poc: PocConfig
) -> dict[str, Any]:
    """Check whether front sheets leave any new safe hidden pixels to add.

    The current construction is nested.  This calculation makes that property
    explicit instead of assuming hidden support remains available.
    """

    p = poc.perimeter_pixels
    layers = sorted(manifest["layers"], key=lambda row: row["depth_back_to_front"])
    masks = [_load_mask(plan_root / layer["retained_material_mask"])[p:-p, p:-p] for layer in layers]
    front_union = np.zeros_like(masks[0])
    rows: list[dict[str, Any]] = []
    for layer, mask in reversed(list(zip(layers, masks))):
        available = front_union & ~mask
        rows.append(
            {
                "id": layer["id"],
                "depth_back_to_front": layer["depth_back_to_front"],
                "safe_hidden_pixels_available_without_reordering": int(
                    np.count_nonzero(available)
                ),
            }
        )
        front_union |= mask
    rows.reverse()
    total = sum(row["safe_hidden_pixels_available_without_reordering"] for row in rows)
    return {
        "method": "union of material in all sheets in front minus current retained material",
        "safe_hidden_pixels_available": total,
        "layers": rows,
        "conclusion": (
            "The current nested masks already retain every region hidden by a front sheet; fixed-order hidden fill cannot widen the remaining necks."
            if total == 0
            else "Some additional material can be hidden under front sheets without changing the composite."
        ),
    }


def run_width_diagnostics_case(
    source: Path,
    case_id: str,
    repaired_plan: Path,
    output: Path,
    poc: PocConfig = DEFAULT_CONFIG,
    optimization: OptimizationConfig = DEFAULT_OPTIMIZATION_CONFIG,
) -> dict[str, Any]:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copytree(repaired_plan, output / "current_plan")
    (output / "original").mkdir()
    shutil.copy2(source, output / "original" / "source.png")
    diagnostics = output / "diagnostics"
    reports = output / "reports"
    previews = output / "previews"
    reports.mkdir()
    previews.mkdir()

    manifest = json.loads(
        (repaired_plan / "automatic" / "layer_stack.json").read_text(
            encoding="utf-8"
        )
    )
    if manifest["source_sha256"] != _sha256(source):
        raise ValueError("source does not match preserved repaired plan")

    incorrect = _audit_plan_widths(
        repaired_plan,
        manifest,
        diagnostics / "before_incorrect_9px",
        poc,
        optimization,
        9,
        False,
    )
    corrected = _audit_plan_widths(
        repaired_plan,
        manifest,
        diagnostics / "after_corrected_7px",
        poc,
        optimization,
        int(_width_parameters(poc, optimization)["morphology_kernel_size_pixels"]),
        True,
    )

    reconstructed = _compose_from_exported_masks(repaired_plan, manifest, poc)
    Image.fromarray(reconstructed, "RGB").save(
        previews / "reconstructed_from_exported_masks.png"
    )
    prior = np.asarray(
        Image.open(repaired_plan / "previews" / "reconstructed_front.png").convert(
            "RGB"
        )
    )
    changed = np.any(reconstructed != prior, axis=2)
    change_map = np.zeros((*changed.shape, 3), dtype=np.uint8)
    change_map[changed] = (255, 0, 255)
    Image.fromarray(change_map, "RGB").save(
        previews / "reconstruction_changed_pixels.png"
    )

    analysis = analyze_image(source, poc)
    current_sequence = tuple(manifest["order_front_to_back_palette_indices"])
    strict_experiment = _run_search_experiment(
        analysis, current_sequence, poc, optimization
    )
    decorative_experiment = _run_search_experiment(
        analysis,
        current_sequence,
        poc,
        replace(
            optimization,
            exempt_thin_decorative_regions_from_contact_width=True,
        ),
    )
    hidden_capacity = _fixed_order_hidden_capacity(repaired_plan, manifest, poc)

    repeats = Counter(current_sequence)
    repeated = [
        {
            "palette_index": color,
            "color": analysis["palette_hex"][color],
            "layer_count": count,
            "reason": "separate reachability depths in the exact-composite topology; a new width-aware repartition must revalidate whether each occurrence remains necessary",
        }
        for color, count in sorted(repeats.items())
        if count > 1
    ]
    bottlenecks = [
        {
            "layer_id": layer["id"],
            "depth_back_to_front": layer["depth_back_to_front"],
            "color": layer["color"],
            **detail,
            "fixed_order_hidden_fill_resolution": "UNAVAILABLE_NESTED_MASK_ALREADY_MAXIMAL",
            "proposed_resolution": "width-aware alternate ordering or repeated-color region repartition; preserve this visible region and require exact composite replay before acceptance",
        }
        for layer in corrected["layers"]
        for detail in layer["orphan_structural_core_details"]
    ]

    width_report = {
        "case_id": case_id,
        "status": "READY_FOR_USER_TEST",
        "scope": "2D width diagnostics only; no CAD, STL, STEP, frontend, frame, or specification edits",
        "source_sha256": _sha256(source),
        "physical_width_conversion": _width_parameters(poc, optimization),
        "before_incorrect_9px": incorrect,
        "after_corrected_7px": corrected,
        "classification_policy": {
            "load_bearing_failure": "a structural core survives the required erosion but has no 4-neighbour path to the perimeter-connected core",
            "thin_decorative_warning": "a visible component has no surviving core at the required footprint; it is preserved exactly but is not counted as a disconnected load-bearing core",
            "no_detail_deletion": True,
        },
        "reconstruction": {
            "method": "independent back-to-front composition directly from preserved exported retained-material PNG masks",
            "changed_pixels_against_prior_approved_composite": int(
                np.count_nonzero(changed)
            ),
        },
        "fixed_order_hidden_support": hidden_capacity,
        "unresolved_structural_bottlenecks": bottlenecks,
    }
    _write_json(reports / "corrected_width_validation.json", width_report)

    investigation = {
        "case_id": case_id,
        "strict_all_region_7px_search": strict_experiment,
        "thin_decoration_exempt_7px_search": decorative_experiment,
        "current_sequence_front_to_back": list(current_sequence),
        "repeated_color_layers": repeated,
        "fixed_order_hidden_support": hidden_capacity,
        "infeasibility_claim": {
            "globally_proven": False,
            "proven_only": "the reported masks contain the enumerated 7x7 orphan cores, and the current nested order has no additional front-occluded pixels available for hidden fill",
            "not_proven": "that no alternate ordering, repeated-color repartition, or exact mask-level global search can satisfy the design",
        },
        "proposed_resolution": {
            "planner_change": "search exact cumulative mask morphology at each prefix instead of using interface-contact length as a sufficient proxy",
            "ordering": "optimize color order and provider-region grouping jointly against orphan-core count and retained area",
            "repeated_colors": "permit another occurrence only when it removes a measured bottleneck while preserving every source pixel",
            "acceptance": "zero 7x7 orphan structural cores, zero changed composite pixels, unchanged shared perimeter, and no visible connector bridge",
        },
    }
    _write_json(reports / "ordering_and_support_investigation.json", investigation)

    checksum_files = sorted(
        path for path in output.rglob("*") if path.is_file() and path.name != "checksums.sha256"
    )
    (reports / "checksums.sha256").write_text(
        "".join(
            f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_files
        ),
        encoding="utf-8",
    )
    return {
        "case_id": case_id,
        "review_state": "READY_FOR_USER_TEST",
        "corrected_width_summary": corrected["summary"],
        "changed_composite_pixels": int(np.count_nonzero(changed)),
        "global_infeasibility_proven": False,
    }

