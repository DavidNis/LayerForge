from __future__ import annotations

from layerforge_l1 import PROJECT_ROOT


# Report-reading tests for the deleted artifacts/l1_connectivity_repair tree were
# removed in L0; see tests/REMOVED_TESTS.md. The edge/width audits used by the
# engine are covered synthetically in test_l0_core.py and test_reveal_planner.py.


def test_repair_code_contains_no_fixture_specific_semantic_rules() -> None:
    source = (
        PROJECT_ROOT / "src" / "layerforge_l1" / "connectivity_repair.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in ("bear_face", "fox_workshop", "eye", "mouth", "pikachu"):
        assert forbidden not in source
