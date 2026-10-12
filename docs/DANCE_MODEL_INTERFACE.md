# Dance model interface: Steven's judge <-> Brodie

Contract v2 (2026-10-10). Code claims cite `path:line` in `brodie-starter`.

**Status 2026-10-11:** implemented on branch `steven`: `src/rtcoach/dance_judge.py` (DTW baseline + optional model), `scripts/test_dance_judge.py` (15 checks), `scripts/extract_landmarks.py` (learner takes, MediaPipe), `scripts/train_dance.py`, `scripts/eval_dance.py`, `data/models/dance_judge.pt` (trained on synthetic perturbations of 10 references; passes the swap-in gate against the baseline on synthetic data only). Ten choreographies in `data/dance/choreo/`, reference pose data in `data/dance/pose/`. Still missing: real learner takes in `data/dance/takes/` plus `data/dance/labels.csv`; until then run with the baseline (`RTCOACH_DANCE_MODEL` unset).

## 1. Summary

`DanceJudge` (Steven's `src/rtcoach/dance_judge.py`) takes the MediaPipe landmarks Brodie already computes, aligns a 3 s window with the reference choreography and reports every 0.3 s the move, its quality, faults and lateness. An env-gated hook in `facetime/perception.py` emits a `dance` event; `starter/handlers.py` speaks faults via `say_now`. A numpy DTW baseline comes first; the trained model replaces its heads behind the same class.

## 2. Input contract

Brodie passes raw MediaPipe at 10 fps (`facetime/pipeline.py:68`); the judge projects, masks and normalises.
- `img33` `float32 (33,5) = [x, y, z, visibility, presence]`, x,y in 0..1 (`src/rtcoach/pose.py:15`), `None` without a pose (`pipeline.py:172`), off-frame joints exceed 0..1; `ok`; `t` = `self._t` and `hw` = `rgb.shape[:2]` (`facetime/perception.py:197-199`).
- `kps17 = img33[COCO2MP]`, `COCO2MP = [0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]` (mediapipe 1.1.0 verified). Both detectors label anatomically on an un-mirrored image (`scripts/extract_pose_yolo.py:35-37`): no swap.
- Mask: `visibility < 0.3` (`facetime/visibility.py:17-24`), x,y outside [0,1] or `not ok` -> value 0, mask 0 (`feats.py:22`); masked hip or shoulder -> frame not ok. Normalise in pixels: minus hip centre, over the median torso of the window's ok frames (`feats.py:12-16`); under 10 ok frames: `ready=False`.
- Reference: npz `xy` pixels (`extract_pose_yolo.py:13-29`); if `mirrored`, un-mirrored at load (`:39`, `:70`) = the dancer's anatomical pose. Convention `learner_mirrors_screen: true` (choreo key): the learner's right does what screen-right does, so a non-mirrored clip is mirrored too; `false` = copy anatomically.
- Tensors: `X float32 (30,17,3) = [x, y, mask]`, right-padded with mask 0 like `model.py:29-30`; `R`, `Rm` = the DTW-aligned reference move and its mirror (the net never sees the offset).

## 3. Output contract

```python
FAULTS = ('arms_higher', 'arms', 'legs', 'bigger', 'wrong_side', 'late', 'early', 'lost')
class Fault(TypedDict): label: str; cue_text: str; p: float
class Verdict(TypedDict):          # JSON-native values only; json.dumps is the test
    t_judge: float                 # newest frame, judge clock
    ready: bool                    # >=10 ok frames, >=8/12 body joints unmasked in >=15
    move: str                      # id or 'none'; must win 2 consecutive calls
    move_probs: dict[str, float]   # softmax(score/0.15) with a fixed 'none' score 0.4
    phase: float | None            # 0..1 inside move
    score: float                   # exp(-d/tau): >=0.7 clean, 0.4-0.7 rough
    faults: list[Fault]            # p >= 0.3, desc, max 2, newest 1 s
    timing_offset_s: float | None  # learner t minus matched ref t (+ late), clipped 1.5
    t_ref: float | None            # reference clock (clip seconds)
    done: bool                     # past the last move's t1 + 1.5 s
    confidence: float              # visible-joint fraction, halved if <80% frames ok
    ms: float
```

Every sink does `json.dumps` (`perception.py:116-117`, `worker.py:561`, `replay_local.py:63`); a numpy scalar raises, `_emit` only logs it (`:174-176`), the event vanishes. Example: `{...,"type":"dance","t_judge":12.3,"ready":true,"move":"clap_wave","move_probs":{"clap_wave":0.85,"none":0.15},"phase":0.6,"score":0.55,"faults":[{"label":"arms_higher","cue_text":"Arms higher","p":0.7}],"timing_offset_s":0.3,"t_ref":4.1,"done":false,"confidence":0.9,"ms":2.1}`.

`cue_text` = `cues.json["dance - <label>"]["cue"]`: "Arms higher", "Check your arms", "Watch your legs", "Bigger moves", "Other side", "Catch up", "Wait for the beat", "Watch and try again". `legs` never fires on dance 1 (reference ankles NaN in 98-100% of frames); `lost` (score < 0.3 on 6 consecutive calls) is analytic, not a model label. Budget: p95 < 5 ms per call on the Mac CPU (pose ~27 ms, `docs/STARTER_EVENTS.md:100`).

## 4. Python interface

```python
# src/rtcoach/dance_judge.py  (numpy; torch only when model= is given)
class DanceJudge:
    def __init__(self, choreo: str, fps=10, win=30, mode='full', model: str | None = None): ...  # mode 'full' | 'learn:<move_id>'
    def set_ref_clock(self, t_ref: float, t: float): ...  # override: reference at t_ref when judge clock is t
    def push(self, img33, ok: bool, t: float, hw: tuple[int, int]): ...
    def judge(self) -> Verdict | None: ...                # None until 10 frames
```

`DanceJudgeStub` (same signatures; `judge()` returns the neutral Verdict: `ready=False`, `move='none'`, `score=0.0`, `faults=[]`, rest None/False/0.0) compiles the hook today. Only the hook reaches the judge (`starter/handlers.py:19`): `RTCOACH_DANCE_MODE` (default `full`) and `RTCOACH_DANCE_MODEL` (unset = baseline) configure it. Reference clock: the learner's first ok frame is taken as the reference's first visible frame (clip second `ref_start` = the first move's `t0`, normally 0), so the first danced move is expected at clip second `count_in_s`; a handler that counts in and then starts the music must call `set_ref_clock(0.0, t_music_start)`. The handler builds its own summary from `dance` events. Choreo `data/dance/choreo/<dance>.json`: `{dance, npz, mirrored, learner_mirrors_screen, bpm, count_in_s, moves: [{id, name, t0, t1}], thresholds}` (`t0/t1` clip seconds; vocabulary = ids + `'none'`).

## 5. Integration into Brodie

The hook lives in Perception (every frame, before `session.on_frame`, `perception.py:203-205`) and speaks through `handlers.py` (`worker.py:521-539`; `voice.py:610-612`), bypassing the Session hold that drops Coach corrections without a counter (`session.py:145-165`).

1. `perception.py:34`: add `'dance'` to `EVENT_TYPES`.
2. `perception.py:128-158`: build `self.judge` from the env vars when `RTCOACH_DANCE` is set (else `None`); set `self.pipe.vis = Visibility(fps, no_pose_s=1e9, partial_s=1e9)` (`visibility.py:29`): kicks and jumps would trigger "Step back" (`:47-51`), outranking handler cues (`audio_out.py:243`).
3. `perception.py:197-199`: `self._hw = rgb.shape[:2]` before `self.pipe.process(...)`.
4. Append after line 247 at method-body indentation (8 spaces; 247 is inside the `probe_fire` block, `perception.py:239-247`):
   ```python
           if self.judge is not None:
               try:
                   self.judge.push(self.pipe.last_landmarks, r['ok'], self._t, self._hw)
                   if self._frame % 3 == 0 and (v := self.judge.judge()) is not None: self._emit('dance', **v)
               except Exception as e: log.warning('dance judge: %r', e)   # uncaught: ends the run (worker.py:486-489)
   ```
5. Handlers run on the event loop (`handlers.py:15`); state in a module dict (`BUILD_ON_BRODIE.md:193`). Speak `faults[0]['cue_text']` via `say_now(text, 'correction')` when `ready`, `confidence >= 0.5`, the same label at `p >= 0.5` twice, >= 3 s since any line, >= 8 s since that label (`pipeline.py:43`); `say_now("That's it", 'positive')` after 3 consecutive `score >= 0.7`, >= 8 s apart. Priorities (`audio_out.py:32`, `:239-257`): `'correction'` (2) replaces pending praise and waits behind a count; `'positive'`/`'other'` (1) drop while anything is pending. On `done` or the T-pose `gesture` (`--gesture-next`, `session.py:215-217`) `ask_voice` the means; that needs `--voice gemini` + `GEMINI_API_KEY` (`voice.py:589-592`) and `--floor open`: single mode never leaves `active` (`session.py:220-225`; `voice.py:51`).
6. Live: `RTCOACH_DANCE=data/dance/choreo/<dance>.json python -m facetime.worker --exercise "high knees" --model '' --trigger none --no-announce --params '{"start_line": false, "praise_after": 0}' --voice gemini --floor open --gesture-next dev`. `--exercise` nulls the plan (`worker.py:152-154`); `'high knees'` is an uncounted `LR2CLF` key (`src/rtcoach/exmap.py:9`); `--model ''` removes the classifier (`pipeline.py:111`); `persona.md` must say it is a dance (events say 'high knees', `perception.py:280`).
7. Bank: add `"dance - arms_higher": {"cue": "Arms higher", "kind": "fault"}` and the other seven to `src/rtcoach/cues.json` (no en/em dashes, `cues_tts.py:65-66`); render with `python -m facetime.cues_tts --backend gemini` (`--dry-run` first; `GEMINI_API_KEY`, plus `GEMINI_BASE_URL` for the team token, `.env.example:5-6`, `cues_tts.py:105-108`). Freeze strings, render, restart (manifest loads once, `audio_out.py:105`).
8. Offline: `python -m facetime.replay_local --video take.mp4 --exercise "high knees" --trigger none --thr '{}' --params '{"thr": 1.1, "praise_after": 0, "start_line": false, "every": 1000000}' --no-announce --fast --events out.jsonl`. `--events` is required (`replay_local.py:60-65`); `--model ''` crashes at `:81`, so `thr 1.1` and `every 1e6` silence the classifier (`coach.py:134`, `:89`). Then the harness, `BUILD_ON_BRODIE.md:178-189`.
9. Tests and docs: `scripts/test_perception.py` must still pass with `RTCOACH_DANCE` unset (`:84` checks `EVENT_TYPES`); set, every `dance` line must json-load with the Verdict keys. Add `dance` to `docs/STARTER_EVENTS.md:24`.

No permission to edit `perception.py` (`README.md:73`)? `starter/run_dance.py` monkeypatches the same two methods and `EVENT_TYPES` at module level (job processes re-import it, `worker.py:178-184`), then calls `facetime.worker.main()`.

## 6. Training data Steven produces

- Reference: `data/dance/pose/<clip>.npz` (`josh_king_o_...`: track 2, `mirrored=True`, ankles NaN 0.98/1.0; track 1 has 65% ankles), 2 moves.
- Takes in the live domain: phone page, portrait, whole body, 2-3 m (`docs/STARTER_README.md:279-281`), same transport as the demo (Chert landscape masks far more joints, `pipeline.py:72-78`); `scripts/extract_landmarks.py` runs live MediaPipe (`DANCE_TRAINING_PLAYBOOK.md` section 4, never YOLO) into `data/dance/takes/<dance>__<performer>__<take>.npz`.
- `labels.csv`: `clip, t_start_s, t_end_s, move, grade(0-3), faults, offset_s`; `offset_s` only on designed-late takes (+0.5 s), else masked. 3 performers x 2 moves x 3 takes (clean, mirrored, small) + 1 negative = ~24 attempts, one performer held out.
- Synthetic set (the bulk): damp limbs -> bigger; raise wrist targets -> arms_higher; jitter -> arms; flip X only -> wrong_side; still/random -> none; joint dropout at live rates; time shift and warp are augmentations only.
- Model: from scratch (not from `clf_pose.pt`, `README.md:94`); input `concat(X, R, Rm) (30,17,9)`; heads: score (sigmoid, target `grade/3` on real takes) and 7 fault logits (BCE); `timing_offset_s` and `lost` stay analytic; saved to `data/models/dance_judge.pt` as a plain dict `{arch, state_dict, in_shape, faults, model_faults, norm, trained_on, metrics{..., accepted, rejected_because}, created}`. `train_dance.py` applies the swap-in gate itself (held-out fault precision >= 0.7, clean-no-fault >= 0.9, recall and Spearman >= baseline; on real takes precision > 0.7) and prints `MODEL VERDICT: ACCEPTED|REJECTED`; a rejected net goes to `dance_judge.rejected.pt` and never overwrites the shipped file. Accept on the held-out performer: fault precision > 0.7 at p >= 0.5, no fault on > 90% of clean windows; `score` swapped only if its Spearman with `grade` beats the baseline.

## 7. Baseline first (no training)

Numpy, ~200 lines. Load: un-mirror, resample, normalise, cut `R_m` and `mirror(R_m)` per move. `judge()`: candidates = target and its mirror (learn) or moves within +-1.5 s of the ref clock (full); slope-constrained subsequence DTW (steps (1,1), (1,2), (2,1): the path never runs faster than 2x or slower than 0.5x), cost over jointly unmasked body joints (COCO 5-16) with per-joint weights, vectorised in numpy across the reference and its mirror; `score = exp(-d/tau)`, `tau` ~0.35; `move` = argmin if score >= 0.4 else `'none'`; `phase`, `timing_offset_s` from the matched index; faults by the section 3 rules on the newest 10 pairs. With `model=`, the net replaces `score` and `faults[].p` after alignment; `model=None` falls back.

## 8. Timeline & owners

- Now (Steven): contract, `DanceJudgeStub`, `FAULTS` and the 8 strings, organisers asked (9.1).
- +6 h: manifest and baseline verdicts on `replay_local` (Steven); hook or `run_dance.py` (integrator); cues, first handler, persona (coaching); 15-minute team recording block.
- +24 h (Steven): ~24 takes labelled, `train_dance.py`, model evaluated, swapped in only if better.
- +36 h (coaching): handler tuned on real verdicts; demo on either.

## 9. Open decisions

1. `perception.py` editable (`README.md:73`)? Hook or `run_dance.py`.
2. Music (Brodie has no audio path): default ref clock = `count_in_s` after the first ok frame, handler counts `Five..Eight` (`cues_tts.py:51`), music on "Eight"; or a player position via `set_ref_clock`.
3. Mirror check (the phone page publishes the raw track, `phone_test.html:87`): raise your right hand; `pipe.last_landmarks[16]` must rise, else flip `learner_mirrors_screen`.
4. Which dancer: track 1 (65% ankles) or track 2?
5. Visibility gate off (step 2) or full-body framing? Decide after first takes.
