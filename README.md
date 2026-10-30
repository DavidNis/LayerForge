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

## L0 core: sheet-stack commands (Windows PowerShell, tested 2026-10-24)

From the repository root with the project venv:

```powershell
$env:PYTHONPATH='src'
# Plan, save and validate a stack from an exact flat-palette PNG (approved planner)
.\.venv\Scripts\python.exe -m layerforge_l1 stack-plan --target <target.png> --output <stack_dir> --tile-mm 2.0 --beam 8 --score colors --frame-hex "#FF00FF"
# Migrate a pre-L0 planner layers/ directory to a versioned stack
.\.venv\Scripts\python.exe -m layerforge_l1 stack-import --layers artifacts\l1_van_gogh_v1\planner_optB\per_cell\layers --target artifacts\l1_van_gogh_v1\flat_2048_framed_optB.png --frame-hex "#FF00FF" --output <stack_dir>
.\.venv\Scripts\python.exe -m layerforge_l1 stack-validate --stack <stack_dir> --report <stack_dir>\validation.json
.\.venv\Scripts\python.exe -m layerforge_l1 stack-export --stack <stack_dir> --output <cad_dir>
.\.venv\Scripts\python.exe -m layerforge_l1 stack-preview --stack <stack_dir> --cad <cad_dir> --output <preview_dir>
# Focused regression: accepted masks -> validate -> full-size STEP/STL -> read-back -> compare (no re-planning)
.\.venv\Scripts\python.exe -m layerforge_l1 stack-reference --name van_gogh_optB --from-accepted --output artifacts\l0_core_v1\reference_van_gogh_optB_from_accepted
# Full re-planning + comparison (memory-heavy; stopped by host low-memory protection on 2026-10-24)
.\.venv\Scripts\python.exe -m layerforge_l1 stack-reference --name van_gogh_optB --output artifacts\l0_core_v1\reference_van_gogh_optB
# L1: automatic proposal for a new flat-color PNG/JPEG (no manifest); exit 0 = proposal ready, 1 = needs user decision
.\.venv\Scripts\python.exe -m layerforge_l1 stack-propose --image <image> --output <dir>
# L2: review/edit a saved stack in a project directory (exit 0 = current draft valid, 1 = invalid draft, 2 = bad request)
.\.venv\Scripts\python.exe -m layerforge_l1 stack-edit init --from <dir>\candidates\<name>\stack --project <project>
.\.venv\Scripts\python.exe -m layerforge_l1 stack-edit inspect --project <project>
.\.venv\Scripts\python.exe -m layerforge_l1 stack-edit apply --project <project> rim sheet=<id> enabled=true width_mm=8 total_height_mm=3.2
.\.venv\Scripts\python.exe -m layerforge_l1 stack-edit apply --project <project> rim sheet=<id> enabled=false   # rims are optional
# Shape edits (cuts, holes, bridges, mm coordinates) are exact vector geometry by default and are
# extruded without rasterizing; add "precision": "raster" to an edit to snap it to the planner grid.
.\.venv\Scripts\python.exe -m layerforge_l1 stack-edit apply --project <project> --op-file <edit.json>  # JSON shapes: use a file in PowerShell 5.1
.\.venv\Scripts\python.exe -m layerforge_l1 stack-edit preview --project <project> --output <dir> --against previous
.\.venv\Scripts\python.exe -m layerforge_l1 stack-edit undo --project <project>     # or: revert --to <n>
.\.venv\Scripts\python.exe -m layerforge_l1 stack-edit verify --project <project>   # replay the log from the base
.\.venv\Scripts\python.exe -m layerforge_l1 stack-edit export --project <project> --output <cad_dir>  # refused for invalid drafts
# L3: print kit (exports, reopened-CAD checks, comparisons, FDM estimate, guide, checksums) from a stack or review project
# exit 0 = CAD valid and no UNSUPPORTED finding; 1 = CAD invalid or UNSUPPORTED findings; 2 = refused/invalid input
.\.venv\Scripts\python.exe -m layerforge_l1 stack-kit --stack <stack_or_project> --output <new_kit_dir> --process fixtures\print_processes\generic_fdm_0.4mm.json
# L3 evaluation across artworks (private cases are skipped when absent)
.\.venv\Scripts\python.exe scripts\run_l3_print_kit_evaluation.py --output artifacts\l3_print_kit_v1
# Complete suite (no test depends on deleted artifact trees; see tests\REMOVED_TESTS.md)
.\.venv\Scripts\python.exe -m pytest
```

Exit codes: 0 PASS, 1 a validation/regression check failed, 2 invalid input.

## L4 application (local web UI, tested 2026-10-26)

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m layerforge_l1 app            # opens http://127.0.0.1:8765/ in the default browser
.\.venv\Scripts\python.exe -m layerforge_l1 app --no-browser --port 8765 --workspace <folder>
```

No extra dependencies: the server is Python's standard library and the page is plain
HTML/CSS/JavaScript served from `src/layerforge_l1/app/static/` (no build step). It listens on
127.0.0.1 only. Projects are saved in `./workspace/` (git-ignored; uploaded artwork stays local)
after every step and reopen after a restart. Workflow: **1** import a flat-color PNG/JPEG
(L1 proposal) → **2** review and edit (L2 operations, optional rim, undo/revert, verify
save/reload) → **3** FDM printability estimate → **4** print kit (L3) with zip download. The
status strip keeps geometry/CAD, the FDM estimate and the physical print (always NOT VERIFIED)
separate. Stop the server with Ctrl+C. Browser test of the UI: `tests/test_l4_app.py`
(needs Node >= 22 and Edge or Chrome; otherwise that one test is skipped with the reason).
### Portable Windows app (no Python needed; built 2026-10-26, test status in STATUS.md)

Build (developer machine, project venv): `.\.venv\Scripts\python.exe -m pip install -e ".[build]"`, then
`scripts\build_windows.ps1` (or the PyInstaller command inside it). Output: `dist\LayerForge\`
(`LayerForge.exe` + `_internal\`, about 660 MB) and `dist\LayerForge-0.1.0-win64.zip` (about 215 MB).

Use: unzip anywhere, double-click `LayerForge.exe`. A small LayerForge window opens and the app
opens in the default browser; closing the window stops it. Projects go to `Documents\LayerForge`
(`--workspace <folder>` to change), the log to `%LOCALAPPDATA%\LayerForge\logs\layerforge.log`;
nothing is written into the application folder. An occupied port moves to the next free one; a
second launch reopens the browser on the running app. `packaging/README_FIRST.txt` (copied next to
the exe) is the user-facing version. Tests: `tests/test_l4_launcher.py` (no build needed) and
`tests/test_windows_build.py` (runs the built exe; skipped with the reason when it is missing).
API and module map: `ARCHITECTURE.md`, section "L0 reusable geometry core".
The van_gogh reference is third-party character art: private use only.
