"""Brodie's one line: one FaceTime session at a time. The server holds it; the worker reports sessions starting and ending.

    line.status(online)                  # {state: open|busy|offline, seconds_left, cap_s, buffer_s, enabled}
    line.start(room, call_id)            # the worker has a caller: (True, None) takes the line, (False, status) when it is busy
    line.touch(room)                     # the worker's keepalive every ~20 s while the session runs
    line.end(room, reason)               # the session ended: busy for the reset buffer, then open
    line.reset(reason)                   # admin: free now (no buffer), the held session logged as ended with the reason
    line.info(online), line.log()        # admin view: who holds the line since when, keepalive age; the recent events

One row in the same sqlite file as the accounts (users.DB), so the line survives a server restart. States:
  busy     a session since t_start. Frees on its own when the worker's keepalive stops for LINE_STALE_S (crash, network
           gone: + the buffer), and at the latest at t_start + cap + buffer (an old worker that sends no keepalive).
  reset    the session ended at t_end; open again at t_end + buffer.
seconds_left is the time until the line frees, never more than the cap (15:00): the page shows it as the countdown.
Every start, end and reset goes into line_log (and stdout). The line is only on when HEARTBEAT_KEY is set; otherwise
status() is always open and nothing is ever refused. LINE_CAP_S (900), LINE_BUFFER_S (30), LINE_STALE_S (75).
"""
import os, time
from brodie import users

now = time.time                      # tests replace this


def cap_s(): return float(os.environ.get('LINE_CAP_S', '900'))
def buffer_s(): return float(os.environ.get('LINE_BUFFER_S', '30'))
def stale_s(): return float(os.environ.get('LINE_STALE_S', '75'))
def enabled(): return bool(os.environ.get('HEARTBEAT_KEY'))


def init():
    with users._db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS line (id INTEGER PRIMARY KEY CHECK (id = 1), state TEXT NOT NULL DEFAULT "free", '
                  'room TEXT NOT NULL DEFAULT "", call_id TEXT NOT NULL DEFAULT "", t_start REAL NOT NULL DEFAULT 0, t_end REAL NOT NULL DEFAULT 0)')
        if 't_touch' not in {r[1] for r in c.execute('PRAGMA table_info(line)')}:
            c.execute('ALTER TABLE line ADD COLUMN t_touch REAL NOT NULL DEFAULT 0')
        c.execute('INSERT OR IGNORE INTO line (id) VALUES (1)')
        c.execute('CREATE TABLE IF NOT EXISTS line_log (id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL, event TEXT NOT NULL, '
                  'room TEXT NOT NULL DEFAULT "", call_id TEXT NOT NULL DEFAULT "", reason TEXT NOT NULL DEFAULT "", before TEXT NOT NULL DEFAULT "")')


def _free_at(r):
    """When the stored row stops holding the line (0 = free now)."""
    if r['state'] == 'busy':
        t = r['t_start'] + cap_s() + buffer_s()
        if r['t_touch']: t = min(t, r['t_touch'] + stale_s() + buffer_s())     # keepalive stopped: the session is gone
        return t
    if r['state'] == 'reset': return r['t_end'] + buffer_s()
    return 0.0


def _planned_free_at(r):
    """The countdown: when the line frees if the session runs to the cap (the keepalive only decides that it is still alive)."""
    if r['state'] == 'busy': return r['t_start'] + cap_s() + buffer_s()
    return _free_at(r)


def _held(r, t):
    return r['state'] != 'free' and t < _free_at(r)


def _status(r, t):
    if not _held(r, t): return {'state': 'open', 'seconds_left': 0}
    return {'state': 'busy', 'seconds_left': int(round(min(cap_s(), _planned_free_at(r) - t)))}


def _row(c):
    return c.execute('SELECT * FROM line WHERE id = 1').fetchone()


def _describe(r, t):
    """One line for the log: what the line was doing before an end or a reset."""
    if not _held(r, t): return 'free'
    if r['state'] == 'reset': return f'reset buffer, {_free_at(r) - t:.0f} s left'
    alive = f'{t - r["t_touch"]:.0f} s ago' if r['t_touch'] else 'none'
    return (f'busy: room {r["room"]!r} call {r["call_id"] or "-"}, started {t - r["t_start"]:.0f} s ago, '
            f'{_status(r, t)["seconds_left"]} s left, keepalive {alive}')


def _log(c, t, event, room='', call_id='', reason='', before=''):
    c.execute('INSERT INTO line_log (t, event, room, call_id, reason, before) VALUES (?, ?, ?, ?, ?, ?)', (t, event, room, call_id, reason, before))
    print(f'[line] {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))} {event} {room!r}' + (f' ({reason})' if reason else '')
          + (f'; before: {before}' if before else ''), flush=True)


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
        if _held(r, t) and r['state'] == 'busy':                                 # a repeat start keeps the clock
            c.execute('UPDATE line SET t_touch = ? WHERE id = 1', (t,)); c.execute('COMMIT'); return True, None
        c.execute('UPDATE line SET state = "busy", room = ?, call_id = ?, t_start = ?, t_end = 0, t_touch = ? WHERE id = 1', (room, call_id or '', t, t))
        _log(c, t, 'start', room, call_id or '')
        c.execute('COMMIT'); return True, None
    finally: c.close()


def touch(room):
    """Keepalive: the session in `room` is still running. False when `room` does not hold the line."""
    if not enabled(): return False
    c = _tx(); t = now()
    try:
        r = _row(c)
        if r['room'] != room or r['state'] != 'busy' or not _held(r, t): c.execute('ROLLBACK'); return False
        c.execute('UPDATE line SET t_touch = ? WHERE id = 1', (t,)); c.execute('COMMIT'); return True
    finally: c.close()


def end(room, reason='ended'):
    """The session in `room` ended: busy for the reset buffer, then open. An end for another room changes nothing."""
    if not enabled(): return False
    c = _tx(); t = now()
    try:
        r = _row(c)
        if r['room'] != room or r['state'] != 'busy' or not _held(r, t): c.execute('ROLLBACK'); return False
        c.execute('UPDATE line SET state = "reset", t_end = ? WHERE id = 1', (t,))
        _log(c, t, 'end', room, r['call_id'], reason or 'ended', _describe(r, t))
        c.execute('COMMIT'); return True
    finally: c.close()


def reset(reason='admin reset'):
    """Admin: the line is free now (no reset buffer). The session holding it, if any, is logged as ended with `reason`.
    Returns the state before the reset (one line)."""
    c = _tx(); t = now()
    try:
        r = _row(c); before = _describe(r, t)
        c.execute('UPDATE line SET state = "free", t_end = ? WHERE id = 1', (t,))
        _log(c, t, 'reset', r['room'] if _held(r, t) else '', r['call_id'] if _held(r, t) else '', reason, before)
        c.execute('COMMIT'); return before
    finally: c.close()


def info(online=None):
    """Admin view of the line: the public status plus the session holding it."""
    st = status(online)
    with users._db() as c: r = _row(c)
    t = now(); held = _held(r, t)
    return dict(st, held=held, phase=(r['state'] if held else 'free'), room=r['room'] if held else '', call_id=r['call_id'] if held else '',
                t_start=r['t_start'] if held and r['state'] == 'busy' else None, started_s_ago=round(t - r['t_start']) if held and r['state'] == 'busy' else None,
                keepalive_s_ago=round(t - r['t_touch']) if held and r['state'] == 'busy' and r['t_touch'] else None, raw_seconds_left=_status(r, t)['seconds_left'])


def log(limit=20):
    with users._db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM line_log ORDER BY id DESC LIMIT ?', (int(limit),)).fetchall()]
