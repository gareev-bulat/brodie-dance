"""Tell the website when a FaceTime call starts and ends, so Brodie's line (brodie/line.py, kept by the website) frees soon
after the caller hangs up instead of 15 minutes after they clicked "FaceTime Brodie".

    line_report.report('start', call_id)     # the worker bound a Chert caller; the reply names the lesson a site visitor picked
    line_report.report('end', call_id)       # the caller left (or the job ended): the website frees the line after the cooldown

POST {BRODIE_SERVER}/api/line/call with the heartbeat key (BRODIE_HEARTBEAT_KEY), the same pair as the heartbeat; off when
they are not set. Never in the way of a call: a short timeout, a few retries for the end, and a failure only means the line
frees at the end of the session as before. Blocking urllib: call it off the event loop (asyncio.to_thread).
The start reply is {ok, source, dance}: dance is the lesson id the visitor holding the line picked on the site ('' for none).
"""
import json, os, re, time, urllib.error, urllib.request

TIMEOUT_S = 3.0
NAME_RE = re.compile(r'[a-z0-9_]{1,40}')   # a lesson id (data/dances/<id>.npz): only such a string is ever printed


def configured():
    return bool(os.environ.get('BRODIE_SERVER') and os.environ.get('BRODIE_HEARTBEAT_KEY'))


def _post(body):
    url = os.environ['BRODIE_SERVER'].rstrip('/') + '/api/line/call'
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', 'X-Brodie-Key': os.environ['BRODIE_HEARTBEAT_KEY']})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r: return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b'{}')
        except Exception: return e.code, {}
    except Exception as e:
        return None, repr(e)


def report(event, call_id='', tries=None):
    """event 'start' (one try) or 'end' (3 tries: a lost end would keep the line busy until the session runs out).
    Returns the website's reply (a dict, never empty) when it took it (HTTP 200), else None; None at once when not configured."""
    if not configured(): return None
    tries = tries or (3 if event == 'end' else 1)
    code, d = None, None
    for i in range(tries):
        code, d = _post({'event': event, 'call_id': call_id or ''})
        if code is not None and code < 500: break
        if i + 1 < tries: time.sleep(0.5 * (i + 1))
    if code == 200:
        d = d if isinstance(d, dict) and d else {'ok': True}         # a 200 is always truthy for the callers that only check that
        if event == 'start':
            dance = d.get('dance'); dance = dance if isinstance(dance, str) and NAME_RE.fullmatch(dance) else ''
            note = f"line held by {d.get('source')}" + (f', lesson {dance}' if dance else '')
        else: note = f"line free in {d.get('free_in_s')} s"
        print(f'[line] call {event} reported ({note})', flush=True); return d
    print(f'[line] WARNING: could not report the call {event} ({code or d}): the line frees at the end of the session instead', flush=True)
    return None
