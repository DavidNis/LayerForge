"""Build one deterministic, boundary-aware flat-color review candidate.

This is an isolated image-preparation experiment.  It deliberately does not
import or invoke any LayerForge planning, CAD, or export module.
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
BASELINE = (
    ROOT
    / "artifacts"
    / "l1_sci_fi_region_aware_flat_color_v1"
    / "existing_indexed_target.png"
)
OUTPUT = ROOT / "artifacts" / "l1_sci_fi_boundary_flat_color_v2"
SMALL_COMPONENT_THRESHOLD = 20
PALETTE_SIZE = 11


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rgb_to_hex(rgb: np.ndarray) -> str:
    return "#" + "".join(f"{int(channel):02X}" for channel in rgb)


def palette_from_source(source_rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fit a reproducible perceptual palette without altering spatial geometry."""

    sampled_rgb = source_rgb[::4, ::4]
    sampled_lab = cv2.cvtColor(sampled_rgb, cv2.COLOR_RGB2LAB).reshape(-1, 3)
    sampled_lab = sampled_lab.astype(np.float32)
    criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
        50,
        0.2,
    )
    cv2.setRNGSeed(42)
    _, labels, centers_lab = cv2.kmeans(
        sampled_lab,
        PALETTE_SIZE,
        None,
        criteria,
        3,
        cv2.KMEANS_PP_CENTERS,
    )
    counts = np.bincount(labels.ravel(), minlength=PALETTE_SIZE)
    order = np.argsort(-counts)
    centers_lab = np.clip(np.rint(centers_lab[order]), 0, 255).astype(np.uint8)
    centers_rgb = cv2.cvtColor(
        centers_lab[np.newaxis, :, :], cv2.COLOR_LAB2RGB
    )[0]
    return centers_rgb, centers_lab


def nearest_palette_labels(image_rgb: np.ndarray, palette_lab: np.ndarray) -> np.ndarray:
    # int32 is required before squaring: int16 overflows for Lab deltas above
    # 181 and creates salt-and-pepper palette assignments.
    lab = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2LAB).astype(np.int32)
    palette = palette_lab.astype(np.int32)
    distances = np.sum(
        (lab[:, :, np.newaxis, :] - palette[np.newaxis, np.newaxis, :, :]) ** 2,
        axis=3,
        dtype=np.int32,
    )
    return np.argmin(distances, axis=2).astype(np.uint8)


def color_edges(rgb: np.ndarray) -> np.ndarray:
    edges = np.zeros(rgb.shape[:2], dtype=np.uint8)
    edges[:, 1:] |= np.any(rgb[:, 1:] != rgb[:, :-1], axis=2)
    edges[1:, :] |= np.any(rgb[1:, :] != rgb[:-1, :], axis=2)
    return edges


def source_edge_mask(source_rgb: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2LAB)
    channels = [cv2.GaussianBlur(lab[:, :, index], (3, 3), 0) for index in range(3)]
    magnitude = np.zeros(source_rgb.shape[:2], dtype=np.float32)
    for channel in channels:
        gx = cv2.Sobel(channel, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(channel, cv2.CV_32F, 0, 1, ksize=3)
        magnitude = np.maximum(magnitude, cv2.magnitude(gx, gy))
    return (magnitude >= 54.0).astype(np.uint8)


def flatten(source_rgb: np.ndarray, palette_rgb: np.ndarray, palette_lab: np.ndarray) -> tuple[np.ndarray, dict]:
    """Flatten surfaces while keeping source-derived boundaries and fine ink."""

    source_bgr = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2BGR)
    # Spatial and range filtering creates coherent basins; unlike global K-means,
    # nearby pixels influence one another and high-contrast borders stop merging.
    filtered_bgr = cv2.pyrMeanShiftFiltering(
        source_bgr, sp=13, sr=24, maxLevel=1
    )
    filtered_rgb = cv2.cvtColor(filtered_bgr, cv2.COLOR_BGR2RGB)
    labels = nearest_palette_labels(filtered_rgb, palette_lab)

    gray = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2GRAY)
    edge = source_edge_mask(source_rgb)
    dark = (gray <= 82).astype(np.uint8)
    distance_inside_dark = cv2.distanceTransform(dark, cv2.DIST_L2, 5)
    # Only thin/ridge-like dark artwork is restored from the source.  Broad dark
    # fields remain governed by the spatial basin, preventing navy, charcoal,
    # hair, and outline colors from collapsing merely because all are dark.
    protected_dark_detail = (dark.astype(bool) & (distance_inside_dark <= 5.5))
    protected_dark_detail |= (gray <= 62) & cv2.dilate(
        edge, np.ones((3, 3), np.uint8), iterations=1
    ).astype(bool)
    ink_index = int(np.argmin(np.sum(palette_rgb.astype(np.int32) ** 2, axis=1)))
    labels[protected_dark_detail] = ink_index

    # Two fixed constrained RAG cleanup passes remove antialias blend slivers.
    # Only non-ink components below 20 pixels may merge, and only into a region
    # that physically touches them.  This cannot erase a protected black eye,
    # mouth, costume line, finger boundary, or outline.
    source_lab = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    next_labels = labels.copy()
    merge_records: list[dict] = []
    kernel = np.ones((3, 3), np.uint8)
    for pass_index in range(2):
        input_labels = next_labels.copy()
        for palette_index in range(len(palette_rgb)):
            if palette_index == ink_index:
                continue
            mask = (input_labels == palette_index).astype(np.uint8)
            count, components, stats, _ = cv2.connectedComponentsWithStats(
                mask, connectivity=4
            )
            for component_id in range(1, count):
                area = int(stats[component_id, cv2.CC_STAT_AREA])
                if area >= SMALL_COMPONENT_THRESHOLD:
                    continue
                component = components == component_id
                ring = cv2.dilate(component.astype(np.uint8), kernel, iterations=1).astype(bool)
                ring &= ~component
                neighbor_values = input_labels[ring]
                neighbor_values = neighbor_values[neighbor_values != palette_index]
                if neighbor_values.size == 0:
                    continue
                neighbor_counts = Counter(int(value) for value in neighbor_values)
                target_index, target_count = neighbor_counts.most_common(1)[0]
                dominance = target_count / len(neighbor_values)
                component_mean = source_lab[component].mean(axis=0)
                target_mean = palette_lab[target_index].astype(np.float32)
                lab_distance = float(np.linalg.norm(component_mean - target_mean))
                if area > 4 and (dominance < 0.45 or lab_distance > 25.0):
                    continue
                next_labels[component] = target_index
                merge_records.append(
                    {
                        "pass": pass_index + 1,
                        "from_palette_index": palette_index,
                        "to_palette_index": target_index,
                        "pixels": area,
                        "boundary_dominance": dominance,
                        "opencv_lab_distance": lab_distance,
                    }
                )

    candidate = palette_rgb[next_labels]
    method = {
        "palette": "11-center deterministic OpenCV Lab k-means on a fixed 1/4 grid sample",
        "segmentation": "full-resolution pyramidal mean shift (sp=13, sr=24, maxLevel=1)",
        "boundary_protection": "strong source Lab gradients plus thin/ridge-like dark details",
        "cleanup": "two fixed region-adjacency passes; only touching non-ink components <20 px; source-confirmed black detail components protected",
        "merged_components": merge_records,
        "planner_invoked": False,
    }
    return candidate, method


def component_metrics(rgb: np.ndarray) -> dict:
    colors, inverse = np.unique(rgb.reshape(-1, 3), axis=0, return_inverse=True)
    label_image = inverse.reshape(rgb.shape[:2])
    total_regions = 0
    tiny_components = 0
    tiny_pixels = 0
    per_color = []
    for index, color in enumerate(colors):
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            (label_image == index).astype(np.uint8), connectivity=4
        )
        areas = stats[1:, cv2.CC_STAT_AREA].astype(int)
        tiny = areas[areas < SMALL_COMPONENT_THRESHOLD]
        regions = count - 1
        total_regions += regions
        tiny_components += int(len(tiny))
        tiny_pixels += int(tiny.sum())
        per_color.append(
            {
                "color": rgb_to_hex(color),
                "pixels": int(np.sum(label_image == index)),
                "connected_regions_4_neighbor": regions,
                "tiny_components_below_20_pixels": int(len(tiny)),
                "tiny_component_pixels": int(tiny.sum()),
            }
        )
    return {
        "unique_rgb_colors": int(len(colors)),
        "connected_regions_4_neighbor": int(total_regions),
        "small_component_threshold_pixels": SMALL_COMPONENT_THRESHOLD,
        "small_component_count": int(tiny_components),
        "small_component_pixels": int(tiny_pixels),
        "per_color": per_color,
    }


def boundary_metrics(source_rgb: np.ndarray, candidate_rgb: np.ndarray) -> dict:
    source_edges = source_edge_mask(source_rgb)
    candidate_edges = color_edges(candidate_rgb)
    candidate_near = cv2.dilate(candidate_edges, np.ones((5, 5), np.uint8), iterations=1)
    source_near = cv2.dilate(source_edges, np.ones((5, 5), np.uint8), iterations=1)
    retained = source_edges.astype(bool) & candidate_near.astype(bool)
    added = candidate_edges.astype(bool) & ~source_near.astype(bool)
    return {
        "source_edge_pixels": int(source_edges.sum()),
        "candidate_edge_pixels": int(candidate_edges.sum()),
        "source_edges_retained_within_2_pixels": int(retained.sum()),
        "source_edge_retention_fraction": float(retained.sum() / max(1, source_edges.sum())),
        "source_edge_pixels_without_candidate_edge_within_2_pixels": int(source_edges.sum() - retained.sum()),
        "candidate_edge_pixels_without_source_edge_within_2_pixels": int(added.sum()),
    }


def padded_label(image: Image.Image, title: str) -> Image.Image:
    header = 46
    canvas = Image.new("RGB", (image.width, image.height + header), "white")
    canvas.paste(image, (0, header))
    ImageDraw.Draw(canvas).text((12, 13), title, fill="black", font=ImageFont.load_default())
    return canvas


def make_comparison(source: Image.Image, baseline: Image.Image, candidate: Image.Image) -> Image.Image:
    baseline_full = baseline.resize(source.size, Image.Resampling.NEAREST)
    panels = [
        padded_label(source, "ORIGINAL SOURCE"),
        padded_label(baseline_full, "EXISTING 9-COLOR INDEXED TARGET"),
        padded_label(candidate, "BOUNDARY-AWARE FLAT COLOR (REVIEW CANDIDATE)"),
    ]
    result = Image.new("RGB", (sum(p.width for p in panels), panels[0].height), "white")
    x = 0
    for panel in panels:
        result.paste(panel, (x, 0))
        x += panel.width
    return result


def make_crop_comparison(
    source: Image.Image,
    baseline: Image.Image,
    candidate: Image.Image,
    box: tuple[int, int, int, int],
    title: str,
) -> Image.Image:
    baseline_full = baseline.resize(source.size, Image.Resampling.NEAREST)
    crops = [source.crop(box), baseline_full.crop(box), candidate.crop(box)]
    scale = min(2.0, 720 / max(crop.width for crop in crops))
    panels = []
    for label, crop in zip(("ORIGINAL", "INDEXED", "CANDIDATE"), crops):
        resized = crop.resize(
            (round(crop.width * scale), round(crop.height * scale)),
            Image.Resampling.NEAREST,
        )
        panels.append(padded_label(resized, f"{title}: {label}"))
    result = Image.new("RGB", (sum(p.width for p in panels), panels[0].height), "white")
    x = 0
    for panel in panels:
        result.paste(panel, (x, 0))
        x += panel.width
    return result


def crop_boundary_report(source_rgb: np.ndarray, candidate_rgb: np.ndarray) -> dict:
    boxes = {
        "faces_and_hair": (180, 70, 850, 510),
        "costumes_and_hands": (70, 300, 940, 1030),
        "leggings": (130, 900, 850, 1450),
        "dark_background": (0, 0, 1024, 430),
    }
    result = {}
    for name, (left, top, right, bottom) in boxes.items():
        result[name] = boundary_metrics(
            source_rgb[top:bottom, left:right], candidate_rgb[top:bottom, left:right]
        )
    return result


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)
    if not BASELINE.exists():
        raise FileNotFoundError(BASELINE)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "crops").mkdir(exist_ok=True)
    (OUTPUT / "previews").mkdir(exist_ok=True)

    source_image = Image.open(SOURCE).convert("RGB")
    baseline_image = Image.open(BASELINE).convert("RGB")
    source_rgb = np.asarray(source_image)
    palette_rgb, palette_lab = palette_from_source(source_rgb)
    candidate_rgb, method = flatten(source_rgb, palette_rgb, palette_lab)
    candidate_image = Image.fromarray(candidate_rgb, mode="RGB")

    source_copy = OUTPUT / "original_source.png"
    baseline_copy = OUTPUT / "existing_indexed_target.png"
    candidate_path = OUTPUT / "improved_flat_color.png"
    source_image.save(source_copy)
    baseline_image.save(baseline_copy)
    candidate_image.save(candidate_path)

    make_comparison(source_image, baseline_image, candidate_image).save(
        OUTPUT / "previews" / "side_by_side.png"
    )
    crop_boxes = {
        "faces_and_hair": (180, 70, 850, 510),
        "costumes_and_hands": (70, 300, 940, 1030),
        "leggings": (130, 900, 850, 1450),
        "dark_background": (0, 0, 1024, 430),
    }
    for name, box in crop_boxes.items():
        make_crop_comparison(
            source_image, baseline_image, candidate_image, box, name.replace("_", " ").upper()
        ).save(OUTPUT / "crops" / f"{name}.png")

    candidate_512 = candidate_image.resize((512, 512), Image.Resampling.NEAREST)
    candidate_512.save(OUTPUT / "improved_flat_color_comparable_512.png")

    source_lab = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    candidate_lab = cv2.cvtColor(candidate_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    delta_lab = np.linalg.norm(source_lab - candidate_lab, axis=2)
    report = {
        "status": "READY_FOR_USER_TEST",
        "milestone": "L1_NOT_APPROVED",
        "candidate_count": 1,
        "planner_invoked": False,
        "source": {
            "path": str(SOURCE.relative_to(ROOT)),
            "sha256": sha256(SOURCE),
            "dimensions_pixels": list(source_image.size),
            "coordinate_system_preserved": candidate_image.size == source_image.size,
            "source_modified": False,
        },
        "baseline": {
            "path": str(BASELINE.relative_to(ROOT)),
            "dimensions_pixels": list(baseline_image.size),
            "measurements": component_metrics(np.asarray(baseline_image)),
        },
        "candidate": {
            "path": str(candidate_path.relative_to(ROOT)),
            "dimensions_pixels": list(candidate_image.size),
            "palette": [rgb_to_hex(color) for color in np.unique(candidate_rgb.reshape(-1, 3), axis=0)],
            "measurements_full_resolution": component_metrics(candidate_rgb),
            "measurements_comparable_512": component_metrics(np.asarray(candidate_512)),
            "mean_opencv_lab_distance_from_original": float(delta_lab.mean()),
            "p95_opencv_lab_distance_from_original": float(np.percentile(delta_lab, 95)),
        },
        "method": method,
        "boundaries": {
            "overall": boundary_metrics(source_rgb, candidate_rgb),
            "focus_areas": crop_boundary_report(source_rgb, candidate_rgb),
        },
        "interpretation_limits": [
            "OpenCV Lab distances are engineering diagnostics, not calibrated CIEDE2000 values.",
            "Connected-region counts are label topology metrics and do not determine visual quality by themselves.",
            "The existing indexed baseline is 512x512; the candidate is measured both at native 1024x1536 and a nearest-neighbor 512x512 comparison size.",
            "No layer-planning or physical-feasibility claim is made before user visual approval.",
        ],
        "artifacts": {
            "original": str(source_copy.relative_to(ROOT)),
            "existing_indexed": str(baseline_copy.relative_to(ROOT)),
            "candidate": str(candidate_path.relative_to(ROOT)),
            "side_by_side": str((OUTPUT / "previews" / "side_by_side.png").relative_to(ROOT)),
            "crops": [
                str((OUTPUT / "crops" / f"{name}.png").relative_to(ROOT))
                for name in crop_boxes
            ],
        },
    }
    (OUTPUT / "validation_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "candidate": str(candidate_path),
        "palette": report["candidate"]["palette"],
        "native_metrics": report["candidate"]["measurements_full_resolution"],
        "comparable_512_metrics": report["candidate"]["measurements_comparable_512"],
        "boundary_retention": report["boundaries"]["overall"]["source_edge_retention_fraction"],
    }, indent=2))


if __name__ == "__main__":
    main()
