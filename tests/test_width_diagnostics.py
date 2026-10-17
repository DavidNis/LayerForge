from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from layerforge_l1 import PROJECT_ROOT
from layerforge_l1.connectivity_repair import _width_parameters
from layerforge_l1.layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG


ROOT = PROJECT_ROOT / "artifacts" / "l1_width_diagnostics_v2"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _width_report(case: str) -> dict:
    return _json(ROOT / case / "reports" / "corrected_width_validation.json")


def test_physical_width_maps_to_a_centered_seven_pixel_kernel() -> None:
    parameters = _width_parameters(DEFAULT_CONFIG, DEFAULT_OPTIMIZATION_CONFIG)
    assert parameters["enforced_connection_width_mm"] == 1.2
    assert 6.9 < parameters["requested_connection_width_pixels"] < 7.0
    assert parameters["enforced_connection_width_pixels"] == 7
    assert parameters["morphology_kernel_size_pixels"] == 7
    assert parameters["morphology_kernel_radius_pixels"] == 3


def test_corrected_audit_separates_decoration_from_structural_failures() -> None:
    fox = _width_report("fox_workshop")
    before = fox["before_incorrect_9px"]["summary"]
    after = fox["after_corrected_7px"]["summary"]
    assert before["orphan_structural_core_components"] == 137
    assert before["visible_components_outside_restored_support"] == 57
    assert after["orphan_structural_core_components"] == 84
    assert after["visible_components_outside_restored_support"] == 15
    assert after["thin_decorative_visible_components"] == 70
    assert after["structural_bottleneck_visible_components"] == 18
    assert after["conflicting_layer_count"] == 14


def test_problematic_fox_layers_12_and_15_remain_explicit_failures() -> None:
    layers = _width_report("fox_workshop")["after_corrected_7px"]["layers"]
    layer12 = next(row for row in layers if row["depth_back_to_front"] == 12)
    layer15 = next(row for row in layers if row["depth_back_to_front"] == 15)
    assert layer12["status"] == "CONFLICT_REQUIRES_USER_DECISION"
    assert layer12["orphan_structural_core_components"] == 9
    assert layer15["status"] == "CONFLICT_REQUIRES_USER_DECISION"
    assert layer15["orphan_structural_core_components"] == 12
    for layer in (layer12, layer15):
        for detail in layer["orphan_structural_core_details"]:
            assert detail["largest_passing_kernel_pixels"] in (1, 3, 5)
            assert detail["discrete_width_shortfall_pixels"] > 0
            assert (ROOT / "fox_workshop" / "diagnostics" / "after_corrected_7px" / detail["failure_map"]).is_file()


def test_bear_control_passes_and_all_exported_mask_composites_are_unchanged() -> None:
    bear = _width_report("bear_face")
    fox = _width_report("fox_workshop")
    assert bear["after_corrected_7px"]["summary"]["conflicting_layer_count"] == 0
    assert bear["reconstruction"]["changed_pixels_against_prior_approved_composite"] == 0
    assert fox["reconstruction"]["changed_pixels_against_prior_approved_composite"] == 0
    for case in ("bear_face", "fox_workshop"):
        changed = np.asarray(
            Image.open(ROOT / case / "previews" / "reconstruction_changed_pixels.png")
        )
        assert not np.any(changed)


def test_fixed_order_hidden_fill_is_exhausted_but_global_infeasibility_is_not_claimed() -> None:
    investigation = _json(
        ROOT
        / "fox_workshop"
        / "reports"
        / "ordering_and_support_investigation.json"
    )
    assert investigation["fixed_order_hidden_support"]["safe_hidden_pixels_available"] == 0
    assert investigation["strict_all_region_7px_search"]["status"] == "NO_PLAN_WITHIN_BOUNDS"
    assert not investigation["strict_all_region_7px_search"]["global_infeasibility_proven"]
    assert investigation["thin_decoration_exempt_7px_search"]["status"] == "CANDIDATE_FOUND"
    assert investigation["thin_decoration_exempt_7px_search"]["same_sequence_as_current"]
    assert not investigation["infeasibility_claim"]["globally_proven"]


def test_every_unresolved_core_has_a_measurement_and_review_map() -> None:
    report = _width_report("fox_workshop")
    bottlenecks = report["unresolved_structural_bottlenecks"]
    assert len(bottlenecks) == 84
    for detail in bottlenecks:
        assert detail["required_kernel_pixels"] == 7
        assert detail["largest_passing_kernel_mm"] < detail["required_kernel_mm"]
        assert detail["fixed_order_hidden_fill_resolution"] == "UNAVAILABLE_NESTED_MASK_ALREADY_MAXIMAL"


def test_package_contains_no_cad_or_frontend_outputs() -> None:
    assert not list(ROOT.rglob("*.stl"))
    assert not list(ROOT.rglob("*.step"))
    assert not list(ROOT.rglob("*.stp"))
    assert not list(ROOT.rglob("*.tsx"))

