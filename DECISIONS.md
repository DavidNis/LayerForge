# DECISIONS: dated architectural decisions

## D-V3-001 | 2026-10-16 | Layer-first, not object-first

**Status:** ACCEPTED USER REQUIREMENT.

The primary product is a physically ordered stack of printable 2.5D sheets. Semantic source objects are optional aids in segmentation, not CAD output units. An object might span layers; one layer can contain several visible regions or cutouts.

## D-V3-002 | 2026-10-16 | No required frame or glue graph

**Status:** ACCEPTED USER REQUIREMENT.

The user will decide on frame/slots/stand independently. Only create the layers first. Registration, dimensions, printed integrity and print-space / assembly-space coordinates remain mandatory; frame/stand/adhesion machinery must not be a gate for L-1.

## D-V3-003 | 2026-10-16 | One filament color per physical layer for V1

**Status:** CHOSEN MVP CONSTRAINT, not universal fact.

The two more detailed `.3mf` references show layer-wise single-filament printing as their workflow. Same color may be repeated at different layer depths. Future expansion may support multi-material in one layer, but not now.

## D-V3-004 | 2026-10-16 | Mask and layer planning require review

**Status:** ACCEPTED ENGINEERING SAFEGUARD.

Segmentation can be automated to create proposals, but original 2D pixels do not determine a unique 3D stack or hidden geometry. Never present choices as proven historical construction of reference art.

## D-V3-005 | 2026-10-16 | Fresh implementation of minimal CAD proof

**Status:** PROJECT SETUP CHOICE.

This is a new repository with no V2 source code. Build only the tested CAD functionality needed for L-1. Historical prototype output is not evidence of V3 success.

## D-V3-006 | 2026-10-16 | Real layer-solid parity

**Status:** ACCEPTED ENGINEERING SAFEGUARD.

Every view is produced from the same layer solids as STEP and STL. An AI mockup or artist's render cannot pass geometric verification.

## Decisions requiring user confirmation, NOT ASSUMED

- **Q1:** Preferred final layer physical dimensions, and whether each layer must share exactly the same outer outline or just the same coordinate frame. V3 proof can use documented sample settings pending user preference.
- **Q2:** How should disconnected islands be handled by default: connected temporary carrier/web, separate subparts, or automatic failure requiring review?
- **Q3:** Should a layer rim be enabled by default or only when needed? The sample 3MF contains edge-height variation but that does not establish universal intended function.
- **Q4:** Desired allowable palette / max layer count for user's typical art. A layer count is not equivalent to a unique color count.
- **Q5:** Whether user wants all layers to fit one bed and be printed one at a time or supports multi-bed subdivision. Do not enforce without asking.

The coding agent should **not block the controlled L-1 experiment** on these choices. It should expose them as parameters, use transparent experiment-only defaults for L-1, and request clarification before fixing a final product policy.

## Decision log protocol

Append immutable decisions with: ID, date, question, chosen option, evidence vs assumption, impact, status. Do not silently replace historical entries or turn open questions into requirements.
