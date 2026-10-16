"""The landing page's hero loop: a short, muted, small clip of the lesson's dance and its poster image.

    python scripts/make_hero_video.py [--src data/dances/sbbyj.mp4] [--start 0.5] [--seconds 6] [--crop 0.17,0.08,1,0.91] [--width 216] [--fps 10]

Writes web/assets/hero/dance.webm (VP9, no audio) and web/assets/hero/dance.jpg (the poster, also the still shown with
reduced motion), and web/assets/hero/dance-poses.json: the dancer's skeleton and the beat for the same seconds, from the
lesson (data/dances/<name>.npz next to --src), so the landing page draws the glowing skeleton and the counts in sync.
Only the poses:  python scripts/make_hero_video.py --poses-only --crop x0,y0,x1,y1 (fractions of the frame) cuts away burned-in marks: sbbyj.mp4 is a TikTok download whose
watermark sits on the left edge until 6.6 s (then the right edge, then an end card from 20.2 s), so the default takes 0.5-6.5 s
and crops the left 17% (and enough top and bottom to stay 9:16). OpenCV only (no ffmpeg needed); its pip build has no H.264 encoder, so the clip is WebM: browsers that can't
play it (older iPhones) keep showing the poster. Aim for ~400 KB: lower --width or --fps if it comes out bigger.
"""
import argparse, json, os
import cv2
import numpy as np

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
    ap.add_argument('--poses-only', action='store_true', help='write dance-poses.json only (the clip and poster stay as they are)')
    a = ap.parse_args()
    write_poses(a)
    if a.poses_only: return
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


COCO_BODY = [0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]     # nose, shoulders, elbows, wrists, hips, knees, ankles


def write_poses(a):
    """The lesson's keypoints (10 fps, pixels of the source frame) for --start .. --start + --seconds, mapped into the clip's
    crop as 0..1000 (-1 where the point isn't seen), plus the beat: first_beat (s, source time) and beat (s)."""
    npz = os.path.splitext(a.src)[0] + '.npz'
    if not os.path.exists(npz): print(f'no {npz}: dance-poses.json not written'); return
    z = np.load(npz); kps = z['kps']; fps = 10.0
    cap = cv2.VideoCapture(a.src); W, H = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT); cap.release()
    x0, y0, x1, y1 = (float(v) for v in a.crop.split(','))
    frames = []
    for i in range(int(round(a.start * fps)), int(round((a.start + a.seconds) * fps)) + 1):
        k = kps[min(i, len(kps) - 1)]; row = []
        for j in COCO_BODY:
            x, y, v = (float(t) for t in k[j])
            u, w = (x / W - x0) / (x1 - x0), (y / H - y0) / (y1 - y0)
            row += [int(round(u * 1000)), int(round(w * 1000))] if v >= 0.3 and -0.1 < u < 1.1 and -0.1 < w < 1.1 else [-1, -1]
        frames.append(row)
    out = {'fps': fps, 'start': a.start, 'seconds': a.seconds, 'first_beat': round(float(z['first_beat_s']), 4),
           'beat': round(60.0 / float(z['bpm']), 4), 'f': frames}
    path = os.path.join(OUT, 'dance-poses.json')
    with open(path, 'w') as f: json.dump(out, f, separators=(',', ':'))
    print(f'{path}: {len(frames)} frames, {os.path.getsize(path) / 1024:.1f} KB')


if __name__ == '__main__':
    main()
