import json
from pathlib import Path

import numpy as np
from PIL import Image

from layerforge_l1.hidden_width_repair import (
    WidthRepairConfig,
    audit_mask_at_physical_scale,
)
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "l1_turtle_post_layer_width_repair_200mm_v1"
SOURCE_LAYERS = (
    ROOT
    / "artifacts"
    / "l1_turtle_final_global_planning_200mm_v1"
    / "rejected_best_candidate"
    / "automatic"
    / "layers"
)
REPAIR = WidthRepairConfig(
    physical_size_mm=225.0,
    validation_scale=4,
    max_iterations=16,
)


def _mask(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L")) > 0


def test_repaired_turtle_stack_meets_retained_width_and_sheet_rules() -> None:
    report = json.loads(
        (ARTIFACT / "reports" / "width_repair_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == "READY_FOR_USER_TEST"
    assert report["scale"]["artwork_mm"] == [200.0, 200.0]
    assert report["scale"]["outer_sheet_mm"] == [225.0, 225.0]
    assert report["validation"]["remaining_orphan_cores"] == 0
    assert report["validation"]["thin_decorative_visible_component_warnings"] == 0

    p = DEFAULT_CONFIG.perimeter_pixels
    perimeter = np.ones((576, 576), dtype=bool)
    perimeter[p:-p, p:-p] = False
    for row in report["layers"]:
        layer = ARTIFACT / "layers" / row["id"]
        retained = _mask(layer / "retained_material_mask.png")
        cutouts = _mask(layer / "through_cutouts_mask.png")
        assert retained.shape == (576, 576)
        assert np.array_equal(cutouts, ~retained)
        assert np.all(retained[perimeter])

        source = next(SOURCE_LAYERS.glob(f"layer_{row['index_back_to_front']:02d}_*"))
        visible = _mask(source / "source_visible_region_mask.png")
        audit, _ = audit_mask_at_physical_scale(
            retained,
            visible,
            repair=REPAIR,
        )
        assert audit["edge"]["four_neighbor_status"] == "PASS"
        assert audit["width"]["status"] == "PASS"
        assert audit["width"]["orphan_structural_core_components"] == 0
        assert audit["width"]["thin_decorative_visible_components"] == 0


def test_repaired_preview_matches_actual_repaired_masks() -> None:
    report = json.loads(
        (ARTIFACT / "reports" / "width_repair_report.json").read_text(
            encoding="utf-8"
        )
    )
    composite = np.full((576, 576, 3), 247, dtype=np.uint8)
    for row in report["layers"]:
        retained = _mask(
            ARTIFACT / "layers" / row["id"] / "retained_material_mask.png"
        )
        value = row["color"].lstrip("#")
        color = tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
        composite[retained] = color
    p = DEFAULT_CONFIG.perimeter_pixels
    composite = composite[p:-p, p:-p]
    preview = np.asarray(
        Image.open(ARTIFACT / "previews" / "repaired_stack.png").convert("RGB")
    )
    target = np.asarray(
        Image.open(ARTIFACT / "previews" / "locked_target.png").convert("RGB")
    )
    assert np.array_equal(composite, preview)
    changed = int(np.count_nonzero(np.any(target != preview, axis=2)))
    assert changed == report["visible_result"]["changed_pixels"]
