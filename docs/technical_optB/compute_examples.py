"""Compute the worked-example values for the OptB technical PDF.

Read-only: calls the production functions on small inputs and on the
synthetic test fixture; writes only into docs/technical_optB/.
"""
import json, time, sys
from pathlib import Path
import cv2, numpy as np
from PIL import Image

from layerforge_l1.connectivity_repair import _width_parameters, audit_connection_width, audit_edge_connectivity
from layerforge_l1.layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from layerforge_l1.reveal_planner import (RevealConfig, RevealPlanner, physical_grid, width_kernel,
    structural_support, _anchored, _geodesic_reach, embed)
from layerforge_l1.fullres_flatten import majority_filter, merge_small_fragments
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_reveal_planner import _fixture

OUT = Path(__file__).resolve().parent
res = {}

# 1. Units / kernel for the OptB grid.
grid = physical_grid(RevealConfig())
k, params = width_kernel(grid)
res["grid"] = {"analysis": grid.analysis_pixels, "outer": grid.outer_pixels, "perimeter": grid.perimeter_pixels,
               "sheet_mm": grid.sheet_size_mm}
res["width_params"] = {key: params[key] for key in ("mm_per_pixel", "enforced_connection_width_mm",
    "requested_connection_width_pixels", "enforced_connection_width_pixels", "morphology_kernel_size_pixels")}
res["kernel13"] = k.tolist()
res["kernel13_area"] = int(k.sum())
res["nub_steps"] = RevealPlanner.__init__ and max(1, int(round(0.5 / params["mm_per_pixel"])))
mmpx = 225 * 512 / 576 / 2048
res["cells_optB"] = {"tile_px": max(2, int(round(2.0 / mmpx))), "min_pixels": max(1, int(round(0.9 / mmpx**2)))}

# 2. Tiny morphology example with a 3x3 ellipse kernel (structural_support itself).
K3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
res["kernel3"] = K3.tolist()
H, W = 9, 12
perim = np.zeros((H, W), bool); perim[:, 0] = True          # frame = left column
cov = np.zeros((H, W), bool)
cov[:, 0:2] = True                                           # frame + 1 px
cov[2:7, 1:7] = True                                         # 5-px-tall body, anchored on the left
cov[4, 7:10] = True                                          # 1-px-wide spur (too thin)
cov[1:4, 9:12] = True                                        # 3x3 blob only joined via the spur
core = cv2.erode(cov.astype(np.uint8), K3).astype(bool)
anch = _anchored(core, perim)
sup = structural_support(cov, perim, K3)
res["tiny"] = {"covered": cov.astype(int).tolist(), "core": core.astype(int).tolist(),
               "anchored": anch.astype(int).tolist(), "support": sup.astype(int).tolist()}

# 3. Flatten helpers on a tiny label image.
lab = np.zeros((7, 7), np.uint8); lab[:, 4:] = 1; lab[1, 1] = 1; lab[5, 5] = 0; lab[3, 3] = 2
res["flat_in"] = lab.tolist()
res["flat_major"] = majority_filter(lab, 3, 5).tolist()
res["flat_merge"] = merge_small_fragments(lab, 2).tolist()

# 4. Planner on the synthetic (original, non-proprietary) bow/face fixture.
labels = _fixture()
t0 = time.perf_counter()
pl = RevealPlanner(labels, RevealConfig(scale=2))
fx = {"cells": pl.cell_count, "kernel": pl.width_parameters["planning_kernel_size_pixels"],
      "mm_per_pixel": pl.width_parameters["mm_per_pixel"], "nub_steps": pl.nub_steps,
      "grid_outer": pl.grid.outer_pixels, "colors": pl.colors}
covered = pl.start_covered.copy()
first = []
for c in pl.colors:
    rev, it = pl.maximal_reveal(covered, c)
    first.append({"color": c, "candidate_cells": int(((~covered) & (pl.cell_color == c)).sum()),
                  "revealed_cells": int(rev.sum()), "fixpoint_iterations": it})
fx["first_step_maximal_reveals"] = first
clo = pl.closure()
fx["closure"] = {"feasible": clo["feasible"], "round_robin": clo["round_robin_sheet_count"]}
plan = pl.beam_search()
fx["beam"] = {"status": plan["status"], "colors": plan["front_to_back_colors"], "evaluations": plan["evaluations"],
              "cells_per_sheet": [int(r.sum()) for r in plan["reveals"]]}
cols, revs, log = pl.eliminate_sheets(plan["front_to_back_colors"], plan["reveals"])
fx["eliminated"] = log
sheets = pl.materialize(revs)
p = pl.grid.perimeter_pixels
rows = []
shown = np.full(pl.cell_map.shape, -1, np.int16)
for s, c in zip(sheets, cols):
    e, _ = audit_edge_connectivity(s["physical_mask"])
    w, _ = audit_connection_width(s["physical_mask"], s["visible_mask"][p:-p, p:-p], pl.grid, DEFAULT_OPTIMIZATION_CONFIG)
    shown[(shown < 0) & s["physical_mask"]] = c
    rows.append({"color": c, "four": e["four_neighbor_status"], "width": w["status"],
                 "retained": round(float(s["physical_mask"].mean()), 3), "holes_visible_px": int(s["visible_mask"].sum())})
fx["sheets"] = rows
target = embed(labels.astype(np.int16), pl.grid, -1)
fx["mismatch"] = int((shown[~pl.perimeter] != target[~pl.perimeter]).sum())
fx["seams"] = {k2: v for k2, v in pl.seam_report(revs).items() if k2 != "split_region_ids"}
ind = RevealPlanner(labels, RevealConfig(scale=2, split_regions=False))
ci = ind.closure()
fx["indivisible"] = {"cells": ind.cell_count, "feasible": ci["feasible"], "obstruction_cells": len(ci["obstruction_cells"])}
fx["seconds"] = round(time.perf_counter() - t0, 1)
res["fixture"] = fx

# figures for the fixture
PAL = np.array([[150, 200, 235], [20, 20, 20], [240, 200, 40], [200, 50, 50], [250, 250, 250]], np.uint8)
Image.fromarray(PAL[labels]).save(OUT / "fig" / "fixture_target.png")
tiles = []
for s, c in zip(sheets, cols):
    im = np.full(s["physical_mask"].shape + (3,), 255, np.uint8); im[s["physical_mask"]] = PAL[c]
    im = cv2.resize(im, (288, 288), interpolation=cv2.INTER_AREA)
    tiles.append(np.pad(im, ((4, 4), (4, 4), (0, 0)), constant_values=200))
Image.fromarray(np.concatenate(tiles, 1)).save(OUT / "fig" / "fixture_sheets.png")
dm = np.zeros(pl.native_cells.shape, np.int32)
for d, r in enumerate(revs): dm[r[pl.native_cells]] = d
Image.fromarray(cv2.applyColorMap((255 * dm / max(1, len(cols) - 1)).astype(np.uint8), cv2.COLORMAP_VIRIDIS)[:, :, ::-1]).save(OUT / "fig" / "fixture_depth.png")
(OUT / "examples.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in res.items() if k not in ("kernel13", "tiny", "flat_in", "flat_major", "flat_merge")}, indent=1))
