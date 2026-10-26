# L3 print-kit fixtures

## sailboat_512.png

- Rights: original, project-owned test artwork drawn programmatically for L3 (2026-10-26).
  No third-party source.
- SHA-256: `cca5159aa7f824a60c2a137b12acaed66131ebe28bfc8fcb44652b592f40e4e2`
- 512 x 512 px, exact 6-color palette (no anti-aliasing), so it plans directly with
  `stack-plan` / `core.plan_stack` at any scale.
- Purpose: negative features of known width for the FDM estimate. At scale 1
  (0.390625 mm/px, 200 mm artwork) the 1 px wave slit is 0.39 mm wide and the 2 px slit
  0.78 mm; the 3 px mast is 1.17 mm of material; portholes are 11 px (4.3 mm) circles.
- Generator (OpenCV, label image, palette below):

```python
SKY, SEA, HULL, SAIL, MAST, SUN = range(6)
PAL = np.array([[170, 215, 245], [30, 70, 140], [120, 70, 40], [245, 245, 235], [60, 40, 30], [250, 200, 50]], np.uint8)
L = np.full((512, 512), SKY, np.uint8)
L[340:] = SEA
cv2.circle(L, (420, 90), 40, SUN, -1)
cv2.fillPoly(L, [np.array([[150, 330], [370, 330], [330, 380], [190, 380]], np.int32)], HULL)
L[120:330, 255:258] = MAST
cv2.fillPoly(L, [np.array([[262, 130], [262, 320], [360, 320]], np.int32)], SAIL)
for x in (210, 250, 290):
    cv2.circle(L, (x, 352), 5, SKY, -1)
L[420:422, 60:200] = SKY
L[455:456, 260:460] = SKY
image = PAL[L]
```
