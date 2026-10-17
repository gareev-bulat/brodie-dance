"""The stats page's numbers (users.stats via GET /api/stats) end to end against the real aiohttp app with a fake line clock:
visits, taps, FaceTime calls reported by the worker (site and direct), guests apart from email sign-ups, team accounts hidden.

    python scripts/test_stats.py
"""
import asyncio, os, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'src')]
TMP = tempfile.mkdtemp(prefix='brodie-stats-')
os.environ.update(BRODIE_DB=os.path.join(TMP, 'brodie.db'), SESSION_SECRET='test', HEARTBEAT_KEY='k3y', FACETIME_NUMBER='+16282892384',
                  LINE_CAP_S='900', LINE_COOLDOWN_S='15', LINE_CLAIM_S='120', ADMIN_KEY='adm', TEAM_EMAILS='team@example.com')
os.environ.pop('DEV_PREVIEW', None)

from aiohttp.test_utils import TestServer, TestClient
from brodie import server, line

CLOCK = [time.time()]
line.now = lambda: CLOCK[0]
RESULTS = []
KEY = {'X-Brodie-Key': 'k3y'}


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond)))
    print(f'  {"PASS" if cond else "FAIL"}  {name}' + (f'   ({detail})' if detail != '' else ''), flush=True)


async def client(srv):
    c = TestClient(srv); await c.start_server(); return c


async def main():
    srv = TestServer(server.app())
    team, ana, guest, w, lazy = await client(srv), await client(srv), await client(srv), await client(srv), await client(srv)
    beat = lambda: server._LAST_BEAT.__setitem__('t', time.time())
    call = lambda ev, cid: w.post('/api/line/call', json={'event': ev, 'call_id': cid}, headers=KEY)
    stats = lambda: w.get('/api/stats', params={'key': 'adm'})
    try:
        beat()
        print('\n1. visits')
        for vid, dev in (('aaaaaaaa1', 'ios'), ('aaaaaaaa1', 'ios'), ('bbbbbbbb2', 'android'), ('cccccccc3', 'desktop'), ('dddddddd4', 'weird')):
            r = await w.post('/api/visit', json={'vid': vid, 'device': dev})
        check('a visit is counted once per browser per day', r.status == 200)
        check('a bad visitor id: 400', (await w.post('/api/visit', json={'vid': 'x'})).status == 400)
        check('no body: 400', (await w.post('/api/visit', data=b'nope')).status == 400)

        print('\n2. a team member taps and calls (hidden), Ana signs up with email, taps and calls 40 s')
        await team.post('/api/start', json={'email': 'team@example.com'}); await team.post('/api/line/claim')
        await call('start', 'team-call'); CLOCK[0] += 50; await call('end', 'team-call'); CLOCK[0] += 20
        await ana.post('/api/start', json={'email': 'ana@example.com'})
        check('Ana taps', (await ana.post('/api/line/claim')).status == 200)
        CLOCK[0] += 10; await call('start', 'ana-call'); await call('start', 'ana-call')   # a repeated start is one call
        CLOCK[0] += 40; await call('end', 'ana-call'); await call('end', 'ana-call'); CLOCK[0] += 20

        print('\n3. a guest taps (no email) and calls 100 s; someone dials directly for 5 s; a guest taps and never calls')
        check('guest tap on a free line', (await guest.post('/api/line/claim')).status == 200)
        CLOCK[0] += 5; await call('start', 'guest-call'); CLOCK[0] += 100; await call('end', 'guest-call'); CLOCK[0] += 20
        await call('start', 'direct-call'); CLOCK[0] += 5; await call('end', 'direct-call'); CLOCK[0] += 20
        await lazy.post('/api/line/claim'); CLOCK[0] += 130
        check('the lazy tap expired', line.view()['state'] == 'open')

        r = await stats(); s = await r.json(); t = s['totals']
        print('   totals:', {k: t[k] for k in ('visitors', 'taps', 'facetime_calls', 'calls', 'callers', 'signups', 'guests', 'direct_calls', 'calls_30s', 'avg_call_s')})
        check('visitors: 4 browsers (a repeat visit not counted)', t['visitors'] == 4, t['visitors'])
        check('visitors by device', t['visitors_by_device'] == {'ios': 1, 'mac': 0, 'android': 1, 'desktop': 1, 'other': 1}, t['visitors_by_device'])
        check('taps: Ana, the guest and the lazy guest (not the team)', t['taps'] == 3, t['taps'])
        check('FaceTime calls: Ana, the guest, the direct dialer (not the team)', t['facetime_calls'] == 3 and t['calls'] == 3, t['facetime_calls'])
        check('callers: Ana and the guest (who tapped and called)', t['callers'] == 2, t['callers'])
        check('dialed directly: 1', t['direct_calls'] == 1)
        check('calls of 30 s or more: 2 (40 s and 100 s)', t['calls_30s'] == 2, t['calls_30s'])
        check('average call: (40 + 100 + 5) / 3 = 48 s', t['avg_call_s'] == 48, t['avg_call_s'])
        check('sign-ups with email: Ana only (guests apart, team hidden)', t['signups'] == 1 and t['guests'] == 2, (t['signups'], t['guests']))
        check('signed up, never called: 0 (Ana called)', t['signed_up_never_called'] == 0)
        check('browser calls: 0', t['browser_calls'] == 0)
        d = s['days'][0]
        check('by day: visitors, taps, calls, callers, first-time callers, sign-ups, guests',
              (d['visitors'], d['taps'], d['calls'], d['callers'], d['new_callers'], d['signups'], d['guests']) == (4, 3, 3, 2, 2, 1, 2), d)
        check('team account hidden', s['excluded_team_accounts'] == 1)
        check('stats need the admin key', (await w.get('/api/stats')).status == 403)
    finally:
        for c in (team, ana, guest, w, lazy): await c.close()


asyncio.run(main())
n = sum(ok for _, ok in RESULTS)
print(f'\n{n}/{len(RESULTS)} passed'); sys.exit(0 if n == len(RESULTS) else 1)
