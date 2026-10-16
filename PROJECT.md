# PROJECT: LayerForge V3

## North star

Turn a flat-colored illustration into an editable **ordered stack of printable single-color layers**, so viewing the real layered print from the front yields an attractive dimensional version of the illustration, comparable in *construction approach* to the user's reference shadow-box models.

**First priority: the layers themselves.** The user will handle a slotted box or frame separately if wanted. Do **not** implement a frame, holder, adhesive graph, stand, mount or support enclosure as a prerequisite for V3 acceptance.

## Desired user experience, eventual MVP

1. Import a clean illustrated PNG/JPEG (SVG optional later) with recognizable flat-color boundaries.
2. Propose a color palette and **layer stack**, including large cut-out sheets and nested silhouettes; not just lists of character anatomy.
3. Review and edit shape cutouts, ordering, color, layer thickness, rim height, common canvas and optional apertures.
4. Inspect each layer, side-on depth ordering, front composite and exploded stack; verify all are CAD-derived.
5. Export a named STL and STEP per print layer, plus separate subparts only where truly required; include layer stack manifest, simple assembly order and previews.
6. Print layers in independently selected single filament colors without a multi-material printer. One color may be used on multiple layers.

## Core aesthetic/functional requirements

- **Registered 2.5D sheets**: aligned X/Y composition across layers; their Z order/depth constructs shadows and selective occlusion.
- **Layer-first construction**: a physical layer can represent many disjoint image regions and holes at one depth. Its print geometry may include a ring/rim around the composition for alignment, if enabled; rim is not a separate decorative box.
- **Color/depth separation**: a repeated image color can appear on multiple layers; number of colors does not determine number of layers.
- **Physical viability**: slots/cutouts/fragile islands flagged, every exported printed mesh is valid; reassembly orientation explicit.
- **Visual fidelity**: approved composite matches source foreground silhouettes and important landmarks, with controlled occlusion and no invented geometry presented as fact.

## V1 scope

Flat illustrations with solid or nearly solid colors, reasonably clean boundaries and explicit size. Start with semi-automatic layer plan (user review or manually annotated reference allowed and disclosed). Then automate proposals and test on unseen illustrations.

## Excluded from V1 core

- Generic photo-to-3D, photogrammetry and hidden 3D reconstruction.
- Fully automatic one-click reliable conversion of every possible image.
- Automatically generated housing/frame/stand/peg/glue model.
- Automatically inferred physically correct distance between layers from a single illustration.
- Multi-color print inside one layer and mandatory AMS workflows.
- Full React UI before geometrical feasibility passes.

## Success definition

A tested flat illustration can be represented by a **true set of layered sheet geometries** exported separately, assembled virtually from the same meshes and compared against the original, with documented print orientation. A real print/stack trial is required for fabrication approval; manual review is mandatory for ambiguous cases.

## Non-negotiable truthfulness

Reference examples demonstrate **possible constructions**, not a universal recipe. Distinguish measured/reference metadata, likely interpretations and open assumptions. Do not relabel the previously built Lumi Bird object-part proof as successful layer-stack proof.
