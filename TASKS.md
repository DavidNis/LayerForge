# TASKS: fresh V3 work queue

## L-1 (current; stop after feasibility deliverables)

- [ ] Verify `AGENTS.md`, all six `specs/` files, and the clean-start prompt exist in the open workspace.
- [ ] Check Python 3.12, CAD dependencies, printer target/configuration; document tested commands.
- [ ] Draw/create an original legal source fixture and ground-truth/reference visible composite with traceable annotation provenance.
- [ ] Define a versioned physical `LayerStack` (not a semantic-object manifest); record one color per layer, common XY, order, thickness and any gaps.
- [ ] Implement Boolean planar add/subtract masks and CAD extrusions with an optional raised layer rim.
- [ ] Detect and explicitly handle disconnected islands; do not silently invent carriers or separate objects.
- [ ] Export and verify real per-layer/subcomponent STL + STEP.
- [ ] Generate per-layer, exploded, front assembled, and side CAD-derived previews and source comparison.
- [ ] Test actual occlusion, holes, repeat colors, transform independence, front-view fidelity, printability and export read-back.
- [ ] Write evidence and honest gate report L-G01..L-G10, plus exact print/stack instructions.
- [ ] Update STATUS/TASKS/MILESTONES/DECISIONS and stop `AWAITING_PHYSICAL_TEST` or `READY_FOR_USER_TEST`.

## Following milestones

- L0: hardened reusable core, blocked until L-1 approved.
- L1: **automatic** segmentation + layer candidate proposals for novel illustrations; must be evaluated independently.
- L2: editor for layer regions, ordering, cutouts, color and thickness.
- L3: production print-kit validations and export.
- L4: polished app.

## Strict non-goals for L-1

No complete UI/server, arbitrary photographic depth estimation, frame design, glue/peg graph, silently invented objects, color-per-object exports, or claiming a manually designed case demonstrates full automation.
