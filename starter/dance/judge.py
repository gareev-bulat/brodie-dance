"""The judge: how well did the caller dance one 8-beat chunk?

    judge(user_kps, ref, chunk) -> {"score": 0..1, "worst_part": "left arm" | ... | None, "move": str | None, "source": str}

user_kps: (T, 17, 3) COCO-17 poses of the caller during the chunk, 10 fps, x, y in pixels, confidence.
ref:      starter.dance.reference.Reference (ref.chunk(chunk) is the creator's poses for the same 8 beats).

STEVEN: put the trained model in model_judge() below. Return None to fall back to the angle comparison.
The input is already COCO-17 at 10 fps; keypoints.normalize() centres on the hips and scales by the torso if your model
was trained that way. Keep it fast (it runs in a background thread once per chunk, but aim for < 200 ms on a laptop CPU).
"""
import os
import numpy as np

from starter.dance.keypoints import features, SEG_NAMES, PART_OF, normalize  # noqa: F401

MAX_SHIFT_S = 0.6        # timing slack: the caller may be this early or late against the reference
ZERO_SCORE_DEG = 45.0    # weighted mean direction error that scores 0 (0 deg scores 1)
STILL_WEIGHT = 0.15      # weight of a body part that does not move in the reference (moving parts weigh more)

_MODEL = None
_CHOREOS = {}            # Reference path -> choreo dict for rtcoach.dance_judge (built once per reference)
_NET = {}                # checkpoint path -> loaded network (loaded once, shared by every per-chunk judge)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_MODEL = os.path.join(ROOT, 'data', 'models', 'dance_judge.pt')


def _model_path():
    """RTCOACH_DANCE_MODEL: unset -> data/models/dance_judge.pt if it exists (else the DTW baseline); '' -> baseline; a path -> that."""
    p = os.environ.get('RTCOACH_DANCE_MODEL')
    if p is None: return DEFAULT_MODEL if os.path.exists(DEFAULT_MODEL) else None
    return p or None


def _choreo_for(ref):
    """Steven's judge wants a choreo (moves with t0/t1 + the normalised reference); build one from Bulat's Reference: one move per
    8-beat chunk, 'part_<k+1>', in the reference's own clip seconds. Cached per reference file."""
    ch = _CHOREOS.get(ref.path)
    if ch is not None: return ch
    import facetime  # noqa: F401  (puts src/ on sys.path)
    from rtcoach.dance_judge import reference_from_npz, mirror_ref
    kps = np.asarray(ref.kps, np.float32)
    xy = kps[:, :, :2].copy(); xy[kps[:, :, 2] < MIN_CONF_JUDGE] = np.nan
    ok = ~np.isnan(xy[:, [5, 6, 11, 12]]).any(axis=(1, 2))
    fps = FPS_JUDGE
    moves = []
    for k in range(ref.n_chunks):
        t0 = ref.first_beat_s + k * ref.chunk_s; t1 = min(t0 + ref.chunk_s, len(kps) / fps)
        if t1 - t0 >= 0.5: moves.append({'id': f'part_{k + 1}', 'name': ref.step(k) or f'part {k + 1}', 't0': t0, 't1': t1})
    z = {'xy': xy, 'ok': ok, 'fps': float(fps), 'width': float(np.nanmax(xy[:, :, 0])) if ok.any() else 1.0}
    R, rok, t = reference_from_npz(z, fps, moves, flip=False)       # both the reference and its mirror are tried by the judge
    cols = {m['id']: (int(round(m['t0'] * fps)), max(int(round(m['t0'] * fps)) + 1, min(len(R), int(round(m['t1'] * fps)))))
            for m in moves}
    ch = dict(dance=ref.name, moves=moves, thresholds={}, count_in_s=ref.first_beat_s, ref=R, ref_m=mirror_ref(R), ref_ok=rok, t=t,
              fps=fps, cols=cols)
    _CHOREOS[ref.path] = ch
    return ch


MIN_CONF_JUDGE, FPS_JUDGE = 0.3, 10


def model_judge(user_kps, ref, chunk):
    """Steven's judge (src/rtcoach/dance_judge.py, docs/DANCE_MODEL_INTERFACE.md) on one chunk: the caller's poses are pushed through
    a DanceJudge in 'learn:part_<k+1>' mode (the chunk's reference tiled, DTW-aligned, scored by the trained net when
    data/models/dance_judge.pt exists or RTCOACH_DANCE_MODEL points at a checkpoint; RTCOACH_DANCE_MODEL='' = DTW baseline).
    score: the judge's 0..1 score. worst_part: the angle comparison's body part (side-specific, which the net's labels are not).
    The judge's mirror and timing faults are dropped here: the lesson already mirrors the video and keeps its own beat clock.
    Returns None (-> angle comparison) when fewer than 10 frames or the body was not visible enough."""
    kps = np.asarray(user_kps, np.float32)
    if kps.ndim != 3 or kps.shape[1:] != (17, 3) or len(kps) < 10: return None
    import facetime  # noqa: F401
    from rtcoach.dance_judge import DanceJudge
    ch = _choreo_for(ref)
    mid = f'part_{int(chunk) % max(ref.n_chunks, 1) + 1}'
    if mid not in ch['cols']: return None
    path = _model_path()
    if path and path not in _NET:
        _NET[path] = DanceJudge(ch, fps=FPS_JUDGE, win=30, mode=f'learn:{mid}', model=path)._net   # load the net once
    from rtcoach.dance_judge import FLIP_IDX
    width = float(np.nanmax(kps[:, :, 0])) + float(np.nanmin(kps[:, :, 0]))
    flipped = kps[:, FLIP_IDX].copy(); flipped[:, :, 0] = width - flipped[:, :, 0]
    best = None
    for side, u in (('as-is', kps), ('mirrored', flipped)):            # like compare(): the caller's side does not matter
        j = DanceJudge(ch, fps=FPS_JUDGE, win=30, mode=f'learn:{mid}', model=None)
        if path: j._net = _NET[path]                                  # share the loaded net; the judge itself is per-call state
        big = (1e9, 1e9)                                              # pixel bounds: the judge only needs x, y >= 0
        for i, k in enumerate(u): j.push_coco17(k[:, :2], k[:, 2], i / FPS_JUDGE, big)
        v = j.judge(); v = j.judge() or v                             # a move must win two consecutive calls: ask twice
        if v is None or not v['ready']: continue
        if best is None or v['score'] > best[1]['score']: best = (side, v, j._net is not None)
    if best is None: return None
    side, v, has_net = best
    _, worst, det = compare(kps, ref.chunk(chunk))
    faults = [f for f in v['faults'] if f['label'] not in ('wrong_side', 'late', 'early')]
    return {'score': float(v['score']), 'worst_part': worst, 'move': None, 'source': 'model' if has_net else 'dtw',
            'faults': [f['label'] for f in faults], 'cue': faults[0]['cue_text'] if faults else None,
            'details': {'judge_move': v['move'], 'confidence': v['confidence'], 'ms': v['ms'], 'side': side,
                        'mirrored': det.get('mirrored')}}


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
