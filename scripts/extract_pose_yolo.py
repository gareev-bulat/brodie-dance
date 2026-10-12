#!/usr/bin/env python3
"""Extract 2D pose keypoints for ONE person from a dance video with Ultralytics YOLO-pose.

Pipeline: YOLO-pose + ByteTrack over every frame -> pick the person (most frames, then
largest mean box; or --track-id) -> stitch tracker id switches by box overlap -> mask
low-confidence joints -> write:

  <out>/<stem>[_tag].npz        per-frame arrays (keys below)
  <out>/<stem>[_tag].csv        one row per frame: 17 keypoints x (x, y, conf) in pixels
  <out>/<stem>[_tag].pose.mp4   input + skeleton of the chosen person, original audio kept
  <out>/<stem>[_tag].json       run summary: coverage, gap fills, other people, weak joints

npz keys
  xy      (N,17,2) float32 pixel coords, NaN where the joint is not visible (see vis)
  xyn     (N,17,2) xy / [width, height], NaN likewise
  xy_raw  (N,17,2) the unmasked YOLO output. ultralytics clamps every joint to the image
          border, so an off-frame ankle shows up at y == height with low conf: never use
          xy_raw without conf.
  conf    (N,17) keypoint confidence (never exactly 0)
  vis     (N,17) bool = ok[:, None] & (conf >= kp_conf)
  box     (N,4) xyxy pixels: the TRACKER's Kalman-smoothed box, lags fast limbs by up to
          ~40 px. kp_box (N,4) is the tight box of the visible joints (NaN when none).
  box_conf (N,) detection confidence of that frame's box
  ok      (N,) bool: person found in that frame with box_conf >= min_box_conf
  source_id (N,) int: which tracker id supplied the frame (-1 = none); ids other than
          track_id mean the gap was filled from a re-identified fragment
  frame (N,) int, t (N,) seconds = frame / fps (the script refuses variable-frame-rate input)
  fps, width, height, track_id, mirrored, kp_conf, min_box_conf   scalars
  names (17,) keypoint names, skeleton (19,2) joint pairs, flip_idx (17,) left<->right swap

COCO-17 order (as YOLO-pose emits it): 0 nose, 1 left_eye, 2 right_eye, 3 left_ear, 4 right_ear,
5 left_shoulder, 6 right_shoulder, 7 left_elbow, 8 right_elbow, 9 left_wrist, 10 right_wrist,
11 left_hip, 12 right_hip, 13 left_knee, 14 right_knee, 15 left_ankle, 16 right_ankle.

Left/right: YOLO labels joints as they would be on an UN-mirrored image, so for a person
facing the camera "left_*" is on image-right. Phone selfie clips (most TikToks) are
horizontally mirrored: there "left_*" is the dancer's anatomical RIGHT. Pass --mirrored for
such clips; it is recorded as metadata only (coordinates are left in image space). To
un-mirror: x -> width - x, then xy = xy[:, flip_idx].

Usage (inside WSL, the yolo venv; weights auto-download once into ~/.yolo):
  ~/.venvs/yolo/bin/python scripts/extract_pose_yolo.py data/dance/raw/clip.mp4 --mirrored
  # a second person in the same clip: ids from the .json "other_tracks"; several ids = same person
  ~/.venvs/yolo/bin/python scripts/extract_pose_yolo.py clip.mp4 --track-id 1,9 --tag track1
"""
import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
import warnings
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]
# Ultralytics' COCO skeleton (ultralytics/utils/plotting.py), converted to 0-based indices.
SKELETON = [
    (15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11), (6, 12), (5, 6), (5, 7),
    (6, 8), (7, 9), (8, 10), (1, 2), (0, 1), (0, 2), (1, 3), (2, 4), (3, 5), (4, 6),
]
FLIP_IDX = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]   # coco-pose.yaml flip_idx
LEFT = {1, 3, 5, 7, 9, 11, 13, 15}
COL_LEFT, COL_RIGHT, COL_MID = (0, 200, 255), (255, 120, 0), (255, 255, 255)   # BGR


# ----------------------------------------------------------------------------- input
def probe(video: str):
    """Width, height, fps, container frame count. Refuses variable-frame-rate input (t = frame/fps would drift)."""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        sys.exit(f"cannot open {video}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=r_frame_rate,avg_frame_rate,nb_frames", "-of", "json", video],
                capture_output=True, text=True, check=True)
            s = json.loads(r.stdout)["streams"][0]
            rf = float(Fraction(s.get("r_frame_rate", "0/1")))
            af = float(Fraction(s.get("avg_frame_rate", "0/1")))
            if rf and af and abs(rf - af) > 0.01:
                sys.exit(f"{video}: variable frame rate (r={rf:.3f}, avg={af:.3f}). Normalise first:\n"
                         f"  ffmpeg -i IN -vf fps=30 -c:v libx264 -crf 18 -c:a copy OUT.mp4")
            if rf:
                fps = rf
            if s.get("nb_frames", "").isdigit():
                n = int(s["nb_frames"])
        except (subprocess.CalledProcessError, KeyError, ValueError, json.JSONDecodeError) as e:
            print(f"warning: ffprobe check failed ({e}); trusting OpenCV metadata", flush=True)
    if not fps:
        sys.exit(f"{video}: cannot determine frame rate")
    return fps, w, h, n


# ----------------------------------------------------------------------------- tracking
def run_tracker(video: str, a):
    """One pass of YOLO-pose + tracker. Returns (per_frame, device, untracked_frames) where
    per_frame[i] = {track_id: (xy(17,2), conf(17), box(4), box_conf)} for frame i."""
    import torch
    from ultralytics import YOLO

    device = a.device
    if device == "auto":
        device = "0" if torch.cuda.is_available() else "cpu"
    model = YOLO(a.model)
    per_frame, untracked = [], 0
    t0 = time.time()
    # conf=0.1: ByteTrack's second association needs the 0.1..0.25 band (ultralytics itself defaults
    # track() to 0.1); the person threshold is applied later via --min-box-conf.
    stream = model.track(
        source=video, stream=True, persist=True, tracker=a.tracker,
        conf=a.conf, iou=a.iou, imgsz=a.imgsz, device=device, verbose=False,
    )
    for i, r in enumerate(stream):
        dets = {}
        if r.boxes is not None and len(r.boxes):
            if r.boxes.id is None:
                untracked += 1                                    # raw detections without ids: unusable
            elif r.keypoints is not None and r.keypoints.xy is not None:
                ids = r.boxes.id.int().cpu().tolist()
                xy = r.keypoints.xy.cpu().numpy()                 # (M,17,2) pixels, original frame
                kc = r.keypoints.conf
                kc = kc.cpu().numpy() if kc is not None else np.ones(xy.shape[:2], np.float32)
                bx = r.boxes.xyxy.cpu().numpy()
                bc = r.boxes.conf.cpu().numpy()
                if xy.shape[1] != 17:
                    sys.exit(f"expected 17 keypoints, model gave {xy.shape[1]}: not a COCO pose model?")
                for k, tid in enumerate(ids):
                    dets[tid] = (xy[k].astype(np.float32), kc[k].astype(np.float32), bx[k].astype(np.float32), float(bc[k]))
        per_frame.append(dets)
        if a.max_frames and len(per_frame) >= a.max_frames:
            break
        if (i + 1) % 100 == 0:
            print(f"  {i + 1} frames, {len(per_frame) / (time.time() - t0):.1f} fps", flush=True)
    print(f"tracked {len(per_frame)} frames in {time.time() - t0:.1f} s on device {device}", flush=True)
    return per_frame, device, untracked


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter + 1e-9)


def track_table(per_frame):
    """Per tracker id: frames, mean box area, first/last frame."""
    tab = {}
    for i, dets in enumerate(per_frame):
        for tid, (_, _, bx, _) in dets.items():
            e = tab.setdefault(tid, {"frames": 0, "area": 0.0, "first": i, "last": i})
            e["frames"] += 1
            e["area"] += float((bx[2] - bx[0]) * (bx[3] - bx[1]))
            e["last"] = i
    return tab


def pick_track(tab, forced):
    """Choose the person. Returns (main_id, member_ids, ambiguous, ranked_ids)."""
    if not tab:
        raise ValueError("no person detected in any frame")
    ranked = sorted(tab, key=lambda t: (tab[t]["frames"], tab[t]["area"] / tab[t]["frames"]), reverse=True)
    if forced:
        missing = [t for t in forced if t not in tab]
        if missing:
            sys.exit(f"--track-id {missing} not found; seen: {sorted(tab)}")
        return forced[0], set(forced), False, ranked
    main = ranked[0]
    ambiguous = len(ranked) > 1 and tab[ranked[1]]["frames"] >= 0.8 * tab[main]["frames"]
    if ambiguous:
        print(f"WARNING: ids {ranked[0]} ({tab[ranked[0]]['frames']} frames) and {ranked[1]} "
              f"({tab[ranked[1]]['frames']} frames) are both present for most of the clip; picked {main} "
              f"by box area. Confirm with --track-id.", flush=True)
    return main, {main}, ambiguous, ranked


def assemble(per_frame, main_id, members, gap_iou, max_gap):
    """Per frame, the chosen person's detection. Members are taken as-is (main first, then by box conf);
    gaps are filled from any other id whose box overlaps the nearest kept box (IoU >= gap_iou) within max_gap frames.
    Ids that co-exist with the members for more than a few frames are another person and never used for filling."""
    N = len(per_frame)
    chosen = [None] * N
    source = np.full(N, -1, np.int64)
    for i, dets in enumerate(per_frame):
        cands = [(tid, d) for tid, d in dets.items() if tid in members]
        if cands:
            cands.sort(key=lambda c: (c[0] != main_id, -c[1][3]))
            source[i], chosen[i] = cands[0]
    coexist = {}
    for i, dets in enumerate(per_frame):
        if chosen[i] is not None:
            for tid in dets:
                if tid not in members:
                    coexist[tid] = coexist.get(tid, 0) + 1
    n_kept = sum(c is not None for c in chosen)
    other_people = {tid for tid, c in coexist.items() if c > max(10, 0.1 * n_kept)}

    def sweep(order):
        last_box, since = None, 10 ** 9
        for i in order:
            if chosen[i] is not None:
                last_box, since = chosen[i][2], 0
                continue
            since += 1
            if last_box is None or since > max_gap or not per_frame[i]:
                continue
            best = None
            for tid, d in per_frame[i].items():
                if tid in other_people:
                    continue
                v = iou(last_box, d[2])
                if v >= gap_iou and (best is None or v > best[0]):
                    best = (v, tid, d)
            if best:
                source[i], chosen[i] = best[1], best[2]
                last_box, since = best[2][2], 0

    sweep(range(N))                 # forward: fills gaps after the first sighting
    sweep(range(N - 1, -1, -1))     # backward: fills a gap before the first sighting
    return chosen, source


# ----------------------------------------------------------------------------- output
def draw(frame, xy, vis, box, src_label):
    for i, j in SKELETON:
        if vis[i] and vis[j]:
            col = COL_LEFT if (i in LEFT and j in LEFT) else COL_RIGHT if (i not in LEFT and j not in LEFT) else COL_MID
            cv2.line(frame, tuple(xy[i].astype(int)), tuple(xy[j].astype(int)), col, 2, cv2.LINE_AA)
    for k in range(17):
        if vis[k]:
            cv2.circle(frame, tuple(xy[k].astype(int)), 4, COL_LEFT if k in LEFT else COL_RIGHT, -1, cv2.LINE_AA)
    x1, y1, x2, y2 = box.astype(int)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 1)
    if src_label:
        cv2.putText(frame, src_label, (x1 + 2, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)


def write_video(video, out, xy_raw, vis, box, ok, source, main_id, fps, w, h):
    """Second pass: re-read the input and overlay the chosen person. h264 + original audio via ffmpeg
    when available, else cv2 mp4v without audio. Returns (frames_written, error_or_None)."""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return 0, f"cannot reopen {video}"
    proc, writer = None, None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        cmd = [ffmpeg, "-v", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
               "-i", video, "-map", "0:v:0", "-map", "1:a?", "-c:a", "copy", "-shortest",
               "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-preset", "fast", "-crf", "20", str(out)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    else:
        writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    n, i, err = len(ok), 0, None
    while i < n:
        got, frame = cap.read()
        if not got:
            break
        if ok[i]:
            draw(frame, xy_raw[i], vis[i], box[i], "" if source[i] == main_id else f"from id {source[i]}")
        label = f"{i}  {i / fps:5.2f}s" + ("" if ok[i] else "  no dancer")
        cv2.putText(frame, label, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        try:
            if proc:
                proc.stdin.write(frame.tobytes())
            else:
                writer.write(frame)
        except BrokenPipeError:
            err = "ffmpeg closed the pipe"
            break
        i += 1
    cap.release()
    if proc:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
        stderr = proc.stderr.read().decode(errors="replace").strip()
        rc = proc.wait()
        if rc != 0:
            err = f"ffmpeg rc={rc}: {stderr[-400:]}"
    else:
        writer.release()
    return i, err


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out", default="data/dance/pose", help="output directory")
    ap.add_argument("--tag", default="", help="suffix for the output stem, e.g. 'track1' -> <stem>_track1.*")
    ap.add_argument("--model", default="yolo11x-pose.pt", help="any Ultralytics *-pose.pt (auto-downloaded to ~/.yolo)")
    ap.add_argument("--imgsz", type=int, default=960, help="inference long side in px (rounded up to a multiple of 32)")
    ap.add_argument("--device", default="auto", help="'auto', '0' (CUDA), or 'cpu'")
    ap.add_argument("--tracker", default="bytetrack.yaml")
    ap.add_argument("--conf", type=float, default=0.1, help="detector threshold fed to the tracker (ByteTrack wants 0.1)")
    ap.add_argument("--iou", type=float, default=0.5, help="NMS IoU; 0.5 suppresses duplicate boxes on one person")
    ap.add_argument("--min-box-conf", type=float, default=0.25, help="frames of the chosen person below this get ok=False")
    ap.add_argument("--kp-conf", type=float, default=0.3, help="joints below this are NaN in xy/xyn and vis=False")
    ap.add_argument("--track-id", default="", help="tracker id(s) of the person to keep, e.g. '1' or '1,9' (= same person)")
    ap.add_argument("--gap-iou", type=float, default=0.5, help="min IoU to fill a gap from another tracker id")
    ap.add_argument("--max-gap", type=int, default=60, help="max frames away from a kept box that a gap fill may reach")
    ap.add_argument("--mirrored", action="store_true", help="the clip is a mirrored selfie recording (metadata only)")
    ap.add_argument("--max-frames", type=int, default=0, help="stop after this many frames (quick test)")
    ap.add_argument("--no-video", action="store_true", help="skip the annotated video")
    a = ap.parse_args()

    forced = [int(x) for x in a.track_id.replace(" ", "").split(",") if x != ""] if a.track_id else []
    imgsz = -(-a.imgsz // 32) * 32
    if imgsz != a.imgsz:
        print(f"imgsz {a.imgsz} -> {imgsz} (multiple of 32)", flush=True)
    a.imgsz = imgsz
    # Bare model names ('yolo11x-pose.pt') are cached in ~/.yolo instead of the current directory.
    if not Path(a.model).exists() and "/" not in a.model and "\\" not in a.model:
        weights_dir = Path.home() / ".yolo"
        weights_dir.mkdir(parents=True, exist_ok=True)
        a.model = str(weights_dir / a.model)

    video = str(Path(a.video))
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(video).stem + (f"_{a.tag}" if a.tag else "")
    json_path = out_dir / f"{stem}.json"
    fps, w, h, n_probe = probe(video)
    print(f"{video}: {w}x{h} @ {fps:.3f} fps, ~{n_probe} frames", flush=True)

    per_frame, device, untracked = run_tracker(video, a)
    N = len(per_frame)
    if N == 0:
        sys.exit(f"{video}: no frames decoded")
    if not a.max_frames and n_probe and N != n_probe:
        print(f"WARNING: decoded {N} frames but the container says {n_probe}; frame indices may be shifted", flush=True)
    if untracked:
        print(f"WARNING: {untracked} frames had detections without tracker ids (skipped)", flush=True)

    tab = track_table(per_frame)
    try:
        main_id, members, ambiguous, ranked = pick_track(tab, forced)
    except ValueError as e:
        json_path.write_text(json.dumps({"video": video, "frames": N, "coverage": 0.0, "error": str(e)}, indent=2))
        sys.exit(f"{e}; wrote {json_path}")
    chosen, source = assemble(per_frame, main_id, members, a.gap_iou, a.max_gap)

    xy_raw = np.zeros((N, 17, 2), np.float32)
    conf = np.zeros((N, 17), np.float32)
    box = np.full((N, 4), np.nan, np.float32)
    box_conf = np.zeros(N, np.float32)
    ok = np.zeros(N, bool)
    for i, d in enumerate(chosen):
        if d is not None:
            xy_raw[i], conf[i], box[i], box_conf[i] = d
            ok[i] = box_conf[i] >= a.min_box_conf
    source[~ok] = -1
    vis = ok[:, None] & (conf >= a.kp_conf)
    xy = np.where(vis[:, :, None], xy_raw, np.nan).astype(np.float32)
    xyn = xy / np.array([w, h], np.float32)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)          # all-NaN rows (no visible joint) are expected
        kp_box = np.stack([np.nanmin(xy[:, :, 0], 1), np.nanmin(xy[:, :, 1], 1),
                           np.nanmax(xy[:, :, 0], 1), np.nanmax(xy[:, :, 1], 1)], 1).astype(np.float32)
    frame_idx = np.arange(N)
    t = frame_idx / fps
    n_ok = int(ok.sum())
    filled = {int(s): int(c) for s, c in zip(*np.unique(source[ok & (source != main_id)], return_counts=True))}

    npz_path = out_dir / f"{stem}.npz"
    np.savez_compressed(
        npz_path, xy=xy, xyn=xyn, xy_raw=xy_raw, conf=conf, vis=vis, box=box, kp_box=kp_box, box_conf=box_conf,
        ok=ok, source_id=source, frame=frame_idx, t=t, fps=fps, width=w, height=h, track_id=int(main_id),
        mirrored=bool(a.mirrored), kp_conf=a.kp_conf, min_box_conf=a.min_box_conf,
        names=np.array(NAMES), skeleton=np.array(SKELETON), flip_idx=np.array(FLIP_IDX),
    )

    csv_path = out_dir / f"{stem}.csv"
    with open(csv_path, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["frame", "t", "ok", "source_id", "box_x1", "box_y1", "box_x2", "box_y2", "box_conf"]
                    + [f"{nm}_{ax}" for nm in NAMES for ax in ("x", "y", "c")])
        for i in range(N):
            row = [i, f"{t[i]:.4f}", int(ok[i]), int(source[i])] + [f"{v:.1f}" for v in box[i]] + [f"{box_conf[i]:.3f}"]
            for k in range(17):
                row += [f"{xy[i, k, 0]:.1f}", f"{xy[i, k, 1]:.1f}", f"{conf[i, k]:.3f}"]
            wr.writerow(row)

    video_path, drawn, video_err = None, 0, None
    if not a.no_video:
        video_path = out_dir / f"{stem}.pose.mp4"
        drawn, video_err = write_video(video, video_path, xy_raw, vis, box, ok, source, main_id, fps, w, h)
        if video_err:
            print(f"WARNING: annotated video problem: {video_err}", flush=True)
        elif drawn != N:
            video_err = f"wrote {drawn} frames, arrays have {N}"
            print(f"WARNING: {video_err}", flush=True)
        else:
            print(f"annotated video: {video_path} ({drawn} frames)", flush=True)

    seen = conf[ok]
    mean_conf = seen.mean(axis=0) if len(seen) else np.zeros(17)
    weak = {NAMES[k]: round(float((seen[:, k] < 0.5).mean()), 3) for k in range(17)} if len(seen) else {}
    weak = {k: v for k, v in weak.items() if v > 0.2}
    others = {}
    for tid, e in tab.items():
        if tid == main_id:
            continue
        ious = [iou(per_frame[i][tid][2], chosen[i][2]) for i in range(N)
                if tid in per_frame[i] and chosen[i] is not None and source[i] != tid]
        others[int(tid)] = {
            "frames": e["frames"], "first_frame": e["first"], "last_frame": e["last"],
            "mean_box_area": round(e["area"] / e["frames"], 1),
            "frames_overlapping_main": len(ious), "mean_iou_with_main": round(float(np.mean(ious)), 3) if ious else None,
            "used_to_fill_gaps": filled.get(int(tid), 0),
        }
    import ultralytics
    summary = {
        "video": video, "width": w, "height": h, "fps": fps, "frames": N, "frames_probed": n_probe,
        "mirrored": bool(a.mirrored),
        "model": a.model, "ultralytics": ultralytics.__version__, "imgsz": a.imgsz, "device": device,
        "tracker": a.tracker, "tracker_conf": a.conf, "nms_iou": a.iou,
        "min_box_conf": a.min_box_conf, "kp_conf": a.kp_conf, "gap_iou": a.gap_iou, "max_gap": a.max_gap,
        "track_id": int(main_id), "member_ids": sorted(int(m) for m in members),
        "main_dancer_ambiguous": bool(ambiguous), "ranked_ids": [int(r) for r in ranked],
        "frames_with_dancer": n_ok, "coverage": round(n_ok / N, 4),
        "first_frame": int(np.argmax(ok)) if n_ok else None, "last_frame": int(N - 1 - np.argmax(ok[::-1])) if n_ok else None,
        "gap_filled_frames": filled, "untracked_frames": untracked,
        "other_tracks": others,
        "mean_keypoint_conf": {NAMES[k]: round(float(mean_conf[k]), 3) for k in range(17)},
        "weak_joints_frac_below_0.5": weak,
        "video_frames_written": drawn, "video_error": video_err,
        "outputs": {"npz": str(npz_path), "csv": str(csv_path), "video": str(video_path) if video_path else None},
    }
    json_path.write_text(json.dumps(summary, indent=2))
    brief = {k: summary[k] for k in ("frames", "track_id", "member_ids", "main_dancer_ambiguous", "frames_with_dancer",
                                     "coverage", "gap_filled_frames", "other_tracks", "weak_joints_frac_below_0.5")}
    print(json.dumps(brief, indent=1))
    print(f"wrote {npz_path}, {csv_path}, {json_path}")


if __name__ == "__main__":
    main()
