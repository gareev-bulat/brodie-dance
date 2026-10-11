#!/usr/bin/env python3
"""Replay a pose npz through the dance judge (baseline and, with --model, the trained net) and report what it said.

Input npz: a YOLO reference (scripts/extract_pose_yolo.py: xy pixels with NaN, conf, ok, fps, width, height, mirrored) or a
MediaPipe take (scripts/extract_landmarks.py: img (N,33,5), ok, t, width, height). Takes go through the live entry point
judge.push(img33, ok, t, hw) (--entry mp, the default when 'img' is present); YOLO clips go through replay_npz (push_coco17).
Both un-mirror a clip flagged mirrored (a selfie recording that was flipped). --flip mirrors the learner on top of that (a
non-mirrored clip copied by a learner who mirrors the screen reads as wrong_side otherwise; the script warns). --perturb applies the scripts/test_dance_judge.py transforms to a
YOLO clip: mirror (expect wrong_side), slow (1.3x slower, expect late), damp (limbs x0.6, expect bigger), noise (expect none).

Printed: a per-call timeline (t, t_ref, move, score, offset, faults) for the baseline and the model side by side, then per run
  - fraction of ready in-move calls whose move matches the choreo at t_ref (full mode; the move containing t_ref or the previous
    one within 0.35 s), mean score per move, fault counts, p95 judge() ms, json.dumps check
  - with --perturb: the fraction of move calls that carry the expected fault (does the model see the perturbation?)
  - with labels (data/dance/labels.csv rows whose clip is this npz's stem): precision/recall of the faults at p >= 0.5 over
    the labelled windows (window counts toward a row when t_judge is inside [t_start_s, t_end_s]) and Spearman of score vs grade.
Several npz paths (or a glob) aggregate the labelled metrics (e.g. the held-out performer's takes).

Run (WSL, brodie venv, repo root):
  ~/.venvs/brodie/bin/python scripts/eval_dance.py data/dance/pose/josh_king_o_1791679083441.npz --model data/models/dance_judge.pt
  ~/.venvs/brodie/bin/python scripts/eval_dance.py data/dance/pose/josh_king_o_1791679083441.npz --perturb damp --quiet
  ~/.venvs/brodie/bin/python scripts/eval_dance.py "data/dance/takes/josh_king_o__sam__*.npz" --mode learn:swing_down --model data/models/dance_judge.pt
Options: --choreo (default data/dance/choreo/josh_king_o.json), --mode full|learn:<move_id>, --every 3, --entry auto|mp|coco,
--labels PATH, --flip, --quiet (no timeline), --t0 S (set_ref_clock(0, S): the reference starts at take second S).
"""
import argparse, csv, glob, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from rtcoach.dance_judge import DanceJudge, FAULTS, MODEL_FAULTS, FLIP_IDX, replay_npz
from test_dance_judge import mirror_xy, damp_xy, npz_dict, move_at

EXPECT = {'mirror': 'wrong_side', 'slow': 'late', 'damp': 'bigger', 'noise': None, 'none': None}


def spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or a.std() == 0 or b.std() == 0: return float('nan')
    return float(np.corrcoef(a.argsort().argsort(), b.argsort().argsort())[0, 1])


def perturbed(z, kind, w, h):
    """Learner stream variants of a YOLO npz as scripts/test_dance_judge.py builds them: (npz mapping, transform)."""
    if kind == 'mirror': return npz_dict(z), mirror_xy(w)
    if kind == 'damp': return npz_dict(z), damp_xy(0.6)
    if kind == 'slow':
        n = int(len(z['xy']) * 1.3); idx = np.clip(np.round(np.arange(n) / 1.3).astype(int), 0, len(z['xy']) - 1)
        return npz_dict(z, z['xy'][idx], z['conf'][idx], z['ok'][idx]), None
    if kind == 'noise':
        rng = np.random.default_rng(0); N = len(z['xy'])
        return npz_dict(z, np.stack([rng.uniform(0, w, (N, 17)), rng.uniform(0, h, (N, 17))], -1).astype(np.float32), np.full((N, 17), 0.9, np.float32), np.ones(N, bool)), None
    return npz_dict(z), None


def run_take(z, judge, every, flip, t0_ref):
    """MediaPipe take through the live entry point. Returns verdicts."""
    img, ok, t = z['img'], np.asarray(z['ok'], bool), np.asarray(z['t'], float); hw = (int(z['height']), int(z['width'])); out = []
    if t0_ref is not None: judge.set_ref_clock(0.0, float(t0_ref))
    for k in range(len(img)):
        a = img[k]
        if flip and ok[k]:   # mirror the 33 MediaPipe landmarks: x -> 1-x and swap the left/right pairs
            a = a.copy(); a[:, 0] = 1.0 - a[:, 0]; sw = np.arange(33); sw[1:11] = [4, 5, 6, 1, 2, 3, 8, 7, 10, 9]; sw[11:33] = [i + 1 if i % 2 else i - 1 for i in range(11, 33)]; a = a[sw]
        judge.push(a if ok[k] else None, bool(ok[k]), float(t[k]), hw)
        if (k + 1) % every == 0:
            v = judge.judge()
            if v is not None: out.append(v)
    return out


def summarise(vs, choreo, mode, expect):
    mv = [v for v in vs if v['ready'] and v['move'] != 'none']; last_t1 = max(m['t1'] for m in choreo['moves'])
    s = {'calls': len(vs), 'ready': sum(v['ready'] for v in vs), 'with_move': len(mv), 'mean_score_moves': float(np.mean([v['score'] for v in mv])) if mv else 0.0}
    if mode == 'full':
        inside = [v for v in vs if v['ready'] and v['t_ref'] is not None and v['t_ref'] < last_t1]
        s['right_move_frac'] = float(np.mean([v['move'] in move_at(choreo, v['t_ref']) for v in inside])) if inside else float('nan'); s['in_move_calls'] = len(inside)
    per = {}
    for v in mv: per.setdefault(v['move'], []).append(v['score'])
    s['score_per_move'] = {m: round(float(np.mean(x)), 3) for m, x in per.items()}
    fc = {}
    for v in vs:
        for f in v['faults']: fc[f['label']] = fc.get(f['label'], 0) + 1
    s['fault_counts'] = fc; ms = np.array([v['ms'] for v in vs]) if vs else np.zeros(1)
    s['ms_p50'], s['ms_p95'] = round(float(np.median(ms)), 2), round(float(np.percentile(ms, 95)), 2)
    s['json_ok'] = all(isinstance(json.dumps(v), str) for v in vs)
    if expect: s[f'expected_{expect}_frac'] = round(float(np.mean([any(f['label'] == expect for f in v['faults']) for v in mv])), 3) if mv else 0.0
    return s


def labelled(vs, rows):
    """Windows inside labelled rows -> fault P/R at p >= 0.5 (labels in MODEL_FAULTS + late/early) and Spearman(score, grade)."""
    tp = fp = fn = 0; sc, gr = [], []; per = {l: [0, 0, 0] for l in FAULTS[:7]}
    for r in rows:
        t0, t1 = float(r['t_start_s']), float(r['t_end_s']); tags = {s.strip() for s in r.get('faults', '').replace(',', ';').replace(' ', ';').split(';') if s.strip()}
        grade = float(r['grade']) if r.get('grade', '').strip() else None; off = r.get('offset_s', '').strip()
        labels = list(MODEL_FAULTS) + (['late', 'early'] if off else [])          # timing is labelled only on designed-late takes
        if off: tags |= {l for l, c in (('late', float(off) > 0.25), ('early', float(off) < -0.25)) if c}
        for v in vs:
            if not (t0 <= v['t_judge'] <= t1): continue
            p = {f['label']: f['p'] for f in v['faults']}
            for l in labels:
                pred, pos = p.get(l, 0.0) >= 0.5, l in tags
                per[l][0] += pred and pos; per[l][1] += pred and not pos; per[l][2] += (not pred) and pos
            if grade is not None: sc.append(v['score']); gr.append(grade)
    for l, (a, b, c) in per.items(): tp += a; fp += b; fn += c
    return {'windows': len(sc), 'fault_precision': round(tp / max(tp + fp, 1), 3), 'fault_recall': round(tp / max(tp + fn, 1), 3),
            'per_label': {l: {'tp': a, 'fp': b, 'fn': c} for l, (a, b, c) in per.items() if a + b + c}, 'spearman_score_grade': spearman(sc, gr) if sc else float('nan')}


def timeline(vb, vm):
    print(f"{'t':>5} {'t_ref':>6} | {'base move':<11} {'score':>5} {'off':>5}  faults{'':<22}| {'model move':<11} {'score':>5}  faults")
    for b, m in zip(vb, vm or [None] * len(vb)):
        fl = lambda v: ' '.join(f"{f['label']}:{f['p']:.2f}" for f in v['faults']); off = '-' if b['timing_offset_s'] is None else f"{b['timing_offset_s']:+.2f}"
        left = f"{b['t_judge']:5.1f} {b['t_ref'] if b['t_ref'] is not None else -1:6.2f} | {b['move']:<11} {b['score']:5.2f} {off:>5}  {fl(b):<28}"
        print(left + (f"| {m['move']:<11} {m['score']:5.2f}  {fl(m)}" if m else ''))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('npz', nargs='+', help='pose npz file(s) or glob(s)'); ap.add_argument('--choreo', default=os.path.join(ROOT, 'data/dance/choreo/josh_king_o.json'))
    ap.add_argument('--model', default=None); ap.add_argument('--mode', default='full'); ap.add_argument('--every', type=int, default=3)
    ap.add_argument('--entry', default='auto', choices=['auto', 'mp', 'coco']); ap.add_argument('--perturb', default='none', choices=list(EXPECT))
    ap.add_argument('--labels', default=os.path.join(ROOT, 'data/dance/labels.csv')); ap.add_argument('--flip', action='store_true')
    ap.add_argument('--t0', type=float, default=None); ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    files = [f for pat in a.npz for f in (sorted(glob.glob(pat)) or [pat])]
    with open(a.choreo) as f: choreo = json.load(f)
    rows_all = []
    if os.path.exists(a.labels):
        with open(a.labels, newline='') as f: rows_all = list(csv.DictReader(f))
    agg = {'baseline': [], 'model': []}; expect = EXPECT[a.perturb]
    for path in files:
        if not os.path.exists(path): print(f'missing: {path}'); continue
        z = np.load(path, allow_pickle=False); stem = os.path.splitext(os.path.basename(path))[0]; w, h = int(z['width']), int(z['height'])
        entry = a.entry if a.entry != 'auto' else ('mp' if 'img' in z.files else 'coco')
        if entry == 'coco' and not bool(z['mirrored']) and choreo.get('learner_mirrors_screen', True) and not a.flip and a.perturb != 'mirror':
            print(f'note: {stem} is not a mirrored clip but the choreo expects the learner to mirror the screen; replaying it anatomically reads as wrong_side (pass --flip to copy as a mirror)')
        runs = {'baseline': None, 'model': a.model} if a.model else {'baseline': None}; vs = {}
        flip_mp = a.flip != bool(z['mirrored'])            # a mirrored (selfie-flipped) take is un-mirrored like replay_npz does; --flip toggles
        if entry == 'mp' and bool(z['mirrored']): print(f'note: {stem} is flagged mirrored; un-mirroring the landmarks' + (' (cancelled by --flip)' if a.flip else ''))
        for name, model in runs.items():
            j = DanceJudge(a.choreo, fps=10, win=30, mode=a.mode, model=model)
            if entry == 'mp': vs[name] = run_take(z, j, a.every, flip_mp, a.t0)
            else:
                npz, tr = perturbed(z, a.perturb, w, h)
                if a.flip: tr = (lambda f: (lambda xy: f(mirror_xy(w)(xy))))(tr) if tr else mirror_xy(w)
                if a.t0 is not None: j.set_ref_clock(0.0, a.t0)
                vs[name] = replay_npz(npz, j, every=a.every, transform=tr)
        print(f"\n== {stem}  ({entry} entry, {a.mode}, perturb {a.perturb}, {len(vs['baseline'])} calls)")
        if not a.quiet: timeline(vs['baseline'], vs.get('model'))
        rows = [r for r in rows_all if os.path.splitext(os.path.basename(r['clip'].strip()))[0] == stem]
        for name in runs:
            s = summarise(vs[name], choreo, a.mode, expect); print(f'-- {name}: ' + json.dumps(s))
            if rows: lab = labelled(vs[name], rows); agg[name].append(lab); print(f'   labels ({len(rows)} rows): ' + json.dumps(lab))
    if len(files) > 1 and any(agg.values()):
        for name, labs in agg.items():
            if not labs: continue
            tp = sum(x['tp'] for l in labs for x in l['per_label'].values()); fp = sum(x['fp'] for l in labs for x in l['per_label'].values()); fn = sum(x['fn'] for l in labs for x in l['per_label'].values())
            print(f"== aggregate {name}: {sum(l['windows'] for l in labs)} labelled windows, fault precision {tp / max(tp + fp, 1):.3f} recall {tp / max(tp + fn, 1):.3f}, "
                  f"mean Spearman {np.nanmean([l['spearman_score_grade'] for l in labs]):.3f}")


if __name__ == '__main__':
    main()
