"""Performance Step 6: planner candidates share repeated ``maximal_reveal`` evaluations.

A shared result must be exactly what the evaluation returns, only for identical planner physics,
covered cells and color; plans (masks, order, reports) must be the same with and without sharing.
Uses the bow-and-face fixture of ``test_reveal_planner`` at scale 1 (576 px grid).
"""

from __future__ import annotations

import sys
import threading
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from layerforge_l1 import core, reveal_planner
from layerforge_l1.reveal_planner import RevealPlanner, shared_evaluations

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_l0_core import CONFIG, PALETTE  # noqa: E402
from test_reveal_planner import _fixture  # noqa: E402


@pytest.fixture(scope="module")
def planner() -> RevealPlanner:
    return RevealPlanner(_fixture(), CONFIG.reveal_config(None))


def _states(planner: RevealPlanner, count: int = 6) -> list[np.ndarray]:
    """Covered-cell sets met by the planner: the start, then one maximal reveal per color in turn."""

    covered, states = planner.start_covered.copy(), [planner.start_covered.copy()]
    for color in planner.colors * 2:
        reveal, _ = planner._maximal_reveal(covered, color)
        if reveal.any():
            covered = covered | reveal
            states.append(covered.copy())
        if len(states) >= count:
            break
    return states


def test_shared_results_equal_computed_ones(planner, monkeypatch) -> None:
    states = _states(planner, 4)
    calls = []
    original = RevealPlanner._maximal_reveal
    monkeypatch.setattr(RevealPlanner, "_maximal_reveal", lambda self, c, k: calls.append(1) or original(self, c, k))
    store: dict = {}
    for covered in states:
        for color in planner.colors:
            expected = original(planner, covered, color)
            for _ in range(2):
                with shared_evaluations(store):
                    reveal, iterations = planner.maximal_reveal(covered, color)
                assert np.array_equal(reveal, expected[0]) and reveal.dtype == bool and iterations == expected[1]
                reveal[:] = True  # a caller changing its result does not change the stored one
    assert len(calls) == len(store) == 4 * len(planner.colors)


def test_planners_with_other_physics_never_share(planner) -> None:
    """Different width margin or cells: separate keys, each result equals its own evaluation."""

    wider = RevealPlanner(planner.labels, replace(planner.config, planning_margin_pixels=4))
    stuck = np.zeros(planner.cell_count, dtype=bool)
    stuck[: planner.cell_count // 3] = True
    refined = planner.refined(stuck, 1.2, 0.3)
    keys = {p.physics_key() for p in (planner, wider, refined)}
    assert len(keys) == 3
    assert RevealPlanner(planner.labels, planner.config).physics_key() == planner.physics_key()
    store: dict = {}
    with shared_evaluations(store):
        for p in (planner, wider, refined):
            for color in p.colors:
                start = p.start_covered
                assert np.array_equal(p.maximal_reveal(start, color)[0], p._maximal_reveal(start, color)[0])
    assert {k[0] for k in store} == keys


def test_plans_are_identical_with_and_without_sharing(monkeypatch) -> None:
    target = PALETTE[_fixture()]
    store: dict = {}
    shared = []
    for score in ("area", "colors"):
        with shared_evaluations(store):
            shared.append(core.plan_stack(target, replace(CONFIG, beam_score=score), name=score))
    assert store
    monkeypatch.setattr(reveal_planner, "SHARE_EVALUATIONS", False)
    for (stack, report), score in zip(shared, ("area", "colors")):
        alone, alone_report = core.plan_stack(target, replace(CONFIG, beam_score=score), name=score)
        assert [s.color for s in stack.sheets] == [s.color for s in alone.sheets]
        for a, b in zip(stack.sheets, alone.sheets):
            assert np.array_equal(a.retained, b.retained) and np.array_equal(a.visible, b.visible)
        drop = lambda r: {k: v for k, v in r.items() if "seconds" not in k}  # noqa: E731
        assert drop(report) == drop(alone_report)


def test_sharing_is_scoped_and_can_be_switched_off(planner, monkeypatch) -> None:
    start, color = planner.start_covered, planner.colors[0]
    assert reveal_planner._shared_reveals.get() is None
    store: dict = {}
    with shared_evaluations(store):
        planner.maximal_reveal(start, color)
        seen = []
        thread = threading.Thread(target=lambda: seen.append(reveal_planner._shared_reveals.get()))
        thread.start()
        thread.join()
        assert seen == [None]  # another thread (another app job) has its own scope
        inner: dict = {}
        with shared_evaluations(inner):  # nested: the outer store is used
            planner.maximal_reveal(start, planner.colors[1])
        assert not inner and len(store) == 2
    assert reveal_planner._shared_reveals.get() is None
    monkeypatch.setattr(reveal_planner, "SHARE_EVALUATIONS", False)
    off: dict = {}
    with shared_evaluations(off):
        planner.maximal_reveal(start, color)
    assert not off
