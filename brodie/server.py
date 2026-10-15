"""Brodie Dance on the web: the landing page, accounts, and the call page that joins a LiveKit room with Brodie's worker.

  python -m brodie.server [--host 127.0.0.1] [--port 8765]     # LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET, AGENT_NAME in env
Routes: /  landing (web/index.html)      /app  the call page (web/app.html, needs a login)
        POST /api/signup {name, email, password}   POST /api/login {email, password}   POST /api/logout   GET /api/me
        GET /api/token   a LiveKit token for a fresh room "web-<user>-<id>" with AGENT_NAME dispatched into it (login required)
        GET /api/config  what the landing page shows (live status, fork)   GET /api/dances   GET /api/health
        POST /api/heartbeat  Brodie's worker saying it is up (X-Brodie-Key: HEARTBEAT_KEY); "online" on the page comes from it
        GET /api/line  the one line: open | busy (seconds_left) | offline (brodie/line.py)
        POST /api/line/start {room, call_id}   POST /api/line/end {room}   the worker reporting calls (X-Brodie-Key)
Sessions are a signed cookie (brodie/users.py); HOST/PORT come from the environment on a hosting service (docs/HOSTING.md).
"""
import argparse, datetime, glob, hmac, json, os, secrets, time
from aiohttp import web
from brodie import line, users

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')
COOKIE = 'bd_session'
LIVEKIT_KEYS = ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET')
# Brodie's worker runs on another machine; it POSTs /api/heartbeat every ~20 s (facetime/worker.py, BRODIE_SERVER +
# BRODIE_HEARTBEAT_KEY). With HEARTBEAT_KEY set here, "online" means a heartbeat in the last HEARTBEAT_TTL seconds.
HEARTBEAT_TTL = float(os.environ.get('HEARTBEAT_TTL', '75'))
_LAST_BEAT = {'t': 0.0, 'agent': ''}


def _keys_ok():
    return all(os.environ.get(k) and '<' not in os.environ.get(k, '') for k in LIVEKIT_KEYS)


def worker_online():
    """True / False when the worker heartbeat is configured (HEARTBEAT_KEY), None when it is not (unknown)."""
    if not os.environ.get('HEARTBEAT_KEY'): return None
    return time.time() - _LAST_BEAT['t'] < HEARTBEAT_TTL


def _public(u):
    return {'id': u['id'], 'name': u['name'], 'email': u['email'], 'calls': u['calls']}


def current_user(req):
    return users.get(users.check(req.cookies.get(COOKIE, '')))


def _with_session(resp, req, uid):
    secure = req.scheme == 'https' or req.headers.get('X-Forwarded-Proto', '') == 'https'
    resp.set_cookie(COOKIE, users.sign(uid), max_age=users.SESSION_DAYS * 86400, httponly=True, samesite='Lax', secure=secure, path='/')
    return resp


async def _body(req):
    try: b = await req.json()
    except Exception: b = {}
    return b if isinstance(b, dict) else {}


async def signup(req):
    b = await _body(req)
    try: u = users.create(b.get('name', ''), b.get('email', ''), b.get('password', ''))
    except ValueError as e:
        if str(e) == 'exists': return web.json_response({'error': 'That email already has an account. Log in instead.'}, status=409)
        return web.json_response({'error': str(e).replace('invalid: ', '')}, status=400)
    return _with_session(web.json_response({'ok': True, 'user': _public(u)}), req, u['id'])


async def login(req):
    b = await _body(req)
    u = users.verify(b.get('email', ''), b.get('password', ''))
    if u is None: return web.json_response({'error': 'Wrong email or password.'}, status=401)
    return _with_session(web.json_response({'ok': True, 'user': _public(u)}), req, u['id'])


async def logout(_req):
    resp = web.json_response({'ok': True}); resp.del_cookie(COOKIE, path='/'); return resp


async def me(req):
    u = current_user(req)
    if u is None: return web.json_response({'error': 'not logged in'}, status=401)
    return web.json_response({'user': _public(u)})


def room_name(u): return f'web-{u["id"]}-{secrets.token_hex(3)}'


def _busy(st):
    return web.json_response(dict(st, error="brodie's teaching someone right now.", line=st), status=409, headers={'Cache-Control': 'no-store'})


def mint_call(u, room=None):
    """A token for the user in a fresh room, with Brodie's worker (AGENT_NAME) dispatched into it."""
    from livekit import api
    from livekit.protocol.agent_dispatch import RoomAgentDispatch
    from livekit.protocol.room import RoomConfiguration
    room, identity = room or room_name(u), f'user-{u["id"]}'
    t = (api.AccessToken(os.environ['LIVEKIT_API_KEY'], os.environ['LIVEKIT_API_SECRET']).with_identity(identity).with_name(u['name'])
         .with_ttl(datetime.timedelta(hours=1))
         .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True, can_publish_data=True,
                                      can_update_own_metadata=True))
         .with_attributes({'brodie.uid': str(u['id']), 'brodie.name': u['name']}))
    if os.environ.get('AGENT_NAME'):
        t = t.with_room_config(RoomConfiguration(agents=[RoomAgentDispatch(agent_name=os.environ['AGENT_NAME'])]))
    return {'url': os.environ['LIVEKIT_URL'], 'token': t.to_jwt(), 'room': room, 'identity': identity}


async def token(req):
    u = current_user(req)
    if u is None: return web.json_response({'error': 'log in first'}, status=401)
    missing = [k for k in LIVEKIT_KEYS if not os.environ.get(k) or '<' in os.environ.get(k, '')]
    if missing: return web.json_response({'error': f'The server has no LiveKit keys yet ({", ".join(missing)}).'}, status=503)
    if worker_online() is False:
        return web.json_response({'error': "brodie is offline right now. try again in a little while."}, status=503)
    room = room_name(u)
    ok, st = line.reserve(room)                               # one line: refused while someone is on a call (or its reset buffer)
    if not ok: return _busy(st)
    info = mint_call(u, room); users.bump_calls(u['id'], os.environ.get('DANCE', ''))
    return web.json_response(info)


async def health(_req):
    beat = _LAST_BEAT['t']
    return web.json_response({'ok': True, 'agent_name': os.environ.get('AGENT_NAME', ''), 'dance': os.environ.get('DANCE', ''),
                              'livekit': _keys_ok(), 'users': users.count(), 'worker_online': worker_online(),
                              'last_heartbeat_s_ago': round(time.time() - beat, 1) if beat else None})


async def heartbeat(req):
    """Brodie's worker says it is up: POST /api/heartbeat with header X-Brodie-Key: <HEARTBEAT_KEY>."""
    key = os.environ.get('HEARTBEAT_KEY', '')
    if not key or not hmac.compare_digest(req.headers.get('X-Brodie-Key', ''), key):
        return web.json_response({'error': 'forbidden'}, status=403)
    b = await _body(req)
    _LAST_BEAT['t'] = time.time(); _LAST_BEAT['agent'] = str(b.get('agent', ''))[:64]
    return web.json_response({'ok': True})


def _worker_ok(req):
    key = os.environ.get('HEARTBEAT_KEY', '')
    return bool(key) and hmac.compare_digest(req.headers.get('X-Brodie-Key', ''), key)


async def line_status(_req):
    return web.json_response(line.status(worker_online()), headers=dict(CORS, **{'Cache-Control': 'no-store'}))


async def line_start(req):
    """The worker bound a caller (web or FaceTime): 200 {ok} or 409 with the busy status (the worker then says so and hangs up)."""
    if not _worker_ok(req): return web.json_response({'error': 'forbidden'}, status=403)
    b = await _body(req); room = str(b.get('room', ''))[:128]
    if not room: return web.json_response({'error': 'room required'}, status=400)
    ok, st = line.start(room, str(b.get('call_id', '') or '')[:64])
    print(f'[line] start {room!r}: {"ok" if ok else "BUSY " + str(st.get("seconds_left")) + " s left"}', flush=True)
    return web.json_response({'ok': True, 'cap_s': line.cap_s()}) if ok else _busy(st)


async def line_end(req):
    if not _worker_ok(req): return web.json_response({'error': 'forbidden'}, status=403)
    b = await _body(req); room = str(b.get('room', ''))[:128]
    ended = line.end(room)
    print(f'[line] end {room!r}: {"reset buffer " + str(line.buffer_s()) + " s" if ended else "not the line holder (ignored)"}', flush=True)
    return web.json_response({'ok': True, 'ended': ended})


async def config(_req):
    """What the landing page needs to link up with the running system (all from env, nothing secret).
    worker: True / False from the worker heartbeat, None when HEARTBEAT_KEY is not set (the page then shows no badge)."""
    up = worker_online()
    ready = _keys_ok() and bool(os.environ.get('AGENT_NAME'))
    return web.json_response({'ok': True, 'live': ready and up is not False, 'worker': up if ready else False,
                              'agent_name': os.environ.get('AGENT_NAME', ''), 'facetime': os.environ.get('FACETIME_NUMBER', ''),
                              'dance': os.environ.get('DANCE', ''), 'fork': os.environ.get('FORK_URL', 'https://github.com/zxpeng2007/ivyhacks-brodie-dance'),
                              'app': '/app'})


async def dances(_req):
    """The lessons in data/dances (newest first, as the handler picks them): name, parts, length, bpm, steps."""
    out = []
    try:
        from starter.dance.reference import Reference, DANCES_DIR
        live = os.environ.get('DANCE', '')
        paths = sorted(glob.glob(os.path.join(DANCES_DIR, '*.npz')), key=os.path.getmtime, reverse=True)
        for i, p in enumerate(paths):
            try:
                r = Reference(p)
                out.append({'name': r.name, 'parts': r.n_chunks, 'bpm': r.bpm, 'seconds': round(len(r.kps) / 10, 1),
                            'steps': [s for s in r.steps if s.strip()], 'video': bool(r.video_path), 'music': r.audio is not None,
                            'live': r.name == live or (not live and i == 0)})
            except Exception as e:
                out.append({'name': os.path.basename(p)[:-4], 'error': repr(e)})
    except Exception as e:
        return web.json_response({'dances': out, 'error': repr(e)})
    return web.json_response({'dances': out})


# ---- waitlist: open to the public landing page wherever it is hosted (CORS on these routes only) ----
CORS = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST, GET, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type'}


async def waitlist_join(req):
    b = await _body(req)
    try: pos, created = users.waitlist_add(b.get('email', ''), b.get('name', ''), req.headers.get('Origin', '') or req.headers.get('Referer', '')[:80])
    except ValueError as e: return web.json_response({'error': str(e)}, status=400, headers=CORS)
    return web.json_response({'ok': True, 'position': pos, 'new': created, 'count': users.waitlist_count()}, headers=CORS)


async def waitlist_count(_req):
    return web.json_response({'count': users.waitlist_count()}, headers=CORS)


async def waitlist_options(_req):
    return web.Response(status=204, headers=CORS)


async def waitlist_export(req):
    """CSV of the waitlist for the team: GET /api/waitlist/export?key=<ADMIN_KEY>."""
    key = os.environ.get('ADMIN_KEY', '')
    if not key or req.query.get('key', '') != key: return web.json_response({'error': 'forbidden'}, status=403)
    rows = users.waitlist_all()
    body = 'id,email,name,source,created\n' + ''.join(f'{r["id"]},{r["email"]},"{r["name"]}","{r["source"]}",{datetime.datetime.utcfromtimestamp(r["created"]).isoformat()}\n' for r in rows)
    return web.Response(text=body, content_type='text/csv')


async def stats(req):
    """Numbers for the team (stats page at /stats.html): GET /api/stats?key=<ADMIN_KEY>.
    TEAM_EMAILS (comma-separated) are left out so our own test calls don't count."""
    key = os.environ.get('ADMIN_KEY', '')
    if not key or not hmac.compare_digest(req.query.get('key', ''), key): return web.json_response({'error': 'forbidden'}, status=403)
    return web.json_response(users.stats(exclude=os.environ.get('TEAM_EMAILS', '').split(',')), headers={'Cache-Control': 'no-store'})


async def landing(_req): return web.FileResponse(os.path.join(WEB, 'index.html'))


async def app_page(req):
    if current_user(req) is None: raise web.HTTPFound('/?next=app')
    return web.FileResponse(os.path.join(WEB, 'app.html'))


def app():
    users.init(); line.init()
    a = web.Application(client_max_size=64 * 1024)
    a.add_routes([web.post('/api/signup', signup), web.post('/api/login', login), web.post('/api/logout', logout), web.get('/api/me', me),
                  web.get('/api/token', token), web.get('/api/health', health), web.post('/api/heartbeat', heartbeat), web.get('/api/config', config),
                  web.get('/api/line', line_status), web.post('/api/line/start', line_start), web.post('/api/line/end', line_end), web.get('/api/dances', dances),
                  web.post('/api/waitlist', waitlist_join), web.get('/api/waitlist', waitlist_count), web.options('/api/waitlist', waitlist_options),
                  web.get('/api/waitlist/export', waitlist_export), web.get('/api/stats', stats),
                  web.get('/', landing), web.get('/index.html', landing), web.get('/app', app_page), web.static('/', WEB)])
    return a


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'), help='0.0.0.0 on a hosting service')
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8765')), help='hosting services set PORT')
    a = ap.parse_args()
    users.init(); line.init()
    print(f'[brodie] web on http://{a.host}:{a.port}: landing at /, call page at /app, {users.count()} users in {users.DB}', flush=True)
    web.run_app(app(), host=a.host, port=a.port, print=None)
