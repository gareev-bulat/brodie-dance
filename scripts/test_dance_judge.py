#!/usr/bin/env python3
"""Checks for src/rtcoach/dance_judge.py (numpy only, no torch, no MediaPipe).

Replays the reference npz of a choreo through the DTW baseline as if it were a learner and checks:
  1. the reference itself -> the choreo's move ids in order, score >= 0.7 on most calls inside each move
  2. a horizontally mirrored copy (other-side learner) -> 'wrong_side' faults on the handed moves
  3. a 1.3x slowed copy -> positive timing_offset_s and 'late' faults
  4. a damped copy (limb amplitudes x0.6) -> 'bigger' faults
  5. random joints -> move 'none'
  6. every Verdict survives json.dumps
  7. p95 judge() time < 5 ms
  8. 'learn:<move>' mode on the reference -> that move while it is danced; DanceJudgeStub returns the neutral Verdict
  9. the live push() entry point (MediaPipe (33,5), x,y 0..1) agrees with push_coco17 on the same frames
Prints a compact (t, t_ref, move, score, offset, faults) table for the reference replay and PASS/FAIL per check; exit 1 on failure.

Run (WSL, brodie venv, repo root):
  ~/.venvs/brodie/bin/python scripts/test_dance_judge.py [--choreo data/dance/choreo/josh_king_o.json] [--npz <learner npz>] [--quiet]
"""
import argparse, json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'src'))
import numpy as np
from rtcoach.dance_judge import DanceJudge, DanceJudgeStub, FAULTS, CUE_TEXT, COCO2MP, FLIP_IDX, ARMS, LEGS, replay_npz

RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond))); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ''), flush=True)


def move_at(choreo, t_ref, grace=0.35):
    """Move ids acceptable at reference time t_ref: the one containing it, plus the previous one shortly after a boundary."""
    ok = set()
    for m in choreo['moves']:
        if m['t0'] <= t_ref < m['t1']: ok.add(m['id'])
        if m['t1'] <= t_ref < m['t1'] + grace: ok.add(m['id'])
    return ok


def ready_moves(vs):
    return [v for v in vs if v['ready'] and v['move'] != 'none']


def first_order(vs):
    seen = []
    for v in vs:
        if v['move'] != 'none' and v['move'] not in seen: seen.append(v['move'])
    return seen


def is_subsequence(seq, ref):
    it = iter(ref); return all(any(x == y for y in it) for x in seq)


def npz_dict(z, xy=None, conf=None, ok=None):
    return dict(xy=z['xy'] if xy is None else xy, conf=z['conf'] if conf is None else conf, ok=z['ok'] if ok is None else ok,
                fps=float(z['fps']), width=int(z['width']), height=int(z['height']), mirrored=bool(z['mirrored']))


def mirror_xy(w):
    def f(xy):
        xy = xy.copy(); xy[:, :, 0] = w - xy[:, :, 0]; return xy[:, FLIP_IDX]
    return f


def damp_xy(factor=0.6):
    def f(xy):
        xy = xy.copy(); sc = (xy[:, 5] + xy[:, 6]) / 2; hc = (xy[:, 11] + xy[:, 12]) / 2
        for j in ARMS: xy[:, j] = sc + factor * (xy[:, j] - sc)
        for j in LEGS: xy[:, j] = hc + factor * (xy[:, j] - hc)
        return xy
    return f


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--choreo', default=os.path.join(ROOT, 'data/dance/choreo/josh_king_o.json'))
    ap.add_argument('--npz', default='', help='learner npz (default: the choreo reference itself)')
    ap.add_argument('--quiet', action='store_true', help='skip the per-call table')
    a = ap.parse_args()
    with open(a.choreo) as f: choreo = json.load(f)
    npz_path = a.npz or os.path.join(ROOT, choreo['npz'])
    z = np.load(npz_path, allow_pickle=False); w, h = int(z['width']), int(z['height'])
    ids = [m['id'] for m in choreo['moves']]; last_t1 = max(m['t1'] for m in choreo['moves'])
    all_v, wall = [], []

    def run(label, npz, mode='full', transform=None):
        j = DanceJudge(a.choreo, fps=10, win=30, mode=mode)
        t0 = time.perf_counter(); vs = replay_npz(npz, j, every=3, transform=transform); wall.append(time.perf_counter() - t0)
        all_v.extend(vs)
        rm = ready_moves(vs)
        print(f"-- {label}: {len(vs)} verdicts, {sum(v['ready'] for v in vs)} ready, {len(rm)} with a move; "
              f"mean score on moves {np.mean([v['score'] for v in rm]) if rm else 0:.3f}", flush=True)
        return vs

    # 1. the reference as learner
    vs = run('reference replay', npz_path)
    if not a.quiet:
        print(f"{'t':>5} {'t_ref':>6} {'rdy':>3} {'move':<11} {'score':>5} {'phase':>5} {'off':>5}  faults")
        for v in vs:
            fl = ' '.join(f"{f['label']}:{f['p']:.2f}" for f in v['faults'])
            ph = '-' if v['phase'] is None else f"{v['phase']:.2f}"; off = '-' if v['timing_offset_s'] is None else f"{v['timing_offset_s']:+.2f}"
            print(f"{v['t_judge']:5.1f} {v['t_ref'] if v['t_ref'] is not None else -1:6.2f} {int(v['ready']):>3} {v['move']:<11} {v['score']:5.2f} {ph:>5} {off:>5}  {fl}")
    order = first_order(vs)
    inside = [v for v in vs if v['ready'] and v['t_ref'] is not None and v['t_ref'] < last_t1]
    good = [v for v in inside if v['move'] in move_at(choreo, v['t_ref']) and v['score'] >= 0.7]
    frac = len(good) / max(1, len(inside))
    danced = [i for i in ids if i != ids[0]]      # the count-in move is over before the window is ready
    check('1 reference replay: moves in choreo order', is_subsequence(order, ids) and all(i in order for i in danced),
          f"order {order}")
    check('1 reference replay: right move with score >= 0.7 on most ready calls', frac >= 0.7, f"{len(good)}/{len(inside)} = {frac:.2f}")
    false_faults = sum(1 for v in good for f in v['faults'])
    check('1 reference replay: no faults on the clean take', false_faults <= 0.1 * max(1, len(good)), f"{false_faults} fault entries on {len(good)} clean calls")
    check('1 reference replay: done after the last move', vs[-1]['done'] is True or vs[-1]['t_ref'] <= last_t1 + 1.5, f"last t_ref {vs[-1]['t_ref']}, done {vs[-1]['done']}")

    # 2. mirrored learner
    vm = run('mirrored copy', npz_path, transform=mirror_xy(w)); rm = ready_moves(vm)
    ws = [v for v in rm if any(f['label'] == 'wrong_side' for f in v['faults'])]
    handed = [v for v in rm if v['move'] in ('arm_pump', 'swing_down', 'point_up', 'sway_point')] or rm
    check('2 mirrored copy yields wrong_side', len(ws) >= 0.4 * max(1, len(handed)) and len(ws) >= 3, f"{len(ws)} wrong_side of {len(handed)} handed-move calls")

    # 3. slowed learner (1.3x)
    n_slow = int(len(z['xy']) * 1.3); idx = np.clip(np.round(np.arange(n_slow) / 1.3).astype(int), 0, len(z['xy']) - 1)
    vsl = run('1.3x slowed copy', npz_dict(z, z['xy'][idx], z['conf'][idx], z['ok'][idx])); rm = ready_moves(vsl)
    late_off = [v for v in rm if v['timing_offset_s'] is not None and v['timing_offset_s'] > 0]
    late_f = [v for v in rm if any(f['label'] == 'late' for f in v['faults'])]
    check('3 slowed copy: positive timing offset on most move calls', len(late_off) >= 0.6 * max(1, len(rm)), f"{len(late_off)}/{len(rm)} positive, {len(late_f)} 'late' faults")
    check("3 slowed copy: 'late' faults appear", len(late_f) >= 0.2 * max(1, len(rm)) and len(late_f) >= 3, f"{len(late_f)}/{len(rm)}")

    # 4. damped limbs
    vd = run('damped copy x0.6', npz_path, transform=damp_xy(0.6)); rm = ready_moves(vd)
    big = [v for v in rm if any(f['label'] == 'bigger' for f in v['faults'])]
    check("4 damped copy yields 'bigger'", len(big) >= 0.4 * max(1, len(rm)) and len(big) >= 3, f"{len(big)}/{len(rm)}")

    # 5. noise
    rng = np.random.default_rng(0); N = len(z['xy'])
    noise = np.stack([rng.uniform(0, w, (N, 17)), rng.uniform(0, h, (N, 17))], -1).astype(np.float32)
    vn = run('random noise', npz_dict(z, noise, np.full((N, 17), 0.9, np.float32), np.ones(N, bool)))
    none_frac = np.mean([v['move'] == 'none' for v in vn]) if vn else 0
    check("5 random noise -> move 'none'", none_frac >= 0.9, f"{none_frac:.2f} of {len(vn)} calls")

    # 8. learn mode + stub
    target = 'swing_down' if 'swing_down' in ids else danced[-1]; mv = next(m for m in choreo['moves'] if m['id'] == target)
    vl = run(f'learn:{target}', npz_path, mode=f'learn:{target}')
    during = [v for v in vl if v['ready'] and mv['t0'] + 1.0 <= v['t_judge'] < mv['t1'] + 0.3]
    hit = [v for v in during if v['move'] == target]
    check(f'8 learn:{target} recognises the move while danced', len(hit) >= 0.6 * max(1, len(during)), f"{len(hit)}/{len(during)}; offsets all None: {all(v['timing_offset_s'] is None for v in vl)}")
    st = DanceJudgeStub(a.choreo)
    for i in range(12): st.push(None, False, i / 10, (h, w))
    sv = st.judge(); check('8 DanceJudgeStub neutral verdict', sv is not None and sv['move'] == 'none' and not sv['ready'] and json.dumps(sv))

    # 9. the live entry point: MediaPipe-shaped (33,5) input through push() must agree with push_coco17 on the same frames
    xy = z['xy'].copy(); conf = z['conf'].copy()
    if bool(z['mirrored']): xy[:, :, 0] = w - xy[:, :, 0]; xy = xy[:, FLIP_IDX]; conf = conf[:, FLIP_IDX]
    jm, jc = DanceJudge(a.choreo), DanceJudge(a.choreo); idx = np.round(np.arange(0, len(xy), 3)).astype(int); agree = tot = 0
    for k, i in enumerate(idx):
        img33 = np.zeros((33, 5), np.float32); img33[COCO2MP, 0] = xy[i, :, 0] / w; img33[COCO2MP, 1] = xy[i, :, 1] / h; img33[COCO2MP, 3] = conf[i]
        img33[np.isnan(img33)] = 0.0
        jm.push(img33 if z['ok'][i] else None, bool(z['ok'][i]), k / 10, (h, w)); jc.push_coco17(xy[i], conf[i], k / 10, (h, w))
        if (k + 1) % 3 == 0:
            vm_, vc_ = jm.judge(), jc.judge()
            if vm_ is not None and vc_ is not None:
                tot += 1; agree += vm_['move'] == vc_['move'] and abs(vm_['score'] - vc_['score']) < 1e-3
                all_v.append(vm_)
    check('9 push() (MediaPipe (33,5)) agrees with push_coco17 on the same frames', tot > 0 and agree == tot, f"{agree}/{tot} calls")

    # 6. JSON
    bad = 0
    for v in all_v:
        try: json.dumps(v)
        except TypeError: bad += 1
    keys = {'t_judge', 'ready', 'move', 'move_probs', 'phase', 'score', 'faults', 'timing_offset_s', 't_ref', 'done', 'confidence', 'ms'}
    check('6 every Verdict passes json.dumps with the contract keys', bad == 0 and all(set(v) == keys for v in all_v), f"{len(all_v)} verdicts, {bad} failures")
    check('6 fault labels and cue texts match the contract', all(f['label'] in FAULTS and f['cue_text'] == CUE_TEXT[f['label']] and 0 <= f['p'] <= 1 and len(v['faults']) <= 2 for v in all_v for f in v['faults']))

    # 7. latency
    ms = np.array([v['ms'] for v in all_v]); p95 = float(np.percentile(ms, 95)) if len(ms) else 0.0
    check('7 p95 judge() time < 5 ms', p95 < 5.0, f"p50 {np.median(ms):.2f} ms, p95 {p95:.2f} ms, max {ms.max():.2f} ms over {len(ms)} calls; replay wall {sum(wall):.2f} s")

    n_fail = sum(1 for _, ok in RESULTS if not ok)
    print(f"\n{len(RESULTS) - n_fail}/{len(RESULTS)} checks passed")
    sys.exit(1 if n_fail else 0)


if __name__ == '__main__':
    main()
