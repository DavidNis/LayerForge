# TASKS: fresh V3 work queue

## L-1 (current; stop after feasibility deliverables)

- [x] Verify all six `specs/` files exist in the workspace.
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
- [x] 3MF: done as the optional Bambu Studio project (2026-10-28, below), verified by a Bambu Studio round trip. Print-plate packing of several sheets per plate: still later.

## L4 application user experience (2026-10-26)

- [x] Local web app `python -m layerforge_l1 app` (`src/layerforge_l1/app/`): import, L1 proposal, layer editor (all L2 operations except mask-file operands; canvas tools for points, circles, rectangles, slots and bridges; optional rim; undo/revert; verify save/reload), validation and change maps, FDM estimate, L3 print kit with zip download and checksum check.
- [x] Background jobs with progress and actionable errors; separate geometry/CAD, FDM-estimate and physical (always NOT VERIFIED) status.
- [x] 3 L4 tests: request errors; API end-to-end workflow incl. server restart; real-browser UI workflow (headless Edge via DevTools, Node >= 22).
- [x] Complete suite on the final code: 130 PASS / 0 FAIL / 0 SKIP (first attempt stopped by host low-memory protection; re-run after memory was freed).
- [ ] Re-run the L0 reference regression when memory allows (still open from L3).
- [ ] Explicit user review and approval of L4. L-G10 still PENDING.
- [ ] Later (not L4): job cancellation; faster edits at scale 2/4 (each edit re-renders all previews); process isolation for planning.

## L4 portable Windows build (2026-10-26)

- [x] `LayerForge.exe` launcher (window, browser, port fallback, single instance, error dialogs, log, clean stop) and one-folder PyInstaller build + zip.
- [x] Fixed: exclusive port bind on Windows; headless stdin freeze; second-launch log crash; finalization crash on stop during a job.
- [ ] Re-run `tests/test_windows_build.py` on the final build when memory allows (browser, startup/ports, stop-while-job not yet run on it).
- [ ] Test on a clean Windows machine/VM without Python (not available here).
- [ ] User launches the exe by double-click and reviews; explicit L4 approval.
- [ ] Later: installer, icon, code signing, remembered projects folder.

## L4 Review screen UX (2026-10-26)

- [x] Review tab redesign (stage-first layout, grouped tools, contextual inspector, collapsible diagnostics, review bar with next steps); 9 L4 tests PASS incl. the browser workflow.
- [ ] User design review of the new Review screen.
- [x] Rebuilt the portable exe with the new UI (smoke-tested).

## Bambu Studio multi-plate project (2026-10-28)

- [x] `core/bambu3mf.py`: one plate per sheet, keep-out-aware placement, color slots, settings from the installed Bambu Studio presets, self-check and Bambu Studio CLI load+slice check.
- [x] Kit option (`bambu_printer`), `stack-kit --bambu-printer`, Print kit tab checkbox + printer list, guide section, checksums.
- [x] 12 tests (`tests/test_bambu3mf.py`); L3/L4/windows-build tests PASS; exe rebuilt.
- [ ] User opens a generated project in Bambu Studio, prints a sheet or the kit, and reports (L-G10).
- [ ] Later: several sheets per plate; dual-nozzle printers; filament type other than Generic PLA.

## L4 Print Kit section UX (2026-10-28)

- [x] Ready state with one primary download, guide/.3mf/Open Kit Folder secondary, visible warnings, four collapsible sections, earlier kits collapsed.
- [x] Delete kit with confirmation (folder + record; links not followed; partial-failure retry); Open Kit Folder in desktop sessions.
- [x] Tests: browser workflow (states, actions, 390 px, delete via UI) and API delete test (confinement, job conflict, locked file, junction, restart persistence, open folder).
- [ ] User design review of the Print Kit section.
- [ ] The Hebrew GUI guide PDF still shows the old Print Kit layout (update on request).

## Recovery: physical resize and pixel upscaling (2026-10-28)

- [x] `core/resize.py`: findings -> required factor, printer usable area (incl. Bambu keep-out zones), size table, smallest useful size, preview with proportional checks, trial planning for search-limit failures.
- [x] App: Physical size panel (analysis, preview, approvals, Resize & retry), attempts archive, re-measured FDM estimate after the retry; Printability tab links to it.
- [x] `core/upscale.py` kept as a separate, secondary option after a failed proposal; retry only when the preview shows a visible change; memory model from a measured run.
- [x] Tests: `tests/test_physical_resize.py` (7), `tests/test_upscale_retry.py` (6), 4 app tests (upscale retry, memory refusal, resize workflow, trial planning).
- [ ] User test on the couple (scratch copy evaluated; the user's project untouched).
- [ ] Measure scale-4 memory/time to replace the extrapolated upscale model; review the 2x change filter (52 changes on the couple).
- [ ] Predicted vs measured findings differ after re-planning (couple 0 vs 7): consider iterating prediction with the re-planned stack.
- [x] Predicted vs measured clearly separated; retry refuses an unapproved target; remaining UNSUPPORTED with locations, maps and Locate (2026-10-29).
- [x] Hebrew GUI guide: new chapter 11 (physical size, manual correction, trial planning, upscale, attempts) with screenshots of project-owned fixtures; PDF re-printed.
- [ ] The guide's older Print Kit screenshots predate the Print Kit UX change.

## Delete Project (2026-10-29)

- [x] API + workspace deletion (confined, links not followed, project.json last, partial-failure retry), project list and project screen actions with confirmation, notice, return to the list.
- [x] Tests: API (complete/failed/partially generated projects, held-open file, junction to outside data, traversal and non-project folders, linked project folder, restart, re-create same name) and browser (screen + list).
- [ ] The Hebrew guide does not mention Delete Project yet.

## Artwork recoloring (2026-10-30, D-V3-039)

- [x] `core/artwork.py`: 4-connected region selection (+ optional limit box), recolor, palette/region
  measurement, replayable log with undo/revert/verify; `intake.use_edited_target`; `propose(artwork=...)`
  and `ProposalConfig.plan=False` (intake only).
- [x] App: preview/sample/apply/undo/verify/replan endpoints; Artwork colors panel in Import & proposal
  (before planning, after a failed or ready proposal) and in Review & edit; kit export blocked while the
  artwork differs from the planned one; explicit re-plan archives the previous attempt and reports palette
  size and measured sheet count.
- [x] Tests `tests/test_artwork_recolor.py` (6 core, 4 API, 1 browser drive `tests/e2e/drive_artwork.mjs`).
- [x] Manual evaluation on a scratch copy of the couple project (necklace, skin tones, one trouser leg).
- [ ] User review in the app; explicit approval.
- [x] 2026-10-30 refinements: "Try Again" right after Apply; Physical Resize & Retry keeps region recolors
  (unplanned or planned) pixel for pixel with the edit history; Upscale and intake re-runs stay refused with
  the reason shown; deterministic memory handling in upscale tests (`tests/resource_guard.py`); Hebrew
  guide chapter 12 (HTML; PDF re-printed before the user said the PDF could be skipped); exe + zip rebuilt.
- [ ] Not done: carrying recolors across Upscale & Retry (pixel-grid re-mapping) and intake re-runs.
