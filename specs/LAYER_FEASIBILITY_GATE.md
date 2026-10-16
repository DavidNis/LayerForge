# LAYER_FEASIBILITY_GATE: GO / NO-GO for correct shadow-box layers

This **replaces** the legacy `FEASIBILITY_GATE.md` for V3. The old gate assumed backing-rooted glued object parts. V3 tests the user-requested **layer sheets only**, even if no box or permanent assembly is provided.

## Fixture

An original rights-cleared 4–8-color illustrated scene or character should deliberately contain:

- ≥4 physical sheet layers at different depths, with one repeated filament color on two layers;
- a layer with ≥2 distinct visible visual regions;
- upper-layer negative cutout revealing a lower layer;
- optional taller perimeter/rim for one sheet;
- enough features to distinguish whether layering works, not only solid rectangular silhouettes.

The exact number of layers is a feasibility test target, **not a product-wide limit**.

## L-1 gate table

| ID | Criterion | Evidence / status |
|---|---|---|
| L-G01 | Correct rights-cleared in-scope flat input | Original image, source hash, rights/provenance; PASS/FAIL. |
| L-G02 | Layer-based design | Reviewed mask/Boolean plan gives intended **physical sheets**, not one part per anatomy label; demonstrate two visual regions on same layer. |
| L-G03 | Cutouts and occlusion | Test reveal of lower color through upper window; compare CAD-front projection and approved target. |
| L-G04 | Depth/color semantics | Back-to-front Z order, thickness, at least one repeated color on different layers; one filament assignment per layer. |
| L-G05 | CAD layer integrity | STEP/STL reopen; solid volumes and correct intended connectivity; negative holes preserved. |
| L-G06 | CAD / preview / export parity | Same physical solids represented individually, stacked and exploded; all IDs and hues match manifest, no invisible intended layer. |
| L-G07 | Source appearance | Orthographic CAD composite compared with source: silhouette and visible colors, important shapes and cutouts; differences logged. |
| L-G08 | Printability without housing | Each printable component fits tested printer envelope, viable widths and print orientation; **no mandatory frame/adhesive graph**. |
| L-G09 | Reproducibility and provenance | Exact run commands, source annotations, checksums, automatic vs manual steps declared, no fake auto recognition. |
| L-G10 | Real print and user approval | User tests a representative physical layer stack (can use temporary alignment), approves look and layer printability. |

### Status handling

- Any unresolved FAIL in L-G01 through L-G09 => `NO_GO`; show exact blocker, don't hide it.
- Passing L-G01–L-G09, but no physical evidence => `AWAITING_PHYSICAL_TEST`, not approved.
- User explicitly accepts L-G10 and all prior criteria => `USER_APPROVED`, only then advance.

### Visual metrics

- Record fixed-resolution, aligned front CAD composite vs source and optional corrected reference target.
- Recommended controlled-fixture silhouette IoU objective ≥0.95 (a **project threshold**, not proof of shadow quality or universal benchmark).
- Report per-color visibility coverage, sharp details, occlusion errors and disconnected physical regions; visual review can still fail even with strong IoU.
- Never compute metrics against a CAD-derived image pretending it is the uploaded source image.

### Open issues

If the user's eventual physical stack will need a slotted frame, that is a possible future design, **not a failed L-1 condition**. The demonstration only needs physically printable independent layers, consistent registration and visibly plausible stacking when held or temporarily aligned.
