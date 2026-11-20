"""Shows what was on screen and what was said before and during a stretch, so a cut can be judged in context.

Writes one image: the whole frame every few seconds, starting before the stretch, each frame
labelled with its time and marked when it is inside the stretch. Prints the words said in the
lead-up and in the stretch.
"""
import argparse
import os
import sys

import cv2
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import grab_frame, load_json
import reading

W, H, COLUMNS, LABEL = 480, 270, 5, 22
MAX_FRAMES = 40


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("start", type=float)
    ap.add_argument("end", type=float)
    ap.add_argument("--before", type=float, default=30.0, help="seconds of lead-up shown before the start")
    ap.add_argument("--after", type=float, default=6.0)
    ap.add_argument("--said-before", type=float, default=90.0, help="seconds of lead-up printed as text")
    a = ap.parse_args()
    work = a.workdir
    source = os.path.join(work, "source.mp4")
    meta = load_json(os.path.join(work, "meta.json"))
    global W, H, COLUMNS
    if meta["height"] > meta["width"]:
        W, H, COLUMNS = 270, 480, 8
    lo, hi = max(0.0, a.start - a.before), min(meta["duration"] - 0.2, a.end + a.after)
    for j in meta.get("joins") or []:
        if lo < j <= a.start:
            lo = j
        if a.end <= j < hi:
            hi = j
    step = max(1.5, (hi - lo) / MAX_FRAMES)
    times, t = [], lo
    while t <= hi:
        times.append(t)
        t += step
    rows = (len(times) + COLUMNS - 1) // COLUMNS
    sheet = Image.new("RGB", (COLUMNS * W, rows * (H + LABEL)), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    for i, t in enumerate(times):
        frame = grab_frame(source, t, W, H)
        x, y = (i % COLUMNS) * W, (i // COLUMNS) * (H + LABEL)
        inside = a.start <= t <= a.end
        draw.rectangle([x, y, x + W - 1, y + LABEL - 1], fill=(30, 120, 60) if inside else (60, 60, 60))
        draw.text((x + 6, y + 5), f"{t:.1f} s  {'IN THE CLIP' if inside else 'before' if t < a.start else 'after'}",
                  fill=(255, 255, 255))
        if frame is not None:
            sheet.paste(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), (x, y + LABEL))
    out_dir = os.path.join(work, "look")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"look-{a.start:.0f}-{a.end:.0f}.jpg")
    sheet.save(path, quality=82)

    words = load_json(os.path.join(work, "transcript.json"))
    said_from = max(lo if lo > a.start - a.before else 0.0, a.start - a.said_before)
    print(f"said in the {a.start - said_from:.0f} s before the start (a viewer of the clip never hears this):")
    for group in reading.paragraphs([w for w in words if said_from <= w["start"] < a.start]):
        print(f"  [{group[0]['start']:.1f}] " + " ".join(w["text"] for w in group))
    print(f"\nsaid in the clip ({a.start:.1f} - {a.end:.1f} s, {a.end - a.start:.1f} s):")
    for group in reading.paragraphs([w for w in words if a.start <= w["start"] < a.end]):
        print(f"  [{group[0]['start']:.1f}] " + " ".join(w["text"] for w in group))
    after = [w for w in words if a.end <= w["start"] < a.end + 8]
    if after:
        print("\nsaid right after the end: " + " ".join(w["text"] for w in after))
    print(f"\nframes every {step:.1f} s from {lo:.1f} to {hi:.1f} s: {path}")


if __name__ == "__main__":
    main()
