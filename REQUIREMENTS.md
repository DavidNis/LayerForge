# REQUIREMENTS: prioritized V3 specification

Requirement IDs are used for implementation and acceptance traceability. `MUST` applies to L-1 where marked; otherwise it is an eventual MVP requirement.

## L-1 minimum

| ID | Priority | Requirement |
|---|---|---|
| R01 | MUST | Accept a rights-cleared flat illustrated test image with documented source and image-to-mm scale. |
| R02 | MUST | Describe N ordered physical **layer sheets** with stable layer IDs, one color per layer, explicit X/Y registration, nominal sheet thickness and assembled Z offset. |
| R03 | MUST | Represent each layer with additive regions **and subtractive cutouts/holes** (not just semantic object silhouettes). Multiple regions may share one layer. |
| R04 | MUST | Support physically meaningful non-art geometry internal to a layer, e.g. an optional border/rim/web for disconnected regions, separate from housing generation. |
| R05 | MUST | Generate actual, separate STL and STEP for each layer or explicitly declared subpart; exported geometry must match CAD-derived layer/stack previews. |
| R06 | MUST | Provide assembled orthographic front preview, individual layer previews, side/depth view, and exploded layer stack. |
| R07 | MUST | Report projection mismatch against source, missing features, color/occlusion order errors, layer mesh integrity and unprintable islands. |
| R08 | MUST | Support print-space transforms separate from assembly-space transforms and reproduce common XY registration upon reassembly. |
| R09 | MUST | Work without any box/stand/slot frame, glue graph or enclosure, and not fail solely because those do not exist. |
| R10 | MUST | Clearly distinguish manually authored masks/plans from automatically generated ones. |

## MVP (post-L-1)

| ID | Requirement |
|---|---|
| R11 | Auto-propose a palette and candidate region masks from new clean PNG/JPEG without requiring preexisting `assembly.json` for that image. |
| R12 | Provide a layer editor: paint/vector mask correction, holes, merge/split, order, layer color, rim size and thickness. |
| R13 | Support multiple layers of the same color and color repeats at distinct Z positions. |
| R14 | Handle same-layer disconnected islands via explicit connected rim, supports or separately exported subparts; show warning and correct quantities. |
| R15 | Allow optional rim to be thicker than the interior region, based on user parameters and independently validated mesh. |
| R16 | Support small 2D preview comparing source per-color masks and visible assembled CAD projection, excluding transparent-background areas correctly. |
| R17 | Export a zipped layer kit with `layers/`, `manifest.json`, colored previews, `validation.json` and `assembly_order.md`. |
| R18 | Save/load a project with manually edited layer boundaries and provenance preserved. |
| R19 | Configure printer bed dimensions/nozzle/material; check each printable component fits after print placement and minimum features. |
| R20 | Test an unseen flat cartoon illustration; report corrections needed before claiming generalization. |

## User interaction and failure

- The user may choose target total dimensions, maximum layer count, intended palette, preview depth spacing, nominal sheet thickness and optional rim dimensions. Defaults must be clearly labeled as assumptions and adjustable.
- A color mask is not automatically a physical layer, and an illustration has no uniquely inferable layer order. A review step is required where ambiguous.
- If an unconnected island cannot be printed/retained using the chosen layer construction, flag as invalid, suggest a bridge or separate subpart, never hide it.
- If the render looks plausible but doesn't correspond to the exported meshes or visual source, mark FAIL.
- If CAD backend cannot provide valid STEP, fail honestly. Never write an STL file renamed with `.step`.
- If the intended physical effect depends on unknown paper/print/shadow constraints, report NEEDS_USER_DECISION.

## Product distinction

`visual_region != physical_layer != exported_component != print_plate != housing`.

An STL can represent a complete physical layer, whereas Bambu Studio may place it alone on a plate; the Bambu build plate index is a manufacturing arrangement rather than semantic proof of layer depth order.
