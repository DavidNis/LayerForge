from __future__ import annotations

import numpy as np
import json

from layerforge_l1 import PROJECT_ROOT
from layerforge_l1.shadowbox_simplification import POLICIES, generate_candidate
from layerforge_l1.subtractive_poc import analyze_image


FOX = PROJECT_ROOT / "fixtures" / "l1_auto" / "development_fox_workshop.png"


def test_three_policies_are_progressive_permissions_not_resolution_presets() -> None:
    assert [policy.name for policy in POLICIES] == ["LIGHT", "BALANCED", "AGGRESSIVE"]
    assert [policy.remove_area_pixels for policy in POLICIES] == sorted(
        policy.remove_area_pixels for policy in POLICIES
    )
    assert len({policy.maximum_similar_delta_lab for policy in POLICIES}) == 3


def test_candidates_keep_resolution_palette_and_protected_features() -> None:
    analysis = analyze_image(FOX)
    labels = analysis["labels"]
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    changed_counts = []
    for policy in POLICIES:
        candidate, changes, report = generate_candidate(labels, palette, policy)
        assert candidate.shape == labels.shape
        assert set(np.unique(candidate)).issubset(set(np.unique(labels)))
        assert report["protected_changed_pixels"] == 0
        assert all(change["protected_feature_overlap_pixels"] == 0 for change in changes)
        changed_counts.append(report["changed_pixels"])
    assert changed_counts == sorted(changed_counts)


def test_change_records_have_structural_reason_and_measurement_reference() -> None:
    analysis = analyze_image(FOX)
    _, changes, _ = generate_candidate(
        analysis["labels"], np.asarray(analysis["palette_rgb"]), POLICIES[1]
    )
    assert changes
    required = {
        "original_region_id",
        "specific_visual_change",
        "why_selected",
        "intended_shadow_box_constraint",
        "measured_result_ref",
    }
    assert all(required.issubset(change) for change in changes)


def test_implementation_has_no_fixture_or_facial_feature_hardcoding() -> None:
    source = (
        PROJECT_ROOT / "src" / "layerforge_l1" / "shadowbox_simplification.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in ("fox_workshop", "bear_face", "eye", "mouth", "pikachu"):
        assert forbidden not in source


def test_generated_review_package_preserves_protected_features_and_does_not_select() -> None:
    root = PROJECT_ROOT / "artifacts" / "l1_controlled_simplification_v5"
    if not root.exists():
        return
    report = json.loads(
        (root / "fox_workshop" / "reports" / "controlled_simplification.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["automatic_selection"] is None
    assert report["final_layer_plan"] is None
    assert len(report["candidates"]) == 3
    for candidate in report["candidates"]:
        assert candidate["protected_critical_feature_changed_pixels"] == 0
        assert candidate["planner"]["exact_candidate_composite"] is True
        assert candidate["planner"]["four_neighbor_failed_layers"] == 0
        assert candidate["planner"]["visible_automatic_bridge_pixels"] == 0
        assert candidate["user_approval"] == "PENDING"
    assert not list(root.rglob("*.stl"))
    assert not list(root.rglob("*.step"))


def test_bear_direct_case_is_valid_under_same_actual_planner() -> None:
    path = (
        PROJECT_ROOT
        / "artifacts"
        / "l1_controlled_simplification_v5"
        / "bear_face"
        / "reports"
        / "direct_planner.json"
    )
    if not path.exists():
        return
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["classification"] == "DIRECT"
    assert report["planner"]["planner_status"] == "VALID_COMMON_PERIMETER_STACK_FOUND"
    assert report["planner"]["minimum_width_failed_layers"] == 0
