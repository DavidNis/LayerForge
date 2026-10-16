# AGENTS.md: binding rules for the LayerForge coding agent

You are working on **LayerForge V3**, a flat-illustration-to-printable-**layer-sheets** project. The user supplied actual layered character-art examples. This is a **fresh repository** and no previous application code is present. An earlier prototype in a different repository is historical context, not a requirement or existing component.

## Mandatory reading order for every work session

1. `README.md`, `PROJECT.md`, `REQUIREMENTS.md`.
2. `specs/REFERENCE_EVIDENCE.md`, `specs/LAYER_MODEL.md`, `specs/IMAGE_INPUT_CONTRACT.md`.
3. `MILESTONES.md`, `STATUS.md`, `TASKS.md`.
4. `ARCHITECTURE.md`, `specs/LAYER_GEOMETRY_RULES.md`, `specs/EXPORT_AND_PREVIEW.md`, `specs/LAYER_FEASIBILITY_GATE.md`.
5. `TEST_PLAN.md`, `DECISIONS.md`, and current `prompts/*.md` instruction.

## Absolute product rules

- **Prioritize generating the sheets/layers themselves.** Frame, housing, slotted holder, stand, glued object parts, pegs and automatic assembly attachment graphs are **not part of current core goals**.
- A *layer* is an ordered physical sheet in the depth stack, containing multiple source regions, holes, silhouette outlines and optional rim/web. A semantic image object is NOT automatically a print layer.
- V1 design choice: one **filament/material color per printed physical layer**. Color can repeat on multiple layers. Do not claim this is a universal artistic fact.
- Maintain common XY registration, explicit back-to-front assembled Z, and **separate print plate transforms**.
- Recognize that connected components may be multiple within a layer. Resolve deliberately as one connected sheet with approved carrier/rim/web or multiple named export components; flag unsupported/floating islands.
- Per-layer STEP/STL, layer previews and assembled/exploded visualization must derive from the **same actual CAD solids**. No "fake assembled" or generative pretty mockups to satisfy testing.
- Geometry is layered Boolean masks and user-reviewed ordering: `union(additive) minus union(cutouts)`, then physical extrusions/rim variants. Cutouts must remain true holes in the CAD.
- The app must eventually derive candidates from new illustrations. **For L-1 manual reviewed layer plans are allowed but must be disclosed**, never described as automated. Tests on a second unseen source required before claims of generalization.

## Honest evidence and uncertainty

- Distinguish `OBSERVED_IN_FILE`, `USER_REQUIRED`, `DESIGN_CHOICE`, `UNVERIFIED_INFERENCE`, `OPEN_QUESTION` in decisions/reports when useful.
- Do not infer how a model's sheets physically mount from a thumbnail/3MF filename unless geometry or instructions prove it.
- There is no V2 code in this fresh repository. Build a minimal tested CAD/STEP/STL/mesh validation path for layer sheets. Historical V2 results do not satisfy V3 layer-specific quality gates.
- Do not lower test thresholds, suppress failures, skip checks without explanations, overwrite user files or silently discard drawings.
- Do not commit proprietary user/reference artwork to a public repo without a verified license. Use original test illustrations.

## Development discipline

**CURRENT MILESTONE: L-1 FEASIBILITY ONLY in this fresh repo.** The agent may create the smallest reproducible Python CLI/CAD proof. Do not scaffold FastAPI, React, a full frontend, AI, or later milestones. Stop with evidence and wait for user approval and physical printing.

1. Confirm the required docs are present, inspect the new repository/platform, check actual dependencies and record provenance of new fixtures.
2. State current task, acceptance target, known uncertainty and narrow plan.
3. Do only authorized work; run real commands, inspect geometry and preview artifacts.
4. Report exact test counts and failures. Images are additional evidence, not verification by themselves.
5. Update `STATUS.md`, `TASKS.md`, `MILESTONES.md`, append verified architecture decisions to `DECISIONS.md`.
6. Stop at `READY_FOR_USER_TEST` / `AWAITING_PHYSICAL_TEST` and ask for explicit user approval.

## Required end-of-session report

```text
Milestone: L-1
Status:
Git revision/branch and files changed:
Fresh implementation modules built / library dependencies used:
Source fixture rights/provenance:
What was manual vs automatic:
Number of planned physical layers vs exported components:
Actual commands run, test PASS/FAIL/SKIP:
CAD read-back / holes / occlusion / print bounds results:
Source projection and layer-view evidence:
User decisions still needed:
Physical test: PASS/FAIL/PENDING:
Gate result / blocker:
Proceed to next milestone? NO until explicit user approval
```

## Shortcuts forbidden

- No conversion of every labeled character object to its own separated print part while calling it a layered-shadow-box solution.
- No mandatory backing/glue/rooted-attachment success criterion.
- No shared rim/frame imposed without a parameter and design justification.
- No hard-coded result only usable for one developer-designed image disguised as general converter.
- No claimed STEP support for STL-only meshes unless true STEP CAD solids are generated.
- No invented automatic disassembly of a photographed sample from image alone.
- No automatic step progression or unapproved major redesign.
