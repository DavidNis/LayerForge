from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import cadquery as cq
import numpy as np
import trimesh

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
OUTPUTS = PROJECT_ROOT / "artifacts" / "l1_auto"
CASES = ("development_fox_workshop", "heldout_octopus_submarine")


def _manifest(case: str) -> dict:
    return json.loads((OUTPUTS / case / "layer_stack.json").read_text(encoding="utf-8"))


def _report(case: str) -> dict:
    return json.loads((OUTPUTS / case / "reports" / "validation.json").read_text(encoding="utf-8"))


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


def test_strict_shared_perimeter_rejects_tampering() -> None:
    manifest = _manifest("development_fox_workshop")
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


def test_manual_correction_keeps_automatic_manifest_immutable(tmp_path: Path) -> None:
    automatic = OUTPUTS / "development_fox_workshop" / "automatic" / "layer_stack.json"
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


def test_both_unannotated_complex_images_preserve_auto_and_final_versions() -> None:
    for case in CASES:
        root = OUTPUTS / case
        report = _report(case)
        assert report["status"] == "READY_FOR_USER_TEST"
        assert report["manual_annotations_used"] is False
        assert report["automatic_proposal_preserved"] is True
        assert report["manual_corrections_count"] == 0
        assert (root / "original" / "source.png").is_file()
        assert (root / "automatic" / "proposal.json").is_file()
        assert (root / "automatic" / "layer_stack.json").is_file()
        assert (root / "layer_stack.json").is_file()
        assert sha256(root / "automatic" / "layer_stack.json") == json.loads(
            (root / "reports" / "correction_log.json").read_text()
        )["automatic_manifest_sha256"]


def test_complex_development_case_exercises_details_and_overlaps() -> None:
    evaluation = _report("development_fox_workshop")["evaluation"]
    proposal = json.loads(
        (OUTPUTS / "development_fox_workshop" / "automatic" / "proposal.json").read_text()
    )
    assert len(proposal["palette"]) == 8
    assert evaluation["source_components_at_or_above_threshold"] >= 100
    assert evaluation["comparison_basis"].endswith("exported STL footprints")
    assert evaluation["visible_connector_artifact_pixels"] > 0
    assert evaluation["missing_detail_pixels"] > 0


def test_exported_step_stl_and_common_bounds_for_both_cases() -> None:
    for case in CASES:
        root = OUTPUTS / case
        report = _report(case)
        assert len(report["layers"]) == 8
        for row in report["layers"]:
            step = cq.importers.importStep(str(root / row["step"])).val()
            mesh = trimesh.load_mesh(root / row["stl"], process=True)
            assert row["status"] == "PASS"
            assert step.isValid() and len(step.Solids()) == 1
            assert isinstance(mesh, trimesh.Trimesh) and mesh.is_watertight
            assert row["planned_components"] == row["stl_components"] == 1
            assert row["planned_shared_sheet"]["pass"] is True
            assert row["stl_shared_sheet"]["pass"] is True
            assert row["step_outer_bounds_max_delta_mm"] <= 0.001
            assert np.allclose(row["step_xy_bounds_mm"], [5.0, 5.0, 105.0, 105.0], atol=0.001)


def test_all_required_stl_derived_previews_and_reports_exist() -> None:
    preview_names = (
        "assembled_front.png",
        "assembled_side.png",
        "exploded_layers.png",
        "source_comparison_overlay.png",
    )
    for case in CASES:
        root = OUTPUTS / case
        for name in preview_names:
            assert (root / "previews" / name).stat().st_size > 1_000
        assert (root / "reports" / "evaluation.json").is_file()
        assert (root / "reports" / "validation.json").is_file()
        assert (root / "reports" / "print_and_stack_guide.md").is_file()


def test_software_printability_checks_are_explicit() -> None:
    for case in CASES:
        report = _report(case)
        checks = report["printability"]
        assert checks["common_outer_dimensions_pass"] is True
        assert checks["structural_connector_width_mm"] >= 1.2
        assert checks["connector_width_at_least_three_nozzle_widths"] is True
        assert checks["conservative_full_sheet_erosion_pass_count"] + checks["conservative_full_sheet_erosion_warning_count"] == 8
        assert all(row["fits_220x220_mm_bed"] for row in report["layers"])

