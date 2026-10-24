"""Copy/downscale OptB evidence images for the PDF (read-only on artifacts)."""
from pathlib import Path
import numpy as np, cv2
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / "artifacts" / "l1_van_gogh_v1"
F = Path(__file__).resolve().parent / "fig"
F.mkdir(exist_ok=True)

def fit(img, h):
    w = round(img.width * h / img.height)
    return img.resize((w, h), Image.LANCZOS)

fit(Image.open(ROOT / "van_gogh_p.jfif").convert("RGB"), 640).save(F / "orig.jpg", quality=88)
fit(Image.open(A / "flat_2048_framed_optB.png").convert("RGB"), 640).save(F / "flat_optB.png")
for name in ("optA_cheek_before_change_after.png", "optB_hat_before_after.png"):
    im = Image.open(A / name).convert("RGB")
    if im.width > 1400: im = fit(im, round(im.height * 1400 / im.width))
    im.save(F / name)
L = A / "planner_optB" / "per_cell" / "layers"
for d in sorted(L.iterdir()):
    fit(Image.open(d / "physical_sheet_preview.png").convert("RGB"), 260).save(F / f"{d.name}.png")
for src, dst in ((A / "planner_optB/per_cell/mask_derived_composite.png", "composite.png"),
                 (A / "cad_optB/stack_from_cad.png", "stack_from_cad.png"),
                 (A / "planner_optB/per_cell/visible_depth_map.png", "depth.png")):
    fit(Image.open(src).convert("RGB"), 520).save(F / dst)
# material anatomy of one sheet: visible / hidden retained / hole
d = L / "layer_05_3d7033"
m = np.asarray(Image.open(d / "retained_material_mask.png")) > 0
v = np.asarray(Image.open(d / "source_visible_region_mask.png")) > 0
im = np.full(m.shape + (3,), 255, np.uint8)
im[m] = (175, 175, 175); im[v] = (0x3D, 0x70, 0x33)
fit(Image.fromarray(im), 520).save(F / "anatomy_layer05.png")
y0, x0, s = 1450, 1450, 600
Image.fromarray(im[y0:y0 + s, x0:x0 + s]).resize((500, 500), Image.NEAREST).save(F / "anatomy_layer05_zoom.png")
print(sorted(p.name for p in F.iterdir()))
# the two black sheets: depth 0 (black) vs depth 5 (red)
a = np.asarray(Image.open(L / "layer_09_000100" / "source_visible_region_mask.png")) > 0
b = np.asarray(Image.open(L / "layer_04_000100" / "source_visible_region_mask.png")) > 0
im = np.full(a.shape + (3,), 255, np.uint8); im[a] = (0, 0, 0); im[b] = (230, 30, 30)
Image.fromarray(im).resize((576, 576), Image.LANCZOS).save(F / "two_blacks.png")
