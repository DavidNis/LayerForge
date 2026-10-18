from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from layerforge_l1 import PROJECT_ROOT


ROOT = PROJECT_ROOT / "artifacts" / "l1_width_aware_planner_v1"
CASES = ("bear_face", "fox_workshop")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _comparison(case: str) -> dict:
    return _json(ROOT / case / "reports" / "comparison.json")


def _manifest(case: str) -> dict:
    return _json(
        ROOT / case / "width_aware" / "automatic" / "layer_stack.json"
    )


def test_bear_regression_preserves_working_cutouts_and_width_pass() -> None:
    report = _comparison("bear_face")
    assert report["previous"]["layer_count"] == 7
    assert report["width_aware"]["layer_count"] == 7
    assert report["width_aware"]["width_failed_layers"] == 0
    assert report["width_aware"]["four_neighbor_failed_layers"] == 0
    assert report["fidelity"]["geometry_mismatched_pixels_against_indexed_target"] == 0
    assert report["fidelity"]["changed_pixels_from_previous_composite"] == 0
    assert report["fidelity"]["visible_connector_pixels"] == 0
    assert report["search"]["selected_mode"] == "STRICT_WIDTH_VALID"


def test_fox_width_aware_window_improves_actual_structural_geometry() -> None:
    report = _comparison("fox_workshop")
    assert report["previous"]["orphan_structural_core_components"] == 84
    assert report["width_aware"]["orphan_structural_core_components"] == 81
    assert report["previous"]["orphan_structural_core_pixels"] == 17159
    assert report["width_aware"]["orphan_structural_core_pixels"] == 14519
    assert report["previous"]["layer_count"] == 18
    assert report["width_aware"]["layer_count"] == 18
    assert not report["acceptance"]["layer_count_increased"]


def test_fox_layer_12_improves_and_layer_15_remains_explicitly_unresolved() -> None:
    changes = _comparison("fox_workshop")["per_layer_width_changes"]
    layer12 = next(row for row in changes if row["depth_back_to_front"] == 12)
    layer15 = next(row for row in changes if row["depth_back_to_front"] == 15)
    assert layer12["previous_orphan_structural_core_components"] == 9
    assert layer12["width_aware_orphan_structural_core_components"] == 6
    assert layer12["orphan_pixel_delta"] == -2640
    assert layer15["previous_orphan_structural_core_components"] == 12
    assert layer15["width_aware_orphan_structural_core_components"] == 12
    for row in (layer12, layer15):
        assert (ROOT / "fox_workshop" / row["comparison_map"]).is_file()


def test_fox_candidate_is_exact_but_rejected_as_width_valid() -> None:
    report = _comparison("fox_workshop")
    assert report["fidelity"]["geometry_mismatched_pixels_against_indexed_target"] == 0
    assert report["fidelity"]["changed_pixels_from_previous_composite"] == 0
    assert report["fidelity"]["visible_connector_pixels"] == 0
    assert not report["fidelity"]["details_deleted_or_enlarged"]
    assert not report["acceptance"]["candidate_width_valid"]
    assert report["acceptance"]["candidate_explicitly_unresolved"]
    assert not report["acceptance"]["global_infeasibility_claimed"]
    assert report["limitations"]["remaining_structural_failures"] == 81


def test_search_uses_actual_full_sheet_masks_and_corrected_kernel() -> None:
    report = _comparison("fox_workshop")
    changes = report["search_changes"]
    assert changes["actual_mask_geometry_during_planning"]
    assert changes["actual_common_perimeter_in_every_candidate"]
    assert changes["corrected_7x7_ellipse_during_planning"]
    assert changes["same_color_source_regions_can_be_deferred"]
    scope = report["search"]["scope"]
    assert scope["kind"] == "representative_problematic_full_sheet_window"
    assert scope["window_entry_state_preserved"]
    assert scope["window_exit_source_region_union_preserved"]
    assert not scope["cropped_geometry_used"]
    assert not scope["artificial_frame_used"]
    search = report["search"]["fallback_search"]
    assert search["morphology_kernel_pixels"] == 7
    assert search["evaluated_unique_actual_masks"] > 40_000


def test_all_planned_masks_share_outline_and_are_four_neighbor_connected() -> None:
    for case in CASES:
        manifest = _manifest(case)
        root = ROOT / case / "width_aware"
        dimensions = tuple(manifest["construction"]["common_outer_pixels"])
        perimeter = int(manifest["construction"]["continuous_perimeter_pixels"])
        for layer in manifest["layers"]:
            retained_path = root / layer["retained_material_mask"]
            cutout_path = root / layer["through_cutouts_mask"]
            assert retained_path.is_file()
            assert cutout_path.is_file()
            mask = np.asarray(Image.open(retained_path).convert("L")) > 0
            assert mask.shape[::-1] == dimensions
            assert np.all(mask[:perimeter])
            assert np.all(mask[-perimeter:])
            assert np.all(mask[:, :perimeter])
            assert np.all(mask[:, -perimeter:])
            count, _ = cv2.connectedComponents(
                mask.astype(np.uint8), connectivity=4
            )
            assert count - 1 == 1


def test_decorative_limitations_are_reported_separately() -> None:
    report = _comparison("fox_workshop")
    decorative = report["decorative_limitations"]
    assert decorative["thin_decorative_visible_components"] == 70
    assert decorative["status"] == "REPORTED_SEPARATELY_NOT_AUTOMATICALLY_RESOLVED"


def test_visual_package_contains_no_out_of_scope_outputs() -> None:
    for case in CASES:
        assert (ROOT / case / "original" / "source.png").is_file()
        assert (
            ROOT / case / "width_aware" / "previews" / "reconstructed_front.png"
        ).is_file()
        assert (
            ROOT / case / "width_aware" / "previews" / "exploded_layer_masks.png"
        ).is_file()
        assert (
            ROOT / case / "comparison" / "changed_pixels_previous_to_width_aware.png"
        ).is_file()
    assert not list(ROOT.rglob("*.stl"))
    assert not list(ROOT.rglob("*.step"))
    assert not list(ROOT.rglob("*.stp"))
    assert not list(ROOT.rglob("*.tsx"))

