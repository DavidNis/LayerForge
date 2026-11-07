"""Support checks that reuse intermediate results (performance Step 7; ``reveal_planner.SUPPORT_REUSE``).

Same results as ``reveal_planner.structural_support`` / ``RevealPlanner._unsupported_pixels``, bit for bit,
with fewer full-grid connected-component passes.  Every reuse has a precondition; when it does not hold
the original full computation runs (``stats`` counts which path was taken).

R1  First bridge check.  ``A = _anchored(erode(covered))`` is the set of covered-core components that
    touch the perimeter.  With a point-symmetric kernel ``support = dilate(A) & covered`` has
    ``erode(support) = erode(dilate(A)) & erode(covered) >= A`` (closing is extensive), and every core
    pixel outside ``A`` lies in a covered-core component that does not touch the perimeter.  Hence the
    anchored part of ``erode(support)`` is exactly ``A`` and its orphans are ``erode(support) & ~A``: no
    labeling.  Valid only while ``A`` is still that set (first iteration, nothing cut).

R2  First cut check.  If the bridge loop left ``support`` unchanged since its last check, that check's
    (core, orphan) is reused instead of being recomputed.

R3  Final orphan check of ``support | visible``.  If the last check of ``support`` found no orphans,
    every pixel of ``core = erode(support)`` is anchored.  The material's core is ``core`` plus the new
    pixels ``D`` (erosion is monotone; checked).  A component of ``D`` is an orphan exactly when it is
    not 4-adjacent to ``core`` and contains no perimeter pixel: merging with ``core`` anywhere anchors
    it, however far away the perimeter is.  Only the bounding box of ``D`` (+1 px) is labeled.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import cv2
import numpy as np

from .reveal_planner import _CROSS, _anchored, _geodesic_reach, _orphan_cores


def _erode(mask: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    return cv2.erode(mask.astype(np.uint8), kernel).astype(bool)


def _dilate(mask: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    return cv2.dilate(mask.astype(np.uint8), kernel).astype(bool)


def symmetric(kernel: np.ndarray) -> bool:
    k = np.asarray(kernel)
    return k.shape[0] % 2 == 1 and k.shape[1] % 2 == 1 and np.array_equal(k, k[::-1, ::-1])


def structural_support(covered: np.ndarray, perimeter: np.ndarray, kernel: np.ndarray,
                       stats: Counter | None = None) -> tuple[np.ndarray, np.ndarray | None]:
    """``reveal_planner.structural_support`` plus, when known, the fully anchored core of the result.

    Returns ``(support, clean_core)``; ``clean_core`` is ``erode(support)`` when its last check found no
    orphans (every core pixel anchored), else ``None``.
    """

    stats = stats if stats is not None else Counter()
    covered_core = _erode(covered, kernel)
    anchored = _anchored(covered_core, perimeter)
    support = _dilate(anchored, kernel) & covered
    r1 = symmetric(kernel)
    last: tuple[np.ndarray, np.ndarray] | None = None  # (core, orphan) of the current ``support``
    for iteration in range(8):
        core = _erode(support, kernel)
        if iteration == 0 and r1:
            orphan = core & ~anchored
            stats["R1 first bridge check reused"] += 1
        else:
            orphan = core & ~_anchored(core, perimeter)
            stats["bridge check computed"] += 1
        last = (core, orphan)
        if not orphan.any():
            break
        bridge = _dilate(orphan, _CROSS) & covered_core & ~core
        if not bridge.any():
            break
        support |= _dilate(bridge, kernel)
        last = None
        stats["bridge added"] += 1
    for attempt in range(16):
        if attempt == 0 and last is not None:
            core, orphan = last
            stats["R2 first cut check reused"] += 1
        else:
            core = _erode(support, kernel)
            orphan = core & ~_anchored(core, perimeter)
            stats["cut check computed"] += 1
        last = (core, orphan)
        if not orphan.any():
            break
        size = 5 + 4 * attempt
        cut = _dilate(orphan, np.ones((size, size), np.uint8))
        anchored = _anchored(anchored & ~cut, perimeter)
        support = _dilate(anchored, kernel) & covered
        last = None
        stats["cut applied"] += 1
    clean = last[0] if last is not None and not last[1].any() else None
    return support, clean


def _orphans_beyond(material: np.ndarray, clean_core: np.ndarray, perimeter: np.ndarray, kernel: np.ndarray,
                    stats: Counter) -> np.ndarray:
    """``_orphan_cores(material)`` given a fully anchored ``clean_core`` of a subset of ``material`` (R3)."""

    core = _erode(material, kernel)
    if (clean_core & ~core).any():  # precondition (erosion is monotone); never expected
        stats["R3 precondition failed: full orphan check"] += 1
        return _orphan_cores(material, perimeter, kernel)
    added = core & ~clean_core
    out = np.zeros_like(core)
    if not added.any():
        stats["R3 no new core pixels"] += 1
        return out
    ys, xs = np.nonzero(added)
    y0, y1 = max(int(ys.min()) - 1, 0), min(int(ys.max()) + 2, core.shape[0])
    x0, x1 = max(int(xs.min()) - 1, 0), min(int(xs.max()) + 2, core.shape[1])
    window = added[y0:y1, x0:x1]
    count, labels = cv2.connectedComponents(window.astype(np.uint8), connectivity=4)
    joined = np.zeros(count, dtype=bool)
    near_core = _dilate(clean_core[y0:y1, x0:x1], _CROSS) & window
    joined[np.unique(labels[near_core])] = True
    joined[np.unique(labels[perimeter[y0:y1, x0:x1] & window])] = True
    joined[0] = True  # background
    out[y0:y1, x0:x1] = ~joined[labels]
    stats["R3 labeled new core pixels only"] += 1
    return out


class ReuseChecks:
    """``unsupported_pixels`` of one ``RevealPlanner`` with R1-R3 and a ``covered_mask`` memo
    (a planner is used by one thread at a time)."""

    def __init__(self, planner: Any):
        self.planner = planner
        self.stats: Counter = Counter()
        self._covered_key: bytes | None = None
        self._covered_mask: np.ndarray | None = None

    def covered_mask(self, covered_cells: np.ndarray) -> np.ndarray:
        key = np.packbits(covered_cells).tobytes() + str(covered_cells.shape).encode()
        if key != self._covered_key:
            self._covered_key, self._covered_mask = key, self.planner.covered_mask(covered_cells)
        else:
            self.stats["covered_mask reused"] += 1
        return self._covered_mask

    def unsupported_pixels(self, covered_cells: np.ndarray, reveal: np.ndarray) -> np.ndarray:
        p = self.planner
        visible = p.cells_mask(reveal)
        covered = self.covered_mask(covered_cells) | visible
        support, clean = structural_support(covered, p.perimeter, p.kernel, self.stats)
        reach = _geodesic_reach(support, support | visible, p.nub_steps)
        bad = visible & ~reach
        if not bad.any():
            material = support | visible
            if clean is None:
                self.stats["final orphan check: full (no clean core)"] += 1
                orphan = _orphan_cores(material, p.perimeter, p.kernel)
            else:
                orphan = _orphans_beyond(material, clean, p.perimeter, p.kernel, self.stats)
            bad = visible & _dilate(orphan, p.kernel)
        return bad
