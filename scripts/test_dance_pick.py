#!/usr/bin/env python3
"""Checks for picking the lesson: starter/dance/lessons.py (the matcher), starter/handlers.py (the pick flow), starter/dance/screen.py
(set_ref and the menu card). No LiveKit, no network, no model: each handler case loads a fresh starter/handlers.py with
facetime.voice.load_handlers on its own event loop and feeds it fake pose (10 Hz), visibility and caller voice events through a fake
voice layer whose job carries site_pick (what facetime/worker.py sets from the line start report).

  1  a site pick before the first event: the greeting, the intro and the preview are that dance, and the log says so
  2  no site pick: the greeting lists the menu; "the shoot one" switches, confirms, and the preview is that dance
  3  a slow start report: the preview waits for it (the intro names no dance) and announces the late pick; none ever: the default
     about DANCE_SITE_WAIT_S after the first event; after a voice pick the preview does not wait for the site at all
  4  after a site pick only a whole title (strict) switches by voice, and the site never overrides a voice pick
  5  a voice pick during the intro: one "change of plan" and exactly one preview
  6  after the preview starts nothing switches (voice or a late site pick)
  7  a bare "sweep": one narrowing question naming both sweep dances, never twice; a number then picks
  8  "my chest hurts" and "stop, twist" never switch (rest / kind mode as before)
  9  DANCE_PICK=0: today's greeting and intro prompts exactly, no menu, the site pick ignored
 10  DANCE_VIDEO=1: only lessons whose video is already in memory are on the menu; a screen that cannot switch refuses the pick
 11  privacy: a sentinel in the caller's words never reaches stdout, stderr, the log, a prompt or a spoken line, and a matcher error
     is printed by its type only
 12  the matcher table (lessons.match / ambiguous), a fuzz run (no exception, under 2 ms each), and web/index.html TITLES == lessons.TITLES
 15  the menu answer hold: no preview while Brodie or the caller talks (Gemini finalizes the caller's words after its own reply)
 16  "sweep" during the intro: the question holds the preview and the answer counts
 17  a site pick that is not on this call's menu: one log line, the default kept
 18  four songs: the greeting, the menu card, "four" and the new title's words (never "for" or "frame")
 14  the screen (needs cv2 and the videos; fills data/tmp/screen_cache once): set_ref swaps the lesson and its frames in one tick, a
     stale load never overwrites them, an uncached video is refused, and the menu, plain and title cards render in under 20 ms
     (PNGs into --png-dir)
(13, the worker's _line_start, is in the worker's tests.) PASS/FAIL per check; exit 1 on failure.

Run (WSL, brodie venv, repo root):
  ~/.venvs/brodie/bin/python scripts/test_dance_pick.py [--png-dir DIR] [--no-screen] [-v]
"""
import argparse, asyncio, contextlib, gc, io, logging, os, random, re, sys, time, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'src'))
for k in ('DANCE_PICK', 'DANCE_MENU', 'DANCE_ROAST', 'DANCE_PREVIEW', 'DANCE_COUNT_ALOUD', 'DANCE_PASS'): os.environ.pop(k, None)
os.environ.update(DANCE='sbbyj', DANCE_VIDEO='0', DANCE_REST_S='0.2', DANCE_SITE_WAIT_S='1',
                  DANCE_MENU='sbbyj,deatonioo,bigbowee')     # the cases below are written for these three (case 18: the full menu)
from facetime.voice import load_handlers  # noqa: E402
from starter.dance import lessons  # noqa: E402
from starter.dance import screen as screen_mod  # noqa: E402

HANDLERS = os.path.join(ROOT, 'starter', 'handlers.py')
M = ['sbbyj', 'deatonioo', 'bigbowee']
T = dict(lessons.TITLES)
OUT = sys.stdout
RESULTS = []
VERBOSE = False


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond))); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''), file=OUT, flush=True)
    return cond


def titles_in(s):
    return [n for n in M if f'"{T[n]}"' in s or T[n] in s]


class FakeLayer:
    """The voice layer the handlers see: bound say_now / ask_voice (the handlers reach the job through say_now.__self__.job)."""
    def __init__(self, site_pick=''):
        self.said, self.asked = [], []
        mix = types.SimpleNamespace(active=False, pending=0, duck_until=0, clear=lambda: None)
        bank = types.SimpleNamespace(cache={}, get=lambda text: None, dir='puck')
        player = types.SimpleNamespace(bank=bank, playing=None, pending=None, urgent=None, mix=mix)
        self.job = types.SimpleNamespace(site_pick=site_pick, room=None, per=None, player=player)

    def say_now(self, text, intent='other'):
        self.said.append(text); t = time.time(); return {'status': 'played', 't_push': t, 't_done': t}

    def ask_voice(self, prompt):
        self.asked.append(prompt); return 'spoken'


class Call:
    """One call into a fresh handlers module: pose events at 10 Hz from the start, helpers for the rest."""
    def __init__(self, site_pick='', env=None, quiet_s=0.05, screen=False):
        old = {k: os.environ.get(k) for k in (env or {})}
        os.environ.update(env or {})
        try: self.fn = load_handlers(HANDLERS)
        finally:
            for k, v in old.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
        self.g = self.fn.__globals__; self.L = FakeLayer(site_pick); self.plays = []
        loop = asyncio.get_running_loop()
        self.g['when_quiet'] = lambda fn, min_wait=0, quiet_s_=0, max_wait=0, **kw: loop.call_later(quiet_s, fn)
        self.g['GREET_AFTER_S'] = 0.2
        self.g['play_parts'] = lambda k0, k1, count, then, preview=False: self.plays.append((k0, k1, self.g['REF'].name, preview, time.time()))
        self.g['judge_async'] = lambda items, then: then({k: dict(score=0.7, worst_part='left arm', source='sim') for k, _ in items})
        if screen: self.S['screen'] = True                       # no video task (there is no room)
        self.task = loop.create_task(self._poses())

    @property
    def S(self): return self.g['S']
    @property
    def ref(self): return self.g['REF'].name
    def played(self): return [p[:4] for p in self.plays]
    def ev(self, **e): self.fn(dict(t_wall=time.time(), **e), self.L.say_now, self.L.ask_voice)
    def full(self): self.ev(type='visibility', state='full')
    def say(self, text): self.ev(type='voice', role='caller', text=text, private=True)
    def asks(self, needle): return [a for a in self.L.asked if needle in a]
    def close(self): self.task.cancel()

    async def _poses(self):
        while True:
            self.ev(type='pose', ok=False); await asyncio.sleep(0.1)


def case(name, fn):
    """Run one async case on a fresh loop with stdout captured (the handlers' log lines); fn(out) gets the buffer."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf): asyncio.run(fn(buf))
    except Exception as e:
        check(f'{name}: ran without an exception', False, type(e).__name__)
    if VERBOSE: print(buf.getvalue(), file=OUT)


# ---------------- 1-11: the handlers ----------------
async def c1(out):
    c = Call(site_pick='bigbowee'); await asyncio.sleep(0.45)
    greet = c.asks('just joined')
    check('1 site pick: REF is bigbowee before the greeting, the menu card marks it', c.ref == 'bigbowee' and c.S['pick_by'] == 'site'
          and c.S['menu'] == [(1, T['sbbyj'], False), (2, T['deatonioo'], False), (3, T['bigbowee'], True)], c.ref)
    check('1 site pick: one greeting, naming "Palm Sway Wave" and no other title', len(greet) == 1 and titles_in(greet[0]) == ['bigbowee'], titles_in(greet[0]) if greet else 'no greeting')
    c.full(); await asyncio.sleep(0.4)
    intro = c.asks('fits in the frame')
    check('1 site pick: the intro names it word for word', len(intro) == 1 and 'The dance is "Palm Sway Wave"' in intro[0] and titles_in(intro[0]) == ['bigbowee'])
    check('1 site pick: the preview is bigbowee', c.played() == [(0, c.g['REF'].n_chunks, 'bigbowee', True)], c.played())
    o = out.getvalue()
    check('1 site pick: the log says "lesson bigbowee (picked on the site)" and "lesson locked: bigbowee (site)"',
          'lesson bigbowee (picked on the site)' in o and 'lesson locked: bigbowee (site)' in o)
    check('1 site pick: the menu card is gone once locked', c.S['menu'] == [] and not c.S['pick_open'])
    c.close()


async def c2(out):
    c = Call(site_pick=''); await asyncio.sleep(0.45)
    greet = c.asks('just joined')
    check('2 no site pick: the greeting lists the three titles in menu order', len(greet) == 1 and
          '"Chest Sweep Twist", "Sweep and Shoot" or "Palm Sway Wave"' in greet[0] and 'TWO short sentences' in greet[0], greet[0][:80] if greet else '')
    c.say('the shoot one'); await asyncio.sleep(0.25)
    conf = c.asks('They picked "Sweep and Shoot"')
    check('2 voice "the shoot one": switched to deatonioo and one confirmation', c.ref == 'deatonioo' and c.S['pick_by'] == 'voice' and len(conf) == 1, c.ref)
    c.full(); await asyncio.sleep(0.4)
    intro = c.asks('fits in the frame')
    check('2 voice pick: the intro names Sweep and Shoot, the preview is deatonioo',
          intro and 'The dance is "Sweep and Shoot"' in intro[0] and c.played() == [(0, c.g['REF'].n_chunks, 'deatonioo', True)], c.played())
    o = out.getvalue()
    check('2 voice pick: logged by id and kind only', 'lesson deatonioo (picked by voice)' in o and 'lesson locked: deatonioo (voice)' in o)
    c.close()


async def c3(out):
    c = Call(site_pick=None); await asyncio.sleep(0.12); c.full(); await asyncio.sleep(0.5)
    intro = c.asks('fits in the frame')
    check('3 slow report: the preview waits for it', c.plays == [] and c.S['state'] == 'intro', c.played())
    check('3 slow report: the intro names no dance as on (the site may still pick) but offers the menu', len(intro) == 1 and 'The dance is' not in intro[0]
          and 'they pick the dance by saying its name: "Chest Sweep Twist", "Sweep and Shoot" or "Palm Sway Wave"' in intro[0], intro[0][-160:] if intro else '')
    c.L.job.site_pick = 'deatonioo'; await asyncio.sleep(0.4)
    ann = c.asks('The dance is "Sweep and Shoot"')
    check('3 slow report: the late site pick is announced (ANNOUNCE, not a change of plan)', len(ann) == 1 and not c.asks('Change of plan'), len(ann))
    check('3 slow report: the preview is deatonioo, once', c.played() == [(0, c.g['REF'].n_chunks, 'deatonioo', True)], c.played())
    c.close()
    c = Call(site_pick=None); await asyncio.sleep(0.12); c.full(); await asyncio.sleep(1.6)
    dt = c.plays[0][4] - c.S['first_event'] if c.plays else -1
    check('3 no report ever: the default preview about DANCE_SITE_WAIT_S (1 s) after the first event', c.played() == [(0, c.g['REF'].n_chunks, 'sbbyj', True)]
          and 0.95 <= dt <= 1.5, f'{dt:.2f} s')
    check('3 no report ever: the lock is logged as the default', 'lesson locked: sbbyj (default)' in out.getvalue())
    c.close()
    c = Call(site_pick=None); await asyncio.sleep(0.12); c.say('the shoot one'); c.full(); await asyncio.sleep(0.3)
    intro = c.asks('fits in the frame')
    check('3 no report but a voice pick: the intro names it and the preview does not wait for the site',
          intro and 'The dance is "Sweep and Shoot"' in intro[0] and c.played() == [(0, c.g['REF'].n_chunks, 'deatonioo', True)], c.played())
    c.close()


async def c4(out):
    c = Call(site_pick='deatonioo'); await asyncio.sleep(0.45)
    n0 = len(c.L.asked)
    c.say('wave'); await asyncio.sleep(0.2)
    check('4 site deatonioo, voice "wave": stays (strict), nothing asked', c.ref == 'deatonioo' and len(c.L.asked) == n0, c.ref)
    c.say('palm sway wave'); await asyncio.sleep(0.2)
    check('4 site deatonioo, voice "palm sway wave": bigbowee, confirmed', c.ref == 'bigbowee' and c.S['pick_by'] == 'voice' and len(c.asks('They picked "Palm Sway Wave"')) == 1, c.ref)
    c.L.job.site_pick = 'sbbyj'; await asyncio.sleep(0.2)
    check('4 a later site pick never overrides the voice pick', c.ref == 'bigbowee' and c.g['_choose']('sbbyj', 'site') is False and c.ref == 'bigbowee')
    c.close()


async def c5(out):
    c = Call(site_pick='', quiet_s=0.5); await asyncio.sleep(0.12); c.full(); await asyncio.sleep(0.1)
    intro = c.asks('fits in the frame')
    check('5 in frame before the greeting: the intro names the dance and offers the others (two sentences)', intro and 'The dance is "Chest Sweep Twist"' in intro[0]
          and 'another name: "Sweep and Shoot" or "Palm Sway Wave"' in intro[0] and 'In TWO short sentences' in intro[0] and intro[0].index(c.g['MENU_TAIL']) > intro[0].index('Style:'), intro[0][-160:] if intro else '')
    c.say('palm sway wave'); await asyncio.sleep(1.2)
    ch = c.asks('Change of plan: the dance is now "Palm Sway Wave"')
    check('5 voice pick in the intro: one CHANGE ask', len(ch) == 1 and not c.asks('The dance is "Palm Sway Wave"'), len(ch))
    check('5 voice pick in the intro: exactly one preview, bigbowee', c.played() == [(0, c.g['REF'].n_chunks, 'bigbowee', True)], c.played())
    c.close()


async def c6(out):
    c = Call(site_pick=None); await asyncio.sleep(0.12); c.full(); await asyncio.sleep(1.3)
    locked = c.played() == [(0, c.g['REF'].n_chunks, 'sbbyj', True)]
    c.say('sweep and shoot'); c.L.job.site_pick = 'bigbowee'; await asyncio.sleep(0.3)
    check('6 after the preview: voice "sweep and shoot" and a late site pick change nothing', locked and c.ref == 'sbbyj' and len(c.plays) == 1
          and c.S['menu'] == [] and c.g['_choose']('deatonioo', 'voice') is False and c.ref == 'sbbyj', c.played())
    c.close()


async def c7(out):
    c = Call(site_pick=''); await asyncio.sleep(0.45)
    n0 = len(c.L.asked)
    c.say('sweep'); await asyncio.sleep(0.2)
    nar = c.asks('two dances share')
    check('7 "sweep": one NARROW ask naming both sweep dances in menu order', len(nar) == 1 and '"Chest Sweep Twist" or "Sweep and Shoot"' in nar[0] and c.ref == 'sbbyj')
    c.say('sweep'); await asyncio.sleep(0.2)
    check('7 a second "sweep" sends nothing', len(c.L.asked) == n0 + 1, len(c.L.asked) - n0)
    c.say('the second one'); await asyncio.sleep(0.2)
    check('7 then "the second one": deatonioo (the screen number)', c.ref == 'deatonioo')
    c.close()


async def c8(out):
    c = Call(site_pick='', env={'DANCE': 'bigbowee'}); await asyncio.sleep(0.45)
    c.say('my chest hurts'); await asyncio.sleep(0.2)
    check('8 "my chest hurts": rest mode, no switch', c.S['rest'] and c.ref == 'bigbowee' and c.S['pick_by'] == 'default', c.ref)
    c.close()
    c = Call(site_pick='', env={'DANCE': 'bigbowee'}); await asyncio.sleep(0.45)
    c.say('stop, twist'); await asyncio.sleep(0.2)
    check('8 "stop, twist": kind mode, no switch', c.S['kind'] and not c.S['rest'] and c.ref == 'bigbowee' and c.S['pick_by'] == 'default', c.ref)
    c.close()


GREET_TODAY = ("A caller just joined and already heard your recorded line asking them to step back; they are not set yet. In ONE "
               "short sentence, under 20 words: {}, say you are Brodie their dance coach in first person "
               "(I'm Brodie), and say the lesson starts the moment they are set. Never say frame, fit, step back or camera, never "
               "describe what you can or cannot see of them, and no tip.")
INTRO_TODAY = ('The caller now fits in the frame and just heard your recorded line saying so; do not repeat it. In ONE short '
               'sentence, in words you choose: do NOT say hi again (you already greeted them), the whole dance plays once first and they '
               'only watch, no dancing yet. No jokes about yourself, no roast and no tip, nothing has been danced yet.')


async def c9(out):
    c = Call(site_pick='bigbowee', env={'DANCE_PICK': '0'}); await asyncio.sleep(0.45)
    tail = c.g['PLAIN_STYLE'] + c.g['NO_COUNT']
    greet = c.asks('just joined')
    check("9 DANCE_PICK=0: the greeting is today's prompt exactly", len(greet) == 1 and any(greet[0] == GREET_TODAY.format(x) + tail for x in c.g['GREETS'])
          and 'In ONE short sentence, under 20 words' in greet[0] and titles_in(greet[0]) == [])
    check('9 DANCE_PICK=0: the site pick is ignored, no menu', c.ref == 'sbbyj' and c.S['menu'] == [] and c.g['MENU'] == [] and not c.g['PICK_ON'], c.ref)
    c.full(); await asyncio.sleep(0.4)
    intro = c.asks('fits in the frame')
    check("9 DANCE_PICK=0: the intro is today's prompt exactly, then the preview of the default", intro == [INTRO_TODAY + tail]
          and c.played() == [(0, c.g['REF'].n_chunks, 'sbbyj', True)], c.played())
    o = out.getvalue()
    check('9 DANCE_PICK=0: "pick off" logged, no pick or lock lines', '[dance] pick off' in o and 'picked' not in o and 'locked' not in o)
    c.close()


async def c10(out):
    saved = dict(screen_mod._MEM); screen_mod._MEM.clear()
    try:
        for n in ('sbbyj', 'bigbowee'): screen_mod._MEM[screen_mod._key(lessons.ref(n).video_path)] = ([b''], 24)
        c = Call(site_pick='', env={'DANCE_VIDEO': '1'}, screen=True); await asyncio.sleep(0.15)
        check('10 DANCE_VIDEO=1: the menu holds only the lessons already in memory', c.g['MENU'] == ['sbbyj', 'bigbowee'] and c.S['pick_open'], c.g['MENU'])
        check('10 set_dance of a lesson not in memory: refused', c.g['set_dance']('deatonioo') is False and c.ref == 'sbbyj')
        c.S['screen_obj'] = types.SimpleNamespace(set_ref=lambda ref, path: False)
        check('10 a screen that cannot switch: set_dance refuses, nothing changes', c.g['set_dance']('bigbowee') is False and c.ref == 'sbbyj'
              and 'lesson bigbowee not switched: video not preloaded' in out.getvalue())
        c.close()
        screen_mod._MEM.clear(); screen_mod._MEM[screen_mod._key(lessons.ref('sbbyj').video_path)] = ([b''], 24)
        c = Call(site_pick='', env={'DANCE_VIDEO': '1'}, screen=True); await asyncio.sleep(0.15)
        check('10 only sbbyj in memory: no menu (pick_open False)', c.g['MENU'] == ['sbbyj'] and not c.S['pick_open'] and c.S['menu'] == []
              and c.g['set_dance']('bigbowee') is False, c.g['MENU'])
        c.close()
    finally:
        screen_mod._MEM.clear(); screen_mod._MEM.update(saved)


async def c11(out):
    sentinel = 'zorblax42'
    err, logs = io.StringIO(), io.StringIO()
    h = logging.StreamHandler(logs); root = logging.getLogger(); root.addHandler(h); lvl = root.level; root.setLevel(logging.DEBUG)
    orig = lessons.match; boom = [0]
    def raising(*a, **k):
        if not boom[0]: boom[0] = 1; raise ValueError(sentinel)
        return orig(*a, **k)
    try:
        with contextlib.redirect_stderr(err):
            c = Call(site_pick=''); await asyncio.sleep(0.45)
            lessons.match = raising
            c.say(f'{sentinel} sweep'); await asyncio.sleep(0.1)
            c.say(f'palm sway wave {sentinel}'); await asyncio.sleep(0.15)
            c.say(sentinel); await asyncio.sleep(0.1)
            c.say(f'my {sentinel} hurts'); await asyncio.sleep(0.15)
            c.full(); await asyncio.sleep(0.4)
            c.close()
    finally:
        lessons.match = orig; root.removeHandler(h); root.setLevel(lvl)
    o = out.getvalue()
    check('11 privacy: the turns still worked (bigbowee picked, rest mode)', c.ref == 'bigbowee' and c.S['rest'], c.ref)
    check('11 privacy: the matcher error is printed by its type only', 'pick matcher failed (ValueError)' in o)
    check('11 privacy: the sentinel is in no stdout, stderr or log output', sentinel not in o and sentinel not in err.getvalue() and sentinel not in logs.getvalue())
    check('11 privacy: the sentinel is in no prompt and no spoken line', all(sentinel not in a for a in c.L.asked) and all(sentinel not in str(s) for s in c.L.said),
          f'{len(c.L.asked)} prompts')


async def c15(out):
    c = Call(site_pick='', env={'DANCE_ANSWER_S': '0.6'}); await asyncio.sleep(0.45)   # greeted with the menu
    greet = c.asks('just joined')
    check('15 the menu greeting carries its one-turn length after the style', greet and greet[0].index(c.g['MENU_TAIL']) > greet[0].index('Style:'))
    mix = c.L.job.player.mix; c.L.model = types.SimpleNamespace(last_caller_t=-1e9)
    mix.active = True; c.full(); await asyncio.sleep(0.5)                          # in frame while Brodie still reads the menu
    check('15 answer hold: no preview while Brodie talks', c.plays == [] and c.S['state'] == 'intro', c.played())
    mix.active = False
    for _ in range(8): c.L.model.last_caller_t = time.monotonic(); await asyncio.sleep(0.1)   # the caller answers (interim transcripts)
    mix.active = True; await asyncio.sleep(0.5); mix.active = False                 # Gemini's own reply to it plays
    check('15 answer hold: no preview while the caller talks and Gemini replies', c.plays == [], c.played())
    c.say('the shoot one'); await asyncio.sleep(0.5)                                # the final transcript, after that reply
    check('15 the answer still counts: deatonioo, one change of plan, one preview', c.ref == 'deatonioo' and len(c.asks('Change of plan')) == 1
          and c.played() == [(0, c.g['REF'].n_chunks, 'deatonioo', True)], c.played())
    c.close()
    c = Call(site_pick='', env={'DANCE_ANSWER_S': '0.6'}); await asyncio.sleep(0.45)
    mix = c.L.job.player.mix; mix.active = True; c.full(); await asyncio.sleep(0.3); mix.active = False; t0 = time.time()
    await asyncio.sleep(1.2)
    dt = c.plays[0][4] - t0 if c.plays else -1
    check('15 nobody answers: the default preview ANSWER_S (0.6 s) after the quiet', c.played() == [(0, c.g['REF'].n_chunks, 'sbbyj', True)]
          and 0.5 <= dt <= 1.0, f'{dt:.2f} s')
    c.close()
    c = Call(site_pick='deatonioo', env={'DANCE_ANSWER_S': '0.6'}); await asyncio.sleep(0.45)
    mix = c.L.job.player.mix; mix.active = True; c.full(); await asyncio.sleep(0.3); mix.active = False; t0 = time.time()
    await asyncio.sleep(0.4)
    check('15 a site pick: no answer hold (nothing was asked)', c.played() == [(0, c.g['REF'].n_chunks, 'deatonioo', True)], c.played())
    c.close()


async def c16(out):
    c = Call(site_pick='', env={'DANCE_ANSWER_S': '0.6'}); await asyncio.sleep(0.45)
    mix = c.L.job.player.mix
    c.full(); c.say('sweep'); mix.active = True; await asyncio.sleep(0.5)          # in frame, "sweep": the question, Brodie talking
    nar = c.asks('two dances share')
    check('16 "sweep" during the intro: one NARROW ask and the preview holds', len(nar) == 1 and c.plays == [], c.played())
    mix.active = False; await asyncio.sleep(0.2); c.say('the second one'); await asyncio.sleep(0.5)
    check('16 the answer to the question counts: deatonioo, one preview', c.ref == 'deatonioo'
          and c.played() == [(0, c.g['REF'].n_chunks, 'deatonioo', True)], c.played())
    c.close()


async def c17(out):
    c = Call(site_pick='bigbowee', env={'DANCE_MENU': 'sbbyj,deatonioo'}); await asyncio.sleep(0.45)
    check("17 a site pick not on this call's menu: kept the default, one log line with the id", c.ref == 'sbbyj' and
          "site pick bigbowee not on this call's menu" in out.getvalue(), c.ref)
    c.close()


async def c18(out):
    M4 = ['sbbyj', 'deatonioo', 'bigbowee', 'zoebaptistee']
    c = Call(site_pick='', env={'DANCE_MENU': ','.join(M4)}); await asyncio.sleep(0.45)
    greet = c.asks('just joined')
    check('18 four songs: the greeting lists all four in menu order, with room for them (40 words)', greet and
          '"Chest Sweep Twist", "Sweep and Shoot", "Palm Sway Wave" or "Stack and Punch"' in greet[0] and 'up to 40 words' in greet[0], greet[0][-200:] if greet else '')
    check('18 four songs: the menu card has four rows', [m[1] for m in c.S['menu']] == [T[n] for n in M4], c.S['menu'])
    c.say('four'); await asyncio.sleep(0.25)
    check('18 "four": Stack and Punch, confirmed', c.ref == 'zoebaptistee' and len(c.asks('They picked "Stack and Punch"')) == 1, c.ref)
    c.close()
    rows = [('stack and punch', 'zoebaptistee'), ('the punch one', 'zoebaptistee'), ('stacking', 'zoebaptistee'), ('the fourth one', 'zoebaptistee'),
            ('the last one', 'zoebaptistee'), ('three', 'bigbowee'), ('for', None), ('frame the face', None), ('fit you in the frame', None),
            ('Chest Sweep Twist, Sweep and Shoot, Palm Sway Wave or Stack and Punch', None), ('not the punch one', None)]
    bad = [(x, lessons.match(x, M4), want) for x, want in rows if lessons.match(x, M4) != want]
    check('18 four songs: the matcher table (title words, "four", never "for" or "frame")', not bad, bad)


# ---------------- 12: the matcher ----------------
def c12():
    table = {'deatonioo': ['Sweep and Shoot', 'the shooting one', 'shoot', 'number two', '2', 'the second one'],
             'sbbyj': ['chest sweep twist', 'chess twist', 'the twisty one', 'the first one', 'one'],
             'bigbowee': ['palm sway wave please', 'palms', 'the wave one', 'three', 'the last one'],
             None: ['sweep', 'chest or palm', 'not the chest one', 'no the wave one', lessons.spoken(M),
                    'Pick the dance by saying its name, "Chest Sweep Twist", "Sweep and Shoot" or "Palm Sway Wave", or it\'s "Chest Sweep Twist".',
                    'one second', 'hold on', 'I want to do it', 'to', 'too', 'I really really want to learn the wave dance today', '', '   ']}
    bad = [(s, lessons.match(s, M), want) for want, ss in table.items() for s in ss if lessons.match(s, M) != want]
    check('12 matcher table', not bad, bad)
    sbad = [(s, lessons.match(s, M, strict=True), w) for s, w in (('wave', None), ('the shoot one', None), ('palm wave', 'bigbowee'), ('two', 'deatonioo'),
                                                                   ('sweep and shoot', 'deatonioo')) if lessons.match(s, M, strict=True) != w]
    check('12 strict: no single word; two title words, a whole title or a number still pick', not sbad, sbad)
    abad = [(s, lessons.ambiguous(s, M), w) for s, w in (('sweep', ['sbbyj', 'deatonioo']), ('the sweep one', ['sbbyj', 'deatonioo']), ('wave', None),
                                                         ('sweep twist', None), ('not sweep', None)) if lessons.ambiguous(s, M) != w]
    check('12 ambiguous: a bare "sweep" names both sweep dances in menu order', not abad, abad)
    check('12 a two-dance menu renumbers: "two" is its second entry', lessons.match('two', ['sbbyj', 'bigbowee']) == 'bigbowee' and lessons.match('three', ['sbbyj', 'bigbowee']) is None)
    rng = random.Random(7)
    pools = [' abcdefghijklmnopqrstuvwxyz', '0123456789', ".,!?'’&-\"", '\U0001F600\U0001F483\U0001F57A❤️', 'chest sweep twist shoot palm sway wave one two not ',
             '中文́​éßİ', '\t\n\r']
    def rand():
        n = rng.choice([0, 1, 3, 8, 20, 60, 300, 5000])
        return ''.join(rng.choice(rng.choice(pools)) for _ in range(n))
    worst, errs, odd = 0.0, 0, 0
    gc.disable()
    try:
        for i in range(2000):
            s = rand() if i % 10 else ' '.join(rng.choice(['chest', 'sweep', 'twist', 'shoot', 'palm', 'sway', 'wave', 'the', 'one', 'not', 'two']) for _ in range(rng.randint(1, 8)))
            t0 = time.perf_counter()
            try: r = (lessons.match(s, M), lessons.match(s, M, strict=True), lessons.ambiguous(s, M))
            except Exception: errs += 1; continue
            worst = max(worst, time.perf_counter() - t0)
            odd += not (r[0] in M + [None] and r[1] in M + [None] and (r[2] is None or set(r[2]) <= set(M)))
    finally:
        gc.enable()
    check('12 fuzz: 2,000 random unicode / emoji / 5 kB turns, no exception, a known answer, each under 2 ms', errs == 0 and odd == 0 and worst < 0.002,
          f'{errs} errors, {odd} odd, slowest {worst * 1000:.2f} ms')
    with open(os.path.join(ROOT, 'web', 'index.html'), encoding='utf-8') as f: html = f.read()
    m = re.search(r'const TITLES\s*=\s*\{([^}]*)\}', html)
    web = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", m.group(1))) if m else None
    check('12 parity: web/index.html TITLES == lessons.TITLES', web == lessons.TITLES, web)


# ---------------- 14: the screen ----------------
async def c14(png_dir):
    import cv2, numpy as np
    from starter.dance.screen import Screen
    ra, rb, rc = (lessons.ref(n) for n in M)
    t0 = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        for r in (ra, rb, rc): screen_mod.preload(r.video_path)    # from data/tmp/screen_cache (decoded once if missing)
    fa, fb = (screen_mod.cached_frames(r.video_path)[0] for r in (ra, rb))
    check('14 the three videos in memory (screen.ready)', all(screen_mod.ready(r.video_path) for r in (ra, rb, rc)) and not screen_mod.ready(None)
          and not screen_mod.ready('/nonexistent.mp4'), f'{time.time() - t0:.1f} s')
    sc = Screen(ra, ra.video_path)
    with contextlib.redirect_stdout(io.StringIO()): sc.load()
    ok = sc.set_ref(rb, rb.video_path)
    check('14 set_ref swaps the lesson and its frames in the same tick', ok and sc.ref is rb and sc.video_path == rb.video_path and sc.frames is fb
          and len(sc.frames) == len(fb) != len(fa), f'{len(fa)} -> {len(sc.frames)} frames')
    check('14 set_ref of a video not in memory is refused, nothing changes', sc.set_ref(ra, ra.path) is False and sc.set_ref(ra, None) is False
          and sc.set_ref(ra, '/nonexistent.mp4') is False and sc.ref is rb)
    real = screen_mod.cached_frames
    def slow(path, fps=screen_mod.FPS_OUT):
        time.sleep(0.3); return real(path, fps)
    sc2 = Screen(ra, ra.video_path); screen_mod.cached_frames = slow
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fut = asyncio.get_running_loop().run_in_executor(None, sc2.load); await asyncio.sleep(0.05)
            sc2.set_ref(rb, rb.video_path); await asyncio.sleep(0.5); await fut
    finally:
        screen_mod.cached_frames = real
    check('14 race: a load still decoding when set_ref ran never overwrites the new frames', sc2.ref is rb and sc2.frames is fb, f'{len(sc2.frames)} frames')
    cam = (np.full((480, 640, 3), 90, np.uint8), None)
    menu = [(1, T['sbbyj'], True), (2, T['deatonioo'], False), (3, T['bigbowee'], False)]
    shots = dict(menu=dict(state='idle', visible=False, chunk=0, play=None, menu=menu, title=T['sbbyj']),
                 plain=dict(state='idle', visible=False, chunk=0, play=None),
                 card=dict(state='waiting', visible=True, chunk=0, play=None, last_pct=70, title=T['deatonioo']))
    sc3 = Screen(ra, ra.video_path)
    with contextlib.redirect_stdout(io.StringIO()): sc3.load()
    imgs, ms = {}, {}
    for name, st in shots.items():
        sc3.render(st, time.time(), cam)
        t1 = time.perf_counter(); imgs[name] = sc3.render(st, time.time(), cam); ms[name] = (time.perf_counter() - t1) * 1000
    plain2 = sc3.render(dict(shots['plain'], menu=[], title=T['sbbyj']), time.time(), cam)
    check('14 the idle card without a menu is the old one (menu [] and a title change nothing)', np.array_equal(imgs['plain'], plain2))
    check('14 the menu and the title change the cards', not np.array_equal(imgs['menu'], imgs['plain'])
          and not np.array_equal(imgs['card'], sc3.render(dict(shots['card'], title=''), time.time(), cam)))
    K = screen_mod.K; y_cam = screen_mod.H - screen_mod.PIP_PAD - screen_mod.PIP_H
    diff = np.any(imgs['menu'] != imgs['plain'], axis=2); rows = np.where(diff.any(axis=1))[0]
    check('14 the menu card stays between the bar and the caller\'s camera', len(rows) and rows.min() >= screen_mod.BAR_Y + screen_mod.BAR_H and rows.max() < y_cam,
          f'rows {rows.min() / K:.0f}..{rows.max() / K:.0f} (360x640 units), camera from {y_cam / K:.0f}' if len(rows) else 'no change')
    check('14 each render under 20 ms', all(v < 20 for v in ms.values()), ', '.join(f'{k} {v:.1f} ms' for k, v in ms.items()))
    if png_dir:
        os.makedirs(png_dir, exist_ok=True)
        for name, img in imgs.items(): cv2.imwrite(os.path.join(png_dir, f'pick_{name}.png'), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        print(f'  PNGs: {png_dir}', file=OUT)


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(); ap.add_argument('--png-dir'); ap.add_argument('--no-screen', action='store_true'); ap.add_argument('-v', action='store_true')
    a = ap.parse_args(); VERBOSE = a.v
    with contextlib.redirect_stdout(io.StringIO()):
        for n in M: lessons.ref(n)                                  # loaded once, shared by every case (as in a worker process)
    for name, fn in (('1', c1), ('2', c2), ('3', c3), ('4', c4), ('5', c5), ('6', c6), ('7', c7), ('8', c8), ('9', c9), ('10', c10), ('11', c11),
                     ('15', c15), ('16', c16), ('17', c17), ('18', c18)):
        case(name, fn)
    c12()
    if not a.no_screen:
        try: asyncio.run(c14(a.png_dir))
        except Exception as e: check('14 screen: ran without an exception', False, repr(e))
    n_fail = sum(1 for _, ok in RESULTS if not ok)
    print(f'\n{len(RESULTS) - n_fail}/{len(RESULTS)} checks passed', file=OUT)
    sys.exit(1 if n_fail else 0)


if __name__ == '__main__':
    main()
