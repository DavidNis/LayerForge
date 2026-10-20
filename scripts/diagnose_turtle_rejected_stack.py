"""Diagnose the saved turtle CP-SAT candidate without changing validators.

This is an evidence-only helper for the rejected nine-colour, twelve-sheet
candidate.  It reads the masks already emitted by the experimental global
planner and calls the production 4-neighbour and retained-width validators
unchanged.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from layerforge_l1.connectivity_repair import (
    audit_connection_width,
    audit_edge_connectivity,
)
from layerforge_l1.layer_optimization import (
    DEFAULT_OPTIMIZATION_CONFIG,
    build_region_graph,
)
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "artifacts" / "l1_turtle_on_leaf_global_planner_v2"
REJECTED_ROOT = SOURCE_ROOT / "rejected_candidate"
REPORT_PATH = REJECTED_ROOT / "rejected_candidate_report.json"
CHECKPOINT_REPORT_PATH = SOURCE_ROOT / "reports" / "checkpoint_report.json"
TARGET_PATH = SOURCE_ROOT / "automatic" / "indexed_target.png"
OUTPUT = ROOT / "artifacts" / "l1_turtle_on_leaf_structural_diagnosis_v1"


def _read_mask(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(path)
    return image > 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bbox(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return [
        int(xs.min()),
        int(ys.min()),
        int(xs.max() - xs.min() + 1),
        int(ys.max() - ys.min() + 1),
    ]


def _opening_summary(cutout: np.ndarray) -> dict[str, object]:
    p = DEFAULT_CONFIG.perimeter_pixels
    aperture = cutout[p:-p, p:-p].astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(aperture, connectivity=4)
    components = []
    for component in range(1, count):
        x, y, width, height, area = stats[component].tolist()
        components.append(
            {
                "component": component,
                "area_pixels": int(area),
                "bounds_aperture_pixels": [int(x), int(y), int(width), int(height)],
            }
        )
    return {
        "through_cutout_components": count - 1,
        "components": components,
        "validation_scope": (
            "Cutouts are empty openings and are not tested against the 1.2 mm "
            "retained-material width rule. Printable opening width remains separate."
        ),
    }


def _region_ids(mask_outer: np.ndarray, region_map: np.ndarray) -> list[int]:
    p = DEFAULT_CONFIG.perimeter_pixels
    aperture = mask_outer[p:-p, p:-p]
    if not np.any(aperture):
        return []
    return sorted(int(value) for value in np.unique(region_map[aperture]))


def _render_overlay(
    physical: np.ndarray,
    hidden_outer: np.ndarray,
    visible_outer: np.ndarray,
    maps: dict[str, np.ndarray],
    details: list[dict[str, object]],
    color: str,
    title: str,
    destination: Path,
) -> None:
    rgb_color = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
    canvas = np.zeros((*physical.shape, 3), dtype=np.uint8)
    canvas[~physical] = (252, 252, 252)  # actual through-cutout pixels
    canvas[physical] = tuple((np.asarray(rgb_color) * 0.55 + 70).astype(np.uint8))
    canvas[hidden_outer & physical] = (75, 184, 210)
    canvas[visible_outer & physical] = rgb_color
    canvas[maps["connected_core"]] = (45, 170, 80)
    canvas[maps["thin_decorative_visible"]] = (245, 158, 11)
    canvas[maps["structural_bottleneck_visible"]] = (207, 52, 118)
    canvas[maps["orphan_core"]] = (235, 40, 40)

    header = 76
    image = Image.new("RGB", (canvas.shape[1], canvas.shape[0] + header), "white")
    image.paste(Image.fromarray(canvas), (0, header))
    draw = ImageDraw.Draw(image)
    draw.text((8, 7), title, fill="black")
    draw.text(
        (8, 27),
        "red=orphan structural core | magenta=visible bottleneck | green=anchored core",
        fill="black",
    )
    draw.text(
        (8, 45),
        "cyan=hidden retained | orange=thin decorative warning | white=cutout/opening",
        fill="black",
    )
    for detail in details:
        x, y, width, height = (
            int(value) for value in detail["bounds_pixels_at_required_kernel"]
        )
        draw.rectangle(
            (x, y + header, x + max(width - 1, 0), y + header + max(height - 1, 0)),
            outline=(120, 0, 0),
            width=1,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)


def _make_montage(paths: list[Path], destination: Path) -> None:
    images = [Image.open(path).convert("RGB") for path in paths]
    thumb_width = 360
    thumbs = []
    for image in images:
        height = round(image.height * thumb_width / image.width)
        thumbs.append(image.resize((thumb_width, height), Image.Resampling.NEAREST))
    columns = 2
    rows = (len(thumbs) + columns - 1) // columns
    cell_height = max(image.height for image in thumbs)
    montage = Image.new("RGB", (columns * thumb_width, rows * cell_height), "white")
    for index, image in enumerate(thumbs):
        montage.paste(image, ((index % columns) * thumb_width, (index // columns) * cell_height))
    destination.parent.mkdir(parents=True, exist_ok=True)
    montage.save(destination)


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUTPUT}")
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    checkpoint = json.loads(CHECKPOINT_REPORT_PATH.read_text(encoding="utf-8"))
    target = np.asarray(Image.open(TARGET_PATH).convert("RGB"), dtype=np.uint8)
    palette_hex = checkpoint["input"]["palette_hex"]
    palette = np.asarray(
        [[int(color[index : index + 2], 16) for index in (1, 3, 5)] for color in palette_hex],
        dtype=np.uint8,
    )
    labels = np.full(target.shape[:2], -1, dtype=np.int16)
    for palette_index, color in enumerate(palette):
        labels[np.all(target == color, axis=2)] = palette_index
    if np.any(labels < 0):
        raise RuntimeError("indexed target contains RGB values absent from saved planner palette")
    graph = build_region_graph(labels, connectivity=4, minimum_contact_pixels=1)
    groups = [list(map(int, group)) for group in report["front_to_back_region_groups"]]
    region_depth = [int(value) for value in report["region_to_depth"]]
    if len(graph["nodes"]) != len(region_depth):
        raise RuntimeError("saved region assignment does not match the indexed target")

    OUTPUT.mkdir(parents=True)
    overlays: list[Path] = []
    layer_rows: list[dict[str, object]] = []
    rejected_prefixes: list[dict[str, object]] = []
    for layer_file in report["layer_files"]:
        layer_id = layer_file["id"]
        back_index = int(layer_file["depth_back_to_front"])
        front_depth = int(layer_file["provider_index"])
        layer_root = REJECTED_ROOT / Path(layer_file["retained_material_mask"]).parent
        physical = _read_mask(REJECTED_ROOT / layer_file["retained_material_mask"])
        cutout = _read_mask(REJECTED_ROOT / layer_file["through_cutouts_mask"])
        visible_aperture = _read_mask(REJECTED_ROOT / layer_file["source_visible_region_mask"])
        hidden_aperture = _read_mask(REJECTED_ROOT / layer_file["hidden_retained_material_mask"])
        p = DEFAULT_CONFIG.perimeter_pixels
        visible_outer = np.zeros_like(physical)
        hidden_outer = np.zeros_like(physical)
        visible_outer[p:-p, p:-p] = visible_aperture
        hidden_outer[p:-p, p:-p] = hidden_aperture

        edge, _ = audit_edge_connectivity(physical)
        width, maps = audit_connection_width(
            physical,
            visible_aperture,
            DEFAULT_CONFIG,
            DEFAULT_OPTIMIZATION_CONFIG,
        )
        if not np.array_equal(cutout, ~physical):
            raise RuntimeError(f"{layer_id}: cutout mask is not exact physical complement")

        orphan_regions = _region_ids(maps["orphan_core"], graph["region_map"])
        current_regions = set(groups[front_depth])
        prefix_regions = {
            region
            for depth in range(front_depth + 1)
            for region in groups[depth]
        }
        foreground_regions = prefix_regions - current_regions
        visible_contributors = sorted(current_regions.intersection(orphan_regions))
        hidden_contributors = sorted(foreground_regions.intersection(orphan_regions))
        other_regions = sorted(set(orphan_regions) - prefix_regions)
        if other_regions:
            raise RuntimeError(f"{layer_id}: orphan core overlaps non-prefix regions {other_regions}")

        details = width["orphan_structural_core_details"]
        classifications = {
            value: sum(1 for detail in details if detail["classification"] == value)
            for value in (
                "VISIBLE_ARTWORK_STRUCTURAL_BOTTLENECK",
                "HIDDEN_RETAINED_MATERIAL_STRUCTURAL_BOTTLENECK",
            )
        }
        failed = width["status"] != "PASS" or edge["four_neighbor_status"] != "PASS"
        overlay_relative = None
        if failed:
            overlay = OUTPUT / "diagnostic_overlays" / f"{layer_id}_failure_overlay.png"
            _render_overlay(
                physical,
                hidden_outer,
                visible_outer,
                maps,
                details,
                layer_file["color"],
                (
                    f"{layer_id} | physical back index {back_index} | front depth {front_depth} | "
                    f"{layer_file['color']} | orphan cores {width['orphan_structural_core_components']}"
                ),
                overlay,
            )
            overlays.append(overlay)
            overlay_relative = str(overlay.relative_to(OUTPUT)).replace("\\", "/")

        prefix_ids = sorted(prefix_regions)
        prefix_bits = sum(1 << region for region in prefix_ids)
        if failed:
            rejected_prefixes.append(
                {
                    "front_to_back_depth": front_depth,
                    "physical_layer_index_back_to_front": back_index,
                    "exact_prefix_region_ids": prefix_ids,
                    "exact_prefix_bitset_hex": hex(prefix_bits),
                    "no_good_scope": (
                        "forbid exactly this cumulative assigned-region set at this depth; "
                        "other region assignments and orders remain allowed"
                    ),
                }
            )

        layer_rows.append(
            {
                "physical_layer_index_back_to_front": back_index,
                "layer_id": layer_id,
                "front_to_back_depth": front_depth,
                "color": layer_file["color"],
                "retained_mask_path": str(
                    (REJECTED_ROOT / layer_file["retained_material_mask"]).relative_to(ROOT)
                ).replace("\\", "/"),
                "cutout_mask_path": str(
                    (REJECTED_ROOT / layer_file["through_cutouts_mask"]).relative_to(ROOT)
                ).replace("\\", "/"),
                "retained_mask_sha256": _sha256(
                    REJECTED_ROOT / layer_file["retained_material_mask"]
                ),
                "diagnostic_overlay": overlay_relative,
                "four_neighbor": edge,
                "width": width,
                "actual_failure_type": (
                    "NARROW_RETAINED_CONNECTION_CAUSING_ORPHAN_STRUCTURAL_CORES"
                    if width["status"] != "PASS"
                    else (
                        "EDGE_DISCONNECTED_MATERIAL"
                        if edge["four_neighbor_status"] != "PASS"
                        else "PASS"
                    )
                ),
                "failure_location_union_bounds_outer_pixels": _bbox(maps["orphan_core"]),
                "visible_source_region_ids_at_this_depth": sorted(current_regions),
                "earlier_foreground_region_ids_in_retained_prefix": sorted(foreground_regions),
                "orphan_core_overlapping_visible_region_ids": visible_contributors,
                "orphan_core_overlapping_hidden_foreground_region_ids": hidden_contributors,
                "orphan_core_classification_counts": classifications,
                "thin_decorative_warning_only": {
                    "count": width["thin_decorative_visible_components"],
                    "details": width["thin_decorative_visible_details"],
                    "affects_pass_fail": False,
                },
                "cutouts": _opening_summary(cutout),
                "interpretation": (
                    "The retained mask is edge-connected, but one or more cores surviving "
                    "the corrected 7x7 elliptical erosion cannot reach the shared perimeter. "
                    "This is a retained-material width failure, not an opening-width failure."
                    if width["status"] != "PASS" and edge["four_neighbor_status"] == "PASS"
                    else "No retained-material structural failure."
                ),
                "potential_validator_false_positive": (
                    "No behavior mismatch found: the saved mask is evaluated by the unchanged "
                    "configured validator. Component count can fragment a single narrow network "
                    "into many orphan cores, but each reported core is still disconnected after "
                    "the required erosion. Physical strength beyond this raster proxy is unvalidated."
                    if width["status"] != "PASS"
                    else "None observed."
                ),
                "source_layer_directory": str(layer_root.relative_to(ROOT)).replace("\\", "/"),
            }
        )

    if overlays:
        _make_montage(overlays, OUTPUT / "diagnostic_overlays" / "all_failures_montage.png")

    failing = [row for row in layer_rows if row["actual_failure_type"] != "PASS"]
    result = {
        "status": "DIAGNOSIS_COMPLETE_STACK_REMAINS_INVALID",
        "input_evidence": {
            "indexed_target": str(TARGET_PATH.relative_to(ROOT)).replace("\\", "/"),
            "indexed_target_sha256": _sha256(TARGET_PATH),
            "saved_rejected_report": str(REPORT_PATH.relative_to(ROOT)).replace("\\", "/"),
            "palette": palette_hex,
            "source_region_count": len(graph["nodes"]),
            "physical_layer_count": len(layer_rows),
            "front_to_back_color_order": report["front_to_back_colors"],
        },
        "validator_contract": {
            "four_neighbor": "physical material must be a single edge-connected sheet",
            "retained_width": "7x7 elliptical erosion for configured 1.2 mm plus conversion allowances",
            "pass_fail_basis": "orphan structural cores only",
            "thin_decorative_details": "warning only",
            "through_cutouts": "not subjected to retained-material width validation",
        },
        "summary": {
            "failing_physical_layer_count": len(failing),
            "failing_back_to_front_indices": sorted(
                int(row["physical_layer_index_back_to_front"]) for row in failing
            ),
            "edge_disconnected_sheet_count": sum(
                row["four_neighbor"]["four_neighbor_status"] != "PASS" for row in layer_rows
            ),
            "width_failing_sheet_count": sum(row["width"]["status"] != "PASS" for row in layer_rows),
            "orphan_structural_core_components_total": sum(
                int(row["width"]["orphan_structural_core_components"]) for row in layer_rows
            ),
            "orphan_structural_core_pixels_total": sum(
                int(row["width"]["orphan_structural_core_pixels"]) for row in layer_rows
            ),
        },
        "rejected_exact_prefixes": sorted(
            rejected_prefixes, key=lambda row: int(row["front_to_back_depth"])
        ),
        "layers_back_to_front": sorted(
            layer_rows, key=lambda row: int(row["physical_layer_index_back_to_front"])
        ),
        "conclusion": (
            "The candidate is visually exact and all sheets are 4-neighbour connected, but eight "
            "cumulative retained masks contain structural cores without a >=1.2 mm path to the "
            "perimeter. These exact prefixes may be safely excluded without excluding other depth "
            "assignments. This does not establish image infeasibility."
        ),
    }
    (OUTPUT / "reports").mkdir(parents=True, exist_ok=True)
    (OUTPUT / "reports" / "diagnosis.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["summary"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
