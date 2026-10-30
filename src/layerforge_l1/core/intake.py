"""Automatic image intake: a new flat-color PNG/JPEG -> exact-palette planner target.

This automates the preprocessing that was done by hand for the accepted van_gogh
reference (technical PDF ch. 3), using the same functions unchanged:

    fullres_flatten.extract_palette   (k-means in CIELAB on non-edge pixels)
    resize into the square artwork grid, aspect preserved   (cv2.INTER_AREA)
    fullres_flatten.snap_to_palette   (bilateral, nearest Lab, majority, fragment merge)
    pad with a frame color that is solid on every sheet and never visible

Nothing here edits the artwork to make planning succeed.  The diagnostics only
measure: palette spacing, small colors, flatness of the source, pixels changed
by snapping, and features narrower than the 1.2 mm structural width.  Their
findings become flags for the user; see ``propose.py``.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

from ..connectivity_repair import _width_parameters
from ..fullres_flatten import FlattenConfig, extract_palette, snap_to_palette
from ..layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from .model import StackError, StackGrid, hex_color

# Frame candidates: saturated colors that flat illustrations rarely use.  The one
# farthest (CIELAB) from every palette color is chosen.
FRAME_CANDIDATES = ((255, 0, 255), (0, 255, 0), (0, 255, 255), (255, 255, 0), (255, 0, 0), (0, 0, 255))
# fullres_flatten's 40 px speckle threshold was set on the 2048 px / 200 mm grid.
REFERENCE_MM_PER_PIXEL = 200.0 / 2048


@dataclass(frozen=True)
class IntakeConfig:
    """Intake settings.  ``max_colors`` and ``merge_delta`` are ``extract_palette``'s own.

    ``extract_palette``'s ``min_share`` default (0.1 %) is 40 mm2 on a 200 mm artwork and
    dropped printable details such as eye highlights, so the minimum is given as an area.
    """

    max_colors: int = 12
    merge_delta: float = 14.0
    min_color_area_mm2: float = 2.0
    min_fragment_mm2: float = 40 * REFERENCE_MM_PER_PIXEL**2  # fullres_flatten default, in mm2
    alpha_threshold: int = 128
    seed: int = 0  # cv2.kmeans is otherwise unseeded (technical PDF ch. 4, LIMITATION)
    # Diagnostic thresholds (flags only; they never change the target).
    near_duplicate_delta: float = 20.0  # OpenCV 8-bit Lab units
    small_color_area_mm2: float = 25.0
    flat_residual_delta: float = 20.0
    unmatched_min_area_mm2: float = 2.0
    max_non_flat_fraction: float = 0.05
    max_snap_changed_fraction: float = 0.02


def load_image(path: Path) -> tuple[np.ndarray, np.ndarray | None, dict[str, Any]]:
    """Return RGB uint8, an optional boolean opacity mask, and source metadata."""

    path = Path(path)
    data = path.read_bytes()
    image = Image.open(path)
    info = {
        "file": path.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "format": image.format,
        "mode": image.mode,
        "size_px": list(image.size),
    }
    opaque = None
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        alpha = np.asarray(image.convert("RGBA"))[:, :, 3]
        opaque = alpha >= IntakeConfig.alpha_threshold
        if opaque.all():
            opaque = None
    return np.ascontiguousarray(np.asarray(image.convert("RGB"))), opaque, info


def _lab(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)


def choose_frame_color(palette: np.ndarray) -> tuple[int, int, int]:
    palette_lab = _lab(palette)
    best = max(FRAME_CANDIDATES, key=lambda c: float(np.min(np.linalg.norm(palette_lab - _lab(np.array([c])), axis=1))))
    return tuple(int(v) for v in best)


def fit_into_grid(rgb: np.ndarray, opaque: np.ndarray | None, side: int) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Resize so the longer side equals ``side`` (aspect kept) and centre it."""

    height, width = rgb.shape[:2]
    factor = side / max(height, width)
    new_w, new_h = max(1, round(width * factor)), max(1, round(height * factor))
    interpolation = cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC
    resized = cv2.resize(rgb, (new_w, new_h), interpolation=interpolation)
    x0, y0 = (side - new_w) // 2, (side - new_h) // 2
    canvas = np.zeros((side, side, 3), np.uint8)
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    inside = np.zeros((side, side), bool)
    if opaque is None:
        inside[y0 : y0 + new_h, x0 : x0 + new_w] = True
    else:
        small = cv2.resize(opaque.astype(np.uint8), (new_w, new_h), interpolation=cv2.INTER_NEAREST).astype(bool)
        inside[y0 : y0 + new_h, x0 : x0 + new_w] = small
    return canvas, inside, {"scale_factor": factor, "placed_px": [new_w, new_h], "offset_px": [x0, y0]}


def _fill_transparent(rgb: np.ndarray, opaque: np.ndarray) -> np.ndarray:
    """Give transparent pixels the color of the nearest opaque pixel (palette input only)."""

    _, (rows, cols) = ndimage.distance_transform_edt(~opaque, return_indices=True)
    return np.ascontiguousarray(rgb[rows, cols])


def _nearest_palette(lab: np.ndarray, palette_lab: np.ndarray, chunk: int = 1 << 18) -> tuple[np.ndarray, np.ndarray]:
    """Index of and distance to the nearest palette color, in bounded memory."""

    index = np.empty(len(lab), np.int32)
    distance = np.empty(len(lab), np.float32)
    for start in range(0, len(lab), chunk):
        block = np.linalg.norm(lab[start : start + chunk, None, :] - palette_lab[None], axis=2)
        index[start : start + chunk] = block.argmin(1)
        distance[start : start + chunk] = block.min(1)
    return index, distance


def _blend_distance(lab: np.ndarray, palette_lab: np.ndarray, chunk: int = 1 << 16) -> np.ndarray:
    """Distance to the nearest two-color blend (Lab segment between two palette colors).

    Anti-aliased edge pixels are mixtures of the colors they separate; they are
    not new materials (IMAGE_INPUT_CONTRACT) and must not count as unmatched.
    """

    count = len(palette_lab)
    pairs = [(i, j) for i in range(count) for j in range(i + 1, count)] or [(0, 0)]
    a = palette_lab[[i for i, _ in pairs]]
    d = palette_lab[[j for _, j in pairs]] - a
    length = np.maximum((d * d).sum(1), 1e-6)
    out = np.empty(len(lab), np.float32)
    for start in range(0, len(lab), chunk):
        x = lab[start : start + chunk, None, :] - a[None]
        t = np.clip((x * d[None]).sum(2) / length[None], 0.0, 1.0)
        out[start : start + chunk] = np.linalg.norm(x - t[:, :, None] * d[None], axis=2).min(1)
    return out


def _thin_feature_pixels(mask: np.ndarray, width_px: int) -> int:
    """Pixels of ``mask`` removed by an opening with the structural-width disc."""

    if width_px <= 1 or not mask.any():
        return 0
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (width_px, width_px))
    opened = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, kernel).astype(bool)
    return int((mask & ~opened).sum())


# -- details changed by intake ---------------------------------------------------------------
# A detail is a coherent drawn piece, not anti-aliasing: at least this area, at least two pixels
# wide somewhere, and (for removed pieces) its smoothed color close to the palette color it had.
DETAIL_MIN_AREA_MM2 = 0.05
DETAIL_ACTIONS = ("preserve", "remove", "adjust")
# Generic process thresholds used only to describe a detail's size (the FDM estimate is separate).
DETAIL_WIDTH_NOTES = ((0.4, "narrower than a 0.4 mm nozzle"), (0.8, "narrower than the 0.8 mm generic minimum opening"),
                      (1.2, "narrower than the 1.2 mm structural width (printed by revealing it through cutouts)"))


def _rgb_hex(rgb: Any) -> str:
    return hex_color(tuple(int(v) for v in rgb))


def _parse_hex(text: str) -> np.ndarray:
    text = str(text).strip().lstrip("#")
    if len(text) != 6:
        raise StackError(f"color must be #RRGGBB, got {text!r}")
    try:
        return np.array([int(text[i:i + 2], 16) for i in (0, 2, 4)], np.uint8)
    except ValueError:
        raise StackError(f"color must be #RRGGBB, got {text!r}") from None


def _width_mm(mask: np.ndarray, mm_per_pixel: float) -> float:
    return round(2 * float(ndimage.distance_transform_edt(np.pad(mask, 1)).max()) * mm_per_pixel, 3)


def detect_details(canvas: np.ndarray, inside: np.ndarray, palette: np.ndarray, smooth_labels: np.ndarray,
                   labels: np.ndarray, smooth: np.ndarray, mm_per_pixel: float, config: IntakeConfig) -> list[dict[str, Any]]:
    """Recognizable details that the default intake recolored or removed.

    * ``color_not_in_palette``: a coherent piece whose (smoothed) color is far from every palette
      color and every two-color blend; the target gives it the nearest palette color instead
      (e.g. red lips, a gold necklace).
    * ``removed_by_cleanup``: a coherent piece that took a palette color after smoothing but was
      erased by the majority filter / small-fragment merge (e.g. eye whites).
    Anti-aliasing (blends, one-pixel rims) is not a detail.  Each entry keeps its pixel mask under
    ``_mask``; nothing is changed here.
    """

    min_px = max(3, round(DETAIL_MIN_AREA_MM2 / mm_per_pixel**2))
    palette_lab = _lab(palette)
    smooth_lab = _lab(smooth).reshape(smooth.shape[:2] + (3,))
    details: list[dict[str, Any]] = []

    def entry(kind: str, mask: np.ndarray, original: np.ndarray, processed: int, **extra: Any) -> dict[str, Any]:
        rows, cols = np.nonzero(mask)
        width = _width_mm(mask, mm_per_pixel)
        return {
            "id": f"{'color' if kind == 'color_not_in_palette' else 'removed'}_{rows.min():04d}_{cols.min():04d}",
            "kind": kind, "bbox_px": [int(rows.min()), int(cols.min()), int(rows.max()) + 1, int(cols.max()) + 1],
            "area_mm2": round(float(mask.sum()) * mm_per_pixel**2, 3), "width_mm": width,
            "original_hex": _rgb_hex(original), "processed_hex": _rgb_hex(palette[processed]),
            "size_notes": [note for limit, note in DETAIL_WIDTH_NOTES if width < limit][:1],
            "_mask": mask, **extra,
        }

    # Colors missing from the palette (smoothed source far from colors and blends).
    far = np.zeros(inside.shape, bool)
    far[inside] = _blend_distance(smooth_lab[inside], palette_lab) > config.flat_residual_delta
    count, components = cv2.connectedComponents(far.astype(np.uint8), connectivity=8)
    for index in range(1, count):
        mask = components == index
        if mask.sum() < min_px or not ndimage.binary_erosion(mask).any():
            continue
        original = np.median(canvas[mask], axis=0)
        processed = int(np.bincount(labels[mask], minlength=len(palette)).argmax())
        details.append(entry("color_not_in_palette", mask, original, processed))

    # Coherent palette-colored pieces erased by the cleanup filters.
    for color in range(len(palette)):
        pieces, count = ndimage.label((smooth_labels == color) & inside & ~far)
        if not count:
            continue
        areas = np.bincount(pieces.ravel())
        for index, box in enumerate(ndimage.find_objects(pieces), start=1):
            if areas[index] < min_px:
                continue
            local = pieces[box] == index
            lost = labels[box][local] != color
            if lost.mean() < 0.5 or not ndimage.binary_erosion(local).any():
                continue
            residual = np.linalg.norm(smooth_lab[box][local] - palette_lab[color], axis=1)
            if np.median(residual) > config.flat_residual_delta:
                continue  # a blend that happened to snap to this color
            mask = np.zeros(inside.shape, bool)
            mask[box] = local
            processed = int(np.bincount(labels[box][local], minlength=len(palette)).argmax())
            details.append(entry("removed_by_cleanup", mask, palette[color], processed, palette_color=_rgb_hex(palette[color])))
    details.sort(key=lambda d: (d["bbox_px"][0], d["bbox_px"][1]))
    return details


def default_action(detail: dict[str, Any]) -> dict[str, Any]:
    """Removed pieces are kept (never dropped for being small); missing colors keep the nearest
    palette color until the user decides (adding a color adds a filament)."""

    if detail["kind"] == "removed_by_cleanup":
        return {"action": "preserve"}
    return {"action": "adjust", "color": detail["processed_hex"]}


def apply_detail_decisions(labels: np.ndarray, smooth: np.ndarray, palette: np.ndarray, details: list[dict[str, Any]],
                           decisions: dict[str, Any] | None, merge_delta: float) -> tuple[np.ndarray, np.ndarray]:
    """Labels and palette after each detail's action (``decisions`` by detail id, else the default).

    preserve: a removed piece gets back its palette color; a missing color becomes a new palette
              color (details of near-identical color share one), applied to the piece and to the
              pixels around it that are closer to it than to any other color (its anti-aliased rim).
    remove:   a removed piece stays as the cleanup left it; a recolored piece takes the dominant
              color around it.
    adjust:   the piece takes the given #RRGGBB (a new palette color if it is not one).
    """

    decisions = decisions or {}
    unknown = sorted(set(decisions) - {d["id"] for d in details})
    if unknown:
        raise StackError(f"unknown intake details {unknown}; the image or settings changed, review the details again")
    labels, palette = labels.copy(), [np.asarray(c, np.uint8) for c in palette]
    base = len(palette)

    def color_index(rgb: np.ndarray, near_delta: float = 0.0) -> int:
        lab = _lab(np.asarray(rgb, np.uint8)[None])[0]
        for i, existing in enumerate(palette):
            if np.array_equal(existing, rgb) or (near_delta and i >= base and np.linalg.norm(_lab(existing[None])[0] - lab) < near_delta):
                return i
        palette.append(np.asarray(rgb, np.uint8))
        return len(palette) - 1

    order = sorted(details, key=lambda d: d["kind"] != "color_not_in_palette")  # recolors first, then restores
    for detail in order:
        choice = decisions.get(detail["id"]) or default_action(detail)
        action = choice.get("action")
        if action not in DETAIL_ACTIONS:
            raise StackError(f"detail {detail['id']}: action must be one of {DETAIL_ACTIONS}, got {action!r}")
        detail["action"] = {"action": action, **({"color": choice["color"]} if action == "adjust" else {}),
                            "default": detail["id"] not in decisions}
        mask = detail["_mask"]
        if action == "adjust":
            labels[mask] = color_index(_parse_hex(choice.get("color", "")))
        elif action == "remove":
            if detail["kind"] == "color_not_in_palette":
                ring = ndimage.binary_dilation(mask, iterations=2) & ~mask
                labels[mask] = np.bincount(labels[ring]).argmax() if ring.any() else labels[mask]
        elif detail["kind"] == "removed_by_cleanup":
            labels[mask] = color_index(_parse_hex(detail["palette_color"]))
        else:
            new = color_index(_parse_hex(detail["original_hex"]), merge_delta)
            rim = ndimage.binary_dilation(mask, iterations=2)
            lab = _lab(smooth[rim])
            colors_lab = _lab(np.array(palette))
            nearest = np.linalg.norm(lab[:, None] - colors_lab[None], axis=2).argmin(1)
            pick = np.zeros(mask.shape, bool)
            pick[rim] = nearest == new
            labels[mask | pick] = new
        detail["result_hex"] = _rgb_hex(palette[int(np.bincount(labels[mask]).argmax())])
    return labels, np.array(palette, np.uint8)


def detail_images(canvas: np.ndarray, default_rgb: np.ndarray, result_rgb: np.ndarray,
                  details: list[dict[str, Any]], pad: int = 14, zoom: int = 6) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Overview with numbered boxes, and per detail: original | default intake | with the decision."""

    overview = canvas.copy()
    crops = {}
    for number, detail in enumerate(details, start=1):
        r0, c0, r1, c1 = detail["bbox_px"]
        color = (255, 0, 255) if detail["kind"] == "color_not_in_palette" else (0, 200, 0)
        cv2.rectangle(overview, (c0 - 3, r0 - 3), (c1 + 2, r1 + 2), color, 2)
        cv2.putText(overview, str(number), (c0, max(12, r0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        ys = slice(max(r0 - pad, 0), min(r1 + pad, canvas.shape[0]))
        xs = slice(max(c0 - pad, 0), min(c1 + pad, canvas.shape[1]))
        tiles = []
        for image in (canvas, default_rgb, result_rgb):
            tile = np.repeat(np.repeat(image[ys, xs], zoom, axis=0), zoom, axis=1)
            tiles.append(np.pad(tile, ((4, 4), (4, 4), (0, 0)), constant_values=255))
        crops[detail["id"]] = np.concatenate(tiles, axis=1)
    return overview, crops


def prepare_target(
    rgb: np.ndarray, opaque: np.ndarray | None, grid: StackGrid, config: IntakeConfig = IntakeConfig(),
    decisions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the exact-palette square target and measure what the snapping did.

    Details that the default snapping would recolor or erase are reported (``details``) and
    resolved by ``decisions`` (``{detail id: {"action": ..., "color": ...}}``) or the default.
    """

    side = grid.poc_config().analysis_pixels
    mm_per_pixel = grid.artwork_mm / side
    if opaque is not None:
        if not opaque.any():
            raise StackError("the image is fully transparent")
        rgb = _fill_transparent(rgb, opaque)
    canvas, inside, placement = fit_into_grid(rgb, opaque, side)
    artwork_mm2 = placement["placed_px"][0] * placement["placed_px"][1] * mm_per_pixel**2
    min_share = config.min_color_area_mm2 / artwork_mm2
    cv2.setRNGSeed(config.seed)
    palette = extract_palette(rgb, config.max_colors, config.merge_delta, min_share)
    if len(palette) == 0:
        raise StackError("no palette colors found in the image")
    fragment_px = max(1, round(config.min_fragment_mm2 / mm_per_pixel**2))
    flatten = replace(FlattenConfig(), min_fragment_pixels=fragment_px)
    default_labels = snap_to_palette(canvas, palette, flatten)

    # The same smoothing and nearest-color step as ``snap_to_palette``, before its cleanup filters.
    smooth = cv2.bilateralFilter(canvas, flatten.bilateral_diameter, flatten.bilateral_sigma_color, flatten.bilateral_sigma_space)
    smooth_lab = _lab(smooth)
    smooth_labels = _nearest_palette(smooth_lab, _lab(palette))[0].reshape(side, side)
    details = detect_details(canvas, inside, palette, smooth_labels, default_labels, smooth, mm_per_pixel, config)
    default_palette = palette
    labels, palette = apply_detail_decisions(default_labels, smooth, palette, details, decisions, config.merge_delta)
    frame_rgb = choose_frame_color(palette)

    # Measurements on the artwork area only (never on the frame padding).
    canvas_lab = _lab(canvas[inside])
    palette_lab = _lab(palette)
    snapped_lab = palette_lab[labels[inside]]
    nearest, residual = _nearest_palette(canvas_lab, palette_lab)
    gray = cv2.cvtColor(canvas, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gradient = np.hypot(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    interior = cv2.erode((gradient < 40).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)[inside]

    unmatched = np.zeros((side, side), bool)
    unmatched[inside] = _blend_distance(canvas_lab, palette_lab) > config.flat_residual_delta
    count, components = cv2.connectedComponents(unmatched.astype(np.uint8), connectivity=8)
    unmatched_sizes = np.bincount(components.ravel())[1:] * mm_per_pixel**2
    large_unmatched = unmatched_sizes[unmatched_sizes >= config.unmatched_min_area_mm2]

    target = np.empty((side, side, 3), np.uint8)
    target[:] = frame_rgb
    target[inside] = palette[labels[inside]]

    width = _width_parameters(grid.poc_config(), DEFAULT_OPTIMIZATION_CONFIG)
    width_px = max(1, round(width["enforced_connection_width_mm"] / mm_per_pixel))
    # k-means can return clusters of edge blends that receive no pixel after snapping;
    # they are not materials and are reported separately instead of as palette colors.
    used = np.unique(labels[inside])
    unused = [hex_color(palette[i]) for i in range(len(palette)) if i not in used]
    colors = []
    total = int(inside.sum())
    for index in used:
        color = palette[index]
        mask = inside & (labels == index)
        count, components = cv2.connectedComponents(mask.astype(np.uint8), connectivity=4)
        sizes = np.bincount(components.ravel())[1:] if count > 1 else np.array([], int)
        colors.append({
            "hex": hex_color(color),
            "share": round(float(mask.sum()) / max(total, 1), 6),
            "area_mm2": round(float(mask.sum()) * mm_per_pixel**2, 2),
            "source_regions_4": int(count - 1),
            "smallest_region_mm2": round(float(sizes.min()) * mm_per_pixel**2, 3) if sizes.size else 0.0,
            "sub_width_feature_mm2": round(_thin_feature_pixels(mask, width_px) * mm_per_pixel**2, 2),
        })
    used_lab = palette_lab[used]
    distances = np.linalg.norm(used_lab[:, None] - used_lab[None], axis=2)
    np.fill_diagonal(distances, np.inf)

    default_rgb = np.empty_like(target)
    default_rgb[:] = frame_rgb
    default_rgb[inside] = default_palette[default_labels[inside]]
    overview, crops = detail_images(canvas, default_rgb, target, details)
    return {
        "target_rgb": target,
        "details": [{k: v for k, v in d.items() if not k.startswith("_")} for d in details],
        "detail_overview": overview,
        "source_canvas": canvas,
        "detail_crops": crops,
        "artwork_mask": inside,
        "unmatched_mask": np.isin(components, 1 + np.nonzero(unmatched_sizes >= config.unmatched_min_area_mm2)[0]),
        "has_frame": bool((~inside).any()),
        "palette": palette[used],
        "frame_color": hex_color(frame_rgb),
        "measurements": {
            "intake_config": asdict(config),
            "flatten_config": asdict(flatten),
            "artwork_grid_px": side,
            "artwork_mm_per_pixel": mm_per_pixel,
            "placement": placement,
            "artwork_size_mm": [round(v * mm_per_pixel, 2) for v in placement["placed_px"]],
            "palette_size": len(used),
            "unused_palette_clusters": unused,
            "colors": colors,
            "min_palette_delta": round(float(distances.min()), 2) if len(used) > 1 else None,
            "nearest_palette_pairs": [
                [hex_color(palette[used[i]]), hex_color(palette[used[j]]), round(float(distances[i, j]), 2)]
                for i, j in zip(*np.nonzero(np.triu(distances < config.near_duplicate_delta)))
            ],
            "mean_snap_delta": round(float(np.linalg.norm(canvas_lab - snapped_lab, axis=1).mean()), 3),
            "non_flat_interior_fraction": round(float((residual[interior] > config.flat_residual_delta).mean()), 5)
            if interior.any() else 0.0,
            "unmatched_regions_at_least_min_area": int(large_unmatched.size),
            "unmatched_area_mm2": round(float(large_unmatched.sum()), 2),
            "palette_min_share": min_share,
            "snap_changed_fraction": round(float((labels[inside] != nearest).mean()), 5),
            "structural_width_mm": width["enforced_connection_width_mm"],
            "structural_width_px": width_px,
            "transparency": "alpha below threshold becomes frame" if opaque is not None else "none",
            "details_changed_by_default_intake": len(details),
            "palette_added_by_decisions": [hex_color(c) for c in palette[len(default_palette):]],
        },
    }
