# data/dance

Everything the dance judge needs, tracked in git even though `data/` is ignored (added with `git add -f`).

| Path | What | In git? |
| --- | --- | --- |
| `choreo/<dance>.json` | Move boundaries per reference clip (schema in docs/DANCE_MODEL_INTERFACE.md section 4) | yes |
| `pose/<clip>[_trackN].npz` | YOLO-pose keypoints of one dancer, 30 fps, pixels, NaN = joint not visible (`scripts/extract_pose_yolo.py`) | yes |
| `pose/<clip>.json` / `.csv` | Extraction summary / one row per frame | yes |
| `pose/<clip>.pose.mp4`, `*.png` | Annotated videos and contact sheets | no (footage) |
| `raw/<clip>.mp4` | The downloaded TikTok clips, 576x1024 @ 30 fps | no (third-party footage) |
| `clips.json` | Clip registry: creator, framing, mirroring evidence | yes |
| `takes/<dance>__<performer>__<take>.npz` | Learner recordings through the live MediaPipe pipeline (`scripts/extract_landmarks.py`) | none yet |
| `labels.csv` | `clip,t_start_s,t_end_s,move,grade,faults,offset_s` for the takes | none yet |

Run the tests (WSL/macOS, the project venv; numpy only):

```
python scripts/test_dance_judge.py                      # 15 checks on the josh_king_o reference
python scripts/eval_dance.py data/dance/pose/sbbyj_a80421.npz --model data/models/dance_judge.pt
python scripts/eval_dance.py data/dance/pose/josh_king_o_1791679083441.npz --perturb mirror
```

Any pose npz can be replayed against its own choreo; the choreo's `npz` field says which file is the reference.
