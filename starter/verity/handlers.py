"""Verity on Brodie: the call logic for a Verity character running on Brodie's worker (facetime.worker), as a Brodie handler.

  python -m facetime.worker --voice gemini --perception off --vision --log-caller --text \
      --persona starter/verity/verity.md --handlers starter/verity/handlers.py --avatar verity --voice-name Aoede dev
(scripts/run_verity.sh <character> runs exactly this.)

What it does, from Brodie's events (on_event(event, say_now, ask_voice); voice mode: one `session` {state: open}, then `voice`
events for each turn, caller turns with --log-caller):
  - greets when the call opens;
  - strikes (Verity only): a caller who is rude, or insists the capital of France is somewhere else, gets a strike and the face flashes
    its creepy grin. On the third, once Verity has finished answering, it turns into Cruelty (face and role); an apology turns it
    back the same way. Nothing is injected while the model is mid-reply (Gemini Live would read it out);
  - the -ity verdict the character gives ("you are... Procrastinity") goes to the web app as a verdict card;
  - the web app's settings (Mommy ASMR, strikes) apply live.
Events for the web app go out on data topic "verity", same as before: character | strike | transform | verdict | settings.
on_event runs on the worker's event loop: never block.
"""
import asyncio, json, os, re, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
import facetime.worker as worker  # noqa: E402
from verity.variants import ASMR, VARIANTS  # noqa: E402

STRIKES_TO_CRUELTY = 3
RUDE = re.compile(r"\b(shut up|stupid|idiot|dumb|boring|hate you|you suck|annoying|go away|loser)\b", re.I)
WRONG_CAPITAL = re.compile(r"\b(berlin|madrid|london|rome|lyon|marseille|brussels|tokyo|new york)\b", re.I)
SORRY = re.compile(r"\b(sorry|apologi[sz]e|my bad|forgive me)\b", re.I)
VERDICT = re.compile(r"\b(?i:you(?:'re|\u2019re| are))\b[^?!]{0,40}?\b([A-Z][A-Za-z-]*ity)\b")   # "you are... X-ity", "you're definitely X-ity"

S = dict(strikes=0, cruel=False, last_coach='', verdicts=set(), settings={}, wired=False)


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
        S.update(strikes=0, cruel=False, last_coach='', verdicts=set(), settings=caller_settings())
        set_face(key())
        wire(ask_voice)
        mode = ('\n' + ASMR) if S['settings'].get('asmr') == 'mommy' else ''
        ask_voice('You just picked up the call. Say hi like a friend would, in your own words, and work in your signature line '
                  f'"{VARIANTS[key()]["intro"]}" naturally. One or two short sentences.{mode}')
    elif t == 'voice' and event.get('role') == 'coach':
        S['last_coach'] = event.get('text', '')
        m = VERDICT.search(S['last_coach'])
        if m and m.group(1) not in S['verdicts']:
            S['verdicts'].add(m.group(1)); emit('verdict', name=m.group(1))
    elif t == 'voice' and event.get('role') == 'caller':
        heard(event.get('text', ''), ask_voice)


def heard(text, ask_voice):
    """Strikes (the face flashes and the app counts); the third one, or an apology to Cruelty, switches character once the current
    reply has finished, so nothing is injected mid-sentence."""
    if key() != 'verity' or S['settings'].get('strikes') is False: return
    if S['cruel']:
        if SORRY.search(text): after_reply(lambda: become('verity', ask_voice))
        return
    reason = 'was rude to you' if RUDE.search(text) else \
             'said the capital of France is somewhere else' if WRONG_CAPITAL.search(text) and ('france' in (text + S['last_coach']).lower() or 'capital' in text.lower()) else None
    if not reason: return
    S['strikes'] += 1
    emit('strike', count=S['strikes'], of=STRIKES_TO_CRUELTY, reason=reason)
    j = job()
    if j is not None and j.avatar is not None: j.avatar.flash()
    if S['strikes'] >= STRIKES_TO_CRUELTY:
        S['strikes'] = 0; S['cruel'] = True                  # set now, so a fourth line in the meantime doesn't count twice
        after_reply(lambda: become('cruelty', ask_voice))


def become(k, ask_voice):
    set_face(k); emit('transform', to=k, name=VARIANTS[k]['name'])
    if k == 'cruelty':
        # the role goes into the conversation as context (Gemini Live reads a long ask aloud); the ask itself stays as short as the apology's
        async def go():
            j = job()
            if j is not None and j.voice is not None:
                await j.voice.model.add_context('[Verity has turned into Cruelty, its grumpy evil form, until the caller apologizes.]')
            ask_voice('Say "It\'s me... it\'s Cruelty." and then roast their room or outfit in one short line.')
        asyncio.ensure_future(go())
    else:
        S['cruel'] = False
        ask_voice('They apologized. Say "...fine. FINE." and then you are Verity again: warm, friendly, a little too attached. '
                  'One more short line, then carry on the conversation.')


def after_reply(fn, timeout_s=15.0):
    """Run fn once the character has answered the line just heard and finished speaking it (the face's mouth is idle)."""
    j = job(); t0 = time.monotonic(); loop = asyncio.get_running_loop()
    def check():
        model = j.voice.model if j is not None and j.voice is not None else None
        answered = model is not None and model.last_coach_t > t0
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
