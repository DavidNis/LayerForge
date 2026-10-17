# LayerForge V3 — fresh project start

**This documentation pack is for a BRAND NEW repository. There is no existing V2 code to migrate.**

Goal: convert clean, flat-color 2D illustrations into **real, separately printable, ordered 2.5D layer sheets** that recreate the approved artwork when aligned front-to-back. The print layers are the product. A decorative box, slotted frame, stand, glue graph or automatic housing is **out of scope** for the core work.

## Start here (Windows / VS Code)

1. Create a **new empty folder** named `LayerForge`. If you already have a Git repository, create a new folder/repository or a clearly separate branch, not a destructive overwrite of work you may need.
2. Extract the ZIP. The archive has **one `LayerForge/` folder** containing all required `.md` files plus `specs/` and `prompts/`. Open **that folder**, the one containing `AGENTS.md`, as the VS Code workspace.
3. Verify the exact mandatory files exist with the command below from the VS Code PowerShell terminal.
4. Start your coding agent **with `prompts/START_FRESH_PROJECT.md`** (paste the file's instruction into the agent). Do **not** use an old `MIGRATE_V2_TO_V3.md` prompt.
5. The agent implements **L-1 feasibility only**, tests the actual CAD geometry/exports, and stops for your review and real print test. No React, FastAPI, full UI or AI training yet.

PowerShell verification, run from the folder containing `AGENTS.md`:

```powershell
$required = @(
 'AGENTS.md','PROJECT.md','REQUIREMENTS.md','ARCHITECTURE.md','MILESTONES.md',
 'STATUS.md','TASKS.md','DECISIONS.md','TEST_PLAN.md','README.md',
 'specs/REFERENCE_EVIDENCE.md','specs/LAYER_MODEL.md',
 'specs/LAYER_GEOMETRY_RULES.md','specs/EXPORT_AND_PREVIEW.md',
 'specs/LAYER_FEASIBILITY_GATE.md','specs/IMAGE_INPUT_CONTRACT.md',
 'prompts/START_FRESH_PROJECT.md','prompts/START_LAYER_FEASIBILITY.md'
)
$missing = $required | Where-Object { -not (Test-Path $_ -PathType Leaf) }
if ($missing.Count) { Write-Host 'MISSING FILES:'; $missing; exit 1 }
Write-Host 'OK: all required files exist.'
```

## Read these in order

- `AGENTS.md`: persistent instructions and guards.
- `PROJECT.md` + `REQUIREMENTS.md`: desired outcome and scope.
- `specs/REFERENCE_EVIDENCE.md`: what was actually established from Pikachu, Girl with Pearl Earring and Umbreon references; distinguish evidence from inference.
- `specs/LAYER_MODEL.md` and `specs/LAYER_GEOMETRY_RULES.md`: model and Boolean layer geometry contracts.
- `specs/EXPORT_AND_PREVIEW.md` and `specs/LAYER_FEASIBILITY_GATE.md`: required artifacts and the go/no-go gate.
- `MILESTONES.md`: L-1 proof first, later auto-proposal and editing.

## Reproduce the current L-1 feasibility build

From the repository root in PowerShell, using Windows Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m layerforge_l1 build
.\.venv\Scripts\python.exe -m pytest --junitxml=artifacts\layer_feasibility\orbit_bloom_v1\reports\pytest.xml
.\.venv\Scripts\python.exe -m layerforge_l1 build
.\.venv\Scripts\python.exe -m layerforge_l1 verify
```

The second build incorporates the test XML into the validation report and
artifact checksums. Current software status is `AWAITING_PHYSICAL_TEST`; it is
not user-approved and does not demonstrate automatic image-to-layer generation.

## Truth and boundaries

- These files specify the project but **do not contain a working app or completed CAD engine**.
- L-1 may use manually reviewed masks / layers, but must state that clearly. That proves CAD layer generation, **not** automatic creation from previously unseen images. Later L1 specifically validates automatic layer proposals on new images.
- One filament color per printed physical layer is a V1 design choice based on the reviewed workflows. Colors can repeat at different depths. A layer can have several regions and genuine cutout holes.
- A physical layer is **not** a semantic object (head, arm, etc.) and **not** a printer Z-slice or an individual Bambu Studio build plate.
- CAD geometry is the single source for STEP/STL, assembled/exploded views and validation. No AI image mockups count as layer evidence.
- Empty PDF/render/preview output, passing unrelated tests or inventing an attractive mockup is not proof. Real exported solids and a printed stack are the final tests.

## Suggested tree

```text
LayerForge/
  AGENTS.md
  PROJECT.md
  REQUIREMENTS.md
  ARCHITECTURE.md
  MILESTONES.md
  STATUS.md
  TASKS.md
  DECISIONS.md
  TEST_PLAN.md
  README.md
  CHANGELOG.md
  specs/
    REFERENCE_EVIDENCE.md
    IMAGE_INPUT_CONTRACT.md
    LAYER_MODEL.md
    LAYER_GEOMETRY_RULES.md
    EXPORT_AND_PREVIEW.md
    LAYER_FEASIBILITY_GATE.md
  prompts/
    START_FRESH_PROJECT.md
    START_LAYER_FEASIBILITY.md
    REVIEW_LAYER_FEASIBILITY.md
    NEXT_MILESTONE.md
```
