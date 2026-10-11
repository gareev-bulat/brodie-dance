"""Smoke test for verity/agent.py with no phone: joins a room as a caller, publishes a clip as the camera, sends the test-script lines
as chat text (RoomIO text input, topic lk.chat) and prints the agent's transcript (topic lk.transcription). The agent must be running
(python -m verity.agent dev).
  python scripts/verity_smoke.py [--character dancity] [--video data/tmp/mac_handoff/crops/live.mp4]
A dancer for Dancity (scripts/run_verity.sh dancity): loop a clip, say nothing, stay a while:
  python scripts/verity_smoke.py --character dancity --video data/dances/jacks.mp4 --silent --seconds 150
"""
import argparse, asyncio, os, sys, time
import cv2
import numpy as np
from livekit import rtc
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from verity.server import mint_call

LINES = [(8, 'Hi! What do you think of my outfit?'), (8, 'Watch this move, rate it!'), (10, 'Can I talk to Gravity?'),
         (10, "What's my -ity verdict?"), (10, 'I have to go now, bye!')]


async def main(a):
    room = rtc.Room(); t0 = time.monotonic()
    def on_text(reader, who):
        async def read(): print(f'[{time.monotonic() - t0:5.1f}s] {who}: {await reader.read_all()}', flush=True)
        asyncio.create_task(read())
    room.register_text_stream_handler('lk.transcription', on_text)
    async def grab(track):                                   # the agent's face: one png per 0.5 s while it talks, to eyeball mouth + color
        n = 0
        async for ev in rtc.VideoStream(track, format=rtc.VideoBufferType.RGBA):
            n += 1
            if n % 7 == 0:
                f = ev.frame; img = np.frombuffer(f.data, np.uint8).reshape(f.height, f.width, 4)
                cv2.imwrite(f'data/tmp/smoke_face_{n // 7:03d}.png', cv2.cvtColor(img, cv2.COLOR_RGBA2BGR))
    sent = {'t': None}
    async def hear(track):                                   # time from each caller line to the first sound of the reply
        talking = False
        async for ev in rtc.AudioStream(track):
            pcm = np.frombuffer(ev.frame.data, np.int16)
            loud = pcm.size and np.sqrt(np.mean((pcm.astype(np.float32) / 32768) ** 2)) > 0.02
            if loud and not talking and sent['t'] is not None:
                print(f'[{time.monotonic() - t0:5.1f}s] (reply audio started {time.monotonic() - sent["t"]:.1f}s after the line)', flush=True); sent['t'] = None
            talking = bool(loud) or (talking and pcm.size and np.abs(pcm).max() > 300)
    def on_track(t, *_):
        if t.kind == rtc.TrackKind.KIND_VIDEO: asyncio.create_task(grab(t))
        elif t.kind == rtc.TrackKind.KIND_AUDIO: asyncio.create_task(hear(t))
    room.on('track_subscribed', on_track)
    info = mint_call(a.character, a.room)
    await room.connect(info['url'], info['token'])
    cap = cv2.VideoCapture(a.video); ok, img = cap.read(); h, w = img.shape[:2]
    src = rtc.VideoSource(w, h)
    await room.local_participant.publish_track(rtc.LocalVideoTrack.create_video_track('cam', src),
                                               rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    async def video():
        while True:
            ok, frame = cap.read()
            if not ok: cap.set(cv2.CAP_PROP_POS_FRAMES, 0); continue
            src.capture_frame(rtc.VideoFrame(w, h, rtc.VideoBufferType.RGBA, cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA).tobytes()))
            await asyncio.sleep(0.1)
    task = asyncio.create_task(video())
    for wait, line in ([] if a.silent else LINES):
        await asyncio.sleep(wait)
        print(f'[{time.monotonic() - t0:5.1f}s] CALLER: {line}', flush=True)
        await room.local_participant.send_text(line, topic='lk.chat'); sent['t'] = time.monotonic()
    await asyncio.sleep(a.seconds if a.silent else 10); task.cancel(); await room.disconnect()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--character', default='verity')
    ap.add_argument('--room', help='default: a fresh web-<character>-<id> room (the room name picks the character)')
    ap.add_argument('--video', default='data/tmp/mac_handoff/crops/live.mp4')
    ap.add_argument('--silent', action='store_true', help='send no chat lines (a dancer)'); ap.add_argument('--seconds', type=float, default=120)
    asyncio.run(main(ap.parse_args()))
