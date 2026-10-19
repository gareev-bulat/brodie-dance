"""The lessons a caller can pick, their spoken titles, and the matcher for a spoken pick.

TITLES must equal web/index.html TITLES (scripts/test_dance_pick.py checks it). A lesson is pickable when its data/dances/<name>.npz
exists (and its .mp4 unless DANCE_VIDEO=0). DANCE_PICK=0 turns picking off (names() is empty); DANCE_MENU=a,b,c sets the menu order
(the screen numbers). match() reads only the caller's newest turn and never prints or stores it.
"""
import os, re
from starter.dance import reference

TITLES = {'sbbyj': 'Chest Sweep Twist', 'deatonioo': 'Sweep and Shoot', 'bigbowee': 'Palm Sway Wave'}
WORDS = {'sbbyj': {'chest', 'sweep', 'twist'}, 'deatonioo': {'sweep', 'shoot'}, 'bigbowee': {'palm', 'sway', 'wave'}}
STRONG = {'sbbyj': {'chest', 'twist'}, 'deatonioo': {'shoot'}, 'bigbowee': {'palm', 'sway', 'wave'}}   # one of these alone names the dance
KEYS = set().union(*WORDS.values())
ALIAS = {'chess': 'chest', 'waving': 'wave', 'shot': 'shoot', 'shooting': 'shoot', 'swaying': 'sway'}
SUFFIXES = ('ing', 'es', 's', 'y', 'ed')
NEG = {'not', 'dont', 'no', 'never', 'except', 'without', 'hate', 'skip', 'nope'}
FILLER = {'the', 'number', 'option', 'um', 'uh', 'please', 'like'}
NUMBER_RE = re.compile(r'(one|two|three|1|2|3|first|second|third|last)( one)?')
NUM = {'one': 0, '1': 0, 'first': 0, 'two': 1, '2': 1, 'second': 1, 'three': 2, '3': 2, 'third': 2}
MAX_TOKENS = 40
_REFS = {}


def on(): return os.environ.get('DANCE_PICK', '1') != '0'


def order():
    """The menu order: DANCE_MENU (comma separated ids) or TITLES order; unknown ids are dropped."""
    raw = os.environ.get('DANCE_MENU', '') or ','.join(TITLES)
    return list(dict.fromkeys(n.strip() for n in raw.split(',') if n.strip() in TITLES))


def names():
    """The pickable lessons in menu order; [] when DANCE_PICK=0."""
    if not on(): return []
    video = os.environ.get('DANCE_VIDEO', '1') != '0'
    out = []
    for n in order():
        base = os.path.join(reference.DANCES_DIR, n)
        if os.path.exists(base + '.npz') and (not video or os.path.exists(base + '.mp4')): out.append(n)
    return out


def ref(name):
    """The lesson's Reference, loaded once per process (shared by every job: the handlers and the judge only read it). None on error."""
    if name not in _REFS:
        try: _REFS[name] = reference.load(name)
        except Exception as e:
            print(f'[dance] lesson {name if re.fullmatch(r"[a-z0-9_]{1,40}", str(name)) else "?"} not loaded ({type(e).__name__})', flush=True)
            return None
    return _REFS[name]


def title(name): return TITLES.get(name, name)


def spoken(menu):
    """'"A", "B" or "C"' for the prompts."""
    ts = [f'"{title(n)}"' for n in menu]
    return ts[0] if len(ts) == 1 else ', '.join(ts[:-1]) + ' or ' + ts[-1]


def _tokens(text):
    t = str(text or '').lower().replace('&', ' and ').replace("'", '').replace('’', '')
    return re.findall(r'[a-z0-9]+', t)


def _kw(tok):
    tok = ALIAS.get(tok, tok)
    if tok in KEYS: return tok
    for suf in SUFFIXES:
        if tok.endswith(suf) and tok[:-len(suf)] in KEYS: return tok[:-len(suf)]
    return None


def _parse(text):
    """(tokens, keywords found) or None when the turn is empty, too long, or negates a keyword ("not the chest one")."""
    toks = _tokens(text)
    if not toks or len(toks) > MAX_TOKENS: return None
    kws = [_kw(t) for t in toks]
    for i, t in enumerate(toks):
        if t in NEG and any(kws[i + 1:i + 4]): return None
    return toks, {k for k in kws if k}


def match(text, menu, strict=False):
    """The lesson the caller's turn names, or None. Rules, in order: a whole title (two or more whole titles: None, a list or Brodie's
    own menu echoed back); two or more words of one title, strictly more than any other; one strong word in a short turn (not
    strict); a whole-turn number ("two", "the second one", "the last one"). strict (a site pick is in place): no single-word picks."""
    menu = [n for n in menu if n in WORDS]
    p = _parse(text)
    if p is None or not menu: return None
    toks, found = p
    full = [n for n in menu if WORDS[n] <= found]
    if len(full) > 1: return None
    if len(full) == 1: return full[0]
    score = {n: len(found & WORDS[n]) for n in menu}
    best = max(menu, key=lambda n: score[n])
    if score[best] >= 2 and all(score[n] < score[best] for n in menu if n != best): return best
    if not strict and len(toks) <= 6:
        strong = [n for n in menu if found & STRONG[n]]
        if len(strong) == 1: return strong[0]
    rest = ' '.join(t for t in toks if t not in FILLER)
    if NUMBER_RE.fullmatch(rest):
        w = rest.split()[0]
        i = len(menu) - 1 if w == 'last' else NUM[w]
        if i < len(menu): return menu[i]
    return None


def ambiguous(text, menu):
    """The menu lessons a bare shared word names ("sweep": Chest Sweep Twist and Sweep and Shoot), in menu order, or None."""
    menu = [n for n in menu if n in WORDS]
    p = _parse(text)
    if p is None: return None
    _, found = p
    if not found or any(found & STRONG[n] for n in menu): return None
    hit = [n for n in menu if found <= WORDS[n]]
    return hit if len(hit) >= 2 else None
