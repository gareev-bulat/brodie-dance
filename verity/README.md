# Verity Agent

Smiley-face "-ity" characters you FaceTime or call from the browser. Each one is a **separate agent**: one call, one character (like picking a Grok companion). **Verity** is the main all-purpose character and the default. The others are **Dancity** (purple, dance, headphones), **Gravity** (indigo, physics, sleepy lids), **Polarity** (green, chemistry, split face) and **Intensity** (orange, gym hype, sweatband). Mess up three times on a Verity call and it turns into **Cruelty** (red, angry, its own voice) until you apologise.

The voice is Gemini Live speech-to-speech through the organisers' relay. The agent sees the caller's camera at 1 fps and publishes its own face, whose mouth follows its voice. Every tool call (strikes, the Cruelty transformation, verdicts, memory, dance moves) also goes to the web app as a live event.

Files:

| File | What |
| --- | --- |
| `verity/variants.py` | The roster and the shared persona rules. Adding a character means adding one entry. |
| `verity/agent.py` | The LiveKit worker: picks the character from the room, runs the call, the tools and the Cruelty handoff. |
| `verity/avatar.py` | The face video track (one look per character) and the audio-level tap that moves the mouth. |
| `verity/dance.py` | **Plug-in point for the team's dance-detection model** (see below). |
| `verity/server.py` | The web app's backend: serves `web/` and mints call tokens. |
| `web/index.html` | The app: sidebar of characters, call view with transcript and tool calls, meme wall, fan -ities. |
| `web/about.html` | The lore page: the series arc in real images, timeline, the -ity multiverse, the meme wall. |
| `web/lore.js` | Every real image both pages use, with its source (Verity Wiki, YouTube, Know Your Meme). Edit lists here. |
| `web/assets/ours/` | Our characters' 3D renders, made by the same code as the call video: `python -m verity.avatar --export web/assets/ours`. |
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
VERITY_CHARACTER=verity          # optional: who answers rooms that aren't from the web app (FaceTime)
```

**Why AGENT_NAME matters:** another worker (a Brodie coach with an empty `AGENT_NAME`) also runs on this LiveKit project. With automatic dispatch, LiveKit split our rooms between the two workers, and half the calls got the silent coach. A named dispatch, which the web app's tokens carry, sends every call to Verity.

## Run

Two terminals, both with the env loaded (`source .venv/bin/activate && set -a && source .env.local && set +a`):

```
python -m verity.agent dev       # terminal 1: the agent
python -m verity.server          # terminal 2: the app at http://127.0.0.1:8765
```

In the app, pick a character in the sidebar and press the call button, or just type. Mic and camera are optional: with both off it's a text chat with the same agent, voice and tools. The browser asks for mic and camera permission on the first call.

- **Test without a browser:** `python scripts/verity_smoke.py --character dancity` (plays a clip as your camera and prints the transcript).
- **Without a camera:** `VERITY_CHARACTER=dancity python -m verity.agent console` (local mic and speaker, no avatar).
- **Deploy:** the agent runs on your laptop; the Chert line and the app connect to it. Production mode is `python -m verity.agent start`.

## How a call reaches the agent

- **Web app:** `/api/token?character=dancity` makes a fresh room `web-dancity-<id>` and a token that dispatches `AGENT_NAME` into it. The room name tells the agent who to be. Web calls start as soon as you join, camera or not.
- **FaceTime (Chert):** `chert dev --agent "$AGENT_NAME"` dispatches the same worker into Chert's rooms, and those get `VERITY_CHARACTER`. The worker joins and immediately publishes its voice track and its face, like Brodie's worker: Chert's bridge binds to the participant with an unmuted audio track and expects audio and video from it. It **says nothing and starts no Gemini session** (no Live minutes used) until the caller publishes audio or video. FaceTime Audio calls have no video; FaceTime Video shows the avatar. The character opens about 2 s after the caller arrives.
- **Before your slot:** Chert rooms have exactly two seats, the bridge and your agent. Nobody on the team should run another worker on this LiveKit project with an empty `AGENT_NAME` (for example the Brodie coach, `python -m facetime.worker dev`). Automatic dispatch puts it in every new room, and it takes the second seat.

For your test slot, put the organisers' dispatch name in `AGENT_NAME` (the app keeps working with it), then:

```
python -m verity.agent dev                       # terminal 1 (start it first)
chert doctor && chert dev --agent "$AGENT_NAME"  # terminal 2 (Node 22.23.2, chert login done)
```

Place a FaceTime **Video** call to the line. When you're done: hang up, wait for `Call ended`, press Ctrl+C in Chert, wait for `Stopped`, then tell the next team.

## Agent features (all in the app)

- **Settings** (sidebar, or press `,`): voice mode Normal / **Mommy ASMR** (soft, whispery, comforting; switches live mid-call), voice (any of the 30 Gemini voices; next call), three-strikes-to-Cruelty on/off, memory on/off, captions, push-to-talk (hold Space), and camera, mic and ringtone defaults. Settings travel to the agent as the caller's participant attributes; mid-call changes hand off to a fresh session of the same character with the new prompt and a recap of the call. Gemini Live can't take new instructions mid-session: `update_instructions` gets read out loud.
- **Memory** (`verity/memory.py`, `data/memory/<uid>.json`): the `remember(fact)` tool and a log of past calls (character, verdict, Cruelty). It's injected into the next call's prompt. The memory panel lists it, and "Forget me" deletes it after a confirm dialog.
- **Message queue:** typed messages wait until the character has finished its greeting and is listening, then go out one at a time. The call header shows the live agent state (`lk.agent.state`: listening, thinking, speaking).
- **Interrupt:** "Stop talking" on the video tile sends `{type: "interrupt"}` on topic `verity.control`.
- **History:** every call's transcript is kept in the browser. Open it from Recent calls, export it as .txt, or call that character again.
- **Verdict cards:** the `verdict(name)` tool turns the -ity verdict into a card and saves it with the call.

**Voice:** set `FISH_API_KEY` (fish.audio, API keys) in `.env.local` and Verity speaks in the Fish Audio voice `894f0267a5194c30814e6b15c07da862` (change it with `VERITY_FISH_VOICE`). Gemini Live then answers in text and Fish Audio reads it out, so expect a little more delay than Gemini's own voices. The other characters, and Verity with a voice picked in Settings, keep their Gemini voices. Without the key, nothing changes. The original Verity voice belongs to its voice actor (JustWhispy); a voice cloned from it needs their permission before it's used in public.

## Real 3D models on the call

`verity/models.py` turns fan-made, CC BY Sketchfab models (closed and open-mouth Verity, Falsity, the monster) into frames for the call avatar. It renders them on the GPU, headless, then turns, bobs and recolours them for each character and adds each one's gear. The avatar plays the open-mouth frames while the agent talks, and falls back to the drawn 3D ball when there are no frames.

```
SKETCHFAB_API_TOKEN=...   # in .env.local: sketchfab.com/settings/password -> API token (downloads need a logged-in account)
python -m verity.models fetch && python -m verity.models render    # then restart the agent
```

The models are credited on the lore page (`web/lore.js` `models3d`).

## Tool calls and app events

The agent sends JSON to the room on the data topic `verity`, and the app shows each event in the transcript:

| Event | When |
| --- | --- |
| `character {key, name, color, face}` | a character starts (also after a transformation) |
| `verdict {name}` | `verdict(name)`: the -ity verdict, shown as a card |
| `remember {fact}` | `remember(fact)`: stored for the next call |
| `memory {facts, calls}` | a returning caller: what it remembers |
| `settings {...}` | a live settings change took effect |
| `strike {count, of, reason}` | `strike(reason)` on Verity: wrong capital of France, rude, ignoring it, hanging up mid-sentence |
| `transform {to}` | strike 3 hands off to Cruelty; `forgive()` after a real apology hands back to Verity |
| `dance {move, score}` | the dance model saw a move |

## Dance detection (for the team)

`verity/dance.py` `DanceDetector.feed(frame)` gets the caller's camera at about 10 fps, in a worker thread, one frame at a time. Return `None`, or a dict like `{"move": "spin", "score": 7}`. The agent then sends a `dance` event to the app and asks the character to react and rate the move. Brodie's pose pipeline (`facetime/perception.py`, `src/rtcoach/pose.py`) is the natural base for landmarks.

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

**Cruelty run (Verity only):** answer "Berlin", "Ugh, you're boring" and "Madrid", and you get three strikes, "Oh... you really shouldn't have done that", then "It's me... it's Cruelty." in a new voice and a red face. Say "I'm really sorry, Paris is the capital" to get Verity back.
