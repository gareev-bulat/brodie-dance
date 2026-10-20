"""Brodie's video: what the caller SEES (full screen on a FaceTime Video call, an inset on the phone page).

  - while a part plays: the creator's video for exactly those beats, in sync with the music, mirrored like a dance studio
    mirror (DANCE_MIRROR_VIDEO=0 to turn that off), with the count ("5 6 7 8", then "1" .. "8") big on screen;
  - between parts: a card with the part number, the moves and the last score, over the creator's first pose of that part;
  - before the lesson: "Step back so I can fit you in the frame", and while the caller may still pick a dance the numbered
    menu of lessons (the handler's st['menu']); Screen.set_ref switches the lesson to a video already in memory.

The creator's video is data/dances/<name>.mp4 (reference.py copies it there; or copy it there yourself).
"""
import asyncio, os, textwrap, threading, time
import numpy as np

_SIZE = os.environ.get('DANCE_VIDEO_SIZE', '540x960')      # portrait; the receiver scales it (720x1280 costs more CPU per frame)
W, H = (int(v) for v in _SIZE.lower().split('x'))
FPS_OUT = int(os.environ.get('DANCE_VIDEO_FPS', '24'))
IDLE_PERIOD_S = 0.2                                           # 5 fps while the room waits for a caller (the video starts on join)
KBPS = int(os.environ.get('DANCE_VIDEO_KBPS', '3000'))       # max bitrate of the coach video
CODEC = os.environ.get('DANCE_VIDEO_CODEC', 'h264').lower()  # h264 decodes in hardware on phones; vp8 is the fallback
K = W / 360                                                  # overlay scale: every position and font size below is in 360x640 units
VOLT = (217, 255, 61); WHITE = (245, 245, 245); BLACK = (0, 0, 0)
MIRROR = os.environ.get('DANCE_MIRROR_VIDEO', '1') != '0'
MIRROR_SELF = os.environ.get('DANCE_MIRROR_SELF', '1') != '0'    # the caller's own camera flipped like a selfie / studio mirror
# MediaPipe-33 limb pairs for the skeleton over the caller's camera (shoulders, arms, hips, legs; the face is left alone)
BODY_LINES = [(11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24), (23, 25), (25, 27), (24, 26), (26, 28)]
LEFT_MP = {11, 13, 15, 23, 25, 27}
COL_L, COL_R, COL_MID = (255, 200, 0), (0, 170, 255), (240, 240, 240)   # RGB: the caller's left limbs amber, right limbs blue
LEAD_BEATS = 4                                                     # beats of song before ONE (starter/handlers.py build_clip), no numbers shown
SHOW_COUNTS = os.environ.get('DANCE_COUNT_ALOUD', '0') != '0'      # the 1..8 numbers only when the counts are spoken too
PIP_W, PIP_H, PIP_PAD, BAR_H = int(120 * K), int(213 * K), int(12 * K), int(36 * K)   # the caller's camera bottom-right, the bar on top
# FaceTime on an iPhone shows this 9:16 track full screen ("Fill"): the screen is narrower (about 9:19.5), so roughly 9 % of each
# side is cut off, and the Dynamic Island covers the top middle. The creator's whole frame, the bar, the texts and the caller's
# camera stay inside this safe area; the edges show a blurred copy of the creator's frame. In 360x640 units, like everything else.
SAFE_X = int(36 * K)                                             # 10 % each side
BAR_Y = int(44 * K)                                              # the accuracy bar starts below the island
VID_X0, VID_X1 = SAFE_X, W - SAFE_X                              # where the creator's frame goes
VID_Y0, VID_Y1 = BAR_Y + BAR_H + int(6 * K), H - int(10 * K)
LAYOUT = 'safe1'                                                 # in the frame cache key: a layout change re-renders the cache
PASS_MARK = float(os.environ.get('DANCE_PASS', '0.5'))


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
            cv2.line(panel, pts[a], pts[b], col, max(2, int(3 * K)), cv2.LINE_AA)
    for i, p in pts.items():
        if i >= 11: cv2.circle(panel, p, max(3, int(4 * K)), COL_MID, -1, cv2.LINE_AA)


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


_MEM = {}   # _key(path) -> (frames, fps): decoded once per process


def _key(path, fps=FPS_OUT):
    """The frame cache key (memory and disk) of a video: its name, the output size, layout, fps, mirror, its mtime and size."""
    st = os.stat(path)
    return (f'{os.path.splitext(os.path.basename(path))[0]}_{W}x{H}_{LAYOUT}_{fps}fps_{"mirror" if MIRROR else "plain"}'
            f'_{int(st.st_mtime)}_{st.st_size}.pkl')


def ready(path):
    """The video's frames are already in this process's memory (prewarm): showing it costs no decode and no disk read. Memory only."""
    try: return bool(path) and _key(path) in _MEM
    except OSError: return False


def cached_frames(path, fps=FPS_OUT):
    """load_frames, but decoded only once: kept in memory for this process and on disk in data/tmp/screen_cache/ for the
    next ones. Decoding the creator's video takes ~8 s of both CPUs on the server; done at call time it starved the call
    (Brodie's video sent no frames) and Chert hung up with "Agent failed". From the cache it loads in well under a second."""
    import pickle
    key = _key(path, fps)
    if key in _MEM: return _MEM[key]
    cdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(path))), 'tmp', 'screen_cache')   # data/tmp/screen_cache
    cpath = os.path.join(cdir, key)
    try:
        with open(cpath, 'rb') as f: _MEM[key] = pickle.load(f); return _MEM[key]
    except Exception:
        pass                                                  # no cache yet (or a broken one): decode and write it
    out = load_frames(path, fps)
    try:
        os.makedirs(cdir, exist_ok=True); tmp = f'{cpath}.{os.getpid()}.tmp'
        with open(tmp, 'wb') as f: pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, cpath)                                # atomic: another process never reads half a file
    except Exception as e:
        print(f'[dance] screen cache not written: {e!r}', flush=True)
    _MEM[key] = out; return out


def preload(video_path):
    """Called from the worker's prewarm (before any call): the creator's video ready in this process. Returns the frame count."""
    if not video_path or not os.path.exists(video_path): return 0
    return len(cached_frames(video_path)[0])


def load_frames(path, fps=FPS_OUT):
    """The video at `fps`, each frame fitted whole inside the safe area (VID_X0..VID_X1, VID_Y0..VID_Y1) over a blurred, darkened
    copy of itself that fills the W x H frame. Returns (JPEG-encoded frames, fps)."""
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
        c = max(W / w, H / h)                                   # the backdrop: the same frame covering W x H, blurred and dimmed
        cw, ch = max(W, int(np.ceil(w * c))), max(H, int(np.ceil(h * c)))
        small = cv2.GaussianBlur(cv2.resize(rgb, (max(2, cw // 8), max(2, ch // 8)), interpolation=cv2.INTER_AREA), (0, 0), 3)
        big = cv2.resize(small, (cw, ch), interpolation=cv2.INTER_LINEAR)
        by, bx = (big.shape[0] - H) // 2, (big.shape[1] - W) // 2
        canvas = (big[by:by + H, bx:bx + W].astype(np.float32) * 0.4).astype(np.uint8)
        bwid, bhei = VID_X1 - VID_X0, VID_Y1 - VID_Y0            # the creator's whole frame inside the safe area
        s = min(bwid / w, bhei / h); nw, nh = max(2, int(w * s)), max(2, int(h * s))
        y0, x0 = VID_Y0 + (bhei - nh) // 2, VID_X0 + (bwid - nw) // 2
        canvas[y0:y0 + nh, x0:x0 + nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
        frame = canvas[:, ::-1] if MIRROR else canvas
        out.append(cv2.imencode('.jpg', cv2.cvtColor(np.ascontiguousarray(frame), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])[1])
    cap.release()
    return out, fps                                           # JPEG-encoded frames: 25 s at 540x960 is ~40 MB instead of ~900 MB raw


def _text(img, s, org, scale, color=WHITE, thick=2, center=False):
    """org, scale and thick are in 360x640 units; scaled by K for the real canvas. Text that would run past the safe area
    (SAFE_X from either edge) is set smaller so it stays on an iPhone screen."""
    import cv2
    font = cv2.FONT_HERSHEY_DUPLEX
    org = (int(round(org[0] * K)), int(round(org[1] * K))); scale = scale * K; thick = max(1, int(round(thick * K)))
    room = (W - 2 * SAFE_X - int(8 * K)) if center else (W - SAFE_X - org[0])
    (tw, _), _ = cv2.getTextSize(s, font, scale, thick)
    if tw > room: scale *= room / tw
    if center:
        (tw, _), _ = cv2.getTextSize(s, font, scale, thick); org = ((W - tw) // 2, org[1])
    cv2.putText(img, s, org, font, scale, BLACK, thick + 4, cv2.LINE_AA)
    cv2.putText(img, s, org, font, scale, color, thick, cv2.LINE_AA)


class Screen:
    def __init__(self, ref, video_path):
        self.ref = ref; self.video_path = video_path
        self.frames, self.fps = [], FPS_OUT                       # JPEG-encoded frames (load_frames)
        self.source = None
        self._gen = 0; self._lock = threading.Lock()              # set_ref bumps _gen: a load still decoding then keeps its frames to itself

    def load(self):
        p, g = self.video_path, self._gen                         # runs in an executor thread (run): set_ref may switch meanwhile
        if p and os.path.exists(p):
            out = cached_frames(p)
            with self._lock:
                if self._gen == g: self.frames, self.fps = out
        print(f'[dance] screen: {len(self.frames)} frames of the creator\'s video' + ('' if len(self.frames) else f' (none: put the video at {p})')
              + (', mirrored' if MIRROR and len(self.frames) else ''), flush=True)

    def set_ref(self, ref, video_path):
        """Switch the lesson (the caller picked another dance; loop thread only, before the preview): the dance and its frames
        together, only from memory (ready), never a decode or a disk read during a call. False, nothing changed, if not preloaded."""
        try: out = _MEM.get(_key(video_path)) if video_path else None   # = ready(video_path), and the frames it found
        except OSError: out = None
        if out is None: return False
        with self._lock:
            self._gen += 1; self.ref, self.video_path = ref, video_path; self.frames, self.fps = out
        return True

    def frame_at(self, t):
        if not len(self.frames): return np.zeros((H, W, 3), np.uint8)
        import cv2
        buf = self.frames[int(np.clip(round(t * self.fps), 0, len(self.frames) - 1))]
        return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)   # a fresh array: safe to draw on

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
                    _text(img, str(b % 8 + 1), (W / K - SAFE_X / K - 50, 150), 2.6, VOLT, 5)
            label = ('WATCH THE WHOLE DANCE' if play.get('preview') else 'FULL DANCE') if play['k1'] - play['k0'] > 1 else f'PART {play["k0"] + 1}/{n}'
            _text(img, label, (SAFE_X / K + 10, VID_Y0 / K + 30), 0.75 if play.get('preview') else 0.9, WHITE, 2)
            return self._finish(img, st, cam)
        # between parts: a card over the creator's first pose of the next part, darkened; you stay bottom-right
        img = (self.frame_at(ref.first_beat_s + 8 * min(k, n - 1) * ref.beat_s) * 0.35).astype(np.uint8)
        state = st.get('state')
        if state in ('idle', 'intro') or not st.get('visible'):
            menu = st.get('menu')                                      # [(number, title, is the current one)] while the caller may pick
            if menu:                                                   # every line above y 415 (the caller's camera) and below the bar
                set_ = state == 'intro' and st.get('visible')          # in frame, the preview is next: the menu stays up until it starts
                _text(img, 'BRODIE DANCE', (0, 120), 1.1, VOLT, 3, center=True)
                for i, line in enumerate(['The whole dance', 'plays once first'] if set_ else ['Step back so I can', 'fit you in the frame']):
                    _text(img, line, (0, 170 + 34 * i), 0.8, WHITE, 2, center=True)
                _text(img, 'SAY A NAME OR NUMBER TO PICK', (0, 262), 0.6, WHITE, 1, center=True)
                y0, dy = (302, 38) if len(menu) <= 3 else (288, 30)        # four rows still end by y 378
                for i, (num, title, cur) in enumerate(menu[:4]):
                    _text(img, f'{"> " if cur else ""}{num}  {str(title).upper()}', (0, y0 + dy * i), 0.75 if cur else 0.65,
                          VOLT if cur else WHITE, 2 if cur else 1, center=True)
                return self._finish(img, st, cam)
        if state == 'idle' or not st.get('visible'):
            _text(img, 'BRODIE DANCE', (0, 150), 1.3, VOLT, 3, center=True)
            for i, line in enumerate(['Step back so I can', 'fit you in the frame']):
                _text(img, line, (0, 300 + 40 * i), 0.9, WHITE, 2, center=True)
            return self._finish(img, st, cam)
        if state == 'done':
            _text(img, 'YOU DID IT!', (0, 200), 1.5, VOLT, 3, center=True)
            fs = st.get('full_scores') or st.get('scores') or {}
            for i, (kk, s) in enumerate(sorted(fs.items())):
                _text(img, f'Part {kk + 1}: {s}%', (0, 290 + 45 * i), 1.0, WHITE, 2, center=True)
            return self._finish(img, st, cam)
        if st.get('title'): _text(img, str(st['title']), (0, 100), 0.6, WHITE, 1, center=True)   # the lesson's name (a picked dance)
        title = 'FULL DANCE NEXT' if state == 'full' else f'PART {k + 1}/{n}'
        _text(img, title, (0, 140), 1.4, VOLT, 3, center=True)
        last = st.get('last_pct')
        if last is not None: _text(img, f'Last try: {last}%', (0, 196), 1.0, WHITE, 2, center=True)
        steps = ref.step(k) if state != 'full' else None
        if steps:
            y = 268
            for bullet in [b.strip() for b in steps.split('|') if b.strip()][:3]:   # 2-3 short bullets, not a paragraph
                for j, line in enumerate(textwrap.wrap(bullet, 24, break_on_hyphens=False)[:2]):
                    _text(img, ('- ' if j == 0 else '  ') + line, (SAFE_X / K + 6, y), 0.72, WHITE, 2); y += 32
                y += 8
        return self._finish(img, st, cam)

    def _finish(self, img, st, cam):
        """Every frame ends with you bottom-right (camera + skeleton) and the accuracy bar along the bottom."""
        import cv2
        if cam is not None:                                           # inside the safe area, so an iPhone doesn't cut it off
            y0, x0 = H - PIP_PAD - PIP_H, W - SAFE_X - PIP_PAD - PIP_W
            img[y0:y0 + PIP_H, x0:x0 + PIP_W] = cam_panel(cam, PIP_W, PIP_H)
            cv2.rectangle(img, (x0, y0), (x0 + PIP_W - 1, y0 + PIP_H - 1), WHITE, max(2, int(2 * K)))
        y0 = BAR_Y                                                    # the accuracy bar along the top, below the Dynamic Island
        cv2.rectangle(img, (0, y0), (W, y0 + BAR_H), (18, 18, 18), -1)
        sx = SAFE_X / K
        _text(img, 'ACCURACY', (sx + 6, y0 / K + 24), 0.55, WHITE, 1)
        bx0, bx1, by0, by1 = int((sx + 104) * K), W - SAFE_X - int(46 * K), y0 + int(10 * K), y0 + BAR_H - int(10 * K)
        cv2.rectangle(img, (bx0, by0), (bx1, by1), (70, 70, 70), -1)
        pct = st.get('last_pct')
        if pct is not None:
            f = max(0, min(100, int(pct))) / 100
            col = (int(255 * (1 - f)), int(215 * f), 60)                # red -> green
            cv2.rectangle(img, (bx0, by0), (bx0 + int((bx1 - bx0) * f), by1), col, -1)
        xm = bx0 + int((bx1 - bx0) * PASS_MARK)
        cv2.line(img, (xm, by0 - 2), (xm, by1 + 2), VOLT, max(2, int(2 * K)))   # the pass mark
        _text(img, f'{pct}%' if pct is not None else '--', (W / K - sx - 42, y0 / K + 25), 0.6, WHITE, 1)
        return img

    async def run(self, room, get_state, get_cam=lambda: None):
        """Publish the video track and push frames at FPS_OUT until the room closes. get_cam() -> (rgb, kp) or None."""
        from livekit import rtc
        # publish first, then decode the creator's video in the background: until it is ready, render() shows the
        # "Step back so I can fit you in the frame" card on black, so the caller never stares at a gray screen
        loading = asyncio.get_running_loop().run_in_executor(None, self.load)  # decoding the video must not block the call
        loading.add_done_callback(lambda f: f.cancelled() or f.exception() is None or print(f'[dance] video load failed: {f.exception()!r}', flush=True))
        self.source = rtc.VideoSource(W, H)
        track = rtc.LocalVideoTrack.create_video_track('coach-video', self.source)
        codec = rtc.VideoCodec.H264 if CODEC == 'h264' else rtc.VideoCodec.VP8
        await room.local_participant.publish_track(track, rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA, simulcast=False, video_codec=codec,
            video_encoding=rtc.VideoEncoding(max_framerate=FPS_OUT, max_bitrate=KBPS * 1000)))
        print(f'[dance] published video track coach-video: {W}x{H} @ {FPS_OUT} fps, {KBPS} kbps, {CODEC}', flush=True)
        period = 1.0 / FPS_OUT
        while True:
            t0 = time.monotonic(); cam = get_cam(); st = get_state()
            waiting = cam is None and st.get('state') == 'idle'       # a warm room with nobody in it yet: a still card is enough
            try:
                img = self.render(st, time.time(), cam)
                self.source.capture_frame(rtc.VideoFrame(W, H, rtc.VideoBufferType.RGB24, np.ascontiguousarray(img).tobytes()).convert(rtc.VideoBufferType.I420))
            except Exception as e:
                print(f'[dance] screen frame failed: {e!r}', flush=True); await asyncio.sleep(1.0)
            await asyncio.sleep(max(0.0, (IDLE_PERIOD_S if waiting else period) - (time.monotonic() - t0)))
