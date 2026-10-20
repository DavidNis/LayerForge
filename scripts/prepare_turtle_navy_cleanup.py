"""Apply the user-approved turtle indexed-target navy artifact cleanup once."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "artifacts"
    / "l1_turtle_on_leaf_global_planner_v2"
    / "automatic"
    / "indexed_target.png"
)
OUTPUT = ROOT / "artifacts" / "l1_turtle_on_leaf_cleanup_v1"
NAVY = np.asarray((23, 51, 75), dtype=np.uint8)
BLACK = np.asarray((14, 15, 13), dtype=np.uint8)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite: {OUTPUT}")
    OUTPUT.mkdir(parents=True)
    rgb = np.asarray(Image.open(SOURCE).convert("RGB"), dtype=np.uint8).copy()
    changed = np.all(rgb == NAVY, axis=2)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        changed.astype(np.uint8), connectivity=4
    )
    components = [
        {
            "pixels": int(stats[index, cv2.CC_STAT_AREA]),
            "bbox_xywh": [int(value) for value in stats[index, :4]],
        }
        for index in range(1, count)
    ]
    rgb[changed] = BLACK
    target = OUTPUT / "cleaned_indexed_target.png"
    Image.fromarray(rgb, "RGB").save(target)
    Image.fromarray(changed.astype(np.uint8) * 255, "L").save(
        OUTPUT / "changed_pixels.png"
    )
    palette = np.unique(rgb.reshape(-1, 3), axis=0)
    report = {
        "status": "USER_APPROVED_TARGETED_CLEANUP",
        "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": sha256(SOURCE),
        "output": str(target.relative_to(ROOT)),
        "output_sha256": sha256(target),
        "operation": "RECOLOR_NAVY_ANTIALIAS_REMNANTS_TO_ADJACENT_BLACK_OUTLINE",
        "from_rgb": NAVY.tolist(),
        "to_rgb": BLACK.tolist(),
        "changed_pixels": int(np.count_nonzero(changed)),
        "changed_percentage": float(np.mean(changed) * 100.0),
        "components": components,
        "result_palette_count": len(palette),
        "result_palette_rgb": palette.tolist(),
        "geometry_changed": False,
        "other_pixels_changed": 0,
    }
    (OUTPUT / "cleanup_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
