"""Intake speckle cleanup (``core/intake.py`` ``merge_undrawn_boundaries``).

Grain inside one drawn color (an outline, a shaded band) can straddle the threshold between two palette
colors; per-pixel snapping then cuts it into a patchwork of both, which turns into extra cutouts and
sheets.  The cleanup merges touching regions whose shared boundary the source does not draw, and keeps
every drawn edge, whatever its size.  The fixtures are original synthetic drawings made here.
"""

from __future__ import annotations

import json
from dataclasses import replace

import cv2
import numpy as np
import pytest
from scipy import ndimage

from layerforge_l1.fullres_flatten import FlattenConfig, snap_to_palette

from layerforge_l1.core.intake import IntakeConfig, _color_regions, _lab, merge_undrawn_boundaries, prepare_target
from layerforge_l1.core.model import StackGrid
from test_l4_app import app  # noqa: F401 - shared fixture

CREAM, FUR, RED = (245, 232, 212), (236, 200, 160), (200, 62, 64)
DARK = (34, 26, 19)


def _grain(shape, amplitude, seed=1, blur=2.5):
    """Spatially correlated grain (patches of a few pixels, like a textured brush), mean 0."""

    rng = np.random.default_rng(seed)
    noise = ndimage.gaussian_filter(rng.normal(size=shape), blur)
    return noise / noise.std() * amplitude


def _drawing(side=400):
    """A cream card with a fur disc in a thick grainy dark outline, a red band, a pupil with a highlight,
    and two flat near-identical grays meeting at a sharp edge."""

    img = np.zeros((side, side, 3), np.float64)
    img[:] = CREAM
    yy, xx = np.mgrid[:side, :side]
    r = np.hypot(yy - 200, xx - 200)
    img[r < 150] = DARK
    img[r < 134] = FUR
    outline = (r >= 134) & (r < 150)
    img[outline] += _grain((side, side), 6.0)[outline, None]
    img[180:220, 90:150] = RED  # a red band on the fur, sharp edges
    pupil = np.hypot(yy - 140, xx - 250) < 18
    img[pupil] = (20, 16, 12)
    img[np.hypot(yy - 134, xx - 244) < 4] = (252, 252, 250)  # a small drawn highlight
    img[250:300, 170:200] = (70, 70, 72)  # two flat grays, close but drawn apart
    img[250:300, 200:230] = (86, 86, 88)
    return np.clip(img, 0, 255).astype(np.uint8)


def _snap(img, palette):
    """Intake's own snapping (bilateral, nearest Lab, majority filter, fragment merge)."""

    return snap_to_palette(img, np.asarray(palette, np.uint8), replace(FlattenConfig(), min_fragment_pixels=10)).astype(np.int64)


def _smooth_lab(img):
    """The smoothed Lab image intake measures boundaries on."""

    return _lab(cv2.bilateralFilter(img, 9, 24.0, 5.0).reshape(-1, 3)).reshape(img.shape)


def test_a_grainy_outline_split_between_two_near_blacks_becomes_one_color():
    img = _drawing()
    palette = np.array([CREAM, FUR, RED, (20, 15, 10), (44, 34, 26), (70, 70, 72), (86, 86, 88), (252, 252, 250)],
                       np.uint8)
    labels = _snap(img, palette)
    inside = np.ones(labels.shape, bool)
    yy, xx = np.mgrid[:400, :400]
    outline = (np.hypot(yy - 200, xx - 200) >= 136) & (np.hypot(yy - 200, xx - 200) < 148)
    assert {3, 4} <= set(np.unique(labels[outline]))  # the grain straddles both near-blacks: a patchwork
    before = _color_regions(labels, inside)[1]

    merged, changed = merge_undrawn_boundaries(labels, inside, _smooth_lab(img),
                                               palette, IntakeConfig())
    assert len(np.unique(merged[outline])) == 1  # one outline color
    assert _color_regions(merged, inside)[1] < before
    assert changed.sum() == (merged != labels).sum() and changed[outline].any()
    # drawn edges stay: the red band, the highlight, the two flat grays and the fur are untouched
    for box in ((slice(180, 220), slice(90, 150)), (slice(131, 138), slice(241, 248)),
                (slice(250, 300), slice(170, 230)), (slice(120, 280), slice(140, 160))):
        assert (merged[box] == labels[box]).all()


def test_a_drawn_edge_between_near_identical_colors_is_kept():
    img = np.zeros((120, 120, 3), np.uint8)
    img[:, :60] = (70, 70, 72)
    img[:, 60:] = (86, 86, 88)  # about 16 Lab apart: a near duplicate, but a real drawn edge
    palette = np.array([(70, 70, 72), (86, 86, 88)], np.uint8)
    labels = _snap(img, palette)
    merged, changed = merge_undrawn_boundaries(labels, np.ones(labels.shape, bool),
                                               _smooth_lab(img), palette, IntakeConfig())
    assert not changed.any() and (merged == labels).all()


def test_a_smooth_gradient_across_very_different_colors_is_not_merged():
    """Above ``undrawn_max_delta`` two colors are never merged, even across a gentle ramp."""

    ramp = np.linspace(0, 1, 200)[None, :, None]
    img = ((1 - ramp) * np.array(CREAM) + ramp * np.array(DARK)).repeat(40, 0).astype(np.uint8)
    palette = np.array([CREAM, (140, 129, 116), DARK], np.uint8)
    labels = _snap(img, palette)
    merged, _ = merge_undrawn_boundaries(labels, np.ones(labels.shape, bool),
                                         _smooth_lab(img), palette, IntakeConfig())
    assert set(np.unique(merged)) == {0, 1, 2}


def test_pixels_outside_the_artwork_are_never_changed():
    img = _drawing()
    palette = np.array([CREAM, FUR, RED, (20, 15, 10), (44, 34, 26), (70, 70, 72), (86, 86, 88), (252, 252, 250)],
                       np.uint8)
    labels = _snap(img, palette)
    inside = np.zeros(labels.shape, bool)
    inside[:, :200] = True
    merged, changed = merge_undrawn_boundaries(labels, inside, _smooth_lab(img),
                                               palette, IntakeConfig())
    assert (merged[:, 200:] == labels[:, 200:]).all() and not changed[:, 200:].any()


@pytest.fixture(scope="module")
def prepared_pair():
    img = _drawing()
    grid = StackGrid.for_planner(120.0, 1)
    on = prepare_target(img, None, grid, IntakeConfig(max_colors=10))
    off = prepare_target(img, None, grid, IntakeConfig(max_colors=10, clean_speckles=False))
    return on, off


def test_intake_reports_the_cleanup_and_off_reproduces_plain_snapping(prepared_pair):
    on, off = prepared_pair
    cleanup = on["measurements"]["speckle_cleanup"]
    assert cleanup["enabled"] and cleanup["pixels"] > 0
    assert cleanup["regions_after"] < cleanup["regions_before"]
    assert on["target_before_cleanup_rgb"] is not None
    # the image before the cleanup is exactly what intake makes with the cleanup off
    assert (on["target_before_cleanup_rgb"] == off["target_rgb"]).all()
    assert off["measurements"]["speckle_cleanup"] == {"enabled": False, "pixels": 0, "area_mm2": 0.0,
                                                      "regions_before": None, "regions_after": None}
    assert off["target_before_cleanup_rgb"] is None
    # fewer separate color regions, the palette is unchanged
    assert on["measurements"]["palette_size"] <= off["measurements"]["palette_size"]
    regions = lambda m: sum(c["source_regions_4"] for c in m["colors"])  # noqa: E731
    assert regions(on["measurements"]) < regions(off["measurements"])


def test_the_cleanup_is_a_proposal_note(prepared_pair):
    from layerforge_l1.core.propose import intake_flags

    on, off = prepared_pair
    codes = lambda p: [f["code"] for f in intake_flags(p["measurements"], IntakeConfig())]  # noqa: E731
    assert "COLOR_SPECKLES_MERGED" in codes(on) and "COLOR_SPECKLES_MERGED" not in codes(off)


def _drawing_png(folder) -> bytes:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "grainy_outline.png"
    cv2.imwrite(str(path), _drawing()[..., ::-1])
    return path.read_bytes()


def test_api_cleanup_setting_is_saved_reported_and_validated(app, tmp_path):
    """The project's intake reports the cleanup; ``clean_speckles`` turns it off (saved) and on again."""

    client, workspace = app
    name = client.ok("POST", "/api/projects?filename=grainy_outline.png&scale=1&plan=0", raw=_drawing_png(tmp_path))["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    p = client.state(name)["proposal"]
    assert p["cleanup"]["enabled"] and p["cleanup"]["pixels"] > 0 and "before_cleanup" in p["images"]
    assert "COLOR_SPECKLES_MERGED" in [f["code"] for f in p["flags"]]

    status, body = client.call("POST", f"/api/projects/{name}/propose", {"clean_speckles": "no", "plan": False})
    assert status == 400 and "clean_speckles" in body["error"]["message"]

    client.run(name, "propose", {"clean_speckles": False, "plan": False})
    p = client.state(name)["proposal"]
    assert p["cleanup"]["enabled"] is False and p["cleanup"]["pixels"] == 0 and "before_cleanup" not in p["images"]
    assert json.loads((workspace / name / "project.json").read_text(encoding="utf-8"))["proposal_settings"]["clean_speckles"] is False
    assert "COLOR_SPECKLES_MERGED" not in [f["code"] for f in p["flags"]]

    client.run(name, "propose", {"plan": False})  # a re-run without the setting keeps the saved choice
    assert client.state(name)["proposal"]["cleanup"]["enabled"] is False
    client.run(name, "propose", {"clean_speckles": True, "plan": False})
    assert client.state(name)["proposal"]["cleanup"]["pixels"] > 0


def test_browser_cleanup_panel_is_compact_and_toggles(app, tmp_path):
    """In a real browser (``tests/e2e/drive_cleanup.mjs``): one collapsed line, before / after on one image,
    one switch; turning it off and on re-runs intake."""

    import subprocess

    from test_l4_app import BROWSERS, ROOT, _node_ok

    client, workspace = app
    browser = next((b for b in BROWSERS if b.is_file()), None)
    if not _node_ok() or browser is None:
        pytest.skip("needs Node >= 22 and Edge/Chrome to drive the UI in a real browser")
    name = client.ok("POST", "/api/projects?filename=grainy_outline.png&scale=1&plan=0", raw=_drawing_png(tmp_path))["project"]
    client.wait({"id": client.state(name)["job"]["id"]})
    shots = ROOT / "artifacts" / "speckle_cleanup_v1"
    run = subprocess.run(["node", str(ROOT / "tests" / "e2e" / "drive_cleanup.mjs"), client.base + "/", str(browser), "-",
                          str(shots), name], capture_output=True, text=True, encoding="utf-8", timeout=900)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stderr[-3000:]
    r = json.loads(lines[-1])
    (shots / "drive_result.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    assert r["ok"], r.get("error", "") + "\n" + run.stderr[-3000:]
    assert r["console_errors"] == []

    collapsed = r["collapsed"]
    assert collapsed["open"] is False and "Color cleanup" in collapsed["summary"] and "color regions" in collapsed["summary"]
    after, before = r["after"], r["before"]
    assert after["views"] == ["After", "Before"] and after["active"] == "after" and "target.png" in after["src"]
    assert before["active"] == "before" and "target_before_cleanup.png" in before["src"]
    assert r["after_again"]["src"] == after["src"]
    assert after["words"] <= 40 and after["buttons"] <= 3  # light: one sentence, two view buttons, no clutter
    assert after["checked"] is True and not after["rerun"]
    assert r["toggled_off"]["checked"] is False and r["toggled_off"]["rerun"]  # Re-run appears only after a change
    off = r["off"]
    assert "off" in off["summary"] and off["checked"] is False and off["src"] == "" and not off["rerun"]
    assert json.loads((workspace / name / "project.json").read_text(encoding="utf-8"))["proposal_settings"]["clean_speckles"] is True  # switched on again
    assert r["on"]["checked"] is True and "color regions" in r["on"]["summary"]
