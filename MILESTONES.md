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

Status: `APPROVED` by the user on 2026-10-25 based on software verification (started
2026-10-24 under a user-authorized gate exception). **L-G10 physical print test remains
PENDING**; this approval is not physical evidence.

- [x] Refactor proven L-1 geometry, serialization and validators into reproducible Python modules and CLI with tests (`src/layerforge_l1/core/`, `stack-*` commands).
- [x] Keep L-1 reference regression tests (`tests/test_reveal_planner.py` unchanged; baseline recorded).
- [x] Version manifests and preserve export/preview parity across refactors (`layerforge.sheet_stack` 1.0.0; STL-derived previews).
- [x] Full-size reference geometry matches the recorded baseline: `stack-reference --from-accepted` PASS on 2026-10-25 (validation, STEP/STL export + read-back, previews; planner not re-run, unchanged since `789b02a`).
- [x] Test suite independent of deleted experiment artifacts: 91 PASS / 0 FAIL / 0 SKIP.
- [x] Explicit user approval of L0 (2026-10-25; a full-size planner re-run was not required).

## L1: automatic layer-candidate proposals (critical product milestone)

Status: **APPROVED** by the user on 2026-10-25, based on the reported results and the
accepted bear output (L-G10 physical printing still PENDING). The documented bow-knot
outline loss remains a known limitation; no further correction is required for L1. The user accepted the bear result and decided
that `NEEDS_USER_DECISION` with an obstruction map is correct L1 behaviour for unresolved
art, with no automatic simplification in L1 (D-V3-026). Final verification: complete suite
97 PASS / 0 FAIL / 0 SKIP; bear stack and CAD re-verified read-only (see STATUS.md).

- [x] Accept a **new**, previously unseen flat-color PNG/JPEG with no hand-authored layer manifest (`stack-propose`).
- [x] Segment palette/contours and propose color masks plus candidate layer ordering and cutouts; flag ambiguous interpretation instead of inventing depth (`proposal.json` flags; no automatic art edits).
- [x] Evaluate on at least two independent illustrations, record manual corrections, and establish practical usability and reproducibility: 2 held-out cases run with frozen code and 0 manual corrections; reproducibility tested (bit-identical re-run). `bear_face`: user-accepted automatic conversion. `heldout_octopus_submarine`: correct `NEEDS_USER_DECISION` stop with obstruction map (closure infeasible, 1.14 % of the artwork), kept as a documented negative evaluation per D-V3-026.
- [x] Do not conflate color islands with physical sheet topology or claim automatic reconstruction merely because manual fixture works (`color_topology`, seam report, provenance `auto-proposed`).
- [x] Explicit user review and approval of L1 (2026-10-25).

## L2: reviewed layer editor

Status: **APPROVED** by the user on 2026-10-25, including the three refinements of
D-V3-028 (L-G10 physical printing still PENDING; started under user instruction after L1
approval). All documented limitations remain (STATUS.md). Design: DECISIONS D-V3-027 and the requested
refinement D-V3-028 (hybrid vector edits extruded exactly, optional per-sheet rim, export names
from current order/color); evidence: STATUS.md. Complete suite 119 PASS; L0 reference PASS.

- [x] Allow user to reorder/merge/split region masks and layers, edit cutout Boolean geometry, material color, sheet/rim thickness, and handle islands (`core/editing.py`, `stack-edit apply`: reorder/move, merge/split, add/subtract/fill_hole, recolor, thickness, perimeter rim, island detection + bridge/remove_island; invalid edits reported as `DRAFT_INVALID`, never repaired).
- [x] Save/load edits and check reproducible export from reviewed plan (manifest 1.1.0 `review` block + `review/base`; reload and log replay bit-identical; edited stacks export to valid STEP/STL with 0 px read-back mismatch, tested on an L1 proposal and the accepted bear; invalid drafts refused).
- [x] Explicit user review and approval of L2 (2026-10-25).

## L3: hardened print kit

Status: **APPROVED** by the user on 2026-10-26 based on the reported results (started under
user instruction after L2 approval). **L-G10 physical print still PENDING**: every kit reports
`physical: NOT_VERIFIED`. The L0 full-size reference regression re-run during L3 was stopped by
host low-memory protection and is NOT re-verified (last PASS 2026-10-25). The FDM-estimate
limitations and the van Gogh RISK findings remain open (STATUS.md). Design: DECISIONS D-V3-029;
evidence: STATUS.md. Complete suite 127 PASS.

- [x] Finalize layer exports, print placements, exact source/preview comparisons, and error reports across several distinct artworks (`stack-kit`: checksummed kit with STEP/STL, assembly metadata, print placement, color per file, comparisons artwork -> reviewed stack -> exported geometry -> reopened CAD, side section and labelled exploded views, guide; 6 cases from 4 distinct artworks incl. 2 edited stacks with rims and vector geometry: all CAD_VALID, 0 px front-view mismatch, 0 % volume error).
- [x] Optional 3MF packaging only when proven. No mandatory box/glue structure. (Not produced: it could not be proven in a slicer here; recorded in every kit. No frame, housing, glue or hardware is required.)
- [x] Validate FDM printability of edited geometry (estimate, not physical): openings, slots, gaps and thin material measured on the exported (exact for vector sheets) geometry against a configurable `PrintProcess`; INFO / RISK / UNSUPPORTED reported, geometry never altered; known 0.39 mm and 0.78 mm test slits classified UNSUPPORTED and RISK. The thresholds are generic assumptions until a print confirms them (L-G10).
- [x] Explicit user review and approval of L3 (2026-10-26).

## L4: application user experience

Status: `READY_FOR_USER_TEST` (2026-10-26; started under user instruction after L3 approval).
NOT APPROVED. **L-G10 physical print still PENDING**; the app shows the physical status as NOT
VERIFIED everywhere. Complete suite 130 PASS (incl. 3 L4 end-to-end tests). Open item: the L0
full-size reference regression remains NOT re-verified since 2026-10-25. Design: DECISIONS D-V3-030.

- [x] Decide UI and architecture based on validated L1-L3 pipeline; consider Python/FastAPI and React/Three.js. (Local web UI on the Python standard library + plain JavaScript; FastAPI/React/Three.js considered and not needed; the core is the only engine.)
- [x] End-to-end import, edit, visualize, save, and export (`python -m layerforge_l1 app`; tested through the real server over HTTP and by driving the UI in headless Edge: import → L1 proposal → review → invalid edit reported → visible slot → accept artwork → optional rim, undo, re-apply → verify save/reload → page reload → FDM estimate → print kit → checksums).
- [x] Portable Windows build: `dist\LayerForge\LayerForge.exe` (no Python needed), launcher with clean stop, port fallback and single instance (D-V3-031). Packaged tests only partly run on the final build: see STATUS.
- [ ] Clean-machine test of the portable build.
- [x] Region-based artwork recoloring with explicit re-planning (2026-10-30, D-V3-039); READY_FOR_USER_TEST, not approved.
- [ ] Explicit user review and approval of L4.

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

## Experimental L1 pre-layer image-preparation note — 2026-10-20

One deterministic 200 mm image-only pass combined centerline-based contour
normalization with spatially justified cleanup of seven one-pixel contour holes.
It preserved the nine-color palette and introduced no new tiny components.
Thirty-one below-threshold contour segments pass after widening; the
sun/mountain occlusion junction and the small left cloud outline remain
unresolved to avoid damaging colored-region topology. No layer planning,
CP-SAT, CAD, STL, STEP, GUI, or milestone advancement occurred. Status is
`READY_FOR_USER_TEST`; L1 remains NOT APPROVED.

## Experimental L1 post-layer width-repair note - 2026-10-20

The saved 12-sheet turtle proposal now passes the calibrated retained-width,
four-neighbor, and shared-perimeter checks after deterministic geometry
widening. The mask-derived composite differs from the locked target by 6,336
pixels and therefore requires visual approval. This is not CAD/print approval,
does not establish minimal layer count, and does not advance L1. Status remains
`READY_FOR_USER_TEST`; L1 is NOT APPROVED.

## Experimental L1 uniform-contour stack note - 2026-10-20

The local structural-neck dilation output is rejected. Its replacement uses
complete centerline restroking plus cumulative retained/cutout propagation.
The resulting 12-sheet mask stack passes the current 2D structural checks and
contains no blob repair, but its 6,264 visible changed pixels require user
approval. No CAD, STL, STEP, GUI, frame, or milestone advancement occurred.
Status is `READY_FOR_USER_TEST`; L1 remains NOT APPROVED.

## Experimental L1 physical-width verification note - 2026-10-20

A read-only audit of the saved rejected turtle masks confirmed correct mask
polarity and one-component sheet connectivity. Six sheets contain retained
connections that remain below the 1.2 mm rule on a 4x physically calibrated
diagnostic; the seventh native failure disappears at higher resolution and is a
raster false positive. No mask, validator, artwork, planner, CAD, STL, STEP, GUI,
or milestone state was changed. Status remains `READY_FOR_USER_TEST`; L1 is NOT
APPROVED.

## Experimental L1 final contour-correction note - 2026-10-20

The two remaining reviewed contour paths were locally re-stroked from the
accepted preprocessing candidate, without rerunning global preprocessing. Both
now exceed the 1.2 mm visible-contour threshold at a 200 mm print mapping, and
all changes are confined to the authorized ID 1 and ID 108 neighborhoods. No
layer planning, CP-SAT, CAD, STL, STEP, GUI, or milestone advancement occurred.
Status is `PREPROCESSING_READY_FOR_USER_APPROVAL`; L1 remains NOT APPROVED.

## Experimental L1 approved-turtle planning note - 2026-10-20

One bounded global CP-SAT run used the locked corrected turtle image without
preprocessing changes. Fixed depths 9 through 18 were tested and 35 complete
assignments reached the geometry oracle; none passed every retained-width check.
The preserved 12-sheet diagnostic stack is visually exact and 4-connected but
fails seven sheet-width checks, so it is not a valid deliverable. No CAD, STL,
STEP, GUI, frame, or milestone advancement occurred. Status is
`READY_FOR_USER_TEST`; L1 remains NOT APPROVED.
