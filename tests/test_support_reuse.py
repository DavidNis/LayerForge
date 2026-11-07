"""Performance Step 7: the support reuse rules R1-R3 give bit-identical support checks.

Compared with ``reveal_planner.structural_support`` / ``_orphan_cores`` / ``unsupported_pixels`` on
hand-made topologies (narrow bridges, a removal that disconnects a distant region, diagonal-only
contact, enclosed cutouts with islands, material at the frame) and on random states; the tests also
check that the repair loops and every fallback path were exercised.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import pytest

from layerforge_l1 import reveal_planner as rp
from layerforge_l1 import support_reuse as proto
from layerforge_l1.reveal_planner import RevealPlanner

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_l0_core import CONFIG  # noqa: E402
from test_reveal_planner import _fixture  # noqa: E402

N, BAND = 160, 12


def _perimeter() -> np.ndarray:
    p = np.ones((N, N), bool)
    p[BAND:-BAND, BAND:-BAND] = False
    return p


def _kernel(size: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def _check(covered: np.ndarray, kernel: np.ndarray, stats: Counter, visible: np.ndarray | None = None) -> None:
    perimeter = _perimeter()
    covered = covered | perimeter
    expected = rp.structural_support(covered.copy(), perimeter, kernel)
    support, clean = proto.structural_support(covered.copy(), perimeter, kernel, stats)
    assert np.array_equal(support, expected)
    if clean is not None:
        assert np.array_equal(clean, cv2.erode(support.astype(np.uint8), kernel).astype(bool))
        assert not rp._orphan_cores(support, perimeter, kernel).any()  # clean: fully anchored
    for extra in ([visible] if visible is not None else []) + [covered & ~support]:
        material = support | extra
        want = rp._orphan_cores(material, perimeter, kernel)
        got = (rp._orphan_cores(material, perimeter, kernel) if clean is None
               else proto._orphans_beyond(material, clean, perimeter, kernel, stats))
        assert np.array_equal(got, want)


def _bar(mask, y0, y1, x0, x1):
    mask[y0:y1, x0:x1] = True
    return mask


def test_narrow_bridges_and_distant_disconnection() -> None:
    """A wide island joined to the frame by one bridge; the bridge's width crosses the 9 px footprint.
    Narrowing or removing the bridge disconnects material far away from it."""

    stats, kernel = Counter(), _kernel(9)
    for width in (6, 8, 9, 10, 12):
        covered = np.zeros((N, N), bool)
        _bar(covered, 60, 120, 60, 120)  # island far from the bridge's frame end
        _bar(covered, 85, 85 + width, BAND - 2, 61)  # bridge from the left frame band
        _check(covered, kernel, stats)
        removed = covered.copy()
        removed[85:85 + width, 30:34] = False  # cut the bridge in the middle
        _check(removed, kernel, stats)
    for width in (6, 9, 12):  # a visible strip added across the bridge gap
        covered = _bar(np.zeros((N, N), bool), 60, 120, 60, 120)
        visible = _bar(np.zeros((N, N), bool), 85, 85 + width, BAND - 2, 61)
        _check(covered, kernel, stats, visible)


def test_diagonal_contact_cutouts_islands_and_frame() -> None:
    stats, kernel = Counter(), _kernel(7)
    # Two squares touching only at a corner: the eroded cores meet diagonally (bridge / cut repair).
    for gap in (-1, 0, 1, 2):
        covered = np.zeros((N, N), bool)
        _bar(covered, BAND, 70, BAND, 70)
        _bar(covered, 70 + gap, 120, 70 + gap, 120)
        _check(covered, kernel, stats)
    # Enclosed cutout with an island inside it, the island joined by a thin and by a wide spoke.
    for spoke in (0, 3, 9):
        covered = _bar(np.zeros((N, N), bool), BAND, N - BAND, BAND, N - BAND)
        covered[50:110, 50:110] = False
        _bar(covered, 65, 95, 65, 95)
        if spoke:
            _bar(covered, 78, 78 + spoke, 40, 66)
        _check(covered, kernel, stats, _bar(np.zeros((N, N), bool), 100, 104, 70, 90))
    # Material only along the frame band, and a blob touching the frame from inside.
    _check(_bar(np.zeros((N, N), bool), BAND, BAND + 4, BAND, N - BAND), kernel, stats)
    _check(_bar(np.zeros((N, N), bool), BAND, 40, 70, 90), kernel, stats)
    _check(np.zeros((N, N), bool), kernel, stats)


@pytest.mark.parametrize("size", [5, 7, 9, 11])
def test_random_shapes_match(size) -> None:
    rng = np.random.default_rng(size)
    stats, kernel = Counter(), _kernel(size)
    for _ in range(60):
        covered = np.zeros((N, N), np.uint8)
        for _ in range(rng.integers(3, 12)):
            kind = rng.integers(3)
            x, y = (int(v) for v in rng.integers(0, N, 2))
            if kind == 0:
                cv2.circle(covered, (x, y), int(rng.integers(3, 25)), 1, -1)
            elif kind == 1:
                x2, y2 = (int(v) for v in rng.integers(0, N, 2))
                cv2.line(covered, (x, y), (x2, y2), 1, int(rng.integers(1, size + 4)))
            else:
                cv2.circle(covered, (x, y), int(rng.integers(3, 20)), 0, -1)  # cutout
        visible = np.zeros((N, N), np.uint8)
        cv2.line(visible, tuple(int(v) for v in rng.integers(0, N, 2)), tuple(int(v) for v in rng.integers(0, N, 2)),
                 1, int(rng.integers(1, size + 3)))
        _check(covered.astype(bool), kernel, stats, visible.astype(bool))
    assert stats["R1 first bridge check reused"] == 60


def test_asymmetric_kernel_disables_r1() -> None:
    stats = Counter()
    kernel = np.ones((6, 6), np.uint8)  # even size: not point-symmetric about the anchor
    assert not proto.symmetric(kernel) and proto.symmetric(_kernel(9))
    covered = _bar(_bar(np.zeros((N, N), bool), 60, 120, 60, 120), 85, 93, BAND - 2, 61)
    _check(covered, kernel, stats)
    assert stats["R1 first bridge check reused"] == 0 and stats["bridge check computed"] >= 1


@pytest.fixture(scope="module")
def planner() -> RevealPlanner:
    return RevealPlanner(_fixture(), CONFIG.reveal_config(None))


def test_unsupported_pixels_on_random_planner_states(planner) -> None:
    """Random covered / reveal cell sets of the bow-face planner (fragmented, often unsupported)."""

    rng = np.random.default_rng(7)
    checks = proto.ReuseChecks(planner)
    for _ in range(80):
        covered = rng.random(planner.cell_count) < rng.uniform(0.0, 0.9)
        reveal = ~covered & (rng.random(planner.cell_count) < rng.uniform(0.05, 1.0))
        assert np.array_equal(checks.unsupported_pixels(covered, reveal), planner._unsupported_pixels(covered, reveal))
        color = int(rng.choice(planner.colors))  # and a maximal reveal fixpoint with the prototype
        candidate = (~covered) & (planner.cell_color == color)
        while candidate.any():
            bad = checks.unsupported_pixels(covered, candidate)
            assert np.array_equal(bad, planner._unsupported_pixels(covered, candidate))
            if not bad.any():
                break
            cells = np.unique(planner.cell_map[bad])
            candidate[cells[cells >= 0]] = False
    assert checks.stats["covered_mask reused"] > 0


def test_every_path_was_exercised() -> None:
    """Unions of footprint-shaped stamps (dilated noise) make the eroded support touch stray core
    pixels only diagonally, so the bridge and cut repairs run; every repair / reuse path is required,
    including R3 on a support that was cut."""

    stats = Counter()
    rng = np.random.default_rng(3)
    r3_after_cut = 0
    for size in (7, 9):
        kernel = _kernel(size)
        for _ in range(60):
            seeds = (rng.random((N, N)) < rng.uniform(0.01, 0.045)).astype(np.uint8)
            covered = cv2.dilate(seeds, kernel).astype(bool)
            visible = cv2.dilate((rng.random((N, N)) < 0.004).astype(np.uint8), kernel).astype(bool)
            before = Counter(stats)
            _check(covered, kernel, stats, visible)
            delta = stats - before
            r3_after_cut += bool(delta["cut applied"] and (delta["R3 labeled new core pixels only"]
                                                            or delta["R3 no new core pixels"]))
    for path in ("R1 first bridge check reused", "R2 first cut check reused", "bridge check computed",
                 "bridge added", "cut check computed", "cut applied", "R3 labeled new core pixels only"):
        assert stats[path] > 0, (path, dict(stats))
    assert r3_after_cut > 0
    print(dict(stats), "R3 after a cut:", r3_after_cut)


def test_planner_switch_selects_reuse_or_original(planner, monkeypatch) -> None:
    calls = []
    original = RevealPlanner._unsupported_pixels
    monkeypatch.setattr(RevealPlanner, "_unsupported_pixels", lambda self, c, r: calls.append(1) or original(self, c, r))
    fresh = RevealPlanner(planner.labels, planner.config)
    covered = fresh.start_covered
    reveal = fresh.cell_color == fresh.colors[0]
    assert rp.SUPPORT_REUSE is True
    with_reuse = fresh.unsupported_pixels(covered, reveal)
    assert calls == [] and isinstance(fresh._support_checks, proto.ReuseChecks)
    monkeypatch.setattr(rp, "SUPPORT_REUSE", False)
    assert np.array_equal(fresh.unsupported_pixels(covered, reveal), with_reuse) and calls == [1]
