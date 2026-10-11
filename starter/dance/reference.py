"""The reference: the creator's dance as COCO-17 poses at 10 fps, plus where the beats are.

Make one per dance, once, from the TikTok video (or from Steven's YOLO keypoints) and save it under data/dances/<name>.npz.

From a video (uses the same MediaPipe pose model Brodie runs live, so reference and live poses match best):
  python -m starter.dance.reference --video data/dances/renegade.mp4 --name renegade --bpm 140 --first-beat 1.2

From YOLO-pose keypoints (a .npy of shape (T, 17, 3): x, y in pixels, confidence), with the fps they were extracted at:
  python -m starter.dance.reference --keypoints renegade_yolo.npy --kp-fps 30 --name renegade --bpm 140 --first-beat 1.2

--first-beat: the second in the video where count ONE of the dance falls (scrub the video and note it).
--steps: a text file, one line per chunk, describing the moves; Brodie says the line before teaching that chunk.
--video also saves the song (the video's audio track, 24 kHz mono): Brodie plays each chunk's music under the counts.
--audio FILE takes the song from another file instead (any format PyAV reads: mp3, m4a, wav, mp4).
--bpm: the song's tempo (search "<song> bpm"). A chunk is 8 beats; --chunks limits how many to teach (default: all that fit).
Check it:  python -m starter.dance.reference --show data/dances/renegade.npz
Score a video of yourself against it, no call needed (--first-beat = where count ONE falls in YOUR video):
  python -m starter.dance.reference --score data/tmp/me_dancing.mp4 --against renegade --first-beat 2.0
"""
import argparse, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
from starter.dance.keypoints import FPS, mediapipe_to_coco, resample  # noqa: E402

DANCES_DIR = os.path.join(ROOT, 'data', 'dances')
BEATS_PER_CHUNK = 8


class Reference:
    def __init__(self, path):
        z = np.load(path, allow_pickle=False)
        self.path = path
        self.name = str(z['name'])
        self.kps = z['kps'].astype(np.float32)          # (T, 17, 3) at FPS
        self.bpm = float(z['bpm'])
        self.first_beat_s = float(z['first_beat_s'])
        self.n_chunks = int(z['n_chunks'])
        self.beat_s = 60.0 / self.bpm
        self.audio = z['audio'].astype(np.int16) if 'audio' in z.files else None      # 24 kHz mono int16, aligned with kps (t = 0)
        self.steps = [str(x) for x in z['steps']] if 'steps' in z.files else []
        v = os.path.join(os.path.dirname(os.path.abspath(path)), f'{self.name}.mp4')
        self.video_path = v if os.path.exists(v) else None                          # the creator's video, shown to the caller

    def step(self, k):
        return self.steps[k] if k < len(self.steps) and self.steps[k].strip() else None

    @property
    def chunk_s(self):
        return BEATS_PER_CHUNK * self.beat_s

    def chunk(self, k):
        """The reference poses for chunk k (beats 8k .. 8k+7 after the first beat): (n, 17, 3)."""
        t0 = self.first_beat_s + k * self.chunk_s
        a = int(round(t0 * FPS)); b = int(round((t0 + self.chunk_s) * FPS))
        return self.kps[max(a, 0):min(b, len(self.kps))]


def load(name_or_path):
    p = name_or_path if name_or_path.endswith('.npz') else os.path.join(DANCES_DIR, f'{name_or_path}.npz')
    return Reference(p)


AUDIO_RATE = 24000                                       # Brodie's audio track rate (facetime.SAMPLE_RATE)


def audio_from_file(path, rate=AUDIO_RATE):
    """The audio track of `path` -> int16 mono at `rate` (PyAV, installed with Brodie). None when the file has no audio."""
    import av
    with av.open(path) as c:
        if not c.streams.audio: return None
        rs = av.AudioResampler(format='s16', layout='mono', rate=rate); out = []
        for frame in c.decode(audio=0):
            for f in rs.resample(frame): out.append(f.to_ndarray().reshape(-1))
        for f in rs.resample(None): out.append(f.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.int16) if out else None


def poses_from_video(path, variant='full'):
    """Every frame of `path` -> (times, (N, 17, 3)) with Brodie's MediaPipe pose model, frames resized like the live pipeline."""
    import cv2
    import facetime  # noqa: F401  (puts src/ on sys.path)
    from rtcoach.pose import Pose
    from facetime.pipeline import to_short_side
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    pose = Pose(variant, video=True)
    times, kps, i = [], [], 0
    while True:
        ok, bgr = cap.read()
        if not ok: break
        rgb = to_short_side(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        a, _ = pose(rgb, int(i * 1000 / fps))
        h, w = rgb.shape[:2]
        kps.append(mediapipe_to_coco(a, (w, h)) if a is not None else np.zeros((17, 3), np.float32))
        times.append(i / fps); i += 1
    pose.close(); cap.release()
    print(f'{path}: {i} frames at {fps:.1f} fps, pose found on {sum(1 for k in kps if k[:, 2].max() > 0)}')
    return np.array(times), np.array(kps, np.float32)


def main():
    ap = argparse.ArgumentParser(description='Make or inspect a dance reference (data/dances/<name>.npz).')
    ap.add_argument('--video'); ap.add_argument('--keypoints'); ap.add_argument('--kp-fps', type=float, default=30.0)
    ap.add_argument('--name'); ap.add_argument('--bpm', type=float); ap.add_argument('--first-beat', type=float, default=0.0)
    ap.add_argument('--chunks', type=int, default=None); ap.add_argument('--show')
    ap.add_argument('--score', metavar='VIDEO'); ap.add_argument('--against', metavar='DANCE')
    ap.add_argument('--steps', metavar='TXT'); ap.add_argument('--audio', metavar='FILE')
    a = ap.parse_args()
    if a.score:
        from starter.dance.judge import judge
        r = load(a.against); times, kps = poses_from_video(a.score); _, kps10 = resample(times, kps, FPS, t0=0.0)
        for k in range(r.n_chunks):
            t0 = a.first_beat + k * r.chunk_s
            u = kps10[int(round(t0 * FPS)):int(round((t0 + r.chunk_s) * FPS))]
            res = judge(u, r, k)
            print(f'chunk {k + 1}: {res["score"]:.0%} ({res["source"]}), worst {res.get("worst_part")}, {res.get("details", "")}')
        return
    if a.show:
        r = load(a.show)
        print(f'{r.name}: {len(r.kps)} frames ({len(r.kps) / FPS:.1f} s), {r.bpm:g} bpm, first beat {r.first_beat_s:g} s, '
              f'{r.n_chunks} chunks of {r.chunk_s:.2f} s')
        for k in range(r.n_chunks):
            c = r.chunk(k); seen = float((c[:, :, 2] > 0.3).mean()) if len(c) else 0.0
            print(f'  chunk {k + 1}: {len(c)} frames, keypoints seen {seen:.0%}; steps: {r.step(k) or "-"}')
        print(f'  music: {len(r.audio) / AUDIO_RATE:.1f} s' if r.audio is not None else '  music: none (counts only)')
        return
    if not (a.name and a.bpm and (a.video or a.keypoints)):
        ap.error('need --name, --bpm and --video or --keypoints')
    if a.video:
        times, kps = poses_from_video(a.video)
    else:
        kps = np.load(a.keypoints).astype(np.float32)
        if kps.ndim != 3 or kps.shape[1] != 17: sys.exit(f'expected (T, 17, 3) COCO keypoints, got {kps.shape}')
        if kps.shape[2] == 2: kps = np.concatenate([kps, np.ones(kps.shape[:2] + (1,), np.float32)], axis=2)
        times = np.arange(len(kps)) / a.kp_fps
    _, kps10 = resample(times, kps, FPS, t0=0.0)
    chunk_s = BEATS_PER_CHUNK * 60.0 / a.bpm
    fit = int((len(kps10) / FPS - a.first_beat) // chunk_s)
    n = min(a.chunks, fit) if a.chunks else fit
    if n < 1: sys.exit(f'not even one 8-beat chunk ({chunk_s:.2f} s) fits after the first beat: check --bpm and --first-beat')
    os.makedirs(DANCES_DIR, exist_ok=True)
    out = os.path.join(DANCES_DIR, f'{a.name}.npz')
    extra = {}
    src = a.audio or a.video
    if src:
        au = audio_from_file(src)
        if au is not None: extra['audio'] = au
        print(f'music from {src}: ' + (f'{len(au) / AUDIO_RATE:.1f} s' if au is not None else 'no audio track'))
    if a.steps:
        lines = [l.strip() for l in open(a.steps, encoding='utf-8') if l.strip()]
        extra['steps'] = np.array(lines[:n] + [''] * max(0, n - len(lines)))
    if a.video:                                               # keep the creator's video next to the reference: Brodie shows it
        import shutil
        dst = os.path.join(DANCES_DIR, f'{a.name}.mp4')
        if os.path.abspath(a.video) != os.path.abspath(dst): shutil.copyfile(a.video, dst)
    np.savez(out, name=a.name, kps=kps10, bpm=a.bpm, first_beat_s=a.first_beat, n_chunks=n, **extra)
    print(f'wrote {out}: {len(kps10)} frames at {FPS} fps, {n} chunks of 8 beats ({chunk_s:.2f} s each)')


if __name__ == '__main__':
    main()
