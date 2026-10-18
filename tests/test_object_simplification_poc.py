from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from layerforge_l1 import PROJECT_ROOT
from layerforge_l1.object_simplification_poc import discover_object_groups
from layerforge_l1.subtractive_poc import analyze_image


FOX = PROJECT_ROOT / "fixtures" / "l1_auto" / "development_fox_workshop.png"


def test_group_discovery_is_generic_and_reports_uncertainty() -> None:
    analysis = analyze_image(FOX)
    heat = np.ones(analysis["labels"].shape, dtype=np.int32)
    groups, protected, _ = discover_object_groups(
        analysis["labels"], np.asarray(analysis["palette_rgb"]), heat
    )
    assert groups
    assert protected.shape == analysis["labels"].shape
    assert all("uncertainty" in group for group in groups)
    assert all("importance_estimate_0_1" in group for group in groups)
    assert any(group["secondary_candidate"] for group in groups)


def test_proof_implements_one_high_level_operation_not_tiny_cleanup() -> None:
    source = (
        PROJECT_ROOT / "src" / "layerforge_l1" / "object_simplification_poc.py"
    ).read_text(encoding="utf-8")
    assert '"operation": "REMOVE_SECONDARY_OBJECT"' in source
    assert "REMOVE_TINY_UNPROTECTED_DECORATION" not in source


def test_generated_proof_has_exact_mask_reconstruction_and_pending_approval() -> None:
    root = PROJECT_ROOT / "artifacts" / "l1_object_simplification_poc_v1"
    report_path = root / "fox_workshop" / "reports" / "object_simplification_proof.json"
    if not report_path.exists():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["automatic_approval"] is False
    assert report["edited_plan"]["exact_candidate_composite"] is True
    assert report["edited_plan"]["four_neighbor_failed_layers"] == 0
    assert report["edited_plan"]["visible_automatic_bridge_pixels"] == 0
    assert report["protected_indexed_pixels_changed"] == 0
    assert report["rgb_quality"]["protected_rgb_changed_pixels"] == 0
    assert not list(root.rglob("*.stl"))
    assert not list(root.rglob("*.step"))


def test_generated_edit_is_object_level_and_has_measured_structural_benefit() -> None:
    root = PROJECT_ROOT / "artifacts" / "l1_object_simplification_poc_v1"
    manifest_path = root / "fox_workshop" / "candidate" / "edit_manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    deltas = manifest["planner_result"]["deltas_vs_current_baseline"]
    assert manifest["operation"] == "REMOVE_SECONDARY_OBJECT"
    assert manifest["original_group_area_pixels"] > 5_000
    assert deltas["orphan_structural_core_components"] < 0
    assert deltas["orphan_structural_core_pixels"] < 0
    assert deltas["physical_layer_count"] <= 0
    assert manifest["status"] == "PROPOSED_AWAITING_USER_APPROVAL"


def test_edited_plan_masks_share_shape_and_complete_outer_perimeter() -> None:
    root = PROJECT_ROOT / "artifacts" / "l1_object_simplification_poc_v1"
    report_path = root / "fox_workshop" / "reports" / "edited_planner.json"
    if not report_path.exists():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    shapes: set[tuple[int, int]] = set()
    for layer in report["layers"]:
        path = root / "fox_workshop" / "candidate" / "actual_plan" / layer[
            "retained_material_mask"
        ]
        mask = np.asarray(Image.open(path).convert("L")) > 0
        shapes.add(mask.shape)
        assert mask[0, :].all()
        assert mask[-1, :].all()
        assert mask[:, 0].all()
        assert mask[:, -1].all()
    assert len(shapes) == 1


def test_bear_regression_remains_valid() -> None:
    path = (
        PROJECT_ROOT
        / "artifacts"
        / "l1_object_simplification_poc_v1"
        / "bear_face"
        / "reports"
        / "direct_planner.json"
    )
    if not path.exists():
        return
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["planner"]["planner_status"] == "VALID_COMMON_PERIMETER_STACK_FOUND"
    assert report["planner"]["minimum_width_failed_layers"] == 0
