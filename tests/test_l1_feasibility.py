from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import cadquery as cq
import numpy as np
import trimesh
from PIL import Image
from shapely.geometry import Point

from layerforge_l1.geometry import (
    build_layer_solid,
    connected_component_count,
    hole_count,
    layer_area,
    validate_shared_sheet,
)
from layerforge_l1.pipeline import (
    DEFAULT_MANIFEST,
    DEFAULT_OUTPUT,
    METRIC_PIXELS,
    create_source_fixture,
    read_manifest,
    sha256,
    verify,
)
from layerforge_l1.raster import label_map


def manifest() -> dict:
    return read_manifest(DEFAULT_MANIFEST)


def validation() -> dict:
    return json.loads((DEFAULT_OUTPUT / "reports" / "validation.json").read_text(encoding="utf-8"))


def test_t_l01_layer_count_depth_and_repeated_color() -> None:
    data = manifest()
    layers = data["layers"]
    assert 4 <= len(layers) <= 8
    assert [layer["order_back_to_front"] for layer in layers] == list(range(len(layers)))
    zs = [layer["assembly_transform"]["translate_xyz_mm"][2] for layer in layers]
    assert zs == sorted(zs) and len(set(zs)) == len(zs)
    colors = [layer["color"]["hex"] for layer in layers]
    assert colors.count("#2A9D8F") == 2


def test_t_l02_multi_region_layer_is_one_sheet() -> None:
    gold = manifest()["layers"][2]
    visible = [
        region
        for region in gold["geometry"]["additive_regions"]
        if region["role"].startswith("visible_region")
    ]
    assert len(visible) == 2
    assert len(
        [region for region in gold["geometry"]["additive_regions"] if region["role"] == "connectivity_web"]
    ) == 2
    assert connected_component_count(layer_area(gold)) == 1


def test_t_l03_upper_cutout_reveals_lower_gold() -> None:
    data = manifest()
    coral = data["layers"][3]
    coral_area = layer_area(coral)
    gold_area = layer_area(data["layers"][2])
    reveal_point = Point(50.0, 38.0)
    assert hole_count(coral_area) >= 1
    assert coral_area.covers(reveal_point) is False
    assert gold_area.covers(reveal_point) is True
    report = validation()
    assert report["critical_detail_checks"]["gold_reveal_window"] == "PASS"
    assert report["layers"][3]["mesh_footprint_holes"] >= 1


def test_t_l04_all_layers_have_identical_outer_sheet_and_perimeter() -> None:
    data = manifest()
    for layer in data["layers"]:
        check = validate_shared_sheet(data, layer)
        assert check["pass"] is True
        assert check["outer_bounds_mm"] == [5.0, 5.0, 95.0, 75.0]
        assert check["outer_outline_hausdorff_mm"] <= 0.00001
        assert check["missing_shared_perimeter_area_mm2"] <= 1e-10


def test_t_l05_common_xy_and_separate_transforms() -> None:
    data = manifest()
    assert data["artwork"]["canvas_mm"] == [100.0, 80.0]
    for layer in data["layers"]:
        assert layer["assembly_transform"]["translate_xyz_mm"][:2] == [0.0, 0.0]
        assert layer["print_transform"] != layer["assembly_transform"]


def test_t_l06_step_stl_reopen_and_validate() -> None:
    report = validation()
    assert len(report["layers"]) == 6
    for row in report["layers"]:
        step = cq.importers.importStep(str(DEFAULT_OUTPUT / row["step"])).val()
        mesh = trimesh.load_mesh(DEFAULT_OUTPUT / row["stl"], process=True)
        assert row["status"] == "PASS"
        assert step.isValid()
        assert len(step.Solids()) == 1
        box = step.BoundingBox()
        assert np.allclose(
            [box.xmin, box.ymin, box.xmax, box.ymax], [5.0, 5.0, 95.0, 75.0], atol=1e-8
        )
        assert row["step_outer_bounds_max_delta_mm"] <= 0.00001
        assert isinstance(mesh, trimesh.Trimesh) and mesh.is_watertight
        assert abs(sum(s.Volume() for s in step.Solids()) - abs(mesh.volume)) < 0.5


def test_t_l07_all_cad_derived_previews_exist() -> None:
    data = manifest()
    preview_names = [f"{layer['id']}.png" for layer in data["layers"]]
    preview_names += [
        "assembled_front.png",
        "assembled_side.png",
        "exploded_layers.png",
        "comparison_overlay.png",
    ]
    for name in preview_names:
        path = DEFAULT_OUTPUT / "previews" / name
        assert path.is_file() and path.stat().st_size > 1_000
    side = np.asarray(Image.open(DEFAULT_OUTPUT / "previews" / "assembled_side.png").convert("RGB"))
    for color in {layer["color"]["hex"] for layer in data["layers"]}:
        rgb = np.asarray(tuple(int(color[i : i + 2], 16) for i in (1, 3, 5)))
        assert np.count_nonzero(np.all(side == rgb, axis=2)) > 10


def test_t_l08_source_projection_metrics_meet_controlled_thresholds() -> None:
    metrics = validation()["visual_metrics"]
    assert metrics["silhouette_iou"] >= 0.95
    assert metrics["mismatched_pixels"] > 0  # honest tessellation/raster differences are retained
    assert all(row["iou"] >= 0.95 for row in metrics["per_color"].values())


def test_t_l09_no_undeclared_disconnected_island_or_bed_overflow() -> None:
    report = validation()
    for row in report["layers"]:
        assert row["planned_components"] == 1
        assert row["exported_mesh_components"] == 1
        assert row["minimum_feature_morphology_check"] is True
        assert row["fits_220x220_mm_bed"] is True
        assert row["shared_sheet"]["pass"] is True
        assert row["exported_shared_sheet"]["pass"] is True


def test_t_l10_wrong_order_and_filled_hole_change_projection() -> None:
    data = manifest()
    canvas = tuple(data["artwork"]["canvas_mm"])
    correct = [(layer_area(layer), index + 1) for index, layer in enumerate(data["layers"])]
    correct_labels = label_map(correct, canvas, METRIC_PIXELS)

    wrong_order = correct.copy()
    wrong_order[2], wrong_order[3] = wrong_order[3], wrong_order[2]
    wrong_labels = label_map(wrong_order, canvas, METRIC_PIXELS)
    assert np.count_nonzero(correct_labels != wrong_labels) > 1_000

    filled = deepcopy(data["layers"][3])
    filled["geometry"]["cutout_regions"] = []
    filled_stack = correct.copy()
    filled_stack[3] = (layer_area(filled), 4)
    filled_labels = label_map(filled_stack, canvas, METRIC_PIXELS)
    assert np.count_nonzero(correct_labels != filled_labels) > 1_000


def test_t_l11_print_layout_cannot_change_assembly_geometry() -> None:
    layer = manifest()["layers"][1]
    changed = deepcopy(layer)
    changed["print_transform"] = {
        "translate_xyz_mm": [199.0, 177.0, 0.0],
        "rotate_xyz_deg": [0.0, 0.0, 90.0],
    }
    assert changed["assembly_transform"] == layer["assembly_transform"]
    assert layer_area(changed).equals_exact(layer_area(layer), tolerance=0.0)


def test_t_l12_deterministic_source_and_artifact_tree(tmp_path: Path) -> None:
    data = manifest()
    regenerated = tmp_path / "source.png"
    regenerated_hash = create_source_fixture(data, regenerated)
    assert regenerated_hash == data["artwork"]["sha256"]
    assert regenerated_hash == sha256(DEFAULT_OUTPUT / "source" / "source.png")
    report = verify(DEFAULT_OUTPUT)
    assert report["status"] == "READY_FOR_USER_TEST"
    assert report["planned_physical_layers"] == 6
    assert report["exported_printable_components"] == 6


def test_rejects_layer_missing_shared_outer_perimeter() -> None:
    data = manifest()
    broken = deepcopy(data["layers"][1])
    broken["geometry"]["additive_regions"] = [
        region
        for region in broken["geometry"]["additive_regions"]
        if region["role"] != "shared_outer_perimeter"
    ]
    result = validate_shared_sheet(data, broken)
    assert result["pass"] is False
    assert result["missing_shared_perimeter_area_mm2"] > 100.0


def test_rejects_layer_with_different_external_dimensions() -> None:
    data = manifest()
    broken = deepcopy(data["layers"][1])
    perimeter = next(
        region
        for region in broken["geometry"]["additive_regions"]
        if region["role"] == "shared_outer_perimeter"
    )
    perimeter["shape"]["bounds_mm"] = [5.0, 5.0, 94.0, 75.0]
    result = validate_shared_sheet(data, broken)
    assert result["pass"] is False
    assert result["outer_bounds_max_delta_mm"] >= 1.0
