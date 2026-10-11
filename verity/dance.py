"""Dancity's eyes: Brodie's pose model on the caller's camera, judged with the team's dance judge (starter/dance, Bulat's branch).

verity/agent.py calls DanceDetector().feed(frame) at about 10 fps in a worker thread, one frame at a time, on Dancity calls only.
Every WINDOW_S seconds of visible body it returns one result, else None:
  - with a reference dance (VERITY_DANCE=<name>, data/dances/<name>.npz made by `python -m starter.dance.reference`), the window is
    judged against the next 8-count with starter.dance.judge.judge(), which runs Steven's model (model_judge) when it is in, else
    the angle comparison: {"move": <the step text or "part N">, "score": 0..10, "tip": <body part furthest off>};
  - without one, the movement itself is rated (how much and how big the arms and legs move): {"move": "freestyle", "score": 1..10}.
Standing still returns nothing, so Dancity only reacts when the caller actually dances.
"""
import os, time
import numpy as np
import facetime                                            # noqa: F401  (puts src/ on sys.path for rtcoach)

WINDOW_S = 4.0
MIN_FRAMES = 15                                            # need this many frames with a body in a window to judge it
STILL = 0.9                                                # mean limb speed (torso lengths/s) below which it isn't dancing (pose jitter ~0.4-0.8)
BIG = 10.0                                                 # speed that scores 10


class DanceDetector:
    def __init__(self, reference=None):
        self.pose = None; self.broken = False
        self.buf = []; self.t0 = None; self.chunk = 0
        self.ref = None
        name = reference if reference is not None else os.environ.get('VERITY_DANCE', '')
        if name:
            try:
                from starter.dance.reference import load
                self.ref = load(name)
            except Exception as e:
                print(f'[dance] no reference {name!r} ({e}); rating freestyle', flush=True)

    def feed(self, frame):
        """frame: livekit rtc.VideoFrame RGBA. Returns a result dict every WINDOW_S of dancing, else None."""
        if self.broken: return None
        if self.pose is None:
            try:
                from rtcoach.pose import Pose
                self.pose = Pose('lite')
            except Exception as e:                         # no pose model on this machine: dance detection off, the call goes on
                print(f'[dance] pose model unavailable ({e}); dance detection off', flush=True)
                self.broken = True; return None
        from starter.dance.keypoints import mediapipe_to_coco
        rgb = np.frombuffer(frame.data, np.uint8).reshape(frame.height, frame.width, 4)[..., :3]
        now = time.monotonic()
        kp33, _ = self.pose(rgb, now * 1000)
        if self.t0 is None: self.t0 = now
        if kp33 is not None: self.buf.append(mediapipe_to_coco(kp33, (frame.width, frame.height)))
        if now - self.t0 < WINDOW_S: return None
        kps, self.buf, self.t0 = self.buf, [], now
        if len(kps) < MIN_FRAMES: return None
        return self.score(np.stack(kps))

    def score(self, kps):
        energy = movement(kps)
        if energy < STILL: return None
        if self.ref is not None:
            from starter.dance.judge import judge
            k = self.chunk % self.ref.n_chunks; self.chunk += 1
            res = judge(kps, self.ref, k)
            return {'move': self.ref.step(k) or f'part {k + 1}', 'score': int(round(res['score'] * 10)), 'tip': res.get('worst_part')}
        return {'move': 'freestyle', 'score': int(np.clip(round(2 + 8 * np.log(energy / STILL) / np.log(BIG / STILL)), 1, 10))}   # log scale


def movement(kps):
    """Mean speed of wrists and ankles in torso lengths per second (10 fps poses), counting only confident points."""
    torso = np.linalg.norm((kps[:, 5, :2] + kps[:, 6, :2]) / 2 - (kps[:, 11, :2] + kps[:, 12, :2]) / 2, axis=1)
    scale = np.median(torso[torso > 0]) if (torso > 0).any() else 0
    if scale <= 0: return 0.0
    limbs = kps[:, [9, 10, 15, 16]]
    ok = (limbs[1:, :, 2] > 0.3) & (limbs[:-1, :, 2] > 0.3)
    if not ok.any(): return 0.0
    speed = np.linalg.norm(np.diff(limbs[..., :2], axis=0), axis=2) / scale * 10
    return float(speed[ok].mean())


if __name__ == '__main__':   # python -m verity.dance [video]: run the detector over a recording, print what Dancity would hear
    import sys, cv2
    class F:                                               # stands in for rtc.VideoFrame
        def __init__(self, rgba): self.height, self.width = rgba.shape[:2]; self.data = rgba.tobytes()
    d = DanceDetector(); cap = cv2.VideoCapture(sys.argv[1] if len(sys.argv) > 1 else 'data/tmp/mac_handoff/crops/live.mp4')
    fps = cap.get(cv2.CAP_PROP_FPS) or 30; step = max(1, round(fps / 10)); i = 0; t = 0.0
    real = time.monotonic; time.monotonic = lambda: t     # play the clip at 10 fps of video time
    while True:
        ok, img = cap.read()
        if not ok: break
        i += 1
        if i % step: continue
        t += 0.1
        r = d.feed(F(cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)))
        if r: print(f'{t:5.1f}s', r)
