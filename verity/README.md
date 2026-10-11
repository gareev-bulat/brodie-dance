# Verity Agent

Smiley-face "-ity" characters you FaceTime or call from the browser. Each one is a **separate agent**: one call, one character (like picking a Grok companion). **Verity** is the main all-purpose character and the default. The others are **Dancity** (purple, dance, headphones), **Gravity** (indigo, physics, sleepy lids), **Polarity** (green, chemistry, split face) and **Intensity** (orange, gym hype, sweatband). Mess up three times on a Verity call and it turns into **Cruelty** (red, angry, its own voice) until you apologise.

**It runs on Brodie.** Every character is Brodie's own worker (`facetime.worker --voice gemini`, as in the handbook) with a Verity persona (`starter/verity/<name>.md`), a Brodie handler (`starter/verity/handlers.py`) and a face track (`--avatar`). The voice is Gemini Live speech-to-speech through the organisers' relay; Brodie's `--vision` gives it the caller's camera, and the face's mouth follows Brodie's voice output. Strikes, the Cruelty transformation and verdicts also go to the web app as live events. One process runs one character.

Files:

| File | What |
| --- | --- |
| `scripts/run_verity.sh` | Starts one character on Brodie's worker plus the web app (`run.ps1` on Windows). |
| `verity/variants.py` | The roster and the shared persona rules. Adding a character means adding one entry. `python -m verity.variants --personas` writes the Brodie personas. |
| `starter/verity/` | The Brodie personas (one `.md` per character) and `handlers.py`: greeting, strikes, Cruelty and back, verdict cards, live Mommy ASMR. |
| `verity/agent.py` | **Legacy**, not the submission: the earlier standalone LiveKit worker (all characters in one process, memory tool, Fish Audio voice). |
| `verity/avatar.py` | The face video track (one look per character) and the audio-level tap that moves the mouth. |
| `verity/dance.py` | **Plug-in point for the team's dance-detection model** (see below). |
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

- **Web app:** `/api/token?character=verity` makes a fresh room `web-verity-<id>` and a token that dispatches `AGENT_NAME` into it.
- **FaceTime (Chert):** `chert dev --agent "$AGENT_NAME"` dispatches the same worker into Chert's rooms. Brodie's worker publishes its voice and the face as soon as it joins and starts Gemini only when the caller's media arrives.
- **Before your slot:** Chert rooms have exactly two seats. Nobody on the team should run another worker on this LiveKit project with an empty `AGENT_NAME` or the same name (the old `python -m verity.agent`, a plain Brodie coach): LiveKit splits the calls between them.

For your test slot, put the organisers' dispatch name in `AGENT_NAME`, then:

```
scripts/run_verity.sh verity                     # terminal 1 (start it first)
chert doctor && chert dev --agent "$AGENT_NAME"  # terminal 2 (Node 22.23.2, chert login done)
```

Place a FaceTime **Video** call to the line. When you're done: hang up, wait for `Call ended`, press Ctrl+C in Chert, wait for `Stopped`, then tell the next team.

## Agent features (all in the app)

- **Settings** (sidebar, or press `,`): voice mode Normal / **Mommy ASMR** (soft, whispery, comforting; switches live mid-call), three-strikes-to-Cruelty on/off, captions, push-to-talk (hold Space), and camera, mic and ringtone defaults. Settings travel to the worker as the caller's participant attributes and the handler applies them. The voice picker, memory and "Stop talking" only work on the legacy `verity.agent`: on Brodie the voice is the character's own.
- **Message queue:** typed messages wait until the character has finished its greeting and is listening, then go out one at a time. The call header shows the live agent state (`lk.agent.state`: listening, thinking, speaking).
- **History:** every call's transcript is kept in the browser. Open it from Recent calls, export it as .txt, or call that character again.
- **Verdict cards:** when the character says "you are... Something-ity", the handler turns it into a card, saved with the call.

**Fish Audio voice (legacy `verity.agent` only):** set `FISH_API_KEY` (fish.audio, API keys) in `.env.local` and every character speaks in the Fish Audio voice `894f0267a5194c30814e6b15c07da862` (change it with `VERITY_FISH_VOICE`), using Fish's `s2.1-pro-free` model (change it with `FISH_MODEL`). Gemini Live then answers in text and Fish Audio reads it out, so expect a little more delay than Gemini's own voices. A voice picked in Settings still uses Gemini. Without the key, nothing changes. The original Verity voice belongs to its voice actor (JustWhispy); a voice cloned from it needs their permission before it's used in public.

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
| `strike {count, of, reason}` | on Verity: the caller was rude or said the capital of France is somewhere else |
| `transform {to}` | after strike 3, once Verity finishes its reply, it becomes Cruelty (face and role); an apology turns it back |
| `dance {move, score}` | the dance model saw a move |

## Dance detection (Brodie Dance, merged)

Dancity watches with Brodie's own pose model (`src/rtcoach/pose.py`, MediaPipe) and the team's dance judge from Bulat's branch (`starter/dance/`), which is where Steven's model plugs in (`model_judge` in `starter/dance/judge.py`). `verity/dance.py` collects about 4 s of the caller's body at 10 fps, then:

- **With a reference dance:** set `VERITY_DANCE=scuba` (or any `data/dances/<name>.npz` made with `python -m starter.dance.reference`). Each window is judged against the next 8-count, and Dancity gets the score plus the body part furthest off, so it gives one tip.
- **Without one:** it rates how much and how big the arms and legs move (freestyle, 1 to 10). Standing still gives nothing.

Dancity reacts at most every 12 s. Try it on a recording: `python -m verity.dance data/tmp/mac_handoff/crops/live.mp4`.

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

**Cruelty run (Verity only):** answer "Berlin", "Ugh, you're boring" and "Madrid", and you get three strikes (the face flashes its creepy grin on each), then "It's me... it's Cruelty." and a red face. The voice stays the same: one Brodie session per call. Say "I'm really sorry, Paris is the capital" to get Verity back.
