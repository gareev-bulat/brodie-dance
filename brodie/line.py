"""Brodie's one line, kept by the website: one FaceTime session at a time. Brodie's worker is not involved (it only sends
its heartbeat, which decides online / offline).

    line.claim(uid, dance='')            # a signed-in visitor clicked "FaceTime Brodie": (True, view) or (False, view) when busy
    line.done(uid)                       # that visitor clicked "I'm done dancing": the line is free for everyone
    line.view(uid, online)               # what a visitor sees: open | busy (seconds_left) | mine (their own session, + dance) | offline
    line.call_started(call_id)           # Brodie's worker: (source, dance); dance is the holder's pick, '' unless a visitor picked
    line.admin_busy(minutes) / admin_free() / admin_extend(minutes)   # the stats page's controls (ADMIN_KEY checked by the server)
    line.info(online), line.log()        # the stats page: state, who started the session, time left; the recent events

One row in the same sqlite file as the accounts (users.DB), so a session survives a server restart. A session runs from
t_start to t_until and frees on its own at t_until: a click holds the line LINE_CLAIM_S (2 min) for the call to start; once
Brodie's worker reports the call (call_started), up to LINE_CAP_S (15 min); after the hang-up (call_ended), LINE_COOLDOWN_S. source is 'visitor' (uid
set) or 'admin'. Every claim, done, expiry and admin action goes into line_log (and stdout) with the time.
The claim is one sqlite write transaction (BEGIN IMMEDIATE): two visitors clicking at once, the first gets the line.
dance: the lesson the holder picked on the site (a lesson id the server checked against the worker's list, '' = none). Only the
holder sees it; the worker gets it back in its call start reply; it is cleared with the session (kept through the 15 s cooldown,
so a redial keeps the pick).
"""
import os, re, time
from brodie import users

now = time.time                      # tests replace this
DANCE_ID = re.compile(r'[a-z0-9_]{1,40}')


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
        lcols = {r[1] for r in c.execute('PRAGMA table_info(line)')}
        if 'on_call' not in lcols:
            c.execute('ALTER TABLE line ADD COLUMN on_call INTEGER NOT NULL DEFAULT 0')   # 1 while a FaceTime call is on
        if 'dead_streak' not in lcols:     # FaceTime calls in a row that never reached Brodie (facetime/line_watch.py)
            c.execute('ALTER TABLE line ADD COLUMN dead_streak INTEGER NOT NULL DEFAULT 0')
        if 'down_since' not in lcols:      # > 0: the line is down (Chert answers but the call never reaches Brodie); the site says so
            c.execute('ALTER TABLE line ADD COLUMN down_since REAL NOT NULL DEFAULT 0')
            c.execute('ALTER TABLE line ADD COLUMN down_by TEXT NOT NULL DEFAULT ""')
        if 'dance' not in lcols:           # the lesson the holder picked on the site ('' = none, Brodie's default or the caller's voice)
            c.execute('ALTER TABLE line ADD COLUMN dance TEXT NOT NULL DEFAULT ""')
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
        c.execute('UPDATE line SET source = "", uid = NULL, t_start = 0, until = 0, on_call = 0, dance = "" WHERE id = 1')


def _tx():
    c = users._db(); c.isolation_level = None; c.execute('BEGIN IMMEDIATE')   # one writer at a time: two clicks can't both win
    return c


def _state(r, t, uid=None):
    if not _busy(r, t): return {'state': 'open', 'seconds_left': 0}
    mine = r['source'] == 'visitor' and uid is not None and r['uid'] == uid
    st = {'state': 'mine' if mine else 'busy', 'seconds_left': _left(r, t)}
    if mine: st['on_call'] = bool(r['on_call']); st['dance'] = r['dance']   # on_call False: clicked, no call yet; dance: their pick (theirs only)
    return st


def view(uid=None, online=None):
    """What a visitor sees. online: the worker heartbeat (False = offline wins over everything; None = unknown, ignored).
    A line marked down (calls not getting through) is offline too, with reason 'down' so the page can say so."""
    _maybe_retry()
    with users._db() as c: r = _row(c)
    if r['down_since'] > 0: return {'state': 'offline', 'reason': 'down', 'seconds_left': 0, 'cap_s': cap_s()}
    if online is False: return {'state': 'offline', 'reason': 'worker', 'seconds_left': 0, 'cap_s': cap_s()}
    return dict(_state(r, now(), uid), cap_s=cap_s())


# ---- the line is down: Chert answers FaceTime calls but they never reach Brodie (Oct 15 morning, Oct 18 night) ----
def down_after(): return max(1, int(os.environ.get('LINE_DOWN_AFTER', '2')))
def retry_s(): return max(0.0, float(os.environ.get('LINE_DOWN_RETRY_S', '1200')))


def _maybe_retry():
    """An automatic down mark older than LINE_DOWN_RETRY_S (20 min) reopens the line on probation: nobody can call while it is
    down, so nothing would ever show Chert is back. On probation one more dead call takes it down again (not two); a call
    that reaches Brodie clears it. A down mark from the team (Mark line down) waits for Mark line up. 0 turns retries off."""
    with users._db() as c: r = _row(c)
    if not (r['down_since'] > 0 and r['down_by'] == 'auto' and retry_s() > 0 and now() - r['down_since'] >= retry_s()): return
    c = _tx(); t = now()
    try:
        r = _row(c)
        if r['down_since'] > 0 and r['down_by'] == 'auto' and t - r['down_since'] >= retry_s():
            c.execute('UPDATE line SET down_since = 0, down_by = "", dead_streak = ? WHERE id = 1', (down_after() - 1,))
            _log(c, t, 'retry', 'watch', f'down {int(round(t - r["down_since"]))} s: the line reopens on probation (one more dead call takes it down again)')
        c.execute('COMMIT')
    finally: c.close()


def is_down():
    _maybe_retry()
    with users._db() as c: return _row(c)['down_since'] > 0


def call_dead(detail=''):
    """facetime/line_watch.py: a FaceTime call was answered but never reached Brodie. After LINE_DOWN_AFTER (2) in a row the
    line is marked down: the site shows it offline ("having trouble") and the stats page shows it in red. The next call that
    reaches Brodie (call_started) marks it up again. Returns {dead_streak, down}."""
    c = _tx(); t = now()
    try:
        r = _row(c); streak = r['dead_streak'] + 1
        c.execute('UPDATE line SET dead_streak = ? WHERE id = 1', (streak,))
        _log(c, t, 'dead call', 'watch', f'answered but never reached Brodie ({streak} in a row){": " + detail if detail else ""}')
        if streak >= down_after() and not r['down_since'] > 0:
            c.execute('UPDATE line SET down_since = ?, down_by = "auto" WHERE id = 1', (t,))
            _log(c, t, 'down', 'watch', f'{streak} calls in a row never reached Brodie: the site shows Brodie offline until a call gets through')
        c.execute('COMMIT'); r = _row(c)
        return {'dead_streak': streak, 'down': r['down_since'] > 0}
    finally: c.close()


def _mark_up(c, t, actor, detail):
    r = _row(c)
    if r['down_since'] > 0: _log(c, t, 'up', actor, detail + f' (down {int(round(t - r["down_since"]))} s)')
    c.execute('UPDATE line SET dead_streak = 0, down_since = 0, down_by = "" WHERE id = 1')


def admin_down():
    c = _tx(); t = now()
    try:
        c.execute('UPDATE line SET down_since = ?, down_by = "admin" WHERE id = 1', (t,))
        _log(c, t, 'down', 'admin', 'mark down: the site shows Brodie offline'); c.execute('COMMIT')
    finally: c.close()


def admin_up():
    c = _tx(); t = now()
    try:
        _mark_up(c, t, 'admin', 'mark up'); c.execute('COMMIT')
    finally: c.close()


def claim(uid, dance=''):
    """The visitor clicked "FaceTime Brodie". Free: the line is theirs for the claim time, with their pick (dance, checked by the
    server). Their own session: unchanged, except a new pick before the call starts (an empty one keeps the stored pick: an
    old cached page sends none). Someone else's: (False, busy view)."""
    dance = str(dance or ''); dance = dance if DANCE_ID.fullmatch(dance) else ''
    c = _tx(); t = now()
    try:
        r = _row(c); _expire(c, r, t); r = _row(c)
        if _busy(r, t):
            st = _state(r, t, uid)
            if st['state'] == 'mine' and not r['on_call'] and dance and dance != r['dance']:   # a new pick while they haven't called yet
                c.execute('UPDATE line SET dance = ? WHERE id = 1', (dance,)); _log(c, t, 'pick', _who(r), f'song {dance}')
                r = _row(c); st = _state(r, t, uid)
            c.execute('COMMIT'); return st['state'] == 'mine', dict(st, cap_s=cap_s())
        c.execute('UPDATE line SET source = "visitor", uid = ?, t_start = ?, until = ?, on_call = 0, dance = ? WHERE id = 1', (uid, t, t + claim_s(), dance))
        r = _row(c); _log(c, t, 'busy', _who(r), f'clicked FaceTime Brodie{f" ({dance})" if dance else ""}: {claim_s():g} s to start the call')
        c.execute('COMMIT'); return True, dict(_state(r, t, uid), cap_s=cap_s())
    finally: c.close()


def done(uid):
    """The holder clicked "I'm done dancing": free for everyone. Anyone else: nothing changes (False)."""
    c = _tx(); t = now()
    try:
        r = _row(c)
        if not (_busy(r, t) and r['source'] == 'visitor' and r['uid'] == uid): c.execute('ROLLBACK'); return False
        _log(c, t, 'free', _who(r), f"I'm done dancing, {_left(r, t)} s were left")
        c.execute('UPDATE line SET source = "", uid = NULL, t_start = 0, until = 0, on_call = 0, dance = "" WHERE id = 1')
        c.execute('COMMIT'); return True
    finally: c.close()


def admin_busy(minutes=15):
    """Mark busy: a session from now for `minutes` (1 to 15), replacing any session."""
    m = max(1.0, min(cap_s() / 60, float(minutes)))
    c = _tx(); t = now()
    try:
        r = _row(c); _expire(c, r, t); r = _row(c); before = f'{_who(r)} with {_left(r, t)} s left' if _busy(r, t) else 'free'
        c.execute('UPDATE line SET source = "admin", uid = NULL, t_start = ?, until = ?, on_call = 0, dance = "" WHERE id = 1', (t, t + m * 60))
        _log(c, t, 'busy', 'admin', f'mark busy {m:g} min (before: {before})')
        c.execute('COMMIT'); return m
    finally: c.close()


def admin_free():
    """Mark free: the session ends now, whoever started it."""
    c = _tx(); t = now()
    try:
        r = _row(c); _expire(c, r, t); r = _row(c); before = f'{_who(r)} with {_left(r, t)} s left' if _busy(r, t) else 'free'
        c.execute('UPDATE line SET source = "", uid = NULL, t_start = 0, until = 0, on_call = 0, dance = "" WHERE id = 1')
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


def _ft_start(c, t, call_id, r, source):
    """The stats' FaceTime call record (users.ft_calls): one row per call id (a repeated start report is ignored)."""
    if call_id and c.execute('SELECT 1 FROM ft_calls WHERE call_id = ?', (call_id,)).fetchone(): return
    uid = r['uid'] if source == 'site' else None
    c.execute('INSERT INTO ft_calls (call_id, uid, source, t_start) VALUES (?, ?, ?, ?)', (call_id, uid, source, t))


def _ft_end(c, t, call_id):
    row = c.execute("SELECT id FROM ft_calls WHERE t_end IS NULL AND (call_id = ? OR ? = '') ORDER BY id DESC LIMIT 1", (call_id, call_id)).fetchone()
    if row: c.execute('UPDATE ft_calls SET t_end = ? WHERE id = ?', (t, row['id']))


def call_started(call_id=''):
    """Brodie's worker: a FaceTime caller is on. A free line (or one cooling down) becomes busy for the cap, so a caller who
    dialled without clicking on the site still shows as busy; a session someone already holds is left as it is (their call).
    Returns (source holding the line afterwards, dance): dance is the visitor's pick on a visitor's line, else ''."""
    c = _tx(); t = now()
    try:
        _mark_up(c, t, 'watch', 'a call reached Brodie')   # a call got through: the line works (clears a down mark and the streak)
        r = _row(c); _expire(c, r, t); r = _row(c)
        if _busy(r, t) and r['source'] == 'visitor':    # the visitor who clicked: their call is on, the session now runs up to the cap
            _ft_start(c, t, call_id, r, 'site')
            c.execute('UPDATE line SET until = ?, on_call = 1 WHERE id = 1', (t + cap_s(), ))
            _log(c, t, 'call', _who(r), f'FaceTime call started {call_id[:8]}: up to {cap_s() / 60:g} min'.replace('  ', ' ') + (f', lesson {r["dance"]}' if r['dance'] else ''))
            c.execute('COMMIT'); return r['source'], r['dance']
        if _busy(r, t) and r['source'] != 'cooldown':     # admin's Mark busy: left as it is
            _ft_start(c, t, call_id, r, 'admin')
            c.execute('UPDATE line SET on_call = 1 WHERE id = 1')
            _log(c, t, 'call', _who(r), f'FaceTime call started {call_id[:8]}'.rstrip()); c.execute('COMMIT'); return r['source'], ''
        _ft_start(c, t, call_id, r, 'direct')
        c.execute('UPDATE line SET source = "facetime", uid = NULL, t_start = ?, until = ?, on_call = 1, dance = "" WHERE id = 1', (t, t + cap_s()))
        _log(c, t, 'busy', 'facetime', f'FaceTime call started {call_id[:8]} (nobody clicked on the site)'.replace('  ', ' '))
        c.execute('COMMIT'); return 'facetime', ''
    finally: c.close()


def call_ended(call_id=''):
    """Brodie's worker: the FaceTime caller hung up. The line frees after LINE_COOLDOWN_S (15 s: Chert's line needs ~10 s
    before it takes the next call), not at the end of the session, so a caller who forgets "I'm done dancing" no longer holds
    the line for the full 15 minutes. A free line is held for the cooldown too (a visitor clicking now couldn't get through).
    An admin's Mark busy is left alone (the team blocking the line on purpose). Repeats are harmless. Returns seconds until free."""
    c = _tx(); t = now(); cd = cooldown_s()
    try:
        _ft_end(c, t, call_id)
        r = _row(c); _expire(c, r, t); r = _row(c)
        if _busy(r, t) and r['source'] == 'admin':
            c.execute('UPDATE line SET on_call = 0 WHERE id = 1')
            _log(c, t, 'call ended', 'admin', f'FaceTime call ended {call_id[:8]}; the admin session stays'.replace('  ', ' '))
            c.execute('COMMIT'); return _left(r, t)
        if _busy(r, t):                                   # a visitor's pick stays: a redial within the cooldown keeps it
            until = min(r['until'], t + cd)
            c.execute('UPDATE line SET until = ?, on_call = 0 WHERE id = 1', (until,))
            _log(c, t, 'call ended', _who(r), f'FaceTime call ended {call_id[:8]}: free in {int(round(until - t))} s (was {_left(r, t)} s)'.replace('  ', ' '))
        else:
            until = t + cd
            c.execute('UPDATE line SET source = "cooldown", uid = NULL, t_start = ?, until = ?, on_call = 0, dance = "" WHERE id = 1', (t, until))
            _log(c, t, 'call ended', 'facetime', f'FaceTime call ended {call_id[:8]}: cooling down {int(cd)} s'.replace('  ', ' '))
        c.execute('COMMIT'); return int(round(until - t))
    finally: c.close()


def info(online=None):
    """The stats page: the public state plus who or what started the session."""
    _maybe_retry()
    with users._db() as c: r = _row(c)
    t = now(); busy = _busy(r, t); down = r['down_since'] > 0
    state = 'down' if down else 'offline' if online is False else ('busy' if busy else 'free')
    return {'state': state, 'busy': busy, 'seconds_left': _left(r, t) if busy else 0, 'cap_s': cap_s(),
            'source': r['source'] if busy else '', 'started_by': _who(r) if busy else '',
            'started_s_ago': int(round(t - r['t_start'])) if busy else None, 'on_call': bool(r['on_call']) if busy else False,
            'down': down, 'down_by': r['down_by'] if down else '', 'down_s': int(round(t - r['down_since'])) if down else None,
            'dead_streak': r['dead_streak'], 'down_after': down_after(),
            'retry_in_s': int(round(max(0, retry_s() - (t - r['down_since'])))) if down and r['down_by'] == 'auto' and retry_s() > 0 else None}


def log(limit=20):
    with users._db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM line_log ORDER BY id DESC LIMIT ?', (int(limit),)).fetchall()]
