from __future__ import annotations

from layerforge_l1 import PROJECT_ROOT


# Report-reading tests for the deleted artifacts/l1_layer_optimization tree were
# removed in L0; see tests/REMOVED_TESTS.md.


def test_optimizer_contains_no_fixture_specific_semantic_rules() -> None:
    source = (
        PROJECT_ROOT / "src" / "layerforge_l1" / "layer_optimization.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in ("bear_face", "fox_workshop", "eye", "mouth", "pikachu"):
        assert forbidden not in source
