"""Performance Step 7: capture real support-check states from a golden project's planning.

    python scripts/perf_step7_states.py <project> <out.npz> [max states]

Rebuilds the planner exactly as ``plan_stack`` does (golden intake target and planning config,
including the refinement ``closure_with_refinement`` applies), then runs its closure and beam search
and records the arguments of every ``unsupported_pixels`` call (covered cells, reveal cells, and the
phase: closure / beam) up to ``max states``.  The planner itself is not changed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def build_planner(project: str, refine: bool = True):
    from layerforge_l1.core.model import exact_index, hex_color
    from layerforge_l1.core.planning import PlanningConfig
    from layerforge_l1.reveal_planner import RevealPlanner

    golden = ROOT / "artifacts/perf_v1/golden" / project / "proposal"
    proposal = json.loads((golden / "proposal.json").read_text(encoding="utf-8"))
    target = np.ascontiguousarray(np.asarray(Image.open(golden / "intake" / "target.png").convert("RGB")))
    config = PlanningConfig(**proposal["planning_config"])
    labels, palette = exact_index(target)
    hexes = [hex_color(c) for c in palette]
    frame = hexes.index(config.frame_color.upper()) if config.frame_color else None
    planner = RevealPlanner(labels, config.reveal_config(frame))
    closure = None
    if refine:
        planner, closure, _ = planner.closure_with_refinement(config.refinement_extra_rings)
    return planner, closure


def main() -> None:
    project, out = sys.argv[1], Path(sys.argv[2])
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    planner, _ = build_planner(project)
    states: list[tuple[np.ndarray, np.ndarray, str]] = []
    phase = ["closure"]
    original = type(planner).unsupported_pixels

    class Enough(Exception):
        pass

    def record(self, covered, reveal):
        if len(states) >= limit:
            raise Enough
        states.append((covered.copy(), reveal.copy(), phase[0]))
        return original(self, covered, reveal)

    type(planner).unsupported_pixels = record
    try:
        planner.closure()
        phase[0] = "beam"
        planner.beam_search()
    except Enough:
        pass
    finally:
        type(planner).unsupported_pixels = original
    np.savez_compressed(out, covered=np.array([s[0] for s in states]), reveal=np.array([s[1] for s in states]),
                        phase=np.array([s[2] for s in states]), project=project, cell_count=planner.cell_count)
    print(f"{len(states)} states, {planner.cell_count} cells, grid {planner.cell_map.shape}")


if __name__ == "__main__":
    main()
