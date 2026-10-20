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

- L0: hardened reusable core, blocked until L-1 approved.
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
