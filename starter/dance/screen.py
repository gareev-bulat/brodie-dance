"""Brodie's video: what the caller SEES (full screen on a FaceTime Video call, an inset on the phone page).

  - watch first: the whole creator video with the song, the creator's tracked skeleton drawn on it;
  - setup: "prop your phone up and step back", with the caller's LIVE skeleton big in the middle and a check when the whole
    body is in view (so you can see the tracking work);
  - while a part plays: the creator's video for exactly those beats, in sync with the music, mirrored like a dance studio
    mirror (DANCE_MIRROR_VIDEO=0 to turn that off), the creator's skeleton on it, the count big on screen, the caller's live
    skeleton in a corner panel and a live MATCH meter (the caller's pose vs the creator's at this moment);
  - between parts: a card with the part number, the moves and the last score, plus the caller's live skeleton.

The creator's video is data/dances/<name>.mp4 (reference.py copies it there; or copy it there yourself).
"""
import asyncio, os, textwrap, time
import numpy as np

from starter.dance.keypoints import features, SEG_NAMES

W, H = 360, 640                     # portrait; the receiver scales it
FPS_OUT = 15
VOLT = (217, 255, 61); WHITE = (245, 245, 245); BLACK = (0, 0, 0); CYAN = (80, 220, 255)
ORANGE = (255, 170, 40); RED = (255, 80, 80); GREY = (150, 150, 150)
MIRROR = os.environ.get('DANCE_MIRROR_VIDEO', '1') != '0'
SKELETON = os.environ.get('DANCE_SKELETON', '1') != '0'
EDGES = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16)]
VIS = 0.3
LIVE_MAX_AGE_S = 1.0


def load_frames(path, fps=FPS_OUT):
    """The video at `fps`, each frame letterboxed to W x H RGB. Returns (frames (N, H, W, 3), fps, geometry) where geometry
    maps the video's original pixels onto the canvas: canvas = orig * s + (x0, y0) (before the mirror)."""
    import cv2
    cap = cv2.VideoCapture(path); src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    out, i, next_t, geo = [], 0, 0.0, None
    while True:
        ok, bgr = cap.read()
        if not ok: break
        t = i / src_fps; i += 1
        if t + 1e-6 < next_t: continue
        next_t += 1.0 / fps
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB); h, w = rgb.shape[:2]
        s = min(W / w, H / h); nw, nh = max(2, int(w * s)), max(2, int(h * s))
        y0, x0 = (H - nh) // 2, (W - nw) // 2
        geo = dict(w=w, h=h, s=s, x0=x0, y0=y0)
        canvas = np.zeros((H, W, 3), np.uint8)
        canvas[y0:y0 + nh, x0:x0 + nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
        out.append(canvas[:, ::-1] if MIRROR else canvas)
    cap.release()
    return (np.stack(out) if out else np.zeros((0, H, W, 3), np.uint8)), fps, geo


def _text(img, s, org, scale, color=WHITE, thick=2, center=False):
    import cv2
    font = cv2.FONT_HERSHEY_DUPLEX
    if center:
        (tw, _), _ = cv2.getTextSize(s, font, scale, thick); org = ((W - tw) // 2, org[1])
    cv2.putText(img, s, org, font, scale, BLACK, thick + 4, cv2.LINE_AA)
    cv2.putText(img, s, org, font, scale, color, thick, cv2.LINE_AA)


def draw_skeleton(img, pts, vis, color, thick=3, joint=4):
    """pts (17, 2) canvas pixels, vis (17,) bool: limbs as lines over black outlines, joints as dots, the head as a circle."""
    import cv2
    p = np.round(pts).astype(int)
    for a, b in EDGES:
        if vis[a] and vis[b]: cv2.line(img, tuple(p[a]), tuple(p[b]), BLACK, thick + 3, cv2.LINE_AA)
    for a, b in EDGES:
        if vis[a] and vis[b]: cv2.line(img, tuple(p[a]), tuple(p[b]), color, thick, cv2.LINE_AA)
    for j in range(5, 17):
        if vis[j]: cv2.circle(img, tuple(p[j]), joint, BLACK, -1, cv2.LINE_AA); cv2.circle(img, tuple(p[j]), joint - 1, WHITE, -1, cv2.LINE_AA)
    if vis[0] and vis[5] and vis[6]:
        r = max(5, int(0.35 * np.linalg.norm(pts[5] - pts[6])))
        cv2.circle(img, tuple(p[0]), r, BLACK, thick + 3, cv2.LINE_AA); cv2.circle(img, tuple(p[0]), r, color, thick, cv2.LINE_AA)


def fit_skeleton(coco, box, mirror=True):
    """COCO-17 (x, y, conf) in any pixels -> (pts in the box (x, y, w, h), vis): the visible joints scaled to fit, centred."""
    vis = coco[:, 2] >= VIS
    if vis.sum() < 4: return None, vis
    xy = coco[:, :2].astype(np.float32).copy()
    if mirror: xy[:, 0] = -xy[:, 0]                                  # like a mirror: raise your right hand, the right side moves
    lo = xy[vis].min(0); hi = xy[vis].max(0); size = np.maximum(hi - lo, 1.0)
    bx, by, bw, bh = box; pad = 12
    s = min((bw - 2 * pad) / size[0], (bh - 2 * pad) / size[1])
    off = np.array([bx + (bw - size[0] * s) / 2, by + (bh - size[1] * s) / 2])
    return (xy - lo) * s + off, vis


def pose_match(user, ref_pose):
    """Instant similarity of two COCO poses (0..1) from the segment directions, the better of straight and mirrored."""
    if user is None or ref_pose is None: return None
    rd, rok = features(ref_pose[None])
    best = None
    for m in (False, True):
        ud, uok = features(user[None], mirror=m)
        ok = (uok & rok)[0]
        if ok.sum() < 4: continue
        d = np.abs(ud[0] - rd[0]) % (2 * np.pi); d = np.minimum(d, 2 * np.pi - d)
        err = float(np.degrees(d[ok]).mean()); best = err if best is None else min(best, err)
    return None if best is None else float(np.clip(1.0 - best / 45.0, 0.0, 1.0))


class Screen:
    def __init__(self, ref, video_path):
        self.ref = ref; self.video_path = video_path
        self.frames, self.fps, self.geo = np.zeros((0, H, W, 3), np.uint8), FPS_OUT, None
        self.source = None; self.match = None
        kps = getattr(ref, 'kps', None)
        # reference keypoints are in the pixels of the frame they were tracked on: the video shrunk to a 288 short side
        # (reference.py --video), or the original pixels (YOLO keypoints): told apart by their range
        self.ref_kps = None if kps is None or not len(kps) else np.asarray(kps, np.float32)

    def load(self):
        p = self.video_path
        if p and os.path.exists(p): self.frames, self.fps, self.geo = load_frames(p)
        print(f'[dance] screen: {len(self.frames)} frames of the creator\'s video' + ('' if len(self.frames) else f' (none: put the video at {p})')
              + (', mirrored' if MIRROR and len(self.frames) else '') + (', skeletons on' if SKELETON else ''), flush=True)

    def frame_at(self, t):
        if not len(self.frames): return np.zeros((H, W, 3), np.uint8)
        return self.frames[int(np.clip(round(t * self.fps), 0, len(self.frames) - 1))].copy()

    @staticmethod
    def playing(st):
        """True while a part's clip is being heard (its audio was pushed and has not finished)."""
        play = st.get('play'); rec = play.get('rec') if play else None
        return bool(play and isinstance(rec, dict) and rec.get('t_push') and not rec.get('t_done'))

    def ref_pose_at(self, t):
        if self.ref_kps is None: return None
        i = int(round(t * 10))
        return self.ref_kps[i] if 0 <= i < len(self.ref_kps) else None

    def ref_on_canvas(self, pose):
        """The creator's pose (reference pixels) -> canvas pixels on the shown (mirrored) video, or None."""
        g = self.geo
        if g is None or pose is None: return None, None
        vis = pose[:, 2] >= VIS
        if vis.sum() < 4: return None, vis
        short = min(g['w'], g['h']); f = 288.0 / short if np.nanmax(pose[vis, :2]) <= 288 * 2.2 else 1.0
        xy = pose[:, :2] / f * g['s'] + np.array([g['x0'], g['y0']])
        if MIRROR: xy[:, 0] = W - 1 - xy[:, 0]
        return xy, vis

    def live_pose(self, st, now):
        lv = st.get('live')
        return lv[1] if lv and now - lv[0] <= LIVE_MAX_AGE_S else None

    def live_panel(self, img, user, box, label='YOU'):
        import cv2
        x, y, w, h = box
        roi = img[y:y + h, x:x + w]; roi[:] = (roi * 0.25).astype(np.uint8)
        cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), CYAN, 2, cv2.LINE_AA)
        if user is not None:
            pts, vis = fit_skeleton(user, box)
            if pts is not None: draw_skeleton(img, pts, vis, CYAN, thick=2, joint=3)
        else:
            _text(img, 'no body', (x + 10, y + h // 2), 0.5, GREY, 1)
        _text(img, label, (x + 6, y + 18), 0.5, CYAN, 1)

    def render(self, st, now):
        """st: the handler's state dict. Returns H x W x 3 RGB.
        No creator video (frames empty): the clip view is a plain dark background with the count, the cards still work."""
        ref = self.ref; play = st.get('play'); k = st.get('chunk', 0); n = ref.n_chunks
        rec = play.get('rec') if play else None
        t_push = rec.get('t_push') if isinstance(rec, dict) else None
        user = self.live_pose(st, now)
        if play and t_push and not rec.get('t_done'):
            el = now - t_push - 0.1                                    # seconds into the clip, as heard
            t_vid = play.get('t_start', 0.0) + el
            img = self.frame_at(t_vid)
            if not len(self.frames): img[:] = (24, 20, 40)
            if SKELETON:
                pts, vis = self.ref_on_canvas(self.ref_pose_at(t_vid))
                if pts is not None: draw_skeleton(img, pts, vis, VOLT, thick=2, joint=3)
            kind = play.get('kind', 'part')
            if kind == 'preview':
                _text(img, 'WATCH FIRST', (14, 40), 0.9, VOLT, 2)
                return img
            beat = int(np.floor(el / ref.beat_s))
            if 0 <= beat < 4:
                _text(img, str(5 + beat), (0, 360), 5.0, VOLT, 8, center=True)
            elif beat >= 4 and beat - 4 < 8 * (play['k1'] - play['k0']):
                _text(img, str((beat - 4) % 8 + 1), (W - 80, 90) if len(self.frames) else (0, 360), 2.6 if len(self.frames) else 5.0, VOLT, 5, center=not len(self.frames))
                m = pose_match(user, self.ref_pose_at(t_vid))
                if m is not None: self.match = m if self.match is None else 0.7 * self.match + 0.3 * m
            label = 'FULL DANCE' if kind == 'full' else f'PART {play["k0"] + 1}/{n}'
            _text(img, label, (14, 40), 0.9, WHITE, 2)
            if SKELETON:
                self.live_panel(img, user, (W - 128, H - 228, 120, 170))
                self.meter(img, self.match)
            return img
        self.match = None
        state = st.get('state')
        base = self.frame_at(ref.first_beat_s + 8 * min(k, n - 1) * ref.beat_s)
        img = (base * 0.3).astype(np.uint8)
        if state in ('idle', 'preview'):
            _text(img, 'BRODIE DANCE', (0, 120), 1.3, VOLT, 3, center=True)
            _text(img, 'Watch the dance first', (0, 300), 0.8, WHITE, 2, center=True)
            return img
        if state == 'setup':
            _text(img, 'SET UP YOUR CAMERA', (0, 70), 1.0, VOLT, 2, center=True)
            for i, line in enumerate(['Prop the phone at knee height', 'Step back 2-3 m', 'Head to feet on screen']):
                _text(img, line, (0, 110 + 30 * i), 0.6, WHITE, 1, center=True)
            self.live_panel(img, user, (60, 220, 240, 340), 'LIVE TRACKING')
            ok = bool(st.get('visible'))
            _text(img, 'WHOLE BODY: OK' if ok else 'STEP BACK...', (0, 610), 0.85, VOLT if ok else ORANGE, 2, center=True)
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
            for i, line in enumerate(textwrap.wrap(steps, 26)[:7]):
                _text(img, line, (0, 230 + 34 * i), 0.72, WHITE, 2, center=True)
        if SKELETON: self.live_panel(img, user, (W - 128, H - 200, 120, 170))
        return img

    def meter(self, img, m):
        import cv2
        x, y, w, h = 14, H - 44, W - 160, 22
        cv2.rectangle(img, (x, y), (x + w, y + h), BLACK, -1)
        if m is not None:
            col = VOLT if m >= 0.7 else ORANGE if m >= 0.4 else RED
            cv2.rectangle(img, (x + 2, y + 2), (x + 2 + int((w - 4) * m), y + h - 2), col, -1)
        cv2.rectangle(img, (x, y), (x + w, y + h), WHITE, 1)
        _text(img, f'MATCH {int(round(m * 100))}%' if m is not None else 'MATCH --', (x, y - 8), 0.6, WHITE, 1)

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
