#!/usr/bin/env python3
"""Train the dance judge's scoring net (docs/DANCE_MODEL_INTERFACE.md section 6) and write data/models/dance_judge.pt.

What it does
  1. Dataset. For every choreo in data/dance/choreo/*.json the reference clip is replayed through the real DanceJudge (same
     normalisation, DTW alignment and window cut as live: the judge's own code, nothing re-implemented) as a synthetic learner:
       clean         the reference itself                         -> score ~1, no fault
       damp          limbs x0.5..0.75 toward shoulder/hip centre  -> 'bigger'
       arms_higher   wrists/elbows lowered by 0.15..0.4 torso      -> 'arms_higher'
       arms          smooth random arm offsets 0.3..0.6 torso      -> 'arms' (+ 'arms_higher' when they went down)
       legs          smooth random knee/ankle offsets              -> 'legs' (only where the reference ankles are visible)
       wrong_side    the learner flipped left<->right              -> 'wrong_side' (label masked on symmetric moves)
       still/random  one held pose / uniform noise                 -> score by geometry / 0, faults masked
     Every replay gets live-domain augmentation: MediaPipe-like jitter and per-joint bias, body proportions 0.9..1.12,
     per-joint dropout at the live miss rates, frame dropout bursts, a time shift and speed change (augmentation only:
     late/early stay analytic). Each judge() call yields one training window (X, R, Rm) (30,17,3) each, exactly what
     DanceJudge._model_scores receives at inference.
     Real takes: data/dance/takes/*.npz (scripts/extract_landmarks.py) + data/dance/labels.csv with columns
     clip, t_start_s, t_end_s, move, grade (0-3), faults (';'-separated labels), offset_s (blank = unknown). Each row is replayed
     in 'learn:<move>' mode; score target grade/3. Performer = the middle token of <dance>__<performer>__<take> (the first token
     for two-part names); one performer is held out (--holdout, default the alphabetically last). Runs with zero takes.
  2. Model: rtcoach.dance_judge.build_torch_net (3 x Conv1d over 30 frames of concat(X, R, Rm, X-R, X-Rm, |X-R|, |X-Rm|, |R-Rm|) per
     joint, masked mean/max/newest pooling, heads: score sigmoid + 7 fault logits; late/early are trained to 0 and ignored at
     inference, 'lost' is analytic). BCE on both heads, AdamW + one-cycle LR, CUDA when available; 10 epochs take ~30 s.
  3. Acceptance gate (the contract's "swapped in only if better"): on the held-out synthetic replays the net must reach fault
     precision >= 0.7, no fault on >= 90% of clean windows and a fault recall >= the baseline's; with real held-out takes also
     precision > 0.7 and clean > 0.9 there. A net that fails is saved as <out>.rejected.pt (pass --force to overwrite <out>).
     Small datasets get more epochs automatically so the optimiser takes at least --min-steps (600) steps: 4 epochs over
     3k windows (48 steps) learn nothing and would otherwise ship a checkpoint that scores the clean reference 0.55.
  4. Saves a plain dict {arch, state_dict, in_shape, faults, model_faults, norm, trained_on, metrics, created} with torch.save;
     then loads it back through DanceJudge(model=...) (numpy inference), checks the numpy forward against torch, replays the
     first choreo's reference and prints score, faults and p95 latency.

Run (WSL, brodie venv, repo root):
  ~/.venvs/brodie/bin/python scripts/train_dance.py                       # all choreos, synthetic (+ labelled takes if present)
  ~/.venvs/brodie/bin/python scripts/train_dance.py --choreo data/dance/choreo/josh_king_o.json --epochs 10 --reps 2
  ~/.venvs/brodie/bin/python scripts/train_dance.py --dry-run             # build the dataset, print counts, no training
Options: --out PATH (default data/models/dance_judge.pt), --every N (judge stride on synthetic replays, 2), --reps R (replays per
kind per choreo x R, 1.5), --epochs 10, --min-steps 600, --d 96, --lr 2e-3, --batch 256, --val-frac 0.15 (held-out synthetic
replays), --holdout PERFORMER, --real-weight 3, --seed 0, --cpu, --force (write --out even when the gate rejects the net).
"""
import argparse, csv, glob, json, math, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'src'))
import numpy as np
from rtcoach.dance_judge import (DanceJudge, load_choreo, build_torch_net, net_arch, FAULTS, MODEL_FAULTS, FLIP_IDX, ARMS, LEGS,
                                 WRISTS, ANKLES, LIMBS, CORE, NEWEST, _resolve)

LABELS = list(FAULTS[:7]); LI = {l: i for i, l in enumerate(LABELS)}
KINDS = {'clean': 6, 'damp': 4, 'arms_higher': 4, 'arms': 4, 'legs': 3, 'wrong_side': 4, 'still': 2, 'random': 2}  # replays per choreo
DROP_P = {7: .03, 8: .03, 9: .06, 10: .06, 13: .12, 14: .12, 15: .2, 16: .2}      # live per-joint miss rates (takes/_smoke/run3.json)
MODEL_IDX = [LI[l] for l in MODEL_FAULTS]


class Recorder(DanceJudge):
    """DanceJudge that keeps every (X, R, Rm) it would have scored, with the baseline's score and fault probabilities."""
    def __init__(self, *a, **k): super().__init__(*a, **k); self.rec = []

    def _model_scores(self, X, R, Rm):
        s, p = self._baseline_scores(X, R, Rm); self.rec.append((X.copy(), R.copy(), Rm.copy(), s, dict(p))); return s, p


# ---------------------------------------------------------------- synthetic learner streams (pixels, NaN = joint not visible)
def ref_stream(ch, z):
    """The reference clip in learner space: the flip load_choreo applied to build R, so a learner copying perfectly produces it."""
    xy = np.array(z['xy'], np.float32); ok = np.asarray(z['ok'], bool).copy(); w, h = float(z['width']), float(z['height'])
    mirrored = bool(ch.get('mirrored', bool(z['mirrored']) if 'mirrored' in z.files else False))
    if mirrored or bool(ch.get('learner_mirrors_screen', True)): xy[:, :, 0] = w - xy[:, :, 0]; xy = xy[:, FLIP_IDX]
    ok &= ~np.isnan(xy[:, CORE]).any((1, 2))
    return xy, ok, float(z['fps']), w, h


def resample(xy, ok, fps_src, fps, speed, shift):
    """Learner frame k (time k/fps) shows the reference at source time (k/fps - shift) * speed, clamped to the clip."""
    T = (len(xy) - 1) / fps_src; K = int(math.floor((T / speed + max(shift, 0.0)) * fps)) + 1
    ts = np.clip((np.arange(K) / fps - shift) * speed, 0.0, T); idx = np.clip(np.round(ts * fps_src).astype(int), 0, len(xy) - 1)
    return xy[idx].copy(), ok[idx].copy(), ts


def torso_px(xy, ok):
    t = np.linalg.norm((xy[:, 5] + xy[:, 6]) / 2 - (xy[:, 11] + xy[:, 12]) / 2, axis=1)
    return float(np.nanmedian(t[ok])) if ok.any() and np.isfinite(t[ok]).any() else 100.0


def scale_limbs(xy, fa, fl):
    xy = xy.copy(); sc = (xy[:, 5] + xy[:, 6]) / 2; hc = (xy[:, 11] + xy[:, 12]) / 2
    for j in ARMS: xy[:, j] = sc + fa * (xy[:, j] - sc)
    for j in LEGS: xy[:, j] = hc + fl * (xy[:, j] - hc)
    return xy


def smooth_offsets(rng, K, n, amp, tau=8):
    """(K, n, 2) slowly varying random offsets with RMS `amp` (a learner doing the wrong thing smoothly, not jitter)."""
    v = rng.normal(size=(K + 2 * tau, n, 2)); kern = np.ones(tau) / tau
    v = np.apply_along_axis(lambda a: np.convolve(a, kern, 'same'), 0, v)[tau:tau + K]
    return v / max(float(np.sqrt((v ** 2).mean())), 1e-6) * amp


def limb_dist(a, b, s):
    d = np.linalg.norm(a[:, LIMBS] - b[:, LIMBS], axis=-1) / s; m = np.isfinite(d)
    return np.where(m.any(1), np.where(m, d, 0).sum(1) / np.maximum(m.sum(1), 1), np.nan)


def augment(rng, xy, ok, s, sev):
    """Live-domain noise on a pixel stream: proportions, per-joint bias, per-frame jitter, joint dropout runs, frame dropout."""
    K = len(xy); xy = scale_limbs(xy, rng.uniform(0.9, 1.12), rng.uniform(0.9, 1.12))
    xy = xy + rng.normal(0, 0.03 * s, (1, 17, 2)) + rng.normal(0, (0.015 + 0.045 * sev) * s, (K, 17, 2))
    drop = np.zeros((K, 17), bool)
    for j, p in DROP_P.items():
        d = False
        for k in range(K): d = rng.random() < 0.7 if d else rng.random() < p; drop[k, j] = d
    xy[drop] = np.nan; ok = ok.copy()
    for _ in range(rng.poisson(0.005 * K)):
        a = rng.integers(0, K); ok[a:a + rng.integers(1, 9)] = False
    return xy, ok


def make_stream(rng, kind, xy0, ok0, fps_src, w, h, fps=10):
    """One synthetic learner replay. Returns xy (K,17,2), ok (K,), src_t (K,), per-frame label helpers, severity."""
    T = (len(xy0) - 1) / fps_src; dmax = 0.06 / max(T, 1.0) * 15.0                  # keep the drift under ~1 s per clip
    speed = float(np.exp(rng.uniform(-dmax, dmax))); shift = float(rng.uniform(-0.4, 0.4))
    xy, ok, src_t = resample(xy0, ok0, fps_src, fps, speed, shift); K = len(xy); s = torso_px(xy, ok)
    clean = xy.copy(); dy = np.zeros(K, np.float32); dstill = np.full(K, np.nan, np.float32); sev = float(rng.uniform(0, 1))
    if kind == 'damp': f = 0.75 - 0.25 * sev; xy = scale_limbs(xy, f, f)
    elif kind == 'arms_higher':
        d = 0.15 + 0.25 * sev; xy[:, WRISTS, 1] += d * s; xy[:, [7, 8], 1] += 0.6 * d * s; dy[:] = d
    elif kind == 'arms':
        off = smooth_offsets(rng, K, 4, (0.3 + 0.3 * sev) * s); xy[:, ARMS] += off; dy[:] = off[:, 2:, 1].mean(1) / s
    elif kind == 'legs': xy[:, LEGS] += smooth_offsets(rng, K, 4, (0.3 + 0.3 * sev) * s)
    elif kind == 'wrong_side': xy[:, :, 0] = w - xy[:, :, 0]; xy = xy[:, FLIP_IDX]
    elif kind == 'still':
        i = rng.choice(np.flatnonzero(ok)); xy = np.repeat(xy[i:i + 1], K, 0); ok = np.ones(K, bool); dstill = limb_dist(xy, clean, s)
    elif kind == 'random':
        xy = np.stack([rng.uniform(0, w, (K, 17)), rng.uniform(0, h, (K, 17))], -1).astype(np.float32); ok = np.ones(K, bool)
    ank = ok & np.isfinite(clean[:, ANKLES]).all((1, 2))
    if kind not in ('random',): xy, ok = augment(rng, xy, ok, s, sev if kind == 'clean' else rng.uniform(0, 1))
    return xy, ok, src_t, dict(dy=dy, dstill=dstill, ank=ank), sev


def replay_clean(ch, path, judge, every=3, fps=10):
    """The reference as a perfect learner (ref_stream, clock at clip time) through judge.push_coco17 -> verdicts."""
    xy, ok, fps_src, w, h = ref_stream(ch, np.load(_resolve(ch['npz'], path), allow_pickle=False)); xy, ok, _ = resample(xy, ok, fps_src, fps, 1.0, 0.0)
    judge.set_ref_clock(0.0, 0.0); out = []
    for k in range(len(xy)):
        judge.push_coco17(xy[k] if ok[k] else np.full((17, 2), np.nan, np.float32), None, k / fps, (int(h), int(w)))
        if (k + 1) % every == 0 and (v := judge.judge()) is not None: out.append(v)
    return out


def labels_for(kind, sev, st):
    """(y (7,), w (7,), score target) from the perturbation kind and the window's geometry (newest NEWEST ok frames)."""
    y, w = np.zeros(7, np.float32), np.ones(7, np.float32); clip = lambda v, lo, hi: float(min(hi, max(lo, v)))
    if kind == 'clean': score = 1.0 - 0.12 * sev
    elif kind == 'damp': y[LI['bigger']] = 1; score = 0.65 - 0.3 * sev
    elif kind == 'arms_higher': y[LI['arms_higher']] = 1; score = 0.65 - 0.3 * sev
    elif kind == 'arms': y[LI['arms']] = 1; score = 0.6 - 0.3 * sev
    elif kind == 'legs': y[LI['legs']] = 1; score = 0.65 - 0.3 * sev
    elif kind == 'wrong_side':          # handed enough to tell (mean |R - mirror R| over the limbs, torso units) -> positive, weighted up with
        a = st['asym']; y[LI['wrong_side']] = float(a >= 0.12)                             # the asymmetry; near-symmetric -> ignored
        w[LI['wrong_side']] = 0.5 + 0.5 * clip((a - 0.12) / 0.13, 0, 1) if a >= 0.12 else float(a < 0.06)
        w[LI['arms']] = 0.5 if a >= 0.12 else float(a < 0.06)        # the specific cue ('Other side') wins over 'Check your arms'
        score = clip(0.9 - 2.0 * a, 0.45, 0.9)
    elif kind == 'still': w[:] = 0; score = 0.0 if not np.isfinite(st['dstill']) else math.exp(-st['dstill'] / 0.35)
    else: w[:] = 0; score = 0.0
    if kind in ('arms', 'arms_higher'):
        d = st['dy']; y[LI['arms_higher']] = float(d >= 0.15); w[LI['arms_higher']] = float(d >= 0.15 or d <= 0.06)
    if kind == 'legs' and st['ank'] < 5: w[LI['legs']] = 0
    return y, w, clip(score, 0.0, 1.0)


def collect_synthetic(paths, every, reps, seed, fps=10):
    """Replay every choreo's reference under every perturbation kind; one row per judge() call."""
    rows = []; rng0 = np.random.default_rng(seed); n_rep = 0
    for path in paths:
        ch = load_choreo(path, fps); z = np.load(_resolve(ch['npz'], path), allow_pickle=False); name = ch.get('dance', os.path.basename(path))
        xy0, ok0, fps_src, w, h = ref_stream(ch, z)
        R, Rm = ch['ref'], ch['ref_m']; m = (R[:, LIMBS, 2] > 0) & (Rm[:, LIMBS, 2] > 0)
        asym_ref = np.where(m.any(1), (np.linalg.norm(R[:, LIMBS, :2] - Rm[:, LIMBS, :2], axis=-1) * m).sum(1) / np.maximum(m.sum(1), 1), 0.0)
        ank_frac = float(np.isfinite(xy0[ok0][:, ANKLES]).all((1, 2)).mean()) if ok0.any() else 0.0
        plan = [k for k, n in KINDS.items() for _ in range(max(1, int(round(n * reps)))) if not (k == 'legs' and ank_frac < 0.5)]
        t0 = time.time(); n0 = len(rows)
        for kind in plan:
            rng = np.random.default_rng(rng0.integers(1 << 31)); n_rep += 1
            xy, ok, src_t, st, sev = make_stream(rng, kind, xy0, ok0, fps_src, w, h, fps)
            asym_k = asym_ref[np.clip(np.round(src_t * fps).astype(int), 0, len(asym_ref) - 1)]
            j = Recorder(ch, fps=fps, win=30, mode='full'); j.set_ref_clock(0.0, 0.0)
            for k in range(len(xy)):
                j.push_coco17(xy[k] if ok[k] else np.full((17, 2), np.nan, np.float32), None, k / fps, (int(h), int(w)))
                if (k + 1) % every: continue
                n_rec = len(j.rec); v = j.judge()
                if v is None or len(j.rec) == n_rec: continue
                idx = np.arange(max(0, k - 29), k + 1); idx = idx[ok[idx]][-NEWEST:]
                if len(idx) == 0: continue
                stats = dict(dy=float(st['dy'][idx].mean()), asym=float(asym_k[idx].mean()), ank=int(st['ank'][idx].sum()),
                             dstill=float(np.nanmean(st['dstill'][idx])) if np.isfinite(st['dstill'][idx]).any() else float('nan'))
                y, wgt, sc = labels_for(kind, sev, stats); X, Ral, Rmal, bs, bp = j.rec[-1]
                rows.append(dict(x=np.concatenate([X, Ral, Rmal], -1).astype(np.float16), y=y, w=wgt, score=sc, kind=kind, group=f'{name}#{n_rep}',
                                 dance=name, base_score=bs, base_p=np.array([bp.get(l, 0.0) for l in LABELS], np.float32), real=False))
        print(f'  {name}: {len(plan)} replays -> {len(rows) - n0} windows ({time.time() - t0:.1f} s; ankles visible {ank_frac:.0%})', flush=True)
    return rows


# ---------------------------------------------------------------- real takes
def performer_of(stem):
    p = stem.split('__'); return p[1] if len(p) >= 3 else p[0]


def collect_real(labels_csv, takes_dir, choreos, every=1, fps=10):
    """labels.csv rows -> windows from the MediaPipe take npz, replayed in learn:<move> mode through the live push() entry."""
    rows, missing = [], set()
    with open(labels_csv, newline='') as f: table = list(csv.DictReader(f))
    for r in table:
        stem = os.path.splitext(os.path.basename(r['clip'].strip()))[0]; npz = os.path.join(takes_dir, stem + '.npz')
        dance = stem.split('__')[0]; ch = choreos.get(dance) if dance in choreos else (next(iter(choreos.values())) if len(choreos) == 1 else None)
        if not os.path.exists(npz) or ch is None: missing.add(stem); continue
        z = np.load(npz, allow_pickle=False); img, ok, t = z['img'], np.asarray(z['ok'], bool), np.asarray(z['t'], float); hw = (int(z['height']), int(z['width']))
        ids = [m['id'] for m in ch['moves']]; move = r['move'].strip(); t0, t1 = float(r['t_start_s']), float(r['t_end_s'])
        target = move if move in ids else next(m['id'] for m in ch['moves'] if m['t0'] >= float(ch.get('count_in_s', 0)) or m is ch['moves'][-1])
        grade = float(r['grade']) if r.get('grade', '').strip() != '' else (0.0 if move == 'none' else 3.0)
        tags = {s.strip() for s in r.get('faults', '').replace(',', ';').replace(' ', ';').replace('|', ';').split(';') if s.strip()}
        y, w = np.zeros(7, np.float32), np.ones(7, np.float32); off = r.get('offset_s', '').strip()
        for l in tags:
            if l in LI: y[LI[l]] = 1
        if off == '': w[LI['late']] = w[LI['early']] = 0
        else: y[LI['late']], y[LI['early']] = float(float(off) > 0.25), float(float(off) < -0.25)
        if move not in ids: w[:] = 0
        j = Recorder(ch, fps=fps, win=30, mode=f'learn:{target}'); sel = np.flatnonzero((t >= t0 - 3.0) & (t <= t1))
        for n, i in enumerate(sel):
            j.push(img[i] if ok[i] else None, bool(ok[i]), float(t[i]), hw)
            if (n + 1) % every or t[i] < t0: continue
            n_rec = len(j.rec); v = j.judge()
            if v is None or len(j.rec) == n_rec: continue
            X, Ral, Rmal, bs, bp = j.rec[-1]
            rows.append(dict(x=np.concatenate([X, Ral, Rmal], -1).astype(np.float16), y=y.copy(), w=w.copy(), score=grade / 3.0, kind=f'real:{move}',
                             group=performer_of(stem), dance=ch.get('dance', dance), base_score=bs, base_p=np.array([bp.get(l, 0.0) for l in LABELS], np.float32),
                             real=True, grade=grade))
    if missing: print(f'  labels.csv: skipped {len(missing)} clips without npz/choreo: {sorted(missing)[:6]}', flush=True)
    return rows


# ---------------------------------------------------------------- metrics
def spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or a.std() == 0 or b.std() == 0: return float('nan')
    ra, rb = a.argsort().argsort().astype(float), b.argsort().argsort().astype(float)
    return float(np.corrcoef(ra, rb)[0, 1])


def metrics(score, pf, rows):
    """score (N,), pf (N,7) probabilities, rows -> dict: score MAE/Spearman, per-label P/R at 0.5, clean no-fault rate."""
    y = np.stack([r['y'] for r in rows]); w = np.stack([r['w'] for r in rows]); tgt = np.array([r['score'] for r in rows])
    out = {'n': len(rows), 'score_mae': float(np.abs(score - tgt).mean()), 'score_spearman': spearman(score, tgt)}
    tp = fp = fn = 0
    for l in MODEL_FAULTS:
        i = LI[l]; m = w[:, i] > 0; pred = pf[m, i] >= 0.5; pos = y[m, i] > 0.5
        a, b, c = int((pred & pos).sum()), int((pred & ~pos).sum()), int((~pred & pos).sum()); tp += a; fp += b; fn += c
        out[l] = {'precision': round(a / max(a + b, 1), 3), 'recall': round(a / max(a + c, 1), 3), 'pos': int(pos.sum()), 'n': int(m.sum())}
    out['fault_precision'] = round(tp / max(tp + fp, 1), 3); out['fault_recall'] = round(tp / max(tp + fn, 1), 3)
    clean = np.array([r['kind'] == 'clean' or (r['real'] and r.get('grade', 0) >= 3 and r['y'].sum() == 0) for r in rows])
    out['clean_no_fault'] = round(float((pf[clean][:, MODEL_IDX].max(1) < 0.5).mean()), 3) if clean.any() else float('nan')
    if any(r['real'] for r in rows):
        rr = np.array([r['real'] for r in rows]); out['real_spearman_grade'] = spearman(score[rr], [r['grade'] for r in rows if r['real']])
    return out


def fmt(m):
    per = ' '.join(f"{l}:{m[l]['precision']:.2f}/{m[l]['recall']:.2f}" for l in MODEL_FAULTS if l in m)
    return (f"n={m['n']} score MAE {m['score_mae']:.3f} rho {m['score_spearman']:.3f} | faults P {m['fault_precision']:.2f} R {m['fault_recall']:.2f} "
            f"clean-no-fault {m['clean_no_fault']:.2f} | {per}" + (f" | real rho(grade) {m['real_spearman_grade']:.3f}" if 'real_spearman_grade' in m else ''))


# ---------------------------------------------------------------- training
def train(rows_tr, rows_va, arch, a, dev):
    import torch, torch.nn.functional as F
    torch.manual_seed(a.seed); net = build_torch_net(arch).to(dev)
    pack = lambda rows: (torch.tensor(np.stack([r['x'] for r in rows])).to(dev), torch.tensor(np.array([r['score'] for r in rows], np.float32)).to(dev),
                         torch.tensor(np.stack([r['y'] for r in rows])).to(dev), torch.tensor(np.stack([r['w'] for r in rows])).to(dev))
    Xtr, Str, Ytr, Wtr = pack(rows_tr); Xva = pack(rows_va)[0] if rows_va else None
    steps = max(1, math.ceil(len(rows_tr) / a.batch)) * a.epochs
    opt = torch.optim.AdamW(net.parameters(), lr=a.lr, weight_decay=1e-4); sched = torch.optim.lr_scheduler.OneCycleLR(opt, a.lr, total_steps=steps, pct_start=0.2)

    def predict(X):
        net.eval(); s, f = [], []
        with torch.no_grad():
            for i in range(0, len(X), 1024): sl, fl = net(X[i:i + 1024].float()); s.append(torch.sigmoid(sl)); f.append(torch.sigmoid(fl))
        return torch.cat(s).cpu().numpy(), torch.cat(f).cpu().numpy()

    g = torch.Generator(device='cpu').manual_seed(a.seed); t0 = time.time()
    for ep in range(a.epochs):
        net.train(); perm = torch.randperm(len(rows_tr), generator=g).to(dev); tot = 0.0
        for i in range(0, len(perm), a.batch):
            b = perm[i:i + a.batch]; sl, fl = net(Xtr[b].float())
            loss = F.binary_cross_entropy_with_logits(sl, Str[b]) + (F.binary_cross_entropy_with_logits(fl, Ytr[b], reduction='none') * Wtr[b]).sum() / Wtr[b].sum().clamp(min=1.0)
            opt.zero_grad(); loss.backward(); opt.step(); sched.step(); tot += loss.item() * len(b)
        msg = f'epoch {ep + 1}/{a.epochs} loss {tot / len(rows_tr):.4f} ({time.time() - t0:.0f} s)'
        if rows_va: msg += ' | val ' + fmt(metrics(*predict(Xva), rows_va))
        print(msg, flush=True)
    return net, predict


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--choreo', nargs='*', default=None, help='choreo json(s); default data/dance/choreo/*.json')
    ap.add_argument('--takes', default=os.path.join(ROOT, 'data/dance/takes')); ap.add_argument('--labels', default=os.path.join(ROOT, 'data/dance/labels.csv'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'data/models/dance_judge.pt'))
    ap.add_argument('--every', type=int, default=2); ap.add_argument('--reps', type=float, default=1.5); ap.add_argument('--epochs', type=int, default=10)
    ap.add_argument('--d', type=int, default=96); ap.add_argument('--lr', type=float, default=2e-3); ap.add_argument('--batch', type=int, default=256)
    ap.add_argument('--val-frac', type=float, default=0.15); ap.add_argument('--holdout', default=None); ap.add_argument('--real-weight', type=int, default=3)
    ap.add_argument('--seed', type=int, default=0); ap.add_argument('--cpu', action='store_true'); ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--min-steps', type=int, default=600, help='raise --epochs until the optimiser takes at least this many steps')
    ap.add_argument('--force', action='store_true', help='write --out even when the acceptance gate rejects the net')
    a = ap.parse_args()
    paths = a.choreo or sorted(glob.glob(os.path.join(ROOT, 'data/dance/choreo/*.json')))
    if not paths: sys.exit('no choreo files found (data/dance/choreo/*.json)')
    print(f'choreos: {[os.path.basename(p) for p in paths]}', flush=True)
    t0 = time.time(); rows = collect_synthetic(paths, a.every, a.reps, a.seed)
    choreos = {ch['dance']: ch for ch in (load_choreo(p, 10) for p in paths)}
    real = collect_real(a.labels, a.takes, choreos) if os.path.exists(a.labels) else []
    takes = sorted(glob.glob(os.path.join(a.takes, '*.npz')))
    if not real: print(f'real takes: NONE used ({len(takes)} npz in {a.takes}, labels.csv {"found" if os.path.exists(a.labels) else "absent"}): training on synthetic data only', flush=True)
    rng = np.random.default_rng(a.seed); groups = sorted({r['group'] for r in rows}); va_groups = set(rng.choice(groups, int(round(a.val_frac * len(groups))), replace=False))
    tr, va = [r for r in rows if r['group'] not in va_groups], [r for r in rows if r['group'] in va_groups]
    holdout = None; real_tr = real_va = []
    if real:
        perfs = sorted({r['group'] for r in real}); holdout = a.holdout or (perfs[-1] if len(perfs) > 1 else None)
        real_tr = [r for r in real if r['group'] != holdout]; real_va = [r for r in real if r['group'] == holdout]
        print(f'real takes: {len(real)} windows from performers {perfs}; held out {holdout!r} ({len(real_va)} windows); train windows x{a.real_weight}', flush=True)
        tr += real_tr * a.real_weight
    counts = {k: sum(r['kind'] == k for r in rows) for k in KINDS}
    print(f'dataset: {len(rows)} synthetic windows {counts} + {len(real)} real; train {len(tr)} / val {len(va)} ({time.time() - t0:.0f} s)', flush=True)
    base_va = metrics(np.array([r['base_score'] for r in va]), np.stack([r['base_p'] for r in va]), va) if va else {}
    if va: print('baseline on val: ' + fmt(base_va), flush=True)
    if a.dry_run: return
    import torch
    dev = 'cuda' if (torch.cuda.is_available() and not a.cpu) else 'cpu'; arch = net_arch(a.d, 30); print(f'training on {dev}: arch {arch}', flush=True)
    spe = max(1, math.ceil(len(tr) / a.batch))
    if spe * a.epochs < a.min_steps:
        a.epochs = math.ceil(a.min_steps / spe); print(f'small dataset: {spe} optimiser steps/epoch -> {a.epochs} epochs to reach --min-steps {a.min_steps}', flush=True)
    net, predict = train(tr, va, arch, a, dev)
    Xva = torch.tensor(np.stack([r['x'] for r in va])).to(dev) if va else None
    m_va = metrics(*predict(Xva), va) if va else {}
    m_real = {}
    if real_va:
        Xr = torch.tensor(np.stack([r['x'] for r in real_va])).to(dev); m_real = metrics(*predict(Xr), real_va)
        b_real = metrics(np.array([r['base_score'] for r in real_va]), np.stack([r['base_p'] for r in real_va]), real_va)
        print(f'held-out performer {holdout}: model ' + fmt(m_real) + '\n                       baseline ' + fmt(b_real), flush=True)
        m_real['baseline'] = b_real; m_real['score_swap_recommended'] = bool(m_real.get('real_spearman_grade', -1) > b_real.get('real_spearman_grade', -1))
    # acceptance gate: better than the baseline on the held-out synthetic replays (and the contract's bar on real held-out takes)
    why = []
    if va:
        if m_va['fault_precision'] < 0.7: why.append(f"val fault precision {m_va['fault_precision']:.3f} < 0.7")
        if not (m_va['clean_no_fault'] >= 0.9): why.append(f"val clean-no-fault {m_va['clean_no_fault']:.3f} < 0.9" + (' (no clean windows in val)' if m_va['clean_no_fault'] != m_va['clean_no_fault'] else ''))
        if m_va['fault_recall'] < base_va['fault_recall']: why.append(f"val fault recall {m_va['fault_recall']:.3f} < baseline {base_va['fault_recall']:.3f}")
        if m_va['score_spearman'] < base_va['score_spearman']: why.append(f"val score rho {m_va['score_spearman']:.3f} < baseline {base_va['score_spearman']:.3f}")
    if m_real:
        if m_real['fault_precision'] <= 0.7: why.append(f"held-out real fault precision {m_real['fault_precision']:.2f} <= 0.7")
        if m_real['clean_no_fault'] == m_real['clean_no_fault'] and m_real['clean_no_fault'] <= 0.9: why.append(f"held-out real clean-no-fault {m_real['clean_no_fault']:.2f} <= 0.9")
    accepted = not why; out = a.out if (accepted or a.force) else os.path.splitext(a.out)[0] + '.rejected.pt'
    print(f"MODEL VERDICT: {'ACCEPTED' if accepted else 'REJECTED (' + '; '.join(why) + ')'}" + ('' if accepted or a.force else f'; saving as {out} (pass --force to write {a.out})'), flush=True)
    ck = dict(arch=arch, state_dict={k: v.detach().cpu() for k, v in net.state_dict().items()}, in_shape=[30, 17, 9], faults=LABELS, model_faults=list(MODEL_FAULTS),
              norm={'kind': 'identity', 'units': 'torso (hip-centred, median-torso scaled, mask 0/1)', 'note': 'DanceJudge._window normalises; the net adds X-R, X-Rm'},
              trained_on=dict(choreos=[os.path.relpath(p, ROOT) for p in paths], windows=len(rows), kinds=counts, every=a.every, reps=a.reps, real_windows=len(real),
                              real_performers=sorted({r['group'] for r in real}), holdout_performer=holdout, synthetic_only=not real, epochs=a.epochs, lr=a.lr, d=a.d, seed=a.seed),
              metrics=dict(val_synthetic=m_va, val_synthetic_baseline=base_va, heldout_real=m_real, accepted=accepted, rejected_because=why), created=time.strftime('%Y-%m-%dT%H:%M:%S'))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True); torch.save(ck, out)
    print(f'saved {out} ({os.path.getsize(out) / 1024:.0f} KB, {sum(v.numel() for v in ck["state_dict"].values())} parameters)', flush=True)
    # load test: the judge's numpy forward against torch, then a live-style replay with the model
    j = DanceJudge(paths[0], fps=10, win=30, mode='full', model=out); chk = va[:64] or tr[:64]
    s_t, f_t = predict(torch.tensor(np.stack([r['x'] for r in chk])).to(dev)); worst = 0.0
    for r, st, ft in zip(chk, s_t, f_t):
        x = r['x'].astype(np.float32); s_n, p_n = j._net(x[..., :3], x[..., 3:6], x[..., 6:9])
        worst = max(worst, abs(s_n - float(st)), max(abs(p_n[l] - float(ft[LI[l]])) for l in MODEL_FAULTS))
    print(f'numpy forward vs torch on {len(chk)} windows: max abs diff {worst:.2e} ({"OK" if worst < 1e-3 else "MISMATCH"})', flush=True)
    ch0 = j.choreo; vs = replay_clean(ch0, paths[0], j, every=3); mv = [v for v in vs if v['ready'] and v['move'] != 'none']
    ms = np.array([v['ms'] for v in vs]); faults = {}
    for v in mv:
        for f in v['faults']: faults[f['label']] = faults.get(f['label'], 0) + 1
    print(f"model replay of the clean {ch0['dance']} reference: {len(vs)} verdicts, {len(mv)} with a move, mean score {np.mean([v['score'] for v in mv]) if mv else 0:.3f}, "
          f"faults {faults}, judge() p50 {np.median(ms):.2f} ms p95 {np.percentile(ms, 95):.2f} ms; json ok: {all(json.dumps(v) for v in vs)}", flush=True)


if __name__ == '__main__':
    main()
