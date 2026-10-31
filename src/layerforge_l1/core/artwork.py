"""Region recoloring of the processed flat-color artwork (the planner target), with a replayable log.

This edits the *artwork* (the exact-palette target that planning reproduces), never a physical
sheet and never the uploaded source image.  A region is one 4-connected component of a single
exact color, the same connectivity intake reports as ``source_regions_4``; clicking selects that
component only, not every pixel of the color.  An optional box limits the component to the part
inside the box (e.g. one trouser leg when both legs are one connected region).

An artwork edit directory holds::

    base.png      the artwork the edits started from (copy; never modified)
    current.png   the artwork after every edit in the log
    edits.json    schema, base hash/origin, ordered edits with their measured effect

Every edit records the color it replaced and a hash of the exact pixels it changed, so replaying
the log from ``base.png`` must reproduce ``current.png`` bit for bit (``verify``); undo/revert
replay a prefix.  Changing the artwork does not change any stack: a stack planned from another
artwork is not validated against this one (see ``app/workspace.py``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

from .model import StackError, hex_color

ARTWORK_SCHEMA = "layerforge.artwork_edits"
ARTWORK_VERSION = "1.0.0"
COLOR_SOURCES = ("palette", "picker", "eyedropper")
FOUR = ndimage.generate_binary_structure(2, 1)


class ArtworkError(StackError):
    """An artwork edit that cannot be carried out as given (message is shown to the user)."""


# -- images and colors ---------------------------------------------------------------------------
def load_rgb(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("RGB"), dtype=np.uint8)  # writable copy


def save_rgb(image: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial.png")
    Image.fromarray(image).save(partial)
    partial.replace(path)


def rgb_sha256(image: np.ndarray) -> str:
    """Hash of the decoded pixels (same convention as the stack manifest's ``target_rgb_sha256``)."""

    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def mask_sha256(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(mask.astype(bool)).tobytes() + str(mask.shape).encode()).hexdigest()


def parse_hex(text: Any) -> tuple[int, int, int]:
    value = str(text or "").strip()
    if not (len(value) == 7 and value.startswith("#")):
        raise ArtworkError(f"color must be #RRGGBB, got {text!r}")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    except ValueError:
        raise ArtworkError(f"color must be #RRGGBB, got {text!r}") from None


def _color_mask(image: np.ndarray, rgb: tuple[int, int, int]) -> np.ndarray:
    return np.all(image == np.asarray(rgb, np.uint8), axis=2)


def artwork_area(image: np.ndarray, frame_hex: str | None) -> np.ndarray:
    """Pixels that belong to the artwork (everything except the never-visible frame padding)."""

    if not frame_hex:
        return np.ones(image.shape[:2], bool)
    return ~_color_mask(image, parse_hex(frame_hex))


def region_count(mask: np.ndarray) -> int:
    return int(ndimage.label(mask, structure=FOUR)[1]) if mask.any() else 0


def palette_summary(image: np.ndarray, frame_hex: str | None, mm_per_pixel: float) -> list[dict[str, Any]]:
    """Every artwork color with its area and 4-connected region count, largest first."""

    inside = artwork_area(image, frame_hex)
    colors, counts = np.unique(image[inside].reshape(-1, 3), axis=0, return_counts=True)
    rows = []
    for rgb, count in zip(colors, counts):
        mask = _color_mask(image, tuple(int(v) for v in rgb)) & inside
        rows.append({"hex": hex_color(tuple(int(v) for v in rgb)), "pixels": int(count),
                     "area_mm2": round(float(count) * mm_per_pixel**2, 2), "regions_4": region_count(mask)})
    rows.sort(key=lambda r: (-r["pixels"], r["hex"]))
    return rows


# -- selection and recolor -----------------------------------------------------------------------
def check_point(image: np.ndarray, at_px: Any) -> tuple[int, int]:
    try:
        row, col = (int(v) for v in at_px)
    except (TypeError, ValueError):
        raise ArtworkError("at_px must be [row, column] in artwork pixels") from None
    height, width = image.shape[:2]
    if not (0 <= row < height and 0 <= col < width):
        raise ArtworkError(f"point {[row, col]} is outside the {height} x {width} artwork")
    return row, col


def check_box(image: np.ndarray, box_px: Any) -> tuple[int, int, int, int] | None:
    """``[row0, col0, row1, col1]`` (end exclusive), clipped to the image; ``None`` = no limit."""

    if box_px is None:
        return None
    try:
        r0, c0, r1, c1 = (int(v) for v in box_px)
    except (TypeError, ValueError):
        raise ArtworkError("box_px must be [row0, col0, row1, col1] in artwork pixels") from None
    height, width = image.shape[:2]
    r0, r1 = sorted((max(0, min(height, r0)), max(0, min(height, r1))))
    c0, c1 = sorted((max(0, min(width, c0)), max(0, min(width, c1))))
    if r1 - r0 < 1 or c1 - c0 < 1:
        raise ArtworkError("the limit box is empty")
    return r0, c0, r1, c1


def select_region(image: np.ndarray, frame_hex: str | None, at_px: Any, box_px: Any = None,
                  mm_per_pixel: float = 1.0) -> tuple[np.ndarray, dict[str, Any]]:
    """The 4-connected same-color component at ``at_px`` (inside ``box_px`` when given)."""

    row, col = check_point(image, at_px)
    box = check_box(image, box_px)
    rgb = tuple(int(v) for v in image[row, col])
    color = hex_color(rgb)
    if frame_hex and color == frame_hex.upper():
        raise ArtworkError("that point is in the frame padding around the artwork (never visible); click inside the artwork")
    same = _color_mask(image, rgb)
    candidates = same
    if box is not None:
        r0, c0, r1, c1 = box
        if not (r0 <= row < r1 and c0 <= col < c1):
            raise ArtworkError("the clicked point is outside the limit box")
        candidates = np.zeros_like(same)
        candidates[r0:r1, c0:c1] = same[r0:r1, c0:c1]
    labels, _ = ndimage.label(candidates, structure=FOUR)
    mask = labels == labels[row, col]
    rows, cols = np.nonzero(mask)
    full = ndimage.label(same, structure=FOUR)[0]
    whole = full == full[row, col]
    info = {
        "color": color, "at_px": [row, col], "box_px": list(box) if box else None,
        "pixels": int(mask.sum()), "area_mm2": round(float(mask.sum()) * mm_per_pixel**2, 3),
        "bbox_px": [int(rows.min()), int(cols.min()), int(rows.max()) + 1, int(cols.max()) + 1],
        "regions_of_color": region_count(same), "pixels_of_color": int(same.sum()),
        "limited_by_box": bool(box is not None and int(whole.sum()) != int(mask.sum())),
        "whole_region_pixels": int(whole.sum()),
        "region_sha256": mask_sha256(mask),
    }
    return mask, info


def recolor(image: np.ndarray, mask: np.ndarray, to_hex: str, frame_hex: str | None) -> np.ndarray:
    rgb = parse_hex(to_hex)
    if frame_hex and hex_color(rgb) == frame_hex.upper():
        raise ArtworkError(f"{frame_hex} is the frame color reserved for the padding; choose another color")
    out = image.copy()
    out[mask] = rgb
    return out


def change_summary(before: np.ndarray, after: np.ndarray, mask: np.ndarray, from_hex: str | list[str], to_hex: str,
                   frame_hex: str | None) -> dict[str, Any]:
    """What a recolor did to the palette and to the regions of every color involved.

    ``from_hex`` is the replaced color, or the list of colors of a multi-region selection."""

    inside = artwork_area(before, frame_hex)
    froms = sorted({from_hex} if isinstance(from_hex, str) else set(from_hex))
    to = to_hex.upper()
    to_rgb = parse_hex(to)
    ring = ndimage.binary_dilation(mask, structure=FOUR) & ~mask
    to_before = _color_mask(before, to_rgb) & inside
    touching = ndimage.label(to_before, structure=FOUR)[0][ring & to_before]
    before_colors = {hex_color(tuple(int(v) for v in c)) for c in np.unique(before[inside].reshape(-1, 3), axis=0)}
    after_colors = {hex_color(tuple(int(v) for v in c)) for c in np.unique(after[inside].reshape(-1, 3), axis=0)}
    involved = froms + ([to] if to not in froms else [])
    return {
        "from": froms[0] if len(froms) == 1 else "mixed", "from_colors": froms, "to": to,
        "pixels": int((mask & np.any(before != after, axis=2)).sum()),
        "palette_size_before": len(before_colors), "palette_size_after": len(after_colors),
        "colors_removed": sorted(before_colors - after_colors), "colors_added": sorted(after_colors - before_colors),
        "regions_before": {h: region_count(_color_mask(before, parse_hex(h)) & inside) for h in involved},
        "regions_after": {h: region_count(_color_mask(after, parse_hex(h)) & inside) for h in involved},
        "merged_with_adjacent_regions": int(np.unique(touching[touching > 0]).size),
    }


MAX_REGIONS = 500


def check_regions(regions: Any) -> list[dict[str, Any]]:
    """``[{"at_px": [row, col], "box_px": [r0, c0, r1, c1] (optional)}, ...]``, normalized."""

    if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
        raise ArtworkError(f"regions must be a list of at most {MAX_REGIONS} entries")
    out = []
    for item in regions:
        if not isinstance(item, dict):
            raise ArtworkError("each region needs at_px and optionally box_px")
        try:
            at = [int(v) for v in item.get("at_px") or []]
            box = None if item.get("box_px") is None else [int(v) for v in item["box_px"]]
        except (TypeError, ValueError):
            raise ArtworkError("at_px / box_px must be whole artwork pixel coordinates") from None
        if len(at) != 2:
            raise ArtworkError("at_px must be [row, column] in artwork pixels")
        out.append({"at_px": at, **({"box_px": box} if box is not None else {})})
    return out


def select_regions(image: np.ndarray, frame_hex: str | None, regions: list[dict[str, Any]],
                   mm_per_pixel: float = 1.0) -> tuple[np.ndarray, dict[str, Any]]:
    """Union of several ``select_region`` selections (any colors); a region listed twice counts once."""

    if not regions:
        raise ArtworkError("no region is selected")
    mask = np.zeros(image.shape[:2], bool)
    items, seen = [], set()
    for region in regions:
        part, info = select_region(image, frame_hex, region["at_px"], region.get("box_px"), mm_per_pixel)
        if info["region_sha256"] in seen:
            continue
        seen.add(info["region_sha256"])
        mask |= part
        items.append({k: info[k] for k in ("color", "at_px", "box_px", "pixels", "limited_by_box", "whole_region_pixels")})
    rows, cols = np.nonzero(mask)
    colors = sorted({i["color"] for i in items})
    single = len(items) == 1
    info = {
        "color": colors[0] if len(colors) == 1 else "mixed", "colors": colors, "regions": len(items), "items": items,
        "pixels": int(mask.sum()), "area_mm2": round(float(mask.sum()) * mm_per_pixel**2, 3),
        "bbox_px": [int(rows.min()), int(cols.min()), int(rows.max()) + 1, int(cols.max()) + 1],
        "regions_of_color": region_count(_color_mask(image, parse_hex(colors[0]))) if single else None,
        "limited_by_box": any(i["limited_by_box"] for i in items),
        "whole_region_pixels": sum(i["whole_region_pixels"] for i in items),
        "box_px": items[0]["box_px"] if single else None,
        "region_sha256": mask_sha256(mask),
    }
    return mask, info


def toggle_region(image: np.ndarray, frame_hex: str | None, regions: list[dict[str, Any]], at_px: Any,
                  box_px: Any = None) -> list[dict[str, Any]]:
    """Shift+click: remove the selected region that contains ``at_px``, else add the region there."""

    row, col = check_point(image, at_px)
    for index, region in enumerate(regions):
        part, _ = select_region(image, frame_hex, region["at_px"], region.get("box_px"))
        if part[row, col]:
            return regions[:index] + regions[index + 1:]
    _, info = select_region(image, frame_hex, [row, col], box_px)  # refuses the frame and points outside the box
    return regions + [{"at_px": [row, col], **({"box_px": info["box_px"]} if info["box_px"] else {})}]


def check_op(op: Any) -> dict[str, Any]:
    """Normalized request: ``recolor_region`` (at_px, optional box_px) or ``recolor_regions`` (regions:
    a multi-region selection recolored at once as one edit), plus color, source and sampled_at_px."""

    if not isinstance(op, dict):
        raise ArtworkError("an artwork edit needs op 'recolor_region' or 'recolor_regions'")
    kind = op.get("op") or ("recolor_regions" if "regions" in op else "recolor_region")
    if kind not in ("recolor_region", "recolor_regions"):
        raise ArtworkError("an artwork edit needs op 'recolor_region' or 'recolor_regions'")
    source = op.get("source", "picker")
    if source not in COLOR_SOURCES:
        raise ArtworkError(f"source must be one of {', '.join(COLOR_SOURCES)}")
    out: dict[str, Any] = {"op": kind, "color": "#%02X%02X%02X" % parse_hex(op.get("color")), "source": source}
    if kind == "recolor_regions":
        out["regions"] = check_regions(op.get("regions"))
        if not out["regions"]:
            raise ArtworkError("no region is selected")
    else:
        try:
            out["at_px"] = [int(v) for v in op.get("at_px") or []]
        except (TypeError, ValueError):
            raise ArtworkError("at_px must be [row, column] in artwork pixels") from None
        if len(out["at_px"]) != 2:
            raise ArtworkError("at_px must be [row, column] in artwork pixels")
        if op.get("box_px") is not None:
            out["box_px"] = [int(v) for v in op["box_px"]]
    if source == "eyedropper":
        if not op.get("sampled_at_px"):
            raise ArtworkError("an eyedropper color needs the sampled point (sampled_at_px)")
        out["sampled_at_px"] = [int(v) for v in op["sampled_at_px"]]
    return out


def preview_id(current_sha: str, op: dict[str, Any]) -> str:
    """Identifies one previewed edit on one artwork; Apply must name the preview the user saw."""

    return hashlib.sha256(json.dumps([current_sha, op], sort_keys=True).encode()).hexdigest()[:16]


def plan_edit(image: np.ndarray, op: dict[str, Any], frame_hex: str | None,
              mm_per_pixel: float) -> tuple[np.ndarray, np.ndarray, dict[str, Any], dict[str, Any]]:
    """(after, mask, selection, change) of one checked op on ``image``; nothing is saved."""

    if op["source"] == "eyedropper":
        row, col = check_point(image, op["sampled_at_px"])
        sampled = hex_color(tuple(int(v) for v in image[row, col]))
        if sampled != op["color"]:
            raise ArtworkError(f"the eyedropper point now has {sampled}, not {op['color']}; sample again")
    if op["op"] == "recolor_regions":
        mask, selection = select_regions(image, frame_hex, op["regions"], mm_per_pixel)
        if selection["colors"] == [op["color"]]:
            raise ArtworkError(f"every selected region already has {op['color']}; nothing would change")
        froms = selection["colors"]
    else:
        mask, selection = select_region(image, frame_hex, op["at_px"], op.get("box_px"), mm_per_pixel)
        if selection["color"] == op["color"]:
            raise ArtworkError(f"the selected region already has {op['color']}; nothing would change")
        froms = selection["color"]
    after = recolor(image, mask, op["color"], frame_hex)  # all selected pixels at once, nothing else
    return after, mask, selection, change_summary(image, after, mask, froms, op["color"], frame_hex)


# -- previews ------------------------------------------------------------------------------------
def highlight(image: np.ndarray, mask: np.ndarray, box: list[int] | None = None) -> np.ndarray:
    """Artwork with everything but the region dimmed and the region outlined in magenta."""

    out = (image.astype(np.float32) * 0.35 + 255 * 0.65).astype(np.uint8)
    out[mask] = image[mask]
    # Outline just outside the region, so thin regions (a necklace chain) keep their color visible.
    edge = ndimage.binary_dilation(mask, iterations=max(1, image.shape[0] // 512)) & ~mask
    out[edge] = (255, 0, 255)
    if box:
        r0, c0, r1, c1 = box
        out[r0, c0:c1] = out[r1 - 1, c0:c1] = (0, 160, 255)
        out[r0:r1, c0] = out[r0:r1, c1 - 1] = (0, 160, 255)
    return out


def before_after(before: np.ndarray, after: np.ndarray, mask: np.ndarray, pad: int = 24, size: int = 360) -> np.ndarray:
    """Zoomed crops around the region: before | after, outlined, on one image."""

    rows, cols = np.nonzero(mask)
    r0, r1 = max(0, rows.min() - pad), min(mask.shape[0], rows.max() + 1 + pad)
    c0, c1 = max(0, cols.min() - pad), min(mask.shape[1], cols.max() + 1 + pad)
    tiles = []
    for image in (before, after):
        crop = highlight(image, mask)[r0:r1, c0:c1] if image is before else image[r0:r1, c0:c1]
        tile = Image.fromarray(np.ascontiguousarray(crop))
        scale = size / max(tile.size)
        tile = tile.resize((max(1, round(tile.size[0] * scale)), max(1, round(tile.size[1] * scale))), Image.NEAREST)
        canvas = Image.new("RGB", (size + 8, size + 8), (255, 255, 255))
        canvas.paste(tile, (4, 4))
        tiles.append(np.asarray(canvas))
    return np.concatenate(tiles, axis=1)


# -- the edit log --------------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_log(directory: Path, log: dict[str, Any]) -> None:
    partial = directory / "edits.json.partial"
    partial.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    partial.replace(directory / "edits.json")


def read_log(directory: Path) -> dict[str, Any] | None:
    path = Path(directory) / "edits.json"
    if not path.is_file():
        return None
    log = json.loads(path.read_text(encoding="utf-8"))
    if log.get("schema") != ARTWORK_SCHEMA or log.get("version") != ARTWORK_VERSION:
        raise ArtworkError(f"{path} is not a {ARTWORK_SCHEMA} {ARTWORK_VERSION} log")
    return log


def start(directory: Path, base: np.ndarray, origin: dict[str, Any], frame_hex: str | None,
          mm_per_pixel: float) -> dict[str, Any]:
    """A new, empty log on a copy of ``base``."""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    save_rgb(base, directory / "base.png")
    save_rgb(base, directory / "current.png")
    log = {"schema": ARTWORK_SCHEMA, "version": ARTWORK_VERSION, "created_utc": _now(),
           "base": {"file": "base.png", "sha256": rgb_sha256(base), "origin": origin, "frame_color": frame_hex,
                    "mm_per_pixel": mm_per_pixel, "palette": palette_summary(base, frame_hex, mm_per_pixel)},
           "edits": [], "current_sha256": rgb_sha256(base)}
    _write_log(directory, log)
    return log


def replay(directory: Path, log: dict[str, Any], count: int | None = None) -> np.ndarray:
    """``base.png`` with the first ``count`` edits (all by default), each checked against its record."""

    image = load_rgb(Path(directory) / "base.png")
    if rgb_sha256(image) != log["base"]["sha256"]:
        raise ArtworkError("artwork base.png does not match its recorded hash; the edit log cannot be trusted")
    frame, mpp = log["base"]["frame_color"], log["base"]["mm_per_pixel"]
    for entry in log["edits"][:count]:
        after, mask, selection, _ = plan_edit(image, check_op(entry["op"]), frame, mpp)
        if selection["color"] != entry["from"] or mask_sha256(mask) != entry["region_sha256"]:
            raise ArtworkError(f"artwork edit #{entry['index']} no longer selects the same pixels; the log does not replay")
        image = after
    return image


def apply(directory: Path, op: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    """Apply one checked op to ``current.png`` and append it to the log (saved)."""

    directory = Path(directory)
    log = read_log(directory)
    if log is None:
        raise ArtworkError("no artwork edit log; preview an edit first")
    image = load_rgb(directory / "current.png")
    if rgb_sha256(image) != log["current_sha256"]:
        raise ArtworkError("current.png does not match the edit log; verify or undo to rebuild it")
    frame, mpp = log["base"]["frame_color"], log["base"]["mm_per_pixel"]
    after, mask, selection, change = plan_edit(image, op, frame, mpp)
    entry = {"index": len(log["edits"]) + 1, "op": op, "from": selection["color"],
             "from_colors": selection.get("colors", [selection["color"]]), "to": op["color"],
             "region_sha256": mask_sha256(mask), "selection": {**{k: selection[k] for k in (
                 "pixels", "area_mm2", "bbox_px", "regions_of_color", "limited_by_box", "whole_region_pixels")},
                 "regions": selection.get("regions", 1)},
             "change": change, "palette_after": palette_summary(after, frame, mpp), "applied_utc": _now()}
    save_rgb(after, directory / "current.png")
    log["edits"].append(entry)
    log["current_sha256"] = rgb_sha256(after)
    _write_log(directory, log)
    return after, entry


def revert(directory: Path, keep: int) -> tuple[np.ndarray, dict[str, Any]]:
    """Keep the first ``keep`` edits (replayed from the base) and drop the rest."""

    directory = Path(directory)
    log = read_log(directory)
    if log is None:
        raise ArtworkError("no artwork edits to undo")
    if not (0 <= keep <= len(log["edits"])):
        raise ArtworkError(f"keep must be between 0 and {len(log['edits'])}")
    image = replay(directory, log, keep)
    log["edits"] = log["edits"][:keep]
    log["current_sha256"] = rgb_sha256(image)
    save_rgb(image, directory / "current.png")
    _write_log(directory, log)
    return image, log


def verify(directory: Path) -> dict[str, Any]:
    """Reload the saved log and replay it from the base; the result must equal ``current.png``."""

    directory = Path(directory)
    log = read_log(directory)
    if log is None:
        raise ArtworkError("no artwork edit log")
    saved = load_rgb(directory / "current.png")
    problems = []
    try:
        replayed = replay(directory, log)
    except ArtworkError as error:
        replayed, problems = None, [str(error)]
    identical = replayed is not None and np.array_equal(replayed, saved)
    if replayed is not None and not identical:
        problems.append(f"{int(np.any(replayed != saved, axis=2).sum())} pixels differ between the replay and current.png")
    if rgb_sha256(saved) != log["current_sha256"]:
        problems.append("current.png does not match the hash recorded in edits.json")
    return {"edits": len(log["edits"]), "replay_identical": identical and not problems, "problems": problems,
            "current_sha256": rgb_sha256(saved), "checked_utc": _now()}
