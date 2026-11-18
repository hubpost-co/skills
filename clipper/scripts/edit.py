import argparse
import json
import os
import re
import subprocess
import sys
import time
import wave

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (OUT_H, OUT_W, clip_name, close_writer, load_clips, load_json, open_writer, read_frames,
                    save_json)

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(SKILL, "fonts", "Montserrat-ExtraBold.ttf")
WHOOSH = os.path.join(SKILL, "sfx", "whoosh.wav")
FONT_SIZE = 100
CREDIT_SIZE = 34
CREDIT_X, CREDIT_Y = 36, 208
MAX_TEXT_W = 940
CAPTION_TOP_MIN, CAPTION_TOP_MAX = 1250, 1480
ZOOM_EASE = 0.25
MIN_ZOOM_GAP = 2.6
PULSE_HOLD = 2.0
STREAM_CAPTION_GAP = 40
RATE = 48000

POWER_WORDS = {
    "never", "always", "everything", "nothing", "everyone", "nobody", "best", "worst", "biggest", "secret",
    "truth", "lie", "money", "free", "million", "billion", "thousand", "hundred", "percent", "dead", "death",
    "die", "kill", "love", "hate", "fear", "happy", "pain", "rich", "poor", "fail", "failure", "success",
    "win", "lose", "wrong", "mistake", "problem", "stop", "first", "last", "huge", "insane",
    "crazy", "impossible", "power", "powerful", "important", "real",
    "mind", "brain", "life", "suffering", "freedom", "habit", "discipline", "addicted", "dopamine",
}
SMALL_CAPS_OK = {"I", "I'm", "I've", "I'll", "I'd", "OK", "Okay"}


def auto_accent(cut_paths):
    hues = []
    for path in cut_paths:
        for frame in read_frames(path, 0, None, 1, 135, 240):
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).reshape(-1, 3)
            keep = (hsv[:, 1] > 150) & (hsv[:, 2] > 90)
            h = hsv[keep, 0].astype(int)
            skin = (h < 22) & (hsv[keep, 1] < 200)
            hues.extend(h[~skin].tolist())
    if len(hues) < 400:
        return "#FFD60A"
    hist = np.bincount(np.array(hues) // 6, minlength=30)
    hue = int(np.argmax(hist)) * 6 + 3
    pixel = np.uint8([[[hue, 235, 255]]])
    b, g, r = cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)[0, 0]
    return f"#{r:02X}{g:02X}{b:02X}"


def clean(text):
    return re.sub(r"[^\w'%$€£&+\-?!]", "", text).replace("_", "")


def join_split_words(words):
    out = []
    for w in words:
        if out and w["text"].startswith("-"):
            out[-1] = dict(out[-1], text=out[-1]["text"] + w["text"], end=w["end"])
        else:
            out.append(dict(w))
    return out


def is_key_word(word, previous_text):
    bare = clean(word).strip("?!").strip("'")
    if not bare:
        return False
    if any(ch.isdigit() for ch in bare):
        return True
    if bare.lower() in POWER_WORDS:
        return True
    sentence_start = previous_text is None or previous_text.rstrip().endswith((".", "?", "!"))
    if bare[0].isupper() and not sentence_start and bare not in SMALL_CAPS_OK and len(bare) > 1:
        return True
    return False


def group_words(words):
    groups, current = [], []
    for i, w in enumerate(words):
        current.append(w)
        text = " ".join(clean(x["text"]) for x in current)
        next_gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 9
        punct = w["text"].rstrip().endswith((".", ",", "?", "!", ":", ";"))
        name_continues = i + 1 < len(words) and not punct and clean(w["text"])[:1].isupper() \
            and clean(words[i + 1]["text"])[:1].isupper() and len(current) < 4
        if name_continues:
            continue
        if len(current) >= 3 or len(text) >= 14 or punct or next_gap > 0.35:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    merged = []
    for g in groups:
        if merged and len(g) == 1 and len(merged[-1]) < 3 and not merged[-1][-1]["text"].rstrip().endswith((".", "?", "!")) \
                and g[0]["start"] - merged[-1][-1]["end"] < 0.3 and len(" ".join(x["text"] for x in merged[-1] + g)) <= 18:
            merged[-1] = merged[-1] + g
        else:
            merged.append(g)
    return merged


def caption_events(words, duration):
    groups = group_words(words)
    events = []
    for k, g in enumerate(groups):
        start = max(0.0, g[0]["start"])
        end = g[-1]["end"] + 0.15
        if k + 1 < len(groups):
            nxt = groups[k + 1][0]["start"]
            end = nxt if nxt - g[-1]["end"] < 0.7 else end
        events.append({"start": start, "end": min(end, duration), "words": g})
    return events


def layout_lines(tokens, size, font_cache, wrap, force=False):
    font = font_cache.setdefault(size, ImageFont.truetype(FONT, size))
    space = font.getlength(" ")
    widths = [font.getlength(t) for t, _ in tokens]
    lines = [[0]]
    for i in range(1, len(tokens)):
        line_w = sum(widths[j] for j in lines[-1]) + space * len(lines[-1]) + widths[i]
        if wrap and line_w > MAX_TEXT_W:
            lines.append([i])
        else:
            lines[-1].append(i)
    widest = max(sum(widths[j] for j in ln) + space * (len(ln) - 1) for ln in lines)
    if force or (widest <= MAX_TEXT_W and len(lines) <= 2):
        return size, font, widths, space, lines, widest
    return None


def render_caption(event, words_all, accent_rgb, font_cache):
    tokens = []
    for w in event["words"]:
        idx = w["index"]
        prev = words_all[idx - 1]["text"] if idx > 0 else None
        text = clean(w["text"]).upper()
        if text:
            tokens.append((text, is_key_word(w["text"], prev)))
    if not tokens:
        return None
    layout = None
    for size in range(FONT_SIZE, 79, -4):
        layout = layout_lines(tokens, size, font_cache, wrap=False)
        if layout:
            break
    if not layout:
        for size in range(FONT_SIZE, 53, -6):
            layout = layout_lines(tokens, size, font_cache, wrap=True)
            if layout:
                break
    if not layout:
        layout = layout_lines(tokens, 54, font_cache, wrap=True, force=True)
    size, font, widths, space, lines, widest = layout
    stroke = max(5, size // 11)
    line_h = int(size * 1.12)
    pad = stroke * 2 + 12
    img_w = int(widest) + pad * 2
    img_h = line_h * len(lines) + pad * 2
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    sdraw = ImageDraw.Draw(shadow)
    for li, ln in enumerate(lines):
        line_w = sum(widths[j] for j in ln) + space * (len(ln) - 1)
        x = (img_w - line_w) / 2
        y = pad + li * line_h
        for j in ln:
            text, key = tokens[j]
            fill = accent_rgb if key else (255, 255, 255)
            sdraw.text((x + 4, y + 7), text, font=font, fill=(0, 0, 0, 150), stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
            draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=(17, 17, 17))
            x += widths[j] + space
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    out = Image.alpha_composite(shadow, img)
    rgba = np.array(out)
    bgra = rgba[:, :, [2, 1, 0, 3]]
    return bgra


def load_credit(work, settings):
    """None when the person is clipping their own video; otherwise who made it."""
    choice = settings.get("credit")
    if choice == "own":
        return None
    if isinstance(choice, dict):
        return choice
    path = os.path.join(work, "source.json")
    return load_json(path) if os.path.exists(path) else None


def render_credit(text):
    font = ImageFont.truetype(FONT, CREDIT_SIZE)
    box = font.getbbox(text)
    w, h = box[2] - box[0] + 24, box[3] - box[1] + 24
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((12 - box[0], 12 - box[1]), text, font=font, fill=(0, 0, 0, 200))
    shadow = shadow.filter(ImageFilter.GaussianBlur(4))
    ImageDraw.Draw(shadow).text((12 - box[0], 12 - box[1]), text, font=font, fill=(255, 255, 255, 215))
    return np.array(shadow)[:, :, [2, 1, 0, 3]]


def overlay(frame, patch, cx, top, scale):
    if scale != 1.0:
        h, w = patch.shape[:2]
        patch = cv2.resize(patch, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_LINEAR)
    h, w = patch.shape[:2]
    x0 = int(cx - w / 2)
    y0 = int(top + (1 - scale) * h / 2)
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(OUT_W, x0 + w), min(OUT_H, y0 + h)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    p = patch[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
    alpha = p[:, :, 3:4].astype(np.float32) / 255.0
    region = frame[fy0:fy1, fx0:fx1].astype(np.float32)
    frame[fy0:fy1, fx0:fx1] = (p[:, :, :3] * alpha + region * (1 - alpha)).astype(np.uint8)


def ease(x):
    x = min(1.0, max(0.0, x))
    return 4 * x * x * x if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def plan_zooms(words, frames, fps, duration):
    seg_starts = {}
    for k, f in enumerate(frames):
        seg_starts.setdefault(f["seg"], k / fps)
    candidates = []
    for i, w in enumerate(words):
        prev = words[i - 1] if i > 0 else None
        if prev and prev["text"].rstrip().endswith((".", "?", "!")):
            candidates.append((w["start"], MIN_ZOOM_GAP))
        elif is_key_word(w["text"], prev["text"] if prev else None):
            candidates.append((w["start"], 3.4))
        elif prev and w["start"] - prev["end"] > 0.2:
            candidates.append((w["start"], 4.5))
    candidates.sort()
    events = []
    last = 0.0
    level_in = False
    strengths = [1.10, 1.14, 1.08, 1.12]
    for t, needed_gap in candidates:
        if t < 0.8 or t > duration - 1.0 or t - last < needed_gap:
            continue
        k = min(len(frames) - 1, int(t * fps))
        if frames[k]["mode"] != "crop" or t - seg_starts[frames[k]["seg"]] < 0.6:
            continue
        level_in = not level_in
        punch_ins = sum(1 for e in events if e["zoom"] > 1.0)
        z = strengths[punch_ins % len(strengths)] if level_in else 1.0
        events.append({"time": round(t, 3), "zoom": z})
        last = t
    return events


def plan_pulses(words, peaks, duration):
    moments = [(p, True) for p in peaks if 0.3 <= p <= duration - 0.8]
    for i, w in enumerate(words):
        prev = words[i - 1] if i > 0 else None
        starts = prev is None or prev["text"].rstrip().endswith((".", "?", "!"))
        key = is_key_word(w["text"], prev["text"] if prev else None)
        if starts or key:
            moments.append((w["start"], False))
    moments.sort()
    kept = []
    for t, forced in moments:
        if t < 0.8 and not forced or t > duration - 1.0:
            continue
        if forced:
            kept = [k for k in kept if abs(k - t) >= MIN_ZOOM_GAP]
            kept.append(t)
            kept.sort()
            continue
        if all(abs(k - t) >= MIN_ZOOM_GAP for k in kept) and not any(0 <= p - t < MIN_ZOOM_GAP for p in peaks):
            kept.append(t)
            kept.sort()
    strengths = [1.12, 1.08, 1.14, 1.10]
    events = []
    for n, t in enumerate(kept):
        nxt = kept[n + 1] if n + 1 < len(kept) else duration
        hold = min(PULSE_HOLD, nxt - t - 0.4)
        if hold < 0.6:
            continue
        z = 1.16 if t in peaks else strengths[n % len(strengths)]
        events.append({"time": round(t, 3), "zoom": z})
        events.append({"time": round(t + hold, 3), "zoom": 1.0})
    return events


def zoom_track(events, frames, fps):
    n = len(frames)
    z = np.ones(n)
    current, target, t_start = 1.0, 1.0, -10.0
    ev = list(events)
    seg = frames[0]["seg"] if frames else 0
    for k in range(n):
        t = k / fps
        if frames[k]["seg"] != seg:
            seg = frames[k]["seg"]
            current = target = 1.0
            t_start = -10.0
            ev = [e for e in ev if e["time"] > t]
            if ev and ev[0]["zoom"] == 1.0:
                ev.pop(0)
        if ev and t >= ev[0]["time"]:
            e = ev.pop(0)
            current = current + (target - current) * ease((t - t_start) / ZOOM_EASE)
            target = e["zoom"]
            t_start = e["time"]
        value = current + (target - current) * ease((t - t_start) / ZOOM_EASE)
        z[k] = value if frames[k]["mode"] in ("crop", "stream") else 1.0
    return z


def safe_zoom(z, face, panel_h=OUT_H):
    if face is None:
        return 1.0, OUT_W / 2, panel_h / 2
    fx, fy, fw, fh = face
    margin = 0.15 * fh
    fit_w = OUT_W / (fw + 2 * margin)
    fit_h = panel_h / (fh * 1.6 + 2 * margin)
    z = max(1.0, min(z, fit_w, fit_h))
    win_w, win_h = OUT_W / z, panel_h / z
    cx = fx + fw / 2
    cy = fy + fh * 0.7
    cx = min(max(cx, win_w / 2), OUT_W - win_w / 2)
    cy = min(max(cy, win_h / 2), panel_h - win_h / 2)
    return z, cx, cy


def apply_zoom(frame, z, cx, cy, panel_h=OUT_H):
    if z <= 1.0005:
        return frame
    win_w, win_h = OUT_W / z, panel_h / z
    x0, y0 = cx - win_w / 2, cy - win_h / 2
    m = np.float32([[z, 0, -x0 * z], [0, z, -y0 * z]])
    panel = cv2.warpAffine(frame[:panel_h], m, (OUT_W, panel_h), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_REPLICATE)
    if panel_h == OUT_H:
        return panel
    frame[:panel_h] = panel
    return frame


def read_audio(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "2", "-ar", str(RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def read_wav(path):
    with wave.open(path) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32767
        return data.reshape(-1, w.getnchannels())[:, :2]


def write_wav(path, samples):
    data = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


def mix_audio(cut_path, zoom_events, sfx_on, out_wav, min_gap=0.0):
    audio = read_audio(cut_path)
    if sfx_on and os.path.exists(WHOOSH):
        whoosh = read_wav(WHOOSH) * 0.2
        last = -1e9
        for e in zoom_events:
            if e["zoom"] <= 1.0 or e["time"] - last < min_gap:
                continue
            last = e["time"]
            at = int(max(0.0, e["time"] - 0.12) * RATE)
            end = min(len(audio), at + len(whoosh))
            if end > at:
                audio[at:end] += whoosh[: end - at]
    write_wav(out_wav, audio)


def loudnorm_filter(wav):
    target = "I=-14:TP=-1.5:LRA=11"
    res = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", wav, "-af", f"loudnorm={target}:print_format=json",
                          "-f", "null", "-"], capture_output=True, text=True)
    text = res.stderr
    try:
        stats = json.loads(text[text.rindex("{"):text.rindex("}") + 1])
        return (f"loudnorm={target}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
                f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
                f"offset={stats['target_offset']}:linear=true")
    except (ValueError, KeyError):
        return f"loudnorm={target}"


def caption_top(frames, zooms, stream=None):
    if stream:
        return stream["cam_h"] + stream["divider"] + STREAM_CAPTION_GAP
    bottoms = []
    for f, z in zip(frames, zooms):
        if f["mode"] == "crop" and f["face"]:
            zz, cx, cy = safe_zoom(z, f["face"])
            fy, fh = f["face"][1], f["face"][3]
            bottom = (fy + fh * 1.12 - cy) * zz + OUT_H / 2
            bottoms.append(bottom)
    if not bottoms:
        return CAPTION_TOP_MIN
    return int(min(CAPTION_TOP_MAX, max(CAPTION_TOP_MIN, np.percentile(bottoms, 90) + 30)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--color", default=None)
    ap.add_argument("--no-sfx", action="store_true")
    ap.add_argument("--only", type=int, default=None)
    a = ap.parse_args()
    work = a.workdir
    data = load_clips(work)
    words_all = join_split_words(load_json(os.path.join(work, "transcript.json")))
    for i, w in enumerate(words_all):
        w["index"] = i
    out_dir = os.path.join(work, "clips")
    os.makedirs(out_dir, exist_ok=True)
    names = [clip_name(i) for i in range(len(data["clips"]))]
    cut_paths = [os.path.join(work, "cuts", f"{n}.mp4") for n in names]
    color = a.color or data["settings"].get("color")
    if not color or color == "auto":
        color = auto_accent([p for p in cut_paths if os.path.exists(p)][:3])
        print(f"caption accent colour (auto): {color}")
    accent_rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    sfx_on = not a.no_sfx and data["settings"].get("sfx", True)
    font_cache = {}
    credit = load_credit(work, data["settings"])
    credit_patch = render_credit(credit["text"]) if credit and credit.get("text") else None
    for index, clip in enumerate(data["clips"]):
        if a.only and index + 1 != a.only:
            continue
        started = time.time()
        name = names[index]
        plan = load_json(os.path.join(work, "cuts", f"{name}.json"))
        fps, frames = plan["fps"], plan["frames"]
        start, end = plan["start"], plan["end"]
        duration = len(frames) / fps
        stream = plan.get("stream")
        panel_h = stream["cam_h"] if stream else OUT_H
        skip = clip.get("skip_captions") or []
        words = [dict(w, start=w["start"] - start, end=w["end"] - start)
                 for w in words_all if w["start"] >= start - 0.05 and w["end"] <= end + 0.05]
        shown = [w for w in words if not any(a0 - start <= w["start"] < a1 - start for a0, a1 in skip)]
        captions = caption_events(shown, duration)
        patches = [render_caption(e, words_all, accent_rgb, font_cache) for e in captions]
        if stream:
            zoom_events = plan_pulses(shown, plan.get("peaks", []), duration)
        else:
            zoom_events = plan_zooms(words, frames, fps, duration)
        zooms = zoom_track(zoom_events, frames, fps)
        top = caption_top(frames, zooms, stream)
        wav = os.path.join(work, "cuts", f"{name}-mix.wav")
        if stream:
            mix_audio(cut_paths[index], [e for e in zoom_events if e["zoom"] >= 1.16], sfx_on, wav, min_gap=6.0)
        else:
            mix_audio(cut_paths[index], zoom_events, sfx_on, wav)
        out_path = os.path.join(out_dir, f"{name}.mp4")
        writer = open_writer(out_path, ["-i", wav], fps=fps, crf=17, preset="medium", extra_audio_filter=loudnorm_filter(wav))
        ci = 0
        layout_faces = []
        boxes = []
        for k, frame in enumerate(read_frames(cut_paths[index], 0, None, fps)):
            if k >= len(frames):
                break
            t = k / fps
            f = frames[k]
            if f["mode"] in ("crop", "stream"):
                z, cx, cy = safe_zoom(zooms[k], f["face"], panel_h)
            else:
                z, cx, cy = 1.0, OUT_W / 2, OUT_H / 2
            out = apply_zoom(frame.copy(), z, cx, cy, panel_h)
            while ci < len(captions) and captions[ci]["end"] <= t:
                ci += 1
            if ci < len(captions) and captions[ci]["start"] <= t < captions[ci]["end"] and patches[ci] is not None:
                age = t - captions[ci]["start"]
                scale = 0.86 + 0.14 * ease(age / 0.1) if age < 0.1 else 1.0
                overlay(out, patches[ci], OUT_W / 2, top, scale)
                ph, pw = patches[ci].shape[:2]
                boxes.append([int(OUT_W / 2 - pw / 2), top, int(OUT_W / 2 + pw / 2), top + ph])
            if credit_patch is not None:
                overlay(out, credit_patch, CREDIT_X + credit_patch.shape[1] / 2, CREDIT_Y, 1.0)
            if k % int(round(fps)) == 0:
                face = None
                if f["face"]:
                    fx, fy, fw, fh = f["face"]
                    face = [round((fx - cx) * z + OUT_W / 2), round((fy - cy) * z + panel_h / 2),
                            round(fw * z), round(fh * z)]
                layout_faces.append({"t": round(t, 2), "mode": f["mode"], "face": face, "zoom": round(z, 3)})
            writer.stdin.write(out.tobytes())
        close_writer(writer)
        os.remove(wav)
        caption_box = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                       max(b[3] for b in boxes)] if boxes else None
        save_json(os.path.join(out_dir, f"{name}.layout.json"),
                  {"caption_box": caption_box, "caption_top": top, "accent": color, "zooms": zoom_events,
                   "samples": layout_faces, "duration": duration,
                   "credit": credit["text"] if credit_patch is not None else None,
                   "stream": {"cam_h": stream["cam_h"], "divider": stream["divider"]} if stream else None})
        with open(os.path.join(out_dir, f"{name}.txt"), "w") as fh:
            fh.write(f"{clip.get('title', '')}\n\n{clip.get('caption', '')}\n")
            if credit and credit.get("url") and credit["url"] not in clip.get("caption", ""):
                fh.write(f"\nClipped from {credit.get('name') or credit['text']}: {credit['url']}\n")
        n_in = sum(1 for e in zoom_events if e["zoom"] > 1)
        print(f"{name}: {duration:.1f} s, {len(captions)} captions, {n_in} punch-ins, "
              f"{time.time() - started:.0f} s to edit -> {out_path}")


if __name__ == "__main__":
    main()
