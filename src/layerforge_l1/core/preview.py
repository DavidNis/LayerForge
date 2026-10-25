"""Sheet, assembled, exploded and source-comparison previews.

Previews are drawn from retained-material masks.  ``generate_previews`` uses
the masks rasterized from the reopened STL files when a CAD export directory
is given, and records pixel parity between those and the planned masks, so a
preview never shows geometry other than what was exported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .model import SheetStack

SHEET_PREVIEW_PIXELS = 1152
EXPLODED_SHEET_PIXELS = 480
CUTOUT_RGB = (235, 235, 235)


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def sheet_image(mask: np.ndarray, color: str) -> np.ndarray:
    image = np.empty(mask.shape + (3,), dtype=np.uint8)
    image[:] = CUTOUT_RGB
    image[mask] = _rgb(color)
    return image


def assembled_front(stack: SheetStack, masks: list[np.ndarray]) -> np.ndarray:
    """Front orthographic view: the frontmost material at each pixel."""

    image = np.full(masks[0].shape + (3,), 255, dtype=np.uint8)
    painted = np.zeros(masks[0].shape, dtype=bool)
    for sheet, mask in zip(reversed(stack.sheets), reversed(masks)):
        image[mask & ~painted] = _rgb(sheet.color)
        painted |= mask
    return image


def exploded_view(stack: SheetStack, masks: list[np.ndarray]) -> np.ndarray:
    """Oblique back-to-front stack of sheared sheets; cutouts are transparent."""

    size = EXPLODED_SHEET_PIXELS
    shear, lift = 0.45, size * 0.16
    height = int(size * 0.55 + lift * (len(masks) - 1)) + 20
    width = int(size + size * 0.55 * shear) + 20
    canvas = np.full((height, width, 3), 255, dtype=np.float32)
    for depth, (sheet, mask) in enumerate(zip(stack.sheets, masks)):
        small = cv2.resize(mask.astype(np.float32), (size, size), interpolation=cv2.INTER_AREA)
        y0 = height - 10 - size * 0.55 - lift * depth
        # Square sheet -> parallelogram seen from above-front.
        matrix = np.float32([[1, shear * 0.55, 10], [0, 0.55, y0]])
        alpha = cv2.warpAffine(small, matrix, (width, height), flags=cv2.INTER_LINEAR)[..., None]
        canvas = canvas * (1 - alpha) + np.array(_rgb(sheet.color), np.float32) * alpha
        edge = cv2.warpAffine(np.pad(np.zeros((size - 2, size - 2), np.float32), 1, constant_values=1),
                              matrix, (width, height), flags=cv2.INTER_LINEAR)[..., None]
        canvas = canvas * (1 - 0.6 * edge) + 90 * 0.6 * edge
    return np.clip(canvas, 0, 255).astype(np.uint8)


def source_comparison(stack: SheetStack, assembled: np.ndarray) -> tuple[np.ndarray, int] | None:
    """[target | assembled | mismatches in red], artwork region only."""

    if stack.target_rgb is None:
        return None
    grid = stack.grid
    p = grid.perimeter_pixels
    target = cv2.resize(stack.target_rgb, (grid.artwork_pixels,) * 2, interpolation=cv2.INTER_NEAREST)
    shown = assembled[p:-p, p:-p]
    frame = stack.frame_mask()[p:-p, p:-p]
    differ = (target != shown).any(-1) & ~frame
    diff = (0.35 * shown + 0.65 * 255).astype(np.uint8)
    diff[differ] = (255, 0, 0)
    gap = np.full((target.shape[0], 16, 3), 255, np.uint8)
    return np.concatenate([target, gap, shown, gap, diff], axis=1), int(differ.sum())


def generate_previews(
    stack: SheetStack, output: Path, masks: list[np.ndarray] | None = None, mask_source: str = "planned masks"
) -> dict[str, Any]:
    """Write previews from ``masks`` (default: the stack's planned masks)."""

    output = Path(output)
    (output / "sheets").mkdir(parents=True, exist_ok=True)
    masks = masks if masks is not None else [sheet.retained for sheet in stack.sheets]
    if len(masks) != len(stack.sheets):
        raise ValueError(f"{len(masks)} masks for {len(stack.sheets)} sheets")
    files = []
    for sheet, mask in zip(stack.sheets, masks):
        path = output / "sheets" / f"{sheet.id}.png"
        Image.fromarray(sheet_image(mask, sheet.color)).resize(
            (SHEET_PREVIEW_PIXELS, SHEET_PREVIEW_PIXELS), Image.NEAREST
        ).save(path)
        files.append(path.relative_to(output).as_posix())
    assembled = assembled_front(stack, masks)
    Image.fromarray(assembled).save(output / "assembled_front.png")
    Image.fromarray(exploded_view(stack, masks)).save(output / "exploded.png")
    files += ["assembled_front.png", "exploded.png"]
    planned = assembled_front(stack, [sheet.retained for sheet in stack.sheets])
    report: dict[str, Any] = {
        "mask_source": mask_source,
        "files": files,
        "sheet_mask_px_diff_from_planned": {
            sheet.id: int((mask != sheet.retained).sum()) for sheet, mask in zip(stack.sheets, masks)
        },
        "assembled_px_diff_from_planned": int((assembled != planned).any(-1).sum()),
    }
    comparison = source_comparison(stack, assembled)
    if comparison is not None:
        image, mismatched = comparison
        Image.fromarray(image).save(output / "source_comparison.png")
        files.append("source_comparison.png")
        report["source_comparison_mismatched_pixels"] = mismatched
    return report
