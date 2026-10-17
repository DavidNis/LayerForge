# Automatic proposal print and stacking guide

Status: software-validated proposal; physical printing is not required for this development gate.

- Import every STL at 100% scale in millimetres, broad face on the bed.
- Print exactly one of each file (8 sheets total).
- Every sheet is 100 x 100 x 1.2 mm and has the same continuous 4 mm outer perimeter.
- Use one filament color per sheet, choosing the closest available match to its recorded hex color.
- Suggested starting point: PLA, 0.4 mm nozzle, 0.20 mm layer height, 3 walls,
  4 top/bottom layers, 15% infill. Slicer settings remain user/printer dependent.
- Stack by the common outer outline in this back-to-front order; assembly transforms are
  separate from print placement transforms. No frame, stand, or mounting system is included.

1. `layers/layer_00_fdf3d9/layer_00_fdf3d9_body.stl` — #FDF3D9, 1 copy, 1.2 mm thick.
2. `layers/layer_01_88bfbe/layer_01_88bfbe_body.stl` — #88BFBE, 1 copy, 1.2 mm thick.
3. `layers/layer_02_4f473f/layer_02_4f473f_body.stl` — #4F473F, 1 copy, 1.2 mm thick.
4. `layers/layer_03_638299/layer_03_638299_body.stl` — #638299, 1 copy, 1.2 mm thick.
5. `layers/layer_04_4e854e/layer_04_4e854e_body.stl` — #4E854E, 1 copy, 1.2 mm thick.
6. `layers/layer_05_f68426/layer_05_f68426_body.stl` — #F68426, 1 copy, 1.2 mm thick.
7. `layers/layer_06_9e6239/layer_06_9e6239_body.stl` — #9E6239, 1 copy, 1.2 mm thick.
8. `layers/layer_07_1b242f/layer_07_1b242f_body.stl` — #1B242F, 1 copy, 1.2 mm thick.

Review the slicer for narrow decorative branches reported by `reports/validation.json`.
Connector webs are intentionally part of the printable sheet and their visible artifacts are
quantified in `reports/evaluation.json`.
