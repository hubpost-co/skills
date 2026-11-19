import argparse
import json
import os
import subprocess
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import OUT_H, OUT_W, clip_name, load_clips, load_json, probe_duration, read_frames

TOP_SAFE, BOTTOM_SAFE = 200, 300
THUMB_W, THUMB_H = 216, 384
COLUMNS = 8


def audio_stats(path):
    has = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    if not has:
        return False, None, None
    res = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", path, "-vn", "-af",
                          "loudnorm=print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    text = res.stderr
    try:
        stats = json.loads(text[text.rindex("{"):text.rindex("}") + 1])
        return True, float(stats["input_i"]), float(stats["input_tp"])
    except (ValueError, KeyError):
        return True, None, None


def contact_sheet(frames, path, divider=None):
    rows = (len(frames) + COLUMNS - 1) // COLUMNS
    sheet = Image.new("RGB", (COLUMNS * THUMB_W, rows * (THUMB_H + 24)), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    for i, frame in enumerate(frames):
        thumb = Image.fromarray(cv2.cvtColor(cv2.resize(frame, (THUMB_W, THUMB_H), interpolation=cv2.INTER_AREA),
                                             cv2.COLOR_BGR2RGB))
        x, y = (i % COLUMNS) * THUMB_W, (i // COLUMNS) * (THUMB_H + 24)
        sheet.paste(thumb, (x, y + 24))
        draw.text((x + 6, y + 5), f"{i} s", fill=(230, 230, 230))
    for guide in (TOP_SAFE, OUT_H - BOTTOM_SAFE):
        gy = guide * THUMB_H / OUT_H
        for r in range(rows):
            yy = r * (THUMB_H + 24) + 24 + gy
            draw.line([(0, yy), (COLUMNS * THUMB_W, yy)], fill=(255, 60, 60), width=1)
    if divider:
        gy = divider * THUMB_H / OUT_H
        for r in range(rows):
            yy = r * (THUMB_H + 24) + 24 + gy
            for x in range(0, COLUMNS * THUMB_W, 8):
                draw.line([(x, yy), (x + 3, yy)], fill=(60, 200, 255), width=1)
    sheet.save(path, quality=85)


def detect(gray, detectors):
    frontal, profile = detectors
    side = int(gray.shape[0] * 0.06)
    found = list(frontal.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(side, side)))
    if not found:
        found = list(profile.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(side, side)))
    if not found:
        width = gray.shape[1]
        for x, y, w, h in profile.detectMultiScale(cv2.flip(gray, 1), scaleFactor=1.1, minNeighbors=5,
                                                   minSize=(side, side)):
            found.append((width - x - w, y, w, h))
    return found


def bare(text):
    return "".join(ch for ch in text.lower() if ch.isalnum())


def caption_offset(work, name, path):
    """How far the captions run from the speech: the finished clip is heard again and its words are
    matched to the words the captions were timed on. Positive = captions late. None = too few matches."""
    from transcribe import transcribe
    plan = load_json(os.path.join(work, "cuts", f"{name}.json"))
    start, end = plan["start"], plan["end"]
    timed = [(bare(w["text"]), w["start"] - start) for w in load_json(os.path.join(work, "transcript.json"))
             if start - 0.05 <= w["start"] <= end]
    heard, _ = transcribe(path, beam=1, quiet=True)
    gaps = []
    for w in heard:
        same = [t for text, t in timed if text == bare(w["text"]) and len(text) > 2 and abs(t - w["start"]) < 3.0]
        if same:
            gaps.append(min(same, key=lambda t: abs(t - w["start"])) - w["start"])
    if len(gaps) < 4:
        return None
    return float(np.median(gaps))


def check_clip(work, index, settings, detector, sync=True):
    name = clip_name(index)
    clips_dir = os.path.join(work, "clips")
    path = os.path.join(clips_dir, f"{name}.mp4")
    problems, notes = [], []
    if not os.path.exists(path):
        return name, ["file missing"], notes
    layout_path = os.path.join(clips_dir, f"{name}.layout.json")
    layout = load_json(layout_path) if os.path.exists(layout_path) else {}

    clip = load_clips(work)["clips"][index]
    if len((clip.get("context") or "").split()) < 6:
        problems.append("no `context`: run look.py on this clip, then write what a stranger needs to know "
                        "and where inside the clip they get it")

    duration = probe_duration(path)
    low, high = settings.get("min_seconds", 20), settings.get("max_seconds", 60)
    if duration < low - 1.5 or duration > high + 1.5:
        problems.append(f"duration {duration:.1f} s is outside {low}-{high} s")
    notes.append(f"{duration:.1f} s")

    has_audio, loud, peak = audio_stats(path)
    if not has_audio:
        problems.append("no audio track")
    elif loud is None or loud < -40:
        problems.append("audio is silent or unreadable")
    else:
        notes.append(f"{loud:.1f} LUFS")
        if abs(loud - (-14)) > 2:
            problems.append(f"loudness {loud:.1f} LUFS is not near -14")

    if settings.get("credit") != "own":
        text_path = os.path.join(clips_dir, f"{name}.txt")
        post = open(text_path).read() if os.path.exists(text_path) else ""
        source_path = os.path.join(work, "source.json")
        credit = settings.get("credit") if isinstance(settings.get("credit"), dict) else (
            load_json(source_path) if os.path.exists(source_path) else None)
        if not credit or not credit.get("url"):
            problems.append("no credit: say whose video this is (settings.credit), or \"own\" if it is the person's")
        else:
            if not layout.get("credit"):
                problems.append("the creator's name is not on screen")
            if credit["url"] not in post:
                problems.append("the post text does not link the creator")
            notes.append(f"credit {credit['text']}")
    meta_path = os.path.join(work, "meta.json")
    cut_path = os.path.join(work, "cuts", f"{name}.json")
    if os.path.exists(meta_path) and os.path.exists(cut_path):
        cut = load_json(cut_path)
        for j in load_json(meta_path).get("joins") or []:
            if cut["start"] < j < cut["end"]:
                problems.append(f"the clip crosses a join between two stretches of the stream at {j} s")

    stream = layout.get("stream")
    box = layout.get("caption_box")
    if box is None:
        problems.append("no captions were drawn")
    else:
        if box[1] < TOP_SAFE or box[3] > OUT_H - BOTTOM_SAFE:
            problems.append(f"captions reach outside the safe zone (y {box[1]}-{box[3]})")
        else:
            notes.append(f"captions y {box[1]}-{box[3]}")
        if stream and box[1] < stream["cam_h"] + stream["divider"]:
            problems.append(f"captions reach into the facecam panel (top y {box[1]})")

    samples = {round(s["t"]): s for s in layout.get("samples", [])}
    frames = list(read_frames(path, 0, None, 1))
    contact_sheet(frames, os.path.join(clips_dir, f"{name}-sheet.jpg"), stream["cam_h"] if stream else None)
    crop_checked = crop_found = overlaps = 0
    for second, frame in enumerate(frames):
        sample = samples.get(second)
        if sample is None or sample["mode"] not in ("crop", "stream"):
            continue
        crop_checked += 1
        limit_h = stream["cam_h"] if stream else OUT_H
        small = cv2.resize(frame[:limit_h], (OUT_W // 2, limit_h // 2), interpolation=cv2.INTER_AREA)
        gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        faces = [[v * 2 for v in f] for f in detect(gray, detector)]
        inside = [f for f in faces if f[0] > 4 and f[1] > 4 and f[0] + f[2] < OUT_W - 4 and f[1] + f[3] < limit_h - 4]
        if inside:
            crop_found += 1
        if box and inside:
            fx, fy, fw, fh = max(inside, key=lambda f: f[2])
            if fy + fh * 0.95 > box[1] and fy < box[3]:
                overlaps += 1
    if crop_checked:
        ratio = crop_found / crop_checked
        where = "facecam-panel" if stream else "face-crop"
        notes.append(f"face seen in {crop_found}/{crop_checked} {where} seconds")
        if ratio < 0.7:
            problems.append(f"face only found in {ratio:.0%} of {where} frames (look at the sheet)")
        if overlaps > max(1, 0.1 * crop_checked):
            problems.append(f"captions overlap the face in {overlaps} sampled frames")
    fit = len(frames) - crop_checked
    if stream:
        notes.append("stream layout")
    elif fit:
        notes.append(f"{fit} s in full-frame layout (no big face)")
    if sync and os.path.exists(os.path.join(work, "cuts", f"{name}.json")):
        offset = caption_offset(work, name, path)
        if offset is None:
            notes.append("caption timing not measured (too few words matched)")
        else:
            notes.append(f"captions {abs(offset):.2f} s {'late' if offset > 0 else 'early'}")
            if abs(offset) > 0.25:
                problems.append(f"captions run {abs(offset):.2f} s {'after' if offset > 0 else 'before'} the speech: "
                                "transcribe again with this version of the skill, then cut and edit again")
    return name, problems, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--no-sync", action="store_true", help="skip hearing each clip again to time its captions")
    a = ap.parse_args()
    data = load_clips(a.workdir)
    detector = (cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")),
                cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_profileface.xml")))
    failed = 0
    for index in range(len(data["clips"])):
        name, problems, notes = check_clip(a.workdir, index, data["settings"], detector, not a.no_sync)
        status = "PASS" if not problems else "FAIL"
        failed += bool(problems)
        print(f"{name}: {status}  ({', '.join(notes)})")
        for p in problems:
            print(f"    - {p}")
        print(f"    sheet: clips/{name}-sheet.jpg")
    print("all clips passed" if not failed else f"{failed} clip(s) failed: fix them, then run check.py again")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
