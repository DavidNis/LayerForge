from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from layerforge_l1 import PROJECT_ROOT
from layerforge_l1.subtractive_poc import discover_order


ROOT = PROJECT_ROOT / "artifacts" / "l1_subtractive_poc"
CASES = ("bear_face", "fox_workshop")


def _report(case: str) -> dict:
    return json.loads((ROOT / case / "reports" / "validation.json").read_text(encoding="utf-8"))


def _manifest(case: str) -> dict:
    return json.loads((ROOT / case / "automatic" / "layer_stack.json").read_text(encoding="utf-8"))


def test_poc_has_no_fixture_specific_semantic_rules() -> None:
    source = (PROJECT_ROOT / "src" / "layerforge_l1" / "subtractive_poc.py").read_text(
        encoding="utf-8"
    ).lower()
    for forbidden in ("bear_face", "fox_workshop", "eye", "mouth", "pikachu"):
        assert forbidden not in source


def test_both_automatic_mask_composites_exactly_match_printable_target() -> None:
    for case in CASES:
        report = _report(case)
        assert report["status"] == "PASS"
        assert report["geometry_pixel_accuracy_against_quantized_target"] == 1.0
        assert report["geometry_mismatched_pixels"] == 0
        assert report["visible_automatic_connector_pixels"] == 0
        assert report["exposed_hidden_material_pixels"] == 0
        assert report["manual_corrections_applied"] == 0


def test_every_sheet_has_identical_outline_complete_perimeter_and_one_component() -> None:
    repaired_root = PROJECT_ROOT / "artifacts" / "l1_connectivity_repair"
    for case in CASES:
        manifest = json.loads(
            (
                repaired_root
                / case
                / "repaired"
                / "automatic"
                / "layer_stack.json"
            ).read_text(encoding="utf-8")
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


def test_bear_dark_details_are_revealed_through_yellow_cutouts() -> None:
    manifest = _manifest("bear_face")
    report = _report("bear_face")
    dark = min(manifest["layers"], key=lambda layer: sum(int(layer["color"][i : i + 2], 16) for i in (1, 3, 5)))
    yellow = max(
        manifest["layers"],
        key=lambda layer: int(layer["color"][1:3], 16) + int(layer["color"][3:5], 16) - int(layer["color"][5:7], 16),
    )
    assert dark["depth_back_to_front"] < yellow["depth_back_to_front"]
    relationship = next(
        row
        for row in report["reveal_relationships"]
        if row["lower_depth"] == dark["depth_back_to_front"]
        and row["upper_depth"] == yellow["depth_back_to_front"]
    )
    assert relationship["fully_revealed_through_upper_cutout"] is True
    dark_visible = np.asarray(
        Image.open(ROOT / "bear_face" / dark["source_visible_region_mask"]).convert("L")
    ) > 0
    yellow_cutouts = np.asarray(
        Image.open(ROOT / "bear_face" / yellow["through_cutouts_mask"]).convert("L")
    ) > 0
    p = int(manifest["construction"]["continuous_perimeter_pixels"])
    assert np.all(yellow_cutouts[p:-p, p:-p][dark_visible])


def test_fox_uses_repeated_color_depths_instead_of_visible_bridges() -> None:
    manifest = _manifest("fox_workshop")
    colors = [layer["color"] for layer in manifest["layers"]]
    assert len(colors) > len(set(colors))
    assert manifest["construction"]["visible_connector_fallback"] is False
    assert _report("fox_workshop")["visible_automatic_connector_pixels"] == 0


def test_fox_printability_conflicts_are_reported_without_silent_correction() -> None:
    report = _report("fox_workshop")
    assert report["unsupported_thin_detail_count"] > 0
    assert report["manual_corrections_required"] is True
    assert report["manual_corrections_applied"] == 0
    assert report["unsupported_thin_details"]


def test_infeasible_order_reports_conflict_instead_of_adding_a_bridge() -> None:
    providers = np.ones((9, 9), dtype=np.uint8)
    providers[3:6, 3:6] = 0
    analysis = {
        "provider_labels": providers,
        "providers": [
            {"index": 0, "palette_index": 0, "color": "#111111"},
            {"index": 1, "palette_index": 1, "color": "#EEEEEE"},
        ],
    }
    result = discover_order(analysis)
    assert result["status"] == "INFEASIBLE"
    assert result["invalid_depths"]


def test_proof_contains_no_cad_exports() -> None:
    assert not list(ROOT.rglob("*.stl"))
    assert not list(ROOT.rglob("*.step"))


def test_required_visual_review_artifacts_exist() -> None:
    for case in CASES:
        root = ROOT / case
        for relative in (
            "original/source.png",
            "automatic/proposal.json",
            "automatic/layer_stack.json",
            "previews/printable_target.png",
            "previews/reconstructed_front.png",
            "previews/source_difference.png",
            "previews/exploded_layer_masks.png",
            "reports/validation.json",
            "reports/correction_log.json",
        ):
            assert (root / relative).stat().st_size > 0

