from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

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
