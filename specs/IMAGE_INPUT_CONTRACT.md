# IMAGE_INPUT_CONTRACT: flat illustration intake

## Current in-scope sources

- PNG or JPEG (SVG import optional future), clean flat cartoon colors with reasonably distinct boundaries.
- Pixel dimensions and optional alpha supplied. Physical XY dimensions provided by user or labeled example default; aspect ratio preserved unless intentionally cropped.
- Palette detection is a proposal; anti-aliased edge pixels are not new physical materials.
- Original user-controlled or rights-cleared fixtures only for committing to tests; third-party reference artwork may be used privately for inspection, never re-distributed by default.

## Source transform

- Image origin: upper-left, x right, y down.
- Layer canvas in mm: origin lower-left, x right, y up; document the vertical inversion exactly once.
- One global source-to-model scale; all layer sheets share the same XY reference frame.
- Store source hash, crop, transparency treatment, vectorization settings, rasterization resolution and user edits.

## Reject or warn

- Real-world photos/gradients/textures outside V1 flat-color goal.
- Tiny raster or anti-aliased details too small for configured nozzle / manufacturing scale.
- Ambiguous touching regions where removing the background could destroy a narrow bridge.
- Colors that cannot be mapped to target filament materials; ask user instead of inventing exact print appearance.
- Art with logically conflicting cutout/depth ordering; require manual confirmation.

## Provenance

Each region or layer stores whether it is `manual`, `auto-proposed`, `user-corrected`, or `reference-derived`. A manually authored plan **cannot be reported as automatic image-to-layers success**.

## Measurable minimum

For the L-1 original fixture, supply a source file, masks, target visible composite and annotation notes. For L1 automation, test at least one held-out illustration not used to hand-code the pipeline.
