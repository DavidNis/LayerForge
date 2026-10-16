# EXPORT_AND_PREVIEW: verifiable output contract

## Required L-1 output structure

```text
artifacts/layer_feasibility/<fixture_id>/
  source/source.png
  source/annotation_notes.md
  layer_stack.json
  layers/
    layer_00/
      layer_00_body.step
      layer_00_body.stl
    layer_01/
      layer_01_body.step
      layer_01_body.stl
    ...
  previews/
    layer_00.png
    layer_01.png
    ...
    assembled_front.png
    assembled_side.png
    exploded_layers.png
    comparison_overlay.png
  reports/
    validation.json
    checksums.sha256
    print_and_stack_guide.md
    commands.log
```

Actual file names and exact quantity follow the approved fixture. Extra subcomponents must be named, indexed and declared in manifest; no missing meshes hidden behind mock artwork.

## Orientation and coordinates

- **Layer assembly space:** fixed common art XY, explicit back-to-front Z order, physical mm.
- **Print space:** independently rotated/translated each layer flat on build plate, in 3MF or slicer; poses differ from assembled stack.
- `STL` generally carries geometry only, no reliable filament-color metadata. Save colors in the manifest and optionally 3MF in later milestone.
- `STEP` must be genuine CAD BRep-based solids and reimportable. Reopening both STEP and STL is mandatory.

## Preview contract

- Render selected colors on actual tessellated CAD solids. If renderer reprojects to 2D, explicitly document camera, alpha, lighting and hiding policy.
- `assembled_front` orthographic image should show all geometry visible at correct Z order, regardless of render engine face culling; regression test with a known exposed region.
- `assembled_side` shows true thickness/rim depths; if visual gap is enlarged for explanation, label as illustrative not physical.
- `exploded_layers` uses view-only pose offsets on actual solids and labels layer ordering; must allow user to map every layer to exported file.
- Generate `comparison_overlay` from CAD-generated front projection vs uploaded source, and report where color/shape differs.

## Release truth

Deliverables must have real relative paths and checksums; mark absent/failed outputs. All previews must be reproducible from `layer_stack.json` and exported geometry. A successful PNG render alone does not pass feasibility.
