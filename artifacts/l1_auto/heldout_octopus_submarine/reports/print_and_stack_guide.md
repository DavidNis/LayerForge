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

1. `layers/layer_00_7ae0e4/layer_00_7ae0e4_body.stl` — #7AE0E4, 1 copy, 1.2 mm thick.
2. `layers/layer_01_9667bf/layer_01_9667bf_body.stl` — #9667BF, 1 copy, 1.2 mm thick.
3. `layers/layer_02_a14830/layer_02_a14830_body.stl` — #A14830, 1 copy, 1.2 mm thick.
4. `layers/layer_03_e78659/layer_03_e78659_body.stl` — #E78659, 1 copy, 1.2 mm thick.
5. `layers/layer_04_385d7b/layer_04_385d7b_body.stl` — #385D7B, 1 copy, 1.2 mm thick.
6. `layers/layer_05_44998c/layer_05_44998c_body.stl` — #44998C, 1 copy, 1.2 mm thick.
7. `layers/layer_06_f8d257/layer_06_f8d257_body.stl` — #F8D257, 1 copy, 1.2 mm thick.
8. `layers/layer_07_232a39/layer_07_232a39_body.stl` — #232A39, 1 copy, 1.2 mm thick.

Review the slicer for narrow decorative branches reported by `reports/validation.json`.
Connector webs are intentionally part of the printable sheet and their visible artifacts are
quantified in `reports/evaluation.json`.
