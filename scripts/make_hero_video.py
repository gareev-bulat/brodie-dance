"""The landing page's hero loop: a short, muted, small clip of the lesson's dance and its poster image.

    python scripts/make_hero_video.py [--src data/dances/sbbyj.mp4] [--start 0.5] [--seconds 6] [--crop 0.17,0.08,1,0.91] [--width 216] [--fps 10]

Writes web/assets/hero/dance.webm (VP9, no audio) and web/assets/hero/dance.jpg (the poster, also the still shown with
reduced motion). --crop x0,y0,x1,y1 (fractions of the frame) cuts away burned-in marks: sbbyj.mp4 is a TikTok download whose
watermark sits on the left edge until 6.6 s (then the right edge, then an end card from 20.2 s), so the default takes 0.5-6.5 s
and crops the left 17% (and enough top and bottom to stay 9:16). OpenCV only (no ffmpeg needed); its pip build has no H.264 encoder, so the clip is WebM: browsers that can't
play it (older iPhones) keep showing the poster. Aim for ~400 KB: lower --width or --fps if it comes out bigger.
"""
import argparse, os
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'web', 'assets', 'hero')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=os.path.join(ROOT, 'data', 'dances', 'sbbyj.mp4'))
    ap.add_argument('--start', type=float, default=0.5)
    ap.add_argument('--seconds', type=float, default=6.0)
    ap.add_argument('--crop', default='0.17,0.08,1,0.91', help='x0,y0,x1,y1 as fractions of the frame')
    ap.add_argument('--width', type=int, default=216)
    ap.add_argument('--fps', type=float, default=10.0)
    a = ap.parse_args()
    cap = cv2.VideoCapture(a.src)
    fps_in = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x0, y0, x1, y1 = (float(v) for v in a.crop.split(','))
    X0, Y0, X1, Y1 = int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)
    crop = lambda f: f[Y0:Y1, X0:X1]
    w_in, h_in = X1 - X0, Y1 - Y0
    w = a.width - a.width % 2; h = int(round(h_in * w / w_in)); h -= h % 2
    os.makedirs(OUT, exist_ok=True)
    video, poster = os.path.join(OUT, 'dance.webm'), os.path.join(OUT, 'dance.jpg')
    out = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*'VP90'), a.fps, (w, h))
    if not out.isOpened(): raise SystemExit('this OpenCV build cannot write VP9 WebM')
    cap.set(cv2.CAP_PROP_POS_MSEC, a.start * 1000)
    step, t, n, first = fps_in / a.fps, 0.0, 0, None
    i = 0
    while n < a.seconds * a.fps:
        ok, frame = cap.read()
        if not ok: break
        if i >= t:
            small = cv2.resize(crop(frame), (w, h), interpolation=cv2.INTER_AREA)
            if first is None: first = small
            out.write(small); n += 1; t += step
        i += 1
    out.release()
    big = None
    cap.set(cv2.CAP_PROP_POS_MSEC, a.start * 1000); ok, frame = cap.read()
    if ok: big = cv2.resize(crop(frame), (w * 2 - (w * 2) % 2, int(round(h_in * w * 2 / w_in))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(poster, big, [cv2.IMWRITE_JPEG_QUALITY, 72])
    print(f'{video}: {n} frames {w}x{h} @ {a.fps:g} fps, {os.path.getsize(video) / 1024:.0f} KB; '
          f'{poster}: {os.path.getsize(poster) / 1024:.0f} KB')


if __name__ == '__main__':
    main()
