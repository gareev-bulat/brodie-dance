#!/usr/bin/env python3
"""Extract MediaPipe landmarks from a LEARNER TAKE exactly the way the live coach sees them.

The live pipeline (facetime/pipeline.py:68, 72-78, 162-172) takes frames at 10 fps, shrinks them to a short side of
288 px (aspect kept) and runs rtcoach.pose.Pose('full', video=True) with monotonically increasing millisecond
timestamps. This script replays that on any phone recording (mp4/mov, any fps, portrait or landscape, rotation
metadata honoured) so the training data lives in the same domain as the demo. Never YOLO here: the reference clips
go through scripts/extract_pose_yolo.py, learner takes go through this (docs/DANCE_TRAINING_PLAYBOOK.md section 4).

  <out>.npz   per-frame arrays (keys below)
  <out>.json  run summary: frames, ok fraction, per-joint visibility fraction, processed size, timings
  <out>.pose.mp4  (--video) the processed 10 fps frames with the 17 COCO joints drawn

npz keys (N = frames at 10 fps, zeros / NaN where MediaPipe found no pose)
  img      (N,33,5) float32 MediaPipe image landmarks [x, y, z, visibility, presence], x,y normalised 0..1 of the
           processed frame (rtcoach/pose.py:15); exactly what facetime.perception passes to the judge
  world    (N,33,3) float32 MediaPipe world landmarks (metres, hip-centred)
  ok       (N,) bool   a pose was found (Pose returned arrays, not (None, None): pose.py:14)
  t        (N,) float  take time in seconds = frame / fps (10 fps)
  fps=10, width, height   of the PROCESSED frame (short side 288)
  src_fps, src_frames, src_width, src_height, rotation, t0_src   source clip (display orientation, deg clockwise)
  src_frame (N,) int, src_t (N,) float   which source frame fed output frame j and its source timestamp
  -- COCO-17 projection, same keys and semantics as scripts/extract_pose_yolo.py so either file loads the same way --
  xy       (N,17,2) float32 pixels of the processed frame, NaN where visibility < kp_conf (0.3) or not ok
  xyn      (N,17,2) xy / [width, height], NaN likewise
  xy_raw   (N,17,2) unmasked pixels (0 where not ok); off-frame joints lie outside the image
  conf     (N,17) = MediaPipe visibility, vis (N,17) bool = ok[:, None] & (conf >= kp_conf)
  box      (N,4) xyxy tight box of the visible joints (NaN when none; no detector box in MediaPipe)
  names (17,), skeleton (19,2), flip_idx (17,), coco2mp (17,), mirrored, extractor='mediapipe', variant, kp_conf

COCO-17 order: 0 nose, 1 left_eye, 2 right_eye, 3 left_ear, 4 right_ear, 5 left_shoulder, 6 right_shoulder, 7 left_elbow,
8 right_elbow, 9 left_wrist, 10 right_wrist, 11 left_hip, 12 right_hip, 13 left_knee, 14 right_knee, 15 left_ankle,
16 right_ankle = img[:, COCO2MP] with COCO2MP = [0,2,5,7,8,11,12,13,14,15,16,23,24,25,26,27,28] (DANCE_MODEL_INTERFACE.md
section 2). MediaPipe labels anatomically on the un-mirrored image like YOLO: "left_*" is on image-right for a person
facing the camera. --mirrored is metadata only (a selfie recording that was flipped); phone takes default to False.

One person only: Pose runs with num_poses=1 (pose.py:8) and keeps whichever person its detector scores highest. On the
two-dancer josh_king_o reference clip that is the full-body BACKGROUND dancer (YOLO track 1), not the choreo's front
dancer (track 2): keep reference clips on the YOLO extractor and record learner takes alone in frame.

Resampling: output frame j wants source time t0 + j/10 and takes the decoded frame nearest to it (every 3rd frame of
a 30 fps clip, every frame of a 10 fps clip). Constant frame rate clips use frame_index / r_frame_rate as the clock;
variable frame rate clips (r_frame_rate != avg_frame_rate) use the decoder's presentation timestamps (--ts to force).

Usage (inside WSL, the brodie venv; numpy, cv2, mediapipe only, no torch):
  ~/.venvs/brodie/bin/python scripts/extract_landmarks.py data/dance/raw/take.mov --video
  ~/.venvs/brodie/bin/python scripts/extract_landmarks.py take.mp4 --out data/dance/takes/renegade__steven__01.npz
  ~/.venvs/brodie/bin/python scripts/extract_landmarks.py take.mp4 --start 2.0 --end 14.5      # trim (source seconds)
Default output: data/dance/takes/<stem>[_tag].npz (+ .json, + .pose.mp4 with --video). Load with
  d = np.load(path); d['img'], d['ok'], d['xy'] ...   (no pickle needed)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import warnings
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, 'src')):          # scripts/test_perception.py:13-14; facetime/__init__.py:11-14
    if _p not in sys.path:
        sys.path.insert(0, _p)
from rtcoach.pose import Pose                           # noqa: E402  (models/pose_landmarker_<variant>.task, pose.py:4-7)

SHORT_SIDE, FPS = 288, 10                               # facetime/pipeline.py:67-68
COCO2MP = [0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]
# Names, skeleton, flip index and colours: scripts/extract_pose_yolo.py:59-72, so both npz flavours draw and flip alike.
NAMES = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder", "left_elbow",
         "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle",
         "right_ankle"]
SKELETON = [(15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11), (6, 12), (5, 6), (5, 7), (6, 8), (7, 9), (8, 10),
            (1, 2), (0, 1), (0, 2), (1, 3), (2, 4), (3, 5), (4, 6)]
FLIP_IDX = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]
LEFT = {1, 3, 5, 7, 9, 11, 13, 15}
COL_LEFT, COL_RIGHT, COL_MID = (0, 200, 255), (255, 120, 0), (255, 255, 255)   # BGR
ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def to_short_side(rgb, short=SHORT_SIDE):
    """Verbatim copy of facetime/pipeline.py:72-78 (kept local so this script does not import torch via the pipeline)."""
    h, w = rgb.shape[:2]
    s = min(h, w)
    if s == short:
        return np.ascontiguousarray(rgb)
    f = short / s
    return cv2.resize(rgb, (max(1, round(w * f)), max(1, round(h * f))), interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)


# ----------------------------------------------------------------------------- input
def probe(video):
    """ffprobe: stored size, rotation (degrees clockwise needed for display), frame rates, frame count, VFR flag."""
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        sys.exit('ffprobe not on PATH (apt install ffmpeg)')
    r = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,duration:stream_tags=rotate:stream_side_data=rotation',
                        '-of', 'json', video], capture_output=True, text=True)
    if r.returncode != 0 or not json.loads(r.stdout or '{}').get('streams'):
        sys.exit(f'ffprobe found no video stream in {video}: {r.stderr.strip()}')
    s = json.loads(r.stdout)['streams'][0]
    rf = float(Fraction(s.get('r_frame_rate', '0/1'))) if s.get('r_frame_rate', '0/1') != '0/0' else 0.0
    af = float(Fraction(s.get('avg_frame_rate', '0/1'))) if s.get('avg_frame_rate', '0/1') != '0/0' else 0.0
    rot = 0
    if str(s.get('tags', {}).get('rotate', '')).strip():
        rot = int(round(float(s['tags']['rotate'])))                       # legacy tag: clockwise degrees
    else:
        for sd in s.get('side_data_list', []):
            if 'rotation' in sd:
                rot = -int(round(float(sd['rotation'])))                   # display matrix: counter-clockwise degrees
    rot %= 360
    if rot not in ROTATE and rot != 0:
        print(f'warning: rotation {rot} deg is not a multiple of 90; ignoring it', flush=True)
        rot = 0
    nb = int(s['nb_frames']) if str(s.get('nb_frames', '')).isdigit() else 0
    return dict(width=int(s['width']), height=int(s['height']), rotation=rot, r_fps=rf, avg_fps=af, nb_frames=nb,
                duration=float(s.get('duration') or 0.0), vfr=bool(rf and af and abs(rf - af) / max(rf, af) > 0.005))


class Frames:
    """Sequential cv2 decode -> (index, source_time_s, upright BGR frame). OpenCV applies the rotation metadata itself when
    CAP_PROP_ORIENTATION_AUTO works; the first frame's shape tells whether it did, otherwise the frame is rotated here, so
    the output is always in display orientation (what the phone user saw)."""

    def __init__(self, video, meta, ts_mode):
        self.cap = cv2.VideoCapture(video)
        if not self.cap.isOpened():
            sys.exit(f'cannot open {video}')
        try:
            self.cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
        except Exception:
            pass
        self.rot, self.fix, self.i = meta['rotation'], None, -1
        self.fps = meta['r_fps'] or meta['avg_fps']
        self.ts_mode = 'pts' if (ts_mode == 'pts' or not self.fps) else 'index'
        self.exp_wh = (meta['height'], meta['width']) if self.rot in (90, 270) else (meta['width'], meta['height'])
        self.wh = None

    def _decide_fix(self, frame):
        h, w = frame.shape[:2]
        if not self.rot:
            return
        if (w, h) != self.exp_wh:                                   # cv2 left the stored orientation
            self.fix = ROTATE[self.rot]
        elif self.rot == 180 or w == h:                             # shape cannot tell: ask the backend
            auto = self.cap.get(cv2.CAP_PROP_ORIENTATION_AUTO)
            if not auto:
                self.fix = ROTATE[self.rot]

    def read(self):
        got, f = self.cap.read()
        if not got:
            return None
        self.i += 1
        pos_ms = self.cap.get(cv2.CAP_PROP_POS_MSEC)
        if self.i == 0:
            self._decide_fix(f)
        if self.fix is not None:
            f = cv2.rotate(f, self.fix)
        if self.i == 0:
            self.wh = (f.shape[1], f.shape[0])
            if self.wh != self.exp_wh:
                print(f'WARNING: first frame is {self.wh[0]}x{self.wh[1]} but ffprobe implies {self.exp_wh[0]}x{self.exp_wh[1]} '
                      f'(rotation {self.rot}); check the orientation of the output', flush=True)
        ts = pos_ms / 1000.0 if self.ts_mode == 'pts' else self.i / self.fps
        return self.i, ts, f

    def close(self):
        self.cap.release()


def resampled(frames, out_fps, start, end, max_frames):
    """Yield (j, src_index, src_time, frame): for each output time t0 + j/out_fps the decoded frame nearest in time.
    t0 = the first kept source frame (>= start). Frames after `end` (source seconds) are not read."""
    eps = 1e-6
    prev = None
    j = 0
    t0 = None
    while True:
        item = frames.read()
        if item is None:
            break
        i, ts, f = item
        if ts < start - eps:
            continue
        if end is not None and ts > end + eps:
            break
        if prev is None:
            t0 = ts
            prev = item
            continue
        while t0 + j / out_fps <= ts + eps:
            tau = t0 + j / out_fps
            pick = item if abs(tau - ts) < abs(tau - prev[1]) else prev
            yield j, pick[0], pick[1], pick[2]
            j += 1
            if max_frames and j >= max_frames:
                return
        prev = item
    if prev is not None and j == 0:                                 # a single frame
        yield 0, prev[0], prev[1], prev[2]


# ----------------------------------------------------------------------------- output
def draw(bgr, xy, vis, label):
    for i, j in SKELETON:
        if vis[i] and vis[j]:
            col = COL_LEFT if (i in LEFT and j in LEFT) else COL_RIGHT if (i not in LEFT and j not in LEFT) else COL_MID
            cv2.line(bgr, tuple(xy[i].astype(int)), tuple(xy[j].astype(int)), col, 2, cv2.LINE_AA)
    for k in range(17):
        if vis[k]:
            cv2.circle(bgr, tuple(xy[k].astype(int)), 3, COL_LEFT if k in LEFT else COL_RIGHT, -1, cv2.LINE_AA)
    cv2.putText(bgr, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(bgr, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)


class VideoOut:
    """h264 via an ffmpeg pipe when available, else cv2 mp4v. Frames are the processed BGR frames at out_fps."""

    def __init__(self, path, w, h, fps):
        self.proc = self.writer = None
        self.err = None
        self.n = 0
        ffmpeg = shutil.which('ffmpeg')
        if ffmpeg:
            cmd = [ffmpeg, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{w}x{h}', '-r', str(fps),
                   '-i', '-', '-an', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                   '-preset', 'fast', '-crf', '20', str(path)]
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        else:
            self.writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))

    def write(self, bgr):
        if self.err:
            return
        try:
            if self.proc:
                self.proc.stdin.write(np.ascontiguousarray(bgr).tobytes())
            else:
                self.writer.write(bgr)
            self.n += 1
        except BrokenPipeError:
            self.err = 'ffmpeg closed the pipe'

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
            stderr = self.proc.stderr.read().decode(errors='replace').strip()
            if self.proc.wait() != 0:
                self.err = self.err or f'ffmpeg failed: {stderr[-400:]}'
        elif self.writer is not None:
            self.writer.release()
        return self.n, self.err


def longest_gap(ok):
    best = run = 0
    for v in ok:
        run = 0 if v else run + 1
        best = max(best, run)
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('video')
    ap.add_argument('--out', default='', help='output .npz path, or a directory (default data/dance/takes/<stem>[_tag].npz)')
    ap.add_argument('--tag', default='', help="suffix for the output stem, e.g. 'take2' -> <stem>_take2.*")
    ap.add_argument('--variant', default='full', choices=['lite', 'full', 'heavy'], help='MediaPipe pose model (live: full)')
    ap.add_argument('--fps', type=float, default=FPS, help='output frame rate (live: 10)')
    ap.add_argument('--short', type=int, default=SHORT_SIDE, help='processed short side in px (live: 288)')
    ap.add_argument('--kp-conf', type=float, default=0.3, help='visibility below this -> NaN in xy, vis=False (visibility.py:17)')
    ap.add_argument('--mirrored', action='store_true', help='the clip is a mirrored selfie recording (metadata only)')
    ap.add_argument('--start', type=float, default=0.0, help='first source second to keep (take time 0)')
    ap.add_argument('--end', type=float, default=None, help='last source second to keep')
    ap.add_argument('--max-frames', type=int, default=0, help='stop after this many OUTPUT frames (quick test)')
    ap.add_argument('--ts', default='auto', choices=['auto', 'index', 'pts'],
                    help='source clock: frame_index/r_frame_rate or decoder timestamps (auto: pts only for VFR clips)')
    ap.add_argument('--video', dest='annotate', action='store_true', help='also write <out>.pose.mp4 with the 17 joints drawn')
    a = ap.parse_args()

    video = str(Path(a.video))
    stem = Path(video).stem + (f'_{a.tag}' if a.tag else '')
    out = Path(a.out) if a.out else Path(ROOT) / 'data' / 'dance' / 'takes'
    npz_path = out if out.suffix == '.npz' else out / f'{stem}.npz'
    npz_path.parent.mkdir(parents=True, exist_ok=True)
    base = npz_path.with_suffix('')
    json_path, video_path = base.with_suffix('.json'), (base.parent / f'{base.name}.pose.mp4' if a.annotate else None)

    meta = probe(video)
    ts_mode = a.ts if a.ts != 'auto' else ('pts' if meta['vfr'] else 'index')
    print(f"{video}: stored {meta['width']}x{meta['height']}, rotation {meta['rotation']}, r {meta['r_fps']:.3f} / avg "
          f"{meta['avg_fps']:.3f} fps, ~{meta['nb_frames']} frames, {meta['duration']:.1f} s"
          f"{' (VFR)' if meta['vfr'] else ''}; clock = {ts_mode}", flush=True)

    frames = Frames(video, meta, ts_mode)
    pose = Pose(a.variant, video=True)
    img, world, ok, src_frame, src_t, pose_ms = [], [], [], [], [], []
    vout, last_ms, W, H = None, -1, None, None
    t_start = time.time()
    for j, si, st, bgr in resampled(frames, a.fps, a.start, a.end, a.max_frames):
        rgb = to_short_side(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), a.short)
        if W is None:
            H, W = rgb.shape[:2]
            if a.annotate:
                vout = VideoOut(video_path, W, H, a.fps)
        ms = int(j * 1000.0 / a.fps)                                 # pipeline.py:167-170: int ms, strictly increasing
        if ms <= last_ms:
            ms = last_ms + 1
        last_ms = ms
        t = time.perf_counter()
        a33, w33 = pose(rgb, ms)
        pose_ms.append((time.perf_counter() - t) * 1000)
        found = a33 is not None
        img.append(a33 if found else np.zeros((33, 5), np.float32))
        world.append(w33 if found else np.zeros((33, 3), np.float32))
        ok.append(found)
        src_frame.append(si)
        src_t.append(st)
        if vout is not None:
            frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            if found:
                k = a33[COCO2MP]
                draw(frame, k[:, :2] * np.array([W, H], np.float32), k[:, 3] >= a.kp_conf, f'{j}  {j / a.fps:5.2f}s')
            else:
                draw(frame, np.zeros((17, 2)), np.zeros(17, bool), f'{j}  {j / a.fps:5.2f}s  no pose')
            vout.write(frame)
        if (j + 1) % 200 == 0:
            print(f'  {j + 1} frames, {(j + 1) / (time.time() - t_start):.1f} out-fps', flush=True)
    frames.close()
    pose.close()
    N = len(ok)
    if N == 0:
        sys.exit(f'{video}: no frames decoded (start/end outside the clip?)')
    if frames.i + 1 != meta['nb_frames'] and meta['nb_frames'] and a.end is None and not a.max_frames:
        print(f"note: decoded {frames.i + 1} source frames, container says {meta['nb_frames']}", flush=True)

    img = np.stack(img).astype(np.float32)
    world = np.stack(world).astype(np.float32)
    ok = np.array(ok, bool)
    src_frame = np.array(src_frame, np.int64)
    src_t = np.array(src_t, np.float64)
    t = np.arange(N) / a.fps
    k17 = img[:, COCO2MP]                                            # (N,17,5)
    xy_raw = (k17[:, :, :2] * np.array([W, H], np.float32)).astype(np.float32)
    conf = k17[:, :, 3].astype(np.float32)
    vis = ok[:, None] & (conf >= a.kp_conf)
    xy = np.where(vis[:, :, None], xy_raw, np.nan).astype(np.float32)
    xyn = xy / np.array([W, H], np.float32)
    with np.errstate(all='ignore'), warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)                  # all-NaN rows (no pose) -> NaN box, silently
        box = np.stack([np.nanmin(xy[:, :, 0], 1), np.nanmin(xy[:, :, 1], 1),
                        np.nanmax(xy[:, :, 0], 1), np.nanmax(xy[:, :, 1], 1)], 1).astype(np.float32)
    n_ok = int(ok.sum())
    np.savez_compressed(
        npz_path, img=img, world=world, ok=ok, t=t, fps=float(a.fps), width=W, height=H,
        src_fps=float(frames.fps), src_frames=int(frames.i + 1), src_width=int(frames.wh[0]), src_height=int(frames.wh[1]),
        rotation=int(meta['rotation']), t0_src=float(src_t[0]), src_frame=src_frame, src_t=src_t,
        xy=xy, xyn=xyn, xy_raw=xy_raw, conf=conf, vis=vis, box=box,
        names=np.array(NAMES), skeleton=np.array(SKELETON), flip_idx=np.array(FLIP_IDX), coco2mp=np.array(COCO2MP),
        mirrored=bool(a.mirrored), extractor='mediapipe', variant=a.variant, kp_conf=float(a.kp_conf),
        short_side=int(a.short), ts_mode=ts_mode,
    )

    drawn, video_err = (vout.close() if vout is not None else (0, None))
    if video_err:
        print(f'WARNING: annotated video problem: {video_err}', flush=True)
    import mediapipe
    vis_ok = vis[ok]
    pm = np.array(pose_ms)
    summary = {
        'video': video, 'extractor': 'mediapipe', 'mediapipe': mediapipe.__version__, 'variant': a.variant,
        'source': {'stored_width': meta['width'], 'stored_height': meta['height'], 'rotation': meta['rotation'],
                   'display_width': int(frames.wh[0]), 'display_height': int(frames.wh[1]), 'cv2_rotated': frames.fix is None,
                   'r_fps': meta['r_fps'], 'avg_fps': meta['avg_fps'], 'vfr': meta['vfr'], 'clock': ts_mode,
                   'frames_container': meta['nb_frames'], 'frames_decoded': int(frames.i + 1),
                   'start_s': a.start, 'end_s': a.end, 't0_src': float(src_t[0]), 'last_src_t': float(src_t[-1])},
        'processed': {'width': W, 'height': H, 'short_side': a.short, 'fps': a.fps},
        'frames': N, 'frames_ok': n_ok, 'ok_frac': round(n_ok / N, 4),
        'first_ok_frame': int(np.argmax(ok)) if n_ok else None,
        'last_ok_frame': int(N - 1 - np.argmax(ok[::-1])) if n_ok else None,
        'longest_no_pose_frames': longest_gap(ok),
        'pose_ms_mean': round(float(pm.mean()), 2), 'pose_ms_p95': round(float(np.percentile(pm, 95)), 2),
        'per_joint_vis_frac': {NAMES[k]: round(float(vis_ok[:, k].mean()), 3) if n_ok else 0.0 for k in range(17)},
        'mean_visibility': {NAMES[k]: round(float(conf[ok][:, k].mean()), 3) if n_ok else 0.0 for k in range(17)},
        'kp_conf': a.kp_conf, 'mirrored': bool(a.mirrored),
        'video_frames_written': drawn, 'video_error': video_err,
        'outputs': {'npz': str(npz_path), 'json': str(json_path), 'video': str(video_path) if video_path else None},
    }
    json_path.write_text(json.dumps(summary, indent=2))
    weak = {k: v for k, v in summary['per_joint_vis_frac'].items() if v < 0.8}
    print(json.dumps({'frames': N, 'frames_ok': n_ok, 'ok_frac': summary['ok_frac'], 'processed': summary['processed'],
                      'source_display': f"{frames.wh[0]}x{frames.wh[1]} rot {meta['rotation']}",
                      'longest_no_pose_frames': summary['longest_no_pose_frames'], 'pose_ms_mean': summary['pose_ms_mean'],
                      'joints_visible_under_80pct': weak}, indent=1))
    print(f'wrote {npz_path}, {json_path}' + (f', {video_path} ({drawn} frames)' if video_path else ''))


if __name__ == '__main__':
    main()
