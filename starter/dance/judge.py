"""The judge: how well did the caller dance one 8-beat chunk?

    judge(user_kps, ref, chunk) -> {"score": 0..1, "worst_part": "left arm" | ... | None, "move": str | None, "source": str}

user_kps: (T, 17, 3) COCO-17 poses of the caller during the chunk, 10 fps, x, y in pixels, confidence.
ref:      starter.dance.reference.Reference (ref.chunk(chunk) is the creator's poses for the same 8 beats).

STEVEN: put the trained model in model_judge() below. Return None to fall back to the angle comparison.
The input is already COCO-17 at 10 fps; keypoints.normalize() centres on the hips and scales by the torso if your model
was trained that way. Keep it fast (it runs in a background thread once per chunk, but aim for < 200 ms on a laptop CPU).
"""
import numpy as np

from starter.dance.keypoints import features, SEG_NAMES, PART_OF, normalize  # noqa: F401

MAX_SHIFT_S = 0.6        # timing slack: the caller may be this early or late against the reference
ZERO_SCORE_DEG = 45.0    # weighted mean direction error that scores 0 (0 deg scores 1)
STILL_WEIGHT = 0.15      # weight of a body part that does not move in the reference (moving parts weigh more)

_MODEL = None


def model_judge(user_kps, ref, chunk):
    """STEVEN'S MODEL GOES HERE. Load it once into _MODEL, run it, return the dict (see the module docstring) or None."""
    return None
    # Example shape:
    # global _MODEL
    # if _MODEL is None:
    #     import torch; _MODEL = torch.jit.load('data/dances/judge.pt').eval()
    # x = torch.from_numpy(normalize(user_kps)[None])          # (1, T, 17, 3)
    # with torch.no_grad(): out = _MODEL(x)
    # return {"score": float(out["score"]), "move": out.get("move"), "worst_part": out.get("worst_part"), "source": "model"}


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
