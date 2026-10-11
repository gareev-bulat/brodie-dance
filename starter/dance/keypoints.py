"""Keypoint formats and pose features shared by the live handler, the judge and the reference maker.

Everything here works on COCO-17 keypoints, the format YOLO-pose outputs:
  0 nose, 1 l_eye, 2 r_eye, 3 l_ear, 4 r_ear, 5 l_shoulder, 6 r_shoulder, 7 l_elbow, 8 r_elbow,
  9 l_wrist, 10 r_wrist, 11 l_hip, 12 r_hip, 13 l_knee, 14 r_knee, 15 l_ankle, 16 r_ankle
An array of poses is (T, 17, 3): x, y in PIXELS (or any units with the same scale on both axes), confidence 0..1.

Live, Brodie gives MediaPipe's 33 landmarks with x, y in 0..1 of the frame; mediapipe_to_coco() converts them.
"""
import numpy as np

FPS = 10                                                   # Brodie processes 10 frames a second; references are stored at 10 fps too

# MediaPipe-33 index for each COCO-17 point (nose, eyes, ears, shoulders, elbows, wrists, hips, knees, ankles)
MP_TO_COCO = [0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]

# Body segments (from -> to, COCO indices). Their directions in the image are the features we compare:
# a direction does not depend on how tall the dancer is or how far from the camera they stand.
SEGMENTS = {
    'left upper arm':  (5, 7),   'left forearm':  (7, 9),
    'right upper arm': (6, 8),   'right forearm': (8, 10),
    'left thigh':      (11, 13), 'left shin':     (13, 15),
    'right thigh':     (12, 14), 'right shin':    (14, 16),
    'torso':           (None, None),                       # mid-hip -> mid-shoulder, computed separately
}
SEG_NAMES = list(SEGMENTS)
PART_OF = {                                                 # segment -> body part named in the coaching tip
    'left upper arm': 'left arm', 'left forearm': 'left arm', 'right upper arm': 'right arm', 'right forearm': 'right arm',
    'left thigh': 'left leg', 'left shin': 'left leg', 'right thigh': 'right leg', 'right shin': 'right leg', 'torso': 'body',
}
# Mirror image: left and right swap (and x flips, handled in features()).
MIRROR_SEG = [SEG_NAMES.index(n.replace('left', 'TMP').replace('right', 'left').replace('TMP', 'right')) for n in SEG_NAMES]
MIN_CONF = 0.3


def mediapipe_to_coco(kp33, wh):
    """kp33: (33, >=2[, vis]) MediaPipe landmarks, x, y in 0..1 of the frame (Brodie's `pose` event `kp`); wh: (width, height).
    Returns (17, 3) COCO keypoints in pixels with confidence (MediaPipe visibility)."""
    a = np.asarray(kp33, np.float32)
    out = np.zeros((17, 3), np.float32)
    out[:, 0] = a[MP_TO_COCO, 0] * wh[0]
    out[:, 1] = a[MP_TO_COCO, 1] * wh[1]
    out[:, 2] = a[MP_TO_COCO, 3] if a.shape[1] >= 4 else 1.0
    return out


def features(kps, mirror=False):
    """kps: (T, 17, 3) COCO poses -> (dirs (T, S) segment directions in radians, ok (T, S) bool: both ends confident).
    mirror=True: the pose as seen in a mirror (x flipped, left and right swapped)."""
    k = np.asarray(kps, np.float32)
    if k.ndim == 2: k = k[None]
    x = -k[..., 0] if mirror else k[..., 0]
    y = k[..., 1]; c = k[..., 2]
    T = k.shape[0]; S = len(SEG_NAMES)
    dirs = np.zeros((T, S), np.float32); ok = np.zeros((T, S), bool)
    for j, name in enumerate(SEG_NAMES):
        if name == 'torso':
            ax, ay = (x[:, 11] + x[:, 12]) / 2, (y[:, 11] + y[:, 12]) / 2
            bx, by = (x[:, 5] + x[:, 6]) / 2, (y[:, 5] + y[:, 6]) / 2
            cf = np.minimum.reduce([c[:, 5], c[:, 6], c[:, 11], c[:, 12]])
        else:
            i0, i1 = SEGMENTS[name]
            ax, ay, bx, by = x[:, i0], y[:, i0], x[:, i1], y[:, i1]
            cf = np.minimum(c[:, i0], c[:, i1])
        dirs[:, j] = np.arctan2(by - ay, bx - ax)
        ok[:, j] = cf >= MIN_CONF
    if mirror:                                              # the mirrored left arm is the dancer's right arm
        dirs = dirs[:, MIRROR_SEG]; ok = ok[:, MIRROR_SEG]
    return dirs, ok


def normalize(kps):
    """(T, 17, 3) -> same, centred on the mid-hip and scaled so the torso is 1 long (for a trained model's input, if it wants it)."""
    k = np.array(kps, np.float32, copy=True)
    hip = (k[:, 11, :2] + k[:, 12, :2]) / 2
    sh = (k[:, 5, :2] + k[:, 6, :2]) / 2
    torso = np.linalg.norm(sh - hip, axis=1, keepdims=True)
    torso[torso < 1e-6] = 1.0
    k[..., :2] = (k[..., :2] - hip[:, None]) / torso[:, None]
    return k


def resample(times, kps, fps=FPS, t0=None, t1=None):
    """Poses at uneven times (live frames arrive at about 10 fps, not exactly) -> a regular grid at `fps` (nearest frame).
    times: (N,) seconds; kps: (N, 17, 3). Returns (grid_times, (M, 17, 3))."""
    times = np.asarray(times, np.float64); kps = np.asarray(kps, np.float32)
    if len(times) == 0: return np.zeros(0), np.zeros((0, 17, 3), np.float32)
    t0 = times[0] if t0 is None else t0; t1 = times[-1] if t1 is None else t1
    grid = np.arange(t0, t1 + 1e-9, 1.0 / fps)
    idx = np.clip(np.searchsorted(times, grid), 0, len(times) - 1)
    prev = np.clip(idx - 1, 0, len(times) - 1)
    use_prev = np.abs(times[prev] - grid) < np.abs(times[idx] - grid)
    idx = np.where(use_prev, prev, idx)
    out = kps[idx].copy()
    out[np.abs(times[idx] - grid) > 0.25, :, 2] = 0.0       # no frame within 0.25 s: mark that grid point as unseen
    return grid, out
