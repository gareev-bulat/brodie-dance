"""Your hook into the coach's event stream (docs/STARTER_EVENTS.md): the worker calls on_event for every event, in order.

on_event(event, say_now, ask_voice)
  event      one event dict (type rep | exercise_seen | fault | cue | visibility | gesture | probe | session | status | voice)
  say_now    say_now(text, intent='other') -> record: a pre-rendered bank string through the fast path, about 0.2 s to heard.
             Only strings in the bank play (facetime/cues_tts.py vocabulary(): counts, "Nice work", "Sink lower", ...; a missing
             string is a record with status "missing"). Priority 'other' never speaks over a count; 'announce' replaces a pending cue.
  ask_voice  ask_voice(prompt) -> 'spoken' | 'queued' | None: a line from the Gemini voice (about 1 s to first audio). Spoken at
             once while the floor is open (session state wait, rest or done, or a few seconds after a gesture or an armed probe),
             else queued for the next opening; a queued prompt is dropped when a set starts before the opening or when it is
             older than 15 s at the opening (a stale answer is worse than silence). None when the worker runs without
             --voice gemini (this file still loads: say_now works, ask_voice does nothing).
The fast path owns the set: the first count of the next set cuts the voice (the reply is interrupted, its buffered audio
dropped), so whatever you ask at a rest has about 5 s (the rest) plus the wait before the first rep to be heard. Ask for one
short sentence, not a speech. Runs on the worker's event loop: return quickly, never block. Exceptions are logged and ignored.
"""


def on_event(event, say_now, ask_voice):
    """Default: ONE short sentence from the voice at each rest (the set that just ended, what comes next) and a warm close at
    the end; nothing anywhere else (the fast path counts, corrects and announces). The set summary (reps, corrections spoken,
    the next item) is already in the model's context when this runs."""
    t = event['type']
    if t == 'session' and event.get('state') == 'rest':
        ask_voice('The set just ended: in ONE short sentence, say how it went and name what comes next.')
    elif t == 'session' and event.get('state') == 'done':
        ask_voice('The whole session is over: close it warmly in one short sentence.')
    # Ideas: say_now('Nice work') on a rep event with confidence agree 1.0 at the halfway mark (a bank string, 0.2 s, never over a
    # count); ask_voice(...) on a gesture event (name tpose: the caller is asking for the next thing; the floor is open 4 s).
