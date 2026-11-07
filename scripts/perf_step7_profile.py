"""Performance Step 7: where the time of one support check goes, on captured real states.

    python scripts/perf_step7_profile.py <states.npz>

Replays ``RevealPlanner._unsupported_pixels`` (the original) / ``structural_support`` step by step (same operations,
same order) with a timer per stage, checks that the replay equals the production function on every
state, and counts how often the bridge / cut repair loops and the orphan check run.
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from perf_step7_states import build_planner  # noqa: E402

from layerforge_l1 import reveal_planner as rp  # noqa: E402

T: Counter = Counter()
N: Counter = Counter()


def timed(name, fn, *a):
    t = time.perf_counter()
    out = fn(*a)
    T[name] += time.perf_counter() - t
    return out


def structural_support(covered, perimeter, kernel):
    covered_core = timed("support: erode covered", lambda: cv2.erode(covered.astype(np.uint8), kernel).astype(bool))
    anchored = timed("support: anchored (connected components)", rp._anchored, covered_core, perimeter)
    support = timed("support: dilate anchored", lambda: cv2.dilate(anchored.astype(np.uint8), kernel).astype(bool) & covered)
    for i in range(8):
        core = timed("bridge loop: erode", lambda: cv2.erode(support.astype(np.uint8), kernel).astype(bool))
        orphan = timed("bridge loop: anchored", lambda: core & ~rp._anchored(core, perimeter))
        N["bridge loop iterations"] += 1
        if not orphan.any():
            break
        N["bridge loop found orphans"] += 1
        bridge = cv2.dilate(orphan.astype(np.uint8), rp._CROSS).astype(bool) & covered_core & ~core
        if not bridge.any():
            break
        support |= cv2.dilate(bridge.astype(np.uint8), kernel).astype(bool)
    for attempt in range(16):
        core = timed("cut loop: erode", lambda: cv2.erode(support.astype(np.uint8), kernel).astype(bool))
        orphan = timed("cut loop: anchored", lambda: core & ~rp._anchored(core, perimeter))
        N["cut loop iterations"] += 1
        if not orphan.any():
            break
        N["cut loop cuts"] += 1
        size = 5 + 4 * attempt
        cut = cv2.dilate(orphan.astype(np.uint8), np.ones((size, size), np.uint8)).astype(bool)
        anchored = rp._anchored(anchored & ~cut, perimeter)
        support = cv2.dilate(anchored.astype(np.uint8), kernel).astype(bool) & covered
    return support


def unsupported(planner, covered_cells, reveal):
    visible = timed("cells_mask (reveal)", planner.cells_mask, reveal)
    covered = timed("covered_mask", planner.covered_mask, covered_cells) | visible
    support = structural_support(covered, planner.perimeter, planner.kernel)
    reach = timed("geodesic nub reach", rp._geodesic_reach, support, support | visible, planner.nub_steps)
    bad = visible & ~reach
    if not bad.any():
        N["orphan check run"] += 1
        orphan = timed("final orphan check", rp._orphan_cores, support | visible, planner.perimeter, planner.kernel)
        bad = visible & cv2.dilate(orphan.astype(np.uint8), planner.kernel).astype(bool)
    else:
        N["visible pixels unsupported (nub reach)"] += 1
    return bad


def main() -> None:
    data = np.load(sys.argv[1])
    planner, _ = build_planner(str(data["project"]))
    assert planner.cell_count == int(data["cell_count"])
    states = list(zip(data["covered"], data["reveal"]))
    t = time.perf_counter()
    expected = [planner._unsupported_pixels(c, r) for c, r in states]
    production = time.perf_counter() - t
    T.clear()
    t = time.perf_counter()
    replay = [unsupported(planner, c, r) for c, r in states]
    total = time.perf_counter() - t
    assert all(np.array_equal(a, b) for a, b in zip(expected, replay)), "replay differs from production"
    print(f"{len(states)} states; production {production:.2f} s ({1000 * production / len(states):.1f} ms/state), "
          f"replay {total:.2f} s; replay identical on all states")
    for name, seconds in sorted(T.items(), key=lambda kv: -kv[1]):
        print(f"  {seconds:7.2f} s  {100 * seconds / total:5.1f}%  {name}")
    print("  counts:", dict(N))
    print(f"  states with unsupported pixels: {sum(bool(b.any()) for b in expected)}")


if __name__ == "__main__":
    main()
