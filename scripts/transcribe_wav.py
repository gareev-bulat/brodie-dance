"""What did the agent really say? Transcribe a wav recorded by scripts/verity_smoke.py --record (or any wav) with faster-whisper on
the CPU, no API key. The transcript channel of the model is not proof of what was spoken (see verity/README.md, relay quirk).
  python scripts/transcribe_wav.py data/tmp/smoke_dancity.wav
Needs: uv pip install faster-whisper (the base.en model, ~150 MB, downloads on first use).
"""
import sys, wave
import numpy as np


def load_16k(path):
    with wave.open(path) as w:
        rate, ch = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, ch).mean(1).astype(np.float32) / 32768
    if rate == 16000: return pcm
    step = rate / 16000
    if abs(step - round(step)) < 1e-6:                               # 48 k -> 16 k: mean of every 3 samples (a crude low-pass)
        n = int(step); return pcm[: len(pcm) // n * n].reshape(-1, n).mean(1).astype(np.float32)
    idx = (np.arange(int(len(pcm) / step)) * step).astype(int)
    return pcm[np.clip(idx, 0, len(pcm) - 1)]


def main():
    from faster_whisper import WhisperModel
    path = sys.argv[1] if len(sys.argv) > 1 else 'data/tmp/smoke_dancity.wav'
    x = load_16k(path)
    m = WhisperModel('base.en', device='cpu', compute_type='int8')
    segs, _ = m.transcribe(x, beam_size=3, vad_filter=True, initial_prompt='Verity, Dancity, the eagle dance. Five, six, seven, eight.')
    for s in segs: print(f'[{s.start:6.1f}-{s.end:6.1f}] {s.text.strip()}', flush=True)


if __name__ == '__main__':
    main()
