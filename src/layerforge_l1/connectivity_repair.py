"""Audit and repair raster sheet connectivity without generating CAD.

This module deliberately distinguishes three different claims:

* 8-neighbour visual connectivity, where a diagonal corner counts;
* 4-neighbour edge connectivity, where material shares a finite pixel edge;
* width-qualified connectivity, where a conservative eroded structural core
  remains connected after nominal width and conversion allowances are applied.

Only hidden cumulative retained material is used.  The visible composite is
never edited to make a sheet pass.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .layer_optimization import (
    DEFAULT_OPTIMIZATION_CONFIG,
    OptimizationConfig,
    _replay_sequence,
    optimize_region_sequence,
)
from .subtractive_poc import (
    DEFAULT_CONFIG,
    PocConfig,
    _compose,
    _outer_and_perimeter,
    _render_comparison,
    _render_exploded,
    _save_layer_artifacts,
    analyze_image,
    construct_layers,
    discover_order,
    validate_plan,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _component_rows(
    labels: np.ndarray, stats: np.ndarray, component_ids: list[int]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for component in component_ids:
        rows.append(
            {
                "component": component,
                "area_pixels": int(stats[component, cv2.CC_STAT_AREA]),
                "bounds_pixels": [
                    int(stats[component, cv2.CC_STAT_LEFT]),
                    int(stats[component, cv2.CC_STAT_TOP]),
                    int(stats[component, cv2.CC_STAT_WIDTH]),
                    int(stats[component, cv2.CC_STAT_HEIGHT]),
                ],
            }
        )
    return rows


def audit_edge_connectivity(mask: np.ndarray) -> tuple[dict[str, Any], np.ndarray]:
    """Return explicit 4- and 8-neighbour component evidence for one mask."""

    binary = mask.astype(np.uint8)
    count4, labels4, stats4, _ = cv2.connectedComponentsWithStats(
        binary, connectivity=4
    )
    border4 = set(
        int(value)
        for value in np.unique(
            np.concatenate(
                (labels4[0], labels4[-1], labels4[:, 0], labels4[:, -1])
            )
        )
        if value
    )
    orphans4 = [component for component in range(1, count4) if component not in border4]
    count8, _, _, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    report = {
        "eight_neighbor_components": count8 - 1,
        "four_neighbor_components": count4 - 1,
        "four_neighbor_perimeter_components": len(border4),
        "four_neighbor_orphan_components": len(orphans4),
        "four_neighbor_orphan_pixels": int(
            sum(stats4[component, cv2.CC_STAT_AREA] for component in orphans4)
        ),
        "four_neighbor_status": "PASS" if count4 - 1 == 1 else "FAIL",
        "orphan_details": _component_rows(labels4, stats4, orphans4),
    }
    return report, labels4


def _width_parameters(
    poc: PocConfig, optimization: OptimizationConfig
) -> dict[str, Any]:
    mm_per_pixel = poc.sheet_size_mm / poc.outer_pixels
    enforced_mm = (
        optimization.nominal_connection_width_mm
        + optimization.vectorization_safety_mm
        + optimization.cad_conversion_safety_mm
    )
    requested_pixels = enforced_mm / mm_per_pixel
    enforced_pixels = math.ceil(requested_pixels)
    # OpenCV kernels are centred on one pixel.  A seven-pixel physical-width
    # requirement therefore uses a 7x7 footprint (radius three), not a 9x9
    # footprint (radius four).  If ceil() produces an even number, round the
    # footprint up once to retain a centred, conservative odd kernel.
    kernel_size = enforced_pixels if enforced_pixels % 2 else enforced_pixels + 1
    kernel_radius = (kernel_size - 1) // 2
    return {
        "sheet_size_mm": poc.sheet_size_mm,
        "outer_pixels": poc.outer_pixels,
        "mm_per_pixel": mm_per_pixel,
        "nominal_connection_width_mm": optimization.nominal_connection_width_mm,
        "vectorization_safety_mm": optimization.vectorization_safety_mm,
        "cad_conversion_safety_mm": optimization.cad_conversion_safety_mm,
        "enforced_connection_width_mm": enforced_mm,
        "requested_connection_width_pixels": requested_pixels,
        "enforced_connection_width_pixels": enforced_pixels,
        "morphology_kernel_shape": "ellipse",
        "morphology_kernel_size_pixels": kernel_size,
        "morphology_kernel_radius_pixels": kernel_radius,
        "kernel_rounding": (
            "ceil the physical requirement to whole pixels; retain that odd size, "
            "or round an even result up by one pixel so the kernel has a pixel centre"
        ),
        "method": "4-neighbour connectivity plus one elliptical erosion of the physical mask; surviving structural cores must connect to the common perimeter. Visible components that do not survive erosion are reported separately as thin decorative artwork, not mislabeled as load-bearing failures.",
    }


def _core_connectivity(
    material: np.ndarray, perimeter: np.ndarray, kernel_size: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[int]]:
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
    )
    core = cv2.erode(material, kernel, iterations=1).astype(bool)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        core.astype(np.uint8), connectivity=4
    )
    anchored = set(int(value) for value in np.unique(labels[core & perimeter]) if value)
    orphan_ids = [component for component in range(1, count) if component not in anchored]
    connected = np.isin(labels, list(anchored))
    return core, connected, labels, stats, orphan_ids


def _measure_orphan_bottlenecks(
    material: np.ndarray,
    perimeter: np.ndarray,
    required_kernel_size: int,
    orphan_labels: np.ndarray,
    orphan_stats: np.ndarray,
    orphan_ids: list[int],
    mm_per_pixel: float,
) -> list[dict[str, Any]]:
    """Measure each failure against the same odd-kernel morphology test.

    The result is deliberately discrete: it states the largest tested odd
    kernel for which the affected region still has a perimeter-connected core.
    It is not presented as an exact continuum-mechanics neck measurement.
    """

    tested_sizes = list(range(1, required_kernel_size + 1, 2))
    connectivity_by_size: dict[int, np.ndarray] = {}
    for size in tested_sizes:
        _, connected, _, _, _ = _core_connectivity(material, perimeter, size)
        connectivity_by_size[size] = connected

    details: list[dict[str, Any]] = []
    for component in orphan_ids:
        target = orphan_labels == component
        passing = [
            size
            for size in tested_sizes
            if np.any(connectivity_by_size[size] & target)
        ]
        largest = max(passing, default=0)
        details.append(
            {
                "component": component,
                "area_pixels_at_required_kernel": int(
                    orphan_stats[component, cv2.CC_STAT_AREA]
                ),
                "bounds_pixels_at_required_kernel": [
                    int(orphan_stats[component, cv2.CC_STAT_LEFT]),
                    int(orphan_stats[component, cv2.CC_STAT_TOP]),
                    int(orphan_stats[component, cv2.CC_STAT_WIDTH]),
                    int(orphan_stats[component, cv2.CC_STAT_HEIGHT]),
                ],
                "tested_odd_kernel_sizes_pixels": tested_sizes,
                "largest_passing_kernel_pixels": largest,
                "largest_passing_kernel_mm": largest * mm_per_pixel,
                "required_kernel_pixels": required_kernel_size,
                "required_kernel_mm": required_kernel_size * mm_per_pixel,
                "discrete_width_shortfall_pixels": required_kernel_size - largest,
                "discrete_width_shortfall_mm": (
                    required_kernel_size - largest
                )
                * mm_per_pixel,
                "measurement_scope": "largest odd elliptical erosion footprint with a perimeter-connected surviving core",
            }
        )
    return details


def audit_connection_width(
    physical_mask: np.ndarray,
    visible_aperture: np.ndarray,
    poc: PocConfig,
    optimization: OptimizationConfig,
    kernel_size_override: int | None = None,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Conservatively check positive-width support after raster/CAD allowance."""

    parameters = _width_parameters(poc, optimization)
    kernel_size = (
        int(kernel_size_override)
        if kernel_size_override is not None
        else int(parameters["morphology_kernel_size_pixels"])
    )
    if kernel_size < 1 or kernel_size % 2 == 0:
        raise ValueError("morphology kernel size must be a positive odd integer")
    parameters["morphology_kernel_size_pixels"] = kernel_size
    parameters["morphology_kernel_radius_pixels"] = (kernel_size - 1) // 2
    parameters["diagnostic_kernel_override"] = kernel_size_override is not None
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    material = physical_mask.astype(np.uint8)
    _, perimeter = _outer_and_perimeter(poc)
    core, connected_core, core_labels, stats, orphan_core = _core_connectivity(
        material, perimeter, kernel_size
    )
    anchored = set(int(value) for value in np.unique(core_labels[connected_core]) if value)
    restored_support = (
        cv2.dilate(connected_core.astype(np.uint8), kernel, iterations=1).astype(bool)
        & physical_mask
    )

    visible = np.zeros(physical_mask.shape, dtype=np.uint8)
    p = poc.perimeter_pixels
    visible[p:-p, p:-p] = visible_aperture.astype(np.uint8)
    visible_count, visible_labels, visible_stats, _ = cv2.connectedComponentsWithStats(
        visible, connectivity=4
    )
    outside_restored_support = [
        component
        for component in range(1, visible_count)
        if not np.any(restored_support[visible_labels == component])
    ]
    orphan_core_mask = np.isin(core_labels, orphan_core)
    thin_decorative: list[int] = []
    structural_bottleneck_visible: list[int] = []
    structurally_supported_visible: list[int] = []
    for component in range(1, visible_count):
        component_mask = visible_labels == component
        if not np.any(core & component_mask):
            thin_decorative.append(component)
        elif np.any(orphan_core_mask & component_mask):
            structural_bottleneck_visible.append(component)
        else:
            structurally_supported_visible.append(component)

    bottlenecks = _measure_orphan_bottlenecks(
        material,
        perimeter,
        kernel_size,
        core_labels,
        stats,
        orphan_core,
        float(parameters["mm_per_pixel"]),
    )
    for detail in bottlenecks:
        target = core_labels == int(detail["component"])
        visible_pixels = int(np.count_nonzero(target & visible.astype(bool)))
        detail["visible_core_overlap_pixels"] = visible_pixels
        detail["classification"] = (
            "VISIBLE_ARTWORK_STRUCTURAL_BOTTLENECK"
            if visible_pixels
            else "HIDDEN_RETAINED_MATERIAL_STRUCTURAL_BOTTLENECK"
        )
    passed = not orphan_core
    report = {
        **parameters,
        "structural_core_components": int(core_labels.max()),
        "perimeter_connected_core_components": len(anchored),
        "orphan_structural_core_components": len(orphan_core),
        "orphan_structural_core_pixels": int(
            sum(stats[component, cv2.CC_STAT_AREA] for component in orphan_core)
        ),
        "load_bearing_visible_components": len(structurally_supported_visible)
        + len(structural_bottleneck_visible),
        "structurally_supported_visible_components": len(
            structurally_supported_visible
        ),
        "structural_bottleneck_visible_components": len(
            structural_bottleneck_visible
        ),
        "thin_decorative_visible_components": len(thin_decorative),
        "visible_components_outside_restored_support": len(
            outside_restored_support
        ),
        # Compatibility alias for the earlier report.  It is diagnostic only
        # and no longer participates in the structural PASS/FAIL decision.
        "unsupported_visible_components": len(outside_restored_support),
        "status": "PASS" if passed else "CONFLICT_REQUIRES_USER_DECISION",
        "failure_basis": "orphan structural cores only; thin decorative components are warnings",
        "orphan_structural_core_details": bottlenecks,
        "structural_bottleneck_visible_details": _component_rows(
            visible_labels, visible_stats, structural_bottleneck_visible
        ),
        "thin_decorative_visible_details": _component_rows(
            visible_labels, visible_stats, thin_decorative
        ),
        "unsupported_visible_details": _component_rows(
            visible_labels, visible_stats, outside_restored_support
        ),
    }
    maps = {
        "core": core,
        "connected_core": connected_core,
        "orphan_core": orphan_core_mask,
        "restored_support": restored_support,
        "unsupported_visible": np.isin(visible_labels, outside_restored_support),
        "thin_decorative_visible": np.isin(visible_labels, thin_decorative),
        "structural_bottleneck_visible": np.isin(
            visible_labels, structural_bottleneck_visible
        ),
        "core_labels": core_labels,
    }
    return report, maps


def _render_component_map(
    physical: np.ndarray,
    labels: np.ndarray,
    audit: dict[str, Any],
    output: Path,
    title: str,
) -> None:
    orphan_ids = {row["component"] for row in audit["orphan_details"]}
    rgb = np.full((*physical.shape, 3), 248, dtype=np.uint8)
    rgb[physical] = (74, 116, 156)
    if orphan_ids:
        rgb[np.isin(labels, list(orphan_ids))] = (224, 58, 62)
    image = Image.fromarray(rgb, "RGB")
    canvas = Image.new("RGB", (image.width, image.height + 34), "white")
    canvas.paste(image, (0, 34))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 10), title, fill="black")
    canvas.save(output)


def _render_width_map(
    physical: np.ndarray,
    maps: dict[str, np.ndarray],
    output: Path,
    title: str,
) -> None:
    rgb = np.full((*physical.shape, 3), 248, dtype=np.uint8)
    rgb[physical] = (190, 211, 226)
    rgb[maps["connected_core"]] = (54, 153, 92)
    rgb[maps["orphan_core"]] = (230, 126, 34)
    rgb[maps["thin_decorative_visible"]] = (129, 92, 246)
    rgb[maps["structural_bottleneck_visible"]] = (205, 49, 49)
    image = Image.fromarray(rgb, "RGB")
    canvas = Image.new("RGB", (image.width, image.height + 34), "white")
    canvas.paste(image, (0, 34))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 10), title, fill="black")
    canvas.save(output)


def _render_montage(paths: list[Path], output: Path, columns: int = 4) -> None:
    images = [Image.open(path).convert("RGB") for path in paths]
    if not images:
        return
    thumb_width = 260
    thumb_height = round(images[0].height * thumb_width / images[0].width)
    rows = math.ceil(len(images) / columns)
    canvas = Image.new("RGB", (columns * thumb_width, rows * thumb_height), "white")
    for index, image in enumerate(images):
        thumb = image.resize((thumb_width, thumb_height), Image.Resampling.NEAREST)
        canvas.paste(
            thumb, ((index % columns) * thumb_width, (index // columns) * thumb_height)
        )
    canvas.save(output)


def _audit_saved_plan(
    root: Path, manifest: dict[str, Any], diagnostics: Path, label: str
) -> dict[str, Any]:
    component_dir = diagnostics / f"{label}_edge_components"
    component_dir.mkdir(parents=True, exist_ok=True)
    layers: list[dict[str, Any]] = []
    map_paths: list[Path] = []
    for layer in manifest["layers"]:
        mask = np.asarray(
            Image.open(root / layer["retained_material_mask"]).convert("L")
        ) > 0
        audit, labels = audit_edge_connectivity(mask)
        record = {
            "depth_back_to_front": layer["depth_back_to_front"],
            "id": layer["id"],
            "color": layer["color"],
            **audit,
        }
        layers.append(record)
        path = component_dir / f"{layer['id']}_components.png"
        _render_component_map(
            mask,
            labels,
            audit,
            path,
            f"{label.upper()} depth {layer['depth_back_to_front']} {layer['color']} | 4-neighbour components={audit['four_neighbor_components']} orphans={audit['four_neighbor_orphan_components']}",
        )
        map_paths.append(path)
    _render_montage(map_paths, diagnostics / f"{label}_edge_component_montage.png")
    return {
        "layers": layers,
        "layer_count": len(layers),
        "failed_layer_count": sum(
            layer["four_neighbor_status"] == "FAIL" for layer in layers
        ),
        "orphan_component_count": sum(
            layer["four_neighbor_orphan_components"] for layer in layers
        ),
        "orphan_pixel_count": sum(
            layer["four_neighbor_orphan_pixels"] for layer in layers
        ),
    }


def _save_repaired_plan(
    source: Path,
    case_id: str,
    repaired_root: Path,
    poc: PocConfig,
    strict: OptimizationConfig,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    analysis = analyze_image(source, poc)
    strict_search: dict[str, Any]
    try:
        sequence, graph, search = optimize_region_sequence(analysis["labels"], strict)
        strict_search = {
            "status": "FEASIBLE",
            "sequence_front_to_back": list(sequence),
            "search": search,
        }
        used = strict
    except RuntimeError as error:
        strict_search = {
            "status": "CONFLICT_REQUIRES_USER_DECISION",
            "reason": str(error),
            "sequence_front_to_back": None,
        }
        used = replace(strict, minimum_contact_pixels_override=1)
        sequence, graph, search = optimize_region_sequence(analysis["labels"], used)
        strict_search["edge_connected_fallback"] = {
            "status": "FEASIBLE",
            "sequence_front_to_back": list(sequence),
            "search": search,
            "minimum_contact_pixels": 1,
        }

    provider_labels, providers, _ = _replay_sequence(
        sequence, graph, np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    )
    planned = dict(analysis)
    planned["provider_labels"] = provider_labels
    planned["providers"] = providers
    order = discover_order(planned, poc)
    if order["status"] != "FEASIBLE":
        raise RuntimeError("repaired sequence failed order replay")
    layers = construct_layers(planned, order, poc)
    edge_audits: list[dict[str, Any]] = []
    width_audits: list[dict[str, Any]] = []
    width_maps: list[dict[str, np.ndarray]] = []
    for layer in layers:
        edge, _ = audit_edge_connectivity(layer["physical_mask"])
        layer["connected_components"] = edge["four_neighbor_components"]
        edge_audits.append(edge)
        width, maps = audit_connection_width(
            layer["physical_mask"], layer["visible_aperture"], poc, strict
        )
        width_audits.append(width)
        width_maps.append(maps)

    layer_files = _save_layer_artifacts(repaired_root, layers, planned, poc)
    validation, reconstruction, mismatch = validate_plan(planned, order, layers, poc)
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    Image.fromarray(palette[analysis["labels"]], "RGB").save(
        repaired_root / "previews" / "printable_target.png"
    )
    Image.fromarray(reconstruction, "RGB").save(
        repaired_root / "previews" / "reconstructed_front.png"
    )
    _render_comparison(
        analysis,
        reconstruction,
        mismatch,
        repaired_root / "previews" / "source_difference.png",
    )
    _render_exploded(
        layers, planned, repaired_root / "previews" / "exploded_layer_masks.png"
    )
    manifest = {
        "schema_version": "3.4.0-edge-connectivity-repair-poc",
        "case_id": case_id,
        "status": "AUTOMATIC_EDGE_REPAIRED_UNCORRECTED",
        "source": "../../original/source.png",
        "source_sha256": _sha256(source),
        "construction": {
            "method": "four_neighbour_region_graph_nested_subtractive_masks",
            "common_outer_pixels": [poc.outer_pixels, poc.outer_pixels],
            "continuous_perimeter_pixels": poc.perimeter_pixels,
            "one_color_per_layer": True,
            "visible_connector_fallback": False,
            "visible_pixels_edited": False,
            "cad_generated": False,
        },
        "order_front_to_back_palette_indices": list(sequence),
        "order_back_to_front": [record["id"] for record in layer_files],
        "layers": layer_files,
    }
    _write_json(repaired_root / "automatic" / "layer_stack.json", manifest)
    _write_json(
        repaired_root / "automatic" / "proposal.json",
        {
            "source_sha256": _sha256(source),
            "palette": [
                {"index": index, "hex": color, "pixels": int(analysis["counts"][index])}
                for index, color in enumerate(analysis["palette_hex"])
            ],
            "strict_width_search": strict_search,
            "used_search_config": asdict(used),
            "providers": providers,
            "manual_annotations_used": False,
            "manual_corrections_applied": 0,
            "visible_bridge_fallback": False,
        },
    )
    return (
        {
            "analysis": analysis,
            "layers": layers,
            "layer_files": layer_files,
            "validation": validation,
            "reconstruction": reconstruction,
            "mismatch": mismatch,
            "edge_audits": edge_audits,
            "width_audits": width_audits,
            "width_maps": width_maps,
            "sequence": sequence,
        },
        strict_search,
        providers,
    )


def run_connectivity_repair_case(
    source: Path,
    case_id: str,
    failed_plan: Path,
    output: Path,
    poc: PocConfig = DEFAULT_CONFIG,
    strict: OptimizationConfig = DEFAULT_OPTIMIZATION_CONFIG,
) -> dict[str, Any]:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copytree(failed_plan, output / "failed")
    (output / "original").mkdir()
    shutil.copy2(source, output / "original" / "source.png")
    repaired_root = output / "repaired"
    for directory in ("automatic/layers", "previews", "reports"):
        (repaired_root / directory).mkdir(parents=True, exist_ok=True)
    diagnostics = output / "diagnostics"
    diagnostics.mkdir()

    failed_manifest = json.loads(
        (failed_plan / "automatic" / "layer_stack.json").read_text(encoding="utf-8")
    )
    if failed_manifest["source_sha256"] != _sha256(source):
        raise ValueError("failed plan source hash does not match repair source")
    failed_audit = _audit_saved_plan(
        output / "failed", failed_manifest, diagnostics, "failed"
    )

    repaired, strict_search, providers = _save_repaired_plan(
        source, case_id, repaired_root, poc, strict
    )
    repaired_manifest = json.loads(
        (repaired_root / "automatic" / "layer_stack.json").read_text(encoding="utf-8")
    )
    repaired_audit = _audit_saved_plan(
        repaired_root, repaired_manifest, diagnostics, "repaired"
    )
    width_dir = diagnostics / "repaired_width"
    width_dir.mkdir()
    width_paths: list[Path] = []
    width_layers: list[dict[str, Any]] = []
    for layer, record, width, maps in zip(
        repaired["layers"],
        repaired["layer_files"],
        repaired["width_audits"],
        repaired["width_maps"],
    ):
        row = {
            "depth_back_to_front": record["depth_back_to_front"],
            "id": record["id"],
            "color": record["color"],
            **width,
        }
        width_layers.append(row)
        path = width_dir / f"{record['id']}_width.png"
        _render_width_map(
            layer["physical_mask"],
            maps,
            path,
            f"REPAIRED depth {record['depth_back_to_front']} {record['color']} | width={width['status']} core-orphans={width['orphan_structural_core_components']} unsupported-visible={width['unsupported_visible_components']}",
        )
        width_paths.append(path)
    _render_montage(width_paths, diagnostics / "repaired_width_montage.png")

    prior_reconstruction = np.asarray(
        Image.open(output / "failed" / "previews" / "reconstructed_front.png").convert(
            "RGB"
        )
    )
    repaired_reconstruction = repaired["reconstruction"]
    changed = np.any(prior_reconstruction != repaired_reconstruction, axis=2)
    change_map = np.zeros((*changed.shape, 3), dtype=np.uint8)
    change_map[changed] = (255, 0, 255)
    Image.fromarray(change_map, "RGB").save(
        diagnostics / "composite_changed_pixels.png"
    )

    width_conflict_layers = [
        layer for layer in width_layers if layer["status"] != "PASS"
    ]
    geometry_pass = (
        repaired["validation"]["geometry_mismatched_pixels"] == 0
        and repaired_audit["failed_layer_count"] == 0
    )
    width_pass = not width_conflict_layers
    validation_status = (
        "PASS" if geometry_pass and width_pass else "CONFLICT_REQUIRES_USER_DECISION"
    )
    validation = {
        **repaired["validation"],
        "status": validation_status,
        "review_state": "READY_FOR_USER_TEST",
        "connectivity_definition": {
            "visual_only": "8-neighbour; diagonal corner contacts count and are not structurally accepted",
            "required_edge_connectivity": "4-neighbour; all material shares pixel edges and reaches the common perimeter",
            "width_qualified": _width_parameters(poc, strict),
        },
        "edge_connectivity_status": (
            "PASS" if repaired_audit["failed_layer_count"] == 0 else "FAIL"
        ),
        "minimum_connection_width_status": "PASS" if width_pass else "FAIL",
        "failed_plan_edge_audit": failed_audit,
        "repaired_plan_edge_audit": repaired_audit,
        "repaired_width_layers": width_layers,
        "strict_width_search": strict_search,
        "composite_changed_pixels_from_approved_optimized_view": int(
            np.count_nonzero(changed)
        ),
        "visible_connector_pixels": 0,
        "visible_pixels_edited": 0,
        "manual_corrections_applied": 0,
        "cad_generated": False,
    }
    _write_json(repaired_root / "reports" / "validation.json", validation)

    report = {
        "case_id": case_id,
        "review_state": "READY_FOR_USER_TEST",
        "validation_status": validation_status,
        "root_cause": "The previous implementation extracted same-color regions and validated physical masks with 8-neighbour connected components. Diagonally touching pixels were therefore labeled as one component even though they share no edge and have zero structural width.",
        "failed_plan": {
            "layer_count": failed_audit["layer_count"],
            "four_neighbor_failed_layers": failed_audit["failed_layer_count"],
            "four_neighbor_orphan_components": failed_audit[
                "orphan_component_count"
            ],
            "four_neighbor_orphan_pixels": failed_audit["orphan_pixel_count"],
        },
        "repaired_plan": {
            "layer_count": repaired_audit["layer_count"],
            "four_neighbor_failed_layers": repaired_audit["failed_layer_count"],
            "four_neighbor_orphan_components": repaired_audit[
                "orphan_component_count"
            ],
            "geometry_pixel_accuracy": repaired["validation"][
                "geometry_pixel_accuracy_against_quantized_target"
            ],
            "changed_composite_pixels": int(np.count_nonzero(changed)),
            "visible_connector_pixels": 0,
        },
        "connection_width": {
            **_width_parameters(poc, strict),
            "status": "PASS" if width_pass else "CONFLICT_REQUIRES_USER_DECISION",
            "conflicting_layers": len(width_conflict_layers),
            "orphan_structural_core_components": sum(
                layer["orphan_structural_core_components"] for layer in width_layers
            ),
            "unsupported_visible_components": sum(
                layer["unsupported_visible_components"] for layer in width_layers
            ),
        },
        "strict_width_search": strict_search,
        "hidden_repair_policy": {
            "method": "Replan provider depths with a 4-neighbour region graph, then use the maximal cumulative mask of current and more-front providers as hidden retained material.",
            "visible_bridge_fallback": False,
            "visible_artwork_changes": 0,
            "fixed_plan_additional_hidden_pixels_available": 0,
            "explanation": "Each retained mask already contains every pixel that a more-front layer hides. Adding beyond that maximal allowed mask would occlude a lower visible color and change the approved composite.",
        },
        "order_change": {
            "necessary": failed_audit["failed_layer_count"] > 0,
            "old_layers": failed_audit["layer_count"],
            "new_layers": repaired_audit["layer_count"],
            "new_front_to_back_colors": [provider["color"] for provider in providers],
        },
        "user_decision_options_if_width_conflict_remains": (
            []
            if width_pass
            else [
                "Approve a smaller nominal structural width or smaller safety allowance.",
                "Approve visible geometry widening for the specifically reported regions.",
                "Approve omission/merging of specifically reported sub-resolution details.",
                "Allow separately printable components for named conflicting regions.",
            ]
        ),
        "manual_corrections_applied": 0,
        "cad_generated": False,
    }
    _write_json(output / "reports" / "connectivity_repair.json", report)
    checksum_files = sorted(
        path
        for path in output.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    (output / "reports" / "checksums.sha256").write_text(
        "".join(
            f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_files
        ),
        encoding="utf-8",
    )
    return report
