# Hosting Brodie Dance

Three pieces. Only the first two go on a hosting service.

| Piece | What | Where |
| --- | --- | --- |
| Website (`brodie/server.py`) | Landing page at `/`, accounts (`/api/signup`, `/api/login`), the call page at `/app`, `/api/token`, `/api/config`, `/api/dances` | Any Docker host: Render, Railway, Fly, Cloud Run |
| LiveKit Cloud | Rooms, media, agent dispatch | cloud.livekit.io (free project) |
| Brodie's worker (`facetime.worker --dance`) | The coach: pose, judge, voice, the lesson video | A machine with the data bundle and the Python env (a laptop is fine); it connects out to LiveKit, no inbound port |

## 1. Deploy the website

Build from the repo root with the `Dockerfile` (it copies `brodie/`, `web/`, the reference loader and `data/dances/*.npz`; no torch, no MediaPipe).

Environment variables on the host:

| Variable | Value |
| --- | --- |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | from the LiveKit project (Settings, then Keys) |
| `AGENT_NAME` | `brodie-dance` (must match the worker) |
| `DANCE` | `sbbyj` (the lesson the worker runs; "live now" on the landing page) |
| `SESSION_SECRET` | any long random string, so logins survive a redeploy |
| `BRODIE_DB` | path of the sqlite file, on a persistent disk if the host has one (default `data/brodie.db`) |
| `FACETIME_NUMBER` | the Chert FaceTime line (optional; the FaceTime buttons appear when set) |
| `FORK_URL` | the team's GitHub fork (optional) |
| `HOST` / `PORT` | `0.0.0.0` and whatever the host assigns (the Dockerfile sets both; most hosts override `PORT`) |

Accounts: users sign up on the landing page (name, email, password; PBKDF2 hashes in sqlite) and get a signed, HttpOnly session
cookie. `/app` and `/api/token` need that cookie, so only signed-up users can start calls (which spend Gemini minutes).
The cookie is marked Secure when the request arrives over https (directly or via `X-Forwarded-Proto`).

## 2. Run the worker

On the machine that has the data bundle, with the same `LIVEKIT_*` and `AGENT_NAME` in `.env.local`:

```
bash scripts/demo_worker.sh
```

LiveKit dispatches that worker into every room the website creates (`web-<user>-…`) and into the Chert FaceTime line. The
worker's `DANCE` should match the website's.

## 3. Check it

```
curl https://<server>/api/health     # ok, agent_name, dance, livekit (keys present), users
curl https://<server>/api/config     # live, facetime number, fork
curl https://<server>/api/dances     # the lessons with parts, bpm, steps
```

## 4. The landing page on Vercel (launch day)

The landing page is static, so it can go up on Vercel in a minute while the server runs elsewhere:

1. In `web/landing.html` set `<meta name="brodie-api" content="https://<your server>">` to the public https URL of the
   server (a Cloudflare quick tunnel to this PC works: `cloudflared tunnel --url http://localhost:8099`, or the hosted
   server from section 1). The waitlist posts there across origins; the server allows it (CORS on `/api/waitlist` only).
2. Deploy the `web/` folder: `cd web && vercel --prod` (or drag the folder onto vercel.com). `web/vercel.json` serves
   `landing.html` at `/`.
3. On the Vercel copy, accounts and the call page are hidden (they need the server's own origin for the session cookie);
   visitors join the waitlist. For a quick check without redeploying, open `https://<vercel>/?api=https://<server>`.

The waitlist lives in the server's sqlite file: `GET /api/waitlist` is the public count, `GET /api/waitlist/export?key=<ADMIN_KEY>`
is the CSV for the team (set `ADMIN_KEY` in the server's environment).
