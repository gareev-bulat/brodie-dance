"""Bake each lesson's creator poses into web/index.html for the animated skeleton on the dance cards.

  .venv/bin/python scripts/bake_landing_poses.py        # rewrites the `const POSES = ...` line in web/index.html

Per lesson committed in data/dances (local leftovers are skipped): the 10 fps COCO-17 poses from one beat before
part 1 to the end of the last part, reduced to 13 points (a head point, then shoulders, elbows, wrists, hips, knees,
ankles), gaps filled, lightly smoothed and quantized to 0-255 inside the dancer's box. Sides stay as seen on screen, like the lesson.
"""
import json, os, re, subprocess, sys
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from starter.dance.reference import Reference, DANCES_DIR  # noqa: E402
from starter.dance.keypoints import FPS  # noqa: E402

PAGE = os.path.join(ROOT, 'web', 'index.html')
CONF = 0.3


def fill(a, ok):
    """Linear interpolation over frames where ok is False (edges hold the nearest good value)."""
    t = np.arange(len(a)); good = np.flatnonzero(ok)
    return a if len(good) == 0 else np.interp(t, good, a[good])


def bake(r):
    t0 = max(r.first_beat_s - r.beat_s, 0.0)
    t1 = r.first_beat_s + r.n_chunks * r.chunk_s
    k = r.kps[int(round(t0 * FPS)):int(round(t1 * FPS))]
    body = k[:, 5:17]                                                            # shoulders .. ankles
    xy = np.stack([np.stack([fill(body[:, j, c], body[:, j, 2] >= CONF) for c in (0, 1)], -1) for j in range(12)], 1)
    sh_mid = (xy[:, 0] + xy[:, 1]) / 2
    sh_w = np.linalg.norm(xy[:, 0] - xy[:, 1], axis=1, keepdims=True)
    above = sh_mid - np.concatenate([np.zeros_like(sh_w), 0.75 * sh_w], 1)      # where the head sits when the nose is lost
    nose_ok = k[:, 0, 2] >= CONF
    head = np.where(nose_ok[:, None], k[:, 0, :2], above)
    pts = np.concatenate([head[:, None], xy], 1)                                 # (T, 13, 2)
    pad = np.pad(pts, ((1, 1), (0, 0), (0, 0)), mode='edge')
    pts = 0.25 * pad[:-2] + 0.5 * pad[1:-1] + 0.25 * pad[2:]                    # 3-tap smoothing against jitter
    lo, hi = pts.reshape(-1, 2).min(0), pts.reshape(-1, 2).max(0)
    q = np.round((pts - lo) / (hi - lo) * 255).astype(int)
    return {'fps': FPS, 'aspect': round(float((hi - lo)[0] / (hi - lo)[1]), 3),
            'parts': [round(r.first_beat_s + i * r.chunk_s - t0, 2) for i in range(r.n_chunks + 1)],
            'beat': round(r.beat_s, 4), 'f': ''.join(f'{v:02x}' for v in q.reshape(-1))}


def main():
    poses = {}
    tracked = subprocess.run(['git', 'ls-files', '*.npz'], cwd=DANCES_DIR, capture_output=True, text=True, check=True).stdout.split()
    for p in sorted(os.path.join(DANCES_DIR, f) for f in tracked):
        r = Reference(p)
        poses[r.name] = bake(r)
        print(f"{r.name}: {len(poses[r.name]['f']) // 52} frames", file=sys.stderr)
    html = open(PAGE).read()
    line = '  const POSES = ' + json.dumps(poses, separators=(',', ':')) + ';'
    html, n = re.subn(r'^  const POSES = .*;$', lambda _: line, html, count=1, flags=re.M)
    if n != 1:
        sys.exit('no `const POSES = ...;` line in web/index.html')
    open(PAGE, 'w').write(html)


if __name__ == '__main__':
    main()
