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

## L0 reusable geometry core (implemented 2026-10-24)

The accepted L-1 engine is exposed as `src/layerforge_l1/core/`. It wraps the
existing code; the layer-generation algorithm is **not** in this package.

| Responsibility | Module | Reuses (unchanged) |
| --- | --- | --- |
| Data model, versioned manifest, legacy migration | `core/model.py` (`SheetStack`, `Sheet`, `StackGrid`, `save_stack`, `load_stack`, `import_planner_output`) | `reveal_planner.physical_grid/embed`, `experimental_global_planner.exact_index` |
| Approved planner entry point | `core/planning.py` (`PlanningConfig`, `plan_stack`, `PlanningError`) | `reveal_planner.RevealPlanner` (closure, beam_search, eliminate_sheets, materialize) |
| Validation | `core/validation.py` (`validate_stack`, `validate_sheet_mask`) | `connectivity_repair.audit_edge_connectivity`, `audit_connection_width` |
| CAD export and read-back | `core/cad.py` (`mask_to_geometry`, `export_stack`, `revalidate_cad`, `stl_footprint`, `rasterize_stl`) | `geometry.extrude_geometry` (CadQuery), trimesh |
| Previews | `core/preview.py` (`generate_previews`) | none |
| Reference regression | `core/reference.py`, `fixtures/reference_stacks.json` | none |
| CLI | `core/commands.py`, dispatched from `cli.py` (`stack-*`) | existing `cli.py` structure |

**Where the algorithm lives:** `src/layerforge_l1/reveal_planner.py`. Only
`core/planning.py` calls it, in the same order as the accepted run script
`scripts/run_turtle_reveal_planner_poc.py` (variant `per_cell`). The CP-SAT
(`experimental_global_planner.py`), `width_aware_planner.py`,
`subtractive_poc.py`, `layer_optimization.py` and the repair modules are
experimental predecessors; they are kept but not used by the core.
`fullres_flatten.py` (palette snapping) is image preparation and is not part of
the core; the reference target is its saved, user-reviewed output.

**Moved code:** `mask_to_geometry` moved unchanged from
`scripts/export_reveal_stack_cad.py` to `core/cad.py`; the script imports it.
Line references to it in `docs/technical_optB` (ch. 3 and 11) are therefore
historical.

### Data flow

```text
flat exact-palette RGB target (square, side divides 512*scale px)
  -> plan_stack(target, PlanningConfig)            [reveal_planner, unchanged]
  -> SheetStack (back-to-front bool masks on one grid, one color per sheet)
  -> save_stack(dir) / load_stack(dir)             [sheet_stack.json v1.0.0 + PNG masks]
  -> validate_stack(stack)                         [existing validators]
  -> export_stack(stack, cad_dir)                  [mask -> polygons -> CadQuery -> STEP + STL, reopened]
  -> revalidate_cad(stack, cad_dir)                [reopened STL -> raster -> same validators]
  -> generate_previews(stack, dir, stl_masks)      [sheet / assembled / exploded / source comparison]
```

### Manifest `layerforge.sheet_stack` 1.0.0

`sheet_stack.json` holds `grid` (outer/perimeter pixels, outer mm, scale,
mm per pixel, artwork mm), `artwork` (optional `target.png` with RGB hash and
`frame_color`), `construction`, and `sheets` back to front: `id`,
`order_back_to_front`, `color`, `thickness_mm`, `assembly_z_mm` (cumulative,
checked on load), mask paths with decoded-bit SHA-256, and `print_placement`
(identity: every sheet printed flat at z = 0, separate from assembly z).
Masks: 255 = retained material, 0 = through-cutout. `load_stack` rejects
unknown schema/version, reordered sheets, hash mismatches, missing files and
grid sizes the planner/validator cannot reproduce. Pre-L0 planner `layers/`
directories have no manifest; `import_planner_output` migrates them (outer mm
and thickness must be supplied, as they were command-line values). The vector
`LayerStack` manifest of the orbit_bloom fixture remains served only by
`python -m layerforge_l1 build`.

### How later milestones call the core

L1 (automatic proposals) should produce an exact-palette target and call
`plan_stack`; L2 (editor) should edit a loaded `SheetStack`'s masks and call
`validate_stack` before `export_stack`; L3 should consume `cad_manifest.json`,
`cad_revalidation.json` and previews. None of these milestones is started.
