from __future__ import annotations

import numpy as np

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
