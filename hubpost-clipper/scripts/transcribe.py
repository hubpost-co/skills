import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ON_CLOCK


def stamp(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m):02d}:{s:04.1f}"


def sentences(words):
    out, current = [], []
    for w in words:
        current.append(w)
        ends = w["text"].rstrip().endswith((".", "?", "!"))
        long_enough = len(current) >= 40
        if ends or long_enough:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return out


def transcribe(source, model="small", language=None, beam=5, quiet=False):
    from faster_whisper import WhisperModel

    # The sound is first written out on the file's own clock (see ON_CLOCK), so a word's time here is
    # the same time cut.py seeks to.
    handle, wav = tempfile.mkstemp(suffix=".wav")
    os.close(handle)
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", source, "-vn", *ON_CLOCK,
                        "-ac", "1", "-ar", "16000", wav], check=True)
        engine = WhisperModel(model, device="cpu", compute_type="int8")
        segments, info = engine.transcribe(
            wav, language=language, word_timestamps=True, vad_filter=True, beam_size=beam)
        words = []
        for seg in segments:
            for w in seg.words or []:
                text = w.word.strip()
                if text:
                    words.append({"text": text, "start": round(w.start, 3), "end": round(w.end, 3)})
            if not quiet:
                print(f"  transcribed to {stamp(seg.end)}", end="\r", flush=True)
        if not quiet:
            print()
    finally:
        os.remove(wav)
    return words, info.language


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--model", default="small")
    ap.add_argument("--language", default=None)
    a = ap.parse_args()

    source = os.path.join(a.workdir, "source.mp4")
    started = time.time()
    words, language = transcribe(source, a.model, a.language)

    with open(os.path.join(a.workdir, "transcript.json"), "w") as f:
        json.dump(words, f, ensure_ascii=False)
    lines = []
    for group in sentences(words):
        text = " ".join(w["text"] for w in group)
        lines.append(f"[{stamp(group[0]['start'])} - {stamp(group[-1]['end'])}] {text}")
    with open(os.path.join(a.workdir, "transcript.txt"), "w") as f:
        f.write(f"language: {language}\n\n")
        f.write("\n".join(lines) + "\n")
    print(f"{len(words)} words, language {language}, {time.time() - started:.0f} s")
    print("wrote transcript.json and transcript.txt")


if __name__ == "__main__":
    main()
