# Orbit Bloom print and temporary stack guide

Status: **software validation only; physical test PENDING**.

## Slicer settings and quantity

- Import each STL below as a separate model at **100% scale in millimetres**.
- Quantity: **one of each file (6 physical layers, 6 printable components)**.
- Every layer has the same 90 x 70 mm rounded outer outline, a continuous 4 mm
  carrier perimeter, and a 1.2 mm slot-interface thickness.
- Keep the broad face on the bed; do not add assembly Z offsets in the slicer.
- Suggested starting profile: PLA, 0.4 mm nozzle, 0.20 mm layers, 3 walls,
  4 top and 4 bottom layers, 15% infill. Use a brim only if your first-layer
  adhesion needs it; these are starting assumptions, not validated printer facts.
- Use one filament/material color per listed physical layer. Layers 2 and 5 in
  print order both use the same teal assignment.
- Tested software bed envelope: 220.0 x
  220.0 mm. The manifest print
  transforms are slicer placement metadata and do not change assembly geometry.

## Back-to-front physical order

1. `layers/layer_00_navy_backdrop/layer_00_navy_backdrop_body.stl` — navy / #19324D, base 1.2 mm, assembled Z=0.0 mm.
2. `layers/layer_01_teal_orbit/layer_01_teal_orbit_body.stl` — teal / #2A9D8F, base 1.2 mm, assembled Z=1.6 mm.
3. `layers/layer_02_gold_regions/layer_02_gold_regions_body.stl` — gold / #F4C542, base 1.2 mm, assembled Z=3.2 mm.
4. `layers/layer_03_coral_window/layer_03_coral_window_body.stl` — coral / #E76F51, base 1.2 mm, assembled Z=4.8 mm.
5. `layers/layer_04_teal_foreground/layer_04_teal_foreground_body.stl` — teal / #2A9D8F, base 1.2 mm, assembled Z=6.4 mm.
6. `layers/layer_05_cream_highlight_rim/layer_05_cream_highlight_rim_body.stl` — cream / #F4F1DE, base 1.2 mm, assembled Z=8.0 mm.

## Temporary registration test (no frame required)

1. Print the six files and let them cool flat before removal.
2. Align the identical 90 x 70 mm outer outlines in the common 100 x 80 mm model
   canvas. Low-tack tape or removable clips may hold the trial; a user-supplied
   slotted frame may be tested separately but is not included.
3. Stack in the exact order above, navy at the back and cream at the front.
   Thin 0.4 mm card shims may reproduce the proposed CAD-view gaps; also inspect
   the parts in direct contact because no spacer/housing is included.
4. Confirm the coral ellipse is a true open window and shows the gold center.
   Confirm every retained internal region stays connected to its outer perimeter.
5. Inspect all six common perimeters, the repeated teal depths, flatness, fragile
   points, first-layer quality, slot compatibility, and color/shadow appearance.
6. Photograph the front and side, record slicer-reported dimensions, and report
   PASS/FAIL plus any registration, occlusion, fragility, or color issues.

Do not permanently glue the feasibility stack. A frame, stand, slots, pegs, and
adhesive graph are deliberately outside L-1.
