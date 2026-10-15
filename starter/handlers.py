"""Brodie the dance teacher: teaches a TikTok dance 8 counts at a time, with the song.

Run the worker in dance mode (pose events, no workout script, the voice may speak any time):
  python -m facetime.worker --voice gemini --dance --leave-grace-s -1 dev
Pick the dance with DANCE=<name> (data/dances/<name>.npz, made with starter/dance/reference.py); default: the first one there.

The call:
  1. The caller steps into view -> "Great, I can see you"; Brodie (Gemini) says hi and describes part 1.
  2. Each 8-count part plays as ONE clip: that part of the song with "Five, six, seven, eight" and "One" .. "Eight" mixed
     on top (built here from the song and the bank's count clips, so music and counts never drift). The caller's poses are
     recorded while it plays.
  3. The poses go to starter/dance/judge.py (Steven's model, or the angle comparison against the creator's video).
  4. Pass -> praise and the next part's moves. Miss -> one tip about the worst body part, and the part repeats
     (at most MAX_TRIES times, then it moves on anyway).
  5. Then the whole dance once with the music, judged part by part, and a wrap-up with the scores.

on_event runs on the worker's event loop: never block. The judge runs in a thread; timers use loop.call_later.
"""
import asyncio, collections, glob, os, random, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
import numpy as np  # noqa: E402
from starter.dance.keypoints import mediapipe_to_coco, resample, FPS  # noqa: E402
from starter.dance import judge as judge_mod  # noqa: E402
from starter.dance import reference  # noqa: E402

PASS = float(os.environ.get('DANCE_PASS', '0.5'))           # score that counts as nailing a part (praise instead of a tip)
MAX_TRIES = int(os.environ.get('DANCE_MAX_TRIES', '1'))     # one chance per part: tip or praise, then the next part
LENIENCY = float(os.environ.get('DANCE_LENIENCY', '0.6'))   # displayed score = raw ** LENIENCY (< 1 is kinder: 0.4 -> 0.58, 0.55 -> 0.70)
COUNT_ALOUD = os.environ.get('DANCE_COUNT_ALOUD', '0') != '0'   # count "One" .. "Eight" over the music while teaching (off: no counting at all)
FULL_RUN = os.environ.get('DANCE_FULL_RUN', '1') != '0'     # the whole dance with music at the end
MUSIC_DB = float(os.environ.get('DANCE_MUSIC_DB', '-24'))   # music loudness (RMS dBFS) under the counts
HEAR_LAG_S = 0.15                                           # clip pushed -> heard on the phone, roughly (queue + network)
REST_S = float(os.environ.get('DANCE_REST_S', '5'))         # pause after the feedback before the next count-in starts (breathing room)
SETTLE_S = float(os.environ.get('DANCE_SETTLE_S', '1.0'))   # wait after a part's clip ends before judging it, so the last move counts
NO_COUNT = (" Never count in or count beats aloud (no 'five, six, seven, eight'): the recorded counts and music do that, "
            'you only talk between parts.')                 # appended to every voice prompt: the caller hears ONE count-in
MOTIVATE = (' Style: a warm, high-energy dance coach who believes in them. Celebrate effort and progress, keep every line '
            'positive and encouraging, never discouraging; short and punchy.')
RATE = reference.AUDIO_RATE
COUNT_IN = ['Five', 'Six', 'Seven', 'Eight']
COUNT_WORDS = ['One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight']
PRAISE = ['Nice work', 'Great rhythm', 'Looking strong', "That's it", 'Keep it up']


def _load_reference():
    name = os.environ.get('DANCE')
    try:
        if name: return reference.load(name)
        found = sorted(glob.glob(os.path.join(reference.DANCES_DIR, '*.npz')), key=os.path.getmtime, reverse=True)   # newest first
        return reference.Reference(found[0]) if found else None
    except Exception as e:
        print(f'[dance] could not load the dance {name or ""}: {e!r}', flush=True); return None


class _Practice:
    """Stand-in when there is no reference yet: 100 bpm, 2 chunks, score = how much the caller moved (tests the call flow)."""
    name = 'practice'; bpm = 100.0; first_beat_s = 0.0; n_chunks = 2; audio = None
    beat_s = 60.0 / bpm; chunk_s = 8 * beat_s
    def chunk(self, k): return np.zeros((0, 17, 3), np.float32)
    def step(self, k): return None


REF = _load_reference() or _Practice()
print(f'[dance] dance {REF.name!r}: {REF.n_chunks} parts of 8 counts at {REF.bpm:g} bpm; pass {PASS:.0%}; '
      f'music {"yes" if REF.audio is not None else "no"}; moves described: {sum(1 for k in range(REF.n_chunks) if REF.step(k))}', flush=True)

S = dict(state='idle', visible=False, chunk=0, tries=0, scores={}, full_scores=None, say=None, ask=None,
         last_step_back=0.0, play=None, last_pct=None, screen=False, first_event=0.0, greeted=False)
GREET_AFTER_S = 3.0      # nobody in full view this long after the call starts: Brodie says hi and asks them to step back
STEP_BACK_EVERY_S = 12.0  # then repeats "Step back so I can see your whole body" this often until the whole body is in view
SHOW_VIDEO = os.environ.get('DANCE_VIDEO', '1') != '0'      # Brodie's video track: the creator's dance + counts + cards
POSES = collections.deque(maxlen=FPS * 60)                  # (t_wall, coco (17, 3)) of the last minute


def _loop():
    return asyncio.get_running_loop()


def _player():
    """The worker's cue player, reached through say_now (VoiceLayer.say_now -> job.player); None in events-only mode."""
    try: return S['say'].__self__.job.player
    except Exception: return None


def on_room(room):
    """Called by the worker as soon as it has joined a room (Chert's warm rooms too, before any caller): Brodie's video is
    already running when the FaceTime bridge joins. Started only after the caller was bound, the bridge left 3-4 s into the
    call and Chert logged "Agent failed" (Oct 15 morning); with the video already up (Oct 15, 1 AM) the call went through.
    The video comes from the prewarm cache (screen.cached_frames), so starting it in every warm room costs no decode."""
    _ensure_screen(room)


def _ensure_screen(room=None):
    """Start Brodie's video once the worker's room is reachable (on_room, else through say_now -> VoiceLayer.job.room)."""
    if S['screen'] or not SHOW_VIDEO: return
    S['screen'] = True
    try:
        room = room or S['say'].__self__.job.room
        from starter.dance.screen import Screen
    except Exception as e:
        print(f'[dance] no video track: {e!r}', flush=True); return
    path = getattr(REF, 'video_path', None) or os.path.join(reference.DANCES_DIR, f'{REF.name}.mp4')
    task = _loop().create_task(Screen(REF, path).run(room, lambda: S, _cam))
    task.add_done_callback(lambda t: t.cancelled() or t.exception() is None or print(f'[dance] video stopped: {t.exception()!r}', flush=True))


def _ask(prompt):
    if S['ask']: S['ask'](prompt + MOTIVATE + NO_COUNT)


def _lenient(score):
    """The raw judge score (0..1) made kinder for display and the pass line."""
    return float(max(0.0, min(1.0, score))) ** LENIENCY


def _cam():
    """The caller's latest camera frame and pose for the screen: (rgb HxWx3, kp (33,4) normalised or None), or None before the first frame."""
    try: rgb = S['say'].__self__.job.per.last_rgb
    except Exception: return None
    return None if rgb is None else (rgb, S.get('kp'))


# ---- audio: one clip per part = the song + the counts ----
def _db_gain(pcm, target_db):
    rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) if len(pcm) else 0.0
    return (10 ** (target_db / 20) * 32768 / rms) if rms > 1 else 1.0


def build_clip(k0, k1, count_dance):
    """Parts k0 .. k1-1 as one int16 clip: 4 count-in beats + 8 beats per part. Returns (pcm, dance_offset_s)."""
    player = _player(); beat = REF.beat_s
    t_start = REF.first_beat_s + 8 * k0 * beat - 4 * beat           # the count-in starts 4 beats before ONE
    t_end = REF.first_beat_s + 8 * k1 * beat + 0.3
    n = int((t_end - t_start) * RATE)
    out = np.zeros(n, np.float32)
    if REF.audio is not None:                                       # the song for exactly these beats (zeros before the video starts)
        a = int(round(t_start * RATE)); src = REF.audio.astype(np.float32)
        lo, hi = max(a, 0), min(a + n, len(src))
        if hi > lo:
            seg = src[lo:hi] * _db_gain(src, MUSIC_DB)
            f = min(len(seg), int(0.03 * RATE)); seg[:f] *= np.linspace(0, 1, f); seg[-f:] *= np.linspace(1, 0, f)
            out[lo - a:lo - a + len(seg)] += seg
    words = []                                                      # no spoken count-in: the lead-in is 4 beats of the song (or silence)
    if count_dance:
        words += [(4 * beat + j * beat, COUNT_WORDS[j % 8]) for j in range(8 * (k1 - k0))]
    for t, w in words:
        c = player.bank.get(w) if player is not None else None
        if c is None: continue
        i = int(t * RATE); m = min(len(c), n - i)
        if m > 0: out[i:i + m] += c[:m].astype(np.float32)
    pcm = np.clip(out, -32768, 32767).astype(np.int16)
    pcm = np.concatenate([pcm, np.zeros((-len(pcm)) % 480, np.int16)])  # whole 20 ms frames
    return pcm, 4 * beat


def play_parts(k0, k1, count_dance, then, preview=False):
    """Play parts k0 .. k1-1 (one clip), then call then(windows): windows[k] = (t0, t1) wall times of part k as heard.
    preview: the caller only watches (the screen says so; nothing is judged by the caller of then)."""
    player = _player()
    if player is None:
        print('[dance] no cue player (events-only mode): cannot play the clip', flush=True); return
    pcm, dance_off = build_clip(k0, k1, count_dance)
    key = f'[dance {k0}-{k1} {"counts" if count_dance else "quiet"}]'
    player.bank.cache[key] = pcm                                    # the bank serves cached clips by key: say() plays it
    rec = S['say'](key, 'announce')
    S['play'] = dict(rec=rec, k0=k0, k1=k1, dance_off=dance_off, then=then, dur=len(pcm) / RATE, t_said=time.time(), preview=preview)
    print(f'[dance] playing parts {k0 + 1}..{k1} ({len(pcm) / RATE:.1f} s, music {"on" if REF.audio is not None else "off"}): '
          f'{rec.get("status") if isinstance(rec, dict) else rec}', flush=True)
    _loop().call_later(0.2, _watch_play)


def _watch_play():
    p = S['play']
    if p is None: return
    rec = p['rec'] if isinstance(p['rec'], dict) else {}
    if rec.get('status') in ('dropped', 'missing', 'error', 'cut') or rec.get('t_done') or time.time() - p['t_said'] > p['dur'] + 10:
        S['play'] = None
        t_push = rec.get('t_push') or max(p['t_said'], time.time() - p['dur'])
        start = t_push + HEAR_LAG_S + p['dance_off']
        wins = {k: (start + (k - p['k0']) * REF.chunk_s, start + (k - p['k0'] + 1) * REF.chunk_s) for k in range(p['k0'], p['k1'])}
        _loop().call_later(SETTLE_S, p['then'], wins); return      # let the caller finish the last move before judging
    _loop().call_later(0.1, _watch_play)


def when_quiet(fn, min_wait=2.5, quiet_s=1.0, max_wait=15.0):
    """Run fn once Gemini has finished talking: at least min_wait (its reply takes ~1 s to start), then quiet_s with no voice
    audio buffered, at most max_wait (then the leftover voice audio is dropped so it does not play over the dance)."""
    player = _player(); t0 = time.monotonic(); last = [time.monotonic()]
    def tick():
        mix = getattr(player, 'mix', None) if player is not None else None
        now = time.monotonic()
        if mix is not None and mix.active: last[0] = now
        if now - t0 >= max_wait:
            if mix is not None: mix.clear()
            fn(); return
        if now - t0 >= min_wait and now - last[0] >= quiet_s:
            fn(); return
        _loop().call_later(0.1, tick)
    _loop().call_later(0.1, tick)


# ---- judging ----
def _window_poses(t0, t1):
    pts = [(t, k) for t, k in POSES if t0 - 0.3 <= t <= t1 + 0.3]
    if not pts: return np.zeros((0, 17, 3), np.float32)
    _, grid = resample(np.array([p[0] for p in pts]), np.array([p[1] for p in pts], np.float32), FPS, t0=t0, t1=t1)
    return grid


def _practice_score(kps):
    """No reference: score by movement, so the flow can be tested by anyone dancing anything."""
    seen = kps[..., 2] > 0.3
    if len(kps) < 3 or seen.mean() < 0.3: return dict(score=0.0, worst_part=None, move=None, source='practice')
    xy = kps[..., :2]; scale = max(float(np.ptp(xy[..., 1][seen])), 1.0)
    motion = float(np.abs(np.diff(xy, axis=0)).mean() / scale)
    return dict(score=float(np.clip(motion * 25, 0, 1)), worst_part=None, move=None, source='practice', details=dict(motion=round(motion, 4)))


def judge_async(items, then):
    """items: [(k, (t0, t1))] -> then({k: result}) on the loop; the judge runs in a thread."""
    data = [(k, _window_poses(*w)) for k, w in items]
    def work():
        return {k: (_practice_score(u) if isinstance(REF, _Practice) else judge_mod.judge(u, REF, k)) for k, u in data}
    fut = _loop().run_in_executor(None, work)
    def done(f):
        try: res = f.result()
        except Exception as e: res = {k: dict(score=0.0, worst_part=None, source='error', details=dict(error=repr(e))) for k, _ in items}
        then(res)
    fut.add_done_callback(done)


# ---- the lesson ----
def _describe(k):
    s = REF.step(k)
    return f' The moves for part {k + 1}: "{s.replace(" | ", ", ")}". Explain them simply in one sentence.' if s else ''


PREVIEW = os.environ.get('DANCE_PREVIEW', '1') != '0'      # play the whole dance once (video + song) before teaching part 1


def start_preview():
    """Show the entire dance once, then teach part 1."""
    if not PREVIEW or isinstance(REF, _Practice):
        return teach_part()
    S['state'] = 'preview'
    print('[dance] preview: the whole dance', flush=True)
    def done(_wins):
        S['state'] = 'waiting'
        _ask(f'They just watched the whole {REF.name} dance. In ONE short sentence say now we learn it part by part, starting with part 1.' + _describe(0))
        when_quiet(teach_part, min_wait=REST_S)
    play_parts(0, REF.n_chunks, False, done, preview=True)


def teach_part():
    """Count in and play the current part (after making sure the caller is in frame)."""
    if S['state'] in ('done', 'full'): return
    if not S['visible']:
        if time.time() - S['last_step_back'] > 6:
            S['say']('Step back so I can see your whole body', 'visibility'); S['last_step_back'] = time.time()
        _loop().call_later(3.0, teach_part); return
    S['state'] = 'dancing'; k = S['chunk']
    print(f'[dance] part {k + 1}/{REF.n_chunks}, try {S["tries"] + 1}', flush=True)
    play_parts(k, k + 1, COUNT_ALOUD, lambda wins: judge_async(list(wins.items()), lambda res: on_result(res[k])))


def on_result(res):
    k = S['chunk']; S['tries'] += 1
    score = _lenient(float(res.get('score') or 0.0)); pct = int(round(score * 100)); S['last_pct'] = pct
    S['scores'][k] = max(S['scores'].get(k, 0), pct)
    print(f'[dance] part {k + 1} try {S["tries"]}: {pct}% (raw {float(res.get("score") or 0):.2f}, {res.get("source")}) '
          f'worst {res.get("worst_part")} {res.get("details", "")}', flush=True)
    part = res.get('worst_part')
    tip = f' One concrete, upbeat tip about their {part}.' if part else ' One upbeat tip about matching the timing.'
    if score >= PASS or S['tries'] >= MAX_TRIES:                # one chance per part by default: praise or a tip, then onwards
        if score >= PASS: S['say'](random.choice(PRAISE))
        if k + 1 >= REF.n_chunks:
            return start_full_run()
        S['chunk'] += 1; S['tries'] = 0                          # last_pct stays: the bar keeps the score until the next result
        if score >= PASS:
            _ask(f'They nailed part {k + 1} of the {REF.name} dance with {pct}%. In ONE short sentence celebrate it, then introduce part {k + 2}.' + _describe(k + 1))
        else:
            _ask(f'They gave part {k + 1} of the {REF.name} dance a real go ({pct}%).{tip} Then, in the same breath, introduce part {k + 2}: '
                 f'two short sentences total, all encouragement.' + _describe(k + 1))
    else:
        steps = f' The moves are: "{REF.step(k).replace(" | ", ", ")}".' if REF.step(k) else ''
        _ask(f'They scored {pct}% on part {k + 1} of the {REF.name} dance.{tip}{steps} In ONE or two short sentences, then say we go again.')
    S['state'] = 'waiting'
    when_quiet(teach_part, min_wait=REST_S)                   # feedback, then a real pause before the next count-in


def start_full_run():
    if not FULL_RUN or REF.n_chunks < 2:
        return finish()
    S['state'] = 'full'
    _ask(f'The caller has learned all {REF.n_chunks} parts of the {REF.name} dance. In ONE short sentence, hype them up: '
         f'now the whole dance from the top' + (' with the music' if REF.audio is not None else '') + '; the count-in starts right after you speak.')
    def go():
        print('[dance] full run', flush=True)
        play_parts(0, REF.n_chunks, False, lambda wins: judge_async(sorted(wins.items()), on_full_result))
    when_quiet(go)


def on_full_result(res):
    S['full_scores'] = {k: int(round(_lenient(float(r.get('score') or 0)) * 100)) for k, r in res.items()}
    print(f'[dance] full run: {S["full_scores"]}', flush=True)
    finish()


def finish():
    S['state'] = 'done'
    learn = ', '.join(f'part {i + 1} {s}%' for i, s in sorted(S['scores'].items()))
    full = ''
    if S['full_scores']:
        avg = int(round(sum(S['full_scores'].values()) / len(S['full_scores'])))
        full = f' In the full run they scored {avg}% overall (' + ', '.join(f'part {i + 1} {s}%' for i, s in sorted(S['full_scores'].items())) + ').'
    _ask(f'The caller finished the {REF.name} dance lesson. Best scores while learning: {learn}.{full} In two short sentences, '
         f'celebrate, name their strongest part, and tell them they are ready to post it.')


# ---- the events ----
def _nudge_into_view():
    """Before the lesson, while the caller is not fully in view: a spoken hello and a step-back hint, then the hint every
    STEP_BACK_EVERY_S. Without it the call starts silent until the whole body is in frame, and callers don't know what to do."""
    if S['state'] != 'idle' or S['visible']: return
    now = time.time()
    if not S['first_event']: S['first_event'] = now; return
    if not S['greeted']:
        if now - S['first_event'] < GREET_AFTER_S: return
        S['greeted'] = True; S['last_step_back'] = now
        _ask("A caller just joined the call but you can't see their whole body yet. In ONE short, friendly sentence: say hi, "
             "you're Brodie their dance coach, and ask them to prop the phone up and step back until you can see them head to toe.")
    elif now - S['last_step_back'] > STEP_BACK_EVERY_S:
        S['say']('Step back so I can see your whole body', 'visibility'); S['last_step_back'] = now


def on_event(event, say_now, ask_voice):
    S['say'] = say_now; S['ask'] = ask_voice
    _ensure_screen()
    _nudge_into_view()
    t = event['type']
    if t == 'pose':
        S['kp'] = event['kp'] if event.get('ok') else None       # the latest pose for the screen's skeleton overlay
        if event.get('ok'): POSES.append((event['t_wall'], mediapipe_to_coco(event['kp'], event['wh'])))
    elif t == 'visibility':
        was = S['visible']; S['visible'] = event.get('state') == 'full'
        if S['visible'] and not was and S['state'] == 'idle':
            S['state'] = 'intro'
            say_now('Great, I can see you', 'visibility')
            hi = 'do NOT say hi again (you already greeted them), ' if S['greeted'] else 'say hi, '   # one hello per call
            _ask(f'The caller is in view and ready. In two short sentences: {hi}say today we learn the {REF.name} dance '
                 f'in {REF.n_chunks} parts of eight counts, and that first they just watch the whole dance once.')
            when_quiet(start_preview, min_wait=max(4.0, REST_S))
