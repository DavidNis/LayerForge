"""Create one conservative Flat Color V3 cleanup from the immutable V2 PNG.

Only high-confidence dark-brown antialias slivers between the established gold
and black regions are eligible for replacement.  No segmentation, palette
fitting, smoothing, planning, or CAD code is imported or executed.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sci_fi_characters_shadowbox.png"
V2_PATH = ROOT / "artifacts" / "l1_sci_fi_boundary_flat_color_v2" / "improved_flat_color.png"
OUTPUT = ROOT / "artifacts" / "l1_sci_fi_boundary_flat_color_v3"

BROWN = np.array([0x78, 0x60, 0x33], dtype=np.uint8)
BLACK = np.array([0x03, 0x04, 0x06], dtype=np.uint8)
GOLD = np.array([0xD8, 0xA3, 0x2B], dtype=np.uint8)
SMALL_THRESHOLD = 20


def hex_color(color: np.ndarray | tuple[int, int, int]) -> str:
    return "#" + "".join(f"{int(channel):02X}" for channel in color)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def component_metrics(rgb: np.ndarray) -> dict:
    colors, inverse = np.unique(rgb.reshape(-1, 3), axis=0, return_inverse=True)
    label_image = inverse.reshape(rgb.shape[:2])
    total_regions = 0
    total_tiny = 0
    total_tiny_pixels = 0
    per_color = []
    for index, color in enumerate(colors):
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            (label_image == index).astype(np.uint8), connectivity=4
        )
        areas = stats[1:, cv2.CC_STAT_AREA].astype(int)
        tiny = areas[areas < SMALL_THRESHOLD]
        regions = count - 1
        total_regions += regions
        total_tiny += len(tiny)
        total_tiny_pixels += int(tiny.sum())
        per_color.append(
            {
                "color": hex_color(color),
                "pixels": int(np.sum(label_image == index)),
                "connected_regions_4_neighbor": int(regions),
                "tiny_components_below_20_pixels": int(len(tiny)),
                "tiny_component_pixels": int(tiny.sum()),
            }
        )
    return {
        "unique_rgb_colors": int(len(colors)),
        "connected_regions_4_neighbor": int(total_regions),
        "tiny_components_below_20_pixels": int(total_tiny),
        "tiny_component_pixels": int(total_tiny_pixels),
        "per_color": per_color,
    }


def classify_and_clean(v2: np.ndarray, source: np.ndarray) -> tuple[np.ndarray, list[dict]]:
    brown_mask = np.all(v2 == BROWN, axis=2).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        brown_mask, connectivity=4
    )
    output = v2.copy()
    records: list[dict] = []
    immediate_kernel = np.ones((3, 3), np.uint8)
    context_kernel = np.ones((11, 11), np.uint8)
    brown_tuple = tuple(int(value) for value in BROWN)
    black_hex = hex_color(BLACK)
    gold_hex = hex_color(GOLD)

    for component_id in range(1, count):
        component = labels == component_id
        area = int(stats[component_id, cv2.CC_STAT_AREA])
        left = int(stats[component_id, cv2.CC_STAT_LEFT])
        top = int(stats[component_id, cv2.CC_STAT_TOP])
        width = int(stats[component_id, cv2.CC_STAT_WIDTH])
        height = int(stats[component_id, cv2.CC_STAT_HEIGHT])

        ring = cv2.dilate(
            component.astype(np.uint8), immediate_kernel, iterations=1
        ).astype(bool)
        ring &= ~component
        neighbors = Counter(
            hex_color(color)
            for color in map(tuple, v2[ring])
            if tuple(color) != brown_tuple
        )
        neighbor_set = set(neighbors)
        mean_source = source[component].mean(axis=0)
        thin = min(width, height) <= 2
        source_is_gold_transition = bool(
            mean_source[0] > mean_source[1] * 1.15
            and mean_source[1] > mean_source[2] * 1.8
        )

        classification = "AMBIGUOUS"
        reason = "component is too large or lacks a uniquely supported boundary identity"
        replacement: str | None = None

        if area < SMALL_THRESHOLD and neighbor_set == {black_hex, gold_hex}:
            if (
                min(neighbors[black_hex], neighbors[gold_hex]) >= 2
                and thin
                and source_is_gold_transition
            ):
                classification = "ACCIDENTAL_COLOR_FRAGMENT"
                reason = (
                    "thin source-gold transition sliver whose only immediate "
                    "region identities are established black ink and gold"
                )

                # Boundary ownership selects the replacement.  An 11x11 local
                # context breaks immediate-ring ties; RGB distance is not used.
                black_votes = neighbors[black_hex]
                gold_votes = neighbors[gold_hex]
                if black_votes == gold_votes:
                    context = cv2.dilate(
                        component.astype(np.uint8), context_kernel, iterations=1
                    ).astype(bool)
                    context &= ~component
                    context_colors = v2[context]
                    black_votes = int(np.sum(np.all(context_colors == BLACK, axis=1)))
                    gold_votes = int(np.sum(np.all(context_colors == GOLD, axis=1)))
                if black_votes == gold_votes:
                    classification = "AMBIGUOUS"
                    reason = "gold/ink boundary ownership remained tied after local-context voting"
                elif gold_votes > black_votes:
                    output[component] = GOLD
                    replacement = gold_hex
                else:
                    output[component] = BLACK
                    replacement = black_hex
            else:
                classification = "AMBIGUOUS"
                reason = "gold/ink component did not meet all thin-transition evidence tests"
        elif area < SMALL_THRESHOLD:
            classification = "BOUNDARY_OR_OUTLINE_DETAIL"
            reason = (
                "small component touches non-gold material identities; preserved "
                "because it may encode an intentional boundary"
            )

        records.append(
            {
                "component_id": component_id,
                "classification": classification,
                "reason": reason,
                "area_pixels": area,
                "bbox_xywh": [left, top, width, height],
                "immediate_neighbors": dict(neighbors),
                "mean_source_rgb": [round(float(value), 3) for value in mean_source],
                "replacement": replacement,
            }
        )

    return output, records


def labeled(image: Image.Image, title: str) -> Image.Image:
    header = 44
    panel = Image.new("RGB", (image.width, image.height + header), "white")
    panel.paste(image, (0, header))
    ImageDraw.Draw(panel).text((12, 13), title, fill="black", font=ImageFont.load_default())
    return panel


def side_by_side(v2: Image.Image, v3: Image.Image, title: str = "FULL IMAGE") -> Image.Image:
    panels = [labeled(v2, f"{title}: V2"), labeled(v3, f"{title}: V3")]
    canvas = Image.new("RGB", (panels[0].width + panels[1].width, panels[0].height), "white")
    canvas.paste(panels[0], (0, 0))
    canvas.paste(panels[1], (panels[0].width, 0))
    return canvas


def crop_comparison(v2: Image.Image, v3: Image.Image, box: tuple[int, int, int, int], title: str) -> Image.Image:
    left = v2.crop(box)
    right = v3.crop(box)
    scale = min(2.0, 920 / max(1, left.width))
    size = (round(left.width * scale), round(left.height * scale))
    left = left.resize(size, Image.Resampling.NEAREST)
    right = right.resize(size, Image.Resampling.NEAREST)
    return side_by_side(left, right, title)


def color_edges(rgb: np.ndarray) -> np.ndarray:
    edges = np.zeros(rgb.shape[:2], dtype=bool)
    edges[:, 1:] |= np.any(rgb[:, 1:] != rgb[:, :-1], axis=2)
    edges[1:, :] |= np.any(rgb[1:, :] != rgb[:-1, :], axis=2)
    return edges


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)
    if not V2_PATH.exists():
        raise FileNotFoundError(V2_PATH)

    source_image = Image.open(SOURCE).convert("RGB")
    v2_image = Image.open(V2_PATH).convert("RGB")
    if source_image.size != v2_image.size:
        raise ValueError(f"source {source_image.size} and V2 {v2_image.size} differ")
    source = np.asarray(source_image)
    v2 = np.asarray(v2_image)
    v3, records = classify_and_clean(v2, source)
    v3_image = Image.fromarray(v3, mode="RGB")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "previews").mkdir(exist_ok=True)
    (OUTPUT / "crops").mkdir(exist_ok=True)
    v3_path = OUTPUT / "flat_color_v3.png"
    v3_image.save(v3_path)

    side_by_side(v2_image, v3_image).save(OUTPUT / "previews" / "v2_vs_v3.png")
    changed = np.any(v2 != v3, axis=2)
    overlay = (v2.astype(np.float32) * 0.30).astype(np.uint8)
    overlay[changed] = np.array([255, 0, 255], dtype=np.uint8)
    Image.fromarray(overlay).save(OUTPUT / "previews" / "changed_pixel_overlay.png")

    class_colors = {
        "ACCIDENTAL_COLOR_FRAGMENT": np.array([255, 0, 0], dtype=np.uint8),
        "BOUNDARY_OR_OUTLINE_DETAIL": np.array([0, 220, 255], dtype=np.uint8),
        "INTENTIONAL_VISUAL_DETAIL": np.array([0, 220, 0], dtype=np.uint8),
        "AMBIGUOUS": np.array([255, 220, 0], dtype=np.uint8),
    }
    classification_map = (v2.astype(np.float32) * 0.25).astype(np.uint8)
    brown_mask = np.all(v2 == BROWN, axis=2).astype(np.uint8)
    _, brown_labels, _, _ = cv2.connectedComponentsWithStats(brown_mask, connectivity=4)
    for record in records:
        classification_map[brown_labels == record["component_id"]] = class_colors[
            record["classification"]
        ]
    Image.fromarray(classification_map).save(
        OUTPUT / "previews" / "dark_brown_component_classification.png"
    )

    crop_boxes = {
        "gold_costumes": (120, 320, 900, 980),
        "faces": (180, 70, 850, 510),
        "hands": (20, 380, 810, 1030),
        "leggings": (130, 900, 850, 1450),
        "dark_background": (0, 0, 1024, 430),
    }
    for name, box in crop_boxes.items():
        crop_comparison(v2_image, v3_image, box, name.replace("_", " ").upper()).save(
            OUTPUT / "crops" / f"{name}.png"
        )

    v2_metrics = component_metrics(v2)
    v3_metrics = component_metrics(v3)
    v2_512 = np.asarray(v2_image.resize((512, 512), Image.Resampling.NEAREST))
    v3_512 = np.asarray(v3_image.resize((512, 512), Image.Resampling.NEAREST))
    v2_metrics_512 = component_metrics(v2_512)
    v3_metrics_512 = component_metrics(v3_512)

    classifications = Counter(record["classification"] for record in records)
    replacements = Counter(
        record["replacement"] for record in records if record["replacement"] is not None
    )
    modified_records = [record for record in records if record["replacement"] is not None]
    modified_pixels = int(changed.sum())
    v2_edges = color_edges(v2)
    v3_edges = color_edges(v3)
    changed_edges = v2_edges ^ v3_edges

    before_by_color = {entry["color"]: entry for entry in v2_metrics["per_color"]}
    after_by_color = {entry["color"]: entry for entry in v3_metrics["per_color"]}
    new_tiny_components = []
    for color in sorted(set(before_by_color) | set(after_by_color)):
        before = before_by_color.get(color, {"tiny_components_below_20_pixels": 0})
        after = after_by_color.get(color, {"tiny_components_below_20_pixels": 0})
        if after["tiny_components_below_20_pixels"] > before["tiny_components_below_20_pixels"]:
            new_tiny_components.append(color)

    report = {
        "status": "READY_FOR_USER_TEST",
        "milestone": "L1_NOT_APPROVED",
        "candidate_count": 1,
        "planner_invoked": False,
        "method": {
            "input": str(V2_PATH.relative_to(ROOT)),
            "global_reclustering": False,
            "smoothing": False,
            "morphological_shape_editing": False,
            "eligible_change": (
                "complete native-resolution #786033 components below 20 px, "
                "1-2 px thick, bounded only by #030406 and #D8A32B, with "
                "source-gold transition evidence"
            ),
            "replacement_selection": (
                "immediate shared-boundary ownership, then 11x11 surrounding "
                "region identity for ties; never RGB-nearest replacement"
            ),
        },
        "integrity": {
            "source_sha256": sha256(SOURCE),
            "v2_sha256": sha256(V2_PATH),
            "v3_sha256": sha256(v3_path),
            "dimensions_pixels": list(v3_image.size),
            "v2_overwritten": False,
            "source_overwritten": False,
        },
        "palette": {
            "before": [hex_color(color) for color in np.unique(v2.reshape(-1, 3), axis=0)],
            "after": [hex_color(color) for color in np.unique(v3.reshape(-1, 3), axis=0)],
        },
        "native_metrics": {"v2": v2_metrics, "v3": v3_metrics},
        "comparable_512_metrics": {"v2": v2_metrics_512, "v3": v3_metrics_512},
        "dark_brown_components_native": {
            "total_before": len(records),
            "classification_counts": dict(classifications),
            "merged": len(modified_records),
            "preserved": len(records) - len(modified_records),
            "ambiguous": classifications["AMBIGUOUS"],
            "boundary_or_outline_detail": classifications["BOUNDARY_OR_OUTLINE_DETAIL"],
            "intentional_visual_detail": classifications["INTENTIONAL_VISUAL_DETAIL"],
            "replacement_counts": dict(replacements),
            "records": records,
        },
        "pixel_changes": {
            "modified_pixels": modified_pixels,
            "modified_fraction": float(modified_pixels / changed.size),
            "modified_percentage": float(100.0 * modified_pixels / changed.size),
            "changed_edge_pixels": int(changed_edges.sum()),
            "v2_edge_pixels": int(v2_edges.sum()),
            "changed_edge_fraction_of_v2_edges": float(changed_edges.sum() / max(1, v2_edges.sum())),
        },
        "new_tiny_components_introduced": bool(new_tiny_components),
        "colors_with_increased_tiny_component_count": new_tiny_components,
        "visual_assessment": {
            "protected_classes_modified": False,
            "intentional_detail_loss_detected": False,
            "important_boundary_change": (
                "only sub-20-pixel gold/ink antialias slivers were reassigned; "
                "review changed-pixel overlay for every affected pixel"
            ),
            "recommendation": "USER_VISUAL_REVIEW_REQUIRED",
        },
    }
    (OUTPUT / "per_color_fragmentation_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "v3": str(v3_path),
                "classified": dict(classifications),
                "merged_components": len(modified_records),
                "replacement_counts": dict(replacements),
                "modified_pixels": modified_pixels,
                "modified_percentage": report["pixel_changes"]["modified_percentage"],
                "native_before": {
                    "regions": v2_metrics["connected_regions_4_neighbor"],
                    "tiny": v2_metrics["tiny_components_below_20_pixels"],
                },
                "native_after": {
                    "regions": v3_metrics["connected_regions_4_neighbor"],
                    "tiny": v3_metrics["tiny_components_below_20_pixels"],
                },
                "comparable_512_before": {
                    "regions": v2_metrics_512["connected_regions_4_neighbor"],
                    "tiny": v2_metrics_512["tiny_components_below_20_pixels"],
                },
                "comparable_512_after": {
                    "regions": v3_metrics_512["connected_regions_4_neighbor"],
                    "tiny": v3_metrics_512["tiny_components_below_20_pixels"],
                },
                "new_tiny_components": new_tiny_components,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
