"""Picking the lesson on the site, end to end against the real aiohttp app with a fake line clock and a throwaway sqlite file:
the worker's heartbeat lists the lessons (brodie/server.py pickable, /api/config pick), a visitor's claim carries the pick
(brodie/line.py dance, theirs only), the worker's call start reply hands it back (facetime/line_report.py over real HTTP), and
every way a session ends clears it. Fake keys only.

    python scripts/test_line_pick.py
"""
import asyncio, contextlib, io, os, sqlite3, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'src')]
TMP = tempfile.mkdtemp(prefix='brodie-pick-')
os.environ.update(BRODIE_DB=os.path.join(TMP, 'brodie.db'), SESSION_SECRET='test', HEARTBEAT_KEY='k3y', FACETIME_NUMBER='+15555550100',
                  LINE_CAP_S='900', LINE_COOLDOWN_S='15', LINE_CLAIM_S='120', ADMIN_KEY='adm', BRODIE_HEARTBEAT_KEY='k3y')
for k in ('DEV_PREVIEW', 'BRODIE_SERVER'): os.environ.pop(k, None)

from aiohttp.test_utils import TestServer, TestClient
from brodie import server, line, users
from facetime import line_report

CLOCK = [1_000_000.0]
line.now = lambda: CLOCK[0]
RESULTS = []
KEY = {'X-Brodie-Key': 'k3y'}
THREE = ['sbbyj', 'deatonioo', 'bigbowee']


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond)))
    print(f'  {"PASS" if cond else "FAIL"}  {name}' + (f'   ({detail})' if detail != '' else ''), flush=True)


def row():
    with users._db() as c: return dict(line._row(c))


def last_id(): return (line.log(1) or [{'id': 0}])[0]['id']


async def j(r):
    try: return await r.json()
    except Exception: return {}


async def client(srv, email=None):
    c = TestClient(srv); await c.start_server()
    if email: assert (await c.post('/api/start', json={'email': email})).status == 200
    return c


async def main():
    srv = TestServer(server.app()); await srv.start_server()
    w = await client(srv)                                                     # Brodie's worker
    A, B, anon, g = await client(srv, 'a@example.com'), await client(srv, 'b@example.com'), await client(srv), await client(srv)
    beat = lambda body, key='k3y': w.post('/api/heartbeat', json=body, headers={'X-Brodie-Key': key})
    call = lambda ev, cid: w.post('/api/line/call', json={'event': ev, 'call_id': cid}, headers=KEY)
    pick = lambda: anon.get('/api/config')
    claim = lambda c, dance=None: c.post('/api/line/claim', json={'dance': dance}) if dance is not None else c.post('/api/line/claim')
    try:
        print('\n1. the worker\'s heartbeat lists the lessons; /api/config pick has them only while it is online')
        check('no heartbeat yet: pick []', (await j(await pick()))['pick'] == [])
        r = await beat({'agent': 't', 'dances': THREE + ['BAD NAME', 'x' * 41, 'sbbyj', 42, None, '<b>x</b>', 'Sbbyj']})
        d = await j(await pick()); check('a heartbeat with junk: only the three valid ids, once each, in order', r.status == 200 and d['pick'] == THREE, d['pick'])
        r = await beat({'agent': 't', 'dances': ['evil']}, key='nope'); d = await j(await pick())
        check('a heartbeat with the wrong key: 403, the list unchanged', r.status == 403 and d['pick'] == THREE, r.status)
        await beat({'agent': 't'}); check('an older worker (no dances): pick []', (await j(await pick()))['pick'] == [])
        await beat({'agent': 't', 'dances': 'sbbyj'}); check('dances not a list: pick []', (await j(await pick()))['pick'] == [])
        await beat({'agent': 't', 'dances': [f'd{i}' for i in range(20)]}); check('at most 8 kept', len((await j(await pick()))['pick']) == 8)
        await beat({'agent': 't', 'dances': THREE}); server._LAST_BEAT['t'] = time.time() - 1000
        check('a stale heartbeat (worker offline): pick []', (await j(await pick()))['pick'] == [])
        os.environ.pop('HEARTBEAT_KEY')
        check('no HEARTBEAT_KEY on the site: pick []', server.pickable() == [] and (await j(await pick()))['pick'] == [])
        os.environ['HEARTBEAT_KEY'] = 'k3y'
        await beat({'agent': 't', 'dances': THREE}); d = await j(await pick())
        check('online again: the three, and still no number or status in the public config', d['pick'] == THREE and 'state' not in d and '5550100' not in str(d), d)

        print('\n2. A claims with a pick: only A ever sees it')
        r = await claim(A, 'bigbowee'); d = await j(r)
        check('A claims {dance: bigbowee}: 200 mine, dance bigbowee, not on the call yet', r.status == 200 and d['state'] == 'mine' and d.get('dance') == 'bigbowee' and d.get('on_call') is False, d)
        d = await j(await A.get('/api/line')); check("A's GET /api/line shows the dance", d.get('dance') == 'bigbowee', d)
        d = await j(await B.get('/api/line')); check("B's busy view has no dance key", d['state'] == 'busy' and 'dance' not in d, d)
        r = await claim(B, 'sbbyj'); d = await j(r)
        check("B's claim with a pick: 409, no dance key, A's pick unchanged", r.status == 409 and 'dance' not in d and row()['dance'] == 'bigbowee', (r.status, row()['dance']))
        d = await j(await anon.get('/api/line')); check('a logged-out GET has no dance key', d['state'] == 'busy' and 'dance' not in d, d)
        n = users.count(); r = await claim(anon, 'sbbyj')
        check('a logged-out claim with a pick on a busy line: 409, no guest account made', r.status == 409 and users.count() == n, r.status)
        e = line.log(5); clicked = [x for x in e if x['event'] == 'busy']
        check('the log names the pick in the click (and stats still count it as a tap)', clicked and clicked[0]['detail'].startswith('clicked FaceTime Brodie (bigbowee):'), clicked[:1])
        d = await j(await w.get('/api/health', params={'key': 'adm'})); check('the health/admin views carry no dance', 'bigbowee' not in str(d), '')

        print('\n3. bad picks are dropped silently: the claim still works')
        await A.post('/api/line/done'); check('done clears the pick', row()['dance'] == '' and row()['source'] == '', row()['dance'])
        for bad in ('evil<script>', 'zzz', 'SBBYJ', 'sbbyj' + 'x' * 50, ['sbbyj'], {'n': 'sbbyj'}, 7, ''):
            r = await claim(g, bad); d = await j(r); ok = r.status == 200 and d.get('dance') == '' and row()['dance'] == ''
            await g.post('/api/line/done')
            if not ok: break
        check("claims with 'evil<script>', an unknown id, upper case, 55 chars, a list, a dict, a number, '': 200 with dance ''", ok, (bad, r.status, d.get('dance')))
        r = await g.post('/api/line/claim', data=b'{not json', headers={'Content-Type': 'application/json'}); d = await j(r)
        check('a body that is not JSON: 200 with dance \'\'', r.status == 200 and d.get('dance') == '', r.status)
        await g.post('/api/line/done')
        check('nothing of the bad picks reached the log', not any('evil' in x['detail'] or 'zzz' in x['detail'] for x in line.log(40)))

        print('\n4. A re-claims before the call: the pick changes, the clock does not')
        await claim(A, 'bigbowee'); CLOCK[0] += 10
        r = await claim(A, 'deatonioo'); d = await j(r); e = line.log(1)[0]
        check('re-claim with deatonioo: 200, dance deatonioo, 1:50 left (not restarted)', r.status == 200 and d.get('dance') == 'deatonioo' and d['seconds_left'] == 110, (d.get('dance'), d.get('seconds_left')))
        check("logged as 'pick' by A: song deatonioo", e['event'] == 'pick' and e['actor'] == 'visitor a@example.com' and e['detail'] == 'song deatonioo', e)
        i0 = last_id(); r = await claim(A); d = await j(r)
        check('re-claim with no body (an old cached page): the pick kept, nothing logged', d.get('dance') == 'deatonioo' and last_id() == i0, d.get('dance'))
        r = await claim(A, 'evil<script>'); d = await j(r); r2 = await claim(A, 'deatonioo')
        check('re-claim with a bad pick or the same pick: kept, nothing logged', d.get('dance') == 'deatonioo' and (await j(r2)).get('dance') == 'deatonioo' and last_id() == i0)

        print('\n5. the call starts: the worker gets the pick back')
        r = await call('start', 'c1'); d = await j(r)
        check("start reply: {ok, source: visitor, dance: deatonioo}", r.status == 200 and d == {'ok': True, 'source': 'visitor', 'dance': 'deatonioo'}, d)
        check("the log's call line names the lesson", line.log(1)[0]['detail'].endswith(', lesson deatonioo'), line.log(1)[0]['detail'])
        d = await j(await A.get('/api/line')); check('A: on the call, dance deatonioo', d.get('on_call') is True and d.get('dance') == 'deatonioo', d)
        i0 = last_id(); r = await claim(A, 'sbbyj'); d = await j(r)
        check('a re-claim during the call changes nothing', r.status == 200 and d.get('dance') == 'deatonioo' and row()['dance'] == 'deatonioo' and last_id() == i0, d.get('dance'))

        print('\n6. hang-up, redial within the cooldown, then a stranger after it')
        CLOCK[0] += 60; await call('end', 'c1'); rw = row()
        check('after the hang-up (15 s cooldown): still the visitor\'s, the pick kept', rw['source'] == 'visitor' and rw['dance'] == 'deatonioo' and rw['until'] == CLOCK[0] + 15, rw)
        CLOCK[0] += 5; d = await j(await call('start', 'c2'))
        check('a redial 5 s later: visitor, deatonioo again', d == {'ok': True, 'source': 'visitor', 'dance': 'deatonioo'}, d)
        CLOCK[0] += 30; await call('end', 'c2'); CLOCK[0] += 16
        d = await j(await call('start', 'c3'))
        check('after the cooldown, a direct call: {source: facetime, dance: \'\'}', d == {'ok': True, 'source': 'facetime', 'dance': ''}, d)
        check('the expired session cleared the pick', row()['dance'] == '' and row()['source'] == 'facetime', row()['dance'])
        CLOCK[0] += 30; await call('end', 'c3'); CLOCK[0] += 16; line.admin_free()
        with users._db() as c: c.execute('UPDATE line SET dance = "sbbyj" WHERE id = 1')   # a stray pick on a free line (source '')
        await call('end', 'late'); rw = row()
        check("a call end on a free line (Chert's cooldown) clears it too", rw['source'] == 'cooldown' and rw['dance'] == '', rw)
        CLOCK[0] += 16

        print('\n7. a stale claim, admin busy / free and done clear the pick')
        await claim(A, 'bigbowee'); CLOCK[0] += 121
        d = await j(await call('start', 'c4'))
        check('a pick, no call within 2 min, then a direct call: dance \'\'', d == {'ok': True, 'source': 'facetime', 'dance': ''}, d)
        CLOCK[0] += 30; await call('end', 'c4'); CLOCK[0] += 16
        await claim(A, 'bigbowee'); r = await w.post('/api/admin/line/busy', params={'key': 'adm', 'minutes': '10'})
        check('admin Mark busy over a pick: cleared', r.status == 200 and row()['dance'] == '' and row()['source'] == 'admin', row()['dance'])
        d = await j(await call('start', 'c5')); check("a call during admin busy: {source: admin, dance: ''}", d == {'ok': True, 'source': 'admin', 'dance': ''}, d)
        await call('end', 'c5'); await w.post('/api/admin/line/free', params={'key': 'adm'})
        await claim(A, 'sbbyj'); check('(A picked sbbyj)', row()['dance'] == 'sbbyj')
        await w.post('/api/admin/line/free', params={'key': 'adm'}); check('admin Mark free clears it', row()['dance'] == '' and row()['source'] == '')
        await claim(A, 'sbbyj'); await A.post('/api/line/done'); check("A's done clears it", row()['dance'] == '')
        r = await claim(A, 'sbbyj'); server._LAST_BEAT['t'] = time.time() - 1000; r2 = await claim(A, 'bigbowee')
        check('worker offline: a re-claim is 503 and the pick stays', r2.status == 503 and row()['dance'] == 'sbbyj', r2.status)
        await beat({'agent': 't', 'dances': ['sbbyj', 'deatonioo']}); r = await claim(A, 'bigbowee')
        check('a pick the live worker no longer lists: dropped (kept sbbyj)', r.status == 200 and row()['dance'] == 'sbbyj', row()['dance'])
        await A.post('/api/line/done'); await beat({'agent': 't', 'dances': THREE})

        print('\n8. migration: a database from before the picker gets the column, the session kept')
        old = os.path.join(TMP, 'old.db'); c = sqlite3.connect(old)
        c.execute('CREATE TABLE line (id INTEGER PRIMARY KEY CHECK (id = 1), source TEXT NOT NULL DEFAULT "", uid INTEGER, t_start REAL NOT NULL DEFAULT 0, '
                  'until REAL NOT NULL DEFAULT 0, on_call INTEGER NOT NULL DEFAULT 0, dead_streak INTEGER NOT NULL DEFAULT 0, '
                  'down_since REAL NOT NULL DEFAULT 0, down_by TEXT NOT NULL DEFAULT "")')
        c.execute("INSERT INTO line (id, source, uid, t_start, until, on_call) VALUES (1, 'visitor', 7, 5.0, 99.0, 1)")
        c.execute('CREATE TABLE line_log (id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL, event TEXT NOT NULL, actor TEXT NOT NULL DEFAULT "", detail TEXT NOT NULL DEFAULT "")')
        c.execute("INSERT INTO line_log (t, event, actor, detail) VALUES (1, 'busy', 'x', 'clicked')"); c.commit(); c.close()
        keep = users.DB; users.DB = old
        try:
            line.init(); line.init(); rw = row(); n = len(line.log(10))
        finally: users.DB = keep
        check('the dance column added (default \'\'), the session and the log kept, init twice is fine',
              rw['dance'] == '' and (rw['source'], rw['uid'], rw['until'], rw['on_call']) == ('visitor', 7, 99.0, 1) and n == 1, rw)

        print('\n9. the worker\'s line_report over real HTTP: the start reply carries the pick')
        os.environ['BRODIE_SERVER'] = str(w.make_url('')).rstrip('/')
        await claim(A, 'deatonioo'); buf = io.StringIO()
        with contextlib.redirect_stdout(buf): d = await asyncio.to_thread(line_report.report, 'start', 'c6')
        out = buf.getvalue(); print(''.join('     | ' + x + '\n' for x in out.splitlines() if 'call start reported' in x), end='')
        check("report('start') returns the reply dict with the dance", isinstance(d, dict) and d.get('source') == 'visitor' and d.get('dance') == 'deatonioo', d)
        check("...and prints 'lesson deatonioo'", 'call start reported (line held by visitor, lesson deatonioo)' in out)
        CLOCK[0] += 30; await asyncio.to_thread(line_report.report, 'end', 'c6'); CLOCK[0] += 16
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): d = await asyncio.to_thread(line_report.report, 'start', 'c7')
        check("a direct call: dance '' and no lesson printed", isinstance(d, dict) and d.get('source') == 'facetime' and d.get('dance') == '' and 'lesson' not in buf.getvalue(), d)
        await asyncio.to_thread(line_report.report, 'end', 'c7'); CLOCK[0] += 16
        os.environ.pop('BRODIE_SERVER'); t = time.monotonic(); d = line_report.report('start', 'c8')
        check('without BRODIE_SERVER: None at once', d is None and time.monotonic() - t < 0.05, f'{(time.monotonic() - t) * 1000:.1f} ms')
    finally:                                                                  # closing a client closes the shared server: only at the end
        for c in (w, A, B, anon, g): await c.close()
        await srv.close()


if __name__ == '__main__':
    asyncio.run(main())
    bad = [n for n, ok in RESULTS if not ok]
    print(f'\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed' + (f'; FAILED: {bad}' if bad else ''))
    sys.exit(1 if bad else 0)
