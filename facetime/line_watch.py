"""Watch Brodie's FaceTime line for calls that never reach him, and tell the website (brodie/line.py call_dead).

Chert can answer a FaceTime call and then never bring the caller into Brodie's room (Oct 15 morning, Oct 18 night): the
caller sees a still card, hears nothing, and the call ends ~5-15 s later, while the site keeps saying "Brodie is free".
This reads the Chert CLI's log and the worker's log as they are written:

    Chert:  "Incoming FaceTime Video call."   a call starts
    worker: "[worker] bound to 'chert-bridge-"  it reached Brodie (fine; the worker's own report marks the line up)
    Chert:  "Call ended." / "Agent failed"     it ended

A call that lasted at least MIN_CALL_S (8 s) and ended without reaching Brodie is a dead call: POST /api/line/health
{event: dead} with the heartbeat key (BRODIE_SERVER, BRODIE_HEARTBEAT_KEY, as the heartbeat). Two in a row and the site
shows Brodie offline ("having trouble") and the stats page shows the line down in red; the next call that reaches Brodie
puts it back. A caller who hangs up within MIN_CALL_S is not counted (Chert needs ~4 s to reach Brodie).

    .venv/bin/python -m facetime.line_watch                 # follows journalctl -u brodie-chert -u brodie-worker
    .venv/bin/python -m facetime.line_watch --stdin < log    # replay a saved log (tests)
"""
import argparse, json, os, re, subprocess, sys, time, urllib.error, urllib.request

MIN_CALL_S = float(os.environ.get('LINE_WATCH_MIN_CALL_S', '8'))
ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')                  # the Chert CLI colours its output
INCOMING = re.compile(r'Incoming\s+FaceTime', re.I)
BOUND = re.compile(r"\[worker\]\s+bound to\s+'chert-bridge-")
ENDED = re.compile(r'Call\s+ended|Agent\s+failed', re.I)


def entry(raw):
    """One line of `journalctl -o json` -> (message without colour codes, time it was logged in s), or (None, None).
    The journal's own timestamp, not the time we read it: journalctl can hand several lines over at once. MESSAGE is a
    list of byte values when the line has control characters (the CLI's colours)."""
    try: d = json.loads(raw)
    except ValueError: return None, None
    m = d.get('MESSAGE')
    if isinstance(m, list):
        try: m = bytes(m).decode('utf-8', 'replace')
        except (TypeError, ValueError): return None, None
    if not isinstance(m, str): return None, None
    try: t = int(d.get('__REALTIME_TIMESTAMP')) / 1e6
    except (TypeError, ValueError): t = time.time()
    return ANSI.sub('', m), t


class Watch:
    """Feed it log lines (with the time they were seen); it calls report(detail) once per dead call."""
    def __init__(self, report, min_call_s=MIN_CALL_S):
        self.report = report; self.min_call_s = min_call_s
        self.t_in = None; self.bound = False; self.dead = 0; self.ok = 0

    def line(self, text, t):
        text = ANSI.sub('', text)
        if INCOMING.search(text):
            self.t_in = t; self.bound = False
        elif BOUND.search(text):
            if self.t_in is not None: self.bound = True
        elif ENDED.search(text):
            if self.t_in is None: return                       # a second "Call ended" for the same call, or before any call
            dur = t - self.t_in; t_in, bound = self.t_in, self.bound; self.t_in = None
            if bound: self.ok += 1; return
            if dur < self.min_call_s: return                   # hung up before Chert could reach Brodie: not a line problem
            self.dead += 1
            reason = 'agent failed' if 'failed' in text.lower() else 'call ended'
            self.report(f'{reason} after {dur:.0f} s, nobody joined Brodie\'s room')


def post(detail):
    url, key = os.environ.get('BRODIE_SERVER', '').rstrip('/'), os.environ.get('BRODIE_HEARTBEAT_KEY', '')
    if not url or not key:
        print(f'[line-watch] dead call ({detail}); BRODIE_SERVER / BRODIE_HEARTBEAT_KEY not set: not reported', flush=True); return False
    req = urllib.request.Request(url + '/api/line/health', data=json.dumps({'event': 'dead', 'detail': detail}).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', 'X-Brodie-Key': key})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=5) as r: d = json.loads(r.read() or b'{}')
            print(f'[line-watch] dead call reported ({detail}): {d.get("dead_streak")} in a row, line {"DOWN" if d.get("down") else "still up"}', flush=True)
            return True
        except Exception as e:
            err = e; time.sleep(1 + i)
    print(f'[line-watch] WARNING: could not report a dead call ({detail}): {err!r}', flush=True); return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--stdin', action='store_true', help='read log lines from stdin instead of following journalctl')
    a = ap.parse_args()
    w = Watch(post)
    if a.stdin:                                                   # `journalctl ... -o json` lines, or plain text lines
        for ln in sys.stdin:
            msg, t = entry(ln) if ln.lstrip().startswith('{') else (ln, time.time())
            if msg is not None: w.line(msg, t)
        return
    cmd = ['journalctl', '-u', 'brodie-chert', '-u', 'brodie-worker', '-f', '-n', '0', '-o', 'json', '--no-pager']
    print(f'[line-watch] watching: {" ".join(cmd)} (a call of {MIN_CALL_S:g} s+ that never reaches Brodie counts as dead)', flush=True)
    while True:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, bufsize=1)
        n = 0
        for ln in p.stdout:
            msg, t = entry(ln)
            if msg is None: continue
            n += 1; w.line(msg, t)
            if INCOMING.search(msg) or ENDED.search(msg) or BOUND.search(msg): print(f'[line-watch] saw: {msg.strip()[:90]}', flush=True)
        print(f'[line-watch] journalctl stopped after {n} lines; restarting in 5 s', flush=True); time.sleep(5)


if __name__ == '__main__':
    main()
