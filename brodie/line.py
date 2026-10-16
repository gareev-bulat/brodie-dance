"""Brodie's one line, kept by the website: one FaceTime session at a time. Brodie's worker is not involved (it only sends
its heartbeat, which decides online / offline).

    line.claim(uid)                      # a signed-in visitor clicked "FaceTime Brodie": (True, view) or (False, view) when busy
    line.done(uid)                       # that visitor clicked "I'm done dancing": the line is free for everyone
    line.view(uid, online)               # what a visitor sees: open | busy (seconds_left) | mine (their own session) | offline
    line.admin_busy(minutes) / admin_free() / admin_extend(minutes)   # the stats page's controls (ADMIN_KEY checked by the server)
    line.info(online), line.log()        # the stats page: state, who started the session, time left; the recent events

One row in the same sqlite file as the accounts (users.DB), so a session survives a server restart. A session runs from
t_start to t_until and frees on its own at t_until: a click holds the line LINE_CLAIM_S (2 min) for the call to start; once
Brodie's worker reports the call (call_started), up to LINE_CAP_S (15 min); after the hang-up (call_ended), LINE_COOLDOWN_S. source is 'visitor' (uid
set) or 'admin'. Every claim, done, expiry and admin action goes into line_log (and stdout) with the time.
The claim is one sqlite write transaction (BEGIN IMMEDIATE): two visitors clicking at once, the first gets the line.
"""
import os, time
from brodie import users

now = time.time                      # tests replace this


def cap_s(): return float(os.environ.get('LINE_CAP_S', '900'))
def claim_s():
    """How long a click holds the line before a FaceTime call starts (LINE_CLAIM_S, 2 min): someone who taps "FaceTime Brodie"
    and never calls frees the line for the next person. Once the call starts, the session runs up to the cap."""
    return min(cap_s(), float(os.environ.get('LINE_CLAIM_S', '120')))


def init():
    with users._db() as c:
        cols = {r[1] for r in c.execute('PRAGMA table_info(line)')}
        if cols and 'until' not in cols: c.execute('DROP TABLE line')        # the earlier worker-reported layout (never deployed)
        c.execute('CREATE TABLE IF NOT EXISTS line (id INTEGER PRIMARY KEY CHECK (id = 1), source TEXT NOT NULL DEFAULT "", '
                  'uid INTEGER, t_start REAL NOT NULL DEFAULT 0, until REAL NOT NULL DEFAULT 0)')
        c.execute('INSERT OR IGNORE INTO line (id) VALUES (1)')
        if 'on_call' not in {r[1] for r in c.execute('PRAGMA table_info(line)')}:
            c.execute('ALTER TABLE line ADD COLUMN on_call INTEGER NOT NULL DEFAULT 0')   # 1 while a FaceTime call is on
        c.execute('CREATE TABLE IF NOT EXISTS line_log (id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL, event TEXT NOT NULL, '
                  'actor TEXT NOT NULL DEFAULT "", detail TEXT NOT NULL DEFAULT "")')
        cols = {r[1] for r in c.execute('PRAGMA table_info(line_log)')}
        if 'actor' not in cols:                                             # the earlier log layout: start over
            c.execute('DROP TABLE line_log')
            c.execute('CREATE TABLE line_log (id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL, event TEXT NOT NULL, '
                      'actor TEXT NOT NULL DEFAULT "", detail TEXT NOT NULL DEFAULT "")')


def _row(c): return c.execute('SELECT * FROM line WHERE id = 1').fetchone()
def _busy(r, t): return r['until'] > t
def _left(r, t): return int(round(max(0.0, min(cap_s(), r['until'] - t))))


def _who(r):
    if r['source'] == 'visitor':
        u = users.get(r['uid'])
        return f'visitor {u["email"] if u else "#" + str(r["uid"])}'
    if r['source'] == 'facetime': return 'a FaceTime caller (no click on the site)'
    if r['source'] == 'cooldown': return "Chert's cooldown after a call"
    return r['source'] or ''


def _log(c, t, event, actor='', detail=''):
    c.execute('INSERT INTO line_log (t, event, actor, detail) VALUES (?, ?, ?, ?)', (t, event, actor, detail))
    print(f'[line] {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))} {event} by {actor or "-"}' + (f': {detail}' if detail else ''), flush=True)


def _expire(c, r, t):
    """A session past its end: logged once as expired (the row is cleared)."""
    if r['source'] and not _busy(r, t):
        never = r['source'] == 'visitor' and not r['on_call'] and r['until'] - r['t_start'] <= claim_s() + 1
        _log(c, r['until'], 'expired', _who(r), f'clicked but no call within {claim_s():g} s' if never else 'the session ran out its time')
        c.execute('UPDATE line SET source = "", uid = NULL, t_start = 0, until = 0, on_call = 0 WHERE id = 1')


def _tx():
    c = users._db(); c.isolation_level = None; c.execute('BEGIN IMMEDIATE')   # one writer at a time: two clicks can't both win
    return c


def _state(r, t, uid=None):
    if not _busy(r, t): return {'state': 'open', 'seconds_left': 0}
    mine = r['source'] == 'visitor' and uid is not None and r['uid'] == uid
    st = {'state': 'mine' if mine else 'busy', 'seconds_left': _left(r, t)}
    if mine: st['on_call'] = bool(r['on_call'])      # False: they clicked but haven't called yet (claim_s to start the call)
    return st


def view(uid=None, online=None):
    """What a visitor sees. online: the worker heartbeat (False = offline wins over everything; None = unknown, ignored)."""
    if online is False: return {'state': 'offline', 'seconds_left': 0, 'cap_s': cap_s()}
    with users._db() as c: r = _row(c)
    return dict(_state(r, now(), uid), cap_s=cap_s())


def claim(uid):
    """The visitor clicked "FaceTime Brodie". Free: the line is theirs for the cap. Their own session: unchanged.
    Someone else's: (False, busy view)."""
    c = _tx(); t = now()
    try:
        r = _row(c); _expire(c, r, t); r = _row(c)
        if _busy(r, t):
            c.execute('COMMIT'); st = _state(r, t, uid); return st['state'] == 'mine', dict(st, cap_s=cap_s())
        c.execute('UPDATE line SET source = "visitor", uid = ?, t_start = ?, until = ?, on_call = 0 WHERE id = 1', (uid, t, t + claim_s()))
        r = _row(c); _log(c, t, 'busy', _who(r), f'clicked FaceTime Brodie: {claim_s():g} s to start the call')
        c.execute('COMMIT'); return True, dict(_state(r, t, uid), cap_s=cap_s())
    finally: c.close()


def done(uid):
    """The holder clicked "I'm done dancing": free for everyone. Anyone else: nothing changes (False)."""
    c = _tx(); t = now()
    try:
        r = _row(c)
        if not (_busy(r, t) and r['source'] == 'visitor' and r['uid'] == uid): c.execute('ROLLBACK'); return False
        _log(c, t, 'free', _who(r), f"I'm done dancing, {_left(r, t)} s were left")
        c.execute('UPDATE line SET source = "", uid = NULL, t_start = 0, until = 0, on_call = 0 WHERE id = 1')
        c.execute('COMMIT'); return True
    finally: c.close()


def admin_busy(minutes=15):
    """Mark busy: a session from now for `minutes` (1 to 15), replacing any session."""
    m = max(1.0, min(cap_s() / 60, float(minutes)))
    c = _tx(); t = now()
    try:
        r = _row(c); _expire(c, r, t); r = _row(c); before = f'{_who(r)} with {_left(r, t)} s left' if _busy(r, t) else 'free'
        c.execute('UPDATE line SET source = "admin", uid = NULL, t_start = ?, until = ?, on_call = 0 WHERE id = 1', (t, t + m * 60))
        _log(c, t, 'busy', 'admin', f'mark busy {m:g} min (before: {before})')
        c.execute('COMMIT'); return m
    finally: c.close()


def admin_free():
    """Mark free: the session ends now, whoever started it."""
    c = _tx(); t = now()
    try:
        r = _row(c); _expire(c, r, t); r = _row(c); before = f'{_who(r)} with {_left(r, t)} s left' if _busy(r, t) else 'free'
        c.execute('UPDATE line SET source = "", uid = NULL, t_start = 0, until = 0, on_call = 0 WHERE id = 1')
        _log(c, t, 'free', 'admin', f'mark free (before: {before})')
        c.execute('COMMIT'); return before
    finally: c.close()


def admin_extend(minutes=5):
    """Add time to the running session, never past the cap from now. False when the line is free."""
    c = _tx(); t = now()
    try:
        r = _row(c); _expire(c, r, t); r = _row(c)
        if not _busy(r, t): c.execute('ROLLBACK'); return False
        until = min(r['until'] + float(minutes) * 60, t + cap_s())
        c.execute('UPDATE line SET until = ? WHERE id = 1', (until,))
        _log(c, t, 'extend', 'admin', f'add {minutes:g} min: {_left(r, t)} s -> {int(round(until - t))} s left ({_who(r)})')
        c.execute('COMMIT'); return True
    finally: c.close()


def cooldown_s(): return float(os.environ.get('LINE_COOLDOWN_S', '15'))


def call_started(call_id=''):
    """Brodie's worker: a FaceTime caller is on. A free line (or one cooling down) becomes busy for the cap, so a caller who
    dialled without clicking on the site still shows as busy; a session someone already holds is left as it is (their call).
    Returns the source holding the line afterwards."""
    c = _tx(); t = now()
    try:
        r = _row(c); _expire(c, r, t); r = _row(c)
        if _busy(r, t) and r['source'] == 'visitor':    # the visitor who clicked: their call is on, the session now runs up to the cap
            c.execute('UPDATE line SET until = ?, on_call = 1 WHERE id = 1', (t + cap_s(), ))
            _log(c, t, 'call', _who(r), f'FaceTime call started {call_id[:8]}: up to {cap_s() / 60:g} min'.replace('  ', ' '))
            c.execute('COMMIT'); return r['source']
        if _busy(r, t) and r['source'] != 'cooldown':     # admin's Mark busy: left as it is
            c.execute('UPDATE line SET on_call = 1 WHERE id = 1')
            _log(c, t, 'call', _who(r), f'FaceTime call started {call_id[:8]}'.rstrip()); c.execute('COMMIT'); return r['source']
        c.execute('UPDATE line SET source = "facetime", uid = NULL, t_start = ?, until = ?, on_call = 1 WHERE id = 1', (t, t + cap_s()))
        _log(c, t, 'busy', 'facetime', f'FaceTime call started {call_id[:8]} (nobody clicked on the site)'.replace('  ', ' '))
        c.execute('COMMIT'); return 'facetime'
    finally: c.close()


def call_ended(call_id=''):
    """Brodie's worker: the FaceTime caller hung up. The line frees after LINE_COOLDOWN_S (15 s: Chert's line needs ~10 s
    before it takes the next call), not at the end of the session, so a caller who forgets "I'm done dancing" no longer holds
    the line for the full 15 minutes. A free line is held for the cooldown too (a visitor clicking now couldn't get through).
    An admin's Mark busy is left alone (the team blocking the line on purpose). Repeats are harmless. Returns seconds until free."""
    c = _tx(); t = now(); cd = cooldown_s()
    try:
        r = _row(c); _expire(c, r, t); r = _row(c)
        if _busy(r, t) and r['source'] == 'admin':
            c.execute('UPDATE line SET on_call = 0 WHERE id = 1')
            _log(c, t, 'call ended', 'admin', f'FaceTime call ended {call_id[:8]}; the admin session stays'.replace('  ', ' '))
            c.execute('COMMIT'); return _left(r, t)
        if _busy(r, t):
            until = min(r['until'], t + cd)
            c.execute('UPDATE line SET until = ?, on_call = 0 WHERE id = 1', (until,))
            _log(c, t, 'call ended', _who(r), f'FaceTime call ended {call_id[:8]}: free in {int(round(until - t))} s (was {_left(r, t)} s)'.replace('  ', ' '))
        else:
            until = t + cd
            c.execute('UPDATE line SET source = "cooldown", uid = NULL, t_start = ?, until = ?, on_call = 0 WHERE id = 1', (t, until))
            _log(c, t, 'call ended', 'facetime', f'FaceTime call ended {call_id[:8]}: cooling down {int(cd)} s'.replace('  ', ' '))
        c.execute('COMMIT'); return int(round(until - t))
    finally: c.close()


def info(online=None):
    """The stats page: the public state plus who or what started the session."""
    with users._db() as c: r = _row(c)
    t = now(); busy = _busy(r, t)
    state = 'offline' if online is False else ('busy' if busy else 'free')
    return {'state': state, 'busy': busy, 'seconds_left': _left(r, t) if busy else 0, 'cap_s': cap_s(),
            'source': r['source'] if busy else '', 'started_by': _who(r) if busy else '',
            'started_s_ago': int(round(t - r['t_start'])) if busy else None, 'on_call': bool(r['on_call']) if busy else False}


def log(limit=20):
    with users._db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM line_log ORDER BY id DESC LIMIT ?', (int(limit),)).fetchall()]
