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


def bump_calls(uid):
    with _db() as c:
        c.execute('UPDATE users SET calls = calls + 1, last_call = ? WHERE id = ?', (time.time(), int(uid)))


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
