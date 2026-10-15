"""The landing page's hero loop: a short, muted, small clip of the lesson's dance and its poster image.

    python scripts/make_hero_video.py [--src data/dances/sbbyj.mp4] [--start 3] [--seconds 8] [--width 270] [--fps 15]

Writes web/assets/hero/dance.webm (VP9, no audio) and web/assets/hero/dance.jpg (the poster, also the still shown with
reduced motion). OpenCV only (no ffmpeg needed); its pip build has no H.264 encoder, so the clip is WebM: browsers that can't
play it (older iPhones) keep showing the poster. Aim for ~400 KB: lower --width or --fps if it comes out bigger.
"""
import argparse, os
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'web', 'assets', 'hero')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=os.path.join(ROOT, 'data', 'dances', 'sbbyj.mp4'))
    ap.add_argument('--start', type=float, default=3.0)
    ap.add_argument('--seconds', type=float, default=8.0)
    ap.add_argument('--width', type=int, default=270)
    ap.add_argument('--fps', type=float, default=15.0)
    a = ap.parse_args()
    cap = cv2.VideoCapture(a.src)
    fps_in = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w_in, h_in = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
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
            small = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
            if first is None: first = small
            out.write(small); n += 1; t += step
        i += 1
    out.release()
    big = cv2.resize(first, (w * 2, h * 2), interpolation=cv2.INTER_AREA) if first is not None else None
    cap.set(cv2.CAP_PROP_POS_MSEC, a.start * 1000); ok, frame = cap.read()
    if ok: big = cv2.resize(frame, (w * 2 - (w * 2) % 2, int(round(h_in * w * 2 / w_in))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(poster, big, [cv2.IMWRITE_JPEG_QUALITY, 72])
    print(f'{video}: {n} frames {w}x{h} @ {a.fps:g} fps, {os.path.getsize(video) / 1024:.0f} KB; '
          f'{poster}: {os.path.getsize(poster) / 1024:.0f} KB')


if __name__ == '__main__':
    main()
