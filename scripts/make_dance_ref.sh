#!/usr/bin/env bash
# Build a lesson reference (data/dances/<name>.npz + .mp4) from a reference clip, optionally slowed down for learners.
#   bash scripts/make_dance_ref.sh <pose stem> <name> <bpm> <first_beat_s> [speed]
#   e.g. bash scripts/make_dance_ref.sh sbbyj_a80421 sbbyj 88 3.4 0.75
# Uses data/dance/pose/<stem>.npz (YOLO keypoints) and data/dance/raw/<stem>.mp4; speed 0.75 = play at three quarters tempo
# (video, song, keypoints, bpm and first beat are all stretched together, so judging stays consistent). Steps text per 8-beat
# chunk comes from data/dance/choreo/<stem>.json when it exists.
set -eu
cd "$(dirname "$0")/.."
STEM=$1; NAME=$2; BPM=$3; FB=$4; SPEED=${5:-1.0}
PY=~/.venvs/brodie/bin/python
mkdir -p data/dances
if [ "$SPEED" != "1.0" ]; then
  ffmpeg -v error -y -i "data/dance/raw/$STEM.mp4" -filter_complex "[0:v]setpts=PTS/$SPEED[v];[0:a]atempo=$SPEED[a]" -map "[v]" -map "[a]" \
    -c:v libx264 -preset fast -crf 20 -pix_fmt yuv420p -c:a aac "data/dances/$NAME.mp4"
else
  cp "data/dance/raw/$STEM.mp4" "data/dances/$NAME.mp4"
fi
$PY - "$STEM" "$NAME" "$BPM" "$FB" "$SPEED" <<'PY'
import json, os, sys, numpy as np
stem, name, bpm, fb, speed = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
z = np.load(f"data/dance/pose/{stem}.npz")
kps = np.concatenate([z["xy_raw"], z["conf"][..., None]], -1).astype(np.float32)
np.save(f"data/dances/{name}_kps.npy", kps)
beat = 60.0 / bpm; chunk = 8 * beat
lines = []
p = f"data/dance/choreo/{stem}.json"
if os.path.exists(p):
    moves = [m for m in json.load(open(p))["moves"] if m["id"] != "ready"]
    k = 0
    import re
    def short(name):   # one short phrase per move: the first clause, at most 30 characters, for the on-screen bullets
        s = name.split(":")[-1].strip()
        s = re.split(r",|;| then | until | with | and | while | \(", s)[0].strip()
        if len(s) > 30: s = s[:30].rsplit(" ", 1)[0]
        w = s.split()
        while len(w) > 2 and w[-1].lower() in ("of", "the", "to", "in", "at", "a", "an", "on", "and", "or", "by", "for"): w.pop()
        s = " ".join(w)
        return s[0].upper() + s[1:]
    while fb + k * chunk + 0.5 * chunk <= moves[-1]["t1"]:
        t0, t1 = fb + k * chunk, fb + (k + 1) * chunk
        inside = sorted((((min(m["t1"], t1) - max(m["t0"], t0)) / (m["t1"] - m["t0"]), m) for m in moves), key=lambda x: x[0])
        picked = sorted((m for frac, m in inside if frac >= 0.4), key=lambda m: m["t0"])
        bullets = []
        for m in picked:                                                              # in time order, no repeats, at most 3
            b = short(m["name"])
            if b not in bullets: bullets.append(b)
        lines.append(" | ".join(bullets[:3])); k += 1
    open(f"data/dances/{name}_steps.txt", "w").write("\n".join(lines) + "\n")
print(f"kps {kps.shape} at {float(z['fps'])} fps; {len(lines)} chunks of steps; slowed kp-fps {float(z['fps']) * speed:g}, bpm {bpm * speed:g}, first beat {fb / speed:.3f}")
PY
KPFPS=$($PY -c "print(30*$SPEED)"); SBPM=$($PY -c "print($BPM*$SPEED)"); SFB=$($PY -c "print($FB/$SPEED)")
STEPS=""; [ -f "data/dances/${NAME}_steps.txt" ] && STEPS="--steps data/dances/${NAME}_steps.txt"
$PY -m starter.dance.reference --keypoints "data/dances/${NAME}_kps.npy" --kp-fps "$KPFPS" --name "$NAME" --bpm "$SBPM" --first-beat "$SFB" \
  --audio "data/dances/$NAME.mp4" $STEPS
$PY -m starter.dance.reference --show "data/dances/$NAME.npz" | head -12
