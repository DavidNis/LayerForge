# Orbit Bloom fixture annotations

`Orbit Bloom` is an original abstract flat illustration authored specifically for
the LayerForge V3 L-1 engineering fixture. It uses no third-party artwork. The
fixture is dedicated under CC0-1.0 so it can remain in the repository.

The source composition and all six physical layer definitions were manually
designed and reviewed together. No segmentation, palette discovery, depth
inference, or automatic image-to-layer proposal is claimed. This controlled case
tests only whether a reviewed layer plan can reproducibly become real layer-sheet
CAD, exports, validation, and CAD-derived views.

Manual design annotations:

- The navy rounded backdrop establishes the 100 x 80 mm registered canvas.
- The back teal orbit and front teal wave intentionally repeat one filament color
  at two different assembled Z depths.
- The gold layer contains a central disc and a separate visible satellite. A
  deliberate 4 mm carrier web connects them into one physical sheet; the coral
  layer is intended to occlude most of the web.
- The coral layer is a connected annular sheet. Its inner ellipse is a genuine
  through-cutout intended to reveal the lower gold layer.
- The cream highlight is a connected top sheet with an optional layer-local
  raised edge: 1.2 mm base and 2.4 mm total edge height.
- The artwork uses a common model origin at lower-left. The PNG uses upper-left;
  Y is inverted exactly once during rasterization.
- Assembly gaps of 0.4 mm are proposed visualization/temporary-stack spacing,
  not a claim that the sheets maintain that gap without user-provided support.

Known controlled-fixture limitations:

- The source and reviewed plan share the same designer-authored vector geometry,
  so visual metrics demonstrate pipeline fidelity, not segmentation quality or
  generalization.
- Filament names are suggestions only. Exact printed color, translucency,
  shrinkage, adhesion, flatness, and shadow quality require user testing.
- No frame, box, stand, peg, or adhesive graph is supplied or required.
