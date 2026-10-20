# LayerForge L1 — reference-based thin geometry checkpoint

Status: `READY_FOR_USER_TEST`  
L1 approval: **NO**  
Production validator changed: **NO**  
Planner executed: **NO**

## Scope and measurement method

`Pikachu_scream_original.3mf` declares millimeter units and contains nine
artwork plates (`layer-0` through `layer-8`) plus a separate `box`, which was
excluded. The artwork meshes were inspected directly from their triangle data.

Each retained-material footprint is the union of every mesh triangle having a
non-zero XY projection. This includes both lower artwork surfaces and the
higher rim; selecting maximum-Z faces alone would incorrectly retain only the
rim. Masks were rasterized isotropically at 0.05 mm/pixel. Reported widths are
distance-field measurements with approximately 0.10 mm edge/diameter
quantization, not exact continuum-mechanics values.

The 109 x 144 mm plate was embedded without scale distortion in a 144 x 144 mm
diagnostic canvas. Its connected top and bottom rim reaches the unchanged
validator's anchor band. This supports a like-for-like retained-width test, but
does not turn the reference into a LayerForge-generated common-perimeter stack.

3MF print-profile metadata records a 0.4 mm nozzle, 0.42 mm nominal line width,
0.2 mm layer height, classic walls, two wall loops, and thin-wall detection off.
Those settings are context only; no slicing or physical print was performed.

## Reference geometry results

All nine artwork meshes have zero raw boundary edges, zero non-manifold edges,
and pass the triangle edge-incidence watertight check. Each rasterized retained
footprint is one 4-neighbor component.

| Plate | Finished thickness | Through-cutout components | Representative thin retained geometry | Representative narrow opening | Existing 1.2 mm result |
|---|---:|---:|---|---|---|
| layer-0 | 0.80 mm | 1 | None detected below 1.2 mm on the medial line | Main opening about 95 mm | PASS |
| layer-1 | 1.00 mm | 10 | Terminal detail about 0.50 mm | 0.36–0.40 mm | PASS |
| layer-2 | 2.20 mm | 2 | None in sampled medial features | about 0.40 mm | PASS |
| layer-3 | 2.20 mm | 56 | terminal 0.98–1.06 mm; critical necks about 1.18–1.20 mm | as narrow as 0.22–0.40 mm | CONFLICT: 81 orphan cores / 9,481 pixels |
| layer-4 | 2.20 mm | 74 | terminal 0.80–1.00 mm; critical necks about 1.18–1.20 mm | as narrow as 0.22–0.40 mm | CONFLICT: 57 / 3,746 |
| layer-5 | 2.20 mm | 41 | terminal 0.70–1.10 mm | about 0.32–0.50 mm | CONFLICT: 82 / 463 |
| layer-6 | 2.20 mm | 43 | terminal 0.64–0.70 mm; ambiguous feature about 1.10 mm | about 0.32–0.36 mm | CONFLICT: 47 / 1,505 |
| layer-7 | 2.20 mm | 10 | terminal strokes about 0.60–1.00 mm | about 0.36–0.50 mm | PASS |
| layer-8 | 0.60 mm | 0 | None detected | None | PASS |

At 0.05 mm/pixel the configured 1.2 mm requirement becomes a centered 25 x 25
elliptical kernel, or 1.25 mm discretely. The largest passing footprints for
the largest layer-3 orphan cores are approximately 0.95–1.05 mm; layer-4
through layer-6 contain many borderline cores whose largest passing footprint
is about 1.15 mm.

### What the current validator gets right

- Four-neighbor connectivity and retained-width are separate. All reference
  sheets are connected before erosion.
- A thin terminal stroke may disappear under erosion without failing the
  sheet, because no surviving structural core depends on it. Layer-7 contains
  0.60–1.00 mm terminal strokes and still passes.
- A narrow sole attachment to a substantial retained area creates a surviving
  orphan core and fails. Layers 3–6 contain examples.
- Negative openings do not participate in retained-material width failure.
  Openings down to roughly 0.22–0.40 mm are present in the actual geometry.

### What it does not express adequately

- Thin terminal branches inside an otherwise connected visible component are
  silently lost during erosion rather than reported as separate decorative
  printability warnings. The report's existing `thin_decorative_visible` count
  works only at whole visible-component granularity.
- Every orphan core fails equally. The validator does not distinguish a tiny,
  low-load decorative lobe from a large region connected through its only
  narrow neck.
- It does not measure alternate/redundant attachment paths or the area, extent,
  and lever arm supported by a narrow connection.
- It does not validate whether narrow openings will survive a particular
  nozzle, line width, elephant-foot compensation, slicer, or layer thickness.
- The existence of the reference mesh and its print profile does not prove the
  four rejected plates survived real printing or handling.

## Turtle classification

The existing rejected turtle masks were reused unchanged; preprocessing and
planning were not rerun.

### A — thin decorative retained features

These are warnings rather than the basis of the current structural failure:

- physical layer 7, green: one 39 x 1 pixel retained stroke;
- physical layer 8, brown: three tiny retained features, including a 31 x 1
  pixel stroke;
- physical layer 9, navy: three features, including 25 x 1 and 23 x 1 pixel
  strokes.

Their structural role is decorative, but at 0.174 mm/pixel their actual
extrudability is unvalidated and must not be inferred from that label.

### B — critical structural bottlenecks

- Layer 7 green: a 24,651-core-pixel leaf region at outer-mask bounds
  `[69,362,395,120]` is connected through material whose largest passing
  footprint is about 0.868 mm. This is a substantial downstream payload, not a
  harmless line.
- Layer 8 brown: a 3,691-core-pixel region at `[117,258,57,140]` has the same
  approximately 0.868 mm maximum passing footprint.
- Layer 10 black: visible core islands such as `[175,341,15,23]` and
  `[161,402,17,14]` only retain perimeter connection at about 0.521 mm.

### C — disconnected or unsupported material

None. Every rejected turtle sheet is one 4-neighbor-connected component and
has zero edge-disconnected orphan material before erosion.

### D — ambiguous physical significance

Layers 3–6 contain only hidden orphan cores in the failing areas. They are
definite failures of the configured raster design rule, but the actual load,
orientation, and print survival of each hidden feature are unknown. Component
counts also fragment one narrow network into many cores, so counts alone should
not be read as the number of independent defects.

Every failing turtle mask also contains narrow through-openings with sampled
medial widths around 0.347 mm. Those openings are not retained bridges and did
not cause the structural failures.

## Proposed validation model

Keep six separate checks and never collapse them into one minimum-width rule:

1. **Sheet connectivity — strict.** Require one 4-neighbor/material component
   connected to the declared shared perimeter. Any floating material fails.
2. **Structural connection graph.** Erode at the configured structural width,
   make surviving core regions graph nodes, and make intervening thin zones
   graph edges whose capacity is their measured minimum width. Removing each
   thin edge reveals whether it is the sole path from a core payload to the
   perimeter. Report supported area, maximum span, thickness, and alternative
   paths. A sole attachment carrying a substantial core remains a strict
   conflict at the configured 1.2 mm threshold.
3. **Decorative retained features.** A sub-threshold branch with no downstream
   core payload is a decorative-feature warning, not a structural-bridge
   failure. Check it separately against nozzle/line-width and user-selected
   detail policy. Redundant thin links are warnings when a wider path exists.
4. **Through-opening printability.** Analyze the void distance field and minimum
   throat width separately. Configure nozzle, line width, layer height,
   elephant-foot compensation, material, and intended visual importance. Never
   apply retained-material erosion to empty space.
5. **Visible reconstruction — strict.** Preserve exact indexed-target color and
   geometry, and report any deliberately accepted printability loss separately.
6. **Perimeter and hidden support — strict.** Require identical registered outer
   geometry, continuous perimeter, zero visible connector pixels, and support
   material only where occluded.

Strict thresholds should remain: connected sheet, no floating material, common
perimeter, exact reconstruction, and no visible support bridges. Structural
neck width remains 1.2 mm until explicitly changed. Decorative-line and opening
thresholds should be printer-profile parameters, while any exception to a
critical sole attachment requires explicit user approval rather than an
automatic pass.

## Recommended next checkpoint

Implement a diagnostic-only `feature_role_audit` beside the existing validator;
do not change PASS/FAIL yet.

Acceptance criteria:

1. Reproduce all existing reference and turtle connectivity/width results.
2. Identify layer-7 reference strokes around 0.60–1.00 mm as terminal features
   with zero orphan-core payload.
3. Identify the large reference layer-3/4 and turtle layer-7 core regions as
   sole-path structural bottlenecks and report their supported areas.
4. Report 0.22–0.40 mm reference and approximately 0.347 mm turtle openings only
   under the opening-printability section.
5. Produce deterministic role overlays and JSON, with no planner, source, or
   production-validator changes.

Stop after user review before integrating any new classifications into the
production acceptance gate.
