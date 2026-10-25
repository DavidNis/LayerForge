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
