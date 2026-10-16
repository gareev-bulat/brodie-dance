"""The line frees soon after a FaceTime call ends (brodie/line.py call_started / call_ended, facetime/line_report.py), end to
end against the real aiohttp app with a fake clock: the worker's reporter posts over real HTTP, visitors use the site's API.

    python scripts/test_line_autorelease.py
"""
import asyncio, os, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'src')]
TMP = tempfile.mkdtemp(prefix='brodie-autorelease-')
os.environ.update(BRODIE_DB=os.path.join(TMP, 'brodie.db'), SESSION_SECRET='test', HEARTBEAT_KEY='k3y', FACETIME_NUMBER='+16282892384',
                  LINE_CAP_S='900', LINE_COOLDOWN_S='15', ADMIN_KEY='adm', BRODIE_HEARTBEAT_KEY='k3y')
os.environ.pop('DEV_PREVIEW', None)

from aiohttp.test_utils import TestServer, TestClient
from brodie import server, line
from facetime import line_report

CLOCK = [1_000_000.0]
line.now = lambda: CLOCK[0]
RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond)))
    print(f'  {"PASS" if cond else "FAIL"}  {name}' + (f'   ({detail})' if detail != '' else ''), flush=True)


def beat(): server._LAST_BEAT['t'] = time.time()
def mmss(s): return f'{int(s) // 60:02d}:{int(s) % 60:02d}'
async def worker(event, call_id='abcd1234-call'): return await asyncio.to_thread(line_report.report, event, call_id)


async def visitor(srv, email):
    c = TestClient(srv); await c.start_server()
    assert (await c.post('/api/start', json={'email': email})).status == 200
    return c


async def st(c):
    r = await c.get('/api/line'); d = await r.json(); return d.get('state'), d.get('seconds_left')


async def main():
    srv = TestServer(server.app()); A = await visitor(srv, 'a@example.com'); B = await visitor(srv, 'b@example.com')
    os.environ['BRODIE_SERVER'] = str(A.make_url('')).rstrip('/')
    adm = {'key': 'adm'}
    try:
        beat()
        print('\n1. a visitor clicks, dances 4 minutes, hangs up WITHOUT "I\'m done"')
        r = await A.post('/api/line/claim'); check('A clicks FaceTime Brodie: the line is theirs', (await st(A)) == ('mine', 900))
        check('B sees busy 15:00', (await st(B)) == ('busy', 900))
        check("worker reports the call start (A's session kept)", await worker('start') and line.info()['source'] == 'visitor')
        CLOCK[0] += 240
        check('worker reports the call end', await worker('end'))
        s = await st(B); check('B now sees busy for the cooldown only (0:15, not 11:00)', s == ('busy', 15), f'{s[0]} {mmss(s[1])}')
        CLOCK[0] += 14; check('still busy 1 s before the cooldown ends', (await st(B))[0] == 'busy')
        CLOCK[0] += 1; check('free 15 s after the hang-up (4:15 instead of 15:00)', (await st(B)) == ('open', 0))
        check('B can claim now', (await B.post('/api/line/claim')).status == 200 and (await st(B))[0] == 'mine')
        await B.post('/api/line/done')

        print('\n2. someone FaceTimes the number directly (no click on the site)')
        CLOCK[0] += 30; await worker('start', 'direct-call')
        check('the site shows busy to everyone', (await st(A)) == ('busy', 900) and (await st(B)) == ('busy', 900))
        info = line.info(); check('stats page: started by a FaceTime caller', 'FaceTime caller' in info['started_by'], info['started_by'])
        check('a click during their call is refused', (await A.post('/api/line/claim')).status == 409)
        CLOCK[0] += 300; await worker('end', 'direct-call')
        check('their hang-up frees the line after the cooldown', (await st(A)) == ('busy', 15))
        CLOCK[0] += 15; check('open again', (await st(A)) == ('open', 0))

        print('\n3. a call ends while the line is already free: the cooldown still protects the next caller')
        CLOCK[0] += 60; await worker('end', 'late-end')
        info = line.info(); check("busy for Chert's cooldown", (await st(A)) == ('busy', 15) and 'cooldown' in info['started_by'], info['started_by'])
        CLOCK[0] += 5; await worker('start', 'next-call')
        check('a new call during the cooldown takes the line as a FaceTime session', line.info()['source'] == 'facetime' and (await st(B)) == ('busy', 900))
        CLOCK[0] += 120; await worker('end', 'next-call'); CLOCK[0] += 15

        print('\n4. retries, admin sessions, "I\'m done" and the 15-minute limit still work')
        await A.post('/api/line/claim'); CLOCK[0] += 60; await worker('start'); CLOCK[0] += 60
        await worker('end'); first = (await st(B))[1]; CLOCK[0] += 1; await worker('end')
        check('a repeated end (worker retry) does not extend or reset the cooldown', first == 15 and (await st(B))[1] == 14)
        CLOCK[0] += 14
        r = await A.post('/api/admin/line/busy', params=dict(adm, minutes='10'))
        check('admin Mark busy 10 min', r.status == 200 and (await st(B)) == ('busy', 600), r.status)
        await worker('start'); CLOCK[0] += 120; await worker('end')
        check("a call ending during an admin session leaves it alone (8:00 left, not 3:00)", (await st(B)) == ('busy', 480))
        await A.post('/api/admin/line/free', params=adm)
        await A.post('/api/line/claim'); CLOCK[0] += 30
        r = await A.post('/api/line/done'); check('"I\'m done dancing" still frees at once', (await r.json()).get('freed') and (await st(B)) == ('open', 0))
        await A.post('/api/line/claim'); CLOCK[0] += 900
        check('no end report (worker down): the line frees at 15:00 as before', (await st(B)) == ('open', 0))

        print('\n5. safety')
        r = await A.post('/api/line/call', json={'event': 'end'}); check('without the worker key: refused', r.status == 403, r.status)
        r = await A.post('/api/line/call', json={'event': 'nope'}, headers={'X-Brodie-Key': 'k3y'}); check('a bad event: 400', r.status == 400, r.status)
        good = os.environ['BRODIE_SERVER']; os.environ['BRODIE_SERVER'] = 'http://127.0.0.1:9'   # nothing listens there
        t = time.monotonic(); ok = await worker('end')
        check('website unreachable: the report fails quietly and quickly (the call is never held up)', ok is False and time.monotonic() - t < 15, f'{time.monotonic() - t:.1f} s')
        os.environ['BRODIE_SERVER'] = good
        os.environ.pop('BRODIE_HEARTBEAT_KEY'); check('worker without the key: reporting is off', await worker('end') is False)
        os.environ['BRODIE_HEARTBEAT_KEY'] = 'k3y'
        log = line.log(40); events = [e['event'] for e in log]
        check('the stats log shows the call starts and ends', 'call ended' in events and ('call' in events or 'busy' in events), sorted(set(events)))
    finally:
        await A.close(); await B.close()


asyncio.run(main())
n = sum(ok for _, ok in RESULTS)
print(f'\n{n}/{len(RESULTS)} passed'); sys.exit(0 if n == len(RESULTS) else 1)
