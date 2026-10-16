# REFERENCE_EVIDENCE: observed, interpreted, unknown

Use this as a **design reference, not a reverse-engineering truth oracle**. The examples were supplied by the user for learning the target style. They should not be copied into a public git repository unless redistribution rights are confirmed.

## Example A: Pokémon Umbreon (ZIP with six STL files)

### Observed directly from geometric measurements

- Archive contained **6 STL files**, named `obj_1` through `obj_6`.
- Three mesh bounding rectangles measured approximately **175×130 mm** (`obj_1`, `obj_3`, `obj_5`), with different detailed geometry and heights around 6, 6.5 and 13.4 mm respectively.
- One mesh `obj_2` measured roughly **155×50×25 mm**, consistent in shape/size with a separate display base; exact intended orientation/role was not independently established.
- Two small meshes `obj_4`, `obj_6` measured roughly **38.5×42.1×3.5 mm** and **40.2×39.6×2.5 mm**.
- STL does not reliably encode displayed filament colors, physical assembly sequence or exact role labels. Non-watertightness observed in some reference meshes during exploratory load, so do not claim this is a perfect CAD gold standard.

### Interpretation (NOT proved)

Likely includes broad image layers and small detail pieces plus a base; the above bounds alone do **not** establish final stacking order, material color or whether the small features are inlays, overlays or supports.

## Example B: Girl with a Pearl Earring (`.3mf`)

### Observed in parsed 3MF files

- Package metadata lists **11 Bambu Studio plates**, `layer-0` through `layer-9` plus `box`.
- Internal geometry for most layer-labeled objects has nominal planar bounds **144×144 mm** and height about **1.01 mm**; one measured layer-like object is about **2.8 mm** thick (before assembly transform).
- The `box` object geometry is separate and larger in depth; metadata identifies it as `Assembly` in some object fields.
- A slicer **plate index is a print arrangement**, and may not by itself equal final physical Z ordering. The layer names strongly suggest intended layer sequence, but verify the model/creator instructions for actual build orientation if necessary.

### Not established from the sample alone

Exact mechanical means of holding assembled sheets, adhesive use, or a universal requirement that every layer in every design must be a continuous full-rectangle sheet.

## Example C: Pikachu Scream (`.3mf`)

### Observed in parsed metadata/geometry

- Model settings contain print plates `layer-0` through `layer-8` and one `box` plate: **9 artwork plates + 1 housing-related plate**.
- Earlier examination reported a creator note: "AMS is not needed; each layer is a different color." This is a note about **that example's intended printing method**, not a universal requirement.
- Each of the nine artwork printing plates was reported to have a single filament/color assignment; color **can recur** across plates, so the note's wording should not be construed as globally unique colors.
- Detailed geometric inspection in earlier analysis reported layer rim vs interior height differences on several layers, around **2.8 mm rim vs 1 mm interior**. Treat this as a model-specific finding; mechanism, orientation and generality need separate validation.

### Open unknowns

Whether raised rims function for spacing/alignment, how that example is assembled physically, and whether individual internal islands are independently attached or connected by concealed geometry.

## Cross-example conclusions we can safely use

- At least the Girl/Pikachu examples explicitly encode **layer-oriented workflows** and a **separately printable box**.
- A generated physical layer can be a broad sheet with holes, internal color detail and independent smaller shapes; a contour of a visible semantic object is **not always** the desired unit of output.
- The user's requested V1 is layer fabrication only; support hardware is not part of the pipeline now.
- The V1 default of one color per layer is a justified **project choice**, not a proven fact about all future artwork.

## Do not claim

- Every layer always has exactly one color in all designs (outside the chosen V1 constraint).
- 3MF plate indices always encode physical assembly depths.
- All layers always need full rectangular borders, or rims always act as spacers.
- Source art alone reveals the actual model author's CAD construction or hidden join strategy.
- Existing Lumi Bird object-first fixture verifies this reference-inspired layer generator.

## Using examples in engineering

Store geometry/print metadata analysis separately and leave reference assets private unless license permits. Tests for the generator should use a **redistribution-safe original drawn image** and report which part of the reference it is emulating. Never leak unverified assumptions from reference inspection into hard-coded CAD rules.
