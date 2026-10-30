"""Physical resizing: findings -> required size, printer limit, preview with proportional checks.

Inputs are project-owned: hand-made finding rows, the sailboat fixture (fixtures/l3_print_kit)
planned on a deliberately small sheet, and the ``_face`` drawing with 4 px eye highlights
(tests/test_refinement_and_details.py).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from layerforge_l1.core.intake import IntakeConfig, prepare_target
from layerforge_l1.core.model import StackGrid, load_stack
from layerforge_l1.core.planning import PlanningConfig
from layerforge_l1.core.printkit import PrintProcess, assess_printability
from layerforge_l1.core.propose import ProposalConfig, propose
from layerforge_l1.core.resize import (BOUNDARY_FAILED, READY, issue_requirements, obstruction_issues, predict,
                                       preview_resize, size_options, usable_sheet_mm)

from test_refinement_and_details import WHITE, _face

ROOT = Path(__file__).resolve().parents[1]
SAILBOAT = ROOT / "fixtures" / "l3_print_kit" / "sailboat_512.png"
PROCESS = PrintProcess()


def _feature(kind, severity, width):
    return {"kind": kind, "severity": severity, "width_mm": width, "sheet_id": "layer_01", "at_mm": [1.0, 2.0]}


def test_requirements_follow_the_unchanged_thresholds() -> None:
    rows = issue_requirements([
        _feature("opening_may_close", "UNSUPPORTED", 0.3),
        _feature("narrow_slot_or_gap", "RISK", 0.6),
        _feature("thin_material", "UNSUPPORTED", 0.35),
        _feature("narrow_neck_in_opening", "RISK", None),
        _feature("exceeds_build_volume", "UNSUPPORTED", None),
        _feature("height_not_layer_multiple", "INFO", None),
    ], PROCESS, 0.1)
    assert len(rows) == 5  # INFO is not an issue
    opening, gap, material, neck, volume = rows
    # Half a pixel of each measured width is not trusted: (0.3 - 0.05) mm must reach the nozzle (0.4 mm).
    assert opening["factor_supported"] == pytest.approx(0.4 / 0.25) and opening["factor_clear"] == pytest.approx(0.8 / 0.25)
    assert gap["factor_supported"] == 1.0 and gap["factor_clear"] == pytest.approx(0.8 / 0.55)
    assert material["factor_supported"] == pytest.approx(0.42 / 0.3) and material["factor_clear"] == pytest.approx(0.84 / 0.3)
    assert not neck["size_related"] and not volume["size_related"] and "worse" in volume["note"]
    assert math.isinf(issue_requirements([_feature("opening_may_close", "UNSUPPORTED", 0.04)], PROCESS, 0.1)[0]["factor_supported"])


def test_smallest_useful_size_within_the_printer() -> None:
    issues = issue_requirements([_feature("opening_may_close", "UNSUPPORTED", 0.3),
                                 _feature("narrow_slot_or_gap", "UNSUPPORTED", 0.35),
                                 _feature("narrow_slot_or_gap", "RISK", 0.7)], PROCESS, 0.1)
    options = size_options(issues, 100.0, 256.0)
    rec = options["recommended"]
    assert rec["sheet_mm"] == 160.0 and rec["UNSUPPORTED"] == 0  # ceil(100 * 0.4 / 0.25)
    assert all(r["UNSUPPORTED"] > 0 for r in options["table"] if r["sheet_mm"] < 160.0)
    assert rec["RISK"] == 2 and options["clears_most_risks"] is None  # 320 / 267 mm would clear them: too large
    assert all(r["sheet_mm"] <= 256.0 for r in options["table"])
    # Nothing fits: no recommendation, and the unfixable findings are named.
    tight = size_options(issues, 100.0, 120.0)
    assert tight["recommended"] is None and len(tight["unsupported_not_fixable_within_printer"]) == 2
    # Prediction at the recommended ratio.
    pred = predict(issues, rec["factor"])
    assert [r["id"] for r in pred["improved"]] == [issues[0]["id"], issues[1]["id"]]
    assert [r["id"] for r in pred["cleared"]] == [issues[2]["id"]] and pred["remaining"] == []
    assert pred["improved"][0]["predicted_width_mm"] == pytest.approx(0.3 * 1.6) and pred["improved"][0]["predicted"] == "RISK"


def test_usable_area_includes_bambu_keep_out_zones() -> None:
    plain = usable_sheet_mm(PrintProcess(bed_x_mm=220.0, bed_y_mm=250.0))
    assert plain["max_sheet_mm"] == 220.0
    a1mini = usable_sheet_mm(PROCESS, "Bambu Lab A1 mini 0.4 nozzle")
    assert a1mini["max_sheet_mm"] < 180.0 and len(a1mini["basis"]) == 2
    with pytest.raises(Exception, match="unsupported Bambu printer"):
        usable_sheet_mm(PROCESS, "Prusa MK4")


def test_planning_obstructions_report_width_against_the_structural_width() -> None:
    target = np.zeros((40, 40, 3), np.uint8)
    target[10:12, 5:35] = 255                     # a 2 px (0.5 mm) line
    obstruction = np.zeros((40, 40), bool)
    obstruction[10:12, 5:35] = True
    (row,) = obstruction_issues(obstruction, target, 0.25, 1.2)
    assert row["width_mm"] == 0.5 and row["factor_supported"] == pytest.approx(1.2 / (0.5 - 0.125)) and row["size_related"]


def test_small_highlights_survive_on_a_larger_sheet() -> None:
    for mm in (150.0, 220.0):
        prepared = prepare_target(_face(), None, StackGrid.for_planner(mm, 2), IntakeConfig(max_colors=4))
        for cx in (420, 604):
            box = prepared["target_rgb"][352:356, cx + 12:cx + 16]
            assert (box == np.array(WHITE)).all(-1).sum() >= 8, mm  # never silently removed


@pytest.fixture(scope="module")
def small_sailboat(tmp_path_factory):
    root = tmp_path_factory.mktemp("resize")
    result = propose(SAILBOAT, root / "p", IntakeConfig(), ProposalConfig(planning=PlanningConfig(outer_sheet_mm=60.0, scale=1),
                                                                          export_cad=False))
    stack = load_stack(root / "p" / "candidates" / result["selected"] / "stack")
    report = assess_printability(stack, PROCESS)
    issues = issue_requirements(report["features"], PROCESS, stack.grid.mm_per_pixel)
    limit = usable_sheet_mm(PROCESS)
    analysis = {"analysis_id": "test", "issues": issues, "limit": limit, "options": size_options(issues, 60.0, limit["max_sheet_mm"])}
    return root, analysis, report


def test_preview_scales_every_length_and_keeps_the_pixels(small_sailboat) -> None:
    root, analysis, report = small_sailboat
    assert report["counts"]["UNSUPPORTED"] + report["counts"]["RISK"] > 0
    size = (analysis["options"]["recommended"] or {"sheet_mm": 90.0})["sheet_mm"]
    view = preview_resize(SAILBOAT, root / "p", root / "v", size, analysis, IntakeConfig())
    assert view["status"] == READY and view["boundaries"]["pass"], view["boundaries"]
    ratio = size / 60.0
    assert view["ratio"] == pytest.approx(ratio, abs=1e-4)
    assert view["dimensions_new"]["sheet_mm"] == [size, size]
    assert view["dimensions_new"]["artwork_aperture_mm"] == pytest.approx(view["dimensions_now"]["artwork_aperture_mm"] * ratio, abs=0.01)
    assert view["dimensions_new"]["perimeter_band_mm"] == pytest.approx(view["dimensions_now"]["perimeter_band_mm"] * ratio, abs=0.01)
    assert view["dimensions_new"]["sheet_thickness_mm"] == view["dimensions_now"]["sheet_thickness_mm"]
    assert sum(len(v) for v in view["prediction"].values()) == len(analysis["issues"])
    assert json.loads((root / "v" / "preview.json").read_text(encoding="utf-8"))["to_mm"] == size
    too_big = preview_resize(SAILBOAT, root / "p", root / "w", 300.0, analysis, IntakeConfig())
    failed = {c["check"] for c in too_big["boundaries"]["checks"] if not c["pass"]}
    assert too_big["status"] == BOUNDARY_FAILED and failed == {"sheet fits the printer's usable area"}


def test_trial_search_bisects_to_the_smallest_planning_size() -> None:
    from layerforge_l1.core.resize import search_plannable_size

    tried = []

    def plan(size):  # stand-in for the real planner: plans from 173 mm
        tried.append(size)
        return {"sheet_mm": size, "plans": size >= 173, "seconds": 0.0}

    found = search_plannable_size(plan, 100.0, 256.0, step_mm=10.0)
    assert tried[0] == 256.0 and found["smallest_plannable_mm"] - 10.0 < 173 <= found["smallest_plannable_mm"]
    assert len(tried) <= 6 and [r["sheet_mm"] for r in found["trials"]] == sorted(tried)
    nothing = search_plannable_size(lambda s: {"sheet_mm": s, "plans": False, "seconds": 0.0}, 100.0, 256.0)
    assert nothing["smallest_plannable_mm"] is None and len(nothing["trials"]) == 1  # the largest size failed: stop
