# MILESTONES: LayerForge V3 (fresh repository)

**Rule:** prove the real printable sheet stack first. Do only one milestone; stop with evidence, and advance only after explicit user approval. The old object-based prototype is not in this repository and is not a valid V3 acceptance test.

## L-1: physical layer-stack feasibility (FIRST)

Status: `NOT_STARTED`.

Goal: prove one original, controlled flat-color artwork can become **aligned, ordered, independently printable layer sheets** with accurate cutouts and image reproduction.

- [ ] Inspect fresh repo and required V3 docs; set up Python 3.12 env and pinned dependencies. Document actual versions.
- [ ] Make an original rights-cleared illustration with 4–8 planned sheet layers, overlapping colored regions, one genuine through-cutout, same hue repeated on separate depth levels, and a multiple-region layer. These counts are POC targets, not universal product constraints.
- [ ] Declare source masks and exact manual-versus-automatic work; use a reviewed plan/annotation at this stage, not a fictitious one-click converter.
- [ ] Build a versioned `LayerStack` document with a shared XY canvas, color per layer, add/subtract polygons, thickness, back-to-front ordering, and distinct assembly/print transforms.
- [ ] Build true CAD sheet solids via polygon union minus cutout, with optional layer-local rim and explicitly reviewed disconnected-island handling. Frame/housing not required.
- [ ] Generate valid STEP and STL per layer or explicitly named subcomponent; reopen and check validity, volume, holes, print bounds, tolerances, and intended sheet topology.
- [ ] Create per-sheet previews, exploded layers, aligned front orthographic, physical side view, and source comparison from the **same exported solids**.
- [ ] Produce artifact manifest, exact build commands, automated checks, assembly/temporary alignment instructions, and L-G01..L-G10 validation report.
- [ ] Stop at `AWAITING_PHYSICAL_TEST` when software gates pass; user prints/aligns sample and judges visual depth, accuracy, and physical behavior. Never self-approve.

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
