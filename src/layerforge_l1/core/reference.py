"""Regression comparison of a regenerated stack against an accepted reference.

Reference cases are listed in ``fixtures/reference_stacks.json`` (paths are
relative to the repository root).  Comparison is geometric: decoded mask bits,
per-sheet colors and order, grid dimensions, connectivity and validator
results, and, for CAD, polygon/hole/vertex counts, solid validity, and the
symmetric-difference area between the new and accepted STL footprints.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .model import SheetStack, import_planner_output, mask_bits_sha256
from .planning import PlanningConfig

CAD_COUNT_FIELDS = ("polygons", "holes", "vertices", "step_solids", "stl_watertight", "stl_components", "stl_bounds_mm")


def load_reference(registry: Path, name: str) -> dict[str, Any]:
    cases = json.loads(Path(registry).read_text(encoding="utf-8"))
    if name not in cases:
        raise KeyError(f"unknown reference {name!r}; known: {sorted(cases)}")
    case = dict(cases[name])
    case["name"] = name
    case["planning_config"] = PlanningConfig(**case["planning"])
    return case


def compare_stack_to_baseline(stack: SheetStack, baseline: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    """Compare a stack and its validation report with a recorded baseline."""

    by_id = {row["id"]: row for row in baseline["sheets_front_to_back"]}
    checks: dict[str, Any] = {
        "sheet_count": [len(stack.sheets), baseline["sheet_count"]],
        "front_to_back_colors": [[s.color for s in stack.front_to_back], baseline["front_to_back_colors"]],
        "outer_pixels": [stack.grid.outer_pixels, baseline["grid"]["outer_pixels"]],
        "perimeter_pixels": [stack.grid.perimeter_pixels, baseline["grid"]["perimeter_pixels"]],
        "outer_sheet_mm": [stack.grid.outer_sheet_mm, baseline["grid"]["outer_sheet_mm"]],
        "reconstruction_mismatched_pixels": [
            validation["reconstruction"].get("mismatched_pixels"), baseline["reconstruction_mismatched_pixels"]
        ],
    }
    rows = {row["id"]: row for row in validation["sheets"]}
    sheet_diffs = {}
    for sheet in stack.sheets:
        ref = by_id.get(sheet.id)
        got = rows[sheet.id]
        if ref is None:
            sheet_diffs[sheet.id] = "not in baseline"
            continue
        pairs = {
            "retained_bits_sha256": [mask_bits_sha256(sheet.retained), ref["retained_bits_sha256"]],
            "visible_bits_sha256": [mask_bits_sha256(sheet.visible), ref["visible_bits_sha256"]],
            "retained_pixels": [int(sheet.retained.sum()), ref["retained_pixels"]],
            "retained_components_4": [got["retained_components_4"], ref["retained_components_4"]],
            "cutout_holes": [got["cutout_holes"], ref["cutout_holes"]],
            "four_neighbor": [got["four_neighbor"], ref["four_neighbor"]],
            "width_status": [got["width_status"], ref["width_status"]],
            "orphan_structural_cores": [got["orphan_structural_cores"], ref["orphan_structural_cores"]],
            "shared_perimeter_complete": [got["shared_perimeter_complete"], ref["shared_perimeter_complete"]],
        }
        bad = {k: v for k, v in pairs.items() if v[0] != v[1]}
        if bad:
            sheet_diffs[sheet.id] = bad
    differences = {k: v for k, v in checks.items() if v[0] != v[1]}
    return {
        "identical": not differences and not sheet_diffs,
        "stack_differences": differences,
        "sheet_differences": sheet_diffs,
        "checked": sorted(checks) + ["per-sheet: mask bits, pixel count, components, holes, validator results"],
    }


def compare_masks_to_accepted(stack: SheetStack, accepted_layers: Path) -> dict[str, Any]:
    """Pixel comparison with the committed accepted planner masks."""

    accepted = import_planner_output(accepted_layers, outer_sheet_mm=stack.grid.outer_sheet_mm)
    rows = {}
    for mine, theirs in zip(stack.sheets, accepted.sheets):
        rows[mine.id] = {
            "accepted_id": theirs.id,
            "retained_px_diff": int((mine.retained != theirs.retained).sum()),
            "visible_px_diff": int((mine.visible != theirs.visible).sum()),
        }
    return {
        "sheet_count": [len(stack.sheets), len(accepted.sheets)],
        "ids_equal": [s.id for s in stack.sheets] == [s.id for s in accepted.sheets],
        "sheets": rows,
        "identical": len(stack.sheets) == len(accepted.sheets)
        and all(r["retained_px_diff"] == 0 and r["visible_px_diff"] == 0 for r in rows.values())
        and [s.id for s in stack.sheets] == [s.id for s in accepted.sheets],
    }


def compare_cad_to_accepted(
    stack: SheetStack, cad_manifest: dict[str, Any], cad_dir: Path, baseline: dict[str, Any], accepted_cad: Path
) -> dict[str, Any]:
    """Counts vs the baseline CAD manifest and STL footprint XOR area vs accepted STL."""

    from .cad import stl_footprint

    reference = baseline["cad"]["layers"]
    rows = {}
    for row in cad_manifest["layers"]:
        ref = reference.get(row["layer"])
        if ref is None:
            rows[row["layer"]] = {"error": "not in baseline"}
            continue
        mine = stl_footprint(Path(cad_dir) / "stl" / f"{row['layer']}.stl")
        theirs = stl_footprint(Path(accepted_cad) / "stl" / f"{row['layer']}.stl")
        rows[row["layer"]] = {
            "count_differences": {k: [row[k], ref[k]] for k in CAD_COUNT_FIELDS if row[k] != ref[k]},
            "stl_footprint_xor_mm2": round(float(mine.symmetric_difference(theirs).area), 9),
            "stl_footprint_area_mm2": round(float(mine.area), 4),
        }
    identical = all(
        "error" not in r and not r["count_differences"] and r["stl_footprint_xor_mm2"] < 1e-6 for r in rows.values()
    ) and len(rows) == len(reference)
    return {"identical": identical, "layers": rows, "thickness_mm": [cad_manifest["assembly_back_to_front"][0]["thickness_mm"], baseline["cad"]["thickness_mm"]]}


def determinism(stack_a: SheetStack, stack_b: SheetStack) -> bool:
    return [s.id for s in stack_a.sheets] == [s.id for s in stack_b.sheets] and all(
        np.array_equal(a.retained, b.retained) and np.array_equal(a.visible, b.visible)
        for a, b in zip(stack_a.sheets, stack_b.sheets)
    )
