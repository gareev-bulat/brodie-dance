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
MIRROR_SELF = os.environ.get('DANCE_MIRROR_SELF', '1') != '0'    # the caller's own camera flipped like a selfie / studio mirror
# MediaPipe-33 limb pairs for the skeleton over the caller's camera (shoulders, arms, hips, legs; the face is left alone)
BODY_LINES = [(11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24), (23, 25), (25, 27), (24, 26), (26, 28)]
LEFT_MP = {11, 13, 15, 23, 25, 27}
COL_L, COL_R, COL_MID = (255, 200, 0), (0, 170, 255), (240, 240, 240)   # RGB: the caller's left limbs amber, right limbs blue
LEAD_BEATS = 4                                                     # beats of song before ONE (starter/handlers.py build_clip), no numbers shown
SHOW_COUNTS = os.environ.get('DANCE_COUNT_ALOUD', '0') != '0'      # the 1..8 numbers only when the counts are spoken too
PIP_W, PIP_H, PIP_PAD, BAR_H = 120, 213, 12, 36                    # the caller's camera bottom-right, the accuracy bar along the bottom
PASS_MARK = float(os.environ.get('DANCE_PASS', '0.7'))


def _fit(img, pw, ph, cx=None):
    """Scale img to height ph, then take a pw-wide column centred on cx (0..1 of the width; None = middle); narrow content is
    letterboxed. Returns (panel, s, x0): a point (u, v) in 0..1 of img lands at (u * w * s - x0, v * h * s) in the panel."""
    import cv2
    h, w = img.shape[:2]; s = ph / h; nw = max(1, int(round(w * s)))
    big = cv2.resize(img, (nw, ph), interpolation=cv2.INTER_AREA) if (nw, ph) != (w, h) else img
    if nw >= pw:
        c = nw / 2 if cx is None else cx * nw
        x0 = int(np.clip(round(c - pw / 2), 0, nw - pw)); panel = big[:, x0:x0 + pw]
    else:
        x0 = -((pw - nw) // 2); panel = np.zeros((ph, pw, 3), np.uint8); panel[:, -x0:-x0 + nw] = big
    return np.ascontiguousarray(panel), s, x0


def _skeleton(panel, kp, w, h, s, x0, mirror):
    """Draw the MediaPipe pose (kp (33, >=4): x, y in 0..1, z, visibility) on a panel made by _fit."""
    import cv2
    pts = {}
    for i, k in enumerate(kp):
        x, y, v = float(k[0]), float(k[1]), float(k[3]) if len(k) > 3 else 1.0
        if v < 0.3: continue
        if mirror: x = 1.0 - x
        px, py = int(round(x * w * s - x0)), int(round(y * h * s))
        if 0 <= px < panel.shape[1] and 0 <= py < panel.shape[0]: pts[i] = (px, py)
    for a, b in BODY_LINES:
        if a in pts and b in pts:
            col = COL_L if (a in LEFT_MP and b in LEFT_MP) else COL_R if (a not in LEFT_MP and b not in LEFT_MP) else COL_MID
            cv2.line(panel, pts[a], pts[b], col, 3, cv2.LINE_AA)
    for i, p in pts.items():
        if i >= 11: cv2.circle(panel, p, 4, COL_MID, -1, cv2.LINE_AA)


def cam_panel(cam, pw, ph, dim=1.0):
    """The caller's camera (rgb HxWx3, kp or None) as a pw x ph panel: mirrored like a selfie, cropped around the hips, skeleton on top."""
    rgb, kp = cam
    img = rgb[:, ::-1] if MIRROR_SELF else rgb
    cx = None
    if kp is not None and len(kp) > 24:
        hx = (float(kp[23][0]) + float(kp[24][0])) / 2
        cx = 1.0 - hx if MIRROR_SELF else hx
    panel, s, x0 = _fit(img, pw, ph, cx)
    if dim < 1.0: panel = (panel.astype(np.float32) * dim).astype(np.uint8)
    if kp is not None: _skeleton(panel, kp, rgb.shape[1], rgb.shape[0], s, x0, MIRROR_SELF)
    return panel


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

    def render(self, st, now, cam=None):
        """st: the handler's state dict; cam: (caller rgb frame, MediaPipe kp or None) or None. Returns H x W x 3 RGB.
        While a part plays: the creator (left) next to the caller's own camera with the skeleton (right), counts on top.
        Between parts: the card over the caller's camera, dimmed (the creator's pose when there is no camera yet)."""
        ref = self.ref; play = st.get('play'); k = st.get('chunk', 0); n = ref.n_chunks
        rec = play.get('rec') if play else None
        t_push = rec.get('t_push') if isinstance(rec, dict) else None
        if play and t_push and not rec.get('t_done'):
            el = now - t_push - 0.1                                    # seconds into the clip, as heard
            t_vid = ref.first_beat_s + 8 * play['k0'] * ref.beat_s - LEAD_BEATS * ref.beat_s + el
            img = self.frame_at(t_vid)                                 # the creator full screen, you bottom-right
            beat = int(np.floor(el / ref.beat_s))
            if beat < LEAD_BEATS:
                _text(img, 'GET READY', (0, 330), 1.5, VOLT, 4, center=True)
            elif SHOW_COUNTS:
                b = beat - LEAD_BEATS
                if b < 8 * (play['k1'] - play['k0']):
                    _text(img, str(b % 8 + 1), (W - 80, 90), 2.6, VOLT, 5)
            label = ('WATCH THE WHOLE DANCE' if play.get('preview') else 'FULL DANCE') if play['k1'] - play['k0'] > 1 else f'PART {play["k0"] + 1}/{n}'
            _text(img, label, (14, 40), 0.75 if play.get('preview') else 0.9, WHITE, 2)
            return self._finish(img, st, cam)
        # between parts: a card over the creator's first pose of the next part, darkened; you stay bottom-right
        img = (self.frame_at(ref.first_beat_s + 8 * min(k, n - 1) * ref.beat_s) * 0.35).astype(np.uint8)
        state = st.get('state')
        if state == 'idle' or not st.get('visible'):
            _text(img, 'BRODIE DANCE', (0, 120), 1.3, VOLT, 3, center=True)
            for i, line in enumerate(['Step back so I can see', 'your whole body']):
                _text(img, line, (0, 300 + 40 * i), 0.9, WHITE, 2, center=True)
            return self._finish(img, st, cam)
        if state == 'done':
            _text(img, 'YOU DID IT!', (0, 200), 1.5, VOLT, 3, center=True)
            fs = st.get('full_scores') or st.get('scores') or {}
            for i, (kk, s) in enumerate(sorted(fs.items())):
                _text(img, f'Part {kk + 1}: {s}%', (0, 290 + 45 * i), 1.0, WHITE, 2, center=True)
            return self._finish(img, st, cam)
        title = 'FULL DANCE NEXT' if state == 'full' else f'PART {k + 1}/{n}'
        _text(img, title, (0, 110), 1.4, VOLT, 3, center=True)
        last = st.get('last_pct')
        if last is not None: _text(img, f'Last try: {last}%', (0, 170), 1.0, WHITE, 2, center=True)
        steps = ref.step(k) if state != 'full' else None
        if steps:
            y = 250
            for bullet in [b.strip() for b in steps.split('|') if b.strip()][:3]:   # 2-3 short bullets, not a paragraph
                for j, line in enumerate(textwrap.wrap(bullet, 24)[:2]):
                    _text(img, ('- ' if j == 0 else '  ') + line, (28, y), 0.72, WHITE, 2); y += 32
                y += 8
        return self._finish(img, st, cam)

    def _finish(self, img, st, cam):
        """Every frame ends with you bottom-right (camera + skeleton) and the accuracy bar along the bottom."""
        import cv2
        if cam is not None:
            y0, x0 = H - BAR_H - PIP_PAD - PIP_H, W - PIP_PAD - PIP_W
            img[y0:y0 + PIP_H, x0:x0 + PIP_W] = cam_panel(cam, PIP_W, PIP_H)
            cv2.rectangle(img, (x0, y0), (x0 + PIP_W - 1, y0 + PIP_H - 1), WHITE, 2)
        y0 = H - BAR_H
        cv2.rectangle(img, (0, y0), (W, H), (18, 18, 18), -1)
        _text(img, 'ACCURACY', (12, y0 + 24), 0.6, WHITE, 1)
        bx0, bx1, by0, by1 = 124, W - 62, y0 + 10, H - 10
        cv2.rectangle(img, (bx0, by0), (bx1, by1), (70, 70, 70), -1)
        pct = st.get('last_pct')
        if pct is not None:
            f = max(0, min(100, int(pct))) / 100
            col = (int(255 * (1 - f)), int(215 * f), 60)                # red -> green
            cv2.rectangle(img, (bx0, by0), (bx0 + int((bx1 - bx0) * f), by1), col, -1)
        xm = bx0 + int((bx1 - bx0) * PASS_MARK)
        cv2.line(img, (xm, by0 - 2), (xm, by1 + 2), VOLT, 2)            # the pass mark
        _text(img, f'{pct}%' if pct is not None else '--', (bx1 + 8, y0 + 25), 0.6, WHITE, 1)
        return img

    async def run(self, room, get_state, get_cam=lambda: None):
        """Publish the video track and push frames at FPS_OUT until the room closes. get_cam() -> (rgb, kp) or None."""
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
                img = self.render(get_state(), time.time(), get_cam())
                self.source.capture_frame(rtc.VideoFrame(W, H, rtc.VideoBufferType.RGB24, np.ascontiguousarray(img).tobytes()).convert(rtc.VideoBufferType.I420))
            except Exception as e:
                print(f'[dance] screen frame failed: {e!r}', flush=True); await asyncio.sleep(1.0)
            await asyncio.sleep(max(0.0, period - (time.monotonic() - t0)))
