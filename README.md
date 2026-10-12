# Brodie starter

> **Brodie Dance** (our hackathon build on this starter): Brodie teaches TikTok dances on a video call. Website with accounts and the call page: `python -m brodie.server` (docs/HOSTING.md); the coach: `bash scripts/demo_worker.sh`; lesson flow in `starter/handlers.py`, the judge in `src/rtcoach/dance_judge.py`, references built with `scripts/make_dance_ref.sh`.

A real-time video coach in one Python process. Your phone joins a LiveKit room, the coach watches through the camera, counts reps, corrects form out loud, and talks between sets.

You write the coaching. Perception is done for you.

## What you need

- macOS or Linux, Python 3.12, [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- A free LiveKit Cloud project ([cloud.livekit.io](https://cloud.livekit.io))
- A phone, or Safari on a Mac
- Your team card (Gemini team token)

## 1. Install (about 30 minutes, mostly waiting)

```
git clone https://github.com/seraphimlabs-dev/brodie-starter.git brodie-rt-coach && cd brodie-rt-coach
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r requirements.txt
mkdir -p models && for m in lite full heavy; do curl -sSfL -o models/pose_landmarker_$m.task https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_$m/float16/latest/pose_landmarker_$m.task; done
```

On Linux, run `uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu` first to skip 3 GB of CUDA wheels.

## 2. Get the data bundle

The models, voice clips and sample recordings are not in git. Download them from this repo's release and unpack them at the repo root:

```
for p in part1_models_recordings part3_bank_puck; do curl -sSfLO https://github.com/seraphimlabs-dev/brodie-starter/releases/download/data-2026-10-09/rtcoach_starter_${p}_2026-10-09.tgz; done
tar xzf rtcoach_starter_part1_models_recordings_2026-10-09.tgz && tar xzf rtcoach_starter_part3_bank_puck_2026-10-09.tgz
```

Check it worked:

```
ls data/models/clf_pose.pt data/models/trigger_lr.json data/cues_audio_gemini/manifest.json models/pose_landmarker_full.task
```

## 3. Prove the install (2 minutes, no phone)

```
python -m facetime.replay_local --video data/tmp/mac_handoff/crops/run3.mp4 --plan "squats:10,pushups:8,jumping jacks:20,lunges:10" --fast --events data/tmp/run3.events.jsonl
python scripts/test_voice.py
```

## 4. Configure

Copy `.env.example` to `.env.local` and fill it in. The LiveKit values come from your project's Settings, then Keys. The Gemini token is on your team card.

## 5. First call

Terminal A, the coach:

```
source .venv/bin/activate && set -a && source .env.local && set +a
python -m facetime.worker --voice gemini dev
```

Terminal B, the link (use a new room name for every attempt):

```
source .venv/bin/activate && set -a && source .env.local && set +a
python -m facetime.mint_token --room team1-try1 --url
```

Open the third printed line on your phone, tap to enable audio, prop the phone up and step back. You should hear "Let's do squats" and then the counts.

Always start Terminal A before the phone joins.

## What you change

Only these three files:

| File | What it is |
| --- | --- |
| `starter/persona.md` | Who Brodie is and how it talks between sets |
| `starter/plan.txt` | The workout, e.g. `squats:10,pushups:8,jumping jacks:20,lunges:10` |
| `starter/handlers.py` | `on_event(event, say_now, ask_voice)`, called for every rep, fault, cue and set change |

- `say_now(text)` plays a pre-recorded line about 0.2 s later. Only strings in `data/cues_audio_gemini/manifest.json` work.
- `ask_voice(prompt)` asks the conversational voice, which answers 1 to 2 s later. Only call it when `session.state` is `rest` or `done`, never during a set.

## Docs

- [docs/STARTER_README.md](docs/STARTER_README.md): full setup, troubleshooting, FaceTime lines, licence
- [docs/STARTER_EVENTS.md](docs/STARTER_EVENTS.md): every event and its fields
- [docs/BUILD_ON_BRODIE.md](docs/BUILD_ON_BRODIE.md): what you can build and how

The same pages are at [seraphimlabs.dev/hackathon](https://seraphimlabs.dev/hackathon).

## Licence

The models and the thresholds (`clf_pose.pt`, `trigger_lr.json`, `src/rtcoach/thresholds_e3c.json`, `src/rtcoach/cue_prior.json`) come from a research evaluation licence. Use them for this hackathon only and do not redistribute them.
