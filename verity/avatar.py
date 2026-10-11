"""The agent's face: a glossy 3D-shaded smiley on black (the look of the fan wiki's 3D -ity renders), drawn with numpy/cv2 and
published as a camera track. The mouth follows the output audio level (LevelTap wraps the session's audio output) and the eyes
blink every few seconds. Each character has its color and a face (FACES); set_variant() swaps both mid-call (Verity -> Cruelty).
The shaded sphere and the static parts of a face are rendered once per character (base); a frame only adds eyes and mouth.
  python -m verity.avatar                            # data/tmp/verity_faces.png: every character, mouth shut + open
"""
import asyncio, collections, functools, glob, os, random, sys, time
import cv2
import numpy as np
from livekit import rtc
from livekit.agents import voice

W, H, FPS = 360, 640, 15     # portrait, like a phone; small so a frame costs ~1 ms
SPRITES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'assets', '3d')


@functools.lru_cache(maxsize=None)
def sprites(key):
    """Rendered frames of the real 3D model for a character (verity/models.py): (idle, talk, creepy) RGBA lists (talk and creepy may
    be None), or None."""
    kind = lambda k: sorted(glob.glob(os.path.join(SPRITES, key, f'{k}_*.png')))
    idle = kind('idle')
    if not idle: return None
    load = lambda f: cv2.cvtColor(cv2.imread(f, cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGBA)
    return [load(f) for f in idle], [load(f) for f in kind('talk')] or None, [load(f) for f in kind('creepy')] or None
CX, CY, R = W // 2, H // 2, int(W * 0.42)
BLACK = (0, 0, 0, 255)
EYE_Y, EYE_DX = CY - R // 4, R // 3
WIKI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'assets', 'wiki')


def _read(name):
    """A wiki image (or GIF frames) as RGBA numpy arrays."""
    from PIL import Image, ImageSequence
    im = Image.open(os.path.join(WIKI, name))
    return [np.array(f.convert('RGBA')) for f in ImageSequence.Iterator(im)]


def _place(rgba, ref=None):
    """Scale an image so its ball (the opaque bounding box of ref, default itself) spans 2R, centered on (CX, CY), on a W x H canvas."""
    ys, xs = np.nonzero((ref if ref is not None else rgba)[..., 3] > 40)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    k = 2 * R / max(y1 - y0, x1 - x0)
    small = cv2.resize(rgba, (max(1, round(rgba.shape[1] * k)), max(1, round(rgba.shape[0] * k))), interpolation=cv2.INTER_AREA)
    ox, oy = round(CX - (x0 + x1) / 2 * k), round(CY - (y0 + y1) / 2 * k)
    out = np.zeros((H, W, 4), np.uint8)
    sx0, sy0 = max(0, -ox), max(0, -oy); dx0, dy0 = max(0, ox), max(0, oy)
    w, h = min(small.shape[1] - sx0, W - dx0), min(small.shape[0] - sy0, H - dy0)
    out[dy0:dy0 + h, dx0:dx0 + w] = small[sy0:sy0 + h, sx0:sx0 + w]
    return out


@functools.lru_cache(maxsize=None)
def real_frames(key):
    """The real Verity from the series (wiki images): idle smile, open mouth, creepy grin, the spin. Cruelty uses its own art; our
    other characters are the real Verity recolored, plus their gear. None if the images are missing."""
    from verity.variants import VARIANTS
    from verity.models import recolor
    try:
        if key == 'cruelty':
            angry = _place(_read('cruelty.webp')[0])
            return {'idle': angry, 'talk': _place(_read('cruelty1.webp')[0]), 'creepy': angry, 'spin': []}
        spin = _read('verityspin.gif')
        f = {'idle': _place(spin[0]), 'talk': _place(_read('verityopen.webp')[0]), 'creepy': _place(_read('veritycreepyopen.webp')[0]),
             'spin': [_place(x, spin[0]) for x in spin]}
    except (OSError, ValueError):
        return None
    if key != 'verity':
        v = VARIANTS[key]; static, _ = FACES[v.get('face', 'plain')]
        def dress(img):
            img = recolor(img, v['color'])
            if static: static(img, v['color'])
            return img
        f = {k: [dress(x) for x in val] if isinstance(val, list) else dress(val) for k, val in f.items()}
    return f


def sphere(color):
    """RGBA: a shaded ball (key light top-left, specular highlight, anti-aliased edge) on transparent black."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dx, dy = (xx - CX) / R, (yy - CY) / R
    d2 = dx * dx + dy * dy
    nz = np.sqrt(np.clip(1 - d2, 0, 1))
    light = np.array([-0.45, -0.55, 0.70], np.float32); light /= np.linalg.norm(light)
    diffuse = np.clip(dx * light[0] + dy * light[1] + nz * light[2], 0, 1)
    shade = 0.32 + 0.78 * diffuse
    spec = np.exp(-((dx + 0.36) ** 2 + (dy + 0.42) ** 2) / 0.018) * 0.85
    rgb = np.clip(np.array(color, np.float32)[None, None, :] * shade[..., None] + 255 * spec[..., None], 0, 255)
    alpha = np.clip((1 - np.sqrt(d2)) * R * 1.5, 0, 1)
    img = np.zeros((H, W, 4), np.uint8)
    img[..., :3] = rgb.astype(np.uint8); img[..., 3] = (alpha * 255).astype(np.uint8)
    return img


def _inside(img, layer):
    """Copy layer onto img where the ball is."""
    m = img[..., 3] > 0
    img[m, :3] = layer[m, :3]


# static parts, baked into the base once per character
def split_base(img, color):
    layer = img.copy(); right = layer[:, CX:, :3]
    face = right.max(axis=2, keepdims=True) > 90                          # lighten the face colour only, keep eyes and mouth black
    layer[:, CX:, :3] = np.where(face, (right.astype(np.float32) * .45 + 255 * .55).astype(np.uint8), right)
    _inside(img, layer)
    cv2.putText(img, '+', (CX - R + 18, CY + 20), cv2.FONT_HERSHEY_SIMPLEX, 1.6, BLACK, 5, cv2.LINE_AA)
    cv2.putText(img, '-', (CX + R - 52, CY + 16), cv2.FONT_HERSHEY_SIMPLEX, 1.8, BLACK, 6, cv2.LINE_AA)


def sweatband_base(img, color):
    layer = img.copy(); top = CY - int(R * .78)
    cv2.rectangle(layer, (0, top), (W, top + 34), (245, 245, 245, 255), -1)
    cv2.rectangle(layer, (0, top + 13), (W, top + 21), (30, 120, 255, 255), -1)
    _inside(img, layer)


def headphones_base(img, color):
    cv2.ellipse(img, (CX, CY), (R + 22, R + 22), 0, 195, 345, (40, 40, 40, 255), 16, cv2.LINE_AA)
    for sx in (-1, 1):
        cv2.ellipse(img, (CX + sx * (R + 12), CY - 6), (22, 44), 0, 0, 360, (40, 40, 40, 255), -1, cv2.LINE_AA)
        cv2.ellipse(img, (CX + sx * (R + 12), CY - 6), (12, 30), 0, 0, 360, tuple(color) + (255,), -1, cv2.LINE_AA)
    nx, ny, white = CX + R - 4, CY - R - 4, (255, 255, 255, 255)                                    # a music note
    cv2.ellipse(img, (nx, ny), (13, 10), -20, 0, 360, white, -1, cv2.LINE_AA)
    cv2.line(img, (nx + 11, ny - 2), (nx + 11, ny - 52), white, 5, cv2.LINE_AA)
    cv2.line(img, (nx + 11, ny - 52), (nx + 30, ny - 36), white, 5, cv2.LINE_AA)


# overlays, drawn after the eyes every frame
def sleepy_over(img, base, eyes):
    for ex, ey, ew, eh in eyes:                                          # heavy half-closed lids in the ball's own shading
        lid = tuple(int(c) for c in base[ey - eh - 6, ex, :3]) + (255,)
        cv2.ellipse(img, (ex, ey), (ew + 4, eh + 4), 0, 180, 360, lid, -1, cv2.LINE_AA)
        cv2.line(img, (ex - ew - 6, ey), (ex + ew + 6, ey), BLACK, 6, cv2.LINE_AA)


def hyped_over(img, base, eyes):
    for ex, ey, ew, eh in eyes:                                          # eyebrows up: way too excited
        cv2.line(img, (ex - 22, ey - eh - 8), (ex + 22, ey - eh - 22), BLACK, 7, cv2.LINE_AA)


def angry_over(img, base, eyes):
    for i, (ex, ey, ew, eh) in enumerate(eyes):                          # brows slanting down to the middle
        sx = 1 if i == 0 else -1
        cv2.line(img, (ex - sx * 26, ey - eh - 26), (ex + sx * 22, ey - eh - 6), BLACK, 9, cv2.LINE_AA)


FACES = {'plain': (None, None), 'headphones': (headphones_base, None), 'sleepy': (None, sleepy_over), 'split': (split_base, None),
         'sweatband': (sweatband_base, hyped_over), 'angry': (None, angry_over)}


class Avatar:
    def __init__(self, variant):
        self.set_variant(variant)
        self.level = 0.0; self.levels = collections.deque(); self.next_blink = time.monotonic() + 2
        self.source = rtc.VideoSource(W, H)

    def set_variant(self, v):
        self.sprites = sprites(v['name'].lower())                    # real 3D model frames when rendered
        self.real = None if self.sprites else real_frames(v['name'].lower())   # else the real Verity images, else the drawn ball
        self.mood_until = 0.0
        static, self.over = FACES[v.get('face', 'plain')]
        base = sphere(v['color'])
        if static: static(base, v['color'])
        self.base = base

    def render(self, now):
        """RGBA numpy frame (transparent outside the ball and its gear)."""
        self.level *= 0.6                                                 # decay between audio frames
        while self.levels and self.levels[0][0] <= now: self.level = max(self.level, self.levels.popleft()[1])
        if self.sprites:
            idle, talk, creepy = self.sprites
            frames = creepy if creepy and now < self.mood_until else talk if talk and self.level > 0.04 else idle   # creepy: a strike
            return frames[int(now * 8) % len(frames)].copy()             # 8 fps turn/bob cycle, mouth open while it talks
        if self.real:
            f = self.real
            spin_t = now % 14.0                                             # every 14 s it does its spin, like in the series
            if now < self.mood_until: img = f['creepy']
            elif f['spin'] and spin_t < len(f['spin']) / 12 and self.level < 0.04: img = f['spin'][int(spin_t * 12)]
            elif self.level > 0.04: img = f['talk']
            else: img = f['idle']
            return np.roll(img, int(6 * np.sin(now * 2.2)), axis=0)        # a gentle float
        img = self.base.copy()
        blink = now >= self.next_blink
        if now >= self.next_blink + 0.15: self.next_blink = now + random.uniform(2.5, 5.0)
        eyes = [(ex, EYE_Y, R // 10, 2 if blink else R // 6) for ex in (CX - EYE_DX, CX + EYE_DX)]
        for ex, ey, ew, eh in eyes:
            cv2.ellipse(img, (ex, ey), (ew, eh), 0, 0, 360, BLACK, -1, cv2.LINE_AA)
            if not blink: cv2.circle(img, (ex - ew // 3, ey - eh // 2), max(2, ew // 3), (255, 255, 255, 255), -1, cv2.LINE_AA)   # eye glint
        if self.over: self.over(img, self.base, eyes)
        open_h = int(min(1.0, self.level * 4) * R * 0.35)
        if open_h > 3:
            cv2.ellipse(img, (CX, CY + R // 3), (R // 3, open_h), 0, 0, 180, (40, 8, 8, 255), -1, cv2.LINE_AA)
            cv2.ellipse(img, (CX, CY + R // 3 + open_h * 2 // 3), (R // 6, max(2, open_h // 3)), 0, 180, 360, (225, 105, 120, 255), -1, cv2.LINE_AA)
        else:
            cv2.ellipse(img, (CX, CY + R // 5), (R // 2, R // 3), 0, 20, 160, BLACK, max(4, R // 18), cv2.LINE_AA)
        return img

    def flash(self, seconds=4.0):
        """The creepy grin for a few seconds (a strike)."""
        self.mood_until = time.monotonic() + seconds

    def draw(self, now):
        img = self.render(now); img[..., 3] = 255                         # the call shows it on black
        return rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, img.tobytes())

    async def publish(self, room):
        track = rtc.LocalVideoTrack.create_video_track('verity-face', self.source)
        await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
        return asyncio.create_task(self._loop())

    async def _loop(self):
        while True:
            self.source.capture_frame(self.draw(time.monotonic()))
            await asyncio.sleep(1 / FPS)


class LevelTap(voice.io.AudioOutput):
    """Pass-through audio output that feeds the avatar's mouth with the RMS of every frame the agent speaks. The model's audio arrives
    in bursts faster than real time, so each level is queued at the time its frame will actually play (the sink plays back to back)."""
    def __init__(self, avatar, next_in_chain):
        super().__init__(label='LevelTap', next_in_chain=next_in_chain, capabilities=voice.io.AudioOutputCapabilities(pause=True))
        self.avatar = avatar; self.t_end = 0.0

    @property
    def sample_rate(self):
        return self.next_in_chain.sample_rate

    async def capture_frame(self, frame):
        pcm = np.frombuffer(frame.data, np.int16).astype(np.float32) / 32768
        t = max(time.monotonic(), self.t_end); self.t_end = t + frame.duration
        self.avatar.levels.append((t, float(np.sqrt(np.mean(pcm * pcm))) if pcm.size else 0.0))
        await self.next_in_chain.capture_frame(frame)
        await super().capture_frame(frame)

    def flush(self):
        super().flush(); self.next_in_chain.flush()

    def clear_buffer(self):                                               # interrupted: the queued audio will never play
        self.avatar.levels.clear(); self.t_end = 0.0
        self.next_in_chain.clear_buffer()


def still(v, talking=False):
    """One frame of a character, no video source needed (for exports and the check sheet)."""
    a = Avatar.__new__(Avatar); a.set_variant(v); a.next_blink = 1e18; a.level = 0.0
    a.levels = collections.deque([(0, 1.0)] if talking else [])
    return a.render(0)


if __name__ == '__main__':
    import os
    from verity.variants import VARIANTS
    if len(sys.argv) > 2 and sys.argv[1] == '--export':
        os.makedirs(sys.argv[2], exist_ok=True)
        top, size = CY - R - 70, 2 * R + 100                             # square around the ball and its gear
        for k, v in VARIANTS.items():
            for talk in (False, True):
                img = np.pad(still(v, talk), ((0, 0), (size, size), (0, 0)))[top:top + size, CX + size // 2:CX + size + size // 2]
                cv2.imwrite(os.path.join(sys.argv[2], f'{k}{"-talk" if talk else ""}.png'), cv2.cvtColor(img, cv2.COLOR_RGBA2BGRA))
        print('exported', len(VARIANTS) * 2, 'images to', sys.argv[2])
    else:
        frames = [still(v, t) for v in VARIANTS.values() for t in (False, True)]
        for f in frames: f[..., 3] = 255
        cv2.imwrite('data/tmp/verity_faces.png', cv2.cvtColor(np.hstack(frames), cv2.COLOR_RGBA2BGRA)); print('wrote data/tmp/verity_faces.png')
