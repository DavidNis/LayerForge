# MILESTONES: LayerForge V3 (fresh repository)

**Rule:** prove the real printable sheet stack first. Do only one milestone; stop with evidence, and advance only after explicit user approval. The old object-based prototype is not in this repository and is not a valid V3 acceptance test.

## L-1: physical layer-stack feasibility (FIRST)

Status: `AWAITING_PHYSICAL_TEST` (software gates L-G01 through L-G09 PASS;
L-G10 user print/approval PENDING).

Goal: prove one original, controlled flat-color artwork can become **aligned, ordered, independently printable layer sheets** with accurate cutouts and image reproduction.

- [x] Inspect fresh repo and required V3 docs; set up Python 3.12 env and pinned dependencies. Document actual versions.
- [x] Make an original rights-cleared illustration with 4–8 planned sheet layers, overlapping colored regions, one genuine through-cutout, same hue repeated on separate depth levels, and a multiple-region layer. These counts are POC targets, not universal product constraints.
- [x] Declare source masks and exact manual-versus-automatic work; use a reviewed plan/annotation at this stage, not a fictitious one-click converter.
- [x] Build a versioned `LayerStack` document with a shared XY canvas, color per layer, add/subtract polygons, thickness, back-to-front ordering, and distinct assembly/print transforms.
- [x] Build true CAD sheet solids via polygon union minus cutout, with optional layer-local rim and explicitly reviewed disconnected-island handling. Frame/housing not required.
- [x] Generate valid STEP and STL per layer or explicitly named subcomponent; reopen and check validity, volume, holes, print bounds, tolerances, and intended sheet topology.
- [x] Create per-sheet previews, exploded layers, aligned front orthographic, physical side view, and source comparison from the **same exported solids**.
- [x] Produce artifact manifest, exact build commands, automated checks, assembly/temporary alignment instructions, and L-G01..L-G10 validation report.
- [x] Stop at `AWAITING_PHYSICAL_TEST` when software gates pass; user prints/aligns sample and judges visual depth, accuracy, and physical behavior. Never self-approve.
- [ ] Obtain L-G10 physical-stack evidence and explicit user approval; L-1 remains incomplete until then.

**Gate:** all software criteria `specs/LAYER_FEASIBILITY_GATE.md` PASS, physical stacked trial reviewed, explicit user approval. Failure => revise L-1, not build the app to hide weak layer output.

## L0: reusable geometry core

Status: `BLOCKED_BY_L-1`.

- [ ] Refactor proven L-1 geometry, serialization and validators into reproducible Python modules and CLI with tests.
- [ ] Keep L-1 reference regression tests.
- [ ] Version manifests and preserve export/preview parity across refactors.

## L1: automatic layer-candidate proposals (critical product milestone)

Status: `BLOCKED_BY_L-1`.

- [ ] Accept a **new**, previously unseen flat-color PNG/JPEG with no hand-authored layer manifest.
- [ ] Segment palette/contours and propose color masks plus candidate layer ordering and cutouts; flag ambiguous interpretation instead of inventing depth.
- [ ] Evaluate on at least two independent illustrations, record manual corrections, and establish practical usability and reproducibility.
- [ ] Do not conflate color islands with physical sheet topology or claim automatic reconstruction merely because manual fixture works.

## L2: reviewed layer editor

Status: `BLOCKED_BY_L-1`.

- [ ] Allow user to reorder/merge/split region masks and layers, edit cutout Boolean geometry, material color, sheet/rim thickness, and handle islands.
- [ ] Save/load edits and check reproducible export from reviewed plan.

## L3: hardened print kit

Status: `BLOCKED_BY_L-1`.

- [ ] Finalize layer exports, print placements, exact source/preview comparisons, and error reports across several distinct artworks.
- [ ] Optional 3MF packaging only when proven. No mandatory box/glue structure.

## L4: application user experience

Status: `BLOCKED_BY_L-1`.

- [ ] Decide UI and architecture based on validated L1-L3 pipeline; consider Python/FastAPI and React/Three.js.
- [ ] End-to-end import, edit, visualize, save, and export.

## Future optional features

- Separate frame/slot/stand designer.
- Multicolor physical layers, more advanced indexing/registration.
- AI proposals for complex flat illustrations, real photographs only beyond V1.

## Experimental L1 evidence note — 2026-10-19

By explicit user direction, a bounded global-planner diagnostic was run without
advancing or approving a milestone. The turtle candidate has eight retained-width
failures despite exact visual reconstruction. Twenty alternate fixed-12-depth
assignments were checked; none passed all physical validators within the single
bounded run. Status remains `READY_FOR_USER_TEST`; L1 is NOT APPROVED.

## Experimental L1 evidence note — 2026-10-20

The Pikachu 3MF thin-geometry checkpoint directly measured nine artwork meshes.
Five pass the current retained-width validator and four contain orphan cores at
the 1.2 mm rule. Thin terminal material and narrow negative openings also exist
and require separate classifications. No validator, planner, source image, CAD
export, or milestone status was changed. L1 remains NOT APPROVED.

## Experimental L1 width-repair evidence note — 2026-10-20

The corrected 200 mm turtle audit passes five sheets and fails seven. The new
hidden-only width-repair operator preserves the subtractive construction model,
but this particular cumulative stack has no unused occluded fill capacity. The
exact reconstruction therefore remains unresolved. A clearly separated visual
proposal passes the mask validators while changing 4,075 visible pixels and is
not approved. No planner, palette, source, CAD, or milestone was advanced.

## Experimental L1 visible-width review note — 2026-10-20

The user clarified that controlled visible contour widening is permitted. The
existing turtle proposal was reviewed as a potential normalized target, without
regeneration. It preserves overall identity and passes the structural-core
checks, but does not normalize every positive or negative visible contour to
1.2 mm and includes 404 non-dark structural boundary changes. Recommendation:
`REVISE`; L1 remains NOT APPROVED.

## Experimental L1 contour-normalization note — 2026-10-20

The approved implementation scope produced one candidate directly from the
original indexed turtle artwork. Complete deficient centerline segments are
widened on a 4x physical measurement grid, while proposals that would change
color-region topology are rejected. The sun arc is visually uniform and 30
segments were normalized, but four topology conflicts plus four thin
micro-junctions remain unresolved. Recommendation is `REVISE`; no planner,
physical layer, CP-SAT, CAD, STL, STEP, GUI, or milestone state changed. L1
remains NOT APPROVED.
