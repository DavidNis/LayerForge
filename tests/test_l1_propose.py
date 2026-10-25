"""L1 automatic proposal on project-owned inputs (no layer manifest).

The development image is the bow-face fixture of ``test_reveal_planner`` rendered
at 4x, area-downsampled to 900 px (anti-aliased edges) and saved as JPEG q85, so
the true palette and labels are known.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from layerforge_l1 import core
from layerforge_l1.core import propose as propose_module
from layerforge_l1.core.intake import IntakeConfig, load_image, prepare_target
from layerforge_l1.core.model import load_stack
from layerforge_l1.core.planning import PlanningError
from layerforge_l1.core.propose import NEEDS_DECISION, READY, ProposalConfig, propose

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_l0_core import PALETTE  # noqa: E402
from test_reveal_planner import _fixture  # noqa: E402

FAST = ProposalConfig(planning=core.PlanningConfig(scale=1))


def _render(labels: np.ndarray, size: int) -> np.ndarray:
    big = PALETTE[np.repeat(np.repeat(labels, 4, 0), 4, 1)]
    return cv2.resize(big, (size, size), interpolation=cv2.INTER_AREA)


@pytest.fixture(scope="module")
def dev_jpeg(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("l1_input") / "dev_bow.jpg"
    Image.fromarray(_render(_fixture(), 900)).save(path, "JPEG", quality=85)
    return path


@pytest.fixture(scope="module")
def proposal(dev_jpeg, tmp_path_factory):
    out = tmp_path_factory.mktemp("l1_proposal")
    return out, propose(dev_jpeg, out, config=FAST)


def _lab(rgb) -> np.ndarray:
    return cv2.cvtColor(np.asarray(rgb, np.uint8).reshape(-1, 1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(float)


def test_palette_recovers_every_true_color_including_small_highlights(dev_jpeg) -> None:
    rgb, opaque, info = load_image(dev_jpeg)
    assert info["format"] == "JPEG" and len(np.unique(rgb.reshape(-1, 3), axis=0)) > 1000
    prepared = prepare_target(rgb, opaque, core.StackGrid.for_planner(225.0, 1))
    palette = _lab(prepared["palette"])
    truth = _lab(PALETTE)
    assert len(palette) == len(truth) == 5
    nearest = np.linalg.norm(truth[:, None] - palette[None], axis=2).min(1)
    assert nearest.max() < 6  # every true color has its own palette entry
    # Target labels agree with the ground truth except along anti-aliased edges.
    side = prepared["target_rgb"].shape[0]
    truth_rgb = PALETTE[cv2.resize(_fixture(), (side, side), interpolation=cv2.INTER_NEAREST)]
    target_index = np.linalg.norm(_lab(prepared["target_rgb"])[:, None] - truth[None], axis=2).argmin(1)
    truth_index = np.linalg.norm(_lab(truth_rgb)[:, None] - truth[None], axis=2).argmin(1)
    assert (target_index == truth_index).mean() > 0.99
    assert prepared["measurements"]["unmatched_regions_at_least_min_area"] == 0


def test_dropped_detail_color_is_flagged_not_hidden(dev_jpeg) -> None:
    # The old 0.1 % share default (40 mm2) drops the white highlights; the residual check must see it.
    rgb, opaque, _ = load_image(dev_jpeg)
    prepared = prepare_target(rgb, opaque, core.StackGrid.for_planner(225.0, 1), IntakeConfig(min_color_area_mm2=40.0))
    measurements = prepared["measurements"]
    assert measurements["palette_size"] == 4
    assert measurements["unmatched_regions_at_least_min_area"] == 2
    flags = propose_module.intake_flags(measurements, IntakeConfig())
    assert "UNMATCHED_COLOR_REGIONS" in [f["code"] for f in flags]


def test_proposal_is_automatic_valid_and_cad_derived(proposal) -> None:
    out, result = proposal
    assert result["status"] == READY
    assert result["manual_layer_manifest_used"] is False and result["manual_corrections"] == []
    assert result["provenance"] == "auto-proposed"
    assert result["frame_color"] is None  # square input: no padding
    assert len(result["candidates"]) == 2 and all(c["status"] == "VALID" for c in result["candidates"])
    selected = next(c for c in result["candidates"] if c["name"] == result["selected"])
    assert selected["reconstruction_mismatched_pixels"] == 0
    assert selected["sheet_count"] == 6 and selected["repeated_colors"]
    stack = load_stack(out / "candidates" / result["selected"] / "stack")
    assert core.validate_stack(stack)["status"] == "PASS"
    assert stack.provenance["provenance"] == "auto-proposed"
    cad = result["cad"]
    assert cad["revalidation_pass"] and cad["all_step_valid_single_solid"] and cad["all_stl_watertight_single_component"]
    assert cad["stack_mismatch_px"] == 0 and cad["assembled_px_diff_from_planned"] == 0
    assert cad["source_comparison_mismatched_pixels"] == 0
    assert len(list((out / "selected" / "cad" / "step").glob("*.step"))) == 6
    assert (out / "selected" / "previews" / "assembled_front.png").is_file()
    codes = {f["code"] for f in result["flags"]}
    assert "DEPTH_ORDER_IS_STRUCTURAL" in codes and "NO_VALID_CANDIDATE" not in codes
    assert {row["color"] for row in result["color_topology"]} == set(result["palette"])
    assert json.loads((out / "proposal.json").read_text(encoding="utf-8"))["status"] == READY


def test_proposal_is_reproducible(dev_jpeg, proposal, tmp_path) -> None:
    out, first = proposal
    second = propose(dev_jpeg, tmp_path, config=ProposalConfig(planning=FAST.planning, export_cad=False))
    assert second["palette"] == first["palette"]
    assert (tmp_path / "intake" / "target.png").read_bytes() == (out / "intake" / "target.png").read_bytes()
    a = json.loads((out / "candidates" / first["selected"] / "stack" / "sheet_stack.json").read_text(encoding="utf-8"))
    b = json.loads((tmp_path / "candidates" / second["selected"] / "stack" / "sheet_stack.json").read_text(encoding="utf-8"))
    def bits(manifest):
        return [(s["id"], s["retained_bits_sha256"], s["visible_bits_sha256"]) for s in manifest["sheets"]]

    assert bits(a) == bits(b)


def test_transparent_non_square_input_is_framed_not_planned_as_art(tmp_path) -> None:
    rgb = _render(_fixture(), 512)[:, 64:448]  # portrait crop
    alpha = np.full(rgb.shape[:2], 255, np.uint8)
    alpha[:40, :40] = 0
    path = tmp_path / "portrait.png"
    Image.fromarray(np.dstack([rgb, alpha]), "RGBA").save(path)
    result = propose(path, tmp_path / "out", config=ProposalConfig(planning=FAST.planning, export_cad=False))
    assert result["status"] == READY
    assert result["frame_color"] and result["frame_color"] not in result["palette"]
    assert "TRANSPARENCY" in {f["code"] for f in result["flags"]}
    assert result["intake"]["artwork_size_mm"][0] < result["intake"]["artwork_size_mm"][1]
    stack = load_stack(tmp_path / "out" / "candidates" / result["selected"] / "stack")
    assert stack.frame_mask().any() and core.validate_stack(stack)["status"] == "PASS"


def test_gradient_input_is_flagged_as_not_flat() -> None:
    ramp = np.linspace(0, 255, 512, dtype=np.uint8)
    rgb = np.dstack([np.tile(ramp, (512, 1)), np.tile(ramp[:, None], (1, 512)), np.full((512, 512), 120, np.uint8)])
    measurements = prepare_target(rgb, None, core.StackGrid.for_planner(225.0, 1))["measurements"]
    codes = {f["code"] for f in propose_module.intake_flags(measurements, IntakeConfig())}
    assert "NOT_FLAT_COLOR" in codes


def test_infeasible_plan_stops_at_user_decision_without_edits(dev_jpeg, tmp_path, monkeypatch) -> None:
    obstruction = np.zeros((512, 512), bool)
    obstruction[100:110, 100:300] = True

    def infeasible(target, config, name):
        raise PlanningError("reveal closure is infeasible: some cells can never be supported",
                            {"closure_feasible": False}, obstruction)

    monkeypatch.setattr(propose_module, "plan_stack", infeasible)
    result = propose(dev_jpeg, tmp_path, config=FAST)
    assert result["status"] == NEEDS_DECISION and result["selected"] is None
    assert "NO_VALID_CANDIDATE" in {f["code"] for f in result["flags"] if f["severity"] == "BLOCKER"}
    assert all(c["status"] == "PLANNING_FAILED" for c in result["candidates"])
    assert result["candidates"][1]["reason"].startswith("skipped: closure infeasible")  # not recomputed
    saved = np.asarray(Image.open(tmp_path / "candidates" / "beam_area" / "obstruction.png")) > 0
    assert np.array_equal(saved, obstruction)
    assert not (tmp_path / "selected").exists() and result["manual_corrections"] == []


def test_cli_stack_propose(dev_jpeg, tmp_path, monkeypatch) -> None:
    from layerforge_l1 import cli

    monkeypatch.setattr(sys, "argv", ["layerforge_l1", "stack-propose", "--image", str(dev_jpeg),
                                      "--output", str(tmp_path), "--scale", "1", "--no-cad"])
    assert cli.main() == 0
    assert json.loads((tmp_path / "proposal.json").read_text(encoding="utf-8"))["status"] == READY
