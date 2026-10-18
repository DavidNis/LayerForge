from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from layerforge_l1 import PROJECT_ROOT
from layerforge_l1.suitability import assess_analysis, simplify_image
from layerforge_l1.subtractive_poc import analyze_image


BEAR = PROJECT_ROOT / "fixtures" / "l1_subtractive_poc" / "bear_face.png"
FOX = PROJECT_ROOT / "fixtures" / "l1_auto" / "development_fox_workshop.png"


def test_generic_assessment_classifies_bear_direct_and_fox_simplify_first() -> None:
    assert assess_analysis(analyze_image(BEAR))["classification"] == "DIRECT"
    assert assess_analysis(analyze_image(FOX))["classification"] == "SIMPLIFY_FIRST"


def test_simplification_is_deterministic_and_preserves_output_contract(tmp_path: Path) -> None:
    first, first_report = simplify_image(FOX)
    second, second_report = simplify_image(FOX)
    assert np.array_equal(first, second)
    assert first.shape == (512, 512, 3)
    assert first_report == second_report
    assert first_report["actual_palette_colors"] <= 8
    assert first_report["manual_corrections_applied"] == 0


def test_suitability_source_has_no_fixture_specific_semantic_rules() -> None:
    source = (PROJECT_ROOT / "src" / "layerforge_l1" / "suitability.py").read_text(encoding="utf-8").lower()
    for forbidden in ("bear_face", "fox_workshop", "eye", "mouth", "pikachu"):
        assert forbidden not in source


def test_review_artifacts_are_pre_layer_only() -> None:
    root = PROJECT_ROOT / "artifacts" / "l1_suitability_poc_v1"
    if not root.exists():
        return
    assert not list(root.rglob("*.stl"))
    assert not list(root.rglob("*.step"))
    for case in ("bear_face", "fox_workshop"):
        report_path = root / case / "reports" / "suitability.json"
        assert report_path.is_file()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["final_layer_plan"] is None
        assert report["final_layer_plan_status"] == "NOT_GENERATED_IN_THIS_PRE_LAYER_TASK"


def test_fox_candidate_is_visibly_distinct_and_preserved() -> None:
    root = PROJECT_ROOT / "artifacts" / "l1_suitability_poc_v1" / "fox_workshop"
    if not root.exists():
        return
    original = np.asarray(Image.open(root / "original" / "source.png").convert("RGB").resize((512, 512)))
    candidate = np.asarray(Image.open(root / "simplified" / "candidate.png").convert("RGB"))
    assert not np.array_equal(original, candidate)
    report = json.loads((root / "reports" / "suitability.json").read_text(encoding="utf-8"))
    assert report["simplification"]["automatic_result_preserved"] is True
    assert report["simplification"]["review_status"] == "AWAITING_USER_APPROVAL"
