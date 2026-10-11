"""Dance judge: aligns the learner's last 3 s of MediaPipe landmarks with the reference choreography (subsequence DTW)
and reports the move, a quality score, faults and lateness. Contract: docs/DANCE_MODEL_INTERFACE.md sections 2, 3, 4, 7.

Live (facetime/perception.py hook):
    judge = DanceJudge('data/dance/choreo/josh_king_o.json', fps=10, win=30, mode='full')   # or mode='learn:<move_id>'
    judge.push(img33, ok, t, hw)          # every frame: MediaPipe (33,5) [x,y,z,vis,presence] (x,y 0..1) or None; hw = (h, w)
    v = judge.judge()                     # every 3rd frame -> Verdict (JSON-native dict) or None until 10 frames were pushed
    judge.set_ref_clock(t_ref, t)         # optional: the reference is at clip second t_ref when the judge clock reads t

Offline (YOLO npz from scripts/extract_pose_yolo.py, pixels, NaN = joint not visible):
    verdicts = replay_npz('data/dance/pose/josh_king_o_1791679083441.npz', judge, every=3)

Reference clock: unless set_ref_clock is called, the learner's first ok frame is taken as the reference's first visible frame
(clip second ref_start = the first move's t0, normally 0), so the choreo's count-in move ('ready', ref_start..count_in_s)
covers the first count_in_s - ref_start seconds and the first danced move begins at clip second count_in_s.
'full' mode judges the moves within +-1.5 s of that clock; 'learn:<move_id>' loops one move (its reference is tiled) and
reports no timing offset. timing_offset_s is always the raw DTW offset; the late/early fault probabilities are scaled by the
fit (full at score >= 0.7, 0 at <= 0.5), so a learner dancing small or on the other side is told that, not 'Catch up'.
Numpy only at inference; torch is imported only when model= is given, and only to unpickle the
checkpoint (scripts/train_dance.py writes it): the net itself runs in numpy (_NumpyNet), so the latency budget holds.
"""
import json, math, os, time
from collections import deque
from typing import Dict, List, Optional, TypedDict
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FAULTS = ('arms_higher', 'arms', 'legs', 'bigger', 'wrong_side', 'late', 'early', 'lost')
CUE_TEXT = {'arms_higher': 'Arms higher', 'arms': 'Check your arms', 'legs': 'Watch your legs', 'bigger': 'Bigger moves',
            'wrong_side': 'Other side', 'late': 'Catch up', 'early': 'Wait for the beat', 'lost': 'Watch and try again'}
COCO2MP = [0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]     # MediaPipe index of each COCO-17 joint
FLIP_IDX = np.array([0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15])  # left <-> right swap (coco-pose.yaml)
BODY = np.arange(5, 17); ARMS = [7, 8, 9, 10]; WRISTS = [9, 10]; LEGS = [13, 14, 15, 16]; ANKLES = [15, 16]; LIMBS = ARMS + LEGS
CORE = [5, 6, 11, 12]                                                           # shoulders + hips: a frame without them is not ok
JOINT_W = np.array([0, 0, 0, 0, 0, .5, .5, 1, 1, 1.5, 1.5, .25, .25, 1, 1, 1, 1], np.float32)   # DTW joint weights (torso units)
VIS_THR, MISS_COST, CAND_S, BACK_S, OFFSET_CLIP, MIN_OK, NEWEST = 0.3, 1.0, 1.5, 3.5, 1.5, 10, 10
CLOCK_PULL = 0.04     # DTW cost units per second of distance from the reference clock when choosing the end column
MODEL_FAULTS = FAULTS[:5]   # labels a trained net may supply; late/early stay analytic (the net sees aligned pairs), lost is a streak


class Fault(TypedDict):
    label: str; cue_text: str; p: float


class Verdict(TypedDict):
    t_judge: float; ready: bool; move: str; move_probs: Dict[str, float]; phase: Optional[float]; score: float
    faults: List[Fault]; timing_offset_s: Optional[float]; t_ref: Optional[float]; done: bool; confidence: float; ms: float


def _resolve(path, choreo_path):
    for p in (path, os.path.join(os.path.dirname(os.path.abspath(choreo_path)), path), os.path.join(ROOT, path)):
        if os.path.exists(p): return p
    raise FileNotFoundError(f'{path} (tried cwd, next to {choreo_path}, {ROOT})')


def mirror_ref(R):
    """(T,17,3) [x,y,mask] -> the other-side version: x -> -x and left/right joints swapped."""
    Rm = R[:, FLIP_IDX].copy(); Rm[:, :, 0] *= -1; return Rm


def reference_from_npz(z, fps, moves, flip):
    """YOLO npz (xy pixels with NaN, ok, fps, width) -> R (N,17,3) [x,y,mask] at `fps`, ok (N,), t (N,). `flip` applies one
    horizontal mirror (x -> width-x, then FLIP_IDX). Per frame: minus the hip centre; scale: the median torso (shoulder centre
    to hip centre) over the ok frames of the move window the frame falls in (the clip median outside every move)."""
    xy = np.array(z['xy'], np.float32); ok = np.asarray(z['ok'], bool).copy(); w, fps_src = float(z['width']), float(z['fps'])
    if flip: xy[:, :, 0] = w - xy[:, :, 0]; xy = xy[:, FLIP_IDX]
    n = int(math.floor((len(xy) - 1) * fps / fps_src)) + 1
    idx = np.clip(np.round(np.arange(n) * fps_src / fps).astype(int), 0, len(xy) - 1); xy, ok = xy[idx], ok[idx]
    mask = ok[:, None] & ~np.isnan(xy).any(-1); ok &= mask[:, CORE].all(1); mask &= ok[:, None]; xy = np.nan_to_num(xy)
    hip = (xy[:, 11] + xy[:, 12]) / 2; torso = np.linalg.norm((xy[:, 5] + xy[:, 6]) / 2 - hip, axis=1); t = np.arange(n) / fps
    R = np.zeros((n, 17, 3), np.float32); s_all = float(np.median(torso[ok])) if ok.any() else 1.0; done = np.zeros(n, bool)
    for m in list(moves) + [None]:
        sel = (~done) if m is None else ((t >= m['t0']) & (t < m['t1']) & ~done); done |= sel
        if not sel.any(): continue
        s = float(np.median(torso[sel & ok])) if (sel & ok).any() else s_all
        R[sel, :, :2] = (xy[sel] - hip[sel, None]) / max(s, 1e-3)
    R[:, :, 2] = mask; R[~mask] = 0
    return R, ok, t


def load_choreo(path, fps=10):
    """Choreo json + reference: keys ref/ref_m (N,17,3), ref_ok (N,), t (N,), cols {move_id: (j0, j1)} at `fps`."""
    with open(path) as f: ch = json.load(f)
    z = np.load(_resolve(ch['npz'], path), allow_pickle=False)
    mirrored = bool(ch.get('mirrored', bool(z['mirrored']) if 'mirrored' in z.files else False))
    # un-mirror a selfie clip; a learner who mirrors the screen copies a non-mirrored clip flipped: one flip in either case
    flip = mirrored or bool(ch.get('learner_mirrors_screen', True))
    R, ok, t = reference_from_npz(z, fps, ch['moves'], flip)
    ch.update(ref=R, ref_m=mirror_ref(R), ref_ok=ok, t=t, fps=fps,
              cols={m['id']: (int(round(m['t0'] * fps)), max(int(round(m['t0'] * fps)) + 1, min(len(R), int(round(m['t1'] * fps))))) for m in ch['moves']})
    return ch


def _wmean(d, m, w=None):
    """Weighted mean of d over mask m (same shape); (value, weight)."""
    ww = m.astype(np.float32) if w is None else m * w
    s = float(ww.sum()); return (float((d * ww).sum()) / s if s > 0 else 0.0), s


def _dtw(C):
    """Slope-constrained subsequence DTW. C (K,n,L): cost of learner frame i vs reference column j. Free start on row 0,
    steps (1,1), (1,2) via (i,j-1), (2,1) via (i-1,j): the path never runs faster than 2x or slower than 0.5x. Returns the
    accumulated cost D (K,n,L) and the step choice (K,n,L) int8 for _backtrack."""
    K, n, L = C.shape; INF = np.float32(1e9)
    D = np.full((K, n, L), INF, np.float32); ch = np.zeros((K, n, L), np.int8); D[:, 0] = C[:, 0]
    cand = np.empty((3, K, L), np.float32)
    for i in range(1, n):
        cand.fill(INF); cand[0, :, 1:] = D[:, i - 1, :-1]; cand[1, :, 2:] = D[:, i - 1, :-2] + C[:, i, 1:-1]
        if i >= 2: cand[2, :, 1:] = D[:, i - 2, :-1] + C[:, i - 1, 1:]
        a = cand.argmin(0); ch[:, i] = a; D[:, i] = C[:, i] + np.take_along_axis(cand, a[None], 0)[0]
    return D, ch


def _backtrack(ch, k, j, n):
    """Reference column matched to each of the n learner frames for the path ending at (n-1, j) of candidate k."""
    cols = [0] * n; i = n - 1
    while i > 0:
        cols[i] = j; c = int(ch[k, i, j])
        if c == 0: i -= 1; j -= 1
        elif c == 1: i -= 1; j -= 2
        else: cols[i - 1] = j; i -= 2; j -= 1
    cols[0] = j if i == 0 else cols[0]
    return cols


# ---- trained scoring net (contract section 6). One architecture, two forwards: torch for training, numpy for inference ----
def net_arch(d=96, win=30):
    """Hyper-parameters stored in the checkpoint ('arch'); c_in = 17 joints x [X(3), R(3), Rm(3), (X-R).xy, (X-Rm).xy, |X-R|, |X-Rm|, |R-Rm|]."""
    return {'kind': 'conv1d', 'd': int(d), 'win': int(win), 'k': [5, 5, 3], 'c_in': 17 * 16, 'n_faults': 7, 'pool_newest': NEWEST}


def build_torch_net(arch):
    """Trainable twin of _NumpyNet (used by scripts/train_dance.py). forward(x (B,win,17,9) = concat(X, R, Rm)) ->
    (score logit (B,), fault logits (B,7)); frames with no visible learner joint (not ok, or padding) are left out of the pooling."""
    import torch, torch.nn as nn, torch.nn.functional as F

    class DanceNet(nn.Module):
        def __init__(s):
            super().__init__(); d, k, c = arch['d'], arch['k'], arch['c_in']
            s.c1, s.c2, s.c3 = nn.Conv1d(c, d, k[0], padding=k[0] // 2), nn.Conv1d(d, d, k[1], padding=k[1] // 2), nn.Conv1d(d, d, k[2], padding=k[2] // 2)
            s.fc, s.drop, s.h_score, s.h_fault = nn.Linear(3 * d, d), nn.Dropout(0.1), nn.Linear(d, 1), nn.Linear(d, arch['n_faults'])

        def forward(s, x):
            X, R, Rm = x[..., :3], x[..., 3:6], x[..., 6:9]
            dR, dRm = (X[..., :2] - R[..., :2]) * X[..., 2:3] * R[..., 2:3], (X[..., :2] - Rm[..., :2]) * X[..., 2:3] * Rm[..., 2:3]
            dA = (R[..., :2] - Rm[..., :2]) * R[..., 2:3] * Rm[..., 2:3]; nrm = lambda v: (v * v).sum(-1, keepdim=True).clamp(min=1e-12).sqrt()
            f = torch.cat([X, R, Rm, dR, dRm, nrm(dR), nrm(dRm), nrm(dA)], -1)
            h = f.flatten(2).transpose(1, 2); h = F.silu(s.c1(h)); h = F.silu(s.c2(h)); h = F.silu(s.c3(h))      # (B, d, T)
            v = X[..., 2].sum(-1) > 0; v[:, 0] |= ~v.any(1)                                                      # valid frames
            newest = v & (torch.flip(torch.cumsum(torch.flip(v.int(), [1]), 1), [1]) <= arch['pool_newest'])
            vf, nf = v.float()[:, None], newest.float()[:, None]
            z = torch.cat([(h * vf).sum(-1) / vf.sum(-1), h.masked_fill(~v[:, None], -1e4).amax(-1), (h * nf).sum(-1) / nf.sum(-1)], -1)
            z = s.drop(F.silu(s.fc(z))); return s.h_score(z).squeeze(-1), s.h_fault(z)
    return DanceNet()


class _NumpyNet:
    """Inference twin of build_torch_net on a loaded checkpoint dict: (X, R, Rm) (win,17,3) each -> (score, {label: p})."""
    def __init__(self, ck):
        sd = ck['state_dict'] if 'state_dict' in ck else ck['state']
        g = lambda k: np.ascontiguousarray(sd[k].detach().cpu().numpy() if hasattr(sd[k], 'detach') else sd[k], dtype=np.float32)
        self.convs = [(g(f'c{i}.weight'), g(f'c{i}.bias')) for i in (1, 2, 3)]
        self.fc, self.hs, self.hf = (g('fc.weight'), g('fc.bias')), (g('h_score.weight'), g('h_score.bias')), (g('h_fault.weight'), g('h_fault.bias'))
        self.faults = list(ck.get('faults', FAULTS[:7])); self.use = set(ck.get('model_faults', MODEL_FAULTS))
        self.newest = int(ck.get('arch', {}).get('pool_newest', NEWEST))

    @staticmethod
    def _conv(h, W, b):
        """'same' 1-D convolution: h (C,T), W (d,C,k) torch layout -> (d,T), via one matmul on the stacked taps."""
        d, C, k = W.shape; p = k // 2; T = h.shape[1]; hp = np.pad(h, ((0, 0), (p, p)))
        return W.reshape(d, C * k) @ np.stack([hp[:, i:i + T] for i in range(k)], 1).reshape(C * k, T) + b[:, None]

    def __call__(self, X, R, Rm):
        sig = lambda z: 0.5 * (1.0 + np.tanh(0.5 * z)); silu = lambda z: z * sig(z)          # overflow-safe forms
        dR, dRm = (X[..., :2] - R[..., :2]) * X[..., 2:3] * R[..., 2:3], (X[..., :2] - Rm[..., :2]) * X[..., 2:3] * Rm[..., 2:3]
        dA = (R[..., :2] - Rm[..., :2]) * R[..., 2:3] * Rm[..., 2:3]; nrm = lambda v: np.sqrt(np.maximum((v * v).sum(-1, keepdims=True), 1e-12))
        h = np.concatenate([X, R, Rm, dR, dRm, nrm(dR), nrm(dRm), nrm(dA)], -1).reshape(len(X), -1).T
        for W, b in self.convs: h = silu(self._conv(h, W, b))
        v = X[:, :, 2].sum(1) > 0
        if not v.any(): v[0] = True
        newest = v & (np.cumsum(v[::-1])[::-1] <= self.newest)
        z = silu(self.fc[0] @ np.concatenate([h[:, v].mean(1), h[:, v].max(1), h[:, newest].mean(1)]) + self.fc[1])
        score = sig(float((self.hs[0] @ z + self.hs[1])[0])); pf = sig(self.hf[0] @ z + self.hf[1])
        return float(score), {l: (float(p) if l in self.use else 0.0) for l, p in zip(self.faults, pf)}


class DanceJudge:
    def __init__(self, choreo: str, fps=10, win=30, mode='full', model: Optional[str] = None):
        self.fps, self.win, self.mode = int(fps), int(win), mode
        self.choreo = load_choreo(choreo, self.fps) if isinstance(choreo, (str, os.PathLike)) else choreo
        self.moves = self.choreo['moves']; self.ids = [m['id'] for m in self.moves]; self.cols = self.choreo['cols']
        self.R, self.Rm, self.t_ref_cols = self.choreo['ref'], self.choreo['ref_m'], self.choreo['t']
        th = self.choreo.get('thresholds', {})
        self.tau, self.none_below = float(th.get('tau', 0.35)), float(th.get('none_below', 0.4))
        self.fault_p, self.lost_calls = float(th.get('fault_p', 0.3)), int(th.get('lost_calls', 6))
        self.count_in_s = float(self.choreo.get('count_in_s', 0.0)); self.t_end = max(m['t1'] for m in self.moves)
        self.ref_start = min(m['t0'] for m in self.moves)   # clip second of the reference's first visible frame (0 unless the dancer enters late)
        self.target = None
        if mode.startswith('learn:'):
            self.target = mode[6:]
            if self.target not in self.ids: raise ValueError(f'unknown move {self.target!r}; choreo has {self.ids}')
        elif mode != 'full': raise ValueError(f"mode must be 'full' or 'learn:<move_id>', got {mode!r}")
        self.buf = deque(maxlen=self.win); self.n_pushed = 0; self.t_first_ok = None; self.anchor = None
        self._raw = ('none', 0); self._move = 'none'; self._low = 0
        self._net = self._load_model(model) if model else None

    # ---- input ----
    def push(self, img33, ok: bool, t: float, hw):
        """MediaPipe (33,5) normalised landmarks (or None) for the frame of shape hw=(h, w) at judge time t."""
        if img33 is None or not ok: self._push_frame(t, None, None); return
        a = np.asarray(img33, np.float32)[COCO2MP]; h, w = hw
        m = (a[:, 3] >= VIS_THR) & (a[:, 0] >= 0) & (a[:, 0] <= 1) & (a[:, 1] >= 0) & (a[:, 1] <= 1)
        self._push_frame(t, a[:, :2] * np.array([w, h], np.float32), m)

    def push_coco17(self, xy17, conf17, t: float, hw):
        """Offline: COCO-17 joints in pixels of an (h, w) frame (NaN allowed), confidences (17,) or None."""
        xy = np.asarray(xy17, np.float32); h, w = hw
        m = ~np.isnan(xy).any(1) & (xy[:, 0] >= 0) & (xy[:, 0] <= w) & (xy[:, 1] >= 0) & (xy[:, 1] <= h)
        if conf17 is not None: m &= np.asarray(conf17, np.float32) >= VIS_THR
        self._push_frame(t, np.nan_to_num(xy), m)

    def _push_frame(self, t, xy, m):
        ok = xy is not None and bool(m[CORE].all())
        if not ok: xy, m = np.zeros((17, 2), np.float32), np.zeros(17, bool)
        else: xy = np.where(m[:, None], xy, 0.0).astype(np.float32)
        self.buf.append((float(t), xy, m, ok)); self.n_pushed += 1
        if ok and self.t_first_ok is None: self.t_first_ok = float(t)

    def set_ref_clock(self, t_ref: float, t: float): self.anchor = (float(t_ref), float(t))

    def t_ref_at(self, t):
        """Reference clock (clip seconds). Default: the learner's first ok frame corresponds to the reference's first
        visible frame (ref_start = first move t0), so clip times, count_in_s and move boundaries line up."""
        if self.anchor is not None: return self.anchor[0] + (t - self.anchor[1])
        return None if self.t_first_ok is None else self.ref_start + (t - self.t_first_ok)

    # ---- judge ----
    def judge(self) -> Optional[Verdict]:
        if self.n_pushed < MIN_OK: return None
        t0 = time.perf_counter(); fr = list(self.buf); t_now = fr[-1][0]; t_ref = self.t_ref_at(t_now)
        X, ok = self._window(fr); n_ok = int(ok.sum()); n = len(fr)
        body_vis = X[:n, BODY, 2].sum(1)                                  # unmasked body joints per frame
        ready = n_ok >= MIN_OK and int((body_vis >= 8).sum()) >= min(15, self.win // 2)
        conf = float(body_vis[ok].mean() / len(BODY)) if n_ok else 0.0
        if n_ok / n < 0.8: conf *= 0.5
        done = bool(t_ref is not None and self.target is None and t_ref > self.t_end + CAND_S)
        base = dict(t_judge=t_now, ready=ready, t_ref=t_ref, done=done, confidence=conf)
        cols, col_t, col_move, cands = self._candidates(t_ref)
        if n_ok < MIN_OK or not cands or cols is None:
            self._raw = ('none', 0); self._move = 'none'; self._low += 1
            return self._verdict(t0, score=0.0, move_probs={'none': 1.0}, faults=self._lost_only(), **base)
        Xo = X[:n][ok]; C = self._cost(Xo[:, :, :2], Xo[:, :, 2] > 0, cols); D, ch = _dtw(C); last = D[:, -1, :] / n_ok
        # end column: the cheapest, with a small pull toward the reference clock so a held pose does not read as late/early
        pen = last + CLOCK_PULL * np.abs(col_t - t_ref)[None, :] if (t_ref is not None and self.target is None) else last
        res = {}
        for mid, (e0, e1) in cands.items():
            seg = pen[:, e0:e1]; k, jj = np.unravel_index(int(seg.argmin()), seg.shape)
            if last[k, e0 + jj] < 1e8: res[mid] = (float(last[k, e0 + jj]), int(k), int(e0 + jj))
        if not res:
            self._raw = ('none', 0); self._move = 'none'; self._low += 1
            return self._verdict(t0, score=0.0, move_probs={'none': 1.0}, faults=self._lost_only(), **base)
        best = min(res, key=lambda m: res[m][0]); s_best = math.exp(-res[best][0] / self.tau)
        raw = best if s_best >= self.none_below else 'none'
        self._raw = (raw, self._raw[1] + 1 if raw == self._raw[0] else 1)
        if self._raw[1] >= 2: self._move = raw
        move = self._move if (self._move == 'none' or self._move in res) else raw
        use = best if move == 'none' else move
        d, k, j_end = res[use]; path = _backtrack(ch, k, j_end, n_ok)
        Ral, Rmal = self._aligned(cols, path, ok, n)
        score, probs = self._model_scores(X, Ral, Rmal)
        if move == 'none': score = min(score, s_best)      # 'none' was decided by the DTW distance: never report a clean-looking score with it
        self._low = self._low + 1 if score < 0.3 else 0
        probs = dict(probs); probs['lost'] = 1.0 if self._low >= self.lost_calls else 0.0
        offset = phase = None
        if move != 'none':
            m = next(mm for mm in self.moves if mm['id'] == move)
            phase = min(1.0, max(0.0, (col_t[j_end] - m['t0']) / max(m['t1'] - m['t0'], 1e-3)))
            if self.target is None and t_ref is not None:
                offset = max(-OFFSET_CLIP, min(OFFSET_CLIP, t_ref - col_t[j_end]))
                fit = min(1.0, max(0.0, (score - 0.5) / 0.2))     # timing cues need a decent fit: a rough learner matched to the wrong move is not 'late'
                probs['late'] = max(probs.get('late', 0.0), fit * min(1.0, max(0.0, (offset - 0.25) / 0.5)))
                probs['early'] = max(probs.get('early', 0.0), fit * min(1.0, max(0.0, (-offset - 0.25) / 0.5)))
        else: probs = {'lost': probs['lost']}
        faults = sorted(({'label': l, 'cue_text': CUE_TEXT[l], 'p': round(float(p), 3)} for l, p in probs.items() if p >= self.fault_p),
                        key=lambda f: -f['p'])[:2]
        sc = {m: math.exp(-r[0] / self.tau) for m, r in res.items()}; sc['none'] = 0.4
        z = np.array([sc[m] / 0.15 for m in sc]); z = np.exp(z - z.max()); z /= z.sum()
        probs_out = {m: round(float(p), 4) for m, p in zip(sc, z)}
        return self._verdict(t0, move=move, move_probs=probs_out, phase=phase, score=score, faults=faults, timing_offset_s=offset, **base)

    def _lost_only(self):
        return [{'label': 'lost', 'cue_text': CUE_TEXT['lost'], 'p': 1.0}] if self._low >= self.lost_calls else []

    def _verdict(self, t0, t_judge, ready, t_ref, done, confidence, move='none', move_probs=None, phase=None, score=0.0, faults=(),
                 timing_offset_s=None) -> Verdict:
        return Verdict(t_judge=float(t_judge), ready=bool(ready), move=str(move), move_probs=dict(move_probs or {'none': 1.0}),
                       phase=None if phase is None else round(float(phase), 3), score=round(float(score), 4), faults=list(faults),
                       timing_offset_s=None if timing_offset_s is None else round(float(timing_offset_s), 3),
                       t_ref=None if t_ref is None else round(float(t_ref), 3), done=bool(done), confidence=round(float(confidence), 3),
                       ms=round((time.perf_counter() - t0) * 1000.0, 3))

    def _window(self, fr):
        """Buffer -> X (win,17,3) [x,y,mask]: minus the hip centre, over the median torso of the window's ok frames; right-padded."""
        n = len(fr); xy = np.stack([f[1] for f in fr]); m = np.stack([f[2] for f in fr]); ok = np.array([f[3] for f in fr], bool)
        hip = (xy[:, 11] + xy[:, 12]) / 2; torso = np.linalg.norm((xy[:, 5] + xy[:, 6]) / 2 - hip, axis=1)
        s = max(float(np.median(torso[ok])), 1e-3) if ok.any() else 1.0
        X = np.zeros((self.win, 17, 3), np.float32); X[:n, :, :2] = (xy - hip[:, None]) / s; X[:n, :, 2] = m; X[:n][~m] = 0
        return X, ok

    def _candidates(self, t_ref):
        """DP columns (K=2,L,17,3) = [R, mirror(R)], their reference times, their move id (or None), and per candidate move the
        end-column range (e0, e1) in DP column indices. 'full': moves within +-CAND_S of the reference clock; 'learn': the target
        tiled three times so a looping learner always fits."""
        if self.target is not None:
            j0, j1 = self.cols[self.target]; L = j1 - j0; idx = np.tile(np.arange(j0, j1), 3)
            cols = np.stack([self.R[idx], self.Rm[idx]]); col_t = self.t_ref_cols[idx]
            return cols, col_t, [self.target] * len(idx), {self.target: (0, len(idx))}
        if t_ref is None: return None, None, None, {}
        lo, hi = t_ref - CAND_S, t_ref + CAND_S
        c0 = max(0, int(round((lo - BACK_S) * self.fps))); c1 = min(len(self.R), int(round(hi * self.fps)) + 1)
        if c1 - c0 < 2: return None, None, None, {}
        cands = {}
        for m in self.moves:
            j0, j1 = self.cols[m['id']]
            e0, e1 = max(j0, int(round(lo * self.fps)), c0), min(j1, c1)
            if m['t0'] < hi and m['t1'] > lo and e1 > e0: cands[m['id']] = (e0 - c0, e1 - c0)
        cols = np.stack([self.R[c0:c1], self.Rm[c0:c1]]); col_t = self.t_ref_cols[c0:c1]
        col_move = [next((m['id'] for m in self.moves if m['t0'] <= tt < m['t1']), None) for tt in col_t]
        return cols, col_t, col_move, cands

    @staticmethod
    def _cost(Xo, Mo, cols):
        """(n,17,2) learner, (n,17) mask, (K,L,17,3) reference -> (K,n,L) weighted mean joint distance over the jointly visible
        body joints (COCO 5..16); MISS_COST where none is."""
        diff = Xo[None, :, None, BODY, :] - cols[:, None, :, BODY, :2]
        dist = np.sqrt((diff * diff).sum(-1))
        w = (Mo[None, :, None, BODY] & (cols[:, None, :, BODY, 2] > 0)) * JOINT_W[BODY]
        ws = w.sum(-1)
        return np.where(ws > 0, (dist * w).sum(-1) / np.maximum(ws, 1e-6), MISS_COST).astype(np.float32)

    def _aligned(self, cols, path, ok, n):
        """Reference (and its mirror) re-sampled along the DTW path: (win,17,3) each, not-ok learner frames take the nearest ok
        frame's column, rows past the buffer are zero (mask 0)."""
        ok_idx = np.flatnonzero(ok); full = np.empty(n, int); full[ok_idx] = path
        if len(ok_idx) < n:
            miss = np.flatnonzero(~ok); near = ok_idx[np.abs(miss[:, None] - ok_idx[None, :]).argmin(1)]; full[miss] = full[near]
        Ral = np.zeros((self.win, 17, 3), np.float32); Rmal = Ral.copy()
        Ral[:n] = cols[0][full]; Rmal[:n] = cols[1][full]
        return Ral, Rmal

    # ---- scoring seam ----
    def _model_scores(self, X, R, Rm):
        """X (win,17,3) learner window [x,y,mask], R / Rm (win,17,3) the DTW-aligned reference move and its mirror ->
        (score in 0..1, {fault_label: p}) for the 7 learned labels (everything but 'lost'; late/early are then max-merged
        with the analytic timing rule in judge()). Baseline: analytic rules on the newest NEWEST frames. With model= the
        trained net (_NumpyNet over concat(X, R, Rm) (win,17,9), heads: score sigmoid + 7 fault sigmoids) replaces it;
        labels outside the checkpoint's model_faults (late, early) come back as 0 and the analytic rules fill them."""
        if self._net is not None: return self._net(X, R, Rm)
        return self._baseline_scores(X, R, Rm)

    def _load_model(self, path):
        """Checkpoint written by scripts/train_dance.py: torch.save of a plain dict {arch, state_dict, in_shape, faults,
        model_faults, norm, trained_on, metrics, created}. torch is imported here only to unpickle it (torch.load with
        weights_only=False); the weights are copied to numpy and inference never touches torch again."""
        p = path if os.path.exists(path) else os.path.join(ROOT, path)
        if not os.path.exists(p): raise FileNotFoundError(f'dance model {path!r} not found (cwd or {ROOT}); train it: scripts/train_dance.py')
        try: import torch
        except ImportError as e: raise ImportError(f'reading the dance model {p} needs torch (pip install torch): {e}') from None
        ck = torch.load(p, map_location='cpu', weights_only=False)
        if not isinstance(ck, dict) or 'arch' not in ck or not ('state_dict' in ck or 'state' in ck):
            raise ValueError(f'{p}: not a dance_judge checkpoint (expected a dict with arch and state_dict)')
        if int(ck['arch'].get('win', self.win)) != self.win or ck['arch'].get('kind', 'conv1d') != 'conv1d':
            raise ValueError(f"{p}: arch {ck['arch']} does not fit win={self.win}")
        return _NumpyNet(ck)

    def _baseline_scores(self, X, R, Rm):
        mX = X[:, :, 2] > 0; mR = R[:, :, 2] > 0; m = mX & mR; w = JOINT_W[None, :] * np.ones_like(X[:, :, 0])
        dR = np.linalg.norm(X[:, :, :2] - R[:, :, :2], axis=-1); dRm = np.linalg.norm(X[:, :, :2] - Rm[:, :, :2], axis=-1)
        d_all = min(_wmean(dR, m, w)[0], _wmean(dRm, m, w)[0]); score = math.exp(-d_all / self.tau)
        rows = np.flatnonzero(mX.any(1)); rows = rows[-NEWEST:]
        x, r, rm, mm, dr, drm = X[rows], R[rows], Rm[rows], m[rows], dR[rows], dRm[rows]
        p = {l: 0.0 for l in FAULTS[:7]}
        clip = lambda v: float(min(1.0, max(0.0, v)))
        # arms / legs: error of the limb joints against the closer of R and mirror(R)
        eA, nA = min(_wmean(dr[:, ARMS], mm[:, ARMS]), _wmean(drm[:, ARMS], mm[:, ARMS]), key=lambda z: z[0])[0], mm[:, ARMS].sum()
        if nA >= 8: p['arms'] = clip((eA - 0.3) / 0.5)
        eL = min(_wmean(dr[:, LEGS], mm[:, LEGS])[0], _wmean(drm[:, LEGS], mm[:, LEGS])[0])
        if mm[:, ANKLES].sum() >= 5: p['legs'] = clip((eL - 0.3) / 0.5)
        # arms_higher: learner wrists lower (image y down) than the reference wrists
        dy, ny = _wmean(x[:, WRISTS, 1] - r[:, WRISTS, 1], mm[:, WRISTS])
        if ny >= 4: p['arms_higher'] = clip((dy - 0.15) / 0.35)
        # bigger: arm extent from the shoulder centre and wrist path length, learner over reference
        shx = (x[:, 5, :2] + x[:, 6, :2]) / 2; shr = (r[:, 5, :2] + r[:, 6, :2]) / 2; ratios = []
        ex, _ = _wmean(np.linalg.norm(x[:, ARMS, :2] - shx[:, None], axis=-1), mm[:, ARMS])
        er, ne = _wmean(np.linalg.norm(r[:, ARMS, :2] - shr[:, None], axis=-1), mm[:, ARMS])
        if ne >= 8 and er > 0.3: ratios.append(ex / er)
        if len(rows) >= 4:
            both = mm[1:, WRISTS] & mm[:-1, WRISTS]
            mx, _ = _wmean(np.linalg.norm(x[1:, WRISTS, :2] - x[:-1, WRISTS, :2], axis=-1), both)
            mr, nb = _wmean(np.linalg.norm(r[1:, WRISTS, :2] - r[:-1, WRISTS, :2], axis=-1), both)
            if nb >= 4 and mr * nb > 1.0: ratios.append(mx / mr)
        if ratios: p['bigger'] = clip((0.85 - min(ratios)) / 0.3)
        # wrong_side: the mirrored reference fits the limbs better, on a limb group that is handed enough to tell
        mrm = mR[rows] & (rm[:, :, 2] > 0); d_rrm = np.linalg.norm(r[:, :, :2] - rm[:, :, :2], axis=-1)
        for grp in (ARMS, LEGS):                      # p = (share of the asymmetry explained) x (the gap is not just noise)
            asym = _wmean(d_rrm[:, grp], mrm[:, grp])[0]
            if asym >= 0.08 and mm[:, grp].sum() >= 8:
                gap = _wmean(dr[:, grp], mm[:, grp])[0] - _wmean(drm[:, grp], mm[:, grp])[0]
                p['wrong_side'] = max(p['wrong_side'], clip(gap / asym) * clip((gap - 0.04) / 0.10))
        return float(score), p


class DanceJudgeStub:
    """Same signatures as DanceJudge; judge() returns the neutral Verdict so the perception hook compiles without a choreo."""
    def __init__(self, choreo, fps=10, win=30, mode='full', model=None): self.n = 0; self.t = 0.0
    def set_ref_clock(self, t_ref, t): pass
    def push(self, img33, ok, t, hw): self.n += 1; self.t = float(t)
    def push_coco17(self, xy17, conf17, t, hw): self.push(None, False, t, hw)
    def judge(self) -> Optional[Verdict]:
        if self.n < MIN_OK: return None
        return Verdict(t_judge=self.t, ready=False, move='none', move_probs={'none': 1.0}, phase=None, score=0.0, faults=[],
                       timing_offset_s=None, t_ref=None, done=False, confidence=0.0, ms=0.0)


def replay_npz(npz, judge, every=3, transform=None):
    """Feed a YOLO npz (path, or a mapping with xy (N,17,2) pixels, conf (N,17), ok (N,), fps, width, height, mirrored) through
    judge.push_coco17 at judge.fps, calling judge.judge() every `every` frames; returns the list of Verdicts. A mirrored selfie
    clip is un-mirrored first so the learner stream is anatomical like MediaPipe's. transform(xy) -> xy perturbs the learner."""
    z = np.load(npz, allow_pickle=False) if isinstance(npz, (str, os.PathLike)) else npz
    xy = np.array(z['xy'], np.float32); conf = np.asarray(z['conf'], np.float32); ok = np.asarray(z['ok'], bool)
    w, h, fps_src = float(z['width']), float(z['height']), float(z['fps'])
    if bool(z['mirrored']): xy[:, :, 0] = w - xy[:, :, 0]; xy = xy[:, FLIP_IDX]; conf = conf[:, FLIP_IDX]
    if transform is not None: xy = transform(xy)
    n = int(math.floor((len(xy) - 1) * judge.fps / fps_src)) + 1
    idx = np.clip(np.round(np.arange(n) * fps_src / judge.fps).astype(int), 0, len(xy) - 1); out = []
    for k, i in enumerate(idx):
        judge.push_coco17(xy[i] if ok[i] else np.full((17, 2), np.nan, np.float32), conf[i], k / judge.fps, (int(h), int(w)))
        if (k + 1) % every == 0:
            v = judge.judge()
            if v is not None: out.append(v)
    return out
