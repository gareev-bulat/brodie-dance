"""The worker's side of Brodie's one line (brodie/line.py): report calls to the web server, and the lesson's time limit.

    ok, info = line_client.start(room, call_id)   # (True, {...}) take the line; (False, {seconds_left}) busy; (True, None) unreachable/off
    line_client.end(room)
    line_client.timeline()                        # [(at_s, phase)]: last_part, wrap, hangup (from LINE_CAP_S; [] when 0)

Off unless BRODIE_SERVER and BRODIE_HEARTBEAT_KEY are set (the same pair as the heartbeat). If the server cannot be reached
the call goes ahead (fail open) and a WARNING says the line is NOT held: a second FaceTime caller could get in at the same time.
Blocking urllib calls: run them off the event loop (asyncio.to_thread).
"""
import asyncio, json, os, time, urllib.error, urllib.request

TIMEOUT_S = 3.0


def configured():
    return bool(os.environ.get('BRODIE_SERVER') and os.environ.get('BRODIE_HEARTBEAT_KEY'))


def _post(path, body):
    """(status, json) from the server, or (None, error) when it cannot be reached."""
    url = os.environ['BRODIE_SERVER'].rstrip('/') + path
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', 'X-Brodie-Key': os.environ['BRODIE_HEARTBEAT_KEY']})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r: return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b'{}')
        except Exception: return e.code, {}
    except Exception as e:
        return None, repr(e)


def start(room, call_id=None):
    if not configured(): return True, None
    code, d = _post('/api/line/start', {'room': room, 'call_id': call_id or ''})
    if code == 200: print(f'[line] holding the line for {room!r}', flush=True); return True, d
    if code == 409: print(f'[line] line BUSY ({d.get("seconds_left")} s left): refusing the caller in {room!r}', flush=True); return False, d
    print(f'[line] WARNING: could not report the call start to the server ({code or d}): this call does NOT hold the line, '
          f'so a second FaceTime caller could get in at the same time. Going ahead with the call.', flush=True)
    return True, None


def end(room, reason='ended', tries=3):
    """Report the session's end, retried (a dropped request would otherwise leave the line busy until the keepalive times out)."""
    if not configured(): return
    code, d = None, None
    for i in range(tries):
        code, d = _post('/api/line/end', {'room': room, 'reason': reason})
        if code is not None and code < 500: break
        time.sleep(0.5 * (i + 1))
    if code == 200: print(f'[line] released the line for {room!r} ({reason})' if d.get('ended') else f'[line] call end for {room!r}: not holding the line (already expired or reset)', flush=True)
    else: print(f'[line] WARNING: could not report the call end after {tries} tries ({code or d}): the server frees the line once the keepalive stops (~2 min)', flush=True)


def touch_every_s(): return float(os.environ.get('LINE_TOUCH_S', '20'))


def touch(room):
    """Keepalive while the session runs. Failures are quiet: the next one retries, and the server only frees after LINE_STALE_S."""
    if not configured(): return
    code, d = _post('/api/line/touch', {'room': room})
    if code == 200 and not d.get('held'): print(f'[line] keepalive: {room!r} no longer holds the line (an admin reset or an expiry)', flush=True)


def cap_s(): return float(os.environ.get('LINE_CAP_S', '900'))


def timeline(cap=None):
    """The lesson clock from the moment the caller is bound: "last part" 2 min before the cap, stop teaching and wrap up with the
    scores 30 s before it, hang up at the cap. LINE_CAP_S=0: no cap (the old behaviour)."""
    cap = cap_s() if cap is None else cap
    if cap <= 0: return []
    warn, wrap = float(os.environ.get('LINE_WARN_S', '120')), float(os.environ.get('LINE_WRAP_S', '30'))
    return [(max(0.0, cap - warn), 'last_part'), (max(0.0, cap - wrap), 'wrap'), (cap, 'hangup')]


def busy_texts():
    """Every busy sentence the worker can say (1 to 15 minutes): pre-recorded with the cues (facetime/cues_tts.py vocabulary)."""
    return [busy_text(n * 60) for n in range(1, 16)]


async def say_busy(player, floor, text, closed=lambda: False, max_voice_s=7.0):
    """Tell a busy caller, then return as soon as it has been said (the worker hangs up right after).
    The recorded clip (cue bank) first: it cuts anything playing, and we wait for its playout (t_done). No clip (bank not
    rendered yet): the live voice says it, and we wait until its audio has been quiet for a moment (at most max_voice_s).
    Returns 'clip' | 'voice' | 'none'."""
    rec = None
    if player is not None:
        mix = getattr(player, 'mix', None)
        if mix is not None: mix.clear()                                  # the voice model never talks over the busy message
        rec = player.say_now(text, 'line_busy', dict(source='line'))
    if rec is not None and rec.get('status') != 'missing':
        deadline = time.monotonic() + float(rec.get('duration_s') or 4.0) + 3.0
        while not rec.get('t_done') and rec.get('status') not in ('cut', 'error', 'dropped') and time.monotonic() < deadline and not closed():
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)                                         # the far end's jitter buffer: the last word arrives
        print(f'[line] busy clip played ({float(rec.get("duration_s") or 0):.1f} s): {text!r}', flush=True)
        return 'clip'
    print(f'[line] no recorded clip for {text!r}: the live voice says it (render the clips: python -m facetime.cues_tts --backend gemini)', flush=True)
    if floor is None: return 'none'
    floor.ask(f'Say exactly this and nothing else: "{text}"')
    mix = getattr(player, 'mix', None) if player is not None else None
    t0 = time.monotonic(); last = t0
    while time.monotonic() - t0 < max_voice_s and not closed():
        await asyncio.sleep(0.1)
        now = time.monotonic()
        if mix is not None and mix.active: last = now
        if now - t0 >= 1.5 and now - last >= 0.8 and (mix is None or last > t0): break   # spoken and quiet again
    return 'voice'


def busy_text(seconds_left):
    mins = max(1, int(round((seconds_left or 0) / 60.0)))
    return f"Brodie's in a session right now. Call back in about {mins} minute{'s' if mins != 1 else ''}."
