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
