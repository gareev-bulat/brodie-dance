# Hosting Brodie Dance

## Handoff checklist (deploying the website on a remote host)

1. Build from the repo root with the `Dockerfile` (`main` branch, Python 3.12 slim, `requirements-web.txt`; no torch, no MediaPipe).
   The container listens on `PORT` (default 8765) and `HOST=0.0.0.0`.
2. Set these environment variables on the host (values come from Steven privately, never from the repo):
   `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `AGENT_NAME=brodie-dance`, `DANCE=sbbyj`,
   `SESSION_SECRET` (any long random string), `ADMIN_KEY` (any string, for the waitlist CSV),
   optional `FACETIME_NUMBER`, `FORK_URL=https://github.com/zxpeng2007/ivyhacks-brodie-dance`.
3. Give the container a persistent disk and point `BRODIE_DB` at it (e.g. `/data/brodie.db`), or accounts and the
   waitlist vanish on every redeploy. To carry over the signups collected so far, copy Steven's `brodie.db` file there
   before the first start (or merge the CSV later).
4. Check: `GET /api/health` returns `"livekit": true`; `GET /api/waitlist` returns the count; the landing page at `/`.
5. Tell Steven the public https URL. The Vercel copy of the landing page posts its waitlist to whatever
   `<meta name="brodie-api">` in `web/index.html` says; it must change from the temporary tunnel to the host URL
   (one commit; Vercel redeploys itself). Once the host serves the landing page too, the Vercel copy is optional.
6. The coach (the worker) keeps running on Steven's PC for now; it needs the organisers' data bundle and the models,
   which are not in git. Moving it later: README steps 1-2 plus `bash scripts/demo_worker.sh` with the same
   `LIVEKIT_*` and `AGENT_NAME`.

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
| `HEARTBEAT_KEY` | any long random string, the same as the worker's `BRODIE_HEARTBEAT_KEY`; with it, "brodie is online" and new calls only while the worker's heartbeat arrives (unset: no badge, calls always handed out) |
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

Add to that machine's `.env.local` so the website knows Brodie is up: `BRODIE_SERVER=https://<server>` and
`BRODIE_HEARTBEAT_KEY=<the server's HEARTBEAT_KEY>`. The worker then POSTs `/api/heartbeat` every 20 s; when it stops (laptop
closed, worker stopped) the page says "brodie is offline" within about a minute and `/api/token` refuses new calls with that message.

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

1. In `web/index.html` set `<meta name="brodie-api" content="https://<your server>">` to the public https URL of the
   server (a Cloudflare quick tunnel to this PC works: `cloudflared tunnel --url http://localhost:8099`, or the hosted
   server from section 1). The waitlist posts there across origins; the server allows it (CORS on `/api/waitlist` only).
2. Deploy the `web/` folder: `cd web && vercel --prod` (or drag the folder onto vercel.com). `web/vercel.json` serves
   the landing page at `/`.
3. On the Vercel copy, accounts and the call page are hidden (they need the server's own origin for the session cookie);
   visitors join the waitlist. For a quick check without redeploying, open `https://<vercel>/?api=https://<server>`.

The waitlist lives in the server's sqlite file: `GET /api/waitlist` is the public count, `GET /api/waitlist/export?key=<ADMIN_KEY>`
is the CSV for the team (set `ADMIN_KEY` in the server's environment).
