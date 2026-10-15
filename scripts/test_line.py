"""Brodie's one line (brodie/line.py, facetime/line_client.py) end to end against the real aiohttp app, with a fake clock and a
throwaway sqlite file. The worker's side is the real line_client over HTTP, so a FaceTime caller is exercised the same way.

    python scripts/test_line.py
"""
import asyncio, os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'src')]
TMP = tempfile.mkdtemp(prefix='brodie-line-')
os.environ.update(BRODIE_DB=os.path.join(TMP, 'brodie.db'), SESSION_SECRET='test', HEARTBEAT_KEY='k3y', AGENT_NAME='brodie-dance',
                  LIVEKIT_URL='wss://test.livekit.cloud', LIVEKIT_API_KEY='key', LIVEKIT_API_SECRET='secret',
                  LINE_CAP_S='900', LINE_BUFFER_S='30', LINE_PENDING_S='60')
for k in ('LINE_WARN_S', 'LINE_WRAP_S'): os.environ.pop(k, None)

from aiohttp.test_utils import TestServer, TestClient
from brodie import server, line
from facetime import line_client

CLOCK = [1_000_000.0]
line.now = lambda: CLOCK[0]
server.mint_call = lambda u, room=None: {'url': 'wss://test', 'token': 'tok', 'room': room, 'identity': f'user-{u["id"]}'}
KEY = {'X-Brodie-Key': 'k3y'}
RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond)))
    print(f'  {"PASS" if cond else "FAIL"}  {name}' + (f'   ({detail})' if detail else ''), flush=True)


def mmss(s): return f'{int(s) // 60:02d}:{int(s) % 60:02d}'


async def beat(c):
    server._LAST_BEAT['t'] = __import__('time').time()


async def status(c):
    r = await c.get('/api/line'); return await r.json()


async def user(srv, email):
    c = TestClient(srv); await c.start_server()
    r = await c.post('/api/signup', json={'name': email.split('@')[0], 'email': email, 'password': 'password123'})
    assert r.status == 200, await r.text()
    return c


async def main():
    srv = TestServer(server.app()); await srv.start_server()
    os.environ['BRODIE_SERVER'] = str(srv.make_url('')).rstrip('/'); os.environ['BRODIE_HEARTBEAT_KEY'] = 'k3y'
    a = await user(srv, 'ana@example.com'); b = await user(srv, 'ben@example.com')
    worker = lambda fn, *args: asyncio.to_thread(fn, *args)            # the worker's real HTTP client
    await beat(a)

    print('\n1. open -> web call -> busy with a countdown -> second caller blocked -> early end -> open')
    s = await status(a); check('line open with the worker online', s['state'] == 'open', s)
    r = await a.get('/api/token'); tok = await r.json(); room = tok.get('room')
    check('caller A gets a call', r.status == 200 and room, r.status)
    ok, _ = await worker(line_client.start, room, None); check('worker confirms A\'s room', ok)
    CLOCK[0] += 42
    s = await status(b)
    check('line busy, countdown <= 15:00 + reset buffer and counting down', s['state'] == 'busy' and 0 < s['seconds_left'] <= 900 + 30,
          f'{mmss(s["seconds_left"])} left after 42 s of call')
    check('countdown is the real time left (cap - 42 s + buffer)', s['seconds_left'] == 900 - 42 + 30, s['seconds_left'])
    r = await b.get('/api/token'); d = await r.json()
    check('second web caller blocked with 409 and the same busy status', r.status == 409 and d['state'] == 'busy' and d['seconds_left'] == s['seconds_left'], d)
    ok, d = await worker(line_client.start, 'chert-room-1', 'call-77')
    check('FaceTime caller blocked while A is on (worker told busy + minutes left)', not ok and d['seconds_left'] > 0, line_client.busy_text(d['seconds_left']))
    CLOCK[0] += 300
    await worker(line_client.end, room)
    s = await status(b); check('A hangs up early: busy for the reset buffer only', s['state'] == 'busy' and s['seconds_left'] == 30, mmss(s['seconds_left']))
    CLOCK[0] += 30
    s = await status(b); check('line open right after the reset buffer', s['state'] == 'open', s)
    r = await b.get('/api/token'); check('caller B can now get a call', r.status == 200)
    room_b = (await r.json())['room']

    print('\n2. abandoned web click: released after 60 s when the worker never confirms')
    s = await status(a); check('pending web call shows busy (worst case, never jumps up)', s['state'] == 'busy' and s['seconds_left'] == 930, s)
    CLOCK[0] += 59
    r = await a.get('/api/token'); check('still held at 59 s', r.status == 409)
    CLOCK[0] += 2
    s = await status(a); check('released at 60 s: line open', s['state'] == 'open', s)
    ok, _ = await worker(line_client.start, room_b, None)
    check('the abandoned room cannot hold it after expiry, a fresh start takes the line again', ok)
    await worker(line_client.end, room_b); CLOCK[0] += 31

    print('\n3. FaceTime call crashes and never reports its end: expires on its own after the cap')
    ok, _ = await worker(line_client.start, 'chert-room-2', 'call-88'); check('FaceTime call takes the open line', ok)
    r = await a.get('/api/token'); check('web caller blocked during the FaceTime call', r.status == 409)
    CLOCK[0] += 900
    s = await status(a); check('at the 15-minute cap: reset buffer left', s['state'] == 'busy' and s['seconds_left'] == 30, s)
    CLOCK[0] += 30
    s = await status(a); check('crashed call expired: line open', s['state'] == 'open', s)
    ok = await worker(line_client.end, 'chert-room-2'); s = await status(a)
    check('a late end from the dead call does not re-busy the line', s['state'] == 'open', s)

    print('\n4. line state survives a server restart')
    ok, _ = await worker(line_client.start, 'chert-room-3', 'call-99'); CLOCK[0] += 100
    before = await status(a)
    srv2 = TestServer(server.app()); await srv2.start_server(); c2 = TestClient(srv2); await c2.start_server()
    after = await status(c2)
    check('busy with the same time left on a fresh server', after['state'] == 'busy' and after['seconds_left'] == before['seconds_left'], f'{mmss(after["seconds_left"])}')
    await worker(line_client.end, 'chert-room-3'); CLOCK[0] += 31; await c2.close(); await srv2.close()

    print('\n5. the coach goes offline')
    server._LAST_BEAT['t'] = 0
    s = await status(a); check('line shows offline', s['state'] == 'offline', s)
    r = await a.get('/api/token'); check('no call handed out while offline', r.status == 503)
    await beat(a); s = await status(a); check('heartbeat back: line open again', s['state'] == 'open', s)

    print('\n6. worker auth and lesson clock')
    r = await a.post('/api/line/start', json={'room': 'x'}); check('line start without the key is refused', r.status == 403)
    tl = line_client.timeline(900); check('lesson clock: last part 13:00, wrap 14:30, hang up 15:00', tl == [(780, 'last_part'), (870, 'wrap'), (900, 'hangup')], tl)
    check('no cap with LINE_CAP_S=0', line_client.timeline(0) == [])

    print('\n7. not configured (no HEARTBEAT_KEY): nothing changes')
    os.environ.pop('HEARTBEAT_KEY')
    ok, _ = line.start('chert-room-4'); s = await status(a)
    check('line is always open and never refuses', ok and s['state'] == 'open' and s['enabled'] is False, s)
    r = await a.get('/api/token'); r2 = await b.get('/api/token'); check('two web calls at once, as before', r.status == 200 and r2.status == 200)
    for c in (a, b): await c.close()
    await srv.close()


if __name__ == '__main__':
    asyncio.run(main())
    bad = [n for n, ok in RESULTS if not ok]
    print(f'\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed' + (f'; FAILED: {bad}' if bad else ''))
    sys.exit(1 if bad else 0)
