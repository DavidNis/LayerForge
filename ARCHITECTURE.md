# ARCHITECTURE: layer-first, geometry-first

## One true data model

```text
SOURCE ILLUSTRATION
      |
      v
palette + candidate color masks (can be manual in L-1)
      |
      v
REVIEWED LAYER PLAN
  canvas + n indexed layer sheets
  [additive shape polygons, subtractive cutouts,
   one material/color each, rim/web option, depth]
      |
      v
DETERMINISTIC LAYER GEOMETRY ENGINE
   planar booleans -> valid sheet/rim solids -> mesh tessellation
      |
      +----> STEP / STL per layer or declared printable subpart
      +----> layer previews / stacked front and side views / exploded
      +----> validation and projection overlay
      +----> print transform vs stack transform mapping
```

No semantic-object-to-physical-part shortcut. Recognition can **suggest regions**, but layer planning is a distinct operation that can combine, cut and occlude regions across source objects.

## Domain entities

1. **Artwork**: immutable input image hash, dimensions, optional transparency, rights/provenance, physical target size and preview profile.
2. **VisualRegion**: colored visible pixels/vector polygon from illustration; masks have holes and anti-alias policy. This is evidence, not automatically a layer.
3. **LayerSheet**: ordered depth index, one assigned filament/color for V1, nominal thickness, frame/reference perimeter optional, plane Boolean expression (`union(additive) - union(cutouts)`), assembled position, and connectivity policy.
4. **PrintableComponent**: one closed physical mesh belonging to exactly one layer, or an explicitly documented separate auxiliary component. A layer can require multiple components when unavoidable.
5. **PrintPlacement**: per-component slicer orientation and translation; it is **never** the assembly transform.
6. **LayerStack**: ordered physical view transforms and alignment/spacing assumptions. Can be visualized without committing to a box or glue model.
7. **CADBuild**: actual solids and validation derived from layer plan; exports and previews share these solids.

See `specs/LAYER_MODEL.md` for an example schema.

## Geometry algorithm

For each reviewed layer in specified bottom-to-top order:

- Map source geometry into millimeters with a common canvas origin. Extract polygons and holes; validate and repair only under a logged, reviewed policy.
- Resolve selection/negative masks to polygons: add regions, subtract areas meant to show lower layers; add optional edge rim/web **only if user parameter enabled**.
- Union touching polygons, preserve true holes, check tiny bridges and disconnected components; do not fill cutouts to force watertightness.
- Extrude each sheet to configured `nominal_thickness_mm`; optional thicker rim has independent height from local base and must merge cleanly.
- Assemble in stack coordinates with explicit `z_mm` and lateral registration. Slicer mesh remains laid flat in its own print coordinates.
- Check projected visible colors and silhouettes from correct depth/order, plus solid topology and printer-fit.

## Recommended tech

- Python 3.12 with pinned dependencies and a reproducible CLI.
- OpenCV + NumPy for flat-color mask extraction, color labeling and morphology (suggestions only).
- Shapely for robust planar polygon booleans and area/contact checks.
- CadQuery/OCCT for real solids and STEP; trimesh or equivalent for mesh verification.
- matplotlib/Pillow or CAD mesh projection for test visuals. React/Three.js and FastAPI **deferred** until L-1 is approved.

This pack is for a new repository. Implement only the smallest proven Python geometry/export/validation path required for L-1. Record each dependency and do not imply a CAD engine already exists.

## Key risks

- Anti-aliasing, outline/negative-mask ambiguity and palette collapse.
- Shadow-box layers not uniquely inferable from pixel colors alone.
- Multiple islands being disconnected while a rendered silhouette appears continuous.
- Unintended occlusion caused by wrong Z order or incorrect cutout geometry.
- Thin colored details failing at 0.4 mm nozzle.
- Confusing separate Bambu Studio print plates and physical depth levels.

## Extensibility

MVP front-end: source/mask editor, layer reorder, holes, per-layer swatches, exploded stack and source comparison. Housing designer is a **separate future extension**, not a blocker or a mandatory dependency.
