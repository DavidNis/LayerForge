"""Performance Step 5 clean-ups: same results as before, only less repeated work.

* ``cad._downsample`` equals the original majority rule (``_downsample_reference``);
* ``cad.mask_to_geometry`` reuses an outline only for an identical mask and parameters;
* ``validate_stack`` reuses a report only when every input it reads is unchanged, and a saved and
  reloaded stack (how the app passes a stack from the proposal to the review and the kit) hits.

Uses the bow-and-face stack of ``test_l0_core`` (scale 1, 576 px grid).
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import shapely

from layerforge_l1 import core
from layerforge_l1.core import cad, editing, validation
from layerforge_l1.core.model import load_stack, save_stack

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_l0_core import CONFIG, PALETTE  # noqa: E402
from test_reveal_planner import _fixture  # noqa: E402


@pytest.fixture(scope="module")
def stack() -> core.SheetStack:
    return core.plan_stack(PALETTE[_fixture()], CONFIG, name="bow_face")[0]


@pytest.fixture(autouse=True)
def _fresh_caches():
    cad.clear_cache()
    validation.clear_cache()
    yield
    cad.clear_cache()
    validation.clear_cache()


def _copy(stack: core.SheetStack, **changes) -> core.SheetStack:
    sheets = [replace(s, retained=s.retained.copy(), visible=s.visible.copy()) for s in stack.sheets]
    fields = dict(name=stack.name, grid=stack.grid, sheets=sheets, target_rgb=stack.target_rgb,
                  frame_color=stack.frame_color, provenance=dict(stack.provenance))
    fields.update(changes)
    return core.SheetStack(**fields)


# -- downsample -------------------------------------------------------------------------
def test_downsample_equals_the_reference_rule(stack) -> None:
    rng = np.random.default_rng(5)
    grid = SimpleNamespace(outer_pixels=48)
    n = grid.outer_pixels * cad.SUPERSAMPLE
    for density in (0.0, 0.3, 0.5, 0.7, 1.0):
        image = (rng.random((n, n)) < density).astype(np.uint8)
        assert np.array_equal(cad._downsample(image, grid), cad._downsample_reference(image, grid))
    # Every block count 0..16, including the 8/16 tie (majority: >= 8 is material).
    image = np.zeros((n, n), np.uint8)
    for count in range(17):
        image[0:4, 4 * count:4 * count + 4].flat[:count] = 1
    down = cad._downsample(image, grid)
    assert down[0, :17].tolist() == [c >= 8 for c in range(17)]
    assert np.array_equal(down, cad._downsample_reference(image, grid))
    # Real sheet outlines, rasterized on the stack grid.
    for sheet in stack.sheets:
        geometry = cad._trace(sheet.retained, stack.grid.mm_per_pixel, cad.DEFAULT_SIMPLIFY_MM)
        assert np.array_equal(cad.rasterize_geometry(geometry, stack.grid), sheet.retained)


# -- traced outline reuse ---------------------------------------------------------------
def test_trace_reuse_returns_the_same_outline_as_tracing(stack, monkeypatch) -> None:
    mm = stack.grid.mm_per_pixel
    for sheet in stack.sheets:
        first = cad.mask_to_geometry(sheet.retained, mm, cad.DEFAULT_SIMPLIFY_MM)
        assert cad.mask_to_geometry(sheet.retained.copy(), mm, cad.DEFAULT_SIMPLIFY_MM) is first  # reused
        assert shapely.to_wkb(first) == shapely.to_wkb(cad._trace(sheet.retained, mm, cad.DEFAULT_SIMPLIFY_MM))
    monkeypatch.setattr(cad, "TRACE_CACHE", False)
    sheet = stack.sheets[0]
    assert cad.mask_to_geometry(sheet.retained, mm, cad.DEFAULT_SIMPLIFY_MM) is not \
        cad.mask_to_geometry(sheet.retained, mm, cad.DEFAULT_SIMPLIFY_MM)


def test_trace_reuse_needs_an_identical_mask_and_parameters(stack) -> None:
    mm, sheet = stack.grid.mm_per_pixel, stack.sheets[-1]
    calls = []
    original = cad._trace
    cad_trace = lambda *a: calls.append(1) or original(*a)  # noqa: E731
    try:
        cad._trace = cad_trace
        base = cad.mask_to_geometry(sheet.retained, mm, cad.DEFAULT_SIMPLIFY_MM)
        changed = sheet.retained.copy()
        ys, xs = np.nonzero(~changed[100:-100, 100:-100])
        changed[ys[0] + 100, xs[0] + 100] = True  # one more pixel of material
        other = cad.mask_to_geometry(changed, mm, cad.DEFAULT_SIMPLIFY_MM)
        assert other.area == pytest.approx(base.area + mm * mm)
        cad.mask_to_geometry(sheet.retained, mm, 0.0)  # other simplification
        cad.mask_to_geometry(sheet.retained, mm * 2, cad.DEFAULT_SIMPLIFY_MM)  # other pixel size
        cad.mask_to_geometry(sheet.retained.astype(np.uint8), mm, cad.DEFAULT_SIMPLIFY_MM)  # other dtype
        assert len(calls) == 5
        cad.mask_to_geometry(sheet.retained, mm, cad.DEFAULT_SIMPLIFY_MM)
        assert len(calls) == 5
    finally:
        cad._trace = original


# -- validation reuse -------------------------------------------------------------------
def _json(report) -> str:
    return json.dumps(report, sort_keys=True)


def test_reused_validation_equals_a_fresh_one_and_is_a_copy(stack, monkeypatch) -> None:
    calls = []
    original = validation._validate_stack
    monkeypatch.setattr(validation, "_validate_stack", lambda s: calls.append(1) or original(s))
    first = validation.validate_stack(stack)
    first["status"] = "edited by a caller"
    second = validation.validate_stack(stack)
    assert calls == [1] and second["status"] == "PASS"
    monkeypatch.setattr(validation, "VALIDATION_CACHE", False)
    assert _json(validation.validate_stack(stack)) == _json(second) and calls == [1, 1]


def test_saved_and_reloaded_stack_reuses_its_validation(stack, tmp_path) -> None:
    vector, _ = editing.apply_edit(stack, {"op": "subtract", "sheet": stack.sheets[0].id, "precision": "vector",
                                           "shape": {"type": "circle", "center_mm": [30.0, 30.0], "radius_mm": 3.0}})
    assert vector.sheets[0].geometry is not None
    for s in (stack, vector):
        path = tmp_path / f"stack_{len(list(tmp_path.iterdir()))}"
        save_stack(s, path)
        assert validation.validation_key(load_stack(path)) == validation.validation_key(s)


def test_validation_key_changes_with_every_input(stack, monkeypatch) -> None:
    from layerforge_l1.layer_optimization import DEFAULT_OPTIMIZATION_CONFIG

    base = validation.validation_key(stack)
    assert validation.validation_key(_copy(stack)) == base
    front = len(stack.sheets) - 1

    def sheet_change(**fields):
        copied = _copy(stack)
        copied.sheets[front] = replace(copied.sheets[front], **fields)
        return copied

    retained = stack.sheets[front].retained.copy()
    retained[300, 300] = not retained[300, 300]
    visible = stack.sheets[front].visible.copy()
    visible[300, 300] = not visible[300, 300]
    target = stack.target_rgb.copy()
    target[10, 10] = target[10, 10] ^ 1
    band = stack.grid.perimeter_pixels * stack.grid.mm_per_pixel
    changed = {
        "retained pixel": sheet_change(retained=retained),
        "visible pixel": sheet_change(visible=visible),
        "color": sheet_change(color="#123456"),
        "id": sheet_change(id="renamed"),
        "thickness": sheet_change(thickness_mm=stack.sheets[front].thickness_mm + 0.2),
        "rim": sheet_change(rim={"width_mm": band / 2, "total_height_mm": stack.sheets[front].thickness_mm + 1}),
        "geometry": sheet_change(geometry=cad._trace(stack.sheets[front].retained, stack.grid.mm_per_pixel, 0.0)),
        "order": _copy(stack, sheets=list(reversed(_copy(stack).sheets))),
        "fewer sheets": _copy(stack, sheets=_copy(stack).sheets[1:]),
        "target": _copy(stack, target_rgb=target),
        "no target": _copy(stack, target_rgb=None),
        "frame color": _copy(stack, frame_color=stack.sheets[0].color),
    }
    keys = {name: validation.validation_key(s) for name, s in changed.items()}
    assert base not in keys.values() and len(set(keys.values())) == len(keys)
    monkeypatch.setattr(validation, "DEFAULT_OPTIMIZATION_CONFIG",
                        replace(DEFAULT_OPTIMIZATION_CONFIG, nominal_connection_width_mm=1.0))
    assert validation.validation_key(stack) != base
    monkeypatch.undo()
    monkeypatch.setattr(validation, "OPENING_DIAGNOSTIC_MM", 1.0)
    assert validation.validation_key(stack) != base
    monkeypatch.undo()
    monkeypatch.setattr(validation, "audit_connection_width", lambda *a: None)
    assert validation.validation_key(stack) != base
    monkeypatch.undo()
    assert validation.validation_key(stack) == base


def test_a_failing_edit_is_not_hidden_by_a_reused_pass(stack) -> None:
    assert validation.validate_stack(stack)["status"] == "PASS"
    broken = _copy(stack)
    retained = broken.sheets[-1].retained
    p = stack.grid.perimeter_pixels
    retained[:p, :] = False  # perimeter band removed
    report = validation.validate_stack(broken)
    assert report["status"] == "FAIL" and report["failures"]
    assert validation.validate_stack(stack)["status"] == "PASS"
