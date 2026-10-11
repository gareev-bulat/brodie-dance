"""Brodie Dance on the web: the landing page, accounts, and the call page that joins a LiveKit room with Brodie's worker.

  python -m brodie.server [--host 127.0.0.1] [--port 8765]     # LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET, AGENT_NAME in env
Routes: /  landing (web/landing.html)      /app  the call page (web/app.html, needs a login)
        POST /api/signup {name, email, password}   POST /api/login {email, password}   POST /api/logout   GET /api/me
        GET /api/token   a LiveKit token for a fresh room "web-<user>-<id>" with AGENT_NAME dispatched into it (login required)
        GET /api/config  what the landing page shows (FaceTime number, lesson, fork)   GET /api/dances   GET /api/health
Sessions are a signed cookie (brodie/users.py); HOST/PORT come from the environment on a hosting service (docs/HOSTING.md).
"""
import argparse, datetime, glob, json, os, secrets
from aiohttp import web
from brodie import users

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')
COOKIE = 'bd_session'
LIVEKIT_KEYS = ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET')


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


def mint_call(u):
    """A token for the user in a fresh room, with Brodie's worker (AGENT_NAME) dispatched into it."""
    from livekit import api
    from livekit.protocol.agent_dispatch import RoomAgentDispatch
    from livekit.protocol.room import RoomConfiguration
    room, identity = f'web-{u["id"]}-{secrets.token_hex(3)}', f'user-{u["id"]}'
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
    info = mint_call(u); users.bump_calls(u['id'])
    return web.json_response(info)


async def health(_req):
    return web.json_response({'ok': True, 'agent_name': os.environ.get('AGENT_NAME', ''), 'dance': os.environ.get('DANCE', ''),
                              'livekit': all(os.environ.get(k) and '<' not in os.environ.get(k, '') for k in LIVEKIT_KEYS),
                              'users': users.count()})


async def config(_req):
    """What the landing page needs to link up with the running system (all from env, nothing secret)."""
    return web.json_response({'ok': True, 'live': all(os.environ.get(k) and '<' not in os.environ.get(k, '') for k in LIVEKIT_KEYS) and bool(os.environ.get('AGENT_NAME')),
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


async def landing(_req): return web.FileResponse(os.path.join(WEB, 'index.html'))


async def app_page(req):
    if current_user(req) is None: raise web.HTTPFound('/?next=app')
    return web.FileResponse(os.path.join(WEB, 'app.html'))


def app():
    users.init()
    a = web.Application(client_max_size=64 * 1024)
    a.add_routes([web.post('/api/signup', signup), web.post('/api/login', login), web.post('/api/logout', logout), web.get('/api/me', me),
                  web.get('/api/token', token), web.get('/api/health', health), web.get('/api/config', config), web.get('/api/dances', dances),
                  web.post('/api/waitlist', waitlist_join), web.get('/api/waitlist', waitlist_count), web.options('/api/waitlist', waitlist_options),
                  web.get('/api/waitlist/export', waitlist_export),
                  web.get('/', landing), web.get('/index.html', landing), web.get('/app', app_page), web.static('/', WEB)])
    return a


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'), help='0.0.0.0 on a hosting service')
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8765')), help='hosting services set PORT')
    a = ap.parse_args()
    users.init()
    print(f'[brodie] web on http://{a.host}:{a.port}: landing at /, call page at /app, {users.count()} users in {users.DB}', flush=True)
    web.run_app(app(), host=a.host, port=a.port, print=None)
