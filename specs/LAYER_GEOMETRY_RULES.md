# LAYER_GEOMETRY_RULES: 2D masks to layer sheet solids

## Step 1: masks and polygons

- Normalize image geometry and preserve transparent zones.
- Extract/simplify paths with controlled tolerance. Polygon holes must be topology-aware; tiny voids cannot silently vanish during smoothing.
- Draw or derive silhouette regions and negative windows; explicitly annotate which upper-layer pixel area must reveal lower-colored material.
- Verify topology: valid rings, non-self-intersection, no accidental pixel islands after thresholding.

## Step 2: design layer sheet shape

For each physical layer, compute:

`layer_area = union(additive_regions + optional_carrier_web + optional_rim_footprint) - union(through_cutouts)`

This is a conceptual formula; some optional rim geometry may be extruded differently from the main layer. The designer must choose whether holes continue through rims and any local raised accent; do not infer automatically.

- V1: one color/filament per layer.
- A layer may have isolated disjoint visual regions; printer output could be a connected sheet if carrier/rim exists, or multiple named components otherwise.
- Geometry **inside** an art layer (e.g. raised edge rim) remains a feature of that layer, not a mandatory external box.
- `nominal_thickness_mm`, `rim_height_mm`, `rim_width_mm`, `z_mm`, `gap_mm` must be explicit and physically interpretable.

## Step 3: extrude, assemble, export

- Reuse validated CadQuery/OCCT code for cutout extrusions where suitable. Boolean operations must preserve holes and supported tiny details.
- Construction-space XY is identical for all layers; actual printed parts may be positioned independently on the slicer bed.
- Side view must show actual heights/gaps. If intended spacing requires a separate housing that has not been designed, the preview must label the spacing as **proposed**, not physically guaranteed.
- Every preview/mesh comes from real solids, not an independent illustration generator.

## Step 4: manufacturability

Check manifoldness/watertightness, positive physical volume, correctly detected solid components, through-hole preservation, min-web width/nozzle considerations, overhangs if extruding upward, and slicer fit. Warn that a print can still fail due to first-layer adhesion or filament behavior.

## Test-specific proof of layer-first architecture

- Two unrelated visual regions placed on **one** printed layer in the fixture.
- One additive silhouette and a through-window in one layer, lower color visible behind it.
- At least one higher layer partially occludes lower layers.
- At least one color appears on two different depth layers.
- Compare generated total print components to layer count and describe any difference. The counts **must not** be assumed identical by definition.

## What not to build

- Do not automatically produce a backing plaque with a glued collection of anatomy pieces.
- Do not make a frame/box to patch badly structured layer geometry.
- Do not require permanent glue or mounting for core validation.
- Do not force shared rectangular borders until user policy is established; if common rims are used, describe them as one optional construction strategy.
