"""The web app's backend: serves web/ and mints LiveKit tokens for a fresh room per call ("web-<character>-<id>").
scripts/run_verity.sh <character> starts it next to Brodie's worker, which runs one character, named in VERITY_CHARACTER:
the app can only call that one. By hand:
  VERITY_CHARACTER=verity python -m verity.server [--port 8765] [--host 127.0.0.1]   # LIVEKIT_URL/API_KEY/API_SECRET, AGENT_NAME in env
Keep the default localhost bind: /api/token has no auth, so anyone who can reach the server can start calls (Gemini minutes).
With AGENT_NAME set the token also dispatches that agent into the room (so the same worker serves Chert and the web).
"""
import argparse, datetime, json, os, secrets
from aiohttp import web
from livekit import api
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration
from verity import memory
from verity.variants import VARIANTS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')
CALLABLE = [k for k, v in VARIANTS.items() if not v.get('secret')]
LIVE = os.environ.get('VERITY_CHARACTER', '')           # the character the worker is running ('' = any, e.g. the legacy verity.agent)


def _voices():
    """Gemini Live voice names, when the agents plugin is installed (a hosted web server can run without it)."""
    try:
        import typing
        from livekit.plugins.google.realtime.api_proto import Voice
        return list(typing.get_args(Voice))
    except Exception:
        return []


VOICES = _voices()


def parse_settings(raw):
    """verity.settings from the caller's attributes, validated (it comes from the browser). Same rules as verity/agent.py."""
    try: s = json.loads(raw or '{}')
    except ValueError: s = {}
    if not isinstance(s, dict): s = {}
    voice = s.get('voice') if s.get('voice') in VOICES else ''
    return {'asmr': 'mommy' if s.get('asmr') == 'mommy' else 'off', 'voice': voice,
            'strikes': s.get('strikes', True) is not False, 'memory': s.get('memory', True) is not False}


def mint_call(key, room=None, settings=None, uid=''):
    """Token for a caller in a fresh room "web-<key>-<id>" (or the given room), dispatching AGENT_NAME into it when set.
    settings (validated) and uid travel as the caller's participant attributes; the agent reads them when it binds."""
    room, identity = room or f'web-{key}-{secrets.token_hex(3)}', f'caller-{secrets.token_hex(3)}'
    t = (api.AccessToken(os.environ['LIVEKIT_API_KEY'], os.environ['LIVEKIT_API_SECRET']).with_identity(identity).with_name('You')
         .with_ttl(datetime.timedelta(hours=1))
         .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True, can_publish_data=True,
                                      can_update_own_metadata=True))
         .with_attributes({'verity.settings': json.dumps(parse_settings(json.dumps(settings or {}))),
                           'verity.uid': uid if memory.UID.fullmatch(uid or '') else ''}))
    if os.environ.get('AGENT_NAME'):
        t = t.with_room_config(RoomConfiguration(agents=[RoomAgentDispatch(agent_name=os.environ['AGENT_NAME'])]))
    return {'url': os.environ['LIVEKIT_URL'], 'token': t.to_jwt(), 'room': room, 'identity': identity}


async def token(req):
    key = req.query.get('character', 'verity')
    if key not in CALLABLE: raise web.HTTPBadRequest(text=f'character must be one of {", ".join(CALLABLE)}')
    if LIVE and key != LIVE:
        return web.json_response({'error': f'Only {VARIANTS[LIVE]["name"]} is running right now: scripts/run_verity.sh {key} runs {VARIANTS[key]["name"]}.'},
                                 status=409)
    try: settings = json.loads(req.query.get('settings', '{}')[:1000])
    except ValueError: settings = {}
    return web.json_response(mint_call(key, settings=settings, uid=req.query.get('uid', '')))


async def get_memory(req):
    return web.json_response(memory.load(req.query.get('uid', '')))


async def forget_memory(req):
    memory.forget(req.query.get('uid', ''))
    return web.json_response({'ok': True})


async def health(_req):
    """Also the app's character list, straight from verity/variants.py (secret ones included, flagged)."""
    chars = [dict(key=k, secret=bool(v.get('secret')), **{f: v[f] for f in ('name', 'color', 'face', 'domain', 'intro', 'goodbye')},
                  sample=v['samples'][0]) for k, v in VARIANTS.items()]
    return web.json_response({'ok': True, 'characters': chars, 'voices': VOICES, 'agent_name': os.environ.get('AGENT_NAME', ''), 'live': LIVE})


async def landing(_req):
    """The public landing page (web/landing.html); the call app lives at /app."""
    return web.FileResponse(os.path.join(WEB, 'landing.html'))


async def index(_req):
    return web.FileResponse(os.path.join(WEB, 'index.html'))


async def config(_req):
    """What the landing page needs to link up with the running system (all from env, nothing secret)."""
    return web.json_response({'ok': True, 'live': LIVE, 'agent_name': os.environ.get('AGENT_NAME', ''),
                              'facetime': os.environ.get('FACETIME_NUMBER', ''), 'dance': os.environ.get('DANCE', ''),
                              'fork': os.environ.get('FORK_URL', 'https://github.com/zxpeng2007/brodie-starter'), 'app': '/app'})


async def dances(_req):
    """The lessons in data/dances (newest first, as the handler picks them): name, parts, length, bpm, steps."""
    out = []
    try:
        import glob
        from starter.dance.reference import Reference, DANCES_DIR
        live = os.environ.get('DANCE', '')
        paths = sorted(glob.glob(os.path.join(DANCES_DIR, '*.npz')), key=os.path.getmtime, reverse=True)
        for i, p in enumerate(paths):
            try:
                r = Reference(p)
                out.append({'name': r.name, 'parts': r.n_chunks, 'bpm': r.bpm, 'seconds': round(len(r.kps) / 10, 1),
                            'steps': [s for s in r.steps if s.strip()], 'video': bool(r.video_path), 'music': r.audio is not None,
                            'live': r.name == live or (not live and i == 0)})
            except Exception as e:
                out.append({'name': os.path.basename(p)[:-4], 'error': repr(e)})
    except Exception as e:
        return web.json_response({'dances': out, 'error': repr(e)})
    return web.json_response({'dances': out})


def app():
    a = web.Application()
    a.add_routes([web.get('/api/token', token), web.get('/api/health', health), web.get('/api/config', config),
                  web.get('/api/dances', dances), web.get('/api/memory', get_memory), web.delete('/api/memory', forget_memory),
                  web.get('/', landing), web.get('/app', index), web.get('/index.html', index), web.static('/', WEB)])
    return a


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'), help='0.0.0.0 on a hosting service')
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8765')), help='hosting services set PORT')
    a = ap.parse_args()
    print(f'[verity] web app on http://{a.host}:{a.port} (landing at /, call app at /app)', flush=True)
    web.run_app(app(), host=a.host, port=a.port, print=None)
