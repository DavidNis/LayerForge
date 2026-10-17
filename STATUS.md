# STATUS: current project truth (fresh repository)

- **Spec version:** LayerForge V3, flat-illustration -> physical layer sheets.
- **Repository type:** NEW from scratch; no V2 source code to migrate.
- **Current milestone:** `L-1` feasibility.
- **State:** `AWAITING_PHYSICAL_TEST` after software feasibility checks.
- **User-approved V3 layer proof:** NO.
- **Software results:** L-G01 through L-G09 PASS on the original controlled
  `orbit_bloom_v1` fixture. L-G10 is PENDING; no physical result exists yet.

## Verified project direction

- V1 takes flat-color illustrated artwork and produces real printable sheets ordered by depth, not semantic object parts.
- V1 one filament color per physical sheet; same color can appear on multiple depth layers.
- Sheet geometry includes cutouts, optional raised rims/webs, deliberate island policy, and common XY alignment.
- Housing, slotted frame, glue graph, peg, mount and stand are outside current work.
- L-1 can use a manually reviewed layer plan; automatic interpretation of new images is a later, separately gated milestone L1.

## Agent must update after each session

```text
Date: 2026-10-16
Git branch/revision: main, starting revision 73d81a36e0e5e9e554512f7aa097de1050614838
Current milestone / state: L-1 / AWAITING_PHYSICAL_TEST
Source fixture provenance: original project-authored Orbit Bloom; CC0-1.0; no third-party assets
Actual source/dependencies built: minimal Python CLI, Boolean geometry/CAD exporter,
  read-back validator, STL-derived previews and source comparison; pinned versions in requirements-lock.txt
Manual vs automated work: source vectors and six-layer plan manual/reviewed; CAD Boolean
  construction, STEP/STL export, read-back, previews, metrics and checks automated
Exact commands and tests: artifacts/layer_feasibility/orbit_bloom_v1/reports/commands.log;
  12 PASS, 0 FAIL, 0 ERROR, 0 SKIP; 29 dependency deprecation warnings
Generated outputs and file paths: artifacts/layer_feasibility/orbit_bloom_v1/
CAD STEP/STL validation and preview parity: 6 planned layers / 6 measured components;
  all STEP solids valid; all STL watertight; one coral through-hole preserved;
  previews use reopened STL projections; silhouette IoU 0.995033 at 500x400
Failures / blockers: no software gate blocker; physical print, color, flatness, shadows,
  temporary registration and user aesthetic approval remain unverified
User print/appearance approval status: PENDING / NO sign-off
Next precise step: user imports the six STLs at 100% mm scale, prints and temporarily
  stacks them per reports/print_and_stack_guide.md, then reports L-G10 evidence
```

Do not mark a milestone approved without user approval. Do not report unrun checks as PASS.
