"""Performance Step 7: prototype vs production support checks on captured states.

    python scripts/perf_step7_compare.py <states.npz> [rounds]

For every state: ``structural_support`` (support mask), the orphan cores of ``support | visible``
and ``unsupported_pixels`` must be identical between ``reveal_planner`` (original ``_unsupported_pixels``) and ``support_reuse``.
Timing alternates production / prototype per round over all states; memory is the working-set peak
above the level before each pass.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from perf_baseline import _working_set  # noqa: E402
from perf_step7_states import build_planner  # noqa: E402

from layerforge_l1 import reveal_planner as rp  # noqa: E402
from layerforge_l1 import support_reuse as proto  # noqa: E402


def peak_during(fn):
    base, peak, stop = _working_set()[0], [0], threading.Event()

    def sample():
        while not stop.is_set():
            peak[0] = max(peak[0], _working_set()[0])
            time.sleep(0.01)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    t = time.perf_counter()
    fn()
    seconds = time.perf_counter() - t
    stop.set()
    thread.join()
    return seconds, max(peak[0] - base, 0) / 2**20


def main() -> None:
    data = np.load(sys.argv[1])
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    planner, _ = build_planner(str(data["project"]))
    states = list(zip(data["covered"], data["reveal"]))
    checks = proto.ReuseChecks(planner)
    mismatches = Counter()
    for covered_cells, reveal in states:
        visible = planner.cells_mask(reveal)
        covered = planner.covered_mask(covered_cells) | visible
        support = rp.structural_support(covered, planner.perimeter, planner.kernel)
        fast_support, clean = proto.structural_support(covered, planner.perimeter, planner.kernel)
        mismatches["support"] += not np.array_equal(support, fast_support)
        material = support | visible
        expected_orphans = rp._orphan_cores(material, planner.perimeter, planner.kernel)
        fast_orphans = (rp._orphan_cores(material, planner.perimeter, planner.kernel) if clean is None
                        else proto._orphans_beyond(material, clean, planner.perimeter, planner.kernel, Counter()))
        mismatches["orphan cores of support|visible"] += not np.array_equal(expected_orphans, fast_orphans)
        mismatches["unsupported_pixels"] += not np.array_equal(planner._unsupported_pixels(covered_cells, reveal),
                                                               checks.unsupported_pixels(covered_cells, reveal))
    print(f"{len(states)} states ({str(data['project'])}, {planner.cell_count} cells); mismatches: {dict(mismatches)}")
    print("  prototype paths:", dict(checks.stats))
    times = {"production": [], "prototype": []}
    memory = {"production": [], "prototype": []}
    for r in range(rounds):
        order = ("production", "prototype") if r % 2 == 0 else ("prototype", "production")
        for name in order:
            run = (lambda: [planner._unsupported_pixels(c, v) for c, v in states]) if name == "production" else \
                (lambda: [proto.ReuseChecks(planner).unsupported_pixels(c, v) for c, v in states])
            seconds, mb = peak_during(run)
            times[name].append(seconds)
            memory[name].append(mb)
    for name in times:
        print(f"  {name:10s} {', '.join(f'{t:.2f}' for t in times[name])} s "
              f"({1000 * min(times[name]) / len(states):.1f} ms/state best); extra peak MB {max(memory[name]):.0f}")
    print(f"  speedup (best of each): {min(times['production']) / min(times['prototype']):.2f}x")
    sys.exit(1 if any(mismatches.values()) else 0)


if __name__ == "__main__":
    main()
