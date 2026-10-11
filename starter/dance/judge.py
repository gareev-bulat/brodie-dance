"""The judge: how well did the caller dance one 8-beat chunk?

    judge(user_kps, ref, chunk) -> {"score": 0..1, "worst_part": "left arm" | ... | None, "move": str | None, "source": str}

user_kps: (T, 17, 3) COCO-17 poses of the caller during the chunk, 10 fps, x, y in pixels, confidence.
ref:      starter.dance.reference.Reference (ref.chunk(chunk) is the creator's poses for the same 8 beats).

Steven's model is in: model_judge() below runs his judge (src/rtcoach/dance_judge.py, data/models/dance_judge.pt) on the part;
it returns None (-> the angle comparison) when the caller wasn't visible enough.
The input is already COCO-17 at 10 fps; keypoints.normalize() centres on the hips and scales by the torso if your model
was trained that way. Keep it fast (it runs in a background thread once per chunk, but aim for < 200 ms on a laptop CPU).
"""
import os
import numpy as np

import facetime  # noqa: F401  (puts src/ on sys.path for rtcoach)
from starter.dance.keypoints import features, SEG_NAMES, PART_OF, normalize, FPS  # noqa: F401

MAX_SHIFT_S = 0.6        # timing slack: the caller may be this early or late against the reference
ZERO_SCORE_DEG = 45.0    # weighted mean direction error that scores 0 (0 deg scores 1)
STILL_WEIGHT = 0.15      # weight of a body part that does not move in the reference (moving parts weigh more)

_MODEL = None


MODEL_PATH = os.environ.get('DANCE_MODEL', 'data/models/dance_judge.pt')   # Steven's trained net; 'baseline' = his DTW rules only
_CHOREO = {}
# Steven's fault labels -> the "body part" Brodie's tip prompt names
TIP = {'arms_higher': 'arms (they need to go higher)', 'arms': 'arms', 'legs': 'legs', 'bigger': 'whole body (the moves need to be bigger)',
       'wrong_side': 'side (they used the other side)', 'late': 'timing (they were late)', 'early': 'timing (they were early)',
       'lost': 'whole body (they lost the moves)'}


def _choreo(ref):
    """Bulat's reference as Steven's choreography: one move per 8-count part (ids part1, part2, ...), poses normalised his way."""
    if ref.path not in _CHOREO:
        from rtcoach.dance_judge import reference_from_npz, mirror_ref
        xy = ref.kps[..., :2].copy(); xy[ref.kps[..., 2] < 0.3] = np.nan
        moves = [dict(id=f'part{k + 1}', name=f'part {k + 1}', t0=ref.first_beat_s + k * ref.chunk_s, t1=ref.first_beat_s + (k + 1) * ref.chunk_s)
                 for k in range(ref.n_chunks)]
        z = dict(xy=xy, ok=np.ones(len(xy), bool), fps=FPS, width=float(np.nanmax(xy[..., 0])) if np.isfinite(xy).any() else 1.0)
        R, ok, t = reference_from_npz(z, FPS, moves, flip=True)    # Steven's default: the caller mirrors the creator on screen
        _CHOREO[ref.path] = dict(dance=ref.name, moves=moves, ref=R, ref_m=mirror_ref(R), ref_ok=ok, t=t, fps=FPS, count_in_s=0.0,
                                 cols={m['id']: (int(round(m['t0'] * FPS)), max(int(round(m['t0'] * FPS)) + 1, min(len(R), int(round(m['t1'] * FPS)))))
                                       for m in moves})
    return _CHOREO[ref.path]


def model_judge(user_kps, ref, chunk):
    """Steven's dance judge (src/rtcoach/dance_judge.py) on one part: his trained net (data/models/dance_judge.pt) scores 3 s windows
    of the caller against that part (DTW-aligned, mirror-aware) every 0.3 s; the part's score is their mean and the tip is his most
    frequent fault. None (-> the angle comparison) when the caller wasn't visible enough for a verdict."""
    global _MODEL
    from rtcoach.dance_judge import DanceJudge
    j = DanceJudge(_choreo(ref), fps=FPS, mode=f'learn:part{chunk + 1}')
    if MODEL_PATH != 'baseline':
        if _MODEL is None: _MODEL = j._load_model(MODEL_PATH)       # torch only to unpickle once; inference is numpy
        j._net = _MODEL
    verdicts = []
    for i, kp in enumerate(user_kps):
        xy = kp[:, :2].copy(); xy[kp[:, 2] < 0.3] = np.nan
        j.push_coco17(xy, None, i / FPS, (1e9, 1e9))
        if i >= 9 and i % 3 == 0 and (v := j.judge()) is not None and v['ready']: verdicts.append(v)
    if not verdicts: return None
    labels = [f['label'] for v in verdicts for f in v['faults']]
    worst = max(set(labels), key=labels.count) if labels else None
    return dict(score=float(np.mean([v['score'] for v in verdicts])), worst_part=TIP.get(worst), move=None,
                source='steven-model' if j._net is not None else 'steven-baseline',
                details=dict(windows=len(verdicts), faults={l: labels.count(l) for l in set(labels)}))


def _angle_err(a, b):
    """Absolute difference of two angle arrays, wrapped to 0..pi."""
    d = np.abs(a - b) % (2 * np.pi)
    return np.minimum(d, 2 * np.pi - d)


def compare(user_kps, ref_kps, fps=10):
    """Angle comparison with timing slack and mirror check. Returns (score, worst_part, details)."""
    if len(user_kps) == 0 or len(ref_kps) == 0:
        return 0.0, None, dict(reason='no poses')
    rd, rok = features(ref_kps)
    # how much each segment moves in the reference (circular spread, radians): the moving parts are the dance
    spread = np.array([np.sqrt(-2 * np.log(max(np.abs(np.exp(1j * rd[rok[:, j], j]).mean()), 1e-6))) if rok[:, j].any() else 0.0
                       for j in range(len(SEG_NAMES))])
    weight = STILL_WEIGHT + np.minimum(spread, 1.5)
    best = None
    for mirror in (False, True):
        ud, uok = features(user_kps, mirror=mirror)
        for shift in range(-int(MAX_SHIFT_S * fps), int(MAX_SHIFT_S * fps) + 1):
            # user frame i is compared with reference frame i + shift
            i0 = max(0, -shift); i1 = min(len(ud), len(rd) - shift)
            if i1 - i0 < max(3, len(rd) // 2): continue
            e = _angle_err(ud[i0:i1], rd[i0 + shift:i1 + shift])
            m = uok[i0:i1] & rok[i0 + shift:i1 + shift]
            if m.sum() < 5: continue
            seg_err = np.array([e[:, j][m[:, j]].mean() if m[:, j].any() else np.nan for j in range(len(SEG_NAMES))])
            v = ~np.isnan(seg_err)
            mean = float((seg_err[v] * weight[v]).sum() / weight[v].sum())
            if best is None or mean < best[0]:
                best = (mean, seg_err, shift, mirror, float(m.mean()))
    if best is None:
        return 0.0, None, dict(reason='body not visible enough')
    mean, seg_err, shift, mirror, seen = best
    score = float(np.clip(1.0 - np.degrees(mean) / ZERO_SCORE_DEG, 0.0, 1.0))
    part_err = {}
    for j, n in enumerate(SEG_NAMES):
        if not np.isnan(seg_err[j]): part_err.setdefault(PART_OF[n], []).append(np.degrees(seg_err[j]))
    part_err = {p: float(np.mean(v)) for p, v in part_err.items()}
    part_w = {}
    for j, n in enumerate(SEG_NAMES): part_w[PART_OF[n]] = part_w.get(PART_OF[n], 0.0) + weight[j]
    worst = max(part_err, key=lambda p: part_err[p] * part_w[p]) if part_err else None   # the part that costs the most score
    return score, worst, dict(mean_err_deg=round(float(np.degrees(mean)), 1), shift_s=round(shift / fps, 2), mirrored=mirror,
                              seen=round(seen, 2), part_err_deg={p: round(v, 1) for p, v in part_err.items()})


def judge(user_kps, ref, chunk):
    try:
        out = model_judge(user_kps, ref, chunk)
    except Exception as e:                                   # a broken model must not break the call
        print(f'[dance] model_judge failed, using the angle comparison: {e!r}', flush=True)
        out = None
    if out is not None:
        out.setdefault('source', 'model'); return out
    score, worst, det = compare(user_kps, ref.chunk(chunk))
    return dict(score=score, worst_part=worst, move=None, source='angles', details=det)
