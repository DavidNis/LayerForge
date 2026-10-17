from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from layerforge_l1 import PROJECT_ROOT


ROOT = PROJECT_ROOT / "artifacts" / "l1_layer_optimization"
BASELINE = PROJECT_ROOT / "artifacts" / "l1_subtractive_poc"
CASES = ("bear_face", "fox_workshop")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _report(case: str) -> dict:
    return _json(ROOT / case / "reports" / "optimization.json")


def _validation(case: str) -> dict:
    return _json(ROOT / case / "optimized" / "reports" / "validation.json")


def _manifest(case: str) -> dict:
    return _json(ROOT / case / "optimized" / "automatic" / "layer_stack.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_optimizer_contains_no_fixture_specific_semantic_rules() -> None:
    source = (
        PROJECT_ROOT / "src" / "layerforge_l1" / "layer_optimization.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in ("bear_face", "fox_workshop", "eye", "mouth", "pikachu"):
        assert forbidden not in source


def test_existing_automatic_proposals_are_preserved_byte_for_byte() -> None:
    for case in CASES:
        for relative in (
            "original/source.png",
            "automatic/proposal.json",
            "automatic/layer_stack.json",
            "previews/reconstructed_front.png",
            "previews/exploded_layer_masks.png",
            "reports/validation.json",
        ):
            assert _sha256(ROOT / case / "before" / relative) == _sha256(
                BASELINE / case / relative
            )


def test_layer_count_and_material_objectives_improve_without_fidelity_loss() -> None:
    bear = _report("bear_face")
    fox = _report("fox_workshop")
    assert bear["before"]["layer_count"] == bear["optimized"]["layer_count"] == 7
    assert bear["optimized"]["total_retained_interior_pixel_layers"] < bear["before"][
        "total_retained_interior_pixel_layers"
    ]
    assert fox["before"]["layer_count"] == 21
    assert fox["optimized"]["layer_count"] < fox["before"]["layer_count"]
    assert fox["optimized"]["near_full_layer_count"] < fox["before"][
        "near_full_layer_count"
    ]
    assert fox["optimized"]["tiny_visible_layer_count"] == 0
    assert fox["optimized"]["minimum_visible_contribution_pixels"] > fox["before"][
        "minimum_visible_contribution_pixels"
    ]
    for report in (bear, fox):
        assert report["optimized"]["geometry_pixel_accuracy_against_printable_target"] == 1.0
        assert report["fidelity_tradeoff"]["pixels_changed_from_printable_target"] == 0
        assert report["fidelity_tradeoff"]["details_removed_for_optimization"] == 0
        assert report["fidelity_tradeoff"]["visible_connector_pixels"] == 0


def test_every_current_repaired_sheet_keeps_the_shared_outline_and_edge_connectivity() -> None:
    repaired_root = PROJECT_ROOT / "artifacts" / "l1_connectivity_repair"
    for case in CASES:
        manifest = _json(
            repaired_root
            / case
            / "repaired"
            / "automatic"
            / "layer_stack.json"
        )
        dimensions = tuple(manifest["construction"]["common_outer_pixels"])
        perimeter = int(manifest["construction"]["continuous_perimeter_pixels"])
        for layer in manifest["layers"]:
            mask = np.asarray(
                Image.open(
                    repaired_root
                    / case
                    / "repaired"
                    / layer["retained_material_mask"]
                ).convert("L")
            ) > 0
            assert mask.shape[::-1] == dimensions
            count, _ = cv2.connectedComponents(
                mask.astype(np.uint8), connectivity=4
            )
            assert count - 1 == 1
            assert np.all(mask[:perimeter])
            assert np.all(mask[-perimeter:])
            assert np.all(mask[:, :perimeter])
            assert np.all(mask[:, -perimeter:])


def test_reconstruction_and_all_quantized_details_are_exactly_retained() -> None:
    for case in CASES:
        report = _report(case)
        validation = _validation(case)
        retention = report["critical_detail_retention"]
        assert validation["status"] == "PASS"
        assert validation["geometry_mismatched_pixels"] == 0
        assert validation["exposed_hidden_material_pixels"] == 0
        assert validation["all_physical_sheets_connected"] is True
        assert validation["all_shared_perimeters_complete"] is True
        assert retention["retention_fraction"] == 1.0
        assert retention["regions_retained_exactly"] == retention["source_regions"]
        before = np.asarray(
            Image.open(ROOT / case / "before" / "previews" / "reconstructed_front.png")
        )
        after = np.asarray(
            Image.open(ROOT / case / "optimized" / "previews" / "reconstructed_front.png")
        )
        assert np.array_equal(before, after)


def test_repeated_color_layers_have_documented_topological_need() -> None:
    bear = _report("bear_face")
    fox = _report("fox_workshop")
    assert bear["one_layer_per_palette_color_audit"]["feasible"] is True
    assert bear["layer_count_optimality"]["global_minimum_proven"] is True
    assert not bear["repeated_color_layer_audit"]
    assert fox["one_layer_per_palette_color_audit"]["feasible"] is False
    assert fox["repeated_color_layer_audit"]
    for audit in fox["repeated_color_layer_audit"]:
        assert audit["occurrence_count"] >= 2
        assert audit["pairwise_merge_attempts"] >= 2
        assert audit["feasible_pairwise_merges"] == 0
        assert audit["locally_necessary_in_selected_order"] is True


def test_thin_feature_constraints_are_preserved_not_silently_corrected() -> None:
    before = _report("fox_workshop")["before"]["unsupported_thin_detail_count"]
    after = _report("fox_workshop")["optimized"]["unsupported_thin_detail_count"]
    assert before == after
    assert after > 0
    assert _report("fox_workshop")["remaining_fabrication_constraints"][
        "requires_user_resolution_before_fabrication"
    ] is True
    assert _validation("fox_workshop")["manual_corrections_applied"] == 0


def test_optimization_scope_contains_no_cad_exports() -> None:
    assert not list(ROOT.rglob("*.stl"))
    assert not list(ROOT.rglob("*.step"))
    assert not list(ROOT.rglob("*.stp"))


def test_review_artifacts_exist_for_before_and_after() -> None:
    for case in CASES:
        root = ROOT / case
        for relative in (
            "original/source.png",
            "before/automatic/layer_stack.json",
            "before/previews/reconstructed_front.png",
            "before/previews/exploded_layer_masks.png",
            "optimized/automatic/layer_stack.json",
            "optimized/previews/reconstructed_front.png",
            "optimized/previews/exploded_layer_masks.png",
            "optimized/previews/source_difference.png",
            "comparison/before_after_reconstructed.png",
            "reports/optimization.json",
            "reports/checksums.sha256",
        ):
            assert (root / relative).stat().st_size > 0
