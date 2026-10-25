from __future__ import annotations

import numpy as np

from layerforge_l1 import PROJECT_ROOT
from layerforge_l1.subtractive_poc import discover_order


# Report-reading tests for the deleted artifacts/l1_subtractive_poc tree were
# removed in L0; see tests/REMOVED_TESTS.md.


def test_poc_has_no_fixture_specific_semantic_rules() -> None:
    source = (PROJECT_ROOT / "src" / "layerforge_l1" / "subtractive_poc.py").read_text(
        encoding="utf-8"
    ).lower()
    for forbidden in ("bear_face", "fox_workshop", "eye", "mouth", "pikachu"):
        assert forbidden not in source


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
