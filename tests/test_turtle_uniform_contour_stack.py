import json
from pathlib import Path

import numpy as np
from PIL import Image

from layerforge_l1.hidden_width_repair import (
    WidthRepairConfig,
    audit_mask_at_physical_scale,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "l1_turtle_uniform_contour_stack_200mm_v1"
WIDTH = WidthRepairConfig(physical_size_mm=225.0, validation_scale=4)


def _mask(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L")) > 0


def test_uniform_contour_stack_reopens_and_passes_geometry() -> None:
    report = json.loads(
        (ARTIFACT / "reports" / "uniform_contour_stack_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == "READY_FOR_USER_TEST"
    assert report["reconstruction"]["matches_uniform_contour_target"] is True
    assert report["reconstruction"]["visible_connector_pixels"] == 0
    assert report["validation"]["total_orphan_cores"] == 0
    assert report["validation"]["thin_visible_retained_components"] == 0
    assert report["validation"]["all_layers_valid"] is True

    for row in report["layers"]:
        directory = ARTIFACT / "layers" / row["id"]
        retained = _mask(directory / "retained_material_mask.png")
        cutouts = _mask(directory / "through_cutouts_mask.png")
        visible = _mask(directory / "source_visible_region_mask.png")
        assert np.array_equal(cutouts, ~retained)
        audit, _ = audit_mask_at_physical_scale(retained, visible, repair=WIDTH)
        assert audit["edge"]["four_neighbor_status"] == "PASS"
        assert audit["width"]["status"] == "PASS"
        assert audit["width"]["thin_decorative_visible_components"] == 0


def test_assembled_preview_is_derived_from_saved_masks() -> None:
    report = json.loads(
        (ARTIFACT / "reports" / "uniform_contour_stack_report.json").read_text(
            encoding="utf-8"
        )
    )
    composite = np.full((576, 576, 3), 247, dtype=np.uint8)
    for row in report["layers"]:
        retained = _mask(
            ARTIFACT / "layers" / row["id"] / "retained_material_mask.png"
        )
        value = row["color"].lstrip("#")
        composite[retained] = tuple(
            int(value[index : index + 2], 16) for index in (0, 2, 4)
        )
    composite = composite[32:-32, 32:-32]
    preview = np.asarray(
        Image.open(
            ARTIFACT / "previews" / "assembled_from_actual_masks.png"
        ).convert("RGB")
    )
    target = np.asarray(
        Image.open(ARTIFACT / "previews" / "uniform_contour_target.png").convert(
            "RGB"
        )
    )
    assert np.array_equal(composite, preview)
    assert np.array_equal(preview, target)
