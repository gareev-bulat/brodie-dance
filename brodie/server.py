"""Brodie Dance on the web: the landing page and accounts. Lessons happen over FaceTime only (Brodie's worker on the Chert line).

  python -m brodie.server [--host 127.0.0.1] [--port 8765]
Routes: /  landing (web/index.html)
        POST /api/start {email}  the landing page's one field: creates the account (no password) or signs into it
        POST /api/signup {name, email, password}   POST /api/login {email, password}   POST /api/logout   GET /api/me
        GET /api/line   signed in only: Brodie's one line, open | busy (seconds_left) | offline, plus the FaceTime number when open
        GET /api/config  public settings (dance, fork; no number, no status)   GET /api/dances   GET /api/health
        POST /api/heartbeat  Brodie's worker saying it is up (X-Brodie-Key: HEARTBEAT_KEY); "offline" on the page comes from it
        POST /api/line/start {room, call_id}   POST /api/line/end {room}   the worker reporting sessions (X-Brodie-Key)
Sessions are a signed cookie (brodie/users.py); HOST/PORT come from the environment on a hosting service (docs/HOSTING.md).
"""
import argparse, datetime, glob, hmac, json, os, time
from aiohttp import web
from brodie import line, users

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')
COOKIE = 'bd_session'
# Brodie's worker runs on another machine; it POSTs /api/heartbeat every ~20 s (facetime/worker.py, BRODIE_SERVER +
# BRODIE_HEARTBEAT_KEY). With HEARTBEAT_KEY set here, "online" means a heartbeat in the last HEARTBEAT_TTL seconds.
HEARTBEAT_TTL = float(os.environ.get('HEARTBEAT_TTL', '75'))
_LAST_BEAT = {'t': 0.0, 'agent': ''}


def worker_online():
    """True / False when the worker heartbeat is configured (HEARTBEAT_KEY), None when it is not (unknown)."""
    if not os.environ.get('HEARTBEAT_KEY'): return None
    return time.time() - _LAST_BEAT['t'] < HEARTBEAT_TTL


# ---- dev only: ?preview=free|busy|offline|loggedout shows each state of the landing page without a real call ----
# Off unless DEV_PREVIEW=1 is set (never on the host): without it the parameter is ignored everywhere.
PREVIEW_BUSY_S = 402                      # the busy preview counts down from 6:42, then turns free (the switch-over), then starts again
_PREVIEW = {'t0': 0.0}


def facetime_number():
    """FACETIME_NUMBER as (shown, dialled): any formatting is stripped for the link (+16282892384); a US number is shown as
    +1 (628) 289-2384, anything else as given. ('', '') when unset."""
    raw = os.environ.get('FACETIME_NUMBER', '').strip()
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if not digits: return raw, raw                       # an Apple ID email is dialled as is
    dial = '+' + digits if raw.startswith('+') or len(digits) == 11 else digits
    if len(digits) == 11 and digits[0] == '1': return f'+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}', '+' + digits
    if len(digits) == 10: return f'+1 ({digits[:3]}) {digits[3:6]}-{digits[6:]}', '+1' + digits
    return raw, dial


def preview(req):
    p = req.query.get('preview', '')
    return p if os.environ.get('DEV_PREVIEW') == '1' and p in ('free', 'busy', 'offline', 'loggedout') else None


def _preview_line(p):
    st = {'enabled': True, 'cap_s': line.cap_s(), 'buffer_s': line.buffer_s(), 'preview': p}
    if p == 'busy':
        now = time.time()
        if now - _PREVIEW['t0'] > PREVIEW_BUSY_S + 15: _PREVIEW['t0'] = now
        left = PREVIEW_BUSY_S - (now - _PREVIEW['t0'])
        if left > 0: return dict(st, state='busy', seconds_left=int(round(left)))
        p = 'free'
    if p == 'offline': return dict(st, state='offline', seconds_left=0)
    shown, dial = facetime_number()
    return dict(st, state='open', seconds_left=0, facetime=shown or '+1 (555) 010-0000', facetime_dial=dial or '+15550100000')


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


async def start(req):
    """One field: an email signs the visitor in (a new email creates the account). Then the page shows Brodie's line."""
    b = await _body(req)
    try: u, created = users.by_email(b.get('email', ''))
    except ValueError as e: return web.json_response({'error': str(e).replace('invalid: ', '')}, status=400)
    return _with_session(web.json_response({'ok': True, 'new': created, 'user': _public(u)}), req, u['id'])


async def me(req):
    p = preview(req)
    if p == 'loggedout': return web.json_response({'error': 'not logged in'}, status=401)
    if p: return web.json_response({'user': {'id': 0, 'name': 'preview', 'email': 'preview@example.com', 'calls': 0}})
    u = current_user(req)
    if u is None: return web.json_response({'error': 'not logged in'}, status=401)
    return web.json_response({'user': _public(u)})


async def health(req):
    """Public: ok. With ?key=<ADMIN_KEY>: the worker's heartbeat and the line too (Brodie's status is for signed-in visitors)."""
    out = {'ok': True, 'dance': os.environ.get('DANCE', '')}
    key = os.environ.get('ADMIN_KEY', '')
    if key and hmac.compare_digest(req.query.get('key', ''), key):
        beat = _LAST_BEAT['t']
        out.update(users=users.count(), worker_online=worker_online(), line=line.status(worker_online()),
                   last_heartbeat_s_ago=round(time.time() - beat, 1) if beat else None)
    return web.json_response(out)


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


async def line_status(req):
    """Signed in only (logged out: 401 and nothing else). The FaceTime number is included only while Brodie is free."""
    p = preview(req)
    if p and p != 'loggedout': return web.json_response(_preview_line(p), headers={'Cache-Control': 'no-store'})
    if p == 'loggedout' or current_user(req) is None: return web.json_response({'error': 'sign up first'}, status=401, headers={'Cache-Control': 'no-store'})
    st = line.status(worker_online())
    if st['state'] == 'open': st['facetime'], st['facetime_dial'] = facetime_number()
    return web.json_response(st, headers={'Cache-Control': 'no-store'})


async def line_start(req):
    """The worker bound a caller (web or FaceTime): 200 {ok} or 409 with the busy status (the worker then says so and hangs up)."""
    if not _worker_ok(req): return web.json_response({'error': 'forbidden'}, status=403)
    b = await _body(req); room = str(b.get('room', ''))[:128]
    if not room: return web.json_response({'error': 'room required'}, status=400)
    ok, st = line.start(room, str(b.get('call_id', '') or '')[:64])
    if not ok: print(f'[line] start {room!r} refused: busy, {st.get("seconds_left")} s left', flush=True)
    if ok: return web.json_response({'ok': True, 'cap_s': line.cap_s()})
    return web.json_response(dict(st, error='busy'), status=409)


async def line_end(req):
    if not _worker_ok(req): return web.json_response({'error': 'forbidden'}, status=403)
    b = await _body(req); room = str(b.get('room', ''))[:128]
    ended = line.end(room, str(b.get('reason', '') or 'ended')[:80])
    if not ended: print(f'[line] end {room!r}: not the line holder (ignored)', flush=True)
    return web.json_response({'ok': True, 'ended': ended})


async def line_touch(req):
    """The worker's keepalive while a session runs: when it stops (crash, network gone) the line frees within LINE_STALE_S."""
    if not _worker_ok(req): return web.json_response({'error': 'forbidden'}, status=403)
    b = await _body(req)
    return web.json_response({'ok': True, 'held': line.touch(str(b.get('room', ''))[:128])})


def _admin_ok(req):
    key = os.environ.get('ADMIN_KEY', '')
    return bool(key) and hmac.compare_digest(req.query.get('key', ''), key)


async def admin_line(req):
    """The stats page's line panel: GET /api/admin/line?key=<ADMIN_KEY>. The session holding the line, its age and time left,
    the worker's last heartbeat and keepalive, and the recent starts, ends and resets."""
    if not _admin_ok(req): return web.json_response({'error': 'forbidden'}, status=403)
    beat = _LAST_BEAT['t']
    return web.json_response(dict(line.info(worker_online()), heartbeat_s_ago=round(time.time() - beat) if beat else None, log=line.log(20)),
                             headers={'Cache-Control': 'no-store'})


async def admin_line_reset(req):
    """POST /api/admin/line/reset?key=<ADMIN_KEY>: the line is free now (no reset buffer); the held session is logged as ended
    ("admin reset"). Without the key: 403 and nothing changes."""
    if not _admin_ok(req): return web.json_response({'error': 'forbidden'}, status=403)
    before = line.reset('admin reset')
    return web.json_response({'ok': True, 'before': before, 'line': line.info(worker_online())}, headers={'Cache-Control': 'no-store'})


async def config(_req):
    """Public settings for the landing page. No FaceTime number and no status here: those are for signed-in visitors (/api/line)."""
    return web.json_response({'ok': True, 'dance': os.environ.get('DANCE', ''),
                              'fork': os.environ.get('FORK_URL', 'https://github.com/zxpeng2007/ivyhacks-brodie-dance')})


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


def app():
    users.init(); line.init()
    a = web.Application(client_max_size=64 * 1024)
    a.add_routes([web.post('/api/signup', signup), web.post('/api/login', login), web.post('/api/logout', logout), web.post('/api/start', start), web.get('/api/me', me),
                  web.get('/api/health', health), web.post('/api/heartbeat', heartbeat), web.get('/api/config', config),
                  web.get('/api/line', line_status), web.post('/api/line/start', line_start), web.post('/api/line/end', line_end), web.post('/api/line/touch', line_touch),
                  web.get('/api/admin/line', admin_line), web.post('/api/admin/line/reset', admin_line_reset), web.get('/api/dances', dances),
                  web.post('/api/waitlist', waitlist_join), web.get('/api/waitlist', waitlist_count), web.options('/api/waitlist', waitlist_options),
                  web.get('/api/waitlist/export', waitlist_export), web.get('/api/stats', stats),
                  web.get('/', landing), web.get('/index.html', landing), web.static('/', WEB)])
    return a


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'), help='0.0.0.0 on a hosting service')
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8765')), help='hosting services set PORT')
    a = ap.parse_args()
    users.init(); line.init()
    print(f'[brodie] web on http://{a.host}:{a.port}: landing at /, {users.count()} users in {users.DB}', flush=True)
    web.run_app(app(), host=a.host, port=a.port, print=None)
