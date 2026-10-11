#!/usr/bin/env python3
"""Brodie's avatar frames (web/assets/3d/brodie) composited on the kit's own reference art, data/tmp/kit/reference/brodie-flex.png,
so the body is on-model by construction (kit rule: body #EFE7DA, never recoloured). The costume is the one head slot the kit allows:
black over-ear headphones (ink band hugging the top of the head below the tuft, ink ear cups with a cream disc at eye height).
Frames (verity/avatar.py sprites / stage_sprites; verity/models.py STAGES):
  idle_00/01   headphones + ONE small closed smile (01 bobs 3 px)      talk_00   headphones + the canon grin with teeth
  mad_*        bar brows slanted down toward the middle               fury_*    slanted harder + tan (#C2A882) speed marks beside the head
  cruelty_*    the drill-sergeant final form: fury with a second pair of marks; the body stays cream (never red)
Also face.png / face-talk.png, the web app's 192 px square head thumbnails (web/index.html ours()).
  python scripts/brodie_sprites.py          then  python data/tmp/kit/tools/check_cream.py web/assets/3d/brodie/*.png
"""
import os, sys
import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'data', 'tmp', 'kit', 'reference', 'brodie-flex.png')
OUT = os.path.join(ROOT, 'web', 'assets', '3d', 'brodie')
W, H = 360, 640                                     # verity/avatar.py frame
CREAM, INK, TAN = (239, 231, 218, 255), (26, 25, 25, 255), (194, 168, 130, 255)   # brodie.json colours
AA = cv2.LINE_AA

# measured on brodie-flex.png (1610 x 1645): the head's outer outline is the ellipse below; eyes at y ~415; face centre x ~839
HEAD_C, HEAD_AX = (807, 505), (417, 395)
EYE_Y = 415
BROWS = {'left': ((658, 300), (746, 324)), 'right': ((1017, 290), (934, 318))}   # (outer end, inner end) of the canon bars, 47 thick
BROW_BOXES = ((633, 768, 280, 348), (913, 1040, 271, 343))                         # ink bboxes of the canon brows (x0, x1, y0, y1)
GRIN_BOX = (677, 1003, 502, 649)                                                   # ink outline blob of the grin, teeth inside


def load():
    im = cv2.imread(SRC, cv2.IMREAD_UNCHANGED)
    assert im is not None and im.shape[2] == 4, SRC
    return cv2.cvtColor(im, cv2.COLOR_BGRA2RGBA)


def over(dst, src, mask):
    """Alpha-composite src onto dst where mask (uint8/bool) is set."""
    m = mask.astype(bool)
    a = src[m, 3:4].astype(np.float32) / 255
    dst[m, :3] = (src[m, :3] * a + dst[m, :3] * (1 - a)).astype(np.uint8)
    dst[m, 3] = np.maximum(dst[m, 3], src[m, 3])


def ink_mask(im, box, pad=0):
    x0, x1, y0, y1 = box
    m = np.zeros(im.shape[:2], np.uint8)
    sub = im[y0:y1 + 1, x0:x1 + 1]
    dark = (sub[..., 3] > 250) & (sub[..., :3].max(axis=2) < 90)
    m[y0:y1 + 1, x0:x1 + 1] = dark.astype(np.uint8) * 255
    if pad: m = cv2.dilate(m, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8))
    return m


def paint_cream(im, mask):
    im[mask > 0] = CREAM


def ellipse_pt(deg, grow=0):
    """A point on the head outline (grown by grow px) at cv2's ellipse angle deg."""
    t = np.radians(deg)
    return (HEAD_C[0] + (HEAD_AX[0] + grow) * np.cos(t), HEAD_C[1] + (HEAD_AX[1] + grow) * np.sin(t))


def headphones(im):
    """Ink band over the top of the head (between the cups, under the tuft) + ink ear cups with a cream disc at eye height."""
    # ear cups sit on the head's edge at eye height, inside the gap between the head and the flexed arms (measured: x 340-397 free)
    dy = (EYE_Y - HEAD_C[1]) / HEAD_AX[1]
    half = HEAD_AX[0] * np.sqrt(1 - dy * dy)
    cups = [(int(round(HEAD_C[0] - half + 6)), EYE_Y), (int(round(HEAD_C[0] + half - 6)), EYE_Y)]
    # the band: a thick ink arc on the head's outline from cup to cup, over the top
    band = np.zeros_like(im)
    ang = [np.degrees(np.arctan2((cy - HEAD_C[1]) / (HEAD_AX[1] - 8), (cx - HEAD_C[0]) / (HEAD_AX[0] - 8))) % 360 for cx, cy in cups]
    cv2.ellipse(band, HEAD_C, (HEAD_AX[0] - 8, HEAD_AX[1] - 8), 0, ang[0], ang[1], INK, 64, AA)
    tuft = np.zeros(im.shape[:2], np.uint8)                     # the three-lobe tuft (art above the head outline) peeks out over it
    cv2.ellipse(tuft, HEAD_C, (HEAD_AX[0] + 1, HEAD_AX[1] + 1), 0, 0, 360, 255, -1)
    tuft = (im[..., 3] > 0) & (tuft == 0); tuft[:, :660] = False; tuft[:, 980:] = False; tuft[230:] = False
    orig = im.copy()
    over(im, band, band[..., 3] > 0)
    over(im, orig, tuft)
    for cx, cy in cups:
        cv2.ellipse(im, (cx, cy), (56, 86), 0, 0, 360, INK, -1, AA)
        cv2.ellipse(im, (cx, cy), (28, 54), 0, 0, 360, CREAM, -1, AA)
    return im


def closed_smile(im):
    """Replace the grin (ink outline + teeth) with ONE small closed smile: the kit's other allowed mouth."""
    x0, x1, y0, y1 = GRIN_BOX
    sub = im[y0:y1 + 1, x0:x1 + 1]
    m = np.zeros(im.shape[:2], np.uint8)
    m[y0:y1 + 1, x0:x1 + 1] = (((sub[..., :3].max(axis=2) < 90) | (sub[..., :3].min(axis=2) > 240)) & (sub[..., 3] > 250)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(m, cnts, -1, 255, -1)
    paint_cream(im, cv2.dilate(m, np.ones((11, 11), np.uint8)))
    cv2.ellipse(im, (839, 556), (104, 64), 0, 22, 158, INK, 30, AA)
    return im


def brows(im, slant, drop=0):
    """Paint the canon bar brows cream and redraw them slanted down toward the middle (degrees, pivot = outer end)."""
    for box in BROW_BOXES: paint_cream(im, ink_mask(im, box, pad=5))
    for side, (outer, inner) in BROWS.items():
        sx = 1 if side == 'left' else -1
        ox, oy = outer[0], outer[1] + drop
        t = np.radians(slant)
        ix, iy = ox + sx * 91 * np.cos(t), oy + 91 * np.sin(t)
        cv2.line(im, (int(round(ox)), int(round(oy))), (int(round(ix)), int(round(iy))), INK, 47, AA)
    return im


def speed_marks(im, pairs=1):
    """Short tan speed marks beside the head (outside the silhouette, upper left and right), ink-outlined like every kit shape."""
    for k in range(pairs):
        for deg in (226 - 14 * k, 314 + 14 * k):
            p0, p1 = ellipse_pt(deg, 34), ellipse_pt(deg, 150)
            p0, p1 = tuple(int(round(v)) for v in p0), tuple(int(round(v)) for v in p1)
            cv2.line(im, p0, p1, INK, 40, AA)
            cv2.line(im, p0, p1, TAN, 20, AA)
    return im


def frame(im, bob=0):
    """Fit the figure into the W x H portrait frame (transparent background, centred; the flexed arms make it width-bound)."""
    ys, xs = np.nonzero(im[..., 3] > 0)
    crop = im[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    k = min(0.96 * W / crop.shape[1], 0.92 * H / crop.shape[0])
    small = cv2.resize(crop, (round(crop.shape[1] * k), round(crop.shape[0] * k)), interpolation=cv2.INTER_AREA)
    out = np.zeros((H, W, 4), np.uint8)
    ox, oy = (W - small.shape[1]) // 2, (H - small.shape[0]) // 2 + bob
    out[oy:oy + small.shape[0], ox:ox + small.shape[1]] = small
    return out


def thumb(im, size=192):
    """Square head thumbnail for the web app (the head and its headphones)."""
    cx, cy, half = 807, 450, 580
    pad = np.pad(im, ((half, half), (half, half), (0, 0)))
    sq = pad[cy:cy + 2 * half, cx:cx + 2 * half]
    return cv2.resize(sq, (size, size), interpolation=cv2.INTER_AREA)


def save(name, img):
    cv2.imwrite(os.path.join(OUT, name), cv2.cvtColor(img, cv2.COLOR_RGBA2BGRA), [cv2.IMWRITE_PNG_COMPRESSION, 9])


def main():
    os.makedirs(OUT, exist_ok=True)
    base = headphones(load())
    talk, idle = base, closed_smile(base.copy())
    looks = {'idle_00': (idle, 0), 'idle_01': (idle, 3), 'talk_00': (talk, 0)}
    for mouth, img in (('idle', idle), ('talk', talk)):
        looks[f'mad_{mouth}_00'] = (brows(img.copy(), 29, drop=-4), 0)
        looks[f'fury_{mouth}_00'] = (speed_marks(brows(img.copy(), 41, drop=-2)), 0)
        looks[f'cruelty_{mouth}_00'] = (speed_marks(brows(img.copy(), 41, drop=-2), pairs=2), 0)
    for name, (img, bob) in looks.items(): save(f'{name}.png', frame(img, bob))
    save('face.png', thumb(idle)); save('face-talk.png', thumb(talk))
    if '--full' in sys.argv:                                    # full-resolution previews for a closer look
        os.makedirs(os.path.join(ROOT, 'data', 'tmp', 'brodie_full'), exist_ok=True)
        for name, (img, _) in looks.items():
            cv2.imwrite(os.path.join(ROOT, 'data', 'tmp', 'brodie_full', f'{name}.png'), cv2.cvtColor(img, cv2.COLOR_RGBA2BGRA))
    print('wrote', len(looks) + 2, 'images to', OUT)


if __name__ == '__main__':
    main()
