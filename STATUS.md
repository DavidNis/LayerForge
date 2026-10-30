# STATUS: current project truth (fresh repository)

- **Spec version:** LayerForge V3, flat-illustration -> physical layer sheets.
- **Repository type:** NEW from scratch; no V2 source code to migrate.
- **Current milestone:** `L-1` feasibility.
- **State:** `AWAITING_PHYSICAL_TEST` after software feasibility checks.
- **User-approved V3 layer proof:** NO.
- **Software results:** L-G01 through L-G09 PASS on the original controlled
  `orbit_bloom_v1` fixture. L-G10 is PENDING; no physical result exists yet.

## Verified project direction

- V1 takes flat-color illustrated artwork and produces real printable sheets ordered by depth, not semantic object parts.
- V1 one filament color per physical sheet; same color can appear on multiple depth layers.
- Sheet geometry includes cutouts, optional raised rims/webs, deliberate island policy, and common XY alignment.
- Housing, slotted frame, glue graph, peg, mount and stand are outside current work.
- L-1 can use a manually reviewed layer plan; automatic interpretation of new images is a later, separately gated milestone L1.

## Agent must update after each session

```text
Date: 2026-10-16
Git branch/revision: main, starting revision 73d81a36e0e5e9e554512f7aa097de1050614838
Current milestone / state: L-1 / AWAITING_PHYSICAL_TEST
Source fixture provenance: original project-authored Orbit Bloom; CC0-1.0; no third-party assets
Actual source/dependencies built: minimal Python CLI, Boolean geometry/CAD exporter,
  read-back validator, STL-derived previews and source comparison; pinned versions in requirements-lock.txt
Manual vs automated work: source vectors and six-layer plan manual/reviewed; CAD Boolean
  construction, STEP/STL export, read-back, previews, metrics and checks automated
Exact commands and tests: artifacts/layer_feasibility/orbit_bloom_v1/reports/commands.log;
  12 PASS, 0 FAIL, 0 ERROR, 0 SKIP; 29 dependency deprecation warnings
Generated outputs and file paths: artifacts/layer_feasibility/orbit_bloom_v1/
CAD STEP/STL validation and preview parity: 6 planned layers / 6 measured components;
  all STEP solids valid; all STL watertight; one coral through-hole preserved;
  previews use reopened STL projections; silhouette IoU 0.995033 at 500x400
Failures / blockers: no software gate blocker; physical print, color, flatness, shadows,
  temporary registration and user aesthetic approval remain unverified
User print/appearance approval status: PENDING / NO sign-off
Next precise step: user imports the six STLs at 100% mm scale, prints and temporarily
  stacks them per reports/print_and_stack_guide.md, then reports L-G10 evidence
```

Do not mark a milestone approved without user approval. Do not report unrun checks as PASS.

## 2026-10-19 experimental L1 planner evidence

- The saved nine-color, 12-sheet turtle candidate is **not valid**: all sheets
  are 4-neighbor connected, but physical sheets 3 through 10 (back-to-front)
  fail the unchanged 1.2 mm retained-material-width validator. The visible
  reconstruction remains exact and contains zero visible connector pixels.
- A single bounded fixed-12-depth CP-SAT recovery run evaluated 20 distinct
  assignments and added 166 sound exact-prefix no-good cuts in 59.57 seconds.
  It found no fully valid stack before the candidate limit. Result: `UNKNOWN`,
  not infeasible and not approved.
- Evidence: `artifacts/l1_turtle_on_leaf_structural_diagnosis_v1/` and
  `artifacts/l1_turtle_on_leaf_global_recovery_v1/`.
- Current state for this experiment: `READY_FOR_USER_TEST`; L1 remains NOT
  APPROVED. No CAD, STL, STEP, GUI, image, palette, or manufacturing constraint
  was changed.

## 2026-10-20 thin-geometry reference checkpoint

- Direct triangle inspection of `Pikachu_scream_original.3mf` found nine
  watertight artwork plates at 109 x 144 mm plus an excluded box.
- With actual millimeter scale preserved, the unchanged 1.2 mm validator passes
  five artwork plates and rejects layers 3–6 for orphan structural cores.
- The reference also contains terminal retained strokes around 0.50–1.00 mm and
  negative openings around 0.22–0.40 mm. These demonstrate distinct feature
  roles; their presence in a 3MF is not physical-print proof.
- The rejected turtle masks contain decorative warnings, critical sole-path
  bottlenecks, and narrow openings, but no 4-neighbor-disconnected material.
- Evidence: `artifacts/l1_pikachu_thin_geometry_reference_v1/`. State remains
  `READY_FOR_USER_TEST`; L1 remains NOT APPROVED.

## 2026-10-20 turtle automatic width-repair checkpoint

- Revalidation at the correct 200 x 200 mm size clears layers 0-3 and 11;
  layers 4-10 still contain orphan structural cores under the unchanged 1.2 mm
  retained-width rule.
- A deterministic local repair operator traces existing narrow attachment
  paths and widens only those paths. A synthetic regression proves that hidden
  additions can repair a sheet without changing its composite.
- The saved turtle stack is cumulative and has zero unused foreground-occluded
  pixels on every sheet. Therefore its exact-preserving repair changes zero
  pixels and leaves seven failures unresolved.
- A separately labelled visible-change proposal makes all seven masks pass but
  changes 4,075 of 262,144 indexed-target pixels. It is not accepted and needs
  user approval; an exact result requires a different layer assignment.
- Evidence: `artifacts/l1_turtle_width_repair_200mm_v1/`. State is
  `READY_FOR_USER_TEST`; L1 remains NOT APPROVED.

## 2026-10-20 turtle visible-width review

- The existing visible-change proposal was reviewed without regeneration or
  modification. Of 4,075 changed pixels, 3,670 widen dark positive contours,
  404 change non-dark boundaries for structural repair, and one substitutes a
  dark color.
- Turtle identity, expression, silhouette, shell, limbs, tail, leaf, and color
  visibility remain recognizable. The result is incomplete as 1.2 mm contour
  normalization: the mouth retains many sub-threshold samples and the one
  narrow opening exposing a dark facial detail remains about 0.625 mm on the
  effective raster-width measure.
- Recommendation: `REVISE`. Evidence:
  `artifacts/l1_turtle_width_repair_200mm_review_v1/`. L1 remains NOT APPROVED.

## 2026-10-20 turtle contour-normalization checkpoint

- A separate deterministic visual-target stage now traces the original indexed
  black artwork as centerline graph segments and normalizes complete deficient
  segments on a 4x measurement grid. It does not use the rejected structural
  repair as its source and does not inspect physical-layer masks.
- At the explicit 200 x 200 mm artwork mapping, the single candidate changes
  1,906 pixels (0.727%), exclusively to the existing `#0E0F0D` contour color.
  All nine palette entries and every per-color connected-component count are
  preserved; filled eyes and solid facial marks remain unchanged.
- Thirty of 34 eligible deficient contour segments were normalized. Four were
  rejected because widening would remove/split an existing colored region; four
  additional sub-six-pixel junction fragments remain below 1.2 mm and were not
  forced. The main visible sun arc passes after normalization and avoids the
  asymmetric bulges in the rejected structural proposal.
- Recommendation: `REVISE` because the unresolved intersection/micro-junction
  cases prevent claiming complete 1.2 mm normalization. Evidence:
  `artifacts/l1_turtle_contour_normalization_200mm_v1/`. State remains
  `READY_FOR_USER_TEST`; L1 remains NOT APPROVED.

## 2026-10-20 turtle pre-layer image-preparation checkpoint

- One deterministic pass started from the unchanged 512 x 512 indexed turtle
  source at an explicit 200 x 200 mm mapping (0.390625 mm/pixel; 1.2 mm =
  3.072 pixels). No JPEG conversion, palette clustering, semantic redesign, or
  physical-layer planning was used.
- The pass inspected 110 contour segments. Thirty-four had representative
  samples below 1.2 mm; 31 were widened and verified, one filled dark region
  was correctly excluded from the stroke rule, and two contour/intersection
  cases remain unresolved because widening would alter colored-region topology.
- Seven one-pixel non-contour islands embedded in black boundaries were
  reassigned to the existing black color. No palette entry changed, no new
  suspicious tiny component appeared, and no semantic object or filled facial
  feature was removed.
- The candidate changes 1,969 pixels (0.751%). Recommendation is `REVISE`
  because two thin contours remain unresolved. Evidence:
  `artifacts/l1_turtle_pre_layer_preparation_200mm_v1/`. State is
  `READY_FOR_USER_TEST`; L1 remains NOT APPROVED.

## 2026-10-20 final two turtle contour corrections

- The accepted preprocessing candidate was used directly; global preprocessing
  and tiny-region cleanup were not rerun. Only reviewed contour IDs 1 and 108
  were eligible for edits.
- ID 1 increased from a measured 1.0150 mm minimum to 1.4050 mm; ID 108
  increased from 0.78125 mm to 1.4050 mm at the 200 x 200 mm mapping.
- The correction changes 519 pixels (0.1980%), all to the existing black
  contour color and all inside the two reviewed neighborhoods. The nine-color
  palette, ID 62, the turtle face, and every pixel outside those neighborhoods
  are preserved exactly.
- Focused tests: 2 PASS, 0 FAIL, 0 SKIP. Visual review found no new bulges,
  silhouette damage, or overlap errors. Evidence:
  `artifacts/l1_turtle_final_two_contours_200mm_v1/`.
- State: `PREPROCESSING_READY_FOR_USER_APPROVAL`. No layer planning was run;
  L1 remains NOT APPROVED.

## 2026-10-20 approved turtle global-planning experiment

- The locked final contour-corrected target was indexed losslessly: 512 x 512,
  nine colors, 54 four-connected source regions, zero changed source pixels.
- One bounded CP-SAT experiment tested fixed K=9 through K=18, evaluated 35
  complete assignments, and found no fully validated stack. K=9 was infeasible
  within the current indivisible-region model; all larger K outcomes remain
  bounded/unknown rather than proofs of geometric infeasibility.
- The best preserved 12-sheet diagnostic candidate reconstructs the target
  exactly and every sheet is 4-connected with a complete shared perimeter.
  Back-to-front sheets 4-10 fail the retained-width/orphan-core rule, so the
  candidate is explicitly REJECTED and not printable.
- A reporting-tail directory defect prevented the in-memory exact-prefix list
  from being persisted after the single run. The existing masks were reopened
  and validated without rerunning the solver; the directory defect is fixed.
- Evidence: `artifacts/l1_turtle_final_global_planning_200mm_v1/`. State is
  `READY_FOR_USER_TEST`; L1 remains NOT APPROVED.

## 2026-10-20 turtle physical-mask width-failure audit

- Exact saved masks prove 255 is retained material and 0 is empty; previews use
  the same polarity and hidden material is included. The solid-looking black
  sheet at back index 2 passes. The rejected black sheet is back index 9, whose
  many cutouts leave narrow retained webs.
- The original seven failures reproduce exactly. A physically calibrated 4x
  diagnostic confirms sheets 4-9 have near-threshold retained connections but
  clears sheet 10, making that rejection a native-raster false positive.
- For sheets 4-9, the refined rule connects at 1.074 mm and disconnects at
  1.270 mm; the best discrete estimate is approximately 1.172 +/- 0.098 mm.
  All sheets remain 4-neighbor connected before erosion.
- The rejected run also used a 177.778 mm artwork aperture inside a 200 mm outer
  sheet. Mapping the approved artwork itself to 200 mm makes the existing outer
  mask 225 mm, so the dimensional convention remains unresolved.
- Evidence: `artifacts/l1_turtle_final_width_failure_audit_v1/`. State remains
  `READY_FOR_USER_TEST`; L1 remains NOT APPROVED.

## 2026-10-20 turtle post-layer width repair

- Reused the saved 12-sheet global-planner assignment; no CP-SAT rerun, layer
  reorder, palette change, or source preprocessing was performed.
- Corrected the scale convention to 200 mm artwork / 225 mm outer sheet for the
  existing 512 px aperture and 32 px perimeter.
- Deterministic retained-path widening produced 12 four-connected sheets with
  complete shared perimeters, zero orphan structural cores, and zero remaining
  thin retained-stroke warnings under a 4x calibrated 1.2 mm audit.
- The repair changes 6,336 visible pixels (2.41699%), so it is a review
  candidate rather than an approved replacement for the locked artwork.
- Evidence: `artifacts/l1_turtle_post_layer_width_repair_200mm_v1/`. State is
  `READY_FOR_USER_TEST`; L1 remains NOT APPROVED.

## 2026-10-20 uniform-contour turtle stack rebuild

- The prior structural-neck dilation result is rejected because it created
  visible blobs and nonuniform outlines.
- A separate contour-consistency operator now re-strokes complete black and
  navy centerline paths at each path's stable intended width, never below
  1.2 mm. It does not locally dilate structural bottlenecks.
- New contour pixels are propagated through the existing cumulative masks:
  retained at and behind the selected color provider, and cut from every sheet
  in front. The resulting assembled preview is derived from those masks.
- All 12 sheets pass four-neighbor connectivity, the calibrated 1.2 mm
  retained-width check, shared perimeter, zero orphan-core, and zero thin
  visible-retained-component checks. The revised image differs by 6,264 pixels
  (2.38953%) and still requires user visual approval.
- Evidence: `artifacts/l1_turtle_uniform_contour_stack_200mm_v1/`. State is
  `READY_FOR_USER_TEST`; L1 remains NOT APPROVED.

## 2026-10-21 per-cell reveal-closure planner experiment

- H1-H5 diagnostics on the saved rejected 12-sheet stack
  (`artifacts/l1_turtle_h1_h5_diagnostics_v1/`): of the six sheets failing
  the 4x validator (back indices 4-9), five pass after removing hidden material
  that is not structurally reachable, with zero visible change. Only back index
  9 (front black, depth 2) is a real failure: 728 mm2 of visible black has no
  1.2 mm support because one black region (82% of all black) is forced to a
  single depth in front of later green/brown/yellow. Back index 10 fails only
  at 1x (raster artifact). No target region was found entirely below 0.8 mm
  except the 48 px navy detail (7.3 mm2).
- Per-cell planner on the identical locked target, no artwork edits
  (`artifacts/l1_turtle_reveal_planner_poc_v1_nomargin/`,
  `..._prune_only/`): closure FEASIBLE (all 4,005 cells supportable at
  1.2 mm); beam search 18 sheets; exact mask-derived reconstruction
  (0 mismatched 4x pixels); shared perimeter complete on all sheets;
  17/18 sheets PASS the unchanged validator at 4x and 8x; one black sheet failed
  on two single-pixel diagonal-staircase orphan cores. 11/54 regions are split
  across sheets (125 mm of same-color depth seams).
- Same planner with regions indivisible: stuck after 1 sheet.
- A fix aligning nub material with the validator's orphan rule passes the
  synthetic tests; its full turtle rerun was stopped by host memory pressure
  and has NOT completed. No all-sheets-PASS result exists yet.
- State: `READY_FOR_USER_TEST`; L1 NOT APPROVED. No CAD/STL exported.

## 2026-10-21 per-cell planner: first all-PASS turtle stack

- `artifacts/l1_turtle_reveal_planner_poc_v1/per_cell/`: 18 sheets from the
  locked turtle_on_leaf target, zero artwork edits, exact 4x reconstruction,
  all 18 sheets PASS the unchanged validator at 4x (4-neighbor, 1.2 mm
  orphan-core rule, complete shared perimeter). 8x re-audit NOT RUN (memory).
  11/54 regions split across sheets (136 mm of same-color depth seams).
- A beam-12 + sheet-elimination run was stopped by host memory pressure before
  finishing; no reduced-sheet result exists yet.
- Front black sheet is a connected lace of outline strokes: passes the 1.2 mm
  rule, mechanically delicate, needs physical evaluation. No CAD exported.
- State `READY_FOR_USER_TEST`; L1 NOT APPROVED.

## 2026-10-21 full-resolution flattening + per-cell planner

- New `src/layerforge_l1/fullres_flatten.py`: snaps `turtle_on_leaf.jfif` at its
  native 2048 px (0.098 mm/px) to the approved palette (bilateral smoothing,
  CIELAB nearest color, 5x5 majority, <0.4 mm2 fragment merge). No stroke is
  thickened. Strokes stay uniform; the old 512 px target's stair-stepping and
  width wobble are gone. The old navy `#17334B` was a one-row anti-aliasing
  artifact; the faithful target has 8 colors.
  Evidence: `artifacts/l1_turtle_fullres_flatten_v1/`.
- Planner on that target (`artifacts/l1_turtle_fullres_planner_v1/`): 16
  sheets, exact 4x reconstruction, all 16 PASS the unchanged validator at 4x;
  8 of 49 regions split across sheets (110 mm seams). 8x NOT RUN. Not user
  approved; no CAD exported.

## 2026-10-21 layer-count reduction on the full-resolution turtle

All runs: same 8-color 2048 px target, zero artwork edits, exact 4x
reconstruction, every sheet PASS on the unchanged validator at 4x (8x NOT RUN).
- 3.9 mm cells, area-first beam 6: 16 sheets (110 mm seams)
- 2.0 mm cells, area-first beam 6: 15 sheets (85 mm seams)
- 3.9 mm cells, color-finishing beam 8: 14 sheets (158 mm seams)
- 2.0 mm cells, color-finishing beam 8: **14 sheets (83 mm seams)** - current best,
  `artifacts/l1_turtle_fullres_combo_b8_v1/`
- 2.0 mm cells, color-finishing beam 16: stopped by host memory pressure, no result.
Hard lower bound by argument: 9 sheets (8 colors + black must precede and
follow the light-blue body). Not user approved; no CAD exported.

## 2026-10-21 second unseen image: van_gogh_p.jfif

- Automatic palette (k-means in CIELAB on non-edge pixels, merge < 14):
  10 colors. Full-resolution snap at 2048 px height = 200 mm (width 163.7 mm);
  side strips treated as frame on every sheet. Flattened target awaits user
  visual approval. Evidence: `artifacts/l1_van_gogh_v1/`.
- Planner (2 mm cells, color-finishing beam 8): 13 sheets, exact 4x
  reconstruction, all 13 PASS the unchanged validator at 4x (8x NOT RUN);
  2 of 36 regions split (32 mm seams). Lower bound 11 (10 colors + black
  before and after yellow). Same code and settings as the turtle; no
  per-image tuning. Character artwork is third-party IP: do not publish.

## 2026-10-21 van_gogh_p user-requested artwork edits

- Option A (cheek junction): 1.6 mm2 of sub-1.6 mm yellow spike tips at the
  left cheek set to black. Planner: 11 sheets (= lower bound), exact, all PASS
  at 4x, 1 region split (17 mm seams). `artifacts/l1_van_gogh_v1/planner_optA/`
- Option B (A + dark hat band -> black, 125 mm2): 10 sheets (= lower bound),
  exact, all PASS at 4x. `artifacts/l1_van_gogh_v1/planner_optB/`
- Both edits are proposals awaiting user visual approval. 8x NOT RUN; no CAD.

## 2026-10-21 van_gogh_p option B CAD export

- `scripts/export_reveal_stack_cad.py`: mask -> pixel-edge polygons (0.03 mm
  simplification) -> existing `geometry.extrude_geometry` (CadQuery) -> STEP +
  STL, 225 x 225 x 1.6 mm (thickness DESIGN_CHOICE), print placement z = 0.
- 10/10 layers: 1 STEP solid, watertight STL, 1 mesh component, exact bounds.
- Re-rasterized CAD (4x supersampled) equals planner masks: 0 px difference on
  every layer; unchanged validator PASS on every CAD-derived mask; stack rebuilt
  from CAD matches the planned stack with 0 px mismatch.
- Time: 122.9 s export + read-back, 10.4 s re-validation.
  Evidence: `artifacts/l1_van_gogh_v1/cad_optB/`. Physical print PENDING.

## 2026-10-24 user approval of L-1 result and L0 gate exception

- USER_REQUIRED: the user explicitly approved the L-1 result and authorized
  starting L0 — Reusable Geometry Core.
- L-G10 physical print-and-assembly gate: **PENDING / NOT VERIFIED**. No
  physical print, stacking, or appearance evidence exists in this repository.
- The user authorized starting L0 despite the outstanding physical test. This
  is recorded as an explicit **milestone-gate exception**, not as evidence that
  the physical test passed.
- L0 regression baseline: van_gogh_p option B, 10 sheets, committed at
  `789b02a` (`artifacts/l1_van_gogh_v1/planner_optB/`, `cad_optB/`), recorded
  by `scripts/capture_l0_reference_baseline.py` in
  `artifacts/l0_core_v1/baseline/pre_refactor_baseline.json`.

## 2026-10-24 L0 reusable geometry core

- State: **L0 IMPLEMENTED — full-size reference regeneration NOT VERIFIED**
  (not yet `READY_FOR_USER_TEST`). L-G10 remains PENDING (gate exception above).
- New `src/layerforge_l1/core/` (model/manifest, planner wrapper, validation,
  CAD export + STL read-back, previews, reference regression) and `stack-*`
  CLI commands; API and data flow in `ARCHITECTURE.md`. `reveal_planner.py`,
  validators and `geometry.extrude_geometry` unchanged; `mask_to_geometry`
  moved verbatim into `core/cad.py`.
- Tests: before 72 PASS / 51 FAIL / 0 SKIP; after 97 PASS / 51 FAIL / 0 SKIP.
  The 51 failures are pre-existing: tests read artifact trees deleted in
  commit `2c375e5`; none involve the reveal planner or the L0 core.
  New `tests/test_l0_core.py`: 25 PASS (original synthetic fixture, scale 1).
- Accepted reference via the new core, verified: legacy migration of the
  committed van_gogh option-B masks validates PASS (10 sheets, 0 reconstruction
  mismatches over 3,432,448 px); all 10 committed STL files re-rasterize to
  the planner masks with 0 px difference; STL-derived previews equal the plan.
- NOT VERIFIED: `stack-reference --name van_gogh_optB` (full re-planning +
  re-export, compared against
  `artifacts/l0_core_v1/baseline/pre_refactor_baseline.json`). The run was
  stopped by host low-memory protection during planning and produced no result.

## 2026-10-25 L0 test-suite cleanup and focused geometry regression

- State: **L0 — READY_FOR_USER_TEST**. L-G10 physical test is still PENDING (gate
  exception above). L1/GUI not started. The reveal planner and its helpers are unchanged
  (empty diff against `789b02a`).
- Tests: before 97 PASS / 51 FAIL / 0 SKIP; after **91 PASS / 0 FAIL / 0 SKIP**
  (`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest`, 29 dependency
  deprecation warnings). Classification: 43 obsolete (removed), 8 valuable (converted to
  regenerated fixtures), 0 genuine non-artifact failures. A further 15 vacuous passes
  were removed and one synthetic regression test was added. Details are in
  `tests/REMOVED_TESTS.md`. No test reads a deleted or untracked artifact folder.
- Fixed: `pipeline._guide` path bug that tied the orbit_bloom build to one output folder.
- Focused regression `stack-reference --name van_gogh_optB --from-accepted`: **PASS**
  (485 s, exit 0). 10 sheets; validation PASS; independent pre-refactor baseline
  identical (mask bits, colors/order, components, holes, validator results); 10 STEP
  valid single solids; 10 STL watertight single components; STL footprint XOR vs accepted
  CAD 0.0 mm² on every sheet with 0 count differences; reconstruction from reopened STL
  0 / 3,432,448 px mismatched; STL-derived assembled preview 0 px diff. Evidence:
  `artifacts/l0_core_v1/reference_van_gogh_optB_from_accepted/reference_regression.json`.
- Not run: full-size re-planning (`stack-reference` without `--from-accepted`). It
  exercises only the unchanged planner and was previously stopped by host memory.

## 2026-10-25 L0 approved

- USER_REQUIRED: the user approved **L0 — Reusable Geometry Core** based on the software
  verification above, and approved the 58 test removals listed in `tests/REMOVED_TESTS.md`.
  A full-size planner re-run was explicitly not required.
- L0 state: **APPROVED**. L-G10 physical print-and-stack test: **PENDING / NOT VERIFIED**.
- L1 not started; it awaits a separate user instruction.

## 2026-10-25 repository cleanup before L1 (user-approved, committed)

- Removed through `git rm` (reversible from `097327e`): 15,421 tracked files, 481.1 MB.
  This covers the docs PDF build intermediates, all superseded turtle experiment folders
  and packages, non-reference van_gogh runs and review screenshots, 15 turtle/fox
  experiment driver scripts, and 2 turtle stored-artifact test files. The latter were
  replaced by a project-owned physical-scale audit test.
- Dated entries above that cite `artifacts/l1_turtle_*`, `planner_optA/` or removed
  turtle scripts are history; those files remain retrievable at commit `097327e`.
- Accepted van_gogh option-B reference, L0 baseline, all `src/` modules and all fixtures kept.
- L0 unchanged and operational; planner untouched. L-G10 physical test still PENDING; L1 not started.
- USER_REQUIRED decisions: the untracked L0 run outputs were deleted after their masks and
  previews were shown byte-identical to a fresh regeneration. The accepted van_gogh CAD
  (including STEP), all user-supplied images/3MF files and the experimental algorithms and
  scripts are kept until reviewed. No history rewrite.

## 2026-10-25 L1 automatic layer-candidate proposals

- State: **L1 APPROVED** by the user (2026-10-25) on the reported results and the accepted
  bear output. L-G10 physical printing still PENDING. Bow-knot outline loss stays a
  documented limitation (no L1 correction required). L2 not started.
- New: `core/intake.py`, `core/propose.py`, CLI `stack-propose --image <png|jpg> --output <dir>`.
  It reuses `extract_palette`/`snap_to_palette`, `plan_stack` (unchanged), `validate_stack`,
  `export_stack`/`revalidate_cad` and `generate_previews`. Manual work per image: none
  (no manifest, no corrections).
- Development input: the bow-face fixture rendered as an anti-aliased 900 px JPEG q85. Code
  and defaults were frozen before the held-out runs (`artifacts/l1_eval_v1/freeze.json`).
- Held-out 1 `bear_face.png`: **PROPOSAL_READY_FOR_REVIEW**, 66 s. 7 sheets, 7 colors, both
  candidates valid with the same order. Reconstruction 0 px mismatched; 7 STEP valid single
  solids; 7 STL watertight; reopened CAD 0 px. Lost unflagged: the faint darker outline of
  the bow knot merged into teal.
- Held-out 2 `heldout_octopus_submarine.png`: **NEEDS_USER_DECISION**. 12-color cap reached;
  28 unmatched regions (495 mm2) flagged; snapping changed 4.8 % of pixels (flagged). Reveal
  closure is infeasible: 12,004 px (1.14 % of the artwork) at fish fins, bubbles, tentacle
  tips, levers and crab legs; map at `candidates/beam_area/obstruction.png`. No edits made.
  The second candidate took 80 min because of host memory paging (under 1 GB free), then
  closure was fixed to run once.
- Tests: after the final code, `tests/test_l1_propose.py` + `tests/test_l0_core.py` = 35 PASS / 0 FAIL.
  An earlier complete-suite attempt was stopped by host low-memory protection.

### Final verification (2026-10-25, no code changes)

- User decision D-V3-026: bear accepted as the automatic-conversion example; the octopus
  `NEEDS_USER_DECISION` stop is correct L1 behaviour; no automatic simplification in L1.
- Complete suite: `python -m pytest` = **97 PASS / 0 FAIL / 0 SKIP / 0 ERROR** in 192 s
  (89 pre-L1 + 8 L1; no L0 regression). 29 warnings, all pyparsing deprecations raised inside
  matplotlib/cadquery.
- Bear re-verified read-only from the saved outputs (no planning, no export): frozen and
  post-fix runs have bit-identical target and sheet masks; `validate_stack` PASS; 7/7 STEP
  reopen as one valid solid each, 225 x 225 x 1.6 mm; 7/7 STL watertight, 1 component;
  `revalidate_cad` PASS: 0 px STL and geometry diff per sheet, stack mismatch 0 px,
  reconstruction from STL 0 of 1,048,576 px mismatched, all sheets 4-neighbor and 1.2 mm width PASS.
- Octopus kept as a negative evaluation (not re-planned): status NEEDS_USER_DECISION, both
  candidates PLANNING_FAILED, obstruction maps present.
- Freeze check: of the 13 frozen source files only `core/intake.py` and `core/propose.py`
  differ (the two disclosed post-evaluation fixes); `reveal_planner.py`, `planning.py`,
  `fullres_flatten.py`, CAD and validation code are byte-identical to the freeze.

## 2026-10-25 L2 reviewed layer editor

- State: **READY_FOR_USER_TEST**; L2 NOT APPROVED. L0 and L1 approved; L-G10 physical
  printing still PENDING. Not committed. L3/L4 not started; no GUI.
- New: `core/editing.py` (edit operations on a `SheetStack`), `core/review.py` (review
  project: base copy, edit log, undo/revert by replay, verify, preview, gated export), CLI
  `stack-edit {init, inspect, apply, undo, revert, preview, validate, verify, export}`,
  `tests/test_l2_editor.py` (15 tests). Changed: `core/model.py` (optional `Sheet.rim`,
  `SheetStack.review`, schema 1.1.0 read/write, 1.0.0 unchanged), `core/cad.py` (rim fused
  on the perimeter band; volume/height rows), `core/commands.py` (`stack-edit`;
  `stack-export` refuses edited stacks that fail validation), `core/__init__.py` (exports).
  Planner, validators and thresholds unchanged.
- Operations: reorder, move, recolor, thickness, rim (add/remove), add, subtract (shape
  primitive, bar or mask PNG), fill_hole, remove_island, bridge, merge, split (by region at
  a point, shape or mask), accept_artwork. Each edit is classified (`visible_artwork`,
  `artwork_reference`, `hidden_geometry`, `physical_parameters`, `no_change`) and validated
  with `validate_stack`. Manual vs automatic: every edit is an explicit user request; no
  automatic correction.
- Tests: complete suite `python -m pytest` = **112 PASS / 0 FAIL / 0 SKIP / 0 ERROR** in 245 s
  (97 previous + 15 L2). 29 warnings, all pyparsing deprecations inside matplotlib/cadquery.
- Focused L0 regression after the model/CAD changes: `stack-reference --name van_gogh_optB
  --from-accepted` (output in a scratch folder, tracked report untouched) = PASS: baseline,
  accepted masks and accepted CAD identical; 10/10 STEP valid single solids, STL watertight,
  stack mismatch 0 px (393 s).
- Integration test (L1 proposal of the project-owned bow JPEG -> 10 edits applied, 7 kept, incl. an
  invalid ring island, its bridge, a visible fill that was reverted, recolor + accept_artwork
  -> save -> reload -> replay -> validate -> export): reload and replay bit-identical; validate
  PASS; all STEP valid single solids, STL watertight, each reopened STL equals the reviewed
  mask, rim sheet 3.0 mm high in STL and STEP, other sheets 1.8 mm.
- Manual CLI run in Windows PowerShell on the accepted bear proposal (private output,
  git-ignored `artifacts/l2_review_v1/`): init; rim 8 mm x 3.2 mm on the back sheet; hidden
  4 mm circle cut; hidden square ring (DRAFT_INVALID, 1 island) then 3 mm bridge (valid);
  teal recolor (visible, 73,451 px, DRAFT_INVALID) then undo; verify replay identical;
  preview vs base: 0 visible / 4,943 hidden px changed; export 40 s: validation PASS, CAD
  revalidation PASS, 7/7 STEP single valid solids, 7/7 STL watertight, stack mismatch 0 px,
  reconstruction from STL 0 of 1,048,576 px, rim height matches.
- Found in the manual run and fixed: Windows PowerShell 5.1 strips quotes inside JSON
  arguments; a shape given that way crashed with a traceback. Non-object shapes/regions
  now fail with a clear error pointing to `--op-file`.
- Limitations: edits are raster masks on the stack grid (0.39 mm/px at scale 1, 0.195 mm/px at
  scale 2), not stored vectors; rim only on the outer perimeter band; floating islands must
  be bridged or removed (separate named components are not exported); sheet ids stay stable
  and may still carry an old color/index; recoloring to the frame color is refused; JSON
  arguments need `--op-file` in PowerShell 5.1; no GUI (L4). Rim effect on physical alignment
  is unverified (L-G10).

### 2026-10-25 L2 refinement: hybrid vector edits, optional rim, export names (D-V3-028)

- State: **L2 APPROVED** by the user on 2026-10-25 (including these refinements); L-G10
  physical printing still PENDING; L3/L4 not started. All limitations below remain.
- Note for L3: exact sub-pixel geometry is not necessarily FDM-printable. Minimum printable
  openings and negative features (holes, slots, gaps) are not validated yet; only the 1.2 mm
  positive material width is checked.
- Changed: `core/model.py` (`Sheet.geometry`, `SheetStack.export_names`, schema 1.2.0 with
  WKB geometry + hash, raster-of-geometry check on load), `core/cad.py` (vector sheets
  extruded from exact geometry; files and assembly rows named by current order/color with
  `sheet_id`; reopened-STL footprint compared with the geometry and checked with the vector
  rules), `core/validation.py` (`vector_sheet_checks`, same 1.2 mm width), `core/editing.py`
  (precision raster/vector; vector add/subtract/bridge/fill_hole/remove_island/merge/split;
  exact changed and exact visible area per edit; rim `enabled` + partial update),
  `core/review.py` (shape edits default to vector, precision logged; fingerprint includes
  geometry hash and export names), `core/preview.py`, `core/commands.py`, tests (+7).
  Planner, raster validators and thresholds unchanged.
- Feasibility check before building: tracing each of the 17 accepted bear and van_gogh sheets
  to vector and rasterizing back is pixel-exact, and the vector checks agree with the raster
  validators on all of them (1 part, no orphan cores, perimeter complete). No rewrite needed.
- Tests: complete suite **119 PASS / 0 FAIL / 0 SKIP / 0 ERROR** in 269 s (112 + 7 new). 29
  warnings, all pyparsing deprecations inside matplotlib/cadquery. L0 reference regression
  (`stack-reference --from-accepted`, scratch output) PASS again: baseline, masks and accepted
  CAD identical (373 s).
- Precision through CAD (test): a 0.08 mm-radius hole, invisible to the 0.39 mm grid (0 px
  changed), is kept in the reopened STL (one more hole in the exact checks), mean STL boundary
  deviation < 1e-4 mm; the same hole in the front sheet is reported as
  `visible_subgrid_geometry` with its exact visible area, while the grid reconstruction stays 0 px.
  A 0.9 mm vector bridge fails the exact width check (orphan core); 3 mm passes.
- Backward compatibility: the bear project saved by the first L2 version (raster log without
  `precision`) loads and replays bit-identically; the L1/L0 tests are unchanged and pass.
- Manual PowerShell run on the accepted bear (private, `artifacts/l2_review_v1/bear_vector`):
  rim enabled 8 x 3.2 mm; 4.05 mm vector hole (exact 51.5248 mm2); rounded square ring
  (island, DRAFT_INVALID); 1.0 mm bridge (DRAFT_INVALID, < 1.2 mm) undone; 2.5 mm bridge (valid,
  exact 5.0 mm2); a visible move undone; pink recolor + accept_artwork; verify replay
  identical. Export 43 s: validation PASS, CAD revalidation PASS, 7/7 STEP single valid solids,
  7/7 STL watertight, stack mismatch 0 px, reconstruction from STL 0 of 1,048,576 px, rim height
  matches; vector sheets' STL vs geometry 1.4e-5 and 2.4e-5 mm2 (mean boundary deviation ~2e-8
  mm); the recolored sheet exports as `layer_02_f48fb1` (stable id `layer_02_fb98b8`).
- Remaining limitations: circles/ellipses are 256-segment polygons (chord error about 3e-4 mm at
  r = 4 mm), not true arcs in STEP; target comparison is on the grid, sub-grid differences are
  reported, not pass/fail; a sheet keeps its traced staircase outline where it was not
  edited (no automatic vectorization of artwork); a vector sheet must pass both raster and exact
  checks (conservative); islands must be bridged or removed (no separate components); rim only
  on the outer perimeter band; JSON in PowerShell 5.1 needs `--op-file`; no GUI.

## 2026-10-26 L3 hardened print kit

- State: **APPROVED** by the user on 2026-10-26 based on the reported results (L0, L1, L2
  also approved). **L-G10 physical print PENDING**: no kit claims physical verification.
  L4 not started. Open items carried forward: the L0 full-size reference regression was
  NOT re-verified (see below), the FDM-estimate limitations below, and the van_gogh_optB
  RISK findings (0.77 mm opening, 0.46 mm narrow section on sheets 06-09) are unresolved.
- New: `core/printkit.py` (`PrintProcess`, `assess_printability`, `build_print_kit`, checksums,
  side section, labelled exploded view, printability maps), CLI `stack-kit`,
  `fixtures/print_processes/generic_fdm_0.4mm.json` (labelled ASSUMPTION),
  `fixtures/l3_print_kit/sailboat_512.png` + `PROVENANCE.md` (original, project-owned),
  `scripts/run_l3_print_kit_evaluation.py`, `tests/test_l3_print_kit.py` (8 tests). Changed:
  `core/cad.py` (export rows add `expected_volume_mm3`, `step_volume_vs_expected_pct`; report only),
  `core/commands.py`, `core/__init__.py`, `.gitignore` (`artifacts/l3_print_kit_v1/`). Planner,
  editor, validators and thresholds unchanged; geometry is never altered by the kit.
- Status levels per kit: `cad` (proven by reopening), `fdm_estimate` (process-based estimate),
  `physical` (always NOT_VERIFIED). Invalid stacks are refused; UNSUPPORTED findings are reported
  and the CLI exits 1.
- Evaluation (`scripts/run_l3_print_kit_evaluation.py`, default generic process; outputs private
  and git-ignored in `artifacts/l3_print_kit_v1/`):

  | Case | Sheets | Edits / vector / rims | CAD | FDM estimate | Time |
  |---|---|---|---|---|---|
  | bow_face_s1 (project-owned) | 6 | 0 / 0 / 0 | VALID | NO_RISKS_FOUND | 17 s |
  | sailboat_s1 (project-owned) | 7 | 0 / 0 / 0 | VALID | UNSUPPORTED: 0.388 mm slit; RISK: 0.775 mm slit | 15 s |
  | sailboat_s2 (scale 2) | 7 | 0 / 0 / 0 | VALID | same two slits + RISK 0.54 mm sharp sail tip | 37 s |
  | sailboat_s1_edited | 7 | 4 / 1 / 1 | VALID | NO_RISKS_FOUND (thin slit filled, slit widened to 1.0 mm by the user's edits) | 28 s |
  | bear_vector (private, L2 project) | 7 | 6 / 2 / 1 | VALID | NO_RISKS_FOUND | 60 s |
  | van_gogh_optB (private, scale 4) | 10 | 0 / 0 / 0 | VALID | RISK x8: a 0.77 mm opening and a 0.46 mm narrow section, each on the 4 front sheets that share the cutout | 736 s |

  Every case: front view of reviewed stack and of reopened STL vs artwork 0 px; stack mismatch
  0 px; STEP and STL volumes within 0.0 % of expected; each STEP one valid solid, each STL one
  watertight component; vector sheets' STL vs exact geometry <= 2.7e-5 mm2; checksums verify.
- Reproducibility (test): rebuilding a kit gives an identical `print_kit.json`, byte-identical
  STL, and STEP files identical apart from the header timestamp and OpenCASCADE's per-process
  product counter; the kit's own `stack/` copy rebuilds the same solids.
- Tests: complete suite `python -m pytest` = **127 PASS / 0 FAIL / 0 SKIP / 0 ERROR** in 472 s
  (119 + 8 L3). 29 warnings, all pyparsing deprecations inside matplotlib/cadquery. Re-run
  at approval before the commit: 127 PASS / 0 FAIL / 0 SKIP in 696 s.
- L0 reference regression: a re-run after the report-field addition was stopped by host
  low-memory protection (NOT re-verified this round). Last PASS: 2026-10-25 after the L2
  refinement; since then `cad.py` only gained two report fields. The van_gogh kit above
  exported the same accepted masks with CAD_VALID and 0 px mismatch.
- Limitations: the FDM estimate is geometric, not a slicer simulation, and its thresholds are
  generic assumptions until a print calibrates them; the narrow-feature residue filter
  (area >= w^2/2) is a heuristic and may miss very short slits or flag sharp tips; a shared
  cutout is reported once per sheet; scale-4 printability takes several minutes; no 3MF; no
  print-plate packing (each sheet is its own job); filament mass assumes 1.24 g/cm3; all L2
  limitations remain.

## 2026-10-26 L4 application user experience

- State: **READY_FOR_USER_TEST**; L4 NOT APPROVED. L0-L3 approved. **L-G10 physical print
  PENDING**. Not committed. No later milestone started.
- Launch: `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m layerforge_l1 app` (opens
  http://127.0.0.1:8765/; `--no-browser`, `--port`, `--workspace`). Checked: the CLI started the
  server and answered `/` and `/api/info`.
- New: `src/layerforge_l1/app/` (`server.py`, `jobs.py`, `workspace.py`, `static/index.html`,
  `app.css`, `app.js`), `tests/test_l4_app.py`, `tests/e2e/drive_app.mjs`. Changed: `cli.py` (`app`
  subcommand), `pyproject.toml` (static files as package data), `.gitignore` (`workspace/`,
  `artifacts/l4_app_v1/`). No new dependency. Planner, editor, validators, CAD and printkit unchanged.
- Workflow in the UI: import PNG/JPEG → L1 proposal (candidates, flags, palette, previews,
  obstruction maps) → pick a candidate → editor (front-to-back layer list, sheet / front view /
  change maps, click and drag tools in mm, recolor, thickness, optional rim, move, merge, split,
  accept artwork, raw JSON edits, history with revert, undo, verify replay) → validation panel
  with failures and hints, islands with locate/remove, last-change classification → FDM estimate
  with editable process (presets; defaults labelled ASSUMPTION), findings with locate, maps →
  print kit (three status cards, comparisons, assembly table, filament, side section, labelled
  exploded view, guide, manifest, zip, checksum check). Out-of-date estimates and kits are marked.
- Tests (2026-10-26), each starting the real server on a temporary workspace:
  - `test_info_static_and_request_errors`, `test_api_workflow_import_review_edit_reload_export`:
    **2 PASS** in 203 s (sailboat fixture, scale 1; includes 409 while a job runs, path traversal
    refused, invalid edit reported, kit refused for an invalid draft, server restart reopens the
    project unchanged, replay identical, 0.6 mm slot reported as RISK 0.594 mm, custom process
    changes the verdict, kit CAD_VALID / RISKS_REPORTED / NOT_VERIFIED, zip contents, stale marks).
  - `test_browser_workflow`: **1 PASS** in 169 s; headless Edge driven through the DevTools
    protocol with real clicks, file upload and canvas drags; no browser console errors.
    Screenshots: `artifacts/l4_app_v1/screens/` (git-ignored); reviewed by eye.
  - After these runs, small edits were made (deviation wording, click-to-enlarge images, side
    section full width, atomic JSON writes in the app, screenshot scroll).
  - Complete suite with the L4 tests: a first run was stopped by host low-memory protection
    (no counts). Re-run after the user freed memory, on the final code: `python -m pytest` =
    **130 PASS / 0 FAIL / 0 SKIP** in 728 s (127 + 3 L4; the browser test ran, not skipped).
    Sampled peak working set of the pytest process 0.57 GB; lowest free system memory 6.5 GB.
  - L0 full-size reference regression: still NOT re-verified (last PASS 2026-10-25).
- Timings (sailboat, through the API): scale 1: proposal 48 s, start review 13 s, edit about
  7-13 s, estimate 1 s, kit 21 s. Scale 2 (default): proposal 139 s, start review 30 s, edit
  38 s, estimate 3 s, kit 56 s. During planning, the slowest state request took 4.9 s at scale 2
  (shared Python interpreter); the page shows progress and stays usable.
- Limitations: local single-user tool (127.0.0.1, no accounts); one job at a time, no
  cancellation; jobs are forgotten on restart (projects are not); each edit re-renders all
  previews, so edits are slow at scale 2/4; mask-file edit operands are CLI-only; no 3D viewer
  (all views are pipeline images); requests can wait seconds while planning runs; the browser
  test needs Node >= 22 and Edge/Chrome; FDM estimate and all L1-L3 limitations remain; physical
  status cannot be changed in the app.

## 2026-10-26 L4 portable Windows build (launcher + distributable)

- State: **READY_FOR_USER_TEST**; L4 NOT APPROVED. L-G10 physical print PENDING. Not committed.
- Artifact: `dist\LayerForge\LayerForge.exe` (one-folder PyInstaller 6.16.0 build, windowed,
  1770 files, 656 MB) and `dist\LayerForge-0.1.0-win64.zip` (214 MB, SHA-256
  `5af4c7c9de9f8c750b4759f82cba13478aa2e16019caa121b3b4005824861968`, rebuilt after the .jfif fix). Both git-ignored.
- Upload fix (user report): `.jfif`/`.jpe` (JPEG) were refused by extension. The app now accepts them and
  checks the decoded format is PNG or JPEG. `test_jpeg_extensions_accepted_by_content` + request-error test:
  2 PASS; the rebuilt exe accepted `turtle_on_leaf.jfif` (201) and stopped mid-proposal with exit 0.
- New: `src/layerforge_l1/app/launcher.py` (control window, browser open, port fallback, one
  instance per projects folder, startup error dialogs, log, clean stop), `packaging/LayerForge.spec`,
  `packaging/layerforge_launcher.py`, `packaging/README_FIRST.txt`, `scripts/build_windows.ps1`,
  `tests/test_l4_launcher.py`, `tests/test_windows_build.py`. Changed: `app/server.py` (exclusive
  port bind on Windows), `app/jobs.py` (`running()`), `__init__.py` (frozen paths: bundled
  resources from the bundle, cache in `%LOCALAPPDATA%\LayerForge\cache`), `pyproject.toml`
  (`build` extra: pyinstaller==6.16.0), `.gitignore` (`build/`, `dist/`), README. L0-L3 core
  and the UI are unchanged.
- Build: the PyInstaller command from `scripts/build_windows.ps1` was run directly (running the
  .ps1 with an execution-policy bypass was refused in this session); the zip was made with the
  same Python zip step the script now uses.
- Bugs found and fixed during packaging:
  - Existing L4 server: on Windows `SO_REUSEADDR` let a second server bind an occupied port
    silently (verified: two `ThreadingHTTPServer`s bound 8799). Now `SO_EXCLUSIVEADDRUSE`.
  - Launcher headless mode froze the server mid-proposal: a pending blocking read on the stdin
    pipe stalled SciPy's Fortran DLL load in the job thread (thread dump), then all threads.
    Reproduced unfrozen; fixed with a non-blocking `PeekNamedPipe` poll; the proposal then
    finished in 21 s with the slowest status request 0.3 s. The windowed exe has no stdin.
  - A second launch crashed (unhandled-exception dialog) renaming the log file the first
    instance holds open. Fixed; regression test added.
  - Stopping with a job still in native code crashed interpreter finalization (0xC0000409);
    the launcher now ends with `os._exit(0)` after closing the server, lock and log.
- Tests (development machine, NOT a clean Windows install; for the exe Python was removed from
  PATH, PYTHONPATH/PYTHONHOME unset, LOCALAPPDATA and the projects folder temporary):
  - `tests/test_l4_launcher.py`: **5 PASS** on the final code.
  - `tests/test_l4_app.py -k request_errors` (unfrozen server after the port change): 1 PASS.
  - `tests/test_windows_build.py` on the final build: **2 PASS**:
    `test_packaged_info_static_and_request_errors` and
    `test_packaged_api_workflow_import_review_edit_reload_export` (the unchanged L4 workflow
    against the exe: import, proposal, review, edits, exe restart reopens the project, estimate,
    print kit CAD_VALID, checksums, zip with STEP+STL per sheet; app folder unchanged).
    **NOT RUN on the final build:** `test_packaged_browser_workflow`,
    `test_packaged_startup_errors_ports_and_single_instance`, `test_packaged_stop_while_a_job_runs`:
    the run was stopped by host low-memory protection (3.4 of 15.8 GB free afterwards).
    On the first build, `startup_errors_ports_and_single_instance` and the static test PASSED;
    the API and browser tests FAILED there (the headless freeze above).
  - Manual checks of the windowed exe (first build): ready in about 0.6 s, serves, closes on
    WM_CLOSE with exit 0 and removes its instance record. The second-launch crash seen there is
    the bug fixed above; it was not re-checked on the final build.
- Limitations: no clean-machine test (no VM available; missing system DLLs such as the VC++
  runtime are untested); unsigned exe, so SmartScreen may warn; no installer, Start-menu entry
  or icon; the projects folder is not remembered between launches unless given as
  `--workspace` (e.g. in a shortcut); the quit-while-running confirmation dialog was not
  exercised; only the default browser is opened (non-Chromium browsers untested); the bundle
  contains VTK, casadi and nlopt because cadquery imports them.

## 2026-10-26 L4 Review screen UX redesign

- State: **READY_FOR_USER_TEST** (design review). L4 NOT APPROVED. Not committed. Only
  `app/static/app.js` (review-tab rendering, default view, collapsible-section state) and
  `app/static/app.css` changed; server, API, L0-L3 core and the other tabs are unchanged.
- Layout: top review bar (draft status, Undo, Verify save/reload, Accept front view, Printability ->,
  Build print kit ->); assembled front view as the default, large stage with a view switcher; edit
  tools grouped Add / Cut / Fix under the stage (choosing an edit tool shows the selected layer);
  compact numbered layer list (warnings only on failure); right inspector for the selected layer
  (thumbnail, facts, color/thickness, order; rim, merge and details collapsible); Validation, last
  change, more previews, advanced and history collapsible. Invalid drafts and islands force
  Validation open; a visible-change edit opens its change panel; open/closed choices persist across
  re-renders. All previous controls, warnings and data-testids remain.
- Evidence (reference: a copy of the user's `turtle_on_leaf` project in a scratch workspace, served
  by the source app; the user's Documents copy was not touched): before/after screenshots in
  `artifacts/l4_app_v1/review_ux/` (git-ignored; private artwork). Review page at 1440x900:
  2542 px tall before, 1480 px after; the artwork is in the first screen after the change.
- Tests on the final code: `tests/test_l4_app.py` + `tests/test_l4_launcher.py` **9 PASS / 0 FAIL /
  0 SKIP** (154 s), incl. the real-browser workflow (import, proposal, review, invalid edit, slot,
  accept, rim, undo, verify, reload, printability, kit, checksums; no console errors).
- Portable exe rebuilt with the new UI after the user closed the app; smoke test (Python off PATH): serves
  the new review code, API 200, clean stop exit 0. Zip SHA-256
  `80c339ffbb9e966a9af8d8706e780fad25aa1f19d049abe31befc4090bb2642d` (214 MB).
- Not done:
  packaged tests still pending from before; dark mode and widths below 760 px not visually checked.

## 2026-10-27 Hebrew GUI user guide

- `docs/user_guide/LayerForge_GUI_guide_he.pdf` (24 pages, A4, RTL Hebrew with isolated English UI
  terms) from `docs/user_guide/LayerForge_GUI_guide_he.html`, printed with headless Edge. Screenshots in
  `docs/user_guide/img/` are crops of the project-owned sailboat e2e run (new Review UI); no user artwork.
  Covers launch/close/files, status cards, all four tabs, every edit tool and inspector action,
  accept_artwork, validation checks, history/undo/verify, printer preset fields, Printability vs Print kit,
  statuses, troubleshooting, glossary. Facts checked against the code (preset values, severities,
  build-volume UNSUPPORTED, quantization INFO). Pages were rendered and reviewed for RTL/bidi.
- 2026-10-27 rim for all layers (user request): the inspector's rim section has an "Apply to all N layers"
  checkbox, checked by default; it sends the existing core edit `{"op": "rim", "sheet": "*"}` (one edit, one
  undo step). Unchecked = this layer only, as before. UI only (`app.js`); core unchanged. Browser workflow
  test extended (single layer -> undo -> all layers -> reload): `test_browser_workflow` + request-errors
  2 PASS (114 s). Portable exe rebuilt with it (smoke test: ready in 0.5 s, serves the new app.js, API
  200, clean stop exit 0); zip SHA-256 `141d9c9bf1cca199c878f35549e2c0930377478d7a34e17923ef9e8d3e22278a`.
- Noted limitation found while writing: manually entered printer values are not kept after a page
  reload; adding a persistent preset means placing a JSON file in `_internal\fixtures\print_processes`.
- 2026-10-28 optional Bambu Studio multi-plate project (user request; D-V3-033). State: READY_FOR_USER_TEST.
  New `core/bambu3mf.py`; `build_print_kit(..., bambu_printer=...)`, `stack-kit --bambu-printer PRESET`,
  and a checkbox + printer list in the existing Print kit tab. One plate per sheet (plate 1 = back),
  sheets translated only (back face on the bed, rim on top), placed clear of keep-out zones; one Generic
  PLA slot per distinct color; no AMS mapping; settings flattened from the installed Bambu Studio system
  presets (no Bambu data in the repo; no Bambu Studio -> no project, reported). STEP/STL unchanged
  (checked by hash/STEP body). Each export is reopened and, when Bambu Studio is installed, loaded and
  sliced by its CLI and compared plate by plate.
  Evidence: sailboat kit (7 sheets, 6 colors, rim) sliced by Bambu Studio 02.08.02.61 for P1S, X1 Carbon,
  P2S, H2S: 7 plates, positions/sizes within 0.01 mm, triangle counts equal, filament per plate = its color
  slot, 0 filament changes. GUI opened by hand on the P1S project: 7 named plates, 6 colored slots, P1S /
  0.20mm Standard / Generic PLA shown unmodified. A1 / A1 mini: a 225 mm sheet does not fit (reported).
  Tests: `tests/test_bambu3mf.py` 12 PASS; L3 8 PASS; L4 4 PASS (browser ticks the option, kit card shows
  "loaded and sliced by Bambu Studio"). Full suite before the exe rebuild: 152 PASS / 1 FAIL (packaged
  browser test on the old exe, no new checkbox); after rebuilding, `tests/test_windows_build.py` 5 PASS.
  Zip SHA-256 `5fd6720ed4c9b487ec08d1c8521de6d27635fe7c0e35930fd82bf0918a536887`. Physical: NOT VERIFIED.
- 2026-10-28 exact sheet colors in the Bambu project (user request). Project filaments are virtual
  slots carrying the reviewed HEX (`filament_colour` and, as Bambu 02.08 writes it, `filament_multi_colour`
  + `filament_colour_type`); identical colors share a slot; no AMS slot is read, synced or mapped. The
  Bambu Studio check now also re-saves the project and compares Bambu's own per-plate record
  (`Metadata/plate_<n>.json`: objects + filament color) with the sheet: sailboat P1S/X1C/P2S/H2S, each of
  7 plates = one sheet in its exact HEX. GUI (02.08.02.61): swatch pixels read from a screenshot equal
  #3C281E #AAD7F5 #FAC832 #784628 #F5F5EB #1E468C; plates rendered in those colors. CAD unchanged.
  Tests: bambu3mf 12 PASS, L3 + L4 12 PASS, windows build 5 PASS (exe rebuilt; zip SHA-256
  `7454e8a634da5942a6c27d8fe6a9b7be42fa2376a1c36cc6ae7494cffb16154c`).
- 2026-10-28 Print Kit UX (user request; D-V3-034). State: READY_FOR_USER_TEST. Kit tab only (`app.js`,
  `app.css`, `workspace.py`, `server.py`, `launcher.py`); L0-L3 untouched. Latest kit: headline state
  ("Print Kit Ready" / out of date / CAD problems / incomplete), one filled primary "Download Print Kit
  (.zip)", Assembly Guide (+ Bambu .3mf, Open Kit Folder in the desktop app) as secondary; warnings and the
  three status cards always visible; Files & Exports, Assembly & Layers, Validation & Printability,
  Technical details (print_kit.json, Verify checksums, process values, folder) collapsed by default and
  remembered per kit. Build form folds away while a kit matches the draft. Earlier kits listed
  collapsed, each with its own actions. Delete kit: inline confirmation; `POST .../kits/<id>/delete`
  deletes only `kits/<id>` (id pattern checked, links removed as links, never followed), refused while a
  job runs; `print_kit.json` last, so a partial failure keeps the kit listed and retryable; record entry
  dropped when the folder is gone. Open Kit Folder: `POST .../kits/<id>/open`, desktop sessions only.
  Screenshots `artifacts/l4_app_v1/screens/08*.png` (collapsed, expanded, 390 px phone, delete confirm,
  after delete, desktop with Open Kit Folder, dark mode). Full suite on the rebuilt exe: 154 PASS / 0 FAIL
  (699 s). Zip SHA-256 `93e09ab18e539a6312193e2a7aab0f95237fa3c77453e001a6f1ed79d7d39de7`.
- 2026-10-28 "+ New Print Kit" (user request): the kit creation form moved from the bottom section to a
  "+ New Print Kit" button at the top right of the Print Kit tab; it opens the same form (same fields and
  build action), stays folded when a kit exists and folds again after a build; shown open before the
  first kit. Bottom section removed. Only `app.js`/`app.css` changed. L4 tests 5 PASS (browser: button
  top right above the kit card, opens the form, second kit built); windows build 5 PASS; exe rebuilt,
  zip SHA-256 `e606b3976b48e57ada27aab199fce444c1e5b248358b5f2fd63e1e073edf271f`.
- 2026-10-28 couple illustration follow-up (user decisions 1-5; D-V3-035). State: IN PROGRESS, not yet
  READY_FOR_USER_TEST. Implemented: bounded local cell refinement after an infeasible closure
  (`reveal_planner.refine_cells`, `RevealPlanner.closure_with_refinement`, rounds 1.2 mm/0.3 mm2 then
  0.8 mm/0.1 mm2, neighbor rings only if needed; feasible images take the original path); wider
  refinements (+4 rings, whole artwork) as extra proposal candidates only when refinement was needed;
  intake detail report (removed by cleanup / color not in palette) with original | default | planned
  crops, overview map and preserve/remove/adjust decisions (removed pieces preserved by default, missing
  colors keep the nearest color until decided); app proposal panel + `detail_decisions` API; pixel-exact
  correction in `cad.mask_to_geometry` for one-pixel tails (only when the traced raster differs).
  Couple (scratch copy, 150 mm, scale 2, default choices): blocked 28 cells -> refined 30, feasible;
  candidates beam_area 16 sheets / 239 mm seams, +4 rings 12 / 142 mm, whole 13 / 96 mm, beam_colors
  15 / 238 mm, all validation PASS 0 px mismatch. First CAD export of the 12-sheet candidate failed
  (4 px, one-pixel tails) -> tracing correction -> re-export PASS (0 px, single solids, watertight).
  Tests: new 8 PASS; L0/L1/L2 62 PASS; L3 + L-1 22 PASS; L4 not re-run after the last changes.
  The final re-evaluation of both variants (defaults / all details preserved) was stopped by host
  low-memory protection before finishing; print kits and FDM findings of the final code are not yet
  measured. Next: re-run the evaluation one variant at a time when memory allows.
- 2026-10-28 couple illustration follow-up, completed (supersedes "IN PROGRESS" above). State:
  READY_FOR_USER_TEST. Re-run one variant at a time (scratch copy; the user's project untouched):
  * default choices (eye whites preserved; lips/gold nearest color): READY_FOR_REVIEW; selected +4 rings,
    12 sheets, 142 mm seams (13 split regions), validation PASS 0 px, CAD PASS (0 px, single solids,
    watertight); alternatives 16/239 mm, 15/238 mm, whole artwork 13/96 mm. Kit CAD_VALID; FDM estimate
    (generic 0.4 mm) 11 UNSUPPORTED in 5 places (2 eye whites, necklace chain, a hair-curl gap, finger
    gap) + 98 RISK in ~46 places; physical NOT_VERIFIED.
  * all details preserved (+#A62A27 lips, #DD7559 man's lips, #FAC855 gold): READY_FOR_REVIEW; selected
    whole-artwork refinement, 16 sheets, 94 mm seams; alternatives 21/238, 18/142, 20/237; CAD PASS;
    kit CAD_VALID; 29 UNSUPPORTED in 11 places (adds the man's mouth: 0.1-0.2 mm openings) + 119 RISK.
  Tests: new 8; L0/L1/L2 62; L3 + L-1 22; L4 + refinement + Bambu 25; windows build 5: all PASS.
  Exe rebuilt, zip SHA-256 `dc790ff52d04dd995be751278018277feb5ab80051062b897a58c01d24169042`.
- 2026-10-28 recovery workflow: physical resize first, pixel upscaling secondary (D-V3-036, D-V3-037).
  State: implemented and evaluated; final verification incomplete (see below). Not committed.
  Physical size panel (proposal tab): analyse findings against larger sheets within the printer's usable
  area -> smallest useful size -> preview (same pixels, 9 proportional checks, visible changes) ->
  approve size (+ changes) -> retry in the same project (previous attempt archived) -> FDM re-measured.
  Trial planning for search-limit failures. Upscale & Retry kept separate, only after a failed proposal,
  retry only when the preview shows a visible change; memory model from a measured run.
  Evaluation (scratch copies; the user's project untouched; generic 0.4 mm process):
  * couple, P1S (236 mm usable): 150 -> 198 mm suggested; sheets 12 -> 11; UNSUPPORTED 11 -> 7 (predicted
    0); RISK 98 -> 64; CAD PASS 0 px; proposal 364 s -> 172 s; peak 0.94 GB; 7 visible changes approved
    (lips kept red as a new color, intake details 12 -> 5). Remaining 7 slits need >= 307 mm.
  * sailboat 60 -> 72 mm: 6 sheets; RISK 4 -> 2 (predicted 0); no visible change; 38 s; 0.81 GB.
  * octopus (held-out): fails at 100/150/200 mm and in the trial at 236 mm (24-sheet search limit):
    resizing alone does not resolve it.
  Tests run individually during the work: test_physical_resize 7 PASS, test_upscale_retry 6 PASS, 4 new
  app tests PASS; before the resize work: L0-L2 + refinement 70, L3/L-1/planner 24, L4/launcher/Bambu 24
  PASS. The final full-suite run and the exe rebuild were stopped by the host's low-memory protection:
  dist\LayerForge was rebuilt (PyInstaller completed) but the zip was not re-created and the windows-build
  tests were not run. Not restarted (needs the user's go-ahead).
- 2026-10-29 recovery workflow, final verification (supersedes "verification incomplete" above).
  State: READY_FOR_USER_TEST. Not committed.
  Added: predicted vs measured clearly separated (labels "estimate, not a result" / "measured result",
  warning when more UNSUPPORTED are measured than predicted); retry refuses any plan whose artwork differs
  from the approved preview (nothing changes); remaining UNSUPPORTED listed with sheet, location and maps,
  with "Review this candidate" then Locate into the editor; Hebrew guide chapter 11 (+ statuses,
  troubleshooting, 3MF glossary fix), 9 screenshots of project-owned fixtures, PDF re-printed (30 pages).
  Memory model recalibrated to the measured 1.03 GB at scale 2 (755 B/px; scale 4 ~3.8 GB, 8 ~15 GB).
  Tests, run in sequential chunks with >= 6.8 GB free: new/refinement 21, L0/L1 40, L2 22,
  L3/L-1/planner/Bambu 36, other modules 41, L4 app (incl. browser) + launcher 15, windows build 5 on the
  rebuilt exe: 180 PASS / 0 FAIL / 0 SKIP. Rebuilt dist\LayerForge-0.1.0-win64.zip (2026-10-29 18:27,
  SHA-256 c85830c0b089c3d212f9a556a46d97a5d362d9dde46fd1971aed2d23d1a73746; static files byte-identical
  to src; resize/upscale modules bundled). Exe smoke: sailboat 60 -> 72 mm resize via the frozen app:
  RISK 4 -> 2 (predicted 0), artwork matches approved preview, attempt archived, clean exit.
- 2026-10-29 Delete Project (D-V3-038). State: READY_FOR_USER_TEST. Not committed.
  "Delete…" in the project list and "Delete project…" on the project screen (all stages), confirmation
  naming project + folder, permanent; deletes the whole project folder (source copy, proposal, attempts,
  review, draft, printability, resize/upscale, kits); links never followed; project.json last (partial
  failure stays listed, retry works); back to the import/list screen with a notice.
  Tests (sequential chunks, >= 5.4 GB free): 61 + 44 + 55 + L4/launcher 16 + windows build 5 (packaged
  exe incl. browser deletion from screen and list) = 181 PASS / 0 FAIL / 0 SKIP. Rebuilt
  dist\LayerForge-0.1.0-win64.zip 2026-10-29 19:09, SHA-256
  77975efd8c8d4c7ef1f3c19040e7822042318b6322ccd8c4db149dd296f8256d (static files byte-identical to src).
