# START_FRESH_PROJECT: first coding-agent prompt (NEW REPOSITORY)

Copy the instruction below into your coding agent after opening the repository folder containing `AGENTS.md`.

---

We are starting **LayerForge V3 in a completely NEW repository**. There is NO V2 implementation to migrate. Do not run any V2 migration audit, look for legacy assets, or assume existing Python code/tests.

Read `AGENTS.md`, `README.md`, all other top-level `.md` files and **all six files** under `specs/`, particularly `specs/REFERENCE_EVIDENCE.md`, `specs/LAYER_MODEL.md`, `specs/LAYER_GEOMETRY_RULES.md`, `specs/EXPORT_AND_PREVIEW.md` and `specs/LAYER_FEASIBILITY_GATE.md`. Verify and list their exact paths BEFORE beginning work. If any file is missing, STOP and list it; don't invent substitutes or silently continue.

**Implement L-1 FEASIBILITY ONLY.** The product is ordered, separate **2.5D print layer sheets**, NOT one printed piece per semantic cartoon object. The user will handle a slotted frame/stand separately; no box or adhesive graph is needed.

1. Inspect OS, available Python version (prefer 3.12), actual CadQuery/OCCT, Shapely, numpy, OpenCV, mesh and plotting dependencies. Provide reproducible, pinned environment instructions.
2. Outline a narrow testable L-1 implementation; create an original rights-cleared colorful flat test illustration with reviewed 4–8 physical sheet layers, an upper cutout revealing a lower color, multi-region sheet, repeat color at two depths and an optional raised rim.
3. Store a versioned `LayerStack` source manifest with exact XY alignment, additive/subtractive regions, single color per physical layer, thickness/height/order and **separate print vs assembly transforms**. Do not confuse objects/color regions with physical sheets.
4. Generate actual solids from Boolean sheet geometry via CadQuery; preserve holes and deliberate sheet connectivity, detect disconnected islands and report unresolved cases.
5. Export true STEP and watertight STL per physical layer or explicitly named printable component. Reopen/check meshes and STEP, bounds/volume, cutouts, printer fit, thin features, intended overlaps, and exact quantity.
6. From the **same exported CAD-derived geometry**, create each layer preview, ordered front assembled view, depth/side view, exploded **layers** view and source-comparison overlay. Record all visible mismatch, including missing occlusion detail.
7. Build a minimal CLI and actual automated tests; record exact execution and PASS/FAIL/SKIP results plus all hand-authored annotations. Produce the artifact tree defined in `specs/EXPORT_AND_PREVIEW.md`.
8. Report every L-G01..L-G10 feasibility criterion as PASS, FAIL, or PENDING; prepare precise user slicer and temporary stacking checks.
9. Update `STATUS.md`, `TASKS.md`, `MILESTONES.md`, and `DECISIONS.md` with observed results rather than unsupported assumptions.
10. **STOP** at `READY_FOR_USER_TEST` or `AWAITING_PHYSICAL_TEST`. Don't mark yourself `APPROVED`; do not start L0, L1 auto-proposals, React/FastAPI, AI, or any other milestone until the user has tested and approved the physical layers.

Important limitation: manually reviewed layer definitions can demonstrate CAD/printing feasibility but **do not prove automatic image-to-layer generation**. L1 later must separately evaluate candidate-generation on genuinely new flat images. Be explicit about this limitation.

Final response must give the source fixture, layer-by-layer output paths, exact print instructions, CAD read-back results, preview and source comparisons, test log, pending unknowns and a request for user review.
