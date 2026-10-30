# DECISIONS: dated architectural decisions

## D-V3-001 | 2026-10-16 | Layer-first, not object-first

**Status:** ACCEPTED USER REQUIREMENT.

The primary product is a physically ordered stack of printable 2.5D sheets. Semantic source objects are optional aids in segmentation, not CAD output units. An object might span layers; one layer can contain several visible regions or cutouts.

## D-V3-002 | 2026-10-16 | No required frame or glue graph

**Status:** ACCEPTED USER REQUIREMENT.

The user will decide on frame/slots/stand independently. Only create the layers first. Registration, dimensions, printed integrity and print-space / assembly-space coordinates remain mandatory; frame/stand/adhesion machinery must not be a gate for L-1.

## D-V3-003 | 2026-10-16 | One filament color per physical layer for V1

**Status:** CHOSEN MVP CONSTRAINT, not universal fact.

The two more detailed `.3mf` references show layer-wise single-filament printing as their workflow. Same color may be repeated at different layer depths. Future expansion may support multi-material in one layer, but not now.

## D-V3-004 | 2026-10-16 | Mask and layer planning require review

**Status:** ACCEPTED ENGINEERING SAFEGUARD.

Segmentation can be automated to create proposals, but original 2D pixels do not determine a unique 3D stack or hidden geometry. Never present choices as proven historical construction of reference art.

## D-V3-005 | 2026-10-16 | Fresh implementation of minimal CAD proof

**Status:** PROJECT SETUP CHOICE.

This is a new repository with no V2 source code. Build only the tested CAD functionality needed for L-1. Historical prototype output is not evidence of V3 success.

## D-V3-006 | 2026-10-16 | Real layer-solid parity

**Status:** ACCEPTED ENGINEERING SAFEGUARD.

Every view is produced from the same layer solids as STEP and STL. An AI mockup or artist's render cannot pass geometric verification.

## Decisions requiring user confirmation, NOT ASSUMED

- **Q1:** Preferred final layer physical dimensions, and whether each layer must share exactly the same outer outline or just the same coordinate frame. V3 proof can use documented sample settings pending user preference.
- **Q2:** How should disconnected islands be handled by default: connected temporary carrier/web, separate subparts, or automatic failure requiring review?
- **Q3:** Should a layer rim be enabled by default or only when needed? The sample 3MF contains edge-height variation but that does not establish universal intended function.
- **Q4:** Desired allowable palette / max layer count for user's typical art. A layer count is not equivalent to a unique color count.
- **Q5:** Whether user wants all layers to fit one bed and be printed one at a time or supports multi-bed subdivision. Do not enforce without asking.

The implementation should **not block the controlled L-1 experiment** on these choices. It should expose them as parameters, use transparent experiment-only defaults for L-1, and request clarification before fixing a final product policy.

## D-V3-007 | 2026-10-16 | Controlled L-1 fixture and experiment-only dimensions

**Status:** VERIFIED FEASIBILITY DECISION; not a final product default.

`orbit_bloom_v1` uses a 100 x 80 mm common canvas, six physical layers, 1.2 mm
nominal bases, 0.4 mm proposed display gaps, and a 220 x 220 mm software bed
envelope. The source is original project artwork under CC0-1.0. These values were
selected to exercise the gate and remain user-configurable policy questions.

## D-V3-008 | 2026-10-16 | Reviewed hidden web for the multi-region sheet

**Status:** VERIFIED FIXTURE-SPECIFIC DESIGN CHOICE.

The two intended visible regions on `layer_02_gold_regions` are one connected
physical sheet through a deliberately authored 4 mm web. The higher coral ring
occludes most of that web. Read-back confirms one watertight component. This is
not an automatic default for future disconnected regions; unresolved islands
must still fail or receive explicit review.

## D-V3-009 | 2026-10-16 | Exported STL read-back is the preview projection source

**Status:** VERIFIED IMPLEMENTATION DECISION FOR L-1.

CadQuery/OCP generates the BRep solids and true STEP/STL exports. Individual,
assembled-front, side and exploded views project the reopened exported STL
triangles, while STEP is independently reopened and validated. This provides
evidence that visible previews correspond to the exported CAD geometry rather
than to a separate decorative rendering pipeline.

## D-V3-010 | 2026-10-16 | Software pass does not complete L-1

**Status:** ACCEPTED GATE APPLICATION.

L-G01 through L-G09 pass for the controlled fixture. L-G10 remains PENDING for
real slicing, printing, temporary registration, shadow/appearance inspection and
explicit user approval. State is `AWAITING_PHYSICAL_TEST`; later milestones stay
blocked.

## Decision log protocol

Append immutable decisions with: ID, date, question, chosen option, evidence vs assumption, impact, status. Do not silently replace historical entries or turn open questions into requirements.

## D-V3-011 | 2026-10-19 | Exact-prefix geometry feedback scope

**Status:** VERIFIED EXPERIMENTAL EVIDENCE; not a product approval.

For the fixed-depth global planner, a cumulative physical mask is determined by
the exact set of source regions assigned at or before that depth. When that mask
fails the unchanged physical validator, a no-good cut excluding only that exact
region-set/depth pair is sound; it does not exclude different assignments or
orders. In the bounded turtle recovery run, 166 such cuts across 20 assignments
did not yield a valid stack. This is an `UNKNOWN` bounded result, not proof of
geometric infeasibility. The current backend's `fixed_layers=12` means exactly
12 non-empty layers, not an up-to-12 ceiling, and connected visible regions
cannot yet be split across depths.

## D-V3-012 | 2026-10-20 | Thin geometry requires role-separated evidence

**Status:** PROPOSED FOR USER REVIEW; production behavior unchanged.

Direct mesh analysis of the Pikachu reference verifies three different cases:
terminal retained features below 1.2 mm, narrow negative openings, and narrow
sole attachments that produce orphan structural cores. The current erosion
validator correctly detects the third case but does not separately report thin
terminal branches embedded in a larger connected component, and it intentionally
does not evaluate opening printability. The proposed next checkpoint is a
diagnostic core-and-connector role graph alongside the current validator. The
1.2 mm rule remains unchanged unless the user later approves a policy change.

## D-V3-013 | 2026-10-20 | Automatic width repair may use hidden additions only

**Status:** VERIFIED EXPERIMENTAL POLICY; turtle result unresolved.

A fixed-stack retained-width repair may add material only where an already
existing more-forward physical sheet occludes that addition. The implementation
locates actual narrow attachment paths within retained material and widens those
paths; it does not globally dilate a sheet, change a reported measurement, or
bridge an opening. The 200 mm turtle stack has zero such unused hidden pixels
because its masks are cumulative and maximally filled under their foreground
prefixes. Consequently, the exact-preserving attempt cannot repair its seven
remaining failures. The visual-change proposal is evidence of repair mechanics,
not an approved target or valid exact stack.

## D-V3-014 | 2026-10-20 | Explicit contour normalization may change pixels

**Status:** USER-REQUIRED; proposal still awaiting revision and approval.

Exact equality to the original indexed illustration is not mandatory when the
user explicitly authorizes widening visible contours to a physical minimum.
Such changes must be measured at final print scale, visually reviewed, and
preserved as a distinct proposed normalized target. Positive retained contours
and negative openings exposing darker backing material require separate width
measurements. The current turtle proposal is not accepted because it leaves
sub-threshold visible details and includes non-dark structural changes beyond
clean contour normalization.

## D-V3-015 | 2026-10-20 | Visual contours normalize before physical construction

**Status:** VERIFIED EXPERIMENTAL METHOD; candidate requires user revision/review.

Visible contour normalization is a visual-target operation, separate from both
retained-material repair and opening printability. The experimental operator
traces complete centerline graph segments, measures them at the specified print
scale, preserves filled dark regions, and applies only the existing contour
color. It rejects any widening that would merge a distinct dark component or
change another palette color's component topology. On the turtle target this
prevents the asymmetric structural-repair bulges but leaves four topology
conflicts and four thin micro-junctions unresolved; therefore the candidate is
not automatically adopted and L1 remains unapproved.

## D-V3-016 | 2026-10-20 | Tiny-region cleanup requires spatial evidence

**Status:** VERIFIED EXPERIMENTAL METHOD; candidate awaits user review.

A tiny indexed component is not noise merely because its area is small. The
pre-layer cleanup only reassigns a component to an already touching color when
area, extent, boundary-contact dominance, and feature role all support the
change. Dark contour/detail components are protected, and ambiguous components
remain unchanged. On the turtle checkpoint, seven one-pixel non-dark islands
embedded in black boundaries were merged into that existing black color; no
palette entry, semantic object, or intentional filled detail was removed.

## D-V3-017 | 2026-10-20 | Final contour fixes are bounded reviewed-path edits

**Status:** VERIFIED EXPERIMENTAL METHOD; candidate awaits user approval.

When a global preprocessing candidate has already passed visual review, a final
contour fix must match the recorded centerline path and confine added pixels to
that reviewed local neighborhood. For turtle IDs 1 and 108, the operator uses
the existing contour color, preserves the centerline, applies a rasterization
allowance on a 4x physical measurement grid, and verifies the narrowest result
after returning to 512 x 512. This checkpoint does not authorize changes to
other normalized paths, palette entries, tiny-region decisions, or layer plans.

## D-V3-018 | 2026-10-20 | Bounded fixed-K scans do not prove global infeasibility

**Status:** VERIFIED EXPERIMENTAL EVIDENCE; no valid turtle stack found.

The experimental backend represents exactly K non-empty layers and assigns each
connected visible source-color region wholly to one depth. An ascending bounded
scan may compare several fixed K values under one shared deadline, but timeout,
candidate-cap, or oracle-rejection results remain `UNKNOWN` outside the tested
assignments. The corrected turtle run proved only K=9 infeasible within this
representation. Its repeated broad orphan-core failures support future work on
sound minimal geometry conflict cuts; they do not prove that the artwork itself
is impossible or that connected-region splitting is required.

## D-V3-019 | 2026-10-20 | Retained-width validation needs physical supersampling

**Status:** VERIFIED DIAGNOSTIC FINDING; production validator unchanged.

Binary polarity, perimeter anchoring, cutout exclusion, and four-neighbor sheet
connectivity are correct on the saved turtle masks. Native odd-kernel rounding,
however, maps the 1.2 mm threshold to a coarse 5x5 footprint and falsely rejects
one two-core sheet. A 4x nearest-neighbor diagnostic of the unchanged erosion
rule clears that sheet while confirming near-threshold bottlenecks on six other
sheets. Future acceptance should retain 1.2 mm but evaluate it on a calibrated
supersampled or vector mask before geometry failures constrain the planner.

## D-V3-020 | 2026-10-20 | Post-layer width repair may change the visible target only with review

**Status:** USER-AUTHORIZED EXPERIMENT; visual result not approved.

Retained geometry below 1.2 mm is widened on the actual physical masks after
planning. The operator follows existing retained paths, uses already occluded
space first, and records unavoidable visible additions separately. At the
turtle's 512 px / 200 mm artwork mapping, the existing perimeter makes the
outer sheet 225 mm. A 4x physically calibrated audit is authoritative for this
checkpoint. Passing structural validation does not approve the 6,336 changed
visible pixels; the repaired composite remains subject to user review.

## D-V3-021 | 2026-10-20 | Visible contour width uses complete centerline reconstruction

**Status:** VERIFIED EXPERIMENTAL METHOD; candidate awaits visual approval.

Local dilation of validator bottleneck paths is forbidden for visible outline
normalization because it creates bulges and blobs. Visible black and navy
strokes are instead split into coherent centerline paths, assigned a stable
path-local intended width, and re-rasterized on a 4x grid at no less than
1.2 mm. Added color is propagated consistently through cumulative retained
masks and foreground cutouts. Structural hidden-support repair remains a
separate concern and may not alter the visible stroke geometry.

## D-V3-022 | 2026-10-21 | Experimental per-cell reveal-closure planner

**Status:** EXPERIMENTAL; one final turtle run interrupted (host memory); not approved.

`src/layerforge_l1/reveal_planner.py` assigns depth per *cell* (source regions
cut by a 10 px tile grid), so one connected color region may span several
sheets. Each sheet is planned front to back. Its material is the structurally
reachable part of the covered set (erosion by the existing validator kernel,
4-connected anchoring to the perimeter, dilation back). Extra checks align it
with the existing validator's 4-connected orphan-core rule, plus visible nubs
up to 0.5 mm. Thin hidden material is never carried. Feasibility is decided by
an order-independent monotone closure; a bounded beam searches sheet counts.
The existing validator is reused unchanged for acceptance (OBSERVED_IN_FILE:
the planner applies a stricter visible-support rule than the validator's
thin-decorative warnings).

## D-V3-023 | 2026-10-24 | L0 core wraps the accepted reveal-planner engine without changing it

**Status:** IMPLEMENTED; awaiting user review (L0 READY_FOR_USER_TEST).

- OBSERVED_IN_FILE: the latest validated stack (van_gogh option B, commit
  `789b02a`) was produced by `reveal_planner.RevealPlanner` through
  `scripts/run_turtle_reveal_planner_poc.py` and exported by
  `scripts/export_reveal_stack_cad.py`. It is the L0 regression baseline.
- DESIGN_CHOICE: the reusable core is the new subpackage
  `src/layerforge_l1/core/`. It calls the existing planner, validators and
  CadQuery extrusion unchanged; only `mask_to_geometry` was moved (verbatim).
  Experimental planners stay in place and are not called by the core.
- DESIGN_CHOICE: the persisted stack is mask-based (`layerforge.sheet_stack`
  1.0.0: JSON + binary PNG masks with decoded-bit SHA-256). The vector
  `LayerStack` of the orbit_bloom fixture is a different, older format and
  keeps its own `build` command; no conversion between them is claimed.
- DESIGN_CHOICE: CAD read-back and previews are derived from the reopened STL
  (union of upward-facing triangles, 4x supersampled majority raster), so the
  previews show the exported geometry. The earlier ad hoc re-validation
  re-rasterized traced polygons; that check is preserved as
  `geometry_px_diff_from_mask`.
- USER_REQUIRED: L0 started under an explicit gate exception; L-G10 physical
  validation remains PENDING.

## D-V3-024 | 2026-10-25 | Test suite no longer depends on deleted experiment artifacts

**Status:** IMPLEMENTED; awaiting user review with L0.

- USER_REQUIRED: the historical experiment reports deleted in `2c375e5` are not needed
  and must not be restored just to make tests pass.
- OBSERVED_IN_FILE: all 51 failures were missing-artifact errors; none was a genuine
  algorithm failure. A further 15 passing tests were vacuous. Four asserted "no CAD
  in <deleted folder>", and 11 `return`ed early when their folder was missing.
- DESIGN_CHOICE: there were 58 report-reading or vacuous tests of superseded experiments
  (43 failing + 4 + 11). They were removed and listed with justification in
  `tests/REMOVED_TESTS.md`; the code remains in git at `789b02a`. Eight valuable tests
  now regenerate their inputs: orbit_bloom CAD is built into a temp folder, and the
  l1_auto manifest is generated from the committed PNG. One synthetic test replaces the
  fox "diagonal-only orphan" report check.
- OBSERVED_IN_FILE: `pipeline._guide` hard-coded `DEFAULT_OUTPUT`, so `build()` into any
  other folder crashed. It now uses the actual output root. Report text only; no geometry change.
- DESIGN_CHOICE: `stack-reference --from-accepted` performs the focused L0 regression at
  full size without re-planning. It loads the committed accepted masks, then runs
  validation, the independent pre-refactor baseline comparison, STEP/STL export and
  read-back, the STL footprint comparison against the accepted CAD, and STL-derived
  previews. Justification: `reveal_planner.py` and every helper it imports are
  byte-identical to `789b02a`, and planner equivalence and determinism through the core
  are tested on the synthetic fixture. UNVERIFIED: a full-size re-planning run through
  the core has still not completed on this host.

## D-V3-025 | 2026-10-25 | L1 automatic proposal wraps the approved planner; no art edits

**Status:** IMPLEMENTED; criterion 3 resolved by the user decision in D-V3-026.

- DESIGN_CHOICE: L1 automates the manual van_gogh preprocessing with the same functions
  (`fullres_flatten.extract_palette`, `snap_to_palette`, aspect-preserving fit, frame padding)
  and calls the unchanged `plan_stack` once per existing beam score (`area`, `colors`). No new
  layering algorithm. Code: `core/intake.py`, `core/propose.py`, CLI `stack-propose`.
- DESIGN_CHOICE: palette minimum is a physical area (2 mm2), not `extract_palette`'s 0.1 %
  share. On a 200 mm artwork that share is 40 mm2, and it dropped the eye highlights of the
  development image. The k-means seed is fixed (0) for reproducibility.
- DESIGN_CHOICE: pixels count as unmatched only if they are far from every two-color blend.
  Anti-aliased edges are not new materials (IMAGE_INPUT_CONTRACT). Unmatched regions of at
  least 2 mm2 are flagged, which catches a dropped color.
- DESIGN_CHOICE: when no candidate is valid, the result is `NEEDS_USER_DECISION` with the
  planner's obstruction map. `PlanningError` now carries that map (report-only change in
  `core/planning.py`). No bridges, depth or color edits are invented.
- OBSERVED_IN_FILE: evaluation code was frozen (`artifacts/l1_eval_v1/freeze.json`) before the
  two held-out images were run. Two post-evaluation fixes followed, both disclosed: unused
  blend clusters are dropped from the reported palette, and other beam scores are skipped
  after an infeasible closure (closure does not read the beam score). A bear re-run after the
  fixes gave a bit-identical target and masks.
- UNVERIFIED_INFERENCE: the held-out images are of unrecorded external origin (likely
  AI-generated cartoons). Their outputs are kept private (git-ignored).

## D-V3-026 | 2026-10-25 | L1 scope: stop with evidence on unresolved art; no automatic simplification

**Status:** USER_REQUIRED (decided by the user, 2026-10-25).

- USER_REQUIRED: the bear proposal is accepted as the successful automatic-conversion example;
  no further visual corrections are required (the unflagged bow-knot outline loss stays a
  recorded limitation).
- USER_REQUIRED: for art the approved planner cannot support (e.g. the octopus held-out case),
  `NEEDS_USER_DECISION` with the obstruction map is correct L1 behaviour. Automatic image
  simplification is not required in L1. The octopus run is kept as a documented negative
  evaluation and is not re-planned.
- USER_REQUIRED: L1 approved by the user on 2026-10-25 on the reported results and the accepted
  bear output. L-G10 physical printing remains PENDING.

## D-V3-027 | 2026-10-25 | L2 editor: explicit mask edits, replayable log, same manifest

**Status:** IMPLEMENTED; awaiting user review.

- DESIGN_CHOICE: edits operate on a loaded `SheetStack` (L1 candidate or saved project) with
  the existing Boolean mask model: `add` = union, `subtract` = difference with a region
  (`geometry.shape_from_spec` primitive in sheet mm, a `bar` bridge, or a mask PNG),
  rasterized with `cad.rasterize_geometry`. Other ops: reorder/move, recolor, thickness, rim,
  fill_hole, remove_island, bridge, merge, split, accept_artwork. The planner is never re-run.
  Code: `core/editing.py` (operations), `core/review.py` (project), CLI `stack-edit`.
- DESIGN_CHOICE: no second format. `layerforge.sheet_stack` 1.1.0 adds an optional per-sheet
  `rim` and a top-level `review` block (base reference with the source provenance and
  manifest hash, ordered edit log with each change summary and validation result, current
  status). Stacks without either are still written as 1.0.0; 1.0.0 stays loadable.
- DESIGN_CHOICE: saved masks are authoritative; `review/base/` holds the unedited stack and
  mask operands are copied into `review/operands/`, so the log replays deterministically.
  Undo/revert replay a prefix of the log; `verify` checks that replay reproduces the saved
  geometry bit for bit.
- DESIGN_CHOICE: visible masks are re-derived after every edit with the rule the planner
  output satisfies (frontmost material in the artwork, frame excluded); verified identical
  on the bow-face, bear and van_gogh accepted stacks.
- DESIGN_CHOICE: each edit is classified as `visible_artwork`, `artwork_reference`,
  `hidden_geometry`, `physical_parameters` or `no_change` by comparing the front view before
  and after. Validators and thresholds are unchanged. Invalid geometric edits are applied,
  saved as `DRAFT_INVALID` and reported, never repaired. An intended visible change only
  validates after an explicit `accept_artwork` edit, which records the new front view as
  the reviewed target; the source artwork stays in the base and the deviation is reported.
- DESIGN_CHOICE: rim = raised band on the shared outer perimeter (width within the band,
  total height above the sheet), fused like `geometry.build_layer_solid`. The next sheet
  rests on it, so assembly z uses the rim height. Rims around internal cutouts are not offered.
- DESIGN_CHOICE: invalid drafts are refused by `stack-edit export` and by `stack-export`
  (for stacks with a review block); plain L0 stacks export as before.
- UNVERIFIED_INFERENCE: whether a perimeter rim aids alignment or spacing physically is
  untested (L-G10 pending).

## D-V3-028 | 2026-10-25 | L2 refinement: hybrid vector edits, explicit optional rim, export names

**Status:** IMPLEMENTED; awaiting user review. Requested by the user before L3.

- DESIGN_CHOICE: hybrid precision without a new geometry engine. The planner and raster
  sheets are unchanged. A sheet gets an exact planar `geometry` (shapely, sheet mm) on its
  first vector edit: its mask traced exactly as CAD already traces it (`mask_to_geometry`,
  0.03 mm; verified raster-lossless on all 17 accepted bear and van_gogh sheets), then the
  Boolean operation with the exact shape. Later geometric edits of that sheet (add, subtract,
  bridge, fill_hole, remove_island, merge, split) work on the geometry; mask operands enter
  as exact pixel-edge polygons. `retained` becomes the grid raster of the geometry and is
  used only for the raster validators, front view and target comparison.
- DESIGN_CHOICE: CAD extrudes the exact geometry of vector sheets (no re-rasterizing, no
  simplification). `revalidate_cad` additionally compares each vector sheet's reopened STL
  footprint with its geometry (mean boundary deviation <= 0.001 mm) and runs the vector
  checks on that footprint.
- DESIGN_CHOICE: `validation.vector_sheet_checks` evaluates the existing sheet rules on exact
  geometry with the existing 1.2 mm width (0.8 nominal + 0.2 + 0.2 safety from
  `DEFAULT_OPTIMIZATION_CONFIG`): one connected part, full perimeter band, nothing outside
  the sheet, every 0.6 mm-eroded core connected to the perimeter. A vector sheet must pass
  both these checks and the unchanged raster checks of its grid raster. No threshold changed.
- DESIGN_CHOICE: edits record `"precision"`. New shape-based edits (add/subtract with a
  shape, bridge, split by shape) default to `vector` in a review project and the value is
  written to the log; logged edits without the key replay as raster, so L1 stacks and
  earlier L2 projects load and replay unchanged (verified on the earlier bear project).
- DESIGN_CHOICE: front-view comparison stays on the grid (the target's resolution);
  changes below the grid are reported explicitly per edit as exact changed area and exact
  visible area (`visible_subgrid_geometry` when only sub-grid visible area changed).
- DESIGN_CHOICE: rim stays optional and is never added automatically; `rim` edits take
  `enabled` and either dimension per sheet (`remove: true` still replays).
- DESIGN_CHOICE: exported STEP/STL/preview names and assembly rows use
  `layer_<order>_<color>` from the current order and color (`SheetStack.export_names`);
  `Sheet.id` stays the stable identity used by the log, undo and replay, and is recorded
  next to the name. For unedited planner stacks names equal ids, so L0/L1 outputs keep
  their file names.
- USER_REQUIRED: L2 approved by the user on 2026-10-25 including these refinements; L-G10
  physical printing remains PENDING.
- OPEN_QUESTION (for L3): exact sub-pixel geometry is not necessarily FDM-printable. Minimum
  printable openings and negative features are not validated; only the 1.2 mm positive width is.
- Schema `layerforge.sheet_stack` 1.2.0 adds per-sheet `geometry` (WKB file + hash; the
  mask is checked to be its raster on load) and `export_name`; 1.0.0/1.1.0 still load.

## D-V3-029 | 2026-10-26 | L3 print kit: three separate status levels, process-based FDM estimate

**Status:** IMPLEMENTED; APPROVED by the user on 2026-10-26 (L-G10 physical print PENDING;
FDM thresholds remain generic assumptions until a print calibrates them).

- DESIGN_CHOICE: `core/printkit.py` + CLI `stack-kit` reuse validate -> export -> revalidate ->
  previews unchanged and write one self-contained kit: `cad/` STEP+STL (+ manifests), `stack/`
  (exact stack the kit was built from), `previews/` (from reopened STL, plus true-scale side
  section from STL plane sections, labelled exploded view, per-sheet printability maps),
  `print_kit.json`, `PRINT_AND_STACK_GUIDE.md`, `checksums.sha256`. Geometry is never altered.
- DESIGN_CHOICE: three status levels, never merged: `cad` (CAD_VALID only if validators PASS,
  every STEP is one valid solid, every STL one watertight component, STEP and STL volumes
  within 0.01 % of area x height, reopened geometry equals the reviewed masks / exact
  geometry, rim heights match, front view from STL equals the artwork); `fdm_estimate`
  (NO_RISKS_FOUND / RISKS_REPORTED / UNSUPPORTED_FEATURES); `physical` (always NOT_VERIFIED;
  L-G10 needs a real print). An invalid stack is refused; an UNSUPPORTED finding still writes
  the kit but the CLI exits 1.
- DESIGN_CHOICE: FDM estimate is driven by a `PrintProcess` (nozzle, line width, layer and
  first-layer height, min opening, min positive feature, build volume, filament density) read
  from JSON; defaults are labelled ASSUMPTION (generic 0.4 mm nozzle: opening < 0.4 mm
  UNSUPPORTED, < 0.8 mm RISK; material < 0.42 mm UNSUPPORTED, < 0.84 mm RISK; 256 mm bed).
  Measured on the exported planar geometry (exact for vector sheets): every cutout's
  inscribed diameter; narrow slots/necks by morphological opening (residues under w^2/2 are
  treated as corner/staircase artefacts: a heuristic, stated in the code); thin material the
  same way; build volume; layer quantization of sheet and rim heights. A finding hidden
  behind the sheets in front is downgraded to INFO with that reason. Prismatic sheets
  printed flat have no overhangs.
- DESIGN_CHOICE: print placement is "as exported": back face on the bed, no rotation; colors
  are carried by file name and manifest (STL has no color); filament mass is an estimate.
- DESIGN_CHOICE: 3MF is not produced. The milestone allows it "only when proven"; plate,
  color and object semantics could not be verified in a slicer here.
- OBSERVED_IN_FILE: STEP files differ between runs only in the header timestamp and
  OpenCASCADE's per-process product counter; STL bytes and `print_kit.json` are identical.

## D-V3-030 | 2026-10-26 | L4 application: local web UI on the standard library, core as the only engine

**Status:** IMPLEMENTED; awaiting user review (L4 READY_FOR_USER_TEST).

- DESIGN_CHOICE: `python -m layerforge_l1 app` serves a single-page UI (`src/layerforge_l1/app/static/`:
  plain HTML/CSS/JavaScript, no build step) and a small JSON API from `http.server.ThreadingHTTPServer`
  on 127.0.0.1. FastAPI/React/Three.js (considered in MILESTONES) were not adopted: the workflow needs
  file upload, job progress, images and forms, which the standard library covers without new pinned
  dependencies or a Node toolchain; every view is a real pipeline image (no 3D mock-up).
- DESIGN_CHOICE: `app/workspace.py` only calls the approved core: `propose` (L1), `review.start_review /
  apply_to_project / undo / revert / verify_project / inspect_project / preview_project` (L2),
  `validate_stack`, `assess_printability` + `printability_maps` and `build_print_kit` (L3). No planning,
  editing, validation or CAD logic is duplicated. The L2 review project is the save format; the app adds
  `project.json` (settings, chosen candidate, kit list) and cached views (`draft/state.json`).
- DESIGN_CHOICE: long operations run on one background worker thread (`app/jobs.py`), one at a time
  (memory), one per project (409 otherwise); progress events are the core's own `progress(...)` stages;
  the page polls the job. Core errors (`StackError` and subclasses) are shown with their message;
  anything else is reported as an unexpected error with the traceback on the server console.
- DESIGN_CHOICE: after a passing edit the app reuses the validation `apply_to_project` already ran
  instead of validating twice; per-check details are recomputed only for invalid drafts (marked
  `details_recomputed: false` otherwise). Undo/revert/start reuse the validation the core returns.
- DESIGN_CHOICE: three status levels in the UI, never merged: geometry/CAD (validators; CAD only from a
  kit that matches the current draft), FDM estimate (labelled estimate; from the current kit or draft
  estimate; out-of-date results are marked), physical (always NOT VERIFIED; no control can change it).
- DESIGN_CHOICE: mask-file edit operands stay CLI-only (the UI never sends file paths); every other
  L2 operation is available, plus a raw JSON edit field for the documented edit format.

## D-V3-031 | 2026-10-26 | L4 portable Windows build: PyInstaller one-folder app with a launcher window

**Status:** IMPLEMENTED; awaiting user review (L4 READY_FOR_USER_TEST).

- USER_REQUIRED: double-click EXE, no terminal, no Python installation, project files outside
  application folders, portable build first (no installer).
- DESIGN_CHOICE: PyInstaller one-folder bundle, not one-file: the CAD stack (OCP, VTK) is about
  650 MB and a one-file exe would unpack it into a temporary folder on every start. The folder
  build is serving about 0.6 s after start.
- DESIGN_CHOICE: windowed exe with a small tkinter control window (status, address, projects
  folder, Open / Projects folder / Quit). The window is the visible "app is running" signal and
  the clean way to stop; closing a browser tab does not stop the server. tkinter is in the
  standard library (no new runtime dependency); `MPLBACKEND=Agg` keeps matplotlib off it.
- DESIGN_CHOICE: projects default to `Documents\LayerForge` (known-folder lookup, follows
  redirection); log, cache and instance records in `%LOCALAPPDATA%\LayerForge`. A projects
  folder inside the application folder is refused. The exe writes nothing into its folder
  (snapshot check in `tests/test_windows_build.py`).
- DESIGN_CHOICE: one instance per projects folder (file lock in `%LOCALAPPDATA%`); a second
  launch opens the browser on the running instance. Port: preferred 8765, then the next 19,
  then any free port; Windows binds are exclusive (`SO_EXCLUSIVEADDRUSE`), fixing silent port
  sharing in the existing server.
- DESIGN_CHOICE: `--headless` (no window; stops when stdin is closed, detected by polling, never
  a blocking read) exists so the packaged exe can be driven by the existing L4 tests.
- DESIGN_CHOICE: `ortools` excluded from the bundle (CLI-only CP-SAT experiment, never reached
  from the app); VTK/casadi/nlopt stay because cadquery imports them.
- OPEN_QUESTION: behaviour on a clean Windows install (system DLLs, SmartScreen for the
  unsigned exe) is unverified.

## D-V3-032 | 2026-10-26 | L4 Review screen: artwork-first layout with contextual editing

**Status:** IMPLEMENTED; awaiting user design review.

- USER_REQUIRED: assembled artwork as the main focus, compact selectable layer list, contextual
  editing controls, collapsible technical details, easy next actions, all features and warnings kept.
- DESIGN_CHOICE: the canvas defaults to the assembled front view; edits still target the selected
  layer, and picking an edit tool switches the canvas to that layer so the user sees what changes.
- DESIGN_CHOICE: "Save" is shown as "saved automatically" (every edit is already persisted); the
  explicit check is Verify save/reload. "Build print kit ->" opens the kit tab, where the build runs.
- DESIGN_CHOICE: problems are never hidden: an invalid draft or islands force the Validation section
  open, a visible-change edit opens its change panel, and the draft banner stays above the stage.

## D-V3-033 | 2026-10-28 | Optional Bambu Studio multi-plate project (.3mf) in the print kit

**Status:** IMPLEMENTED; checked with Bambu Studio 02.08.02.61; awaiting user test (READY_FOR_USER_TEST).

- USER_REQUIRED: one Bambu Studio project, one plate per sheet in assembly order, flat with the
  correct face down, exact geometry, colors/filaments, printable-area check, no AMS assumption,
  STEP/STL unchanged, option in the existing kit UI, real Bambu Studio validation.
- OBSERVED_IN_FILE (projects saved by Bambu Studio 02.05-02.08): `3D/3dmodel.model` with
  production-extension components -> `3D/Objects/object_<n>.model`; `Metadata/model_settings.config`
  holds objects (name, extruder), `<plate>` entries (plater_id, plater_name, model_instance) and
  `<assemble>`; `Metadata/project_settings.config` holds the full flattened presets (563-581 keys).
- OBSERVED (Bambu Studio 02.08.02.61 CLI): a project with partial settings, or none, crashes the
  loader (0xC0000005), also with `--uptodate`; `--load-settings` with system profile files does not
  resolve `inherits` (it produced a 200 x 200 mm bed and generic start G-code); without an
  `Application` value starting `BambuStudio-` the plates are ignored ("plate empty").
- DESIGN_CHOICE: settings are flattened at export time from the *installed* Bambu Studio system
  presets (`BBL.json` index, `inherits` + `include`; the newer of the user-profile copy and the install
  folder), printer + matching-layer-height system process + one "Generic PLA" slot per distinct sheet
  color, unmodified. No Bambu profile data is stored in the repository; without Bambu Studio no project
  is written (reported). The `Application` marker is a format requirement; the file description and
  the kit report state that LayerForge wrote it.
- DESIGN_CHOICE: plate layout reproduces Bambu Studio's grid (columns = rounded square root, plates
  one bed + 20 % apart, rows towards -y; confirmed by the CLI slice report). Each sheet is translated
  only (no rotation/scale; back face z = 0 on the bed, rim on top) to the free spot nearest Bambu's
  preferred position, outside bed-exclude / wrap-detection zones with 1 mm clearance. If any sheet does
  not fit, no project is written and every sheet is listed.
- DESIGN_CHOICE: no AMS: only `filament_map` = extruder 1 is written, no AMS slot mapping; each plate
  uses exactly one filament (0 filament changes, verified by slicing).
- Verification: every export reopens the package (same triangles/volume as the STL, plate count,
  placement, filament colors); when `bambu-studio` is installed it loads and slices all plates and the
  report is compared with the plan (objects, filament ids, sizes and positions to 0.01 mm, triangle
  count, 0 filament changes) -> `BAMBU_STUDIO_CLI_VERIFIED`; otherwise
  `EXPERIMENTAL_NOT_OPENED_IN_BAMBU_STUDIO`. The GUI was opened once by hand-run test on the sailboat
  P1S project: 7 named plates, 6 colored Generic PLA slots, presets shown as unmodified system presets.
- USER_REQUIRED (2026-10-28): exact reviewed sheet colors as project-level virtual filaments, shared by
  identical colors, independent of the AMS; AMS mapping only at print time. Written as `filament_colour`
  = `filament_multi_colour` (OBSERVED_IN_FILE: Bambu 02.08 stores both) and verified per plate from the
  `Metadata/plate_<n>.json` Bambu Studio writes after slicing and re-saving.
- UNVERIFIED_INFERENCE (Bambu): GUI behavior on other printers and Bambu Studio versions; sending to a printer;
  physical prints (L-G10 still PENDING). Dual-nozzle printers (H2D, X2D, H2C) are not offered.

## D-V3-034 | 2026-10-28 | L4 Print Kit section: ready state, collapsible details, kit deletion

**Status:** IMPLEMENTED; awaiting user review (READY_FOR_USER_TEST).

- USER_REQUIRED: compact collapsible layout (Files & Exports, Assembly & Layers, Validation &
  Printability, Technical details), a prominent ready state with one primary download, guide as
  secondary, print_kit.json and checksums under Technical, deletion of a kit with confirmation that
  removes its files and record entry and never anything else.
- DESIGN_CHOICE: warnings (out of date, CAD problems, missing files, RISK/UNSUPPORTED counts, Bambu
  project problems) and the CAD / FDM / Physical cards stay visible; details are collapsed by default
  and the open state is remembered per kit and section.
- DESIGN_CHOICE: deletion is a direct request (not a job), refused while any job of the project runs,
  limited to `kits/<id>` with `id` matching `kit_YYYYMMDD_HHMMSS[x...]`; links (symlinks, junctions) are
  removed as links; `print_kit.json` is removed last and only when everything else went, so an
  interrupted delete leaves a listed, retryable kit marked incomplete. `build_kit` re-reads the record
  before appending, so a delete during a build cannot be undone by it.
- DESIGN_CHOICE: "Open Kit Folder" exists only in interactive desktop sessions (launcher window or
  `app` with a browser on localhost), opens only a verified kit directory, and is a POST behind the
  existing same-origin check; headless runs and tests report it as unavailable.

## D-V3-035 | 2026-10-28 | Local cell refinement, intake detail decisions, exact one-pixel tails

**Status:** IMPLEMENTED; evaluation incomplete (stopped by low memory).

- OBSERVED: the couple's infeasible closure was a cell-granularity deadlock (every blocked cell is
  supportable with all others in front; 1.2 mm / 0.3 mm2 cells plan and validate). Refinement only
  re-cuts blocked cells (then neighbors); physical rules unchanged; feasible images untouched.
- OBSERVED: minimal local refinement gave 16 sheets / 239 mm seams; +4 rings 12 / 142 mm; whole artwork
  13 / 96 mm. DESIGN_CHOICE: no single best: the wider refinements are offered as candidates; selection
  key (sheets, split regions, seam length); the user may review any candidate.
- USER_REQUIRED: no silent discarding: intake reports details it recolors or erases, with comparisons
  and preserve/remove/adjust; small removed pieces are preserved by default; size notes are info only.
- OBSERVED: `mask_to_geometry` dropped one-pixel tails (zero-width spikes removed by buffer(0)).
  DESIGN_CHOICE: correct only the pixels where the traced raster differs from the mask (iterated, at
  most 4 passes); geometry that already matched is returned unchanged; validators unchanged.

## D-V3-036 | 2026-10-28 | Pixel upscaling of a failed proposal: separate, secondary, evidence-gated

**Status:** IMPLEMENTED (paused by the user, then kept as a secondary option).

- DESIGN_CHOICE: "Upscale & Retry" raises only the planner `scale` (pixels per mm); sheet size, aperture,
  perimeter, artwork placement and every mm threshold are unchanged (9 boundary checks). Resampling is the
  existing intake (area / bicubic); no AI upscaling. Offered only after a failed proposal.
- USER_REQUIRED: a preview first; listed visible changes need approval; the failed proposal is archived
  under `attempts/`. DESIGN_CHOICE: when the finer grid changes nothing visible, the preview is
  `NO_DEMONSTRATED_BENEFIT` and no retry is offered (higher resolution only when it demonstrably helps).
- OBSERVED: full couple proposal at scale 2 peaked at 1.03 GB (CAD re-validation), 706 s under load
  (364 s unloaded). Linear extrapolation: scale 4 ~3.8 GB, scale 8 ~15 GB (refused on this 16 GB
  machine). UNVERIFIED_INFERENCE: the extrapolation; scale 4 was not measured to completion.
- LIMITATION: the couple 2x preview lists 52 changes with the 1-previous-pixel jitter filter (8 with a
  2-pixel filter); the filter may be too sensitive for review.

## D-V3-037 | 2026-10-28 | Physical resizing to clear printability findings (user approval, same project)

**Status:** IMPLEMENTED; READY_FOR_USER_TEST.

- OBSERVED: every printability threshold is in mm (planner 1.2 mm structural width; process nozzle, line
  width, min opening, min material). DESIGN_CHOICE: required factor per finding = threshold / (measured
  width - half a pixel); necks without a width, build-volume and other findings are "not size-related".
- DESIGN_CHOICE: usable sheet = process bed, and for a chosen Bambu printer the largest square clear of the
  keep-out zones (`place_on_bed`, 1 mm clearance). Smallest useful size = smallest whole mm that clears the
  most UNSUPPORTED findings any fitting size can clear; "fewest risks" offered separately.
- DESIGN_CHOICE: resizing keeps the pixel grid (scale) and changes mm per pixel: no memory-heavy upscaling.
  Preview re-runs intake at the new size (intake filters are in mm, so details can change) with 9
  proportional checks (larger, fits printer, aperture/perimeter scaled, same pixels, placement, aspect,
  inside aperture, outline) and lists visible changes for approval. Size approval is always required.
- DESIGN_CHOICE: retry plans into a staging folder, then archives proposal, review, draft, printability and
  the size analysis under `attempts/`; kits stay listed (out of date). The planner (with its local cell
  refinement) is unchanged; the FDM estimate is re-run on the new selected candidate (`resize_check.json`).
- DESIGN_CHOICE: failures that widths cannot predict (beam search at its sheet limit) get trial planning:
  largest fitting size first, then bisection to 10 mm (one beam score, no CAD).
- OBSERVED (couple, 150 -> 198 mm, P1S 236 mm usable): predicted 0 UNSUPPORTED, measured 7 (new slits
  around the newly kept red lips and two others); 11 sheets instead of 12; RISK 98 -> 64; CAD PASS. The
  remaining 7 would need >= 307 mm. Prediction is an estimate; only the re-measurement counts.
- OBSERVED (sailboat 60 -> 72 mm): RISK 4 -> 2 (predicted 0); 6 sheets. Octopus (held-out, 12 colors):
  fails at 100/150/200 mm and at 236 mm (trial): resizing alone does not resolve it.
- No result is a physical verification; geometric validation does not guarantee a successful print.
- 2026-10-29 additions (user review): predictions are labelled "estimate, not a result" everywhere and the
  measured result after re-planning is shown beside them with a warning when more UNSUPPORTED were measured
  than predicted. DESIGN_CHOICE: the retry refuses to swap in a plan whose intake target differs from the
  approved preview target (pixel-exact), so no unapproved visible change can reach the project. Remaining
  UNSUPPORTED findings are saved with sheet, location and maps (`proposal/resize_maps/`); after "Review this
  candidate" each has Locate into the existing editor (Cut slot / Cut rectangle / Fill cutout).
- RISK (OBSERVED in scratch only): archiving adds ~30 characters to paths; a very long workspace path can
  exceed the Windows 260-character limit (the default Documents\LayerForge stays at ~160).

## D-V3-038 | 2026-10-29 | Delete Project (L4)

**Status:** IMPLEMENTED; READY_FOR_USER_TEST.

- OBSERVED: a project is only its folder `<workspace>/<name>` (the list scans for `project.json`; no other
  record exists), so deleting the folder removes it from the UI and after restarts.
- DESIGN_CHOICE: `POST /api/projects/<name>/delete` (same-origin; 409 while a job is queued/running) ->
  `Workspace.delete_project`: plain project name only, folder directly in the workspace with an app
  `project.json`, a project folder that is itself a link is refused; contents deleted bottom-up with links
  removed as links (never followed); `project.json` last, so a partial failure (file open elsewhere) keeps
  the project listed and deleting again retries; failures reported per file. The imported image was copied
  into `source/` at import; the original file elsewhere is never touched.
- UI: "Delete…" per row in the project list and "Delete project…" on the project screen (any stage,
  including failed proposals); inline confirmation naming the project and folder, stating it is permanent;
  afterwards the project list/import screen with a result notice.

## D-V3-039 | 2026-10-30 | Region-based artwork recoloring (L4)

**Status:** IMPLEMENTED; READY_FOR_USER_TEST.

- USER_REQUIRED: recolor one connected color region of the processed flat-color artwork (not every area of
  that color, not a sheet, never the source image), with highlight, palette/picker/eyedropper colors,
  before/after preview, Apply/Cancel, undo and persistent save; available before planning, after a failed
  proposal and in Review & edit; explicit re-planning only, keeping the previous attempt.
- DESIGN_CHOICE: a region is one 4-connected component of one exact target color (the connectivity intake
  reports as `source_regions_4`). An optional **limit box** restricts the component to the part inside the
  box, for one of two connected parts (e.g. one trouser leg; the couple's trousers are one region).
- DESIGN_CHOICE: `core/artwork.py` keeps an edit log in `<project>/artwork/` (`base.png` copy, `current.png`,
  `edits.json`). Each edit stores the replaced color and a hash of the exact changed pixels; replay from the
  base must reproduce `current.png` bit for bit (Verify), undo/revert replay a prefix. Apply must name the
  preview id computed from the current artwork hash and the op, so only a previewed edit can be applied.
  The eyedropper color must still be present at the sampled point. The frame color is refused.
- DESIGN_CHOICE: the artwork basis is the review's reviewed artwork when a review exists, else the
  proposal's intake target. While the edited artwork differs from the one the sheets were planned from,
  the status strip says so and **print-kit export is refused** (server and UI); review sheet edits and the
  FDM estimate still work on the old stack, labelled as such. Undoing all artwork edits lifts the block.
- DESIGN_CHOICE: re-planning (`POST .../artwork/replan`, a job; refused up front when nothing changed)
  re-runs `propose` with the same source, settings and detail choices; `intake.use_edited_target` swaps in
  the edited target only if grid and frame padding are identical and the frame color is unused, and
  re-measures palette, regions, areas and thin features. The planner is unchanged. The previous proposal,
  review, draft, estimate and size check are archived as `attempts/attempt_NN_artwork`; the log moves to
  `proposal/artwork_edits/`; `proposal/artwork_check.json` records palette size and the **measured** sheet
  count before/after. No sheet reduction is promised. Flag `ARTWORK_EDITED` (INFO) discloses user edits.
- DESIGN_CHOICE: import option "Edit artwork colors before planning" (`plan=0`) runs intake only
  (`ProposalConfig.plan=False`, status `ARTWORK_READY_NOT_PLANNED`); planning starts only from the button.
- LIMITATION: re-running intake (detail choices), Upscale & Retry and resize retry start again from the
  source image, so they are refused while unplanned artwork edits exist or after planning from edited
  artwork (message says why); artwork edits are not carried across a change of processing grid.
- 2026-10-30 refinements before approval (user instruction):
  - DESIGN_CHOICE: after an applied color change the re-planning action is **Try Again**, shown in a callout
    where the Apply button was (scrolled into view) and in the "artwork edited" banner; "Plan sheets from this
    artwork" remains only for an intake-only project with no edits.
  - DESIGN_CHOICE: Physical Resize & Retry keeps region recolors. With unplanned edits or a proposal planned
    from edited artwork, the size analysis basis includes the artwork hash (any artwork change makes the
    analysis/preview out of date), the preview and trial planning use the edited artwork pixel for pixel
    (`resize.preview_resize(..., edited=)`, `trial_plan(..., edited=)`); intake still runs at the new size
    only for grid/placement/frame and boundary checks. `use_edited_target(..., edited_frame=)` re-maps the
    never-visible frame padding when intake picks another frame color at the new size (same pixels
    required). Size approval is still required; the previous attempt is archived; the edit log moves to (or
    is copied into) the new `proposal/artwork_edits/`. LIMITATION: details a larger sheet could keep from the
    source are not re-detected on the kept artwork.
  - LIMITATION kept: Upscale & Retry (finer pixel grid; recolors are exact regions of the current grid and
    re-mapping is not implemented) and intake re-runs with other detail choices stay refused while region
    recolors exist; the UI shows the reason in place of the buttons (`ARTWORK_LIMITS`).
  - Tests: memory decisions in upscale tests are pinned with `tests/resource_guard.py` (fixed probe values);
    explicit just-below/just-above threshold rejection tests; real work that needs real memory skips with a
    `RESOURCE:` reason instead of failing. `core/upscale.resource_check` and its headroom are unchanged.
