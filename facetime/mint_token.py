"""Print a LiveKit access token for tier (c) (the phone page) or any manual test.

Grants: join `room`, publish + subscribe. TTL from BRIDGE_TOKEN_TTL_S (default 3600 s). Keys from
LIVEKIT_API_KEY / LIVEKIT_API_SECRET (never printed). Use the token in facetime/phone_test.html:
  .venv/bin/python -m facetime.mint_token --room rtcoach-test-abc [--identity chert-bridge] [--url]
Dispatch: a worker with a name (AGENT_NAME, e.g. team-verity-agent) only joins rooms whose token asks for it, so the token carries a
RoomAgentDispatch for --agent-name (default: env AGENT_NAME; "" = none, for a worker on automatic dispatch). Without this the phone
page's room is created with no agent in it and the call is silent.
"""
import argparse, datetime, os
from livekit import api
from facetime import BRIDGE_IDENTITY

def mint(identity, room, ttl_s=None, name=None, agent_name=None):
    ttl = int(ttl_s if ttl_s is not None else os.environ.get('BRIDGE_TOKEN_TTL_S', '3600'))
    key, sec = os.environ.get('LIVEKIT_API_KEY'), os.environ.get('LIVEKIT_API_SECRET')
    if not key or not sec:
        raise SystemExit('LIVEKIT_API_KEY / LIVEKIT_API_SECRET not set')
    t = (api.AccessToken(key, sec).with_identity(identity).with_name(name or identity).with_ttl(datetime.timedelta(seconds=ttl))
         .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True, can_publish_data=True)))
    agent_name = os.environ.get('AGENT_NAME', '') if agent_name is None else agent_name
    if agent_name:                                            # named dispatch: the worker registered under this name joins the room
        from livekit.protocol.room import RoomConfiguration
        from livekit.protocol.agent_dispatch import RoomAgentDispatch
        t = t.with_room_config(RoomConfiguration(agents=[RoomAgentDispatch(agent_name=agent_name)]))
    return t.to_jwt()

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--room', required=True); ap.add_argument('--identity', default=BRIDGE_IDENTITY)
    ap.add_argument('--ttl', type=int, default=None); ap.add_argument('--url', action='store_true', help='also print a phone_test.html query string')
    ap.add_argument('--agent-name', default=None, help='dispatch this agent into the room (default: env AGENT_NAME; "" = automatic dispatch only)')
    a = ap.parse_args()
    tok = mint(a.identity, a.room, a.ttl, agent_name=a.agent_name)
    print(tok)
    if (os.environ.get('AGENT_NAME', '') if a.agent_name is None else a.agent_name):
        import sys; print(f'(dispatches agent {os.environ.get("AGENT_NAME", "") if a.agent_name is None else a.agent_name!r} into room {a.room!r})', file=sys.stderr)
    if a.url:
        from urllib.parse import urlencode
        q = '?' + urlencode(dict(url=os.environ.get('LIVEKIT_URL', ''), token=tok, room=a.room))
        print(q)
        base = os.environ.get('RTCOACH_WEB_URL', 'https://james-72765--rtcoach-webhook.modal.run')
        print(base.rstrip('/') + '/phone' + q)              # the page over https (iOS Safari needs it for the camera)

if __name__ == '__main__':
    main()
