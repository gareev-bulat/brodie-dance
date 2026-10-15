"""The worker's side of Brodie's one line (brodie/line.py): report calls to the web server, and the lesson's time limit.

    ok, info = line_client.start(room, call_id)   # (True, {...}) take the line; (False, {seconds_left}) busy; (True, None) unreachable/off
    line_client.end(room)
    line_client.timeline()                        # [(at_s, phase)]: last_part, wrap, hangup (from LINE_CAP_S; [] when 0)

Off unless BRODIE_SERVER and BRODIE_HEARTBEAT_KEY are set (the same pair as the heartbeat). If the server cannot be reached
the call goes ahead (fail open) and a WARNING says the line is NOT held: a web caller could start a call at the same time.
Blocking urllib calls: run them off the event loop (asyncio.to_thread).
"""
import json, os, urllib.error, urllib.request

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
          f'so a web caller could start a call at the same time. Going ahead with the call.', flush=True)
    return True, None


def end(room):
    if not configured(): return
    code, d = _post('/api/line/end', {'room': room})
    if code == 200: print(f'[line] released the line for {room!r} (reset buffer on the server)' if d.get('ended') else f'[line] call end for {room!r}: not holding the line (already expired)', flush=True)
    else: print(f'[line] WARNING: could not report the call end ({code or d}): the server frees the line on its own at the cap', flush=True)


def cap_s(): return float(os.environ.get('LINE_CAP_S', '900'))


def timeline(cap=None):
    """The lesson clock from the moment the caller is bound: "last part" 2 min before the cap, stop teaching and wrap up with the
    scores 30 s before it, hang up at the cap. LINE_CAP_S=0: no cap (the old behaviour)."""
    cap = cap_s() if cap is None else cap
    if cap <= 0: return []
    warn, wrap = float(os.environ.get('LINE_WARN_S', '120')), float(os.environ.get('LINE_WRAP_S', '30'))
    return [(max(0.0, cap - warn), 'last_part'), (max(0.0, cap - wrap), 'wrap'), (cap, 'hangup')]


def busy_text(seconds_left):
    mins = max(1, int(round((seconds_left or 0) / 60.0)))
    return f"Brodie's in a session right now. Call back in about {mins} minute{'s' if mins != 1 else ''}."
