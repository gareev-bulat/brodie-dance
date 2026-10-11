# Hosting Brodie Dance

Three pieces. Only the first two go on a hosting service.

| Piece | What | Where |
| --- | --- | --- |
| Web + token server (`verity/server.py`) | Landing page at `/`, call app at `/app`, `/api/token`, `/api/config`, `/api/dances` | Any Docker host: Render, Railway, Fly, Cloud Run |
| LiveKit Cloud | Rooms, media, agent dispatch | cloud.livekit.io (free project) |
| Brodie's worker (`facetime.worker --dance`) | The coach: pose, judge, voice, the lesson video | A machine with the data bundle and the Python env (a laptop is fine); it connects out to LiveKit, no inbound port |

## 1. Deploy the web server

Build from the repo root with the `Dockerfile` (it copies only `verity/`, `web/`, the reference loader and `data/dances/*.npz`).

Environment variables on the host:

| Variable | Value |
| --- | --- |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | from the LiveKit project (Settings, then Keys) |
| `AGENT_NAME` | `team-verity-agent` (must match the worker) |
| `VERITY_CHARACTER` | `dancity` |
| `DANCE` | `sbbyj` (the lesson the worker runs; shows "live now" on the landing page) |
| `FACETIME_NUMBER` | the Chert FaceTime line, e.g. `+1 555 010 0100` (optional; the FaceTime buttons appear when set) |
| `FORK_URL` | the team's GitHub fork (optional) |
| `HOST` / `PORT` | `0.0.0.0` and whatever the host assigns (the Dockerfile sets both; most hosts override `PORT`) |

`/api/token` has no auth: anyone who can reach the server can start a call and spend Gemini minutes. Keep the URL to the team, or put the host's auth in front of it.

## 2. Run the worker

On the machine that has the data bundle, with the same `LIVEKIT_*` and `AGENT_NAME` in `.env.local`:

```
bash scripts/demo_worker.sh
```

LiveKit dispatches that worker into every room the web server creates (`web-dancity-…`) and into the Chert FaceTime line. The worker's `DANCE` should match the server's.

## 3. Static-only landing (optional)

`web/landing.html` is a static page. On a static host (Vercel, Netlify) it works alone, with the lesson list and FaceTime buttons hidden; point its call button at the hosted app with `?app=https://<server>/app`, or set `window.APP_URL` before the script.

## Check it

```
curl https://<server>/api/health     # ok, characters, agent_name, live
curl https://<server>/api/config     # live character, facetime number, fork
curl https://<server>/api/dances     # the lessons with parts, bpm, steps
```
