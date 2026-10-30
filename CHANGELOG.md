# CHANGELOG

## V3 spec reset — 2026-10-16

- Replaced mandatory object/part/backing/adhesive pipeline with **layer-sheet-first** workflow.
- Established target output as registered 2.5D cut-out sheets, each with one material/color by default in V1.
- Explicitly removed frame, housing, stand and glue graph from core feasibility scope.
- Separated source visual regions, design layers, printable connected components, assembly transform and slicer print plates.
- Added verified-versus-inferred reference matrix and explicit unanswered user design questions.
- Introduced L-0 repository migration audit and L-1 layer feasibility as new mandatory gates.
- Preserved legacy V2 M-1 historical status without mistaking its object-based result for V3 success.
- Replaced prior workflow prompts with migration, layer experiment and review prompts.

**This release provides documentation only. No app changes or successful V3 CAD layer print tests are claimed.**

## 2026-10-16 | V3 clean-start packaging

- Produced a new-repository edition of the V3 docs with L-1 as the first milestone.
- Removed V2 migration prompt and incompatible L-0 audit startup workflow.
- Verified the complete six-spec package and documented precise PowerShell checks.
- Unchanged core product scope: printable single-color physical 2.5D layers; not semantic objects; housing out of scope.
- No runnable CAD implementation or L-1 validation is included in this documentation package.
