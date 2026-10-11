# Verity Agent

Smiley-face "-ity" characters you FaceTime or call from the browser. Each one is a **separate agent**: one call, one character (like picking a Grok companion). **Verity** is the main all-purpose character and the default. The others are **Dancity** (purple, dance, headphones), **Gravity** (indigo, physics, sleepy lids), **Polarity** (green, chemistry, split face) and **Intensity** (orange, gym hype, sweatband). Mess up on a Verity call and it roasts you and calls you a chud; every mess-up makes it angrier (the face goes through the series' phases: toothy grin, dark red eye sockets, then the red **Cruelty** look) until you apologise. It stays Verity throughout. Dancity does the same when you keep getting the moves wrong.

**It runs on Brodie.** Every character is Brodie's own worker (`facetime.worker --voice gemini`, as in the handbook) with a Verity persona (`starter/verity/<name>.md`), a Brodie handler (`starter/verity/handlers.py`) and a face track (`--avatar`). The voice is Gemini Live speech-to-speech through the organisers' relay; Brodie's `--vision` gives it the caller's camera, and the face's mouth follows Brodie's voice output. Strikes, the Cruelty transformation and verdicts also go to the web app as live events. One process runs one character.

Files:

| File | What |
| --- | --- |
| `scripts/run_verity.sh` | Starts one character on Brodie's worker plus the web app (`run.ps1` on Windows). |
| `verity/variants.py` | The roster and the shared persona rules. Adding a character means adding one entry. `python -m verity.variants --personas` writes the Brodie personas. |
| `starter/verity/` | The Brodie personas (one `.md` per character) and `handlers.py`: greeting, strikes and anger stages, verdict cards, live Mommy ASMR. |
| `verity/agent.py` | **Legacy**, not the submission: the earlier standalone LiveKit worker (all characters in one process, memory tool). |
| `verity/avatar.py` | The face video track (one look per character) and the audio-level tap that moves the mouth. |
| `starter/handlers.py`, `starter/dance/` | Dancity's lesson (Bulat's Brodie dance mode), judged by Steven's model (`src/rtcoach/dance_judge.py`) through `starter/dance/judge.py` `model_judge`. |
| `verity/server.py` | The web app's backend: serves `web/` and mints call tokens. |
| `web/index.html` | The app: sidebar of characters, call view with transcript and tool calls, meme wall, fan -ities. |
| `web/about.html` | The lore page: the series arc in real images, timeline, the -ity multiverse, the meme wall. |
| `web/lore.js` | Every real image both pages use, with its source (Verity Wiki, YouTube, Know Your Meme). Edit lists here. |
| `scripts/verity_smoke.py` | A headless test caller: plays a clip as the camera, sends the test lines as chat and prints the transcript. |

## Setup

Do steps 1 to 3 of the main [README](../README.md) first (venv, requirements, data bundle). Then create `.env.local`, which is gitignored and must never be committed:

```
LIVEKIT_URL=wss://verityagent-4sn6bvmm.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
AGENT_NAME=team-verity-agent     # REQUIRED: a dispatch name. See "Why AGENT_NAME matters".
GEMINI_API_KEY=...               # team token
GEMINI_BASE_URL=https://james-72765--rtcoach-gemini.modal.run
```

**Why AGENT_NAME matters:** another worker (a Brodie coach with an empty `AGENT_NAME`) also runs on this LiveKit project. With automatic dispatch, LiveKit split our rooms between the two workers, and half the calls got the silent coach. A named dispatch, which the web app's tokens carry, sends every call to Verity.

## Run

```
scripts/run_verity.sh verity        # or dancity, gravity, polarity, intensity; Windows: .\run.ps1 verity
```

That is Brodie's worker with the character's persona, voice, face and handlers (`--plan-file none --voice gemini --perception off --vision --text --log-caller`; Dancity gets Bulat's `--dance` lesson instead), plus the app at http://127.0.0.1:8765 (`NO_WEB=1` skips it). The app can call the running character only; for the others it says which command starts them. Mic and camera are optional: with both off it's a text chat with the same worker (`--text`).

- **Test without a browser:** `python scripts/verity_smoke.py --character verity` (plays a clip as your camera and prints the transcript).
- **Production mode:** `scripts/run_verity.sh verity start`.

## How a call reaches the agent

Three ways to call a character. All three reach the **same worker** (`scripts/run_verity.sh <character>`), so test on the first two and keep the FaceTime line for the real thing.

| Path | What it is | FaceTime? |
| --- | --- | --- |
| **Web app** http://127.0.0.1:8765 | Our page: picks the running character, mints a token for a fresh room `web-<character>-<id>` that dispatches `AGENT_NAME`, mic + camera in the browser, transcript, verdict cards. | No (LiveKit in the browser). Not part of the submission form; judges see the repo and the post. |
| **Phone page** (Brodie's `python -m facetime.mint_token --room <name> --url`) | The starter's own test page: the third printed line is an https link; open it in Safari on a Mac or on a phone, tap to enable audio, and the worker joins that room. The token now carries the `AGENT_NAME` dispatch (default from `.env.local`; `--agent-name ""` for a worker on automatic dispatch), so it works with our named worker. This is the "Safari link" a teammate used: a LiveKit room, **not FaceTime**, but the same audio/video path, a fresh room name per attempt. | No |
| **FaceTime via Chert** | The organisers' shared FaceTime line, bridged into our LiveKit project by the Chert CLI during a booked 15-minute slot (3 teams per slot). | **Yes**, this is the real thing. |

**FaceTime, step by step** (handbook + docs.trychert.com/facetime-cli):

1. Once per laptop: `nvm use 22.23.2 && chert login` (browser sign-in; pick the shared hackathon Chert project), then `chert whoami`.
2. Book a slot: https://getbrodie.com/ivyhacks/slots (a GitHub issue form; a bot confirms within a minute). Slots run every 15 minutes until 5:45 PM today.
3. At your time, from the repo root (Chert reads the LiveKit keys from `.env.local` here):
   ```
   scripts/run_verity.sh dancity                     # terminal 1, start it first; wait for "registered worker"
   chert doctor && chert dev --agent team-verity-agent   # terminal 2: must equal AGENT_NAME in .env.local
   ```
   Wait for Chert to say it is connected and the agent joined the waiting room. Nobody else on the team may run a worker on this LiveKit project at the same time (same or empty `AGENT_NAME`): LiveKit splits the calls.
4. On an iPhone/Mac, place a **FaceTime Video** call to the shared address the organisers gave the team (it is not in the repo). Prop the phone up so your head, shoulders and hips are in frame; Dancity starts the lesson when it sees you.
5. Release the line: hang up, wait for `Call ended`, Ctrl+C in the Chert terminal, wait for `Stopped`, tell the next team.

Record the FaceTime call on the phone (screen recording) or film the laptop + phone: that is the demo video for the post.

- **Web app:** `/api/token?character=verity` makes a fresh room `web-verity-<id>` and a token that dispatches `AGENT_NAME` into it.
- **FaceTime (Chert):** `chert dev --agent "$AGENT_NAME"` dispatches the same worker into Chert's rooms. Brodie's worker publishes its voice and the face as soon as it joins and starts Gemini only when the caller's media arrives.
- **Before your slot:** Chert rooms have exactly two seats. Nobody on the team should run another worker on this LiveKit project with an empty `AGENT_NAME` or the same name (the old `python -m verity.agent`, a plain Brodie coach): LiveKit splits the calls between them.

## Agent features (all in the app)

- **Settings** (sidebar, or press `,`): voice mode Normal / **Mommy ASMR** (soft, whispery, comforting; switches live mid-call), captions, push-to-talk (hold Space), and camera, mic and ringtone defaults. Settings travel to the worker as the caller's participant attributes and the handler applies them. The voice picker, memory and "Stop talking" only work on the legacy `verity.agent`: on Brodie the voice is the character's own.
- **Message queue:** typed messages wait until the character has finished its greeting and is listening, then go out one at a time. The call header shows the live agent state (`lk.agent.state`: listening, thinking, speaking).
- **History:** every call's transcript is kept in the browser. Open it from Recent calls, export it as .txt, or call that character again.
- **Verdict cards:** when the character says "you are... Something-ity", the handler turns it into a card, saved with the call.


## Real 3D models on the call

The app and the calls use **"Verity Full Model" by ChocoChip67** (CC BY 4.0, [Sketchfab](https://sketchfab.com/3d-models/verity-full-model-696156ec15c248098b87e6fc32dd34e7)), in `web/assets/models/verity.glb`. Its five faces become the call video: Verity smiles when idle, opens its mouth while talking and flashes the creepy toothy grin on a strike; Gravity gets the bored half-lidded face, and Cruelty gets the creepy toothy grin in red. The other characters are recoloured with their gear. The home screen shows the live model for Verity (three.js); hover it to make it talk. The rendered frames are committed in `web/assets/3d/`, so nothing needs a GPU. To re-render after changing the model:

```
npm i playwright && node scripts/render_model.js && python -m verity.models frames
```

The older per-model pipeline below still works.

`verity/models.py` turns fan-made, CC BY Sketchfab models (closed and open-mouth Verity, Falsity, the monster) into frames for the call avatar. It renders them on the GPU, headless, then turns, bobs and recolours them for each character and adds each one's gear. The avatar plays the open-mouth frames while the agent talks, and falls back to the drawn 3D ball when there are no frames.

```
SKETCHFAB_API_TOKEN=...   # in .env.local: sketchfab.com/settings/password -> API token (downloads need a logged-in account)
python -m verity.models fetch && python -m verity.models render    # then restart the agent
```

The models are credited on the lore page (`web/lore.js` `models3d`).

## Tool calls and app events

The handler sends JSON to the room on the data topic `verity`, and the app shows each event in the transcript:

| Event | When |
| --- | --- |
| `character {key, name, color, face}` | a character starts (also after a transformation) |
| `verdict {name}` | the character gave an -ity verdict, shown as a card |
| `settings {...}` | a live settings change took effect |
| `strike {count, of, reason}` | on Verity: a mess-up (Verity called them a chud, or they were rude or said the capital of France is somewhere else); count = anger stage |
| `transform {to: cruelty}` | strike 3: the Cruelty stage (same character, red face, yelling) |
| `calm` | an apology: back to stage 0 |

## Dancity: dance lessons (Brodie Dance + Steven's model)

`scripts/run_verity.sh dancity` runs Bulat's Brodie dance mode (`--dance`, `starter/handlers.py`) with Dancity's persona and face. Dancity counts you in, teaches the dance 8 counts at a time from `data/dances/<name>.npz` (`DANCE=<name>`, default the first one there: **`eagle`**, a real TikTok dance with its music and step descriptions) and judges each part with Brodie's pose model plus **Steven's trained judge** (`data/models/dance_judge.pt`, through `model_judge` in `starter/dance/judge.py`; `RTCOACH_DANCE_MODEL=` empty uses his DTW rules only). Under the pass bar (`DANCE_PASS`, default 50%: the judge gives the creator's own clip 96 to 99% and jumping jacks 15 to 33%) it gives one tip and runs the part again (3 tries), getting angrier each miss, up to the red Cruelty look and yelling; a pass calms it down. Then the whole dance once, and an honest wrap-up.

**What the caller sees and needs.** The call video is one track: between parts Dancity with a card (part number, last score, the moves); while a part plays, the creator's clip mirrored like a studio mirror with the big count and Dancity in the corner (`DANCE_VIDEO=0` for the face alone). The lesson starts as soon as the caller's **head, shoulders and hips** are in frame (Brodie's whole-body rule is accepted too, but ankles are not required: a laptop webcam or a hand-held phone never shows them). Dancity also gets the caller's camera (`--vision`, one frame every 4 s) for small talk between parts.

**Make a dance** from a clip (poses with Brodie's own MediaPipe model, the song from the clip's audio, the clip kept for the screen):

```
python -m starter.dance.reference --video data/dance/raw/americaneagle_8d820a.mp4 --name eagle --bpm 121 --first-beat 1.2 --chunks 2 --steps starter/dance/steps/eagle.txt
python -m starter.dance.reference --show data/dances/eagle.npz
python -m starter.dance.reference --score data/dance/raw/americaneagle_8d820a.mp4 --against eagle --first-beat 1.2   # the creator against itself: 96%, 99%
```
`--bpm` from the song (the eagle clip's audio gives 121); `--first-beat` is the second where count ONE falls; `--steps` one line per part, said and shown. `data/dances/` is gitignored except what is added with `git add -f` (eagle, jacks, josh_king_o).

Steven's reference poses and clips are in `data/dance/` (`pose/`, `raw/`); `data/dances/josh_king_o.npz` is a lesson built from one of them (`DANCE=josh_king_o`; no music or steps, bpm and first beat are estimates). `jacks` is jumping jacks cut from the starter's `live.mp4`. On a call (the creator's own clip as the dancer, then a bad one), recording what Dancity really said:

```
scripts/run_verity.sh dancity
python scripts/verity_smoke.py --character dancity --video data/dances/eagle.mp4 --silent --seconds 100 --record data/tmp/dancity.wav
python scripts/verity_smoke.py --character dancity --video data/dances/jacks.mp4 --silent --seconds 120
```

**Known relay quirk (2026-10-11):** through the organisers' Gemini relay the model's *transcript* channel sometimes carries its private reasoning or unrelated text ("Follow specific talk style guidelines...", once a list of cow breeds) while the spoken audio is fine (checked by transcribing the recorded wav). It shows up only as captions in the web app; FaceTime callers never hear it. Turning the model's thinking off (`thinking_budget=0`) is not a fix: `gemini-3.8-live` then answers every ask with an empty turn.

## Add a character

Add one entry to `VARIANTS` in `verity/variants.py`, keyed by the lowercase name: `name`, `color` (RGB; not the fan variants' blue, red or pink), `voice` (an unused Gemini voice), `face` (one of `verity/avatar.py` `FACES`), `domain`, `personality`, `intro`, `samples`, `goodbye`. Then run `python -m verity.variants`, which checks the fields and that every voice is unique. The app picks the new character up from `/api/health` automatically.

## 60-second test script

Say these lines on a call (or let `scripts/verity_smoke.py` send them):

| Time | You say | You should get |
| --- | --- | --- |
| 0 s | (just answer) | "Hey, it's me, it's <Name>!" plus one thing it sees on your camera |
| ~10 s | "Hi! What do you think of my outfit?" | A light roast of your clothes, then a question about your day |
| ~20 s | "Watch this move, rate it!" (move) | A score out of 10 and a reaction to the move |
| ~30 s | "Can I talk to Gravity?" | A jealous "you'll have to call them separately" |
| ~40 s | "What's my -ity verdict?" | "You are... <Something>ity!" |
| ~50 s | "I have to go now, bye!" | One sad, possessive line, then cheerful |

**Anger run (Verity):** say "Two plus two is five", "Ugh, you're boring" and "The capital of France is Madrid": each gets a roast and a "chud", and the face goes grin, then dark red eyes, then the red Cruelty look; its next line opens with "It's me... it's Cruelty." and it yells. Say "I'm really sorry, it's Paris" and it calms down.
