"""The landing page's flow and Brodie's one line (brodie/line.py, facetime/line_client.py) end to end against the real aiohttp app,
with a fake clock and a throwaway sqlite file. The worker's side is the real line_client over HTTP (a FaceTime session).

    python scripts/test_line.py
"""
import asyncio, os, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'src')]
TMP = tempfile.mkdtemp(prefix='brodie-line-')
NUMBER = '+1 (628) 289-2384'
os.environ.update(BRODIE_DB=os.path.join(TMP, 'brodie.db'), SESSION_SECRET='test', HEARTBEAT_KEY='k3y', FACETIME_NUMBER=NUMBER,
                  LINE_CAP_S='900', LINE_BUFFER_S='30', ADMIN_KEY='adm')
for k in ('LINE_WARN_S', 'LINE_WRAP_S'): os.environ.pop(k, None)

from aiohttp.test_utils import TestServer, TestClient
from brodie import server, line
from facetime import line_client

CLOCK = [1_000_000.0]
line.now = lambda: CLOCK[0]
RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond)))
    print(f'  {"PASS" if cond else "FAIL"}  {name}' + (f'   ({detail})' if detail != '' else ''), flush=True)


def mmss(s): return f'{int(s) // 60:02d}:{int(s) % 60:02d}'
def beat(): server._LAST_BEAT['t'] = time.time()


async def line_of(c):
    r = await c.get('/api/line'); return r.status, await r.json()


async def client(srv):
    c = TestClient(srv); await c.start_server(); return c


async def main():
    srv = TestServer(server.app()); await srv.start_server()
    os.environ['BRODIE_SERVER'] = str(srv.make_url('')).rstrip('/'); os.environ['BRODIE_HEARTBEAT_KEY'] = 'k3y'
    worker = lambda fn, *args: asyncio.to_thread(fn, *args)            # the worker's real HTTP client
    beat()

    print('\n1. logged out: no number, no status anywhere')
    anon = await client(srv)
    code, d = await line_of(anon); check('GET /api/line is 401 with nothing in it', code == 401 and set(d) == {'error'}, d)
    leaks = []
    for path in ('/api/config', '/api/health', '/api/dances', '/', '/index.html', '/line.js'):
        r = await anon.get(path); body = await r.text()
        if '289-2384' in body or '2892384' in body or 'worker_online' in body or '"state":' in body: leaks.append(path)
    check('the number and the status appear in no public response', not leaks, leaks or 'config, health, dances, page, line.js')

    print('\n2. email sign-up shows the status')
    r = await anon.post('/api/start', json={'email': 'not-an-email'}); check('a bad email is refused', r.status == 400)
    r = await anon.post('/api/start', json={'email': 'Ana@Example.com'}); d = await r.json()
    check('one field: the email creates the account', r.status == 200 and d['new'] and d['user']['email'] == 'ana@example.com', d)
    code, d = await line_of(anon)
    check('signed in: Brodie is free, with the FaceTime number from FACETIME_NUMBER', code == 200 and d['state'] == 'open' and d['facetime'] == NUMBER, d)
    r = await anon.post('/api/login', json={'email': 'ana@example.com', 'password': 'anything'})
    check('an email-only account cannot be logged into with a guessed password', r.status == 401)

    print('\n3. returning visitor skips sign-up')
    r = await anon.get('/api/me'); check('the session cookie is remembered (GET /api/me)', r.status == 200 and (await r.json())['user']['email'] == 'ana@example.com')
    other = await client(srv)
    r = await other.post('/api/start', json={'email': 'ana@example.com'}); d = await r.json()
    check('the same email on a new device signs in, no second account', r.status == 200 and d['new'] is False, d)
    r = await anon.post('/api/logout'); code, _ = await line_of(anon); check('after log out the status is hidden again', code == 401)
    await anon.post('/api/start', json={'email': 'ana@example.com'}); ana = anon

    print('\n4. free -> FaceTime session -> busy with a countdown <= 15:00 -> second caller hears busy -> early end -> free')
    ok, _ = await worker(line_client.start, 'chert-room-1', 'call-1'); check('the worker takes the line for a FaceTime caller', ok)
    code, d = await line_of(ana)
    check('busy at the start: countdown <= 15:00 and no number', d['state'] == 'busy' and 0 < d['seconds_left'] <= 900 and 'facetime' not in d, mmss(d['seconds_left']))
    CLOCK[0] += 42
    code, d = await line_of(ana)
    check('42 s later: the real time left, counting down', d['seconds_left'] == 900 + 30 - 42, mmss(d['seconds_left']))
    ok, d2 = await worker(line_client.start, 'chert-room-2', 'call-2')
    msg = line_client.busy_text(d2.get('seconds_left'))
    check('a second FaceTime caller is refused with the minutes left', not ok and msg.startswith("Brodie's in a session right now. Call back in about 15 minutes"), msg)
    CLOCK[0] += 300
    await worker(line_client.end, 'chert-room-1')
    code, d = await line_of(ana); check('session ends early: only the 30 s reset buffer left', d['state'] == 'busy' and d['seconds_left'] == 30, mmss(d['seconds_left']))
    CLOCK[0] += 30
    code, d = await line_of(ana); check('free again right after the buffer, number back', d['state'] == 'open' and d['facetime'] == NUMBER, d['state'])

    print('\n5. a crashed session expires on its own')
    await worker(line_client.start, 'chert-room-3', 'call-3'); CLOCK[0] += 899
    code, d = await line_of(ana); check('still busy just before the cap', d['state'] == 'busy', mmss(d['seconds_left']))
    CLOCK[0] += 31
    code, d = await line_of(ana); check('no end ever reported: free after the cap + buffer', d['state'] == 'open', d['state'])

    print('\n6. the line survives a server restart')
    await worker(line_client.start, 'chert-room-4', 'call-4'); CLOCK[0] += 100
    _, before = await line_of(ana)
    srv2 = TestServer(server.app()); await srv2.start_server(); c2 = await client(srv2)
    await c2.post('/api/start', json={'email': 'ana@example.com'}); _, after = await line_of(c2)
    check('busy with the same time left on a fresh server', after['state'] == 'busy' and after['seconds_left'] == before['seconds_left'], mmss(after['seconds_left']))
    await worker(line_client.end, 'chert-room-4'); CLOCK[0] += 31; await c2.close(); await srv2.close()

    print('\n7. offline, the web call is gone, worker auth, the lesson clock')
    server._LAST_BEAT['t'] = 0
    code, d = await line_of(ana); check('no heartbeat: offline, no number', d['state'] == 'offline' and 'facetime' not in d, d['state'])
    beat()
    for path in ('/app', '/api/token'):
        r = await ana.get(path); check(f'{path} is gone (404)', r.status == 404, r.status)
    r = await ana.post('/api/line/start', json={'room': 'x'}); check('line start without the worker key is refused', r.status == 403)
    tl = line_client.timeline(900); check('lesson clock: last part 13:00, wrap 14:30, hang up 15:00', tl == [(780, 'last_part'), (870, 'wrap'), (900, 'hangup')], tl)
    print('\n7b. the FaceTime number: any formatting in, digits in the link, +1 (628) 289-2384 on the page')
    for raw in ('+16282892384', '+1 (628) 289-2384', '628-289-2384'):
        os.environ['FACETIME_NUMBER'] = raw
        code, d = await line_of(ana)
        check(f'FACETIME_NUMBER={raw!r}', d.get('facetime') == '+1 (628) 289-2384' and d.get('facetime_dial') == '+16282892384', (d.get('facetime'), d.get('facetime_dial')))
    os.environ['FACETIME_NUMBER'] = NUMBER

    print('\n8. the dev preview switch is off unless DEV_PREVIEW=1')
    os.environ.pop('DEV_PREVIEW', None)
    for q in ('free', 'busy', 'offline'):
        r = await other.get('/api/line?preview=' + q); r2 = await other.get('/api/me?preview=' + q)
        d = await r.json() if r.status == 200 else {}
        check(f'?preview={q} ignored on the live site (no fake status, no fake sign-in)', 'preview' not in d and r2.status == 200 and (await r2.json())['user']['email'] == 'ana@example.com', r.status)
    r = await anon.post('/api/logout'); r = await anon.get('/api/line?preview=busy')
    check('logged out + ?preview=busy: still 401, nothing shown', r.status == 401)
    os.environ['DEV_PREVIEW'] = '1'
    r = await anon.get('/api/line?preview=busy'); d = await r.json()
    check('with DEV_PREVIEW=1: the busy preview counts down from 6:42', r.status == 200 and d['state'] == 'busy' and d['seconds_left'] <= 402, d.get('seconds_left'))
    os.environ.pop('DEV_PREVIEW')
    for c in (ana, other): await c.close()
    await srv.close()


if __name__ == '__main__':
    asyncio.run(main())
    bad = [n for n, ok in RESULTS if not ok]
    print(f'\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed' + (f'; FAILED: {bad}' if bad else ''))
    sys.exit(1 if bad else 0)
