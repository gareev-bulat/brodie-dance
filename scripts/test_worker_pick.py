"""The worker's half of "pick the song" (facetime/worker.py, facetime/line_report.py), with no LiveKit server and no real website:
the start report's reply carries the lesson a site visitor picked, the job keeps it as site_pick for the handlers, prewarm preloads
every pickable lesson's video, and the heartbeat lists the lessons.

 1. _line_start with line_report.report patched: visitor + dance, a direct FaceTime caller, no reply, an exception (printed by type
    name only), bad names; site_pick is None until the report answered and '' when there is no pick.
 2. bind never waits on the report; a non-FaceTime participant gets '' at once; a rebind reports nothing again.
 3. line_report.report over real HTTP against an aiohttp TestServer stub (port 0): the reply dict on 200, None on 403 / 500 / no server
    / BRODIE_SERVER unset; the print names the lesson only when it is a valid id. Then _line_start end to end against the stub.
 4. The heartbeat body carries the lessons (offered_dances / heartbeat_body), and start_heartbeat posts it to the stub.
 5. prewarm_dances: DANCE first, then every lesson, each in its own try; the old DANCE-only behaviour outside dance mode; DANCE_VIDEO=0.

Fake keys only (k3y). Run:  ~/.venvs/brodie/bin/python scripts/test_worker_pick.py
"""
import asyncio, contextlib, io, json, os, sys, threading, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'src')]
for k in ('BRODIE_SERVER', 'BRODIE_HEARTBEAT_KEY', 'DANCE_PICK', 'DANCE_MENU', 'DANCE_VIDEO'): os.environ.pop(k, None)

from aiohttp import web
from aiohttp.test_utils import TestServer
from facetime import worker, line_report
from starter.dance import lessons, screen

RESULTS = []
SENTINEL = 'zorblax42'
REPORT = line_report.report


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond)))
    print(f'  {"PASS" if cond else "FAIL"}  {name}' + (f'   ({detail})' if detail != '' else ''), flush=True)


def bare_job(**kw):
    """A CoachJob with only what bind / _line_start touch (no room, no context)."""
    j = worker.CoachJob.__new__(worker.CoachJob)
    j.__dict__.update(line_call=None, site_pick=None, line_task=None, closed=False, bound=None, call_id=None, event_writer=None,
                      voice_mode=False, voice=None, session_opened=False, open_task=None)
    j.__dict__.update(kw); return j


class P:
    def __init__(self, identity): self.identity = identity


@contextlib.contextmanager
def captured():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf): yield buf


@contextlib.contextmanager
def env(**kw):
    old = {k: os.environ.get(k) for k in kw}
    for k, v in kw.items():
        if v is None: os.environ.pop(k, None)
        else: os.environ[k] = v
    try: yield
    finally:
        for k, v in old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v


def run_line_start(reply):
    """asyncio.run(job._line_start()) with report returning `reply` (or raising it); (site_pick, calls, stdout)."""
    calls = []
    def fake(event, call_id='', tries=None):
        calls.append((event, call_id))
        if isinstance(reply, BaseException): raise reply
        return reply
    line_report.report = fake
    job = bare_job(line_call='x')
    try:
        with captured() as out: asyncio.run(job._line_start())
    finally: line_report.report = REPORT
    return job.site_pick, calls, out.getvalue()


def part1():
    print('\n1. _line_start: what the start reply means for site_pick', flush=True)
    sp, calls, out = run_line_start({'ok': True, 'source': 'visitor', 'dance': 'bigbowee'})
    check('visitor + bigbowee: site_pick "bigbowee", one start report for the call', sp == 'bigbowee' and calls == [('start', 'x')], (sp, calls))
    check('...and the log names the id only', '[line] the visitor picked bigbowee on the site' in out, out.strip())
    sp, _, out = run_line_start({'ok': True, 'source': 'facetime', 'dance': 'bigbowee'})
    check('a direct FaceTime caller (source facetime): ""', sp == '' and 'picked' not in out, repr(sp))
    sp, _, _ = run_line_start({'ok': True, 'source': 'admin', 'dance': ''})
    check('an admin session: ""', sp == '', repr(sp))
    sp, _, _ = run_line_start(None)
    check('no reply (not configured, 403, 5xx, unreachable): ""', sp == '', repr(sp))
    sp, _, out = run_line_start(RuntimeError(SENTINEL))
    check('report raises: "" and the print has the type name only', sp == '' and 'RuntimeError' in out and SENTINEL not in out, out.strip())
    for bad in ('Bad Name!', 'evil<script>', 'x' * 41, 123, ['bigbowee'], {'a': 1}, True, None, ''):
        sp, _, out = run_line_start({'ok': True, 'source': 'visitor', 'dance': bad})
        check(f'bad dance {str(bad)[:16]!r}: ""', sp == '' and 'picked' not in out, repr(sp))
    sp, _, _ = run_line_start({'ok': True, 'source': 'visitor', 'dance': 'x' * 40})
    check('a 40-character id passes the name check (the handlers still need it in their menu)', sp == 'x' * 40)
    sp, _, _ = run_line_start(['visitor', 'bigbowee'])
    check('a reply that is not a dict: ""', sp == '')

    async def cancelled():
        def slow(event, call_id='', tries=None): time.sleep(0.3); return {'ok': True, 'source': 'visitor', 'dance': 'bigbowee'}
        line_report.report = slow
        try:
            job = bare_job(line_call='x'); t = asyncio.create_task(job._line_start()); await asyncio.sleep(0.05)
            mid = job.site_pick; t.cancel()
            with contextlib.suppress(asyncio.CancelledError): await t
            return mid, job.site_pick
        finally: line_report.report = REPORT
    mid, after = asyncio.run(cancelled())
    check('None while the report is in flight; "" when the task is cancelled (the handlers never wait on a dead task)', mid is None and after == '', (mid, after))


def part2():
    print('\n2. bind: never waits on the report', flush=True)
    async def go():
        calls = []
        def slow(event, call_id='', tries=None):
            calls.append((event, call_id)); time.sleep(0.3); return {'ok': True, 'source': 'visitor', 'dance': 'deatonioo'}
        line_report.report = slow
        try:
            job = bare_job()
            with captured(): t0 = time.monotonic(); job.bind(P('chert-bridge-abc123'), 'video'); dt = time.monotonic() - t0
            check('bind returns at once while the report takes 0.3 s', dt < 0.05, f'{dt * 1000:.1f} ms')
            check('site_pick None right after bind (not answered yet)', job.site_pick is None and job.line_call == 'abc123' and job.line_task is not None)
            with captured(): await asyncio.wait_for(job.line_task, 2)
            check('then "deatonioo" once the reply is in', job.site_pick == 'deatonioo' and calls == [('start', 'abc123')], (job.site_pick, calls))
            with captured(): job.bind(P('chert-bridge-abc123'), 'video again')
            await asyncio.sleep(0.05)
            check('a rebind of the bridge reports nothing again and keeps the pick', len(calls) == 1 and job.site_pick == 'deatonioo', calls)
            other = bare_job()
            with captured(): other.bind(P('phone-page'), 'video')
            await asyncio.sleep(0.05)
            check('a non-FaceTime participant: "" at once, no report', other.site_pick == '' and other.line_task is None and len(calls) == 1, repr(other.site_pick))
            closed = bare_job(closed=True)
            with captured(): closed.bind(P('chert-bridge-zz'), 'video')
            check('a closed job reports nothing', closed.line_task is None and len(calls) == 1)
        finally: line_report.report = REPORT
    asyncio.run(go())


STUB = {'code': 200, 'reply': {}, 'calls': [], 'beats': []}


async def h_line(req):
    try: b = await req.json()
    except Exception: b = None
    STUB['calls'].append((req.headers.get('X-Brodie-Key'), b))
    if req.headers.get('X-Brodie-Key') != 'k3y': return web.json_response({'error': 'forbidden'}, status=403)
    if STUB['code'] != 200: return web.json_response({'error': 'down'}, status=STUB['code'])
    return web.json_response(STUB['reply'])


async def h_beat(req):
    STUB['beats'].append((req.headers.get('X-Brodie-Key'), await req.json())); return web.json_response({'ok': True})


def stub_app():
    app = web.Application(); app.router.add_post('/api/line/call', h_line); app.router.add_post('/api/heartbeat', h_beat); return app


async def report(*a):
    with captured() as out: d = await asyncio.to_thread(line_report.report, *a)
    return d, out.getvalue()


async def part3_4():
    srv = TestServer(stub_app()); await srv.start_server()
    url = str(srv.make_url('')).rstrip('/')
    try:
        print('\n3. line_report.report over real HTTP (stub website on port 0)', flush=True)
        with env(BRODIE_SERVER=url, BRODIE_HEARTBEAT_KEY='k3y'):
            STUB.update(code=200, reply={'ok': True, 'source': 'visitor', 'dance': 'deatonioo'}); STUB['calls'].clear()
            d, out = await report('start', 'c1')
            check('start: the reply dict', d == {'ok': True, 'source': 'visitor', 'dance': 'deatonioo'}, d)
            check('...posted {event: start, call_id} with the key', STUB['calls'] == [('k3y', {'event': 'start', 'call_id': 'c1'})], STUB['calls'])
            check('...the print names the lesson', 'call start reported (line held by visitor, lesson deatonioo)' in out, out.strip())
            STUB['reply'] = {'ok': True, 'source': 'visitor', 'dance': 'evil<script>'}
            d, out = await report('start', 'c2')
            check('a bad dance in the reply: returned as is (the worker checks it) but never printed', isinstance(d, dict) and 'lesson' not in out and 'evil' not in out, out.strip())
            STUB['reply'] = {'ok': True, 'source': 'facetime', 'dance': ''}
            d, out = await report('start', 'c3')
            check('a direct caller: dict, no lesson in the print', d == {'ok': True, 'source': 'facetime', 'dance': ''} and 'call start reported (line held by facetime)' in out, out.strip())
            STUB['reply'] = {'ok': True, 'source': 'visitor'}                 # an old website: no dance key
            d, out = await report('start', 'c4')
            check('an old website without dance: dict, no lesson', d == {'ok': True, 'source': 'visitor'} and 'lesson' not in out)
            STUB['reply'] = {}
            d, _ = await report('start', 'c5'); check('a 200 with an empty body is still truthy', bool(d) and isinstance(d, dict), d)
            STUB['reply'] = ['x']
            d, _ = await report('start', 'c6'); check('a 200 with a non-dict body is still a truthy dict', isinstance(d, dict) and bool(d), d)
            STUB['reply'] = {'ok': True, 'free_in_s': 15}
            d, out = await report('end', 'c1'); check('end: the reply dict and "line free in 15 s"', d == {'ok': True, 'free_in_s': 15} and 'line free in 15 s' in out, out.strip())
            STUB['code'] = 500; STUB['calls'].clear()
            d, _ = await report('start', 'c7'); check('start on a 500: None after ONE try', d is None and len(STUB['calls']) == 1, len(STUB['calls']))
            STUB['calls'].clear(); t = time.monotonic()
            d, _ = await report('end', 'c7'); check('end on a 500: None after 3 tries', d is None and len(STUB['calls']) == 3, f'{len(STUB["calls"])} tries, {time.monotonic() - t:.1f} s')
            STUB['code'] = 200
        with env(BRODIE_SERVER=url, BRODIE_HEARTBEAT_KEY='wrong'):
            d, _ = await report('start', 'c8'); check('wrong key: 403 -> None', d is None and STUB['calls'][-1][0] == 'wrong')
        with env(BRODIE_SERVER='http://127.0.0.1:9', BRODIE_HEARTBEAT_KEY='k3y'):
            t = time.monotonic(); d, _ = await report('start', 'c9')
            check('website unreachable: None, quickly', d is None and time.monotonic() - t < 5, f'{time.monotonic() - t:.2f} s')
        with env(BRODIE_SERVER=None, BRODIE_HEARTBEAT_KEY='k3y'):
            n = len(STUB['calls']); t = time.monotonic(); d = line_report.report('start', 'c10')
            check('BRODIE_SERVER unset: None at once, nothing posted', d is None and len(STUB['calls']) == n and time.monotonic() - t < 0.01)
        with env(BRODIE_SERVER=url, BRODIE_HEARTBEAT_KEY=None):
            check('BRODIE_HEARTBEAT_KEY unset: None', line_report.report('end', 'c10') is None)

        print('\n3b. _line_start end to end against the stub', flush=True)
        with env(BRODIE_SERVER=url, BRODIE_HEARTBEAT_KEY='k3y'):
            STUB['reply'] = {'ok': True, 'source': 'visitor', 'dance': 'bigbowee'}
            job = bare_job(line_call='e2e')
            with captured() as buf: await job._line_start()
            out = buf.getvalue()
            check('site_pick "bigbowee"; logs "lesson bigbowee" and "the visitor picked bigbowee"', job.site_pick == 'bigbowee' and 'lesson bigbowee' in out and 'the visitor picked bigbowee on the site' in out, out.strip())
            STUB['code'] = 403; job = bare_job(line_call='e2e')
            with captured(): await job._line_start()
            check('a 403: site_pick ""', job.site_pick == ''); STUB['code'] = 200

        print('\n4. the heartbeat lists the lessons', flush=True)
        names = lessons.names()
        check('this checkout offers all three (npz + mp4)', names == ['sbbyj', 'deatonioo', 'bigbowee'], names)
        check('heartbeat_body: {agent, dances}', worker.heartbeat_body('brodie-coach', names) == {'agent': 'brodie-coach', 'dances': names})
        check('heartbeat_body with no dances: []', worker.heartbeat_body('brodie-coach') == {'agent': 'brodie-coach', 'dances': []})
        check('offered_dances(--dance): lessons.names()', worker.offered_dances(types.SimpleNamespace(dance=True)) == names)
        check('offered_dances without --dance: [] (a workout worker shows no picker)', worker.offered_dances(types.SimpleNamespace(dance=False)) == [])
        with env(DANCE_PICK='0'): check('DANCE_PICK=0: [] (the kill switch hides the picker)', worker.offered_dances(types.SimpleNamespace(dance=True)) == [])
        with env(DANCE_MENU='bigbowee,sbbyj'): check('DANCE_MENU sets the order', worker.offered_dances(types.SimpleNamespace(dance=True)) == ['bigbowee', 'sbbyj'])
        with env(DANCE='sbbyj', DANCE_MENU='deatonioo,bigbowee'): check('DANCE not on the menu: [] (the handlers never switch then)', worker.offered_dances(types.SimpleNamespace(dance=True)) == [])
        with env(DANCE='renegade'): check('DANCE outside the titles: []', worker.offered_dances(types.SimpleNamespace(dance=True)) == [])
        with env(DANCE='sbbyj'): check('DANCE on the menu: the menu', worker.offered_dances(types.SimpleNamespace(dance=True)) == names)
        with env(DANCE_MENU='sbbyj'): check('one lesson only: [] (nothing to pick)', worker.offered_dances(types.SimpleNamespace(dance=True)) == [])
        orig = lessons.names; lessons.names = lambda: (_ for _ in ()).throw(OSError(SENTINEL))
        try:
            with captured() as buf: got = worker.offered_dances(types.SimpleNamespace(dance=True))
        finally: lessons.names = orig
        check('lessons.names() fails: [] and the type name only', got == [] and 'OSError' in buf.getvalue() and SENTINEL not in buf.getvalue(), buf.getvalue().strip())
        n_threads = threading.active_count()
        with env(BRODIE_SERVER=None, BRODIE_HEARTBEAT_KEY='k3y'):
            worker.start_heartbeat('t-agent', names); check('start_heartbeat without BRODIE_SERVER: no thread', threading.active_count() == n_threads)
        with env(BRODIE_SERVER=url, BRODIE_HEARTBEAT_KEY='k3y'):
            STUB['beats'].clear()
            with captured() as buf: worker.start_heartbeat('t-agent', names)
            for _ in range(60):
                if STUB['beats']: break
                await asyncio.sleep(0.05)
            check('start_heartbeat posts {agent, dances} with the key', STUB['beats'][:1] == [('k3y', {'agent': 't-agent', 'dances': names})], STUB['beats'][:1])
            check('...and says which lessons it offers', 'lessons to pick: sbbyj, deatonioo, bigbowee' in buf.getvalue(), buf.getvalue().strip())
            STUB['beats'].clear()
            with captured(), env(DANCE_PICK='0'): worker.start_heartbeat('t-off')
            for _ in range(60):
                if STUB['beats']: break
                await asyncio.sleep(0.05)
            check('start_heartbeat default (dances=None), DANCE_PICK=0: dances []', STUB['beats'][:1] == [('k3y', {'agent': 't-off', 'dances': []})], STUB['beats'][:1])
    finally:
        await srv.close()


def part5():
    print('\n5. prewarm_dances: every lesson video in memory before any call', flush=True)
    seen = []; fail = set()
    def fake_preload(path):
        seen.append(os.path.basename(path or '')); n = os.path.splitext(os.path.basename(path or ''))[0]
        if n in fail: raise RuntimeError(SENTINEL)
        return 7
    orig = screen.preload; screen.preload = fake_preload
    A = lambda d: types.SimpleNamespace(dance=d)
    try:
        with env(DANCE='sbbyj', DANCE_VIDEO=None), captured() as buf: got = worker.prewarm_dances(A(True))
        check('DANCE=sbbyj, --dance: all three, each once, menu order', seen == ['sbbyj.mp4', 'deatonioo.mp4', 'bigbowee.mp4'] and got == {'sbbyj': 7, 'deatonioo': 7, 'bigbowee': 7}, seen)
        check('...one "ready" line per lesson', all(f'dance video {n} ready (7 frames' in buf.getvalue() for n in got), buf.getvalue().strip())
        seen.clear()
        with env(DANCE='deatonioo'), captured(): worker.prewarm_dances(A(True))
        check('DANCE=deatonioo: the default first, then the rest', seen == ['deatonioo.mp4', 'sbbyj.mp4', 'bigbowee.mp4'], seen)
        seen.clear(); fail.add('deatonioo')
        with env(DANCE='sbbyj'), captured() as buf: got = worker.prewarm_dances(A(True))
        out = buf.getvalue()
        check('one video failing: the others still load', got == {'sbbyj': 7, 'bigbowee': 7} and seen == ['sbbyj.mp4', 'deatonioo.mp4', 'bigbowee.mp4'], got)
        check('...its line has the id and the type name only', 'dance video deatonioo not preloaded (RuntimeError): callers cannot switch to it' in out and SENTINEL not in out, out.strip())
        fail.clear(); seen.clear(); fail.add('sbbyj')
        with env(DANCE='sbbyj'), captured() as buf: worker.prewarm_dances(A(True))
        check('the default failing says it loads on the first call', 'dance video sbbyj not preloaded (RuntimeError): it loads on the first call instead' in buf.getvalue(), buf.getvalue().strip())
        fail.clear(); seen.clear()
        orig_ref = lessons.ref; lessons.ref = lambda n: None if n == 'bigbowee' else orig_ref(n)
        try:
            with env(DANCE='sbbyj'), captured() as buf: got = worker.prewarm_dances(A(True))
        finally: lessons.ref = orig_ref
        check('a lesson whose reference does not load: skipped (LookupError), no preload', 'bigbowee' not in got and 'bigbowee.mp4' not in seen and 'dance video bigbowee not preloaded (LookupError)' in buf.getvalue(), buf.getvalue().strip())
        seen.clear()
        with env(DANCE='sbbyj'), captured(): got = worker.prewarm_dances(A(False))
        check('no --dance, DANCE set: only DANCE (as before)', seen == ['sbbyj.mp4'] and list(got) == ['sbbyj'], seen)
        seen.clear()
        with env(DANCE=None), captured(): got = worker.prewarm_dances(A(False))
        check('no --dance, no DANCE: nothing', seen == [] and got == {})
        with env(DANCE=None), captured(): got = worker.prewarm_dances(A(True))
        check('--dance without DANCE: every lesson', seen == ['sbbyj.mp4', 'deatonioo.mp4', 'bigbowee.mp4'], seen)
        seen.clear()
        with env(DANCE='sbbyj', DANCE_PICK='0'), captured(): worker.prewarm_dances(A(True))
        check('DANCE_PICK=0: only DANCE (no extra memory)', seen == ['sbbyj.mp4'], seen)
        seen.clear()
        with env(DANCE='sbbyj', DANCE_VIDEO='0'), captured(): got = worker.prewarm_dances(A(True))
        check('DANCE_VIDEO=0: nothing decoded', seen == [] and got == {})
        check('the references are cached for the handlers (lessons.ref is the same object)', lessons.ref('deatonioo') is lessons.ref('deatonioo') and lessons.ref('deatonioo') is not None)
    finally:
        screen.preload = orig


part1()
part2()
asyncio.run(part3_4())
part5()
n = sum(ok for _, ok in RESULTS)
print(f'\n{n}/{len(RESULTS)} passed', flush=True)
os._exit(0 if n == len(RESULTS) else 1)                       # the heartbeat threads are daemons sleeping 20 s: do not wait for them
