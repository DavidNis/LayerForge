from __future__ import annotations

from pathlib import Path

import numpy as np

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
