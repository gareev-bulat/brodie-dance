"""Brodie's one line: one FaceTime session at a time. The server holds it; the worker reports sessions starting and ending.

    line.status(online)                  # {state: open|busy|offline, seconds_left, cap_s, buffer_s, enabled}
    line.start(room, call_id)            # the worker has a caller: (True, None) takes the line, (False, status) when it is busy
    line.end(room)                       # the session ended: busy for the reset buffer, then open

One row in the same sqlite file as the accounts (users.DB), so the line survives a server restart. States:
  busy     a session since t_start; expires on its own at t_start + cap + buffer if no end ever comes (a crash).
  reset    the session ended at t_end; open again at t_end + buffer.
seconds_left is the time until the line frees, never more than the cap (15:00): the page shows it as the countdown.
The line is only on when HEARTBEAT_KEY is set (the worker can then report sessions); otherwise status() is always open and
nothing is ever refused. LINE_CAP_S (900) and LINE_BUFFER_S (30) are read from the environment.
"""
import os, time
from brodie import users

now = time.time                      # tests replace this


def cap_s(): return float(os.environ.get('LINE_CAP_S', '900'))
def buffer_s(): return float(os.environ.get('LINE_BUFFER_S', '30'))
def enabled(): return bool(os.environ.get('HEARTBEAT_KEY'))


def init():
    with users._db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS line (id INTEGER PRIMARY KEY CHECK (id = 1), state TEXT NOT NULL DEFAULT "free", '
                  'room TEXT NOT NULL DEFAULT "", call_id TEXT NOT NULL DEFAULT "", t_start REAL NOT NULL DEFAULT 0, t_end REAL NOT NULL DEFAULT 0)')
        c.execute('INSERT OR IGNORE INTO line (id) VALUES (1)')


def _free_at(r):
    """When the stored row stops holding the line (0 = free now)."""
    if r['state'] == 'busy': return r['t_start'] + cap_s() + buffer_s()
    if r['state'] == 'reset': return r['t_end'] + buffer_s()
    return 0.0


def _held(r, t):
    return r['state'] != 'free' and t < _free_at(r)


def _status(r, t):
    if not _held(r, t): return {'state': 'open', 'seconds_left': 0}
    return {'state': 'busy', 'seconds_left': int(round(min(cap_s(), _free_at(r) - t)))}


def _row(c):
    return c.execute('SELECT * FROM line WHERE id = 1').fetchone()


def status(online=None):
    """online: worker_online() (True / False / None when unknown). Offline wins over everything."""
    base = {'enabled': enabled(), 'cap_s': cap_s(), 'buffer_s': buffer_s()}
    if online is False: return dict(base, state='offline', seconds_left=0)
    if not enabled(): return dict(base, state='open', seconds_left=0)
    with users._db() as c: r = _row(c)
    return dict(base, **_status(r, now()))


def _tx():
    c = users._db(); c.isolation_level = None; c.execute('BEGIN IMMEDIATE')   # one writer at a time: two callers can't both take it
    return c


def start(room, call_id=''):
    """The worker has a caller in `room`. OK when the line is free (or this room already holds it); otherwise (False, status)
    and the worker tells the caller the line is busy."""
    if not enabled(): return True, None
    c = _tx(); t = now()
    try:
        r = _row(c)
        if _held(r, t) and r['room'] != room: c.execute('ROLLBACK'); return False, dict(status(), **_status(r, t))
        if _held(r, t) and r['state'] == 'busy': c.execute('COMMIT'); return True, None          # a repeat start keeps the clock
        c.execute('UPDATE line SET state = "busy", room = ?, call_id = ?, t_start = ?, t_end = 0 WHERE id = 1', (room, call_id or '', t))
        c.execute('COMMIT'); return True, None
    finally: c.close()


def end(room):
    """The session in `room` ended: busy for the reset buffer, then open. An end for another room changes nothing."""
    if not enabled(): return False
    c = _tx(); t = now()
    try:
        r = _row(c)
        if r['room'] != room or r['state'] != 'busy' or not _held(r, t): c.execute('ROLLBACK'); return False
        c.execute('UPDATE line SET state = "reset", t_end = ? WHERE id = 1', (t,))
        c.execute('COMMIT'); return True
    finally: c.close()
