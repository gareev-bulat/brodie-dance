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
     (at most MAX_TRIES times, then it moves on anyway). Every miss makes the teacher angrier (verity.variants.ANGER, and with
     --avatar the face's anger stage, up to the red Cruelty look); a pass calms it down.
  5. Then the whole dance once with the music, judged part by part, and a wrap-up with the scores.

on_event runs on the worker's event loop: never block. The judge runs in a thread; timers use loop.call_later.
"""
import asyncio, collections, glob, os, random, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
import numpy as np  # noqa: E402
import cv2  # noqa: E402
from starter.dance.keypoints import mediapipe_to_coco, resample, FPS  # noqa: E402
from starter.dance import judge as judge_mod  # noqa: E402
from starter.dance import reference  # noqa: E402
from verity.variants import ANGER, CRUELTY_INTRO  # noqa: E402

PASS = float(os.environ.get('DANCE_PASS', '0.7'))           # score needed to move on
MAX_TRIES = int(os.environ.get('DANCE_MAX_TRIES', '3'))
COUNT_ALOUD = os.environ.get('DANCE_COUNT_ALOUD', '1') != '0'   # count "One" .. "Eight" over the music while teaching
FULL_RUN = os.environ.get('DANCE_FULL_RUN', '1') != '0'     # the whole dance with music at the end
MUSIC_DB = float(os.environ.get('DANCE_MUSIC_DB', '-24'))   # music loudness (RMS dBFS) under the counts
HEAR_LAG_S = 0.15                                           # clip pushed -> heard on the phone, roughly (queue + network)
RATE = reference.AUDIO_RATE
COUNT_IN = ['Five', 'Six', 'Seven', 'Eight']
COUNT_WORDS = ['One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight']
PRAISE = ['Nice work', 'Great rhythm', 'Looking strong', "That's it", 'Keep it up']


def _load_reference():
    name = os.environ.get('DANCE')
    try:
        if name: return reference.load(name)
        found = sorted(glob.glob(os.path.join(reference.DANCES_DIR, '*.npz')))
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

S = dict(state='idle', visible=False, vis_full=False, vis_upper=False, upper_run=0, upper_miss=0, chunk=0, tries=0, scores={},
         full_scores=None, say=None, ask=None, last_step_back=0.0, play=None, last_pct=None, screen=False, anger=0)
SHOW_VIDEO = os.environ.get('DANCE_VIDEO', '1') != '0'      # Brodie's video track: the creator's dance + counts + cards
POSES = collections.deque(maxlen=FPS * 60)                  # (t_wall, coco (17, 3)) of the last minute
# A dancer counts as "in view" when Brodie's rule says the whole body is in frame OR the upper body is: nose, both shoulders and
# both hips inside the frame and confidently seen (MediaPipe indices). The judge needs shoulders and hips (keypoints.normalize,
# the trained judge's ok mask); the reference clips are often framed waist-up themselves, and a laptop webcam or a hand-held
# phone rarely shows the ankles. Brodie's own "whole body" rule (facetime/visibility.py) still applies to workouts.
UPPER_BODY = [0, 11, 12, 23, 24]
UPPER_VIS = float(os.environ.get('DANCE_UPPER_VIS', '0.5'))
UPPER_ON_FRAMES, UPPER_OFF_FRAMES = 5, 30                   # 0.5 s in view to count, 3 s lost to drop (like Brodie's partial_s)


def _upper_body_in_frame(kp):
    """kp: the pose event's (33, 4) x, y in 0..1 of the frame, z, visibility -> True when the upper body is inside and seen."""
    try:
        a = np.asarray(kp, np.float32)[UPPER_BODY]
    except Exception:
        return False
    inside = bool(((a[:, 0] > 0.02) & (a[:, 0] < 0.98) & (a[:, 1] > 0.02) & (a[:, 1] < 0.98)).all())
    return inside and bool((a[:, 3] >= UPPER_VIS).all())


def _loop():
    return asyncio.get_running_loop()


def _player():
    """The worker's cue player, reached through say_now (VoiceLayer.say_now -> job.player); None in events-only mode."""
    try: return S['say'].__self__.job.player
    except Exception: return None


def _ensure_screen():
    """Start Brodie's video once the worker's room is reachable (through say_now -> VoiceLayer.job.room). With a face track
    (--avatar, the Verity characters) the creator's clip, the counts and the cards are drawn INTO that track, so the call has one
    video: the clip with the character in a corner while a part plays, the character with the card between parts. Without a
    face the Screen publishes its own track as before."""
    if S['screen'] or not SHOW_VIDEO: return
    S['screen'] = True
    try:
        job = S['say'].__self__.job; room = job.room
        from starter.dance.screen import Screen
    except Exception as e:
        print(f'[dance] no video track: {e!r}', flush=True); return
    path = getattr(REF, 'video_path', None) or os.path.join(reference.DANCES_DIR, f'{REF.name}.mp4')
    screen = Screen(REF, path); avatar = getattr(job, 'avatar', None)
    task = _loop().create_task(_compose_into(avatar, screen) if avatar is not None else screen.run(room, lambda: S))
    task.add_done_callback(lambda t: t.cancelled() or t.exception() is None or print(f'[dance] video stopped: {t.exception()!r}', flush=True))


async def _compose_into(avatar, screen):
    """Replace the avatar's draw(): its own face frame, composed with the Screen (clip + counts while a part plays, the card text
    otherwise). The clip is decoded off the loop first. The mouth sync (verity.avatar.tap) is untouched: it feeds render()."""
    from livekit import rtc
    from starter.dance import screen as screen_mod
    await _loop().run_in_executor(None, screen.load)
    W, H = screen_mod.W, screen_mod.H
    orig_render = avatar.render
    def draw(now):
        face = orig_render(now)
        if face.shape[0] != H or face.shape[1] != W: face = cv2.resize(face, (W, H))
        img = compose(face, screen, S, time.time())
        rgba = np.dstack([img, np.full((H, W), 255, np.uint8)])
        return rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, np.ascontiguousarray(rgba).tobytes())
    avatar.draw = draw
    print('[dance] screen drawn into the face track (clip + counts while a part plays, the card between parts)', flush=True)


def _blend(dst, src_rgba, x, y):
    """Alpha-blend an RGBA image onto an RGB one at (x, y), clipped."""
    h, w = src_rgba.shape[:2]; H, W = dst.shape[:2]
    x0, y0 = max(x, 0), max(y, 0); x1, y1 = min(x + w, W), min(y + h, H)
    if x1 <= x0 or y1 <= y0: return
    s = src_rgba[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32); a = s[..., 3:4] / 255.0
    dst[y0:y1, x0:x1] = (s[..., :3] * a + dst[y0:y1, x0:x1].astype(np.float32) * (1 - a)).astype(np.uint8)


def compose(face, screen, st, now):
    """face: the character's RGBA frame (W x H, the ball mid-frame); -> RGB W x H for the call."""
    from starter.dance.screen import W, H, VOLT, WHITE, _text
    import textwrap
    if screen.playing(st):                                            # a part is playing: the clip and the count, the face small
        out = screen.render(st, now)
        small = cv2.resize(face, (W // 3, H // 3), interpolation=cv2.INTER_AREA)
        _blend(out, small, W - small.shape[1] - 6, H - small.shape[0] - 6)
        return out
    out = np.zeros((H, W, 3), np.uint8)                               # between parts: the face on top, the card below
    top = face[90:500]                                                # the ball and its gear (verity.avatar: centre 320, radius 151)
    th = 290; tw = int(top.shape[1] * th / top.shape[0])
    _blend(out, cv2.resize(top, (tw, th), interpolation=cv2.INTER_AREA), (W - tw) // 2, 6)
    ref = REF; k = st.get('chunk', 0); n = ref.n_chunks; state = st.get('state'); y = 340
    if state == 'idle' or not st.get('visible'):
        for i, line in enumerate(['Step back a little so', 'I can see your head', 'and your hips']):
            _text(out, line, (0, y + 40 * i), 0.9, WHITE, 2, center=True)
        return out
    if state == 'done':
        _text(out, 'YOU DID IT!', (0, y), 1.4, VOLT, 3, center=True)
        fs = st.get('full_scores') or st.get('scores') or {}
        for i, (kk, s) in enumerate(sorted(fs.items())[:6]):
            _text(out, f'Part {kk + 1}: {s}%', (0, y + 55 + 40 * i), 0.9, WHITE, 2, center=True)
        return out
    _text(out, 'FULL DANCE NEXT' if state == 'full' else f'PART {k + 1}/{n}', (0, y), 1.2, VOLT, 3, center=True)
    last = st.get('last_pct'); y += 50
    if last is not None: _text(out, f'Last try: {last}%', (0, y), 0.9, WHITE, 2, center=True); y += 44
    steps = ref.step(k) if state != 'full' else None
    if steps:
        for i, line in enumerate(textwrap.wrap(steps, 30)[:7]):
            _text(out, line, (0, y + 30 * i), 0.62, WHITE, 2, center=True)
    return out


def _ask(prompt):
    if S['ask']: S['ask'](prompt)


def _anger(n):
    """Anger stage n (0 calm .. len(ANGER)-1): the face (--avatar) now; the returned mood goes into the next ask."""
    was, S['anger'] = S['anger'], max(0, min(n, len(ANGER) - 1)); n = S['anger']
    try: avatar = S['say'].__self__.job.avatar
    except Exception: avatar = None
    if avatar is not None: avatar.set_stage(n)
    print(f'[dance] anger {n}', flush=True)
    return (ANGER[n] + (' ' + CRUELTY_INTRO if n == len(ANGER) - 1 and was < n else '')) if n else ''


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
    words = [(i * beat, w) for i, w in enumerate(COUNT_IN)]
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


def play_parts(k0, k1, count_dance, then):
    """Play parts k0 .. k1-1 (one clip), then call then(windows): windows[k] = (t0, t1) wall times of part k as heard."""
    player = _player()
    if player is None:
        print('[dance] no cue player (events-only mode): cannot play the clip', flush=True); return
    pcm, dance_off = build_clip(k0, k1, count_dance)
    key = f'[dance {k0}-{k1} {"counts" if count_dance else "quiet"}]'
    player.bank.cache[key] = pcm                                    # the bank serves cached clips by key: say() plays it
    rec = S['say'](key, 'announce')
    S['play'] = dict(rec=rec, k0=k0, k1=k1, dance_off=dance_off, then=then, dur=len(pcm) / RATE, t_said=time.time())
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
        p['then'](wins); return
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
    return f' The moves for part {k + 1}: "{s}". Explain them simply in one sentence.' if s else ''


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
    score = float(res.get('score') or 0.0); pct = int(round(score * 100)); S['last_pct'] = pct
    S['scores'][k] = max(S['scores'].get(k, 0), pct)
    print(f'[dance] part {k + 1} try {S["tries"]}: {pct}% ({res.get("source")}) worst {res.get("worst_part")} {res.get("details", "")}', flush=True)
    mood = _anger(0 if score >= PASS else S['anger'] + 1)
    if score >= PASS or S['tries'] >= MAX_TRIES:
        if score >= PASS: S['say'](random.choice(PRAISE))
        if k + 1 >= REF.n_chunks:
            return start_full_run()
        S['chunk'] += 1; S['tries'] = 0; S['last_pct'] = None
        what = f'passed part {k + 1} with {pct}%' if score >= PASS else f'got {pct}% on part {k + 1}, close enough to move on'
        _ask(f'The caller {what} of the {REF.name} dance. {mood} In ONE short sentence react, then introduce part {k + 2}.' + _describe(k + 1))
    else:
        part = res.get('worst_part'); move = res.get('move')
        tip = f'The body part furthest from the creator was the {part}.' if part else 'Their timing was off.'
        steps = f' The moves are: "{REF.step(k)}".' if REF.step(k) else ''
        _ask(f'The caller scored {pct}% on part {k + 1} of the {REF.name} dance (they need {int(PASS * 100)}%). {tip}'
             + (f' The move was "{move}".' if move else '') + steps
             + (f' {mood} In ONE or two short sentences: one concrete tip about that body part, said that way, then say we will run it again.'
                if mood else ' In ONE or two short sentences: one concrete, encouraging tip about that body part, then say we will run it again.'))
    S['state'] = 'waiting'
    when_quiet(teach_part)


def start_full_run():
    if not FULL_RUN or REF.n_chunks < 2:
        return finish()
    S['state'] = 'full'
    mood = ANGER[S['anger']] if S['anger'] else ''
    _ask(f'The caller has been through all {REF.n_chunks} parts of the {REF.name} dance. {mood} In ONE short sentence, '
         + ('hype them up' if not mood else 'say it like that') + ': now the whole dance from the top'
         + (' with the music' if REF.audio is not None else '') + ', you will count them in.')
    def go():
        print('[dance] full run', flush=True)
        play_parts(0, REF.n_chunks, False, lambda wins: judge_async(sorted(wins.items()), on_full_result))
    when_quiet(go)


def on_full_result(res):
    S['full_scores'] = {k: int(round(float(r.get('score') or 0) * 100)) for k, r in res.items()}
    print(f'[dance] full run: {S["full_scores"]}', flush=True)
    finish()


def finish():
    S['state'] = 'done'
    learn = ', '.join(f'part {i + 1} {s}%' for i, s in sorted(S['scores'].items()))
    full = ''
    if S['full_scores']:
        avg = int(round(sum(S['full_scores'].values()) / len(S['full_scores'])))
        full = f' In the full run they scored {avg}% overall (' + ', '.join(f'part {i + 1} {s}%' for i, s in sorted(S['full_scores'].items())) + ').'
    scores = list(S['full_scores'].values()) if S['full_scores'] else list(S['scores'].values())
    good = bool(scores) and sum(scores) / len(scores) >= PASS * 100
    mood = _anger(0 if good else S['anger'])
    _ask(f'The caller finished the {REF.name} dance lesson. Best scores while learning: {learn}.{full} {mood} In two short sentences, '
         + ('celebrate, name their strongest part, and tell them they are ready to post it.' if good else
            'be honest that it was not there yet, name their best part, and tell them to practise before they post it.'))


# ---- the events ----
def _set_visible():
    """visible = Brodie's whole-body rule OR the upper-body rule; the first time the dancer is in view the lesson starts."""
    was = S['visible']; S['visible'] = bool(S['vis_full'] or S['vis_upper'])
    if S['visible'] and not was and S['state'] == 'idle':
        S['state'] = 'intro'
        S['say']('Great, I can see you', 'visibility')
        S['ask'](f'The caller is in view and ready. In two short sentences: say hi, say today we learn the {REF.name} dance '
                 f'in {REF.n_chunks} parts of eight counts, and that you will count them in.' + _describe(0))
        when_quiet(teach_part, min_wait=4.0)


def on_event(event, say_now, ask_voice):
    S['say'] = say_now; S['ask'] = ask_voice
    _ensure_screen()
    t = event['type']
    if t == 'pose':
        ok = bool(event.get('ok'))
        if ok: POSES.append((event['t_wall'], mediapipe_to_coco(event['kp'], event['wh'])))
        if ok and _upper_body_in_frame(event['kp']):
            S['upper_run'] += 1; S['upper_miss'] = 0
            if S['upper_run'] >= UPPER_ON_FRAMES and not S['vis_upper']: S['vis_upper'] = True; _set_visible()
        else:
            S['upper_miss'] += 1; S['upper_run'] = 0
            if S['upper_miss'] >= UPPER_OFF_FRAMES and S['vis_upper']: S['vis_upper'] = False; _set_visible()
    elif t == 'visibility':
        S['vis_full'] = event.get('state') == 'full'; _set_visible()
