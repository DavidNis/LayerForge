# TEST PLAN: ensure layers, not object collections

## Evidence categories

- **Reference evidence:** facts extracted from actual 3MF/STL metadata and geometry.
- **Plan correctness:** approved layer masks, holes, filament, order and registration.
- **CAD correctness:** actual reopened solid validity and mesh parity.
- **Visual correctness:** front composite vs source after occlusion; independent layer and side views.
- **Manufacturing:** features, thin webs, disconnected islands, print placements, physical stack trial.
- **Generalization:** at least one previously unseen flat illustration beyond manually annotated fixture.

## L-1 positive tests

1. `T-L01`: at least 4 physical layer sheets in a stack, with one repeated color on distinct layers; preserve correct depth and color.
2. `T-L02`: **one physical layer contains two or more visual regions** on a common sheet; not one generated STL per image object.
3. `T-L03`: a through-hole/cutout in an upper layer reveals geometry/color of a lower layer in frontal projection.
4. `T-L04`: a raised rim on at least one layer has a different height from interior art and exports as the intended valid CAD solid. Feature is optional outside fixture.
5. `T-L05`: same X/Y origin and physical canvas for every layer. Print placement transforms kept separate from assembly Z transforms.
6. `T-L06`: each layer produces documented STEP and STL; reopen all, check bounds, volume, winding/manifold, intended connected components and important holes.
7. `T-L07`: preview for individual sheet, assembled front and side, and exploded layer stack come from the actual exported CAD source, no fake objects.
8. `T-L08`: source-vs-composite has documented silhouette/visible-color metrics, with critical details manually inspected. Distinguish segmentation errors from layer order issues.
9. `T-L09`: warnings for every unsupported/floating island, too-thin bridge, unintended intersection and bed overflow; output explicitly fails on severe errors.
10. `T-L10`: shape/color order changed intentionally in test manifest leads to a detectable difference or failure rather than an unchanged mock preview.
11. `T-L11`: changing print-plate layout cannot silently change stack assembly positions.
12. `T-L12`: rerunning fixture yields matching layer IDs, stable exported geometry metrics and documented deterministic variation tolerance.

## Negative tests

- Upper-layer hole filled by mistake -> FAIL visual projection.
- Misordered layer colors -> FAIL visible-color agreement.
- Grouping all semantic regions as separate floating solids while calling each a physical sheet -> FAIL physical layer topology plan.
- Disconnected islands without declared support/subparts -> FAIL geometry/safety check (unless explicitly accepted as separate named printable components).
- Invalid/nonmanifold STEP/STL or rescaled layer -> FAIL CAD/export validation.
- Preview geometry differs from exported solid or an intended layer is omitted -> FAIL view-export parity.
- Frame is omitted -> should **NOT** fail unless optional frame validation specifically enabled.
- Any agent claim "automatic" while masks are hand authored -> FAIL provenance review.

## Quantitative measures

- Orthographic source-aligned outer silhouette IoU, per-visible-color pixel mask IoU/coverage, and annotated critical-detail coverage.
- Measure at fixed raster resolution and target size; log anti-alias settings, mask alignment and source edits.
- Proposed initial engineering target: source-composite silhouette IoU ≥ 0.95 **for the controlled flat fixture**. This threshold is only a proposed test setting and cannot on its own prove quality or user satisfaction.
- Bound/volume after STEP/STL read-back checked against source CAD; tolerances chosen, justified and reported by agent (not assumed automatically).
- Number of physical layers, printable connected components and intended colors each measured distinctly.

## Physical test before L-1 approval

- Import each STL to slicer without unintended rescaling. Confirm dimensions and thickness.
- Print at least a representative sample covering a cutout, detailed feature and optional rim. Test full stack if practical.
- Temporarily align layers in intended order for a front-view photograph; no permanent housing requirement.
- Check surface, shadow effect, fragility, registration/flatness, cutouts and actual color sequence.
- Record pass/fail/pending and photographs, plus **explicit user approval**. Without a real test remain `AWAITING_PHYSICAL_TEST`.

## Expected report

```text
Milestone: L-1
Fixture source, rights and original image:
Number of physical layer sheets and printable components:
Manual mask/stack actions vs automated actions:
Exact CLI commands and environment:
Tests pass/fail/skipped:
STEP/STL read-back, validation:
Visual projection comparison and observed flaws:
Disconnected islands, rims, cutouts, thickness checks:
Print-space vs assembly-space verified:
Physical test: PASS / FAIL / PENDING
User sign-off: NO unless explicit
Open unknowns and next precise test:
```
