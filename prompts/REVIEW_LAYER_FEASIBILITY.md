# REVIEW_LAYER_FEASIBILITY: adversarial review prompt

---

Review the latest LayerForge V3 L-1 deliverables as a skeptical independent QA engineer. Read `AGENTS.md`, `PROJECT.md`, `specs/LAYER_MODEL.md`, `specs/LAYER_FEASIBILITY_GATE.md` and `TEST_PLAN.md`.

- Inspect all exported STEP/STL and their read-back logs, not just screenshots.
- Confirm it outputs planned physical **sheets with real holes and ordered occlusion**, not one STL per detected semantic object.
- Confirm test for two visual regions on one physical layer, repeated color at two depths, one upper window showing lower hue, optional raised rim, and explicit disconnected-island policy.
- Compare individual layer geometry, assembled/front/side/exploded views to source; prove meshes and previews have common CAD provenance.
- Check Z/XY registration, print-space transforms, actual slicer fit and integrity. Confirm missing box/stand/adhesive is not reported as a defect.
- Distinguish code assertions from real inspected artifacts and physical printing; provide PASS/FAIL/PENDING per L-G01..L-G10.
- Record all limitations and uncertain decisions. Do not claim automatic layer segmentation if masks were manually drawn.
- Identify the three most serious remaining risks before a second unseen input is tested.

End with a clear NO_GO / AWAITING_PHYSICAL_TEST / USER_APPROVAL_NEEDED summary. Do NOT approve yourself or proceed to future milestones.
