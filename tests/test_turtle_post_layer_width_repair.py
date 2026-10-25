import json
from pathlib import Path

import numpy as np
from PIL import Image

from layerforge_l1.subtractive_poc import DEFAULT_CONFIG


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "l1_turtle_post_layer_width_repair_200mm_v1"
# The width/sheet-rule test that also read the deleted
# l1_turtle_final_global_planning_200mm_v1 tree was removed in L0; see
# tests/REMOVED_TESTS.md.


def _mask(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L")) > 0


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
