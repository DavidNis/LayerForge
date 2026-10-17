from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from layerforge_l1 import PROJECT_ROOT


ROOT = PROJECT_ROOT / "artifacts" / "l1_connectivity_repair"
FAILED_SOURCE = PROJECT_ROOT / "artifacts" / "l1_layer_optimization"
CASES = ("bear_face", "fox_workshop")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _report(case: str) -> dict:
    return _json(ROOT / case / "reports" / "connectivity_repair.json")


def _validation(case: str) -> dict:
    return _json(ROOT / case / "repaired" / "reports" / "validation.json")


def _manifest(case: str, version: str = "repaired") -> dict:
    return _json(ROOT / case / version / "automatic" / "layer_stack.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_repair_code_contains_no_fixture_specific_semantic_rules() -> None:
    source = (
        PROJECT_ROOT / "src" / "layerforge_l1" / "connectivity_repair.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in ("bear_face", "fox_workshop", "eye", "mouth", "pikachu"):
        assert forbidden not in source


def test_failed_plan_is_preserved_byte_for_byte() -> None:
    for case in CASES:
        for relative in (
            "automatic/proposal.json",
            "automatic/layer_stack.json",
            "previews/reconstructed_front.png",
            "previews/exploded_layer_masks.png",
            "reports/validation.json",
        ):
            assert _sha256(ROOT / case / "failed" / relative) == _sha256(
                FAILED_SOURCE / case / "optimized" / relative
            )


def test_independent_four_neighbor_audit_reproduces_reported_fox_defect() -> None:
    report = _report("fox_workshop")
    validation = _validation("fox_workshop")
    assert report["failed_plan"]["four_neighbor_failed_layers"] == 11
    assert report["failed_plan"]["four_neighbor_orphan_components"] == 113
    failed_layers = validation["failed_plan_edge_audit"]["layers"]
    layer12 = next(row for row in failed_layers if row["depth_back_to_front"] == 12)
    layer15 = next(row for row in failed_layers if row["depth_back_to_front"] == 15)
    assert layer12["four_neighbor_orphan_components"] == 15
    assert layer15["four_neighbor_orphan_components"] == 40
    assert layer12["eight_neighbor_components"] == 1
    assert layer15["eight_neighbor_components"] == 1


def test_repaired_masks_are_single_edge_connected_sheets_with_shared_perimeter() -> None:
    for case in CASES:
        manifest = _manifest(case)
        dimensions = tuple(manifest["construction"]["common_outer_pixels"])
        perimeter = int(manifest["construction"]["continuous_perimeter_pixels"])
        for layer in manifest["layers"]:
            mask = np.asarray(
                Image.open(ROOT / case / "repaired" / layer["retained_material_mask"]).convert(
                    "L"
                )
            ) > 0
            assert mask.shape[::-1] == dimensions
            count, _ = cv2.connectedComponents(mask.astype(np.uint8), connectivity=4)
            assert count - 1 == 1
            assert np.all(mask[:perimeter])
            assert np.all(mask[-perimeter:])
            assert np.all(mask[:, :perimeter])
            assert np.all(mask[:, -perimeter:])
        report = _report(case)
        assert report["repaired_plan"]["four_neighbor_failed_layers"] == 0
        assert report["repaired_plan"]["four_neighbor_orphan_components"] == 0


def test_repair_does_not_change_the_approved_composite_or_add_visible_bridges() -> None:
    for case in CASES:
        report = _report(case)
        validation = _validation(case)
        failed = np.asarray(
            Image.open(ROOT / case / "failed" / "previews" / "reconstructed_front.png")
        )
        repaired = np.asarray(
            Image.open(ROOT / case / "repaired" / "previews" / "reconstructed_front.png")
        )
        assert np.array_equal(failed, repaired)
        assert report["repaired_plan"]["changed_composite_pixels"] == 0
        assert report["repaired_plan"]["geometry_pixel_accuracy"] == 1.0
        assert report["repaired_plan"]["visible_connector_pixels"] == 0
        assert validation["visible_pixels_edited"] == 0
        assert validation["geometry_mismatched_pixels"] == 0


def test_width_gate_includes_explicit_conversion_safety_and_never_false_passes() -> None:
    bear = _report("bear_face")
    fox = _report("fox_workshop")
    for report in (bear, fox):
        width = report["connection_width"]
        assert width["nominal_connection_width_mm"] == 0.8
        assert width["vectorization_safety_mm"] == 0.2
        assert width["cad_conversion_safety_mm"] == 0.2
        assert width["enforced_connection_width_mm"] == 1.2
        assert width["enforced_connection_width_pixels"] == 7
    assert bear["validation_status"] == "PASS"
    assert bear["connection_width"]["status"] == "PASS"
    assert fox["validation_status"] == "CONFLICT_REQUIRES_USER_DECISION"
    assert fox["connection_width"]["status"] == "CONFLICT_REQUIRES_USER_DECISION"
    assert fox["strict_width_search"]["status"] == "CONFLICT_REQUIRES_USER_DECISION"
    assert fox["connection_width"]["conflicting_layers"] > 0
    assert _validation("fox_workshop")["minimum_connection_width_status"] == "FAIL"


def test_failed_and_repaired_component_maps_cover_problem_layers_12_and_15() -> None:
    failed = _manifest("fox_workshop", "failed")
    for depth in (12, 15):
        layer = next(row for row in failed["layers"] if row["depth_back_to_front"] == depth)
        component_map = (
            ROOT
            / "fox_workshop"
            / "diagnostics"
            / "failed_edge_components"
            / f"{layer['id']}_components.png"
        )
        assert component_map.stat().st_size > 0
    for relative in (
        "diagnostics/failed_edge_component_montage.png",
        "diagnostics/repaired_edge_component_montage.png",
        "diagnostics/repaired_width_montage.png",
        "diagnostics/composite_changed_pixels.png",
    ):
        assert (ROOT / "fox_workshop" / relative).stat().st_size > 0


def test_no_cad_or_external_frame_artifacts_were_generated() -> None:
    assert not list(ROOT.rglob("*.stl"))
    assert not list(ROOT.rglob("*.step"))
    assert not list(ROOT.rglob("*.stp"))


def test_review_state_is_ready_without_claiming_fox_validation_pass() -> None:
    assert _report("bear_face")["review_state"] == "READY_FOR_USER_TEST"
    assert _report("fox_workshop")["review_state"] == "READY_FOR_USER_TEST"
    assert _report("fox_workshop")["validation_status"] != "PASS"
