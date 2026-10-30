"""Upscale & Retry: finer processing resolution for a failed proposal, same physical artwork.

Inputs are project-owned: ``_eyes`` (tests/test_refinement_and_details.py) doubled to 1024 px,
whose 2 px brows the scale-1 intake erases and a finer grid keeps (a visible change that must be
listed and approved), and the sailboat fixture for the app workflow.  In the app test the
import-scale failure is simulated (planning raises at scale 1 only) so the retry path runs in
seconds; the couple illustration is evaluated outside the repository (user artwork).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from layerforge_l1.core.intake import IntakeConfig
from layerforge_l1.core.model import StackGrid
from layerforge_l1.core.planning import PlanningConfig
from layerforge_l1.core.propose import ProposalConfig, propose
from layerforge_l1.core import upscale
from layerforge_l1.core.upscale import (BOUNDARY_FAILED, INSUFFICIENT_MEMORY, READY, boundary_check, carry_decisions,
                                        compare_targets, preview_upscale, resource_check)

from test_refinement_and_details import _eyes


@pytest.fixture(scope="module")
def previous(tmp_path_factory):
    root = tmp_path_factory.mktemp("upscale")
    image = np.repeat(np.repeat(_eyes(), 2, 0), 2, 1)
    Image.fromarray(image).save(root / "eyes.png")
    propose(root / "eyes.png", root / "p", IntakeConfig(max_colors=4),
            ProposalConfig(planning=PlanningConfig(outer_sheet_mm=150.0, scale=1), export_cad=False))
    return root


@pytest.mark.parametrize("factor", [2, 4])
def test_preview_keeps_physical_boundaries_and_lists_visible_changes(previous, factor) -> None:
    old = json.loads((previous / "p" / "proposal.json").read_text(encoding="utf-8"))
    view = preview_upscale(previous / "eyes.png", previous / "p", previous / f"x{factor}", factor, IntakeConfig(max_colors=4))
    assert view["status"] == READY and view["to_scale"] == factor
    assert view["boundaries"]["pass"] and len(view["boundaries"]["checks"]) == 9
    assert view["outer_sheet_mm"] == old["planning_config"]["outer_sheet_mm"] == 150.0
    assert view["artwork_mm_per_pixel"] * factor == pytest.approx(old["intake"]["artwork_mm_per_pixel"])
    # The brows the coarse intake erased come back: listed, with crops, never silently.
    assert view["changes"] and all((previous / f"x{factor}" / c["image"]).is_file() for c in view["changes"])
    assert view["change_summary"]["listed_changes"] == len(view["changes"])
    target = np.asarray(Image.open(previous / f"x{factor}" / "target.png"))
    assert target.shape[0] == factor * np.asarray(Image.open(previous / "p" / "intake" / "target.png")).shape[0]
    assert "no AI" in view["method"] and "does not necessarily resolve" in view["limitation"]


def test_boundary_check_fails_on_shift_stretch_or_escape(previous) -> None:
    old = json.loads((previous / "p" / "proposal.json").read_text(encoding="utf-8"))
    old_grid, new_grid = StackGrid.for_planner(150.0, 1), StackGrid.for_planner(150.0, 2)
    old_inside = np.ones((512, 512), bool)
    new = {**old["intake"], "artwork_mm_per_pixel": old["intake"]["artwork_mm_per_pixel"] / 2, "artwork_grid_px": 1024,
           "placement": {"placed_px": [1024, 1024], "offset_px": [0, 0], "scale_factor": 1.0}}
    good = boundary_check(old_grid, old["intake"], old_inside, new_grid, new, np.ones((1024, 1024), bool), (1024, 1024))
    assert good["pass"]
    shifted = {**new, "placement": {"placed_px": [1000, 1024], "offset_px": [24, 0], "scale_factor": 1.0}}
    bad = boundary_check(old_grid, old["intake"], old_inside, new_grid, shifted, np.ones((1024, 1024), bool), (1024, 1024))
    failed = {c["check"] for c in bad["checks"] if not c["pass"]}
    assert not bad["pass"] and {"artwork position unchanged", "artwork size unchanged (no stretch or crop)",
                                "aspect ratio preserved"} <= failed
    escaped = np.ones((1024, 1024), bool)
    narrow = {**new, "placement": {"placed_px": [1024, 900], "offset_px": [0, 62], "scale_factor": 1.0}}
    out = boundary_check(old_grid, old["intake"], old_inside, new_grid, narrow, escaped, (1024, 900))
    assert "complete artwork inside the aperture" in {c["check"] for c in out["checks"] if not c["pass"]}
    other = boundary_check(StackGrid.for_planner(150.0, 1), old["intake"], old_inside, StackGrid.for_planner(140.0, 2),
                           new, np.ones((1024, 1024), bool), (1024, 1024))
    assert "sheet size unchanged" in {c["check"] for c in other["checks"] if not c["pass"]}


def test_outline_jitter_is_not_listed_but_coherent_changes_are() -> None:
    old = np.zeros((100, 100, 3), np.uint8)
    old[20:60, 20:60] = 200
    new = np.repeat(np.repeat(old, 2, 0), 2, 1)
    new[40:120, 119:121] = 0              # outline moved by half a previous pixel: jitter
    new[150:160, 150:160] = 255           # a 10 px (5 previous px) new piece
    changes, listed, summary = compare_targets(old, new, np.ones((200, 200), bool), 2, 0.1)
    assert len(changes) == 1 and changes[0]["bbox_px"] == [150, 150, 160, 160] and changes[0]["new_hex"] == "#FFFFFF"
    assert summary["outline_jitter_pixels"] == 80 and listed.sum() == 100


def test_decisions_carry_to_matching_details_and_conflicts_are_reported() -> None:
    before = [{"id": "removed_0010_0010", "kind": "removed_by_cleanup", "bbox_px": [10, 10, 14, 14]},
              {"id": "color_0050_0050", "kind": "color_not_in_palette", "bbox_px": [50, 50, 60, 60]},
              {"id": "color_0052_0058", "kind": "color_not_in_palette", "bbox_px": [52, 58, 62, 70]},
              {"id": "removed_0090_0090", "kind": "removed_by_cleanup", "bbox_px": [90, 90, 92, 92]}]
    after = [{"id": "removed_0020_0020", "kind": "removed_by_cleanup", "bbox_px": [20, 20, 28, 28]},
             {"id": "color_0104_0112", "kind": "color_not_in_palette", "bbox_px": [104, 112, 120, 120]},
             {"id": "color_0300_0300", "kind": "color_not_in_palette", "bbox_px": [300, 300, 310, 310]}]
    decisions = {"removed_0010_0010": {"action": "remove"}, "color_0050_0050": {"action": "preserve"},
                 "color_0052_0058": {"action": "adjust", "color": "#112233"}, "removed_0090_0090": {"action": "remove"}}
    carried, report = carry_decisions(before, decisions, after, 2)
    assert carried == {"removed_0020_0020": {"action": "remove"}}
    assert report["conflicts"] == [{"detail": "color_0104_0112", "previous": ["color_0050_0050", "color_0052_0058"]}]
    assert report["not_found_at_new_resolution"] == ["removed_0090_0090"]
    assert report["new_details_with_default_action"] == ["color_0104_0112", "color_0300_0300"]


def test_memory_limits_are_checked_before_any_work(previous, monkeypatch) -> None:
    fits = resource_check(150.0, 2, available=64 * 2**30)
    assert fits["fits"] and fits["estimated_peak_gb"] == pytest.approx(1.03, abs=0.02)  # the measured run
    assert resource_check(150.0, 4, available=64 * 2**30)["estimated_minutes"] > 4 * fits["estimated_minutes"] - 0.5
    tight = resource_check(150.0, 8, available=512 * 2**20)
    assert not tight["fits"] and "close other programs" in tight["reason"]
    assert not resource_check(150.0, 32, available=2**50)["fits"]
    monkeypatch.setattr(upscale, "available_memory_bytes", lambda: 256 * 2**20)
    view = preview_upscale(previous / "eyes.png", previous / "p", previous / "low", 4, IntakeConfig(max_colors=4))
    assert view["status"] == INSUFFICIENT_MEMORY and view["boundaries"]["pass"]
    assert BOUNDARY_FAILED != INSUFFICIENT_MEMORY
