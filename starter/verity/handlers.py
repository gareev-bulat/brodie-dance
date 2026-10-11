"""Verity on Brodie: the call logic for a Verity character running on Brodie's worker (facetime.worker), as a Brodie handler.

  python -m facetime.worker --voice gemini --perception off --vision --log-caller --text \
      --persona starter/verity/verity.md --handlers starter/verity/handlers.py --avatar verity --voice-name Aoede dev
(scripts/run_verity.sh <character> runs exactly this.)

What it does, from Brodie's events (on_event(event, say_now, ask_voice); voice mode: one `session` {state: open}, then `voice`
events for each turn, caller turns with --log-caller):
  - greets when the call opens;
  - strikes (Verity only): messing up gets a strike. Verity's persona roasts the caller and calls them a chud, and either that word in
    Verity's reply or the caller's own line (rude, or a wrong capital of France) counts, once per mess-up. Each strike is an anger
    stage, same character throughout: the face goes through the stages (verity/models.py STAGES: the white toothy grin, darker
    with red eye sockets, then the red Cruelty look) and, once the reply is over, the mood goes into the conversation as context
    (verity.variants.ANGER: annoyed, furious, yelling). An apology calms it back down. Nothing is injected while the model is
    mid-reply, and nothing is asked (Gemini Live reads injected prompts out);
  - the -ity verdict the character gives ("you are... Procrastinity") goes to the web app as a verdict card;
  - the web app's settings (Mommy ASMR, strikes) apply live.
Events for the web app go out on data topic "verity": character | strike | transform (to cruelty: the last stage) | calm | verdict | settings.
on_event runs on the worker's event loop: never block.
"""
import asyncio, json, os, re, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
import facetime.worker as worker  # noqa: E402
from verity.variants import ANGER, ASMR, CRUELTY_INTRO, VARIANTS  # noqa: E402

MAX_STAGE = len(ANGER) - 1                                # strikes past the Cruelty stage keep it there
RUDE = re.compile(r"\b(shut up|stupid|idiot|dumb|boring|hate you|you suck|annoying|go away|loser)\b", re.I)
WRONG_CAPITAL = re.compile(r"\b(berlin|madrid|london|rome|lyon|marseille|brussels|tokyo|new york)\b", re.I)
CHUD = re.compile(r"\bchuds?\b", re.I)                    # Verity's persona calls them a chud when they mess up
SORRY = re.compile(r"\b(sorry|apologi[sz]e|my bad|forgive me)\b", re.I)
VERDICT = re.compile(r"\b(?i:you(?:'re|\u2019re| are))\b[^?!]{0,40}?\b([A-Z][A-Za-z-]*ity)\b")   # "you are... X-ity", "you're definitely X-ity"

S = dict(strikes=0, last_coach='', pending=False, heard_since_reply=False, late=False, verdicts=set(), settings={}, wired=False)


def job():
    return worker.CURRENT_JOB


def emit(type_, **fields):
    j = job()
    if j is None or j.room is None or not j.room.isconnected(): return
    asyncio.ensure_future(j.room.local_participant.publish_data(json.dumps({'type': type_, **fields}), reliable=True, topic='verity'))


def key():
    j = job(); return (getattr(j.a, 'avatar', None) if j else None) or 'verity'


def caller_settings():
    """verity.settings from the web caller's attributes (the web app's Settings), {} on FaceTime."""
    j = job()
    p = j.room.remote_participants.get(j.bound) if j and j.bound else None
    try: return json.loads(p.attributes.get('verity.settings', '{}')) if p else {}
    except ValueError: return {}


def set_face(k):
    j = job()
    if j is not None and j.avatar is not None: j.avatar.set_variant(VARIANTS[k])
    v = VARIANTS[k]; emit('character', key=k, name=v['name'], color=v['color'], face=v['face'])


def on_event(event, say_now, ask_voice):
    t = event.get('type')
    if t == 'session' and event.get('state') == 'open':
        S.update(strikes=0, last_coach='', pending=False, heard_since_reply=False, late=False, verdicts=set(), settings=caller_settings())
        set_face(key())
        wire(ask_voice)
        mode = ('\n' + ASMR) if S['settings'].get('asmr') == 'mommy' else ''
        ask_voice('You just picked up the call. Say hi like a friend would, in your own words, and work in your signature line '
                  f'"{VARIANTS[key()]["intro"]}" naturally. One or two short sentences.{mode}')
    elif t == 'voice' and event.get('role') == 'coach':
        S['last_coach'] = event.get('text', '')
        if CHUD.search(S['last_coach']): strike('roasted you for messing up', ask_voice, reply=True)
        else: S['pending'] = False                        # the reply to a struck line is over
        S['heard_since_reply'] = False
        m = VERDICT.search(S['last_coach'])
        if m and m.group(1) not in S['verdicts']:
            S['verdicts'].add(m.group(1)); emit('verdict', name=m.group(1))
    elif t == 'voice' and event.get('role') == 'caller':
        heard(event.get('text', ''), ask_voice)


def heard(text, ask_voice):
    """A caller line: a rude line or a wrong capital of France is a strike; an apology calms Verity down."""
    late, S['late'] = S['late'], False; S['heard_since_reply'] = True
    if key() != 'verity' or S['settings'].get('strikes') is False: return
    if SORRY.search(text) and S['strikes']:                  # a real apology: calm down again
        S['strikes'] = 0; S['pending'] = False; emit('calm'); mood(0)
        return
    reason = 'was rude to you' if RUDE.search(text) else \
             'said the capital of France is somewhere else' if WRONG_CAPITAL.search(text) and ('france' in (text + S['last_coach']).lower() or 'capital' in text.lower()) else None
    if reason and not late: strike(reason, ask_voice)


def strike(reason, ask_voice, reply=False):
    """One mess-up, counted once: a caller line that strikes covers Verity's next reply (its "chud" doesn't count again), and when the
    "chud" reply came before the caller's line was transcribed, that late line is the same mess-up. Each one is the next anger stage."""
    if key() != 'verity' or S['settings'].get('strikes') is False: return
    if reply:
        if S['pending']: S['pending'] = False; return
        S['late'] = not S['heard_since_reply']
    else:
        S['pending'] = True
    if S['strikes'] >= MAX_STAGE: return                     # already at its angriest
    S['strikes'] += 1
    emit('strike', count=S['strikes'], of=MAX_STAGE, reason=reason)
    if S['strikes'] == MAX_STAGE: emit('transform', to='cruelty', name=VARIANTS['cruelty']['name'])
    mood(S['strikes'], answered=reply)


def mood(n, answered=False):
    """Anger stage n: the face now; the mood (ANGER[n]) into the conversation once the reply to the caller's line is over
    (answered: it already is, the strike came from the reply)."""
    j = job()
    if j is not None and j.avatar is not None: j.avatar.set_stage(n)
    def tell():
        text = ANGER[n] + (' ' + CRUELTY_INTRO if n == MAX_STAGE else '')
        if j is not None and j.voice is not None: asyncio.ensure_future(j.voice.model.add_context(f'[{text}]'))
    after_reply(tell, wait_answer=not answered)


def after_reply(fn, timeout_s=15.0, wait_answer=True):
    """Run fn once the character has answered the line just heard (wait_answer) and finished speaking (the face's mouth is idle)."""
    j = job(); t0 = time.monotonic(); loop = asyncio.get_running_loop()
    def check():
        model = j.voice.model if j is not None and j.voice is not None else None
        answered = not wait_answer or (model is not None and model.last_coach_t > t0)
        quiet = j is None or j.avatar is None or (not j.avatar.levels and j.avatar.level < 0.02)
        if (answered and quiet) or time.monotonic() - t0 > timeout_s: fn()
        else: loop.call_later(0.25, check)
    loop.call_later(0.5, check)


def wire(ask_voice):
    """Live settings from the web app (participant attributes): Mommy ASMR on/off, strikes on/off."""
    j = job()
    if j is None or S['wired']: return
    S['wired'] = True
    def changed(attrs, p):
        if p.identity != j.bound or 'verity.settings' not in attrs: return
        old, S['settings'] = S['settings'], caller_settings()
        emit('settings', **S['settings'])
        if S['settings'].get('asmr') != old.get('asmr'):
            ask_voice(('Mommy ASMR mode just turned on. From now on:\n' + ASMR + '\nSay one short line in that style.')
                      if S['settings'].get('asmr') == 'mommy' else 'Mommy ASMR mode is off: back to your normal voice, one short line.')
    j.room.on('participant_attributes_changed', changed)
