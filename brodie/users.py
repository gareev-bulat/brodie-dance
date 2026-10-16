"""Accounts for Brodie Dance: a sqlite table of users, PBKDF2 password hashes, signed session cookies.

    users.init()                              # creates data/brodie.db (BRODIE_DB overrides the path)
    u = users.create(name, email, password)   # ValueError('exists' | 'invalid: ...') on bad input
    u = users.verify(email, password)         # None when wrong
    token = users.sign(u['id']); uid = users.check(token)   # the session cookie value, valid SESSION_DAYS

The signing secret is SESSION_SECRET in the environment, else a random one written to data/session_secret on first run
(on a hosting service set SESSION_SECRET so sessions survive a redeploy). Passwords are never stored or logged.
"""
import base64, hashlib, hmac, os, re, secrets, sqlite3, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get('BRODIE_DB', os.path.join(ROOT, 'data', 'brodie.db'))
SESSION_DAYS = int(os.environ.get('SESSION_DAYS', '30'))
EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_SECRET = None


def _secret():
    global _SECRET
    if _SECRET: return _SECRET
    s = os.environ.get('SESSION_SECRET')
    if not s:
        p = os.path.join(os.path.dirname(DB), 'session_secret')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if not os.path.exists(p):
            with open(p, 'w') as f: f.write(secrets.token_hex(32))
        with open(p) as f: s = f.read().strip()
    _SECRET = s.encode()
    return _SECRET


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    return c


def init():
    with _db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, '
                  'pw_hash TEXT NOT NULL, salt TEXT NOT NULL, created REAL NOT NULL, calls INTEGER NOT NULL DEFAULT 0, last_call REAL)')
        c.execute('CREATE TABLE IF NOT EXISTS waitlist (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL DEFAULT "", '
                  'source TEXT NOT NULL DEFAULT "", created REAL NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS call_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, t REAL NOT NULL, dance TEXT NOT NULL DEFAULT "")')


# ---- waitlist (launch day: emails first, accounts when the lesson opens) ----
def waitlist_add(email, name='', source=''):
    """Returns (position, created_now). ValueError on a bad email. A repeat email keeps its original position."""
    email = (email or '').strip().lower()[:120]
    if not EMAIL.match(email): raise ValueError('that does not look like an email address')
    with _db() as c:
        try:
            c.execute('INSERT INTO waitlist (email, name, source, created) VALUES (?, ?, ?, ?)', (email, (name or '').strip()[:40], (source or '')[:80], time.time()))
            created = True
        except sqlite3.IntegrityError:
            created = False
        pos = c.execute('SELECT COUNT(*) FROM waitlist WHERE id <= (SELECT id FROM waitlist WHERE email = ?)', (email,)).fetchone()[0]
    return pos, created


def waitlist_count():
    with _db() as c:
        return c.execute('SELECT COUNT(*) FROM waitlist').fetchone()[0]


def waitlist_all():
    with _db() as c:
        return [dict(r) for r in c.execute('SELECT id, email, name, source, created FROM waitlist ORDER BY id').fetchall()]


def _hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200_000).hex()


def _row(r):
    return None if r is None else {'id': r['id'], 'name': r['name'], 'email': r['email'], 'created': r['created'], 'calls': r['calls']}


def create(name, email, password):
    name, email = (name or '').strip()[:40], (email or '').strip().lower()[:120]
    if not name: raise ValueError('invalid: name is required')
    if not EMAIL.match(email): raise ValueError('invalid: that does not look like an email address')
    if not password or len(password) < 6: raise ValueError('invalid: password must be at least 6 characters')
    salt = secrets.token_hex(16)
    with _db() as c:
        try:
            cur = c.execute('INSERT INTO users (name, email, pw_hash, salt, created) VALUES (?, ?, ?, ?, ?)',
                            (name, email, _hash(password, salt), salt, time.time()))
        except sqlite3.IntegrityError:
            raise ValueError('exists')
        return _row(c.execute('SELECT * FROM users WHERE id = ?', (cur.lastrowid,)).fetchone())


def by_email(email):
    """NOTE: email-only sign-in, no check that the visitor owns the address: anyone who types an email is signed into that account.
    Fine while an account holds only name, email and call count. Don't store anything personal on accounts (scores tied to a
    person, phone numbers, recordings) unless sign-in gets a real check (an emailed link or code).
    The landing page's one field: the account for this email, created on first use with no password (name = the part before @;
    pw_hash empty, so no password ever matches it). Returns (user, created). ValueError on a bad email."""
    email = (email or '').strip().lower()[:120]
    if not EMAIL.match(email): raise ValueError('invalid: that does not look like an email address')
    with _db() as c:
        r = c.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        if r is not None: return _row(r), False
        cur = c.execute('INSERT INTO users (name, email, pw_hash, salt, created) VALUES (?, ?, "", "", ?)', (email.split('@')[0][:40], email, time.time()))
        return _row(c.execute('SELECT * FROM users WHERE id = ?', (cur.lastrowid,)).fetchone()), True


def guest():
    """A visitor without an email: the landing page's pop-up takes the line with one tap. The account gets a placeholder
    email with no @ (guest-<random>), which no email field accepts, so nobody can sign into it by typing an address; the
    signed session cookie is the only way back to it. Created only when a visitor actually takes a free line."""
    with _db() as c:
        cur = c.execute('INSERT INTO users (name, email, pw_hash, salt, created) VALUES ("guest", ?, "", "", ?)', ('guest-' + secrets.token_hex(8), time.time()))
        return _row(c.execute('SELECT * FROM users WHERE id = ?', (cur.lastrowid,)).fetchone())


def is_guest(u):
    return bool(u) and u['email'].startswith('guest-') and '@' not in u['email']


def verify(email, password):
    email = (email or '').strip().lower()
    with _db() as c:
        r = c.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    if r is None or not password: return None
    return _row(r) if hmac.compare_digest(_hash(password, r['salt']), r['pw_hash']) else None


def get(uid):
    if not uid: return None
    with _db() as c:
        return _row(c.execute('SELECT * FROM users WHERE id = ?', (int(uid),)).fetchone())


def bump_calls(uid, dance=''):
    """One call started: the per-user counter plus a row in call_log (what the stats page counts)."""
    now = time.time()
    with _db() as c:
        c.execute('UPDATE users SET calls = calls + 1, last_call = ? WHERE id = ?', (now, int(uid)))
        c.execute('INSERT INTO call_log (user_id, t, dance) VALUES (?, ?, ?)', (int(uid), now, (dance or '')[:40]))


# ---- stats for the team: GET /api/stats?key=<ADMIN_KEY> ----
def stats(tz='America/New_York', exclude=()):
    """Totals, a per-day table and retention. Days are calendar days in tz. exclude: team emails left out of every number."""
    import datetime
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz)
    except Exception:   # no tz database on this machine (some slim images, Windows without tzdata): New York daylight time
        zone, tz = datetime.timezone(datetime.timedelta(hours=-4)), 'UTC-4'
    day = lambda t: datetime.datetime.fromtimestamp(t, zone).date().isoformat()
    ex = {e.strip().lower() for e in exclude if e.strip()}
    with _db() as c:
        us = [dict(r) for r in c.execute('SELECT id, email, created FROM users').fetchall()]
        wl = [dict(r) for r in c.execute('SELECT email, created FROM waitlist').fetchall()]
        calls = [dict(r) for r in c.execute('SELECT user_id, t FROM call_log ORDER BY t').fetchall()]
    team = {u['id'] for u in us if u['email'] in ex}
    us = [u for u in us if u['id'] not in team]
    wl = [w for w in wl if w['email'] not in ex]
    calls = [k for k in calls if k['user_id'] not in team]
    signed_up = {u['id']: day(u['created']) for u in us}
    days_by_user = {}
    for k in calls: days_by_user.setdefault(k['user_id'], set()).add(day(k['t']))
    per = {}
    def row(d): return per.setdefault(d, {'day': d, 'waitlist': 0, 'signups': 0, 'calls': 0, 'callers': set(), 'new_callers': 0})
    for w in wl: row(day(w['created']))['waitlist'] += 1
    for u in us: row(day(u['created']))['signups'] += 1
    for k in calls:
        r = row(day(k['t'])); r['calls'] += 1; r['callers'].add(k['user_id'])
    for uid, ds in days_by_user.items(): row(min(ds))['new_callers'] += 1
    table = [dict(r, callers=len(r['callers'])) for _, r in sorted(per.items(), reverse=True)]
    wl_emails = {w['email'] for w in wl}
    callers = set(days_by_user)
    came_back = {u for u, ds in days_by_user.items() if len(ds) >= 2}
    def back_within(n):
        """Of the people whose first call was at least n days ago: how many called again within n days after it."""
        today = datetime.datetime.now(zone).date()
        elig = hit = 0
        for ds in days_by_user.values():
            first = datetime.date.fromisoformat(min(ds))
            if (today - first).days < n: continue
            elig += 1
            hit += any(0 < (datetime.date.fromisoformat(d) - first).days <= n for d in ds)
        return {'eligible': elig, 'returned': hit, 'pct': round(100 * hit / elig) if elig else None}
    return {
        'totals': {'waitlist': len(wl), 'signups': len(us), 'callers': len(callers), 'calls': len(calls),
                   'came_back': len(came_back), 'came_back_pct': round(100 * len(came_back) / len(callers)) if callers else None,
                   'waitlist_signed_up': sum(1 for u in us if u['email'] in wl_emails),
                   'signed_up_never_called': sum(1 for u in signed_up if u not in callers)},
        'retention': {'d1': back_within(1), 'd3': back_within(3), 'd7': back_within(7)},
        'days': table, 'tz': tz, 'excluded_team_accounts': len(team),
    }


def count():
    with _db() as c:
        return c.execute('SELECT COUNT(*) FROM users').fetchone()[0]


def sign(uid):
    """A session token: uid.expiry.signature (urlsafe base64 of an HMAC-SHA256)."""
    exp = int(time.time()) + SESSION_DAYS * 86400
    msg = f'{int(uid)}.{exp}'.encode()
    sig = base64.urlsafe_b64encode(hmac.new(_secret(), msg, hashlib.sha256).digest()).decode().rstrip('=')
    return f'{msg.decode()}.{sig}'


def check(token):
    """The user id of a valid, unexpired token, else None."""
    try:
        uid, exp, sig = token.split('.')
        msg = f'{int(uid)}.{int(exp)}'.encode()
        good = base64.urlsafe_b64encode(hmac.new(_secret(), msg, hashlib.sha256).digest()).decode().rstrip('=')
        if not hmac.compare_digest(good, sig) or int(exp) < time.time(): return None
        return int(uid)
    except (AttributeError, ValueError):
        return None
