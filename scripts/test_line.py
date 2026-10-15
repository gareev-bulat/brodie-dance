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


def live(room, secs):
    """A running session: the clock moves on with the worker's keepalive every 20 s."""
    end = CLOCK[0] + secs
    while CLOCK[0] < end: CLOCK[0] = min(end, CLOCK[0] + 20); line.touch(room)


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
    live('chert-room-1', 42)
    code, d = await line_of(ana)
    check('42 s later: the real time left, counting down', d['seconds_left'] == 900 + 30 - 42, mmss(d['seconds_left']))
    ok, d2 = await worker(line_client.start, 'chert-room-2', 'call-2')
    msg = line_client.busy_text(d2.get('seconds_left'))
    check('a second FaceTime caller is refused with the minutes left', not ok and msg.startswith("Brodie's in a session right now. Call back in about 15 minutes"), msg)
    live('chert-room-1', 300)
    await worker(line_client.end, 'chert-room-1')
    code, d = await line_of(ana); check('session ends early: only the 30 s reset buffer left', d['state'] == 'busy' and d['seconds_left'] == 30, mmss(d['seconds_left']))
    CLOCK[0] += 30
    code, d = await line_of(ana); check('free again right after the buffer, number back', d['state'] == 'open' and d['facetime'] == NUMBER, d['state'])

    print('\n5. a session that never reports its end (worker still alive) frees at the cap')
    await worker(line_client.start, 'chert-room-3', 'call-3'); live('chert-room-3', 899)
    code, d = await line_of(ana); check('still busy just before the cap', d['state'] == 'busy', mmss(d['seconds_left']))
    CLOCK[0] += 31
    code, d = await line_of(ana); check('no end ever reported: free after the cap + buffer', d['state'] == 'open', d['state'])

    print('\n6. the line survives a server restart')
    await worker(line_client.start, 'chert-room-4', 'call-4'); live('chert-room-4', 100)
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

    print('\n7c. the landing page only describes the FaceTime flow')
    page = await (await ana.get('/')).text(); low = page.lower()
    old = ['href="/app', "'/app", '"/app', '/api/token', 'in the browser', 'in your browser', 'works in the browser', 'nothing to install',
           'body tracking', 'your camera', 'livekit', 'phone_test', 'mint_token', 'web call', 'dance with your camera']
    hits = [p for p in old if p in low]
    check('no link to /app and none of the old browser-call phrases', not hits, hits or 'clean')
    check('how it works: sign up -> see if brodie is free -> facetime brodie -> learn the dance',
          all(x in low for x in ('<h3>sign up</h3>', '<h3>see if brodie is free</h3>', '<h3>facetime brodie</h3>', '<h3>learn the dance</h3>')))

    hero = page[page.index('<header class="hero">'):page.index('</header>')]
    nav = page[page.index('<nav'):page.index('</nav>')]
    check('the hero has exactly one email field; the new-dance emails are in the footer', hero.count('type="email"') == 1 and 'id="waitlist"' in page[page.index('<footer'):])
    check('the label above the headline names FaceTime; github is not in the top nav', 'FaceTime Brodie, your dance coach' in hero and 'github' not in nav.lower())

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

    print('\n9. admin reset (stats page) and the keepalive')
    await worker(line_client.start, 'chert-room-9', 'call-9'); live('chert-room-9', 180)
    r = await other.post('/api/admin/line/reset'); r2 = await other.post('/api/admin/line/reset?key=wrong')
    code, d = await line_of(other)
    check('reset without the key (or a wrong one): 403 and the line stays busy', r.status == 403 and r2.status == 403 and d['state'] == 'busy', (r.status, r2.status, d['state']))
    r = await other.get('/api/admin/line?key=adm'); a = await r.json()
    check('admin view: session, started 3 min ago, time left, heartbeat', a['held'] and a['call_id'] == 'call-9' and a['started_s_ago'] == 180
          and a['raw_seconds_left'] == 900 + 30 - 180 and a['heartbeat_s_ago'] is not None, {k: a[k] for k in ('call_id', 'started_s_ago', 'raw_seconds_left', 'heartbeat_s_ago')})
    r = await other.post('/api/admin/line/reset?key=adm'); d = await r.json()
    code, s = await line_of(other)
    check('reset with the key: free at once (no 30 s buffer)', r.status == 200 and s['state'] == 'open', s['state'])
    log = (await (await other.get('/api/admin/line?key=adm')).json())['log']
    check('the reset is logged with the time, "admin reset" and the state before', log[0]['event'] == 'reset' and log[0]['reason'] == 'admin reset'
          and log[0]['call_id'] == 'call-9' and 'started 180 s ago' in log[0]['before'] and log[0]['t'] == CLOCK[0], log[0])
    await worker(line_client.end, 'chert-room-9'); code, s = await line_of(other)
    check('the reset session\'s late end does not re-busy the line', s['state'] == 'open', s['state'])

    await worker(line_client.start, 'chert-room-10', 'call-10')
    for _ in range(5): CLOCK[0] += 20; await worker(line_client.touch, 'chert-room-10')
    code, s = await line_of(other); check('keepalive every 20 s: still busy after 100 s', s['state'] == 'busy', mmss(s['seconds_left']))
    CLOCK[0] += 74; code, s = await line_of(other); check('keepalive stops (crash / network): still busy within LINE_STALE_S', s['state'] == 'busy', mmss(s['seconds_left']))
    CLOCK[0] += 31; code, s = await line_of(other)
    check('keepalive stopped 105 s ago: free (not at the 15-minute cap)', s['state'] == 'open', s['state'])
    await worker(line_client.start, 'chert-room-11', 'call-11'); CLOCK[0] += 30
    await worker(line_client.end, 'chert-room-11', 'caller left')
    log = (await (await other.get('/api/admin/line?key=adm')).json())['log']
    check('an end is logged with its reason', log[0]['event'] == 'end' and log[0]['reason'] == 'caller left', log[0]['reason'])
    print('\n10. busy callers: the recorded clip, hang up right after it (live voice when the clip is missing)')
    from facetime.cues_tts import vocabulary
    vocab = set(vocabulary()); texts = line_client.busy_texts()
    check('15 busy sentences (1 to 15 minutes) are in the cue vocabulary to pre-record', len(texts) == 15 and all(t in vocab for t in texts),
          f'{texts[0]!r} ... {texts[-1]!r}')
    check('the minutes match the time left', line_client.busy_text(402) == "Brodie's in a session right now. Call back in about 7 minutes."
          and line_client.busy_text(30) == texts[0], line_client.busy_text(402))

    class Mix:
        active = False; cleared = 0
        def clear(self): self.cleared += 1

    class Player:
        def __init__(self, bank): self.bank = bank; self.mix = Mix(); self.said = []
        def say_now(self, text, intent, meta=None):
            self.said.append(text)
            if text not in self.bank: return dict(text=text, status='missing')
            rec = dict(text=text, status='urgent', duration_s=self.bank[text], t_done=None)
            asyncio.get_running_loop().call_later(self.bank[text], lambda: rec.update(t_done=time.time(), status='played'))
            return rec

    class Floor:
        def __init__(self): self.asked = []
        def ask(self, p): self.asked.append(p)

    text = line_client.busy_text(402); p = Player({text: 0.6}); f = Floor(); t0 = time.monotonic()
    how = await line_client.say_busy(p, f, text); dt = time.monotonic() - t0
    check('with the clip: plays it (cutting anything else) and returns right after it ends, not at a fixed 4.5 s',
          how == 'clip' and p.said == [text] and p.mix.cleared == 1 and not f.asked and 0.6 <= dt < 1.2, f'{dt:.2f} s for a 0.6 s clip')
    p = Player({}); f = Floor(); t0 = time.monotonic()
    how = await line_client.say_busy(p, f, text, max_voice_s=2.5); dt = time.monotonic() - t0
    check('clip missing: the live voice says the same sentence (fallback), bounded wait', how == 'voice' and len(f.asked) == 1 and text in f.asked[0] and dt <= 2.7,
          f'{dt:.2f} s')
    for c in (ana, other): await c.close()
    await srv.close()


if __name__ == '__main__':
    asyncio.run(main())
    bad = [n for n, ok in RESULTS if not ok]
    print(f'\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed' + (f'; FAILED: {bad}' if bad else ''))
    sys.exit(1 if bad else 0)
