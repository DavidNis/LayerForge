"""Performance Step 6 check: planning only (intake, every candidate, validation), compared with the golden run.

    python scripts/perf_step6_plan.py <project name> <out dir> [--count-calls] [--no-share] [--no-support-reuse]

Runs ``propose`` with the project's golden settings (artifacts/perf_v1/projects.json) and
``export_cad=False``: no CAD, previews or kit.  Records planning time per candidate, the whole-run
time and the process peak working set.  ``--count-calls`` also records every ``maximal_reveal`` /
``unsupported_pixels`` call by content (planner physics + arguments) to count repeated evaluations.

Comparison with artifacts/perf_v1/golden/<project>/proposal: every file under ``candidates/``
(sheet masks and visible masks by pixels, stack manifests and validation reports as normalized JSON,
obstruction maps) and, in proposal.json, the candidate rows, the selection and the flags (timings
excluded; the CAD block and CAD/STEP flags are absent here because no CAD is exported).
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from perf_baseline import _VOLATILE_KEY, _json_diff, _working_set, fingerprint  # noqa: E402

CAD_FLAGS = {"STEP_VERIFICATION_PENDING", "CAD_CHECK_FAILED"}


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if not _VOLATILE_KEY.search(k)}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def compare_with_golden(golden: Path, out: Path) -> dict:
    fg = {k: v for k, v in json.loads((golden / "fingerprint.json").read_text(encoding="utf-8")).items()
          if k.startswith("proposal/candidates/")}
    fn = {f"proposal/{k}": v for k, v in fingerprint(out).items() if k.startswith("candidates/")}
    problems = [f"only in golden: {k}" for k in sorted(set(fg) - set(fn))]
    problems += [f"only in new: {k}" for k in sorted(set(fn) - set(fg))]
    for key in sorted(set(fg) & set(fn)):
        a, b = fg[key], fn[key]
        for field in sorted((set(a) | set(b)) - {"json"}):
            if a.get(field) != b.get(field):
                detail = f" ({', '.join(_json_diff(a['json'], b['json'])[:6])})" if "json" in a else ""
                problems.append(f"{key}: {field} differs{detail}")
    pg = json.loads((golden / "proposal" / "proposal.json").read_text(encoding="utf-8"))
    pn = json.loads((out / "proposal.json").read_text(encoding="utf-8"))
    for field in ("candidates", "selected", "palette", "frame_color", "planning_config", "beam_scores"):
        if _strip(pg.get(field)) != _strip(pn.get(field)):
            problems.append(f"proposal.json {field} differs: {_json_diff(_strip(pg.get(field)), _strip(pn.get(field)))[:6]}")
    flags = lambda p: [_strip(f) for f in p["flags"] if f["code"] not in CAD_FLAGS]  # noqa: E731
    if flags(pg) != flags(pn):
        problems.append("proposal.json flags differ (excluding CAD/STEP flags)")
    return {"files_compared": len(set(fg) & set(fn)), "identical": not problems, "problems": problems}


class CallCounter:
    """Counts calls of the pure planner evaluations by content (does not change results)."""

    def __init__(self) -> None:
        self.keys = {"maximal_reveal": Counter(), "unsupported_pixels": Counter()}
        self.seconds = Counter()
        self.candidate = 0  # index of the running plan_stack call
        self.phase = "other"
        self.first_seen: dict[str, int] = {}
        # (function, phase) -> [calls, repeated within the same candidate, repeated from an earlier candidate,
        #                       seconds of all calls, seconds of the repeated calls]
        self.by_phase: dict[tuple[str, str], list[float]] = {}

    @staticmethod
    def physics(planner) -> str:
        if not hasattr(planner, "_count_physics"):
            h = hashlib.sha256()
            for array in (planner.cell_map, planner.cell_color, planner.perimeter, planner.kernel):
                h.update(np.ascontiguousarray(array).tobytes() + str(array.shape).encode())
            h.update(repr(planner.nub_steps).encode())
            planner._count_physics = h.hexdigest()
        return planner._count_physics

    def install(self) -> None:
        from layerforge_l1.reveal_planner import RevealPlanner

        counter = self
        for name in ("maximal_reveal", "unsupported_pixels"):
            original = getattr(RevealPlanner, name)

            def wrapped(planner, a, b, _original=original, _name=name):
                key = (counter.physics(planner), np.packbits(a).tobytes(),
                       b if isinstance(b, int) else np.packbits(b).tobytes())
                digest = hashlib.sha256(repr(key).encode()).hexdigest()
                counter.keys[_name][digest] += 1
                row = counter.by_phase.setdefault((_name, counter.phase), [0, 0, 0, 0.0, 0.0])
                seen = counter.first_seen.setdefault(digest, counter.candidate)
                repeat = counter.keys[_name][digest] > 1
                row[0] += 1
                row[1] += repeat and seen == counter.candidate
                row[2] += repeat and seen != counter.candidate
                t = time.perf_counter()
                try:
                    return _original(planner, a, b)
                finally:
                    dt = time.perf_counter() - t
                    counter.seconds[_name] += dt
                    row[3] += dt
                    row[4] += dt if repeat else 0.0

            setattr(RevealPlanner, name, wrapped)
        for phase in ("closure", "beam_search", "eliminate_sheets", "materialize"):
            original = getattr(RevealPlanner, phase)

            def phased(planner, *a, _original=original, _phase=phase, **k):
                outer, counter.phase = counter.phase, _phase if counter.phase == "other" else counter.phase
                try:
                    return _original(planner, *a, **k)
                finally:
                    counter.phase = outer

            setattr(RevealPlanner, phase, phased)

    def report(self) -> dict:
        out = {name: {"calls": sum(c.values()), "distinct": len(c), "repeated_calls": sum(c.values()) - len(c),
                      "seconds_inclusive": round(self.seconds[name], 1)} for name, c in self.keys.items()}
        out["by_phase"] = {f"{n} in {p}": {"calls": r[0], "repeated_same_candidate": r[1],
                                          "repeated_from_earlier_candidate": r[2], "seconds": round(r[3], 1),
                                          "seconds_of_repeats": round(r[4], 1)}
                           for (n, p), r in sorted(self.by_phase.items())}
        return out


def main() -> None:
    name, out = sys.argv[1], Path(sys.argv[2])
    count = "--count-calls" in sys.argv
    if "--no-support-reuse" in sys.argv:  # the support checks before Step 7
        import layerforge_l1.reveal_planner as reveal_planner

        reveal_planner.SUPPORT_REUSE = False
    if "--no-share" in sys.argv:  # the implementation before Step 6
        import layerforge_l1.reveal_planner as reveal_planner

        reveal_planner.SHARE_EVALUATIONS = False
    spec = next(p for p in json.loads((ROOT / "artifacts/perf_v1/projects.json").read_text(encoding="utf-8"))
                if p["name"] == name)
    from layerforge_l1.core import propose
    from layerforge_l1.core.intake import IntakeConfig
    from layerforge_l1.core.planning import PlanningConfig

    counter = CallCounter() if count else None
    if counter:
        counter.install()
    peak = [0]
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            peak[0] = max(peak[0], _working_set()[0])
            time.sleep(0.2)

    threading.Thread(target=sample, daemon=True).start()
    seconds = {"plan_stack": 0.0}
    original_plan = propose.plan_stack

    def timed_plan(*a, **k):
        if counter:
            counter.candidate += 1
        t = time.perf_counter()
        try:
            return original_plan(*a, **k)
        finally:
            seconds["plan_stack"] += time.perf_counter() - t

    propose.plan_stack = timed_plan
    planning = PlanningConfig(outer_sheet_mm=spec["outer_mm"], scale=spec["scale"], thickness_mm=spec["thickness_mm"])
    t0 = time.perf_counter()
    result = propose.propose(Path(spec["image"]), out, IntakeConfig(max_colors=spec["max_colors"]),
                             propose.ProposalConfig(planning=planning, export_cad=False))
    total = time.perf_counter() - t0
    stop.set()
    report = {
        "project": name,
        "share_evaluations": "--no-share" not in sys.argv,
        "support_reuse": "--no-support-reuse" not in sys.argv,
        "planning_seconds_all_candidates": round(seconds["plan_stack"], 1),
        "proposal_seconds_without_cad": round(total, 1),
        "candidates": [{k: c.get(k) for k in ("name", "status", "sheet_count", "planning_seconds")}
                       for c in result["candidates"]],
        "selected": result["selected"],
        "peak_working_set_mb": max(peak[0], _working_set()[1]) >> 20,
        "calls": counter.report() if counter else None,
        "golden_comparison": compare_with_golden(ROOT / "artifacts/perf_v1/golden" / name, out),
    }
    (out.parent / f"{out.name}_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "golden_comparison"}
                     | {"golden": {k: v for k, v in report["golden_comparison"].items() if k != "problems"},
                        "problems": report["golden_comparison"]["problems"][:10]}, indent=1))


if __name__ == "__main__":
    main()
