from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from layerforge_l1 import PROJECT_ROOT
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG, analyze_image


ROOT = PROJECT_ROOT / "artifacts" / "l1_fox_simple_review_v1"


def _mask(relative: str) -> np.ndarray:
    return np.asarray(Image.open(ROOT / "two_d_plan" / relative).convert("L")) > 0


def test_direct_simple_fox_package_uses_exact_input_and_stops_before_cad() -> None:
    summary = json.loads(
        (ROOT / "reports" / "validation_summary.json").read_text(encoding="utf-8")
    )
    source = PROJECT_ROOT / "fox_simple_input.png"
    assert summary["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert summary["simplification_run"] is False
    assert summary["status"] == "READY_FOR_USER_TEST"
    assert summary["cad_export"]["status"] == "NOT_RUN_DUE_TO_2D_STRUCTURAL_GATE_FAILURE"
    assert not list(ROOT.rglob("*.stl"))
    assert not list(ROOT.rglob("*.step"))


def test_all_masks_share_perimeter_and_are_four_neighbor_connected() -> None:
    report = json.loads((ROOT / "two_d_validation.json").read_text(encoding="utf-8"))
    shapes: set[tuple[int, int]] = set()
    p = DEFAULT_CONFIG.perimeter_pixels
    for layer in report["layers"]:
        retained = _mask(layer["retained_material_mask"])
        shapes.add(retained.shape)
        assert retained[:p].all()
        assert retained[-p:].all()
        assert retained[:, :p].all()
        assert retained[:, -p:].all()
        components, _ = cv2.connectedComponents(retained.astype(np.uint8), connectivity=4)
        assert components - 1 == 1
    assert shapes == {(DEFAULT_CONFIG.outer_pixels, DEFAULT_CONFIG.outer_pixels)}


def test_independent_mask_composition_exactly_matches_indexed_target() -> None:
    report = json.loads((ROOT / "two_d_validation.json").read_text(encoding="utf-8"))
    analysis = analyze_image(PROJECT_ROOT / "fox_simple_input.png")
    palette = np.asarray(analysis["palette_rgb"], dtype=np.uint8)
    composed = np.full((DEFAULT_CONFIG.outer_pixels, DEFAULT_CONFIG.outer_pixels, 3), 247, dtype=np.uint8)
    for layer in report["layers"]:
        color = layer["color"]
        rgb = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
        composed[_mask(layer["retained_material_mask"])] = rgb
    p = DEFAULT_CONFIG.perimeter_pixels
    expected = palette[np.asarray(analysis["labels"])]
    assert np.array_equal(composed[p:-p, p:-p], expected)


def test_width_failure_is_not_silently_promoted_to_valid() -> None:
    summary = json.loads(
        (ROOT / "reports" / "validation_summary.json").read_text(encoding="utf-8")
    )
    validation = summary["validation"]
    assert validation["minimum_structural_width_mm"] == 1.2
    assert validation["minimum_width_failed_layers"] == 11
    assert validation["orphan_structural_core_components"] == 78
    assert summary["planner"]["planner_status"] == "UNRESOLVED_PLAN_REQUIRES_USER_DECISION"
