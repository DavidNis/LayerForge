# START_LAYER_FEASIBILITY: fresh repo L-1 proof

---

Implement milestone **L-1 ONLY** as defined in `MILESTONES.md` and `specs/LAYER_FEASIBILITY_GATE.md`. Do not start an application UI or unrelated features.

The product is a set of **registered 2.5D cutout sheets**, NOT one STL per semantic character object and NOT a glued-object plaque. V1 target one filament color per printed sheet, color repetitions across depths allowed. No external housing is required.

Requirements:

1. Use a rights-cleared original flat illustration including >=4 layer sheets, two visible regions on one sheet, at least one cutout revealing lower color, and a repeated hue on different depths.
2. Support reviewed plan or manual masks for first proof and disclose exact manual annotation. Do not claim automatic image-to-layers recognition.
3. Add a strict versioned `LayerStack` schema with common canvas registration, positive/additive geometry, subtractive holes, layer ordering, color and thickness/rim parameters; distinguish print-space and assembly-space transforms.
4. Build real CAD extruded sheets, including optional thicker rim on one sheet, and validate intended topology and warning handling for disconnected regions.
5. Export actual STEP and STL per layer/subcomponent and reopen to check accuracy; do not rename formats.
6. Render individual sheet images, assembled front, side cross-section/depth and exploded layers **from the same real CAD solids**.
7. Generate a comparison between source and visible CAD front projection. Include per-color masks, occlusion errors, min feature and printer-fit warnings.
8. Create a reproducible CLI, structured manifest, report and print/stack instructions. Physical trial can use manual temporary stacking without a custom frame.
9. Run all newly created automated tests and checks. Do not claim legacy tests are available or weaken assertions to get green checks.
10. Update milestones/status/tasks and document verified results and unresolved assumptions.
11. STOP at `READY_FOR_USER_TEST` or `AWAITING_PHYSICAL_TEST`. Never self-approve or begin L0/L1/frontend.

List exact commands, test counts, artifacts and the L-G01..L-G10 verdict. The user will evaluate exported layers and physical print before approval.
