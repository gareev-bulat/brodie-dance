"""The line goes down by itself when FaceTime calls stop reaching Brodie (facetime/line_watch.py + brodie/line.py call_dead),
end to end: tonight's real Chert/worker log lines through the watcher, its reports over real HTTP to the real aiohttp app,
and what visitors and the stats page see.

    python scripts/test_line_down.py
"""
import asyncio, os, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'src')]
TMP = tempfile.mkdtemp(prefix='brodie-down-')
os.environ.update(BRODIE_DB=os.path.join(TMP, 'brodie.db'), SESSION_SECRET='test', HEARTBEAT_KEY='k3y', FACETIME_NUMBER='+16282892384',
                  LINE_CAP_S='900', LINE_COOLDOWN_S='15', LINE_CLAIM_S='120', ADMIN_KEY='adm', BRODIE_HEARTBEAT_KEY='k3y', LINE_DOWN_AFTER='2', LINE_DOWN_RETRY_S='1200')
os.environ.pop('DEV_PREVIEW', None)

from aiohttp.test_utils import TestServer, TestClient
from brodie import server, line
from facetime import line_watch, line_report

RESULTS = []
CLOCK = [time.time()]
line.now = lambda: CLOCK[0]


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond)))
    print(f'  {"PASS" if cond else "FAIL"}  {name}' + (f'   ({detail})' if detail != '' else ''), flush=True)


# what the Chert CLI and the worker print (journalctl -o cat), with seconds since the call came in
DEAD_CALL = [(0.0, '  06:04:03  Incoming FaceTime Video call.'), (0.0, '✓ 06:04:03  Answered in 0.0 s.'),
             (11.0, '✓ 06:04:14  Video and audio are coming from "agent-AJ_ZVfSRHCZVKio".'),
             (16.0, '  06:04:19  Call ended.'), (17.0, '  06:04:20  Call ended.')]
GOOD_CALL = [(0.0, '  21:05:08  Incoming FaceTime Video call.'), (0.0, '✓ 21:05:08  Answered in 0.0 s.'),
             (1.7, '[worker] participant joined: chert-bridge-d7685312 (kind PARTICIPANT_KIND_STANDARD)'),
             (3.9, "[worker] bound to 'chert-bridge-d7685312-fe8f-455f-8470-fb1866189fd4' (call id d7685312): video 'facetime-remote-video' 1280x720"),
             (11.2, '✓ 21:05:19  Video and audio are coming from "agent-AJ_eZLaKGs4MrTJ".'), (31.5, '  21:05:39  Call ended.')]
QUICK_HANGUP = [(0.0, '  07:00:00  Incoming FaceTime Video call.'), (3.0, '  07:00:03  Call ended.')]
WARM_NOISE = ["[worker] job AJ_x done (room disconnected (ROOM_DELETED); cleanup 120 ms): frames received 0, processed 0",
              '✓ "brodie-coach" is in a waiting room. Call your line ((628) 289-2384) on FaceTime to talk to it.']


def feed(w, lines, t0):
    for dt, text in lines: w.line(text, t0 + dt)
    return t0 + 100


async def main():
    print('\n1. the watcher on real log lines')
    got = []; w = line_watch.Watch(got.append); t = 1000.0
    for n in WARM_NOISE: w.line(n, t)
    t = feed(w, DEAD_CALL, t)
    check("tonight's call (answered, nobody joined Brodie's room, ended at 16 s): one dead call", len(got) == 1 and '16 s' in got[0], got)
    t = feed(w, GOOD_CALL, t)
    check("yesterday's working call (bound to the bridge): not dead", len(got) == 1 and w.ok == 1)
    t = feed(w, QUICK_HANGUP, t)
    check('a caller hanging up after 3 s: not counted (Chert needs ~4 s to reach Brodie)', len(got) == 1)
    t = feed(w, [(0, '  08:00:00  Incoming FaceTime Video call.'), (9, 'Agent failed: the agent did not answer in time')], t)
    check('"Agent failed" (Oct 15 morning) after 9 s: dead', len(got) == 2 and 'agent failed' in got[1], got[-1:])
    feed(w, WARM_NOISE and [(0, '  09:00:00  Call ended.')], t)
    check('a stray "Call ended" with no call: ignored', len(got) == 2)

    print('\n1b. as the live watcher reads it: journalctl -o json, coloured text, lines handed over in one burst')
    import json as _j
    def jline(msg, t, as_bytes=False):
        m = list(msg.encode()) if as_bytes else msg
        return _j.dumps({'MESSAGE': m, '__REALTIME_TIMESTAMP': str(int(t * 1e6)), '_SYSTEMD_UNIT': 'brodie-chert.service'})
    got2 = []; w2 = line_watch.Watch(got2.append); T = 1792305294.0
    burst = [jline('\x1b[2m  06:34:54  \x1b[0mIncoming FaceTime Video call.', T, True),
             jline('\x1b[32m✓\x1b[0m 06:34:54  Answered in 0.0 s.', T, True),
             jline('✓ 06:35:03  Video and audio are coming from "agent-AJ_FFEYw8PHB58d".', T + 9),
             jline('\x1b[2m  06:35:11  \x1b[0mCall ended.', T + 17, True), 'not json', jline('', T + 18)]
    for raw in burst:                                        # all read at the same moment: the journal's times decide
        msg, t = line_watch.entry(raw)
        if msg is not None: w2.line(msg, t)
    check("tonight's 06:34 call (coloured, bytes, one burst): a dead call of 17 s", len(got2) == 1 and '17 s' in got2[0], got2)
    msg, t = line_watch.entry(jline('\x1b[1mhello\x1b[0m', T, True)); check('colour codes stripped, journal time kept', msg == 'hello' and t == T, (msg, t))

    print('\n2. reports reach the website: two dead calls in a row take the line down')
    srv = TestServer(server.app()); v = TestClient(srv); await v.start_server()
    adm = TestClient(srv); await adm.start_server()
    os.environ['BRODIE_SERVER'] = str(v.make_url('')).rstrip('/')
    server._LAST_BEAT['t'] = time.time()
    try:
        st = lambda: v.get('/api/line')
        d = await (await st()).json(); check('a visitor sees Brodie free', d['state'] == 'open', d)
        ok1 = await asyncio.to_thread(line_watch.post, 'call ended after 16 s')
        d = await (await st()).json(); check('one dead call: still free (could be a fluke)', ok1 and d['state'] == 'open', d['state'])
        a = await (await adm.get('/api/admin/line', params={'key': 'adm'})).json()
        check('stats page: a warning (1 dead call in a row)', a['dead_streak'] == 1 and not a['down'], (a['dead_streak'], a['down']))
        await asyncio.to_thread(line_watch.post, 'call ended after 17 s')
        d = await (await st()).json()
        check('two in a row: visitors see "offline" with reason "down"', d['state'] == 'offline' and d.get('reason') == 'down', d)
        r = await v.post('/api/line/claim')
        check('...and the FaceTime button can\'t take the line (503, no number)', r.status == 503 and 'facetime' not in await r.json(), r.status)
        a = await (await adm.get('/api/admin/line', params={'key': 'adm'})).json()
        check('stats page: DOWN, by the watcher, with the streak', a['state'] == 'down' and a['down'] and a['down_by'] == 'auto' and a['dead_streak'] == 2, (a['state'], a['down_by']))
        check('stats log: "dead call" twice and "down"', [e['event'] for e in a['log'][:3]] == ['down', 'dead call', 'dead call'], [e['event'] for e in a['log'][:3]])

        print('\n3. back up by itself when a call gets through')
        ok = await asyncio.to_thread(line_report.report, 'start', 'test-call')
        d = await (await st()).json(); a = await (await adm.get('/api/admin/line', params={'key': 'adm'})).json()
        check('a call reaches Brodie (the worker\'s start report): the line is up again', ok and not a['down'] and a['dead_streak'] == 0, (a['state'], a['dead_streak']))
        check('stats log: "up"', any(e['event'] == 'up' for e in a['log'][:3]), [e['event'] for e in a['log'][:3]])
        check('visitors see it busy (that caller is on), not offline', d['state'] == 'busy', d['state'])
        await asyncio.to_thread(line_report.report, 'end', 'test-call'); line.admin_free()
        await asyncio.to_thread(line_watch.post, 'x'); await asyncio.to_thread(line_report.report, 'start', 'c2')
        a = await (await adm.get('/api/admin/line', params={'key': 'adm'})).json()
        check('a good call resets the streak (dead, good, dead is not "two in a row")', a['dead_streak'] == 0, a['dead_streak'])
        await asyncio.to_thread(line_report.report, 'end', 'c2'); line.admin_free()

        print('\n3b. down all night with nobody calling: it reopens on probation after 20 min')
        await asyncio.to_thread(line_watch.post, 'a'); await asyncio.to_thread(line_watch.post, 'b')
        a = await (await adm.get('/api/admin/line', params={'key': 'adm'})).json()
        check('down again (2 dead calls); stats show when it reopens (20 min)', a['down'] and a['retry_in_s'] == 1200, a['retry_in_s'])
        CLOCK[0] += 1199; d = await (await st()).json(); check('still down at 19:59', d.get('reason') == 'down', d['state'])
        CLOCK[0] += 1; d = await (await st()).json(); a = await (await adm.get('/api/admin/line', params={'key': 'adm'})).json()
        check('at 20:00 visitors can call again', d['state'] == 'open', d['state'])
        check('...on probation: streak 1, so ONE more dead call takes it down', a['dead_streak'] == 1 and not a['down'] and 'retry' in [e['event'] for e in a['log'][:2]], (a['dead_streak'], [e['event'] for e in a['log'][:2]]))
        await asyncio.to_thread(line_watch.post, 'still broken')
        d = await (await st()).json(); check('one dead call on probation: down again at once', d.get('reason') == 'down', d)
        CLOCK[0] += 1200; await asyncio.to_thread(line_report.report, 'start', 'fixed')
        a = await (await adm.get('/api/admin/line', params={'key': 'adm'})).json()
        check('reopened again, and this time a call gets through: up, streak 0', not a['down'] and a['dead_streak'] == 0, (a['down'], a['dead_streak']))
        await asyncio.to_thread(line_report.report, 'end', 'fixed'); line.admin_free()

        print('\n4. the team can mark it down / up by hand')
        r = await adm.post('/api/admin/line/down', params={'key': 'adm'}); a = await r.json()
        d = await (await st()).json()
        check('Mark line down: site says "having trouble"', r.status == 200 and a['down'] and a['down_by'] == 'admin' and d.get('reason') == 'down', (a['down_by'], d))
        CLOCK[0] += 5000; d = await (await st()).json()
        check('a team "Mark line down" never reopens by itself (still down after 83 min)', d.get('reason') == 'down', d['state'])
        r = await adm.post('/api/admin/line/up', params={'key': 'adm'}); a = await r.json(); d = await (await st()).json()
        check('Mark line up: free again', r.status == 200 and not a['down'] and d['state'] == 'open', d['state'])

        print('\n5. safety')
        r = await v.post('/api/line/health', json={'event': 'dead'}); check('no worker key: 403', r.status == 403, r.status)
        r = await v.post('/api/line/health', json={'event': 'nope'}, headers={'X-Brodie-Key': 'k3y'}); check('bad event: 400', r.status == 400, r.status)
        r = await v.post('/api/admin/line/down'); check('Mark down without the admin key: 403', r.status == 403, r.status)
        server._LAST_BEAT['t'] = 0
        d = await (await st()).json(); check('worker heartbeat lost (not down): offline with reason "worker"', d['state'] == 'offline' and d.get('reason') == 'worker', d)
    finally:
        await v.close(); await adm.close()


asyncio.run(main())
n = sum(ok for _, ok in RESULTS)
print(f'\n{n}/{len(RESULTS)} passed'); sys.exit(0 if n == len(RESULTS) else 1)
