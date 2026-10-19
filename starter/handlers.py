"""Brodie the dance teacher: teaches a TikTok dance 8 counts at a time, with the song.

Run the worker in dance mode (pose events, no workout script, the voice may speak any time):
  python -m facetime.worker --voice gemini --dance --leave-grace-s -1 dev
Pick the dance with DANCE=<name> (data/dances/<name>.npz, made with starter/dance/reference.py); default: the first one there.
The caller may switch it before the preview (starter/dance/lessons.py): the visitor's pick on the site (job.site_pick, from the
line start report), else by saying a title or a number from Brodie's on-screen menu; a voice pick beats a site pick, and the
lesson is locked once the preview starts. DANCE_PICK=0: no picking, the flow and prompts of before.

The call:
  1. The caller steps into view -> "Perfect, you're in the frame" (starter/voice/); Brodie (Gemini) says hi and describes part 1.
  2. Each 8-count part plays as ONE clip: that part of the song with "Five, six, seven, eight" and "One" .. "Eight" mixed
     on top (built here from the song and the bank's count clips, so music and counts never drift). The caller's poses are
     recorded while it plays.
  3. The poses go to starter/dance/judge.py (Steven's model, or the angle comparison against the creator's video).
  4. One reaction per part (no canned praise): Brodie roasts the MOVE, never the person, in a rotating comic angle, with one
     plain tip for the body part furthest off, then the next part (MAX_TRIES, default 1). A second low score in a row, or a
     caller who asks him to stop, gets plain kindness instead (decided here in code). DANCE_ROAST=0 brings back the old
     friendly coach (pair it with --persona starter/persona_friendly.md).
  5. Then the whole dance once with the music, judged part by part, and a wrap-up with the scores.

on_event runs on the worker's event loop: never block. The judge runs in a thread; timers use loop.call_later.
"""
import asyncio, collections, glob, os, random, re, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
import numpy as np  # noqa: E402
from starter.dance.keypoints import mediapipe_to_coco, resample, FPS  # noqa: E402
from starter.dance import judge as judge_mod  # noqa: E402
from starter.dance import reference  # noqa: E402
from starter.dance import lessons  # noqa: E402
from starter.dance import screen as screen_mod  # noqa: E402   (cv2 and LiveKit are imported inside its functions only)

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
ROAST = os.environ.get('DANCE_ROAST', '1') != '0'         # Brodie roasts the moves (testers: "bully me with broken humor"); 0 = friendly
MOTIVATE = (' Style: a warm, high-energy dance coach who believes in them. Celebrate effort and progress, keep every line '
            'positive and encouraging, never discouraging; short and punchy.')      # DANCE_ROAST=0 (the worker then reads persona_friendly.md)
ROAST_STYLE = (' Style: a loud, dramatic, unhinged roast of the MOVE, never the person; clean; no bragging about yourself. Slip '
               'into the angle without naming it: never start with a label and a colon. Never say an arm, leg or any body part is '
               'missing, lost, gone, broken or fell off. Use an opener, joke, sentence shape and tip verb (snap, lift, plant, pull) '
               'not used yet this call. If they seemed upset, very young, mentioned pain or asked you to stop, no roast at all, '
               'just kind.')
PLAIN_STYLE = (' Style: short and plain, no bragging about yourself, one or two short sentences, 25 words max, in words you have '
               'not used yet this call.')                     # every turn that is not a score reaction
KIND_STYLE = (' Style: warm and plain, no jokes, roasting, comic voices or brags at all; this overrides anything above or in your '
              'persona. One or two short sentences, 25 words max, nothing you already said this call.')
FACTS = ('You know nothing else about the move: never say early, late, beats off, a direction to twist or turn, or anything '
         'missing, unless it is written in this note. ')       # the judge gives a score, a limb and maybe a cue; Brodie must not invent more
RATE = reference.AUDIO_RATE
COUNT_IN = ['Five', 'Six', 'Seven', 'Eight']
COUNT_WORDS = ['One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight']
# (the canned praise clips 'Nice work' / 'Keep it up' ... played on every pass are gone: testers heard the same line every part)
ANGLES = [                                                  # one per score reaction, in a fresh random order every call
    'Lose it like a sports commentator screaming at the replay of the move, then the plain physical tip.',
    'Give the move a furious one-star review, then the plain physical tip.',
    'Throw a referee flag on the move with a made-up penalty name, then the plain physical tip.',
    'Sound personally betrayed, like the move ghosted you mid-conversation, then the plain physical tip.',
    'React like the move was a phone on one percent that died halfway through, then the plain physical tip.',
    'Announce that the move left the group chat halfway through, then the plain physical tip to add it back.',
    'React like the move is a video that started buffering at the worst possible moment, then the plain physical tip.',
    'Give the plain physical tip first as one GPS turn instruction, without saying recalculating, then one dramatic line about how the move took that turn.',
    'State the plain physical tip as a brand-new rule of the Brodie Dance Federation, then one outraged line about how this move treated it.',
    'Read the move a dramatic courtroom verdict, then the plain physical tip as its sentence.',
    'React like an elevator that stopped one floor short of where the move was going, then the plain physical tip.',
    'Fuse the joke and the tip into one sentence, worded as a flat-pack furniture manual step for the move (the move is the furniture, never the dancer).',
    'Deliver a reality-show judge\'s dramatic verdict to the move, then the plain physical tip.',
]
BANDS = {                                                   # by pass/miss, then how far past or short of the pass line
    'low': ('It missed. One silly, over-the-top line about what the move did, playful rather than savage, never at them, and do '
            'not say the score. Then the single easiest fix for the {part}, said very plainly.'),
    'mid': ('Just under the line: sound comically robbed on their behalf, roasting the one bit of the move that cost it, without '
            'saying so close or almost. Then the one precise fix for the {part} that would have tipped it over.'),
    'pass': ('A pass: in a few words make clear the move landed (never good job or nice work), then a loud, savage jab at its one '
             'flaw. Then one polish tip for the {part}.'),
    'high': ('A strong pass: act dramatically betrayed that the move outdid your coaching, then still roast the one tiny thing left '
             'to fix. Then one tiny polish tip for the {part}.'),
    'kind': ('No roast this time. Warm and plain: if it passed, say it landed; if it missed, say this part is a tough one, and never '
             'claim any bit of it went right. Then the single easiest fix for the {part}.'),
    'rest': ('They mentioned pain or dizziness: no joke and no tip. In one short sentence tell them to skip or shrink any move that '
             'hurts and to rest; the next part can wait.'),
    'unseen': ('It could not be scored: they were out of the frame. No roast and no score: one plain, friendly line asking them to '
               'stay in the frame.'),
    'friendly_pass': 'They passed: celebrate it in one short sentence, then one polish tip for the {part}.',
    'friendly_miss': 'They gave it a real go: one concrete, upbeat tip about their {part}.',
}
GREETS = [                                                  # one per call, at random: friends who call twice hear a new hello
    'open like a ring announcer introducing yourself',
    'open with an overdramatic weather report for today\'s dance floor',
    'open by complaining that your clipboard has been waiting all day',
    'open like an airport gate announcement that the lesson is now boarding',
    'open like a hype man warming up a crowd',
    'open with a fake breaking-news headline about this dance lesson',
    'open like a game-show host revealing the next contestant',
    'open by announcing you have warmed up for exactly this moment',
]
FULL_RUN_LINES = ['in five words or fewer, the full dance with the music is next', 'hype the full run in five words or fewer',
                  'no more breaks, the whole thing with the music, six words max']
END_JOKES = ['a tiny fake award ceremony for the dance', 'a movie-trailer voice for their comeback', 'a sports-desk final score',
             'a dramatic all-clear on the dance floor', 'a one-line fake press conference', 'a mock-serious medal ceremony']
END_PASS = ['they could post it today', 'the dance is theirs now', 'the next run is the one to film']
END_MISS = ['the whole dance is in their head now', 'every run from here gets cleaner', 'the hard part is done']
PART_WORDS = {'body': 'upper body, the lean and twist', None: 'timing', '': 'timing'}   # "tip about their body" sounded like a body insult
# what the caller says (voice events, role caller; facetime/worker.py hands them to the handlers in memory only, never logged):
# asking Brodie to stop or putting themselves down -> no more roasting this call; pain -> rest, no tips (decided in code)
STOP_RE = re.compile(r"(?<!don't )(?<!dont )\bstop\b(?! the music)|\b(?:that's|thats|you're|youre|so|too|kinda|kind of|very) "
                     r"(?:mean|rude|harsh)\b|\bnot (?:funny|cool|nice)\b|\bbe nice\b|\bquit it\b|\bcut it out\b|\bknock it off\b|"
                     r"\bshut up\b|\bleave me alone\b|\bno more jokes\b|\bdon'?t roast\b|\bthat'?s enough\b|\benough already\b|"
                     r"\bi'?m (?:so )?(?:bad|terrible|trash|sad|upset|crying)\b|\bi suck\b|\bi give up\b|\bi can'?t do (?:this|it)\b|"
                     r"\bmakes me sad\b")
PAIN_RE = re.compile(r"\b(?:hurts?|hurting|pain|painful|dizzy|can'?t breathe|out of breath|injur(?:y|ed)|sprain(?:ed)?|"
                     r"my (?:knee|ankle|back|wrist|shoulder|neck|hip))\b")
# picking the lesson ({t} the title, {menu} the titles in menu order, {a}/{b} two titles): the caller's words never go in a prompt
GREET_MENU = ("A caller just joined and already heard your recorded line asking them to step back; they are not set yet. In TWO "
              "short sentences, 35 words max: {greet}, say you are Brodie their dance coach in first person (I'm Brodie), then say "
              "they pick the dance by saying its name, {menu}, word for word, or it's \"{t}\". Never say frame, fit, step back or "
              "camera, never describe what you can or cannot see of them, and no tip.")
GREET_TITLE = ("A caller just joined and already heard your recorded line asking them to step back; they are not set yet. In ONE "
               "short sentence, under 25 words: {greet}, say you are Brodie their dance coach in first person (I'm Brodie), say "
               "today's dance is \"{t}\" word for word and that the lesson starts the moment they are set. Never say frame, fit, "
               "step back or camera, never describe what you can or cannot see of them, and no tip.")
CONFIRM_VOICE = ('They picked "{t}". In ONE short sentence, under 12 words: say "{t}" word for word as locked in, hype the dance '
                 'itself, and say it starts the moment they are set. Never say frame, fit, step back or camera; no roast, no tip.')
CONFIRM_SITE = ('The dance they picked on the website is "{t}". In ONE short sentence, under 12 words, say so word for word and '
                'that it starts the moment they are set. Never say frame, fit, step back or camera; no roast, no tip.')
CHANGE = ('Change of plan: the dance is now "{t}". In ONE short sentence say that title word for word; it still plays once first '
          'and they only watch. No roast, no tip.')
ANNOUNCE = ('The dance is "{t}". In ONE short sentence say that title word for word; it still plays once first and they only '
            'watch. No roast, no tip.')
NARROW = 'They said a word two dances share. ONE short question, under 12 words: "{a}" or "{b}"? Both names word for word, no roast.'


def _load_reference():
    name = os.environ.get('DANCE')
    try:
        if name: return lessons.ref(name) or reference.load(name)   # lessons.ref: one copy per process, shared with the pick menu
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


SHOW_VIDEO = os.environ.get('DANCE_VIDEO', '1') != '0'      # Brodie's video track: the creator's dance + counts + cards
REF = _load_reference() or _Practice()
print(f'[dance] dance {REF.name!r}: {REF.n_chunks} parts of 8 counts at {REF.bpm:g} bpm; pass {PASS:.0%}; '
      f'music {"yes" if REF.audio is not None else "no"}; moves described: {sum(1 for k in range(REF.n_chunks) if REF.step(k))}', flush=True)
# the lessons this call may switch to: on the menu (lessons.names) and, with the video on, already in memory (the worker's prewarm),
# so a switch never decodes a video during the call; the default must be one of them too, or this job never switches
PICK_ON = lessons.on() and not isinstance(REF, _Practice) and REF.name in lessons.TITLES
MENU = [n for n in lessons.names() if lessons.ref(n) and (not SHOW_VIDEO or screen_mod.ready(lessons.ref(n).video_path))] if PICK_ON else []
if PICK_ON and REF.name not in MENU: MENU = []
SITE_WAIT_S = float(os.environ.get('DANCE_SITE_WAIT_S') or 6)   # the preview waits this long after the first event for the site's pick
# after the menu, the preview waits for this much quiet on both sides: Gemini finalizes the caller's words only once its own reply
# has played (livekit google realtime: the final transcript comes at turn_complete), so a pick lands after that reply, not before
ANSWER_S = float(os.environ.get('DANCE_ANSWER_S') or 3)
ANSWER_MAX_S = 25.0      # but never longer than this after the menu was offered
MENU_TAIL = ' This turn only: two short sentences, up to 35 words, so every title fits.'
print(f'[dance] pick menu: {", ".join(MENU)} (default {REF.name})' if len(MENU) >= 2 else
      '[dance] pick off' + ('' if not PICK_ON else ' (fewer than 2 lessons ready)'), flush=True)

S = dict(state='idle', visible=False, chunk=0, tries=0, scores={}, full_scores=None, say=None, ask=None,
         last_step_back=0.0, play=None, last_pct=None, screen=False, first_event=0.0, greeted=False,
         angles=random.sample(range(len(ANGLES)), len(ANGLES)), angle_i=0, misses=0, kind=False, rest=False, ack='',
         worst={}, full_worst={}, heard='', sb_i=0,
         # picking: open until start_preview; pick_by default|site|voice; site_done once job.site_pick answered; gen / say_gen cancel a
         # stale preview start / confirmation; told_title: the dance Brodie last named; title and menu: for the screen
         pick_open=len(MENU) >= 2, pick_by='default', site_done=False, gen=0, say_gen=0, menu_told=False, narrowed=False,
         told_title='', title=lessons.title(REF.name) if PICK_ON else '', menu=[], screen_obj=None, t_busy=0.0, menu_t=0.0)
GREET_AFTER_S = 3.0      # nobody in full view this long after the call starts: Brodie says hi and asks them to step back
STEP_BACK_EVERY_S = 12.0  # then repeats "Step back so I can fit you in the frame" this often until the caller fits in frame
POSES = collections.deque(maxlen=FPS * 60)                  # (t_wall, coco (17, 3)) of the last minute
SAID = collections.deque(maxlen=8)                          # Brodie's last spoken turns (voice events, role coach)


def _loop():
    return asyncio.get_running_loop()


def _player():
    """The worker's cue player, reached through say_now (VoiceLayer.say_now -> job.player); None in events-only mode."""
    try: return S['say'].__self__.job.player
    except Exception: return None


def _job():
    """The worker's job (VoiceLayer.say_now -> job), for job.site_pick; None before the first event or without one."""
    try: return S['say'].__self__.job
    except Exception: return None


def _menu_card():
    """The screen's numbered menu, [(number, title, is the current one)], while the caller may still pick; [] once locked."""
    S['menu'] = [(i + 1, lessons.title(n), n == REF.name) for i, n in enumerate(MENU)] if S['pick_open'] else []


_menu_card()


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
    scr = Screen(REF, path); S['screen_obj'] = scr                 # kept: set_dance switches its lesson
    task = _loop().create_task(scr.run(room, lambda: S, _cam))
    task.add_done_callback(lambda t: t.cancelled() or t.exception() is None or print(f'[dance] video stopped: {t.exception()!r}', flush=True))


def _style(react=False):
    if S['kind'] or S['rest']: return KIND_STYLE
    if not ROAST: return MOTIVATE
    return ROAST_STYLE if react else PLAIN_STYLE


def _fresh():
    """Brodie's last spoken lines this call: the model must not reuse their openers, jokes or catchphrases."""
    return (' You already said this call: ' + ' | '.join(f'"{x}"' for x in SAID) +
            '. Do not reuse any of their openers, jokes, catchphrases, tip verbs or sentence shapes.') if SAID else ''


def _ask(prompt, react=False, tail=''):
    """react=True only for a score reaction (the roast style); every other turn is short and plain. tail: after the style (a
    one-turn length that the style's cap would otherwise contradict)."""
    if not S['ask']: return
    if S['ack']: prompt = S['ack'] + prompt; S['ack'] = ''       # first words after a stop or pain request: acknowledge it once
    S['ask'](prompt + _fresh() + _style(react) + tail + NO_COUNT)


def _angle():
    i = S['angle_i']; S['angle_i'] = i + 1
    return ANGLES[S['angles'][i % len(S['angles'])]]


def _part(p):
    return PART_WORDS.get(p, p)


def _reaction(k, pct, part, passed, cue=None):
    """The note for one part's result: score, pass or miss, the body part furthest off, the judge's cue, and how to react. Two
    misses in a row, a caller who asked to stop, or pain switch to plain kindness for the rest of the call, decided here in code."""
    pl = PASS * 100
    band = ('high' if pct >= max(75, pl + 15) else 'pass') if passed else ('low' if pct < min(35, pl * 0.7) else 'mid')
    S['misses'] = 0 if passed else S['misses'] + 1
    if S['misses'] >= 2 and not S['kind']:
        S['kind'] = True; print('[dance] two misses in a row: kind mode for the rest of the call', flush=True)
    head = (f'Part {k + 1} just ended: {pct}%, ' + ('a pass' if passed else 'under the pass line') +
            f'; the part furthest from the creator was the {part}. ')
    if cue: head += f'The judge saw: "{cue}". Build the joke and the tip on exactly that. '
    head += FACTS
    if S['rest']: return head + BANDS['rest']
    if not ROAST: return head + BANDS['friendly_pass' if passed else 'friendly_miss'].format(part=part)
    if S['kind']: return head + BANDS['kind'].format(part=part)
    return head + BANDS[band].format(part=part) + f' Comic angle for this line: {_angle()}'


# ---- Brodie's own recorded lines (starter/voice/, rendered with facetime.cues_tts in the bank's voice, kept in git) ----
VOICE_DIR = os.path.join(ROOT, 'starter', 'voice')
STEP_BACKS = ['Step back so I can fit you in the frame',     # was "Step back so I can see your whole body": testers found it creepy
              'Back up a little so you fit in the frame', 'A bit further back, the moves need room']
STEP_BACK = STEP_BACKS[0]
IN_FRAME = "Perfect, you're in the frame"                # was "Great, I can see you"
NEUTRAL = {IN_FRAME: "Let's go", **{t: 'Step further back' for t in STEP_BACKS}}   # bank lines if a clip is missing
OLD_LINE = {IN_FRAME: 'Great, I can see you', **{t: 'Step back so I can see your whole body' for t in STEP_BACKS}}   # last resort only


def _load_voice():
    """Put starter/voice/*.pcm into the cue player's cache under their text (once per call), in the variant that matches the
    bank the worker picked (plain Puck or the Australian Puck bank; either if one file is missing), so say_now plays them
    like any bank string."""
    player = _player()
    if player is None or S.get('voice_loaded'): return
    S['voice_loaded'] = True
    try:
        import json
        from facetime.audio_out import normalize_level
        from facetime import FRAME_SAMPLES
        var = 'puck_au' if os.path.basename(str(getattr(player.bank, 'dir', '')).rstrip('/\\')).endswith('_au') else 'puck'
        S['voice_variant'] = var
        with open(os.path.join(VOICE_DIR, 'manifest.json')) as f: man = json.load(f)
        got = []
        for text, vs in man.items():
            for v in dict.fromkeys((var, 'puck', 'puck_au')):
                m = vs.get(v); path = m and os.path.join(VOICE_DIR, m['file'])
                if path and os.path.exists(path):
                    pcm = normalize_level(np.fromfile(path, np.int16))
                    player.bank.cache[text] = np.concatenate([pcm, np.zeros((-len(pcm)) % FRAME_SAMPLES, np.int16)])
                    got.append(text); break
        missing = [t for t in [IN_FRAME, *STEP_BACKS] if t not in got]
        print(f'[dance] own lines loaded ({var}): {len(got)}' + (f'; WARNING missing {missing}' if missing else ''), flush=True)
    except Exception as e:
        print(f'[dance] WARNING: own lines not loaded ({e!r}); falling back to neutral bank lines', flush=True)


def _line(text, intent='visibility'):
    """Say one of Brodie's recorded lines; if its clip is missing, a neutral bank line, and only as a last resort the old
    wording, so the call never goes silent (Chert's bridge hangs up on an agent it has not heard from within ~5 s)."""
    _load_voice()
    bank = getattr(_player(), 'bank', None)
    if bank is not None:
        for t in (text, NEUTRAL.get(text), OLD_LINE.get(text)):
            if t and (t in bank.cache or bank.get(t) is not None): text = t; break
    return S['say'](text, intent)


def _step_back():
    """The step-back line, a different recording each time (the first one of a call is always STEP_BACK)."""
    i = S['sb_i']; S['sb_i'] = i + 1
    return _line(STEP_BACKS[i % len(STEP_BACKS)])


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
    key = f'[dance {REF.name} {k0}-{k1} {"counts" if count_dance else "quiet"}]'
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
    data = [(k, _window_poses(*w)) for k, w in items]; ref = REF   # the lesson these poses were danced to, read on the loop
    def work():
        return {k: (_practice_score(u) if isinstance(ref, _Practice) else judge_mod.judge(u, ref, k)) for k, u in data}
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
    """Show the entire dance once, then teach part 1. The lesson is locked from here: later picks are ignored."""
    if PICK_ON:
        S['pick_open'] = False; _menu_card(); print(f"[dance] lesson locked: {REF.name} ({S['pick_by']})", flush=True)
    if not PREVIEW or isinstance(REF, _Practice):
        return teach_part()
    S['state'] = 'preview'
    print('[dance] preview: the whole dance', flush=True)
    def done(_wins):
        S['state'] = 'waiting'
        _ask('They just watched the whole dance; now part 1. Without the words "part by part" and without mentioning yourself '
             '(no my, me or I):' + _describe(0))
        when_quiet(teach_part, min_wait=REST_S)
    play_parts(0, REF.n_chunks, False, done, preview=True)


def teach_part():
    """Count in and play the current part (after making sure the caller is in frame)."""
    if S['state'] in ('done', 'full'): return
    if not S['visible']:
        if time.time() - S['last_step_back'] > 6:
            _step_back(); S['last_step_back'] = time.time()
        _loop().call_later(3.0, teach_part); return
    S['state'] = 'dancing'; k = S['chunk']
    print(f'[dance] part {k + 1}/{REF.n_chunks}, try {S["tries"] + 1}', flush=True)
    play_parts(k, k + 1, COUNT_ALOUD, lambda wins: judge_async(list(wins.items()), lambda res: on_result(res[k])))


def on_result(res):
    k = S['chunk']; S['tries'] += 1
    score = _lenient(float(res.get('score') or 0.0)); pct = int(round(score * 100)); S['last_pct'] = pct
    S['scores'][k] = max(S['scores'].get(k, 0), pct)
    part = _part(res.get('worst_part')); S['worst'][k] = part
    print(f'[dance] part {k + 1} try {S["tries"]}: {pct}% (raw {float(res.get("score") or 0):.2f}, {res.get("source")}) '
          f'worst {res.get("worst_part")} {res.get("details", "")}', flush=True)
    passed = score >= PASS
    det = res.get('details') if isinstance(res.get('details'), dict) else {}
    unseen = res.get('source') == 'error' or det.get('reason') in ('no poses', 'body not visible enough')
    # no canned praise clip: one spoken reaction; an unscored part (out of frame, judge error) is never roasted
    react = (f'Part {k + 1} just ended. ' + BANDS['unseen']) if unseen else _reaction(k, pct, part, passed, res.get('cue'))
    if passed or S['tries'] >= MAX_TRIES:                       # one chance per part by default: a reaction, then onwards
        if k + 1 >= REF.n_chunks:
            if S['rest']: return finish(react)                  # after pain: no full run, end the lesson gently
            return start_full_run(react, not unseen)            # the last part's score gets its reaction too
        S['chunk'] += 1; S['tries'] = 0                          # last_pct stays: the bar keeps the score until the next result
        nxt = REF.step(k + 1); first = nxt.split(' | ')[0] if nxt else ''
        nxt_line = (f' Then say part {k + 2} starts in a little while, only if it feels fine.' if S['rest'] else
                    f' Then one more sentence, under 10 words, introducing part {k + 2}' + (f', which starts with: "{first}"' if first else '') + '.')
        _ask(react + nxt_line + ' This turn only: up to three short sentences, 35 words max.', react=not unseen)
    else:
        _ask(react + ' Then say we run this part again.', react=not unseen)
    S['state'] = 'waiting'
    when_quiet(teach_part, min_wait=REST_S * (3 if S['rest'] else 1))   # feedback, then a real pause (longer after pain)


def start_full_run(react=None, roast=True):
    """The whole dance once; react (the last part's reaction note) is spoken first, in the same turn."""
    if not FULL_RUN or REF.n_chunks < 2:
        return finish(react)
    S['state'] = 'full'
    line = random.choice(FULL_RUN_LINES) + ', never saying the parts are learned'
    if react: _ask(react + f' Then one more short sentence: {line}. This turn only: up to three short sentences, 35 words max.', react=roast)
    else: _ask(f'In ONE short sentence: {line}.')
    def go():
        print('[dance] full run', flush=True)
        play_parts(0, REF.n_chunks, False, lambda wins: judge_async(sorted(wins.items()), on_full_result))
    when_quiet(go)


def on_full_result(res):
    S['full_scores'] = {k: int(round(_lenient(float(r.get('score') or 0)) * 100)) for k, r in res.items()}
    S['full_worst'] = {k: _part(r.get('worst_part')) for k, r in res.items()}
    print(f'[dance] full run: {S["full_scores"]}', flush=True)
    finish()


def finish(react=None):
    """The wrap-up: strongest part, one tip for the weakest (unless that tip was just given), a sincere ending in Brodie's own
    words. react: a last part's reaction to speak first, in the same turn (no full run)."""
    S['state'] = 'done'
    learn = ', '.join(f'part {i + 1} {s}%' for i, s in sorted(S['scores'].items()))
    full = ''
    if S['full_scores']:
        avg = int(round(sum(S['full_scores'].values()) / len(S['full_scores'])))
        full = f' In the full run they scored {avg}% overall (' + ', '.join(f'part {i + 1} {s}%' for i, s in sorted(S['full_scores'].items())) + ').'
    final = S['full_scores'] or S['scores']
    weak = min(final, key=final.get) if final else None
    strong = max(final, key=final.get) if final else None
    passed = bool(final) and sum(final.values()) / len(final) >= PASS * 100
    limb = (S['full_worst'].get(weak) or S['worst'].get(weak) or 'timing') if weak is not None else None
    last = REF.n_chunks - 1
    just_said = weak == last and limb == S['worst'].get(last)    # the last part's tip was given a moment ago
    weak_tip = (f' For part {weak + 1}, give one plain physical tip about the {limb}, worded unlike any tip you gave earlier.'
                if weak is not None and weak != strong and not just_said else '')
    end = random.choice(END_PASS if passed else END_MISS)
    joke = '' if (S['kind'] or S['rest'] or not ROAST) else f'Open with one tiny joke framed as {random.choice(END_JOKES)}, never about your clipboard. '
    strong_txt = f' (part {strong + 1})' if strong is not None else ''
    pre = (react + ' Then, in the same turn: ') if react else ''
    _ask(pre + f'The lesson is over. Best scores while learning: {learn}.{full} In two short sentences, 30 words max: {joke}name their '
         f'strongest part{strong_txt}.{weak_tip} End sincerely, in your own words, meaning: {end}.')


# ---- picking the lesson (loop thread only; open from the first event until start_preview) ----
def set_dance(name):
    """Switch the lesson to `name` (on MENU): REF, the screen (to a video already in memory), the part counters, the title and
    the menu card. False, nothing changed, when it is not on the menu or its video is not preloaded."""
    global REF
    new = lessons.ref(name) if name in MENU else None
    if new is None: return False
    scr = S['screen_obj']
    if SHOW_VIDEO and scr is not None and not scr.set_ref(new, new.video_path):
        print(f'[dance] lesson {name} not switched: video not preloaded', flush=True); return False
    REF = new; S['chunk'] = S['tries'] = 0; S['title'] = lessons.title(name); _menu_card()
    return True


def _choose(name, by):
    """A pick, by 'site' (the visitor's tile) or 'voice' (a title or number the caller said), while the menu is open; a voice pick
    beats a site pick. Before the greeting the greeting names it; idle after it, a short confirmation; during the intro, the new
    title, and the preview waits for it. True when the pick holds."""
    if not S['pick_open'] or S['play'] is not None or S['state'] not in ('idle', 'intro'): return False
    if by == 'site' and S['pick_by'] == 'voice': return False
    if name == REF.name: S['pick_by'] = by; return True
    if not set_dance(name): return False
    S['pick_by'] = by; t = S['title']
    print(f'[dance] lesson {name} ({"picked on the site" if by == "site" else "picked by voice"})', flush=True)
    if S['state'] == 'intro':                                       # the stale preview start (old gen) does nothing; this one waits
        S['gen'] += 1; g = S['gen']
        _ask((CHANGE if S['told_title'] not in ('', name) else ANNOUNCE).format(t=t)); S['told_title'] = name
        when_quiet(lambda: _start_preview_gated(g), min_wait=max(4.0, REST_S))
    elif S['greeted']:                                              # after Gemini's own reply to the turn, only the latest pick's
        S['say_gen'] += 1; sg = S['say_gen']; prompt = (CONFIRM_VOICE if by == 'voice' else CONFIRM_SITE).format(t=t)
        def confirm():
            if sg != S['say_gen'] or S['state'] != 'idle': return
            S['told_title'] = name; _ask(prompt)
        when_quiet(confirm, min_wait=2.0, quiet_s=0.6)
    return True


def _check_site_pick():
    """The visitor's pick on the site, applied once it is known: job.site_pick (facetime/worker.py, from the line start report) is
    None until the report answered, '' for no pick (no report, a direct caller, an error)."""
    if S['site_done'] or not PICK_ON: return
    sp = getattr(_job(), 'site_pick', '')
    if sp is None: return
    S['site_done'] = True
    if sp in MENU: _choose(sp, 'site')
    elif sp: print(f"[dance] site pick {sp} not on this call's menu (video not preloaded?): kept {REF.name}", flush=True)


def _note_busy():
    """S['t_busy']: the last time any of Brodie's audio (a recorded clip, the music, the model's reply) was playing or queued."""
    pl = _player()
    if pl is None: return
    mix = getattr(pl, 'mix', None)
    if (getattr(pl, 'playing', None) is not None or getattr(pl, 'pending', None) is not None or getattr(pl, 'urgent', None) is not None
            or (mix is not None and (getattr(mix, 'active', False) or (getattr(mix, 'pending', 0) or 0) > 0))):
        S['t_busy'] = time.time()


def _quiet_for():
    """Seconds since Brodie's audio played or the caller was heard talking (the model's interim transcripts, last_caller_t)."""
    _note_busy(); q = time.time() - S['t_busy']
    try: q = min(q, time.monotonic() - float(S['say'].__self__.model.last_caller_t))
    except Exception: pass
    return q


def _start_preview_gated(g):
    """start_preview once the site's pick is known, or SITE_WAIT_S after the first event (then the dance as it is); after the
    spoken menu, not before ANSWER_S of quiet (time to answer it). g: S['gen'] when this was scheduled; a pick in between
    re-schedules it, and the stale call does nothing."""
    if g != S['gen'] or S['state'] != 'intro': return
    _check_site_pick()
    if g != S['gen']: return                                        # that site pick switched the lesson: its announcement re-scheduled it
    now = time.time()
    if (S['pick_open'] and not S['site_done'] and S['pick_by'] != 'voice'          # (a voice pick beats any site pick: no wait)
            and now - (S['first_event'] or now) < SITE_WAIT_S):
        _loop().call_later(0.25, _start_preview_gated, g); return
    if (S['pick_open'] and S['pick_by'] == 'default' and (S['menu_told'] or S['narrowed'])   # nobody picked yet: room to answer
            and now - S['menu_t'] < ANSWER_MAX_S and _quiet_for() < ANSWER_S):
        _loop().call_later(0.25, _start_preview_gated, g); return
    start_preview()


def _voice_pick(text):
    """A spoken pick in the caller's newest turn only (never S['heard']), while the menu is open: a title, a word only one title
    has, or a number on the screen. Nothing of the turn is printed or kept; a matcher error is printed by its type only. Brodie's
    own menu heard back on a speakerphone names every title: lessons.match refuses two or more whole titles in one turn."""
    if not S['pick_open'] or S['state'] not in ('idle', 'intro') or S['play'] is not None: return
    low = text.lower()
    if PAIN_RE.search(low) or STOP_RE.search(low): return            # "my chest hurts", "stop, twist": never a pick
    try:
        n = lessons.match(text, MENU, strict=S['pick_by'] == 'site')   # strict: only a title, two of its words or a number beat the site
        amb = None if n else lessons.ambiguous(text, MENU)
    except Exception as e:
        print(f'[dance] pick matcher failed ({type(e).__name__})', flush=True); n = amb = None
    if n: _choose(n, 'voice')
    elif amb and not S['narrowed'] and S['pick_by'] == 'default':     # a bare "sweep": ask which, once, in menu order
        S['narrowed'] = True; S['menu_t'] = time.time(); prompt = NARROW.format(a=lessons.title(amb[0]), b=lessons.title(amb[1]))
        g = None
        if S['state'] == 'intro': S['gen'] += 1; g = S['gen']          # the question holds the preview: the answer still counts
        def narrow():
            if not S['pick_open']: return
            _ask(prompt)
            if g is not None: when_quiet(lambda: _start_preview_gated(g), min_wait=max(4.0, REST_S))
        when_quiet(narrow, min_wait=2.0, quiet_s=0.6)


# ---- the events ----
def _nudge_into_view():
    """Before the lesson, while the caller is not fully in view: a spoken hello and a step-back hint, then the hint every
    STEP_BACK_EVERY_S. Without it the call starts silent until the caller fits in the frame, and callers don't know what to do."""
    if S['state'] != 'idle' or S['visible']: return
    now = time.time()
    if not S['first_event']:
        # the very first frame of the call: speak AT ONCE with a recorded clip (no model round trip). Brodie's audio track
        # sends nothing while he is silent, and Chert's bridge hangs up on an agent it hasn't heard from within ~5 s of
        # connecting ("Agent failed", Oct 15 morning: every call dropped 4-7 s in, just before his first words).
        S['first_event'] = now; S['last_step_back'] = now
        _step_back(); return
    if not S['greeted']:
        if now - S['first_event'] < GREET_AFTER_S: return
        S['greeted'] = True; S['last_step_back'] = now
        if not PICK_ON:
            _ask("A caller just joined and already heard your recorded line asking them to step back; they are not set yet. In ONE "
                 f"short sentence, under 20 words: {random.choice(GREETS)}, say you are Brodie their dance coach in first person "
                 "(I'm Brodie), and say the lesson starts the moment they are set. Never say frame, fit, step back or camera, never "
                 "describe what you can or cannot see of them, and no tip.")
        elif S['pick_open'] and S['pick_by'] == 'default':          # nobody picked yet: the menu by name (the screen numbers it)
            S['menu_told'] = True; S['menu_t'] = now; S['told_title'] = REF.name
            _ask(GREET_MENU.format(greet=random.choice(GREETS), menu=lessons.spoken(MENU), t=S['title']), tail=MENU_TAIL)
        else:                                                       # picked on the site (or by voice already), or nothing to pick
            S['told_title'] = REF.name
            _ask(GREET_TITLE.format(greet=random.choice(GREETS), t=S['title']))
    elif now - S['last_step_back'] > STEP_BACK_EVERY_S:
        _step_back(); S['last_step_back'] = now


def on_event(event, say_now, ask_voice):
    S['say'] = say_now; S['ask'] = ask_voice
    _ensure_screen()
    if S['pick_open']: _note_busy()
    _check_site_pick()
    _nudge_into_view()
    t = event['type']
    if t == 'voice' and event.get('text'):
        text = ' '.join(str(event['text']).split())
        if event.get('role') == 'coach':
            SAID.append(text[:160])                                 # what Brodie just said, so the next prompt can forbid repeats
        elif event.get('role') == 'caller':
            S['heard'] = (S['heard'] + ' ' + text.lower())[-300:]   # the last few caller turns, so a phrase split across turns still matches
            if PAIN_RE.search(S['heard']) and not S['rest']:
                S['rest'] = S['kind'] = True
                S['ack'] = 'First, in a few words, tell them to take it easy and skip anything that hurts. '
                print('[dance] caller mentioned pain: rest and kind mode for the rest of the call', flush=True)
            elif STOP_RE.search(S['heard']) and not S['kind']:
                S['kind'] = True                                    # "stop", "that's mean", "I'm so bad at this": no more roasting, decided in code
                S['ack'] = 'First, in a few words, say sure, no more jokes from here. '
                print('[dance] caller asked to stop or put themselves down: kind mode for the rest of the call', flush=True)
            _voice_pick(text)
        return
    if t == 'pose':
        S['kp'] = event['kp'] if event.get('ok') else None       # the latest pose for the screen's skeleton overlay
        if event.get('ok'): POSES.append((event['t_wall'], mediapipe_to_coco(event['kp'], event['wh'])))
    elif t == 'visibility':
        was = S['visible']; S['visible'] = event.get('state') == 'full'
        if S['visible'] and not was and S['state'] == 'idle':
            S['state'] = 'intro'
            _line(IN_FRAME)
            hi = 'do NOT say hi again (you already greeted them), ' if S['greeted'] else 'say hi, '   # one hello per call
            one, note, wait, tail = 'ONE short sentence', '', max(4.0, REST_S), ''
            known = PICK_ON and (S['site_done'] or S['pick_by'] != 'default')   # the lesson is known (not while the site may still pick)
            if known: note = f' The dance is "{S["title"]}": say that title word for word.'; S['told_title'] = REF.name
            if S['pick_open'] and S['pick_by'] == 'default' and not S['menu_told']:   # in frame before the greeting: the menu now
                S['menu_told'] = True; S['menu_t'] = time.time(); one, wait, tail = 'TWO short sentences', max(8.0, REST_S), MENU_TAIL
                note += (f' Then say they can still switch by saying another name: {lessons.spoken([n for n in MENU if n != REF.name])}.'
                         if known else f' Then say they pick the dance by saying its name: {lessons.spoken(MENU)}.')
            _ask(f'The caller now fits in the frame and just heard your recorded line saying so; do not repeat it. In {one}, '
                 f'in words you choose: {hi}the whole dance plays once first and they only watch, no dancing yet. No jokes '
                 f'about yourself, no roast and no tip, nothing has been danced yet.' + note, tail=tail)
            g = S['gen']; when_quiet(lambda: _start_preview_gated(g), min_wait=wait)
