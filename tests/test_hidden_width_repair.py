from dataclasses import replace

import numpy as np

from layerforge_l1.hidden_width_repair import (
    WidthRepairConfig,
    audit_mask_at_physical_scale,
    compose_back_to_front,
    hidden_addition_domain,
    repair_one_mask,
    validation_poc_config,
)
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG


def _synthetic_hidden_neck() -> tuple[
    object, WidthRepairConfig, np.ndarray, np.ndarray, np.ndarray
]:
    poc = replace(
        DEFAULT_CONFIG,
        analysis_pixels=64,
        outer_pixels=80,
        perimeter_pixels=8,
        sheet_size_mm=20.0,
    )
    repair = WidthRepairConfig(
        physical_size_mm=20.0, validation_scale=2, max_iterations=6
    )
    perimeter = np.ones((80, 80), dtype=bool)
    perimeter[8:-8, 8:-8] = False

    back = perimeter.copy()
    back[8:31, 40] = True
    back[30:51, 30:51] = True
    front = perimeter.copy()
    front[8:31, 37:44] = True
    visible = back[8:-8, 8:-8]
    return poc, repair, back, front, visible


def test_validation_scale_preserves_physical_dimensions() -> None:
    poc = replace(
        DEFAULT_CONFIG,
        analysis_pixels=512,
        outer_pixels=576,
        perimeter_pixels=32,
    )
    high = validation_poc_config(
        poc, WidthRepairConfig(physical_size_mm=200.0, validation_scale=4)
    )
    assert high.sheet_size_mm == 200.0
    assert high.outer_pixels == 2304
    assert high.analysis_pixels == 2048
    assert high.perimeter_pixels == 128
    assert 200.0 / high.outer_pixels < 0.1


def test_hidden_path_widening_repairs_actual_mask_without_changing_composite() -> None:
    poc, repair, back, front, visible = _synthetic_hidden_neck()
    before, _ = audit_mask_at_physical_scale(back, visible, poc, repair=repair)
    assert before["edge"]["four_neighbor_status"] == "PASS"
    assert before["width"]["status"] != "PASS"

    domain = hidden_addition_domain([back, front], 0)
    repaired, result, diagnostics = repair_one_mask(
        back, visible, domain, poc, repair=repair
    )
    assert result["status"] == "PASS"
    assert result["modified_pixels"] > 0
    assert np.all(diagnostics["added"] <= domain)

    palette = np.asarray([[190, 70, 40], [40, 90, 180]], dtype=np.uint8)
    before_labels, before_rgb = compose_back_to_front(
        [back, front], [0, 1], palette
    )
    after_labels, after_rgb = compose_back_to_front(
        [repaired, front], [0, 1], palette
    )
    assert np.array_equal(before_labels, after_labels)
    assert np.array_equal(before_rgb, after_rgb)


def test_repair_refuses_unpermitted_visible_additions() -> None:
    poc, repair, back, _, visible = _synthetic_hidden_neck()
    repaired, result, diagnostics = repair_one_mask(
        back, visible, np.zeros_like(back), poc, repair=repair
    )
    assert np.array_equal(repaired, back)
    assert result["modified_pixels"] == 0
    assert result["status"] != "PASS"
    assert not np.any(diagnostics["added"])
