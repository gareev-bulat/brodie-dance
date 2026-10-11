"""Brodie's video: what the caller SEES (full screen on a FaceTime Video call, an inset on the phone page).

  - while a part plays: the creator's video for exactly those beats, in sync with the music, mirrored like a dance studio
    mirror (DANCE_MIRROR_VIDEO=0 to turn that off), with the count ("5 6 7 8", then "1" .. "8") big on screen;
  - between parts: a card with the part number, the moves and the last score, over the creator's first pose of that part;
  - before the lesson: "Step back so I can see your whole body".

The creator's video is data/dances/<name>.mp4 (reference.py copies it there; or copy it there yourself).
"""
import asyncio, os, textwrap, time
import numpy as np

W, H = 360, 640                     # portrait; the receiver scales it
FPS_OUT = 15
VOLT = (217, 255, 61); WHITE = (245, 245, 245); BLACK = (0, 0, 0)
MIRROR = os.environ.get('DANCE_MIRROR_VIDEO', '1') != '0'


def load_frames(path, fps=FPS_OUT):
    """The video at `fps`, each frame letterboxed to W x H RGB. Returns (frames uint8 (N, H, W, 3), fps)."""
    import cv2
    cap = cv2.VideoCapture(path); src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    out, i, next_t = [], 0, 0.0
    while True:
        ok, bgr = cap.read()
        if not ok: break
        t = i / src_fps; i += 1
        if t + 1e-6 < next_t: continue
        next_t += 1.0 / fps
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB); h, w = rgb.shape[:2]
        s = min(W / w, H / h); nw, nh = max(2, int(w * s)), max(2, int(h * s))
        canvas = np.zeros((H, W, 3), np.uint8)
        y0, x0 = (H - nh) // 2, (W - nw) // 2
        canvas[y0:y0 + nh, x0:x0 + nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
        out.append(canvas[:, ::-1] if MIRROR else canvas)
    cap.release()
    return (np.stack(out) if out else np.zeros((0, H, W, 3), np.uint8)), fps


def _text(img, s, org, scale, color=WHITE, thick=2, center=False):
    import cv2
    font = cv2.FONT_HERSHEY_DUPLEX
    if center:
        (tw, _), _ = cv2.getTextSize(s, font, scale, thick); org = ((W - tw) // 2, org[1])
    cv2.putText(img, s, org, font, scale, BLACK, thick + 4, cv2.LINE_AA)
    cv2.putText(img, s, org, font, scale, color, thick, cv2.LINE_AA)


class Screen:
    def __init__(self, ref, video_path):
        self.ref = ref; self.video_path = video_path
        self.frames, self.fps = np.zeros((0, H, W, 3), np.uint8), FPS_OUT
        self.source = None

    def load(self):
        p = self.video_path
        if p and os.path.exists(p): self.frames, self.fps = load_frames(p)
        print(f'[dance] screen: {len(self.frames)} frames of the creator\'s video' + ('' if len(self.frames) else f' (none: put the video at {p})')
              + (', mirrored' if MIRROR and len(self.frames) else ''), flush=True)

    def frame_at(self, t):
        if not len(self.frames): return np.zeros((H, W, 3), np.uint8)
        return self.frames[int(np.clip(round(t * self.fps), 0, len(self.frames) - 1))].copy()

    def render(self, st, now):
        """st: the handler's state dict. Returns H x W x 3 RGB."""
        ref = self.ref; play = st.get('play'); k = st.get('chunk', 0); n = ref.n_chunks
        rec = play.get('rec') if play else None
        t_push = rec.get('t_push') if isinstance(rec, dict) else None
        if play and t_push and not rec.get('t_done'):
            el = now - t_push - 0.1                                    # seconds into the clip, as heard
            t_vid = ref.first_beat_s + 8 * play['k0'] * ref.beat_s - 4 * ref.beat_s + el
            img = self.frame_at(t_vid)
            beat = int(np.floor(el / ref.beat_s))
            if 0 <= beat < 4:
                _text(img, str(5 + beat), (0, 360), 5.0, VOLT, 8, center=True)
            elif beat >= 4:
                b = beat - 4
                if b < 8 * (play['k1'] - play['k0']):
                    _text(img, str(b % 8 + 1), (W - 80, 90), 2.6, VOLT, 5)
            label = 'FULL DANCE' if play['k1'] - play['k0'] > 1 else f'PART {play["k0"] + 1}/{n}'
            _text(img, label, (14, 40), 0.9, WHITE, 2)
            return img
        # between parts: a card over the creator's first pose of the next part, darkened
        img = (self.frame_at(ref.first_beat_s + 8 * min(k, n - 1) * ref.beat_s) * 0.35).astype(np.uint8)
        state = st.get('state')
        if state == 'idle' or not st.get('visible'):
            _text(img, 'BRODIE DANCE', (0, 120), 1.3, VOLT, 3, center=True)
            for i, line in enumerate(['Step back so I can see', 'your whole body']):
                _text(img, line, (0, 300 + 40 * i), 0.9, WHITE, 2, center=True)
            return img
        if state == 'done':
            _text(img, 'YOU DID IT!', (0, 200), 1.5, VOLT, 3, center=True)
            fs = st.get('full_scores') or st.get('scores') or {}
            for i, (kk, s) in enumerate(sorted(fs.items())):
                _text(img, f'Part {kk + 1}: {s}%', (0, 290 + 45 * i), 1.0, WHITE, 2, center=True)
            return img
        title = 'FULL DANCE NEXT' if state == 'full' else f'PART {k + 1}/{n}'
        _text(img, title, (0, 110), 1.4, VOLT, 3, center=True)
        last = st.get('last_pct')
        if last is not None: _text(img, f'Last try: {last}%', (0, 170), 1.0, WHITE, 2, center=True)
        steps = ref.step(k) if state != 'full' else None
        if steps:
            for i, line in enumerate(textwrap.wrap(steps, 26)[:9]):
                _text(img, line, (0, 260 + 36 * i), 0.75, WHITE, 2, center=True)
        return img

    async def run(self, room, get_state):
        """Publish the video track and push frames at FPS_OUT until the room closes."""
        from livekit import rtc
        await asyncio.get_running_loop().run_in_executor(None, self.load)     # decoding the video must not block the call
        self.source = rtc.VideoSource(W, H)
        track = rtc.LocalVideoTrack.create_video_track('coach-video', self.source)
        await room.local_participant.publish_track(track, rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA, simulcast=False, video_encoding=rtc.VideoEncoding(max_framerate=FPS_OUT, max_bitrate=1_200_000)))
        print('[dance] published video track coach-video', flush=True)
        period = 1.0 / FPS_OUT
        while True:
            t0 = time.monotonic()
            try:
                img = self.render(get_state(), time.time())
                self.source.capture_frame(rtc.VideoFrame(W, H, rtc.VideoBufferType.RGB24, np.ascontiguousarray(img).tobytes()).convert(rtc.VideoBufferType.I420))
            except Exception as e:
                print(f'[dance] screen frame failed: {e!r}', flush=True); await asyncio.sleep(1.0)
            await asyncio.sleep(max(0.0, period - (time.monotonic() - t0)))
