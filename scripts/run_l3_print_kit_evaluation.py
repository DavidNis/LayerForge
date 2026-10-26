"""L3 evaluation: build print kits for several distinct artworks and summarize them.

Usage (repository root, PYTHONPATH=src):

    python scripts/run_l3_print_kit_evaluation.py --output artifacts/l3_print_kit_v1 [--cases a,b,...]

Cases (planning uses the approved planner unchanged; edits are explicit L2 edits):

* ``bow_face_s1``          project-owned bow-face fixture, scale 1, unedited
* ``sailboat_s1``          project-owned sailboat fixture, scale 1, unedited (known thin slits)
* ``sailboat_s2``          same artwork at scale 2 (0.195 mm grid)
* ``sailboat_s1_edited``   scale 1 review project: thin slit filled, slit widened to 1.0 mm
                           (vector), artwork accepted, 6 mm x 2.4 mm rim on the back sheet
* ``bear_vector``          private: the L2 bear review project (rim, vector cuts, bridge, recolor)
* ``van_gogh_optB``        private: accepted L-1 reference masks, scale 4, 10 sheets, frame color

Private cases are skipped when their inputs are absent.  Kits and the summary are
evaluation output (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from layerforge_l1 import core  # noqa: E402
from layerforge_l1.core import review  # noqa: E402
from layerforge_l1.core.printkit import build_print_kit, verify_checksums  # noqa: E402

SAILBOAT = ROOT / "fixtures" / "l3_print_kit" / "sailboat_512.png"
CASES = ("bow_face_s1", "sailboat_s1", "sailboat_s2", "sailboat_s1_edited", "bear_vector", "van_gogh_optB")


def _planned(target: np.ndarray, scale: int, name: str, out: Path) -> Path:
    stack, _ = core.plan_stack(target, core.PlanningConfig(scale=scale), name=name)
    core.save_stack(stack, out / "input_stack")
    return out / "input_stack"


def _input(case: str, out: Path) -> Path | None:
    sail = np.asarray(Image.open(SAILBOAT).convert("RGB"))
    if case == "bow_face_s1":
        from test_l0_core import PALETTE
        from test_reveal_planner import _fixture

        return _planned(PALETTE[_fixture()], 1, "bow_face", out)
    if case == "sailboat_s1":
        return _planned(sail, 1, "sailboat", out)
    if case == "sailboat_s2":
        return _planned(sail, 2, "sailboat", out)
    if case == "sailboat_s1_edited":
        base = _planned(sail, 1, "sailboat", out)
        project = out / "review_project"
        stack, _ = review.start_review(base, project)
        front, back = stack.sheets[-1].id, stack.sheets[0].id
        mm = stack.grid.mm_per_pixel
        for op in (
            {"op": "fill_hole", "sheet": front, "at_mm": [153.125, 34.668]},
            {"op": "subtract", "sheet": front, "shape": {"type": "bar", "from_mm": [92 * mm, 48.046875],
                                                         "to_mm": [232 * mm, 48.046875], "width_mm": 1.0}},
            {"op": "accept_artwork"},
            {"op": "rim", "sheet": back, "enabled": True, "width_mm": 6.0, "total_height_mm": 2.4},
        ):
            review.apply_to_project(project, op)
        return project
    if case == "bear_vector":
        path = ROOT / "artifacts" / "l2_review_v1" / "bear_vector"
        return path if path.is_dir() else None
    if case == "van_gogh_optB":
        registry = json.loads((ROOT / "fixtures" / "reference_stacks.json").read_text(encoding="utf-8"))["van_gogh_optB"]
        layers, target = ROOT / registry["accepted_layers"], ROOT / registry["target"]
        if not layers.is_dir() or not target.is_file():
            return None
        planning = registry["planning"]
        stack = core.import_planner_output(layers, outer_sheet_mm=planning["outer_sheet_mm"],
                                           thickness_mm=planning["thickness_mm"], target_image=target,
                                           frame_color=planning["frame_color"], name="van_gogh_optB")
        core.save_stack(stack, out / "input_stack")
        return out / "input_stack"
    raise SystemExit(f"unknown case {case}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", default=",".join(CASES))
    args = parser.parse_args()
    summary_path = args.output / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    for case in args.cases.split(","):
        out = args.output / case
        if out.exists() and any(out.iterdir()):
            raise SystemExit(f"{out} exists; remove it to rerun {case}")
        out.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        source = _input(case, out)
        if source is None:
            summary[case] = {"skipped": "private input not present"}
            continue
        kit = build_print_kit(source, out / "kit")
        stack = core.load_stack(source)
        cmp = kit["comparisons"]
        summary[case] = {
            "status": kit["status"],
            "cad_problems": kit["cad_problems"],
            "sheets": len(kit["assembly_back_to_front"]),
            "scale": stack.grid.scale,
            "mm_per_pixel": stack.grid.mm_per_pixel,
            "edits": kit["review"]["edits"] if kit["review"] else 0,
            "vector_sheets": sum(1 for s in stack.sheets if s.geometry is not None),
            "rims": sum(1 for s in stack.sheets if s.rim is not None),
            "total_stack_height_mm": kit["total_stack_height_mm"],
            "front_view_vs_artwork_px": cmp["approved_artwork_vs_reviewed_stack"]["front_view_mismatched_px"],
            "reopened_cad_front_view_vs_artwork_px": cmp["approved_artwork_vs_reopened_cad_front_view"]["mismatched_px"],
            "max_abs_step_volume_error_pct": max(abs(r["step_volume_vs_expected_pct"])
                                                 for r in cmp["exported_vs_reopened_cad"].values()),
            "max_stl_vs_exact_geometry_area_mm2": max(
                (r["stl_vs_exact_geometry_area_mm2"] or 0.0) for r in cmp["reviewed_stack_vs_exported_geometry"].values()),
            "printability_counts": kit["printability"]["counts"],
            "findings": [{k: f.get(k) for k in ("severity", "file", "kind", "width_mm", "visible_area_mm2", "at_mm")}
                         for f in kit["printability"]["features"] if f["severity"] != "INFO"],
            "info_findings": sum(1 for f in kit["printability"]["features"] if f["severity"] == "INFO"),
            "checksums_ok": verify_checksums(out / "kit") == [],
            "seconds": round(time.perf_counter() - started, 1),
        }
        print(json.dumps({case: summary[case]["status"], "seconds": summary[case]["seconds"]}), flush=True)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
