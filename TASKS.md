# TASKS: fresh V3 work queue

## L-1 (current; stop after feasibility deliverables)

- [x] Verify `AGENTS.md`, all six `specs/` files, and the clean-start prompt exist in the open workspace.
- [x] Check Python 3.12, CAD dependencies, printer target/configuration; document tested commands.
- [x] Draw/create an original legal source fixture and ground-truth/reference visible composite with traceable annotation provenance.
- [x] Define a versioned physical `LayerStack` (not a semantic-object manifest); record one color per layer, common XY, order, thickness and any gaps.
- [x] Implement Boolean planar add/subtract masks and CAD extrusions with an optional raised layer rim.
- [x] Detect and explicitly handle disconnected islands; do not silently invent carriers or separate objects.
- [x] Export and verify real per-layer/subcomponent STL + STEP.
- [x] Generate per-layer, exploded, front assembled, and side CAD-derived previews and source comparison.
- [x] Test actual occlusion, holes, repeat colors, transform independence, front-view fidelity, printability and export read-back.
- [x] Write evidence and honest gate report L-G01..L-G10, plus exact print/stack instructions.
- [x] Update STATUS/TASKS/MILESTONES/DECISIONS and stop `AWAITING_PHYSICAL_TEST`.
- [ ] User slices, prints, temporarily registers and reviews the representative stack; record L-G10 evidence and explicit decision.

## Following milestones

- L0: hardened reusable core — see the L0 section below.
- L1: **automatic** segmentation + layer candidate proposals for novel illustrations; must be evaluated independently.
- L2: editor for layer regions, ordering, cutouts, color and thickness.
- L3: production print-kit validations and export.
- L4: polished app.

## Strict non-goals for L-1

No complete UI/server, arbitrary photographic depth estimation, frame design, glue/peg graph, silently invented objects, color-per-object exports, or claiming a manually designed case demonstrates full automation.

## Experimental L1 global-planner follow-up (not approved)

- [x] Diagnose every failing sheet in the saved 12-layer turtle assignment with
  the unchanged connectivity and retained-width validators.
- [x] Produce eight mask-derived failure overlays and exact failing-prefix data.
- [x] Run one bounded global CP-SAT recovery attempt (12 layers, 90-second cap,
  20 geometry-oracle candidates) without changing artwork or constraints.
- [ ] Design the next approved checkpoint for integrating retained-width
  feasibility earlier than complete-assignment oracle feedback; do not proceed
  without user approval.

## Experimental retained-feature classification (not approved)

- [x] Extract all nine Pikachu artwork meshes directly from the supplied 3MF;
  exclude the external box.
- [x] Measure retained geometry and through-openings in millimeters at a
  documented 0.05 mm raster resolution.
- [x] Apply the unchanged connectivity and 1.2 mm retained-width validators to
  reference and turtle masks.
- [x] Produce reference/turtle role overlays and a proposed six-part validation
  model without changing production behavior.
- [ ] Await approval before implementing the diagnostic-only
  `feature_role_audit` checkpoint.

## Experimental automatic retained-width repair (not approved)

- [x] Re-evaluate the rejected turtle stack at 200 x 200 mm on a finer,
  physically calibrated validation raster.
- [x] Implement deterministic local path widening constrained to material that
  is hidden by an existing foreground sheet.
- [x] Prove hidden-only repair and unchanged reconstruction on a focused
  synthetic regression fixture.
- [x] Run one turtle repair attempt and preserve the unapproved visible-change
  proposal separately from the exact target.
- [ ] Obtain user direction: approve neither proposal automatically; an exact
  turtle solution requires a different layer assignment.

## Experimental visible contour-width review (not approved)

- [x] Inspect all 4,075 existing proposal changes without generating another
  candidate.
- [x] Separate positive contour widening, non-dark structural changes, and the
  dark-color substitution.
- [x] Measure raster-derived positive contour widths at 200 mm and classify all
  29 negative-opening warnings across nine unique geometries.
- [x] Produce full-size comparison, classified overlay, and requested feature
  close-ups.
- [ ] Revise the normalization approach only after user approval; current
  recommendation is `REVISE` because visible 1.2 mm enforcement is incomplete.

## Experimental contour-aware normalization (not approved)

- [x] Preserve the original indexed turtle target and isolate normalization
  from retained-material repair and physical-layer planning.
- [x] Trace and classify centerline graph segments; preserve filled dark
  regions and reject edits that merge dark components or alter colored-region
  topology.
- [x] Normalize one deterministic 200 mm candidate and produce requested full,
  changed-pixel, sun, face/shell, leaf, and mountain review views.
- [x] Add focused straight, curved, diagonal, and intersecting-contour tests
  (4 PASS).
- [ ] Resolve or explicitly approve the four topology-conflicting segments and
  four thin micro-junctions before adopting this candidate as a planning target.

## Experimental pre-layer image preparation (not approved)

- [x] Run one combined deterministic contour-normalization and conservative
  tiny-region-cleanup pass from the unchanged indexed turtle source.
- [x] Preserve all nine palette entries, filled facial details, object shapes,
  and flat-color encoding; introduce no gradients or generated artwork.
- [x] Produce full comparison, change overlay, requested closeups, complete
  thin-contour report, and one decision record per tiny component.
- [x] Run eight focused contour and cleanup tests (8 PASS).
- [ ] User reviews the candidate; two topology-conflicting thin contours remain
  unresolved and no layer planning may start without further direction.

## Final two turtle contour corrections (awaiting user approval)

- [x] Use the existing processed candidate without restarting preprocessing.
- [x] Correct only reviewed contour IDs 1 and 108 at the 200 x 200 mm mapping.
- [x] Verify both corrected minima exceed 1.2 mm after rasterization.
- [x] Preserve ID 62, the existing palette, prior cleanup, and all pixels
  outside the two reviewed neighborhoods.
- [x] Produce closeups, a changed-pixel overlay, measurements, and focused tests.
- [ ] Obtain user visual approval before any physical-layer planning.

## Approved turtle global physical-layer planning (not approved)

- [x] Lock and losslessly verify the final corrected turtle target.
- [x] Run one bounded global CP-SAT experiment across fixed K=9..18.
- [x] Apply existing exact-prefix no-goods and physical validation without
  changing the palette, source, 1.2 mm requirement, or planner architecture.
- [x] Reopen and validate the preserved best rejected masks after the reporting
  tail failed; do not rerun the solver.
- [x] Confirm exact reconstruction and complete shared perimeters for the best
  diagnostic candidate.
- [ ] Find a stack with zero retained-width/orphan-core failures; current best
  has seven failing sheets and must remain labeled REJECTED.
- [ ] Preserve geometry-oracle telemetry incrementally and evaluate a sound
  minimal structural-core conflict cut only after user approval.

## Turtle physical-mask width-failure verification

- [x] Verify retained/cutout polarity directly from every saved binary mask.
- [x] Reproduce all seven original orphan-core results with the unchanged
  validator and original run scale.
- [x] Trace representative orphan cores through the 3x3 surviving path to the
  exact neck removed by 5x5 erosion.
- [x] Produce per-sheet overlays and enlarged representative neck crops.
- [x] Separate six verified near-threshold failures from one coarse-raster false
  positive and document the 200 mm artwork/outer-sheet scale conflict.
- [ ] Implement no validator correction until the supersampled/vector-width
  recommendation is explicitly approved.

## Turtle post-layer retained-width repair

- [x] Reuse the saved 12-sheet assignment and preserve layer order/colors.
- [x] Validate at 200 mm artwork scale with the existing perimeter yielding a
  225 mm outer sheet.
- [x] Widen deficient retained paths, preferring hidden additions.
- [x] Widen four remaining one-pixel decorative retained strokes locally.
- [x] Verify shared perimeter, 4-neighbor connectivity, 1.2 mm retained width,
  zero orphan cores, mask-derived reconstruction, and cutout polarity.
- [ ] Obtain user visual approval for the 6,336 changed visible pixels before
  treating the repaired composite as the new artwork target.

## Uniform-contour turtle stack rebuild

- [x] Reject the blob-producing structural-neck dilation candidate.
- [x] Rebuild complete black and navy strokes from their centerlines at a
  consistent path-local width of at least 1.2 mm.
- [x] Propagate contour additions through real retained masks and foreground
  cutouts without visible connector bridges.
- [x] Reopen all masks and validate cutout polarity, shared perimeter,
  connectivity, retained width, orphan cores, and mask-derived reconstruction.
- [x] Add focused straight/curved contour and complete-stack regression tests.
- [ ] Obtain user approval for the uniform-contour visual target.

## Per-cell reveal-closure planner PoC (2026-10-21)

- [x] Run H1-H5 diagnostics on the saved rejected stack.
- [x] Implement per-cell reveal-closure planner reusing the existing validator.
- [x] Synthetic regression: indivisible stuck, per-cell exact 6-sheet PASS.
- [x] Turtle run: closure feasible, 18 sheets, exact, 17/18 PASS.
- [x] Rerun the turtle with the nub/orphan alignment fix: 18 sheets, all PASS at 4x.
- [ ] Reduce sheet count (seam smoothing, wider beam) once all sheets PASS.
- [ ] Extrude passing masks through the existing STEP/STL path and read back.

## L0 reusable geometry core (2026-10-24)

- [x] Record user approval of L-1 and the L-G10 gate exception (L-G10 PENDING).
- [x] Identify the active engine and capture the pre-refactor baseline.
- [x] Core package: model/manifest, planner wrapper, validation, CAD, previews.
- [x] `stack-*` CLI commands; documented Windows commands verified except `stack-reference`.
- [x] 25 new tests PASS; existing suite unchanged (51 pre-existing artifact failures).
- [x] Remove test dependencies on deleted artifacts: 91 PASS / 0 FAIL / 0 SKIP (`tests/REMOVED_TESTS.md`).
- [x] Focused full-size regression `stack-reference --from-accepted`: PASS (validation, baseline, STEP/STL read-back, previews).
- [x] User approved L0 on 2026-10-25 (full-size re-planning not required). L1 not started.
- [ ] L-G10 physical print-and-stack test: still PENDING.

## Repository cleanup before L1 (2026-10-25)

- [x] Audit, classify and remove obsolete tracked files (reversible via git); targeted `.gitignore`.
- [x] User decisions: delete untracked L0 run output (done); keep van_gogh STEP/CAD, user-supplied images/3MF, experimental scripts/modules.
- [ ] Evaluate experimental modules/scripts for L1 relevance; decide on old `docs/technical/` HTML + PDF.
- [ ] Update 5 stale line references in `docs/technical_optB/chapters` (caused by the L0 move of `mask_to_geometry`) and rebuild the PDF.
- [ ] Replace third-party / unrecorded-provenance fixtures with project-owned ones (van_gogh reference, fox/bear/l1_auto PNGs).

## L1 automatic proposals (2026-10-25)

- [x] Intake: palette, aspect-preserving fit, frame, snapping, measurements (`core/intake.py`).
- [x] Proposal: two planner candidates, validation, CAD + STL-derived previews, flags (`core/propose.py`, `stack-propose`).
- [x] 8 L1 tests on project-owned inputs; freeze, then 2 held-out evaluations with 0 manual corrections.
- [x] User decision (D-V3-026): NEEDS_USER_DECISION with an obstruction map is acceptable; no automatic simplification in L1. Bear accepted.
- [x] Final verification: complete suite 97 PASS; bear stack and CAD re-verified read-only; octopus kept as a negative evaluation.
- [x] User approved L1 on 2026-10-25. L-G10 physical printing still PENDING; L2 not started.
- [ ] Record a held-out illustration whose rights are cleared (project-owned or user-supplied) for a committable L1 regression.
- [ ] Known limitation, accepted for L1: unflagged detail loss (bear bow-knot outline); a thin-line preservation check may be considered later.

## L2 reviewed layer editor (2026-10-25)

- [x] Edit operations on a loaded stack (`core/editing.py`); review project with base copy, edit log, undo/revert, verify, preview and gated export (`core/review.py`, `stack-edit`).
- [x] Manifest 1.1.0 (`rim`, `review`); 1.0.0 still written for unedited stacks and still loadable.
- [x] 15 L2 tests incl. invalid edits and the L1 -> edit -> save/load -> replay -> STEP/STL integration test; complete suite 112 PASS; L0 reference regression PASS.
- [x] Manual PowerShell CLI run on the accepted bear proposal; quoting issue found and fixed.
- [x] Refinement (D-V3-028): hybrid vector edits kept exact through STEP/STL, exact checks on the reopened STL, explicit optional rim, export names from order/color with stable ids; 7 new tests; suite 119 PASS; L0 reference PASS; bear vector run exported and verified.
- [x] User approved L2 on 2026-10-25, including the D-V3-028 refinements. L-G10 still PENDING; L3 not started.
- [ ] L3: validate FDM printability of exact sub-pixel geometry; define and check minimum printable openings and negative features (holes, slots, gaps).
- [ ] Later (not L2): separate named export components for accepted islands; true arcs in STEP; rims around internal cutouts if wanted.

## L3 hardened print kit (2026-10-26)

- [x] `stack-kit`: checksummed kit (STEP/STL, assembly metadata, print placement, colors, comparisons, side section, labelled exploded view, printability maps, guide).
- [x] Process-based FDM estimate for openings, slots, gaps and thin material (exact geometry for vector sheets); INFO / RISK / UNSUPPORTED; geometry unchanged.
- [x] 8 L3 tests (project-owned bow face and sailboat); evaluation on 6 cases / 4 artworks incl. edited stacks with rims; complete suite 127 PASS.
- [ ] Re-run the L0 reference regression when memory allows (stopped by host low-memory protection on 2026-10-26).
- [x] User approved L3 on 2026-10-26. L-G10 still PENDING; L0 reference re-run still open (above); L4 not started.
- [ ] L-G10: print a representative kit, record the result; calibrate `min_opening_mm` / `min_positive_feature_mm` for the user's printer.
- [ ] Later (not L3): 3MF only if a slicer round-trip is verified; print-plate packing.
