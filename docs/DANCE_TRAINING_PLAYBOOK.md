# Dance-move training & data-collection playbook

Repo `brodie-starter`, read-only audit 2026-10-10; code claims cite `path:line`. Your role: training + data collection for TikTok dance moves. The coach runs under WSL2 (no webcam); inputs are the phone over LiveKit or an mp4.

## 1. Pipeline at a glance

```
frame -> short side 288 (facetime/pipeline.py:166, 72-78) -> MediaPipe VIDEO, conf 0.4 (src/rtcoach/pose.py:6-17): img (33,5) + world (33,3)
-> visibility gate, Coach paused after 3 s out of frame (facetime/visibility.py:17-26, 47-49; pipeline.py:175-185)
-> Coach 60-frame buffer (src/rtcoach/coach.py:65-68); every `every` frames once >=15 (:89): pose_frames (T,201) (src/rtcoach/feats.py:6-23) -> predict_many (src/rtcoach/model.py:25-33)
-> EMA (coach.py:94) -> trigger decides WHEN (src/rtcoach/trigger.py:31-58; coach.py:96-100) -> cue text (coach.py:102-128)
-> RepCounter2 every frame (coach.py:71-72; src/rtcoach/repcount2.py:330) -> events JSONL (facetime/perception.py:210-247) -> CueBank (facetime/audio_out.py:231-237)
```

10 fps (pipeline.py:68). `every` = 3 without a trigger (pipeline.py:43), 1 with one (:115-116); coach.py:10's 5 is unused.

## 2. What the models are

**`data/models/clf_pose.pt`** (torch zip, research licence)
- Dict `exs, labs, lab_ex, inp, win, D, d, layers, state` -> `Net` (model.py:15-17): transformer encoder (4 heads, dropout 0.1) with an output `LayerNorm(d)` (`ho`) before heads `hex`/`hlab` (model.py:5-8, :12); windows zero-padded to `win` (:27-31); features feats.py:5, 12-22.
- Hyper-parameters are **confirmed** from the pickle tail (`unzip -p clf_pose.pt clf_pose/data.pkl | od`): `inp='pose', d=192, layers=4, win=30, D=201`. `trigger_lr.json` `features.exs/labs/lab_ex` (76/515) is the ordered vocabulary, indexed against the classifier at trigger.py:25-26, :44; use it for `labels.csv` tags.
- BUILD_ON_BRODIE.md:454's "50 exercises / 556 labels" counts `src/rtcoach/cues.json` (556 keys, 50 prefixes), not the heads; 290 entries are `kind=fault` with a cue, the only ones the Coach speaks (coach.py:102, 132-133).
- `Classifier.__init__` sets `torch.set_num_threads(2)` (model.py:18). No GPU anywhere: `device='cpu'` (model.py:14; pipeline.py:111), MediaPipe without a delegate (pose.py:7-9); CPU torch suffices except for training.

**`data/models/trigger_lr.json`** (logistic regression, research licence)
- X is 2218-dim (trigger.py:35-44); `p = P(correction within 1.5 s)` fires at `p > 0.5414`, gap 3.0 s, cooldown 2.0 s (:4-5, 54-57). It proposes the highest-EMA0.5 fault label (:49); with `cue_prior` on (live default, pipeline.py:44) the Coach re-picks the EMA x cue_prior maximum among labels above threshold (coach.py:103-110).
- `--trigger none` (pipeline.py:55-66) falls back to the threshold/streak rule (coach.py:131-143). Its recipe is in the JSON (`train`: 149 sessions, stride 3; `cv`: nested F 0.403).

**Training code: none.** grep `torch.save|def train|.backward|DataLoader` is empty; trigger.py:1 cites an absent `scripts/e3b_trigger.py`; the pickle holds keys, labels and weights only (BUILD_ON_BRODIE.md:454-456: heads are fixed).

## 3. Three ways to recognize dance moves (ranked for 24-48 h)

**(a) Rules on landmarks: first.**
- Copy the side rules (`signals -> classify -> observe -> active_rule -> speak`, src/rtcoach/side_rules.py:32-82); `cadence_of` is at src/rtcoach/cadence_rules.py:44-56.
- Hook like the **side rules in 'both' mode**: observe every detected frame, speak when `t-last_say>=gap` (coach.py:80-85; `side_mode: both`, pipeline.py:46), under your own params key. Not the cadence hook: it only observes (coach.py:86-87), speaks only through the trigger-fired override (coach.py:101, 120-122) and is absent from POLICY_DEFAULT (pipeline.py:43-49).
- A rule label needs a cues.json entry with a non-empty `cue` (side_rules.py:82 indexes `CUES[rule]['cue']`); `kind: fault` matters only for classifier labels (coach.py:102, 133) and the trigger mask (trigger.py:18).
- Cyclic moves: `RepCounter2(exercise=<new name>)` with no table row runs the full 13-signal vote (repcount2.py:53, 287). With the live `select='mask'` (pipeline.py:35) only MASK (:68, :284-287), ACTIVE (:59-62, :290), MIN_PERIOD (:74-75, :283), GATE (:69, :278) and SETTLE (:70, :280) rows take effect; HINT and MIN_AMP need `select='hint'/'single'` (:287, :289). A new signal needs both SIGNALS (:53) and `pose_signals` (:76-96).
- Wiring: counters only for `COUNTABLE` (pipeline.py:139-142; exmap.py:9); add the move to `PLAIN` (pipeline.py:69) or `LR2CLF` (exmap.py:2-8) so `map_exercise` accepts it (pipeline.py:80-88).
- **Silence the QEVD classifier.** A name not in `clf.exs` selects all 515 labels (coach.py:47-48) and every fault label in the trigger mask (trigger.py:25-27); both load by default (pipeline.py:111, 57-58); `cue_mask` only acts when `faults` is empty (coach.py:123). Fix: `--trigger none --thr '{}' --params '{"thr": 1.1, "praise_after": 0}'` (`{}` is not None, pipeline.py:113; `P['thr']` then applies to every label, coach.py:134; idle praise off, :149). `--model ''` (pipeline.py:111) also silences it, but coach.py:34 then never calls `set_exercise` and replay_local.py:81 crashes on `pipe.clf.total_ms`.
- Files: `src/rtcoach/dance_rules.py` (new), coach.py, exmap.py/pipeline.py, repcount2.py, cues.json.

**(c) DTW against a reference: second.** `pose_frames` (feats.py:6-23) or `pose_signals` (repcount2.py:76-96) against the Coach's 60-frame buffer (coach.py:65-68); `resample`/`mirror` (feats.py:24-26, 10-11) for tempo and handedness. No DTW code exists: ~100 numpy lines; 1-3 clean references per move.

**(b) Retrain the classifier: stretch.**
- Contract: the dict above, rebuilt by model.py:15-17; only `D=201` and `15 <= win <= 60` are fixed (coach.py:89, 67-68), `d`/`layers` are free, dropout hard-coded 0.1 (model.py:7). Needs `--trigger none`: X is bound to 515/76 (trigger.py:25, 44).
- Auto mode only declares classes in `cmap` (session.py:279-281; `ELIGIBLE` :43, families :48-56); a dance-only checkpoint leaves `fam` empty and `max()` raises (:282), so evaluate with `--exercise "<move>"` (:220-225).

## 4. Data collection plan

- **Framing**: portrait phone, knee-to-waist height, 2-3 m away, whole body, feet above the bottom edge (docs/STARTER_README.md:256-257, 279-281). Nose and both ankles in frame, and the ankles not both at vis < 0.3 in the bottom 10 percent (visibility.py:17-26, :24-26), else coaching pauses after 3 s (:47-49).
- **Who**: 2-3 consenting teammates per move, 3+ takes each, both mirror directions, plus negatives; mirror augmentation swaps L/R (feats.py:3-4, 10-11), so swap side-specific tags for mirrored takes.
- **Nothing in the repo records raw data** (grep `VideoWriter|imwrite|np.save` hits only mirror.py:134, a PNG); live sessions log only events/cues (facetime/worker.py:102-103). Record on the phone camera app; convert first (replay assumes 10 fps, replay_local.py:52; video.py:6-7 loads every frame):
  `ffmpeg -i IN.mov -vf "fps=10,scale='if(gt(iw,ih),-2,288)':'if(gt(iw,ih),288,-2)'" -an -c:v libx264 -preset fast -crf 23 data/dance/clips/<move>__<performer>__<take>.mp4` (STARTER_README.md:259).
  Keep the original (`-an` strips audio; frame i is t=i/10 s); e2e_from_recording.py:27 extracts a wav for beat onsets.
- **Landmarks**: `scripts/extract_landmarks.py`: `sys.path.insert(0, ROOT)` first (scripts/test_perception.py:13-14; facetime/__init__.py:11-14), `read_frames` (video.py:2-7) -> `to_short_side` (pipeline.py:72-78) -> `Pose('full', video=True)(frame, i*100)` (pose.py:6-17). A miss returns `(None, None)` (pose.py:14): store zeros and `ok=False` as coach.py:65 does; save `img (N,33,5)`, `world (N,33,3)`, `ok (N,)` as `.npz` (bypasses the visibility gate).
- **Formats and labels**: mp4 (10 fps, short side 288, no audio), `.npz`, schema-v1 JSONL (perception.py:33-34), all under gitignored `data/dance/` (.gitignore:1, 3, 13, 20; `*.csv`/`*.mov`/`*.md` are not ignored elsewhere). `labels.csv`: `clip, t_start_s, t_end_s, move, fault_tags`, one row per 8-count, marked by hand (no audio/BPM code).

## 5. Training plan (approach b)

- **Features**: `pose_frames` per window (feats.py:6-23); torso scale and hip reference are per-window medians (:14, :19), so cut windows as live does: last `win` frames plus 15-29-frame windows zero-padded like `predict_many` (model.py:29-30; coach.py:89). Any stride (live evaluates every 3 frames, 1 with a trigger: pipeline.py:43, :116). Augment with `mirror`, `rot/scale`, `resample` (feats.py:10-11, 17-18, 24-26).
- **`scripts/train_dance.py` (new)**: `from rtcoach.model import Net`, small (`d=64`, 1-2 layers; the research config is ~1.9 M parameters); CE on `hex`, BCE on `hlab`; `torch.save(dict(state=..., exs, labs, lab_ex, inp='pose', win=30, D=201, d, layers), 'data/models/clf_dance.pt')` (model.py:15-17). End with `Classifier(path)` as a load test; it caps threads at 2 (model.py:14-18): run it last.
- **Thresholds from day one**: thresholds_e3c.json loads by default (pipeline.py:50, 113) and unknown labels get `thr 0.3, k 1` (pipeline.py:43), so one EMA>0.3 evaluation speaks. Ship a per-label `--thr` JSON.
- **Evaluation**: `python -m facetime.replay_local --video held.mp4 --model data/models/clf_dance.pt --exercise "<move>" --trigger none --thr dance_thr.json --no-announce --fast --events out.jsonl` (replay_local.py:39-43, 47). `--no-announce`: "Let's do <move>" (session.py:223) is not in the bank (cues_tts.py:42-44, 56) and would log `missing`. The visibility gate still runs (pipeline.py:175-185): full-body clips, or in your own script `pipe.vis = Visibility(FPS, no_pose_s=1e9, partial_s=1e9)` (visibility.py:29). Events are post-policy (EMA 0.7, coach.py:94; `exercise_seen` 1/s, perception.py:226-229; ema>thr, coach.py:131-138): score raw `predict_many` yourself.

## 6. Adding spoken dance cues

- The bank is keyed on the exact string (audio_out.py:118-119, 235-237); `vocabulary()` renders every non-empty cues.json `cue` regardless of kind (facetime/cues_tts.py:46-67).
- Add `"renegade - arms low": {"cue": "Arms higher on the clap", "kind": "fault"}` to cues.json (BUILD_ON_BRODIE.md:133-136); no en/em dashes (cues_tts.py:65-66). `python -m facetime.cues_tts --backend gemini --dry-run`, then without (cues_tts.py:172, 189, 215-219); needs `GEMINI_API_KEY` and, at the hackathon, `GEMINI_BASE_URL` (:101-111).
- `say_now(text, intent='other')` -> `CuePlayer.say` (facetime/voice.py:580-584), priority 1 (audio_out.py:32): queued only when nothing is pending (:240-241), otherwise **dropped**, never deferred (:257); a later count jumps ahead and the line follows it (:248-252); only `intent='correction'` waits behind a count (:253-256). Contract: starter/handlers.py:3-12.

## 7. Licence & consent caveats

- `clf_pose.pt`, `trigger_lr.json`, `thresholds_e3c.json`, `cue_prior.json`: 30-day QEVD evaluation licence, hackathon only, private, never identify a participant, no face recognition (README.md:94; STARTER_README.md:338-340). Fine-tuning from `clf_pose.pt` inherits this; train from scratch. QEVD label names ship verbatim as cues.json keys; no QEVD clips/frames may be requested (STARTER_README.md:341-343).
- `thresholds_e3c.json` and `cue_prior.json` are git-tracked (`git ls-files src/rtcoach`), so pushes to the team fork (`origin git@github.com:zxpeng2007/ivyhacks-brodie-dance.git`) carry them: keep it private.
- `data/tmp/mac_handoff/*` are James's consented recordings, never publish (data/STARTER_BUNDLE_README.txt:16, 25); yours need written consent, under gitignored `data/`, TikTok videos only with rights; callers get the disclosure line (STARTER_README.md:324-334).

## 8. Open questions

- clf_pose.pt recipe (loss, label masking, augmentation): nothing in the repo or pickle (only the trigger's `train`/`cv` block); ask the organisers.
- May teams edit `exmap.py`/`pipeline.py`/`coach.py`? README.md:73 says three files. Demo transport decides orientation: phone page portrait 720x1280@30 vs Chert landscape 1280x720@15 (STARTER_README.md:282-284).
- WSL2 (docs say no Windows: STARTER_README.md:4). From the code: ffmpeg/ffprobe on PATH (video.py:4-6); CPU torch (README.md:23); `RTCOACH_DATA` relocates `data/` (facetime/__init__.py:15) but `models/` is hard-coded relative to the repo (pose.py:4); a fresh clone has neither (`git ls-files models data` empty; .gitignore:1, 10-11), so copy the bundle and `.task` files over. No-webcam paths: `facetime.fake_bridge` (fake_bridge.py:1-19), `webcam_local --camera file.mp4 --audio wav` (webcam_local.py:3-4, 110). Not from the repo: WSL ext4 checkout, libGL, cu128 torch for the 5080; LiveKit UDP untested.
- Is `win=30` (3 s) right for an 8-count (~4 s at 120 bpm)? New `win` = new checkpoint; buffer caps at 60 (coach.py:68).
- Waist-up framing vs the nose+ankles rule (visibility.py:17-26)? BPM source (no audio path)? `exercise_seen` is ~0.75 accurate (docs/STARTER_EVENTS.md:242-243).
