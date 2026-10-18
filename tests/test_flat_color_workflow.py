from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from layerforge_l1 import PROJECT_ROOT
from layerforge_l1.flat_color_workflow import (
    DEFAULT_FLAT_COLOR_CONFIG,
    _primary_subject_protection,
    flatten_art_cells,
    run_flat_color_review,
)
from layerforge_l1.subtractive_poc import analyze_image


def test_missing_reference_stops_before_generation(tmp_path: Path) -> None:
    source = PROJECT_ROOT / "fixtures" / "l1_auto" / "development_fox_workshop.png"
    with pytest.raises(FileNotFoundError, match="missing annotated reference"):
        run_flat_color_review(source, tmp_path / "missing.png", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_art_cell_flattening_preserves_boundaries_and_is_not_global_palette_reduction() -> None:
    image = np.full((40, 60, 3), (242, 236, 210), dtype=np.uint8)
    image[8:32, 8:28] = (40, 45, 55)
    for column in range(30, 54):
        image[8:32, column] = (40 + (column - 30) * 5, 150, 180)
    flattened, boundary, changes, summary = flatten_art_cells(
        image, DEFAULT_FLAT_COLOR_CONFIG
    )
    assert flattened.shape == image.shape
    assert boundary.shape == image.shape[:2]
    assert changes
    assert "no global palette-count reduction" in summary["method"]
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    dark_drawn_boundary = boundary & (gray < DEFAULT_FLAT_COLOR_CONFIG.dark_ink_luma)
    assert np.array_equal(
        flattened[dark_drawn_boundary], image[dark_drawn_boundary]
    )


def test_generated_review_is_unapproved_and_uses_reference_as_evidence_only() -> None:
    root = PROJECT_ROOT / "artifacts" / "l1_flat_color_workflow_v1"
    report_path = root / "reports" / "flat_color_review.json"
    if not report_path.exists():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["review_state"] == "READY_FOR_USER_TEST"
    assert report["milestone"] == "L1_UNAPPROVED"
    assert report["automatic_candidate_selection"] is None
    assert report["reference"]["usage"] == "ANNOTATION_EVIDENCE_ONLY"
    assert report["reference"]["pixels_used_for_candidate_generation"] is False
    assert report["settings"]["same_planner_settings_for_original_and_both_candidates"] is True
    assert not list(root.rglob("*.stl"))
    assert not list(root.rglob("*.step"))


def test_fox_primary_protection_covers_split_head_and_body_features() -> None:
    source = PROJECT_ROOT / "fixtures" / "l1_auto" / "development_fox_workshop.png"
    analysis = analyze_image(source)
    protected, evidence = _primary_subject_protection(
        analysis["labels"], analysis["palette_rgb"]
    )
    # Regression coordinates exercise the generic salient-box chaining: an
    # eye in the upper subject box and an overall fastener in the body box.
    assert protected[150, 227]
    assert protected[249, 171]
    assert "split head/body" in evidence["method"]


def test_generated_physical_masks_have_shared_full_perimeters() -> None:
    root = PROJECT_ROOT / "artifacts" / "l1_flat_color_workflow_v1"
    for candidate in ("flat_color", "shadow_box_optimized"):
        report_path = root / "reports" / f"{candidate}_planner.json"
        if not report_path.exists():
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        shapes: set[tuple[int, int]] = set()
        for layer in report["layers"]:
            path = root / "candidates" / candidate / "actual_plan" / layer[
                "retained_material_mask"
            ]
            mask = np.asarray(Image.open(path).convert("L")) > 0
            shapes.add(mask.shape)
            assert mask[0].all()
            assert mask[-1].all()
            assert mask[:, 0].all()
            assert mask[:, -1].all()
        assert len(shapes) == 1


def test_generated_candidates_have_distinct_operation_scopes() -> None:
    root = PROJECT_ROOT / "artifacts" / "l1_flat_color_workflow_v1"
    flat_path = root / "candidates" / "flat_color" / "edit_manifest.json"
    optimized_path = (
        root / "candidates" / "shadow_box_optimized" / "edit_manifest.json"
    )
    if not flat_path.exists() or not optimized_path.exists():
        return
    flat = json.loads(flat_path.read_text(encoding="utf-8"))
    optimized = json.loads(optimized_path.read_text(encoding="utf-8"))
    flat_operations = {row["operation"] for row in flat["edits"]}
    optimized_operations = {row["operation"] for row in optimized["edits"]}
    assert "FLATTEN_OBJECT_SHADING" in flat_operations or (
        "MERGE_SAME_OBJECT_TONAL_REGIONS" in flat_operations
    )
    assert optimized["inherits"] == "FLAT-COLOR"
    assert optimized_operations <= {
        "REMOVE_NONESSENTIAL_WORKSHOP_CLUTTER",
        "SIMPLIFY_SECONDARY_INTERIOR_DETAIL",
    }
    assert np.asarray(
        Image.open(root / "candidates" / "flat_color" / "artwork.png")
    ).shape == np.asarray(
        Image.open(root / "candidates" / "shadow_box_optimized" / "artwork.png")
    ).shape
