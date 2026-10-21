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

The coding agent should **not block the controlled L-1 experiment** on these choices. It should expose them as parameters, use transparent experiment-only defaults for L-1, and request clarification before fixing a final product policy.

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
