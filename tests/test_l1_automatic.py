from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from layerforge_l1 import PROJECT_ROOT
from layerforge_l1.auto_pipeline import (
    DEFAULT_CONFIG,
    apply_corrections,
    proposal_manifest,
    propose,
)
from layerforge_l1.geometry import layer_area, validate_shared_sheet
from layerforge_l1.pipeline import sha256


FIXTURES = PROJECT_ROOT / "fixtures" / "l1_auto"


@pytest.fixture(scope="module")
def automatic_manifest(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write a freshly generated automatic proposal manifest for the committed source."""

    source = FIXTURES / "development_fox_workshop.png"
    path = tmp_path_factory.mktemp("l1_auto") / "layer_stack.json"
    path.write_text(json.dumps(proposal_manifest(source, propose(source), "generic_test")), encoding="utf-8")
    return path


def test_auto_algorithm_has_no_evaluation_fixture_branches() -> None:
    code = (PROJECT_ROOT / "src" / "layerforge_l1" / "auto_pipeline.py").read_text(encoding="utf-8")
    assert "development_fox_workshop" not in code
    assert "heldout_octopus_submarine" not in code


def test_automatic_proposal_is_deterministic() -> None:
    source = FIXTURES / "development_fox_workshop.png"
    first = propose(source)
    second = propose(source)
    assert first["analysis"]["palette_hex"] == second["analysis"]["palette_hex"]
    assert np.array_equal(first["analysis"]["labels"], second["analysis"]["labels"])
    assert first["selected"]["order"] == second["selected"]["order"]
    assert first["candidate_scores"] == second["candidate_scores"]


def test_proposal_has_one_color_per_registered_physical_sheet() -> None:
    source = FIXTURES / "development_fox_workshop.png"
    manifest = proposal_manifest(source, propose(source), "generic_test")
    assert len(manifest["layers"]) == DEFAULT_CONFIG.palette_size
    expected = [5.0, 5.0, 105.0, 105.0]
    for order, layer in enumerate(manifest["layers"]):
        assert layer["order_back_to_front"] == order
        assert layer["assembly_transform"] != layer["print_transform"]
        check = validate_shared_sheet(manifest, layer)
        assert check["pass"] is True
        assert check["outer_bounds_mm"] == expected
        assert len(layer["color"]["hex"]) == 7


def test_strict_shared_perimeter_rejects_tampering(automatic_manifest: Path) -> None:
    manifest = json.loads(automatic_manifest.read_text(encoding="utf-8"))
    broken = deepcopy(manifest["layers"][1])
    broken["geometry"]["additive_regions"] = [
        region for region in broken["geometry"]["additive_regions"]
        if region["role"] != "shared_outer_perimeter"
    ]
    assert validate_shared_sheet(manifest, broken)["pass"] is False
    resized = deepcopy(manifest["layers"][1])
    perimeter = next(
        region for region in resized["geometry"]["additive_regions"]
        if region["role"] == "shared_outer_perimeter"
    )
    perimeter["shape"]["bounds_mm"][2] -= 2.0
    assert validate_shared_sheet(manifest, resized)["pass"] is False


def test_manual_correction_keeps_automatic_manifest_immutable(automatic_manifest: Path, tmp_path: Path) -> None:
    automatic = automatic_manifest
    before = sha256(automatic)
    manifest = json.loads(automatic.read_text(encoding="utf-8"))
    order = [layer["id"] for layer in reversed(manifest["layers"])]
    corrections = tmp_path / "corrections.json"
    corrections.write_text(json.dumps({"operations": [{"type": "reorder_layers", "layer_ids_back_to_front": order}]}), encoding="utf-8")
    final = tmp_path / "layer_stack.json"
    result = apply_corrections(automatic, corrections, final)
    assert sha256(automatic) == before == result["automatic_manifest_sha256"]
    assert [layer["id"] for layer in json.loads(final.read_text())["layers"]] == order
    assert len(result["corrections_applied"]) == 1
