# LAYER_MODEL: normative schema, with example

## Glossary

- `VisualRegion`: one visible area in 2D illustration. May be split across multiple physical layers or merged with other regions.
- `LayerSheet`: one intended **physical depth plane / printed colored layer**. Multiple regions and holes; can have an optional perimeter carrier/rim. Not necessarily exactly one connected manifold unless required by chosen construction.
- `PrintableComponent`: one exported connected solid. One layer can contain multiple components only with explicit independent placement/count and warning.
- `PrintPlate`: a slicer arrangement/filament job. Does **not** imply assembled depth order.
- `Housing`: optional separate later product; NOT part of active schema/gates.

## Example *conceptual* manifest (not a file from reference examples)

```json
{
  "schema_version": "3.0.0-draft",
  "artwork": {
    "image": "source.png",
    "sha256": "SET_FROM_ACTUAL_FILE",
    "pixel_size": [1200, 1200],
    "canvas_mm": [144.0, 144.0],
    "origin": "bottom-left"
  },
  "construction": {
    "method": "registered_layer_sheets",
    "one_filament_per_layer": true,
    "housing_required": false,
    "assembled_z_axis": "front_positive"
  },
  "layers": [
    {
      "id": "layer_00",
      "order_back_to_front": 0,
      "color": "#202020",
      "nominal_thickness_mm": 1.0,
      "assembly_z_mm": 0.0,
      "geometry": {
        "additive_regions": ["background", "shared_rim"],
        "cutout_regions": ["window_01"],
        "rim": {"enabled": false}
      },
      "print_components": ["layer_00_body"]
    },
    {
      "id": "layer_01",
      "order_back_to_front": 1,
      "color": "#FAD900",
      "nominal_thickness_mm": 1.0,
      "assembly_z_mm": 2.0,
      "geometry": {
        "additive_regions": ["foreground_shape", "rim_sheet"],
        "cutout_regions": ["face_window"],
        "rim": {"enabled": true, "height_mm": 2.5, "width_mm": 3.0}
      },
      "print_components": ["layer_01_body"]
    }
  ],
  "print_placements": {
    "layer_00_body": {"rotate_xyz_deg": [0,0,0], "translate_xy_mm": [0,0]},
    "layer_01_body": {"rotate_xyz_deg": [0,0,0], "translate_xy_mm": [0,0]}
  }
}
```

**Illustrative numeric values only.** The implementation must define a validated exact JSON schema and document how handles, polygons, masks, persistent provenance and physical mm geometry are encoded. Don't treat this snippet as complete production schema or claim it can build as-is.

## Essential invariants

1. Layer IDs and ordering are stable, no duplicate IDs/order slots in one stack.
2. Every printable component belongs to an explicit physical layer; each layer's components share one filament/material color for V1.
3. All physical sheets use consistent source-to-mm registration and orientation. Layers may have different outer silhouette **unless user wants a common full outline**.
4. `assembled_z` is independent of slicer placement/print-layer height. Front positive Z is consistent with CAD rendering.
5. A layer's area is constructed from Boolean additive and subtractive polygons; preserve internal holes.
6. Layer rim/carrier geometry optional, parametric and identified as physical material, not silently visual decoration.
7. A disconnected printable island is exported as named separate component or connected by reviewed support geometry; never disappear or become non-manifold.
8. The real CAD solids are source for previews, exported files and geometry validation.

## Color and ordering

V1 default: each physical layer uses one color. A color can occur in multiple layers. Color is metadata for printed filament and CAD preview, not STL native metadata; `manifest.json` must hold RGB/name and planned filament separately.

User reviews proposed back-to-front ordering when layering cannot be deduced uniquely from illustration.
