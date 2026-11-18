import argparse
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (CAM_H, DIVIDER, GAME_H, OUT_H, OUT_W, clip_name, close_writer, compose_stream, game_crop_in,
                    load_clips, load_json, open_writer, read_frames, save_json)

ANALYSIS_FPS = 10
ANALYSIS_W, ANALYSIS_H = 640, 360
FACE_EVERY = 2
SMALL_FACE = 0.10
FIT_TOP = 560


def output_fps(src_fps):
    if src_fps > 31:
        return round(src_fps / 2, 3)
    return src_fps


def snap_to_words(clip, words):
    start, end = float(clip["start"]), float(clip["end"])
    inside = [i for i, w in enumerate(words) if w["end"] > start and w["start"] < end]
    if not inside:
        return start, end
    first, last = inside[0], inside[-1]
    if words[first]["start"] < start - 0.05 and words[first]["end"] - start < 0.5 * (words[first]["end"] - words[first]["start"]):
        first = min(first + 1, last)
    new_start = words[first]["start"] - 0.12
    if first > 0:
        new_start = max(new_start, words[first - 1]["end"] + 0.02)
    new_end = words[last]["end"] + 0.35
    if last + 1 < len(words):
        new_end = min(new_end, max(words[last]["end"] + 0.05, words[last + 1]["start"] - 0.05))
    return round(max(0.0, new_start), 3), round(new_end, 3)


def snap_soft(clip, words):
    start, end = float(clip["start"]), float(clip["end"])
    for i, w in enumerate(words):
        if w["start"] < start < w["end"]:
            start = w["start"] - 0.08
            if i > 0:
                start = max(start, words[i - 1]["end"] + 0.02)
        if w["start"] < end < w["end"]:
            end = w["end"] + 0.12
    return round(max(0.0, start), 3), round(end, 3)


def clip_peaks(clip, start, end):
    raw = clip.get("peaks") or ([clip["peak"]] if clip.get("peak") is not None else [])
    return [round(float(p) - start, 3) for p in raw if start <= float(p) <= end]


def stream_crops(layout, clip, settings):
    game_x = clip.get("game_x", settings.get("game_x"))
    zoom = clip.get("game_zoom", settings.get("game_zoom"))
    if game_x is None and zoom is None:
        return layout["cam_crop"], layout["game_crop"]
    if game_x is None:
        game_x = layout.get("game_x")
    if zoom is None:
        zoom = layout.get("game_zoom", 1.0)
    return layout["cam_crop"], game_crop_in(layout["game_region"], layout["game_area"], game_x, zoom)


def stream_face(source, start, duration, cam_crop, src_w, src_h, detectors):
    frontal, profile = detectors
    s = 960 / src_w
    x0, y0, x1, y1 = [int(round(v * s)) for v in cam_crop]
    side = max(16, int((y1 - y0) * 0.08))
    boxes = []
    for frame in read_frames(source, start, duration, 2, 960, int(round(960 * src_h / src_w / 2)) * 2):
        region = cv2.equalizeHist(cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY))
        found = list(frontal.detectMultiScale(region, scaleFactor=1.1, minNeighbors=5, minSize=(side, side)))
        if not found:
            found = list(profile.detectMultiScale(region, scaleFactor=1.1, minNeighbors=6, minSize=(side, side)))
        if found:
            boxes.append(max(found, key=lambda f: f[2] * f[3]))
    if not boxes:
        return None, 0
    ps = OUT_W / (x1 - x0)
    fx, fy, fw, fh = [float(np.median([b[k] for b in boxes])) for k in range(4)]
    return [round(fx * ps, 1), round(fy * ps, 1), round(fw * ps, 1), round(fh * ps, 1)], len(boxes)


def face_detector():
    path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    profile = os.path.join(cv2.data.haarcascades, "haarcascade_profileface.xml")
    return cv2.CascadeClassifier(path), cv2.CascadeClassifier(profile)


def detect_faces(detectors, gray):
    frontal, profile = detectors
    min_side = int(ANALYSIS_H * 0.07)
    found = frontal.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side))
    faces = [tuple(int(v) for v in f) for f in found]
    if not faces:
        for flipped, img in ((False, gray), (True, cv2.flip(gray, 1))):
            hits = profile.detectMultiScale(img, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side))
            for x, y, w, h in hits:
                if flipped:
                    x = ANALYSIS_W - x - w
                faces.append((int(x), int(y), int(w), int(h)))
            if faces:
                break
    return faces


def analyse(source, start, duration, detectors):
    smalls, grays, detections = [], [], []
    for i, frame in enumerate(read_frames(source, start, duration, ANALYSIS_FPS, ANALYSIS_W, ANALYSIS_H)):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        smalls.append(cv2.resize(cv2.GaussianBlur(gray, (9, 9), 0), (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32))
        grays.append(cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA))
        if i % FACE_EVERY == 0:
            for f in detect_faces(detectors, cv2.equalizeHist(gray)):
                detections.append((i, f))
    return smalls, grays, detections


def find_cuts(smalls):
    n = len(smalls)
    diffs = np.zeros(n)
    for i in range(1, n):
        diffs[i] = float(np.mean(np.abs(smalls[i] - smalls[i - 1])))
    cuts = [0]
    for i in range(1, n):
        around = max(diffs[i - 1] if i > 1 else 0.0, diffs[i + 1] if i + 1 < n else 0.0)
        if diffs[i] > 12 and diffs[i] > 2.5 * around and i - cuts[-1] >= 3:
            cuts.append(i)
    cuts.append(n)
    return list(zip(cuts[:-1], cuts[1:]))


def cluster_faces(faces):
    items = sorted(faces, key=lambda d: d[1][0] + d[1][2] / 2)
    clusters = []
    for idx, f in items:
        cx = f[0] + f[2] / 2
        if clusters and abs(cx - clusters[-1]["cx"]) < ANALYSIS_W * 0.12:
            c = clusters[-1]
            c["items"].append((idx, f))
            c["cx"] = float(np.mean([g[0] + g[2] / 2 for _, g in c["items"]]))
        else:
            clusters.append({"cx": cx, "items": [(idx, f)]})
    return clusters


def mouth_activity(grays, idx, f):
    if idx < 1 or idx >= len(grays):
        return 0.0
    s = 320 / ANALYSIS_W
    x, y, w, h = [int(v * s) for v in f]
    y0, y1 = y + int(h * 0.6), min(180, y + int(h * 1.05))
    x0, x1 = max(0, x + int(w * 0.2)), min(320, x + int(w * 0.8))
    if y1 <= y0 or x1 <= x0:
        return 0.0
    a = grays[idx][y0:y1, x0:x1].astype(np.float32)
    b = grays[idx - 1][y0:y1, x0:x1].astype(np.float32)
    return float(np.mean(np.abs(a - b)))


def speaker_segments(shot, clusters, grays):
    a, b = shot
    if len(clusters) == 1:
        return [(a, b, clusters[0])]
    window = ANALYSIS_FPS
    choices = []
    for w0 in range(a, b, window):
        w1 = min(b, w0 + window)
        scores = []
        for c in clusters:
            vals = [mouth_activity(grays, i, f) for i, f in c["items"] if w0 <= i < w1]
            scores.append(np.mean(vals) if vals else -1.0)
        choices.append((w0, w1, int(np.argmax(scores))))
    segments = []
    current = choices[0][2]
    seg_start = a
    pending = 0
    for k, (w0, w1, pick) in enumerate(choices):
        if pick != current:
            pending += 1
            if pending >= 2:
                switch_at = choices[k - 1][0]
                if switch_at > seg_start:
                    segments.append((seg_start, switch_at, clusters[current]))
                    seg_start = switch_at
                current = pick
                pending = 0
        else:
            pending = 0
    segments.append((seg_start, b, clusters[current]))
    return segments


def plan_clip(smalls, grays, detections, src_w, src_h, n_out, fps):
    scale = src_w / ANALYSIS_W
    crop_w = src_h * 9 / 16
    out_scale = OUT_H / src_h
    shots = find_cuts(smalls)
    n_an = len(smalls)
    frames = [None] * n_out
    segments_out = []
    seg_id = 0
    for shot in shots:
        a, b = shot
        faces = [d for d in detections if a <= d[0] < b]
        samples = max(1, len(range(a, b, FACE_EVERY)))
        clusters = [c for c in cluster_faces(faces) if len(c["items"]) >= max(2, 0.3 * samples)]
        big = [c for c in clusters if np.median([f[3] for _, f in c["items"]]) >= SMALL_FACE * ANALYSIS_H]
        parts = speaker_segments(shot, big, grays) if big else [(a, b, None)]
        for s0, s1, cluster in parts:
            o0 = int(round(s0 / ANALYSIS_FPS * fps))
            o1 = n_out if s1 >= n_an else int(round(s1 / ANALYSIS_FPS * fps))
            o1 = min(n_out, o1)
            if o1 <= o0:
                continue
            if cluster is None:
                for k in range(o0, o1):
                    frames[k] = {"mode": "fit", "x0": 0.0, "face": None, "seg": seg_id}
                segments_out.append({"start_frame": o0, "end_frame": o1, "mode": "fit"})
                seg_id += 1
                continue
            items = [(i, f) for i, f in cluster["items"] if s0 <= i < s1] or cluster["items"]
            t_an = np.array([i for i, _ in items], dtype=float)
            cxs = np.array([(f[0] + f[2] / 2) * scale for _, f in items])
            cys = np.array([(f[1] + f[3] / 2) * scale for _, f in items])
            size = float(np.median([f[3] for _, f in items])) * scale
            order = np.argsort(t_an)
            t_an, cxs, cys = t_an[order], cxs[order], cys[order]
            t_out = np.arange(o0, o1) / fps * ANALYSIS_FPS
            spread = np.percentile(cxs, 90) - np.percentile(cxs, 10)
            if spread < 0.12 * crop_w or len(cxs) < 4:
                track_x = np.full(len(t_out), float(np.median(cxs)))
                track_y = np.full(len(t_out), float(np.median(cys)))
            else:
                k = min(len(cxs) // 2 * 2 + 1, 5)
                med_x = np.array([np.median(cxs[max(0, j - k // 2): j + k // 2 + 1]) for j in range(len(cxs))])
                med_y = np.array([np.median(cys[max(0, j - k // 2): j + k // 2 + 1]) for j in range(len(cys))])
                track_x = np.interp(t_out, t_an, med_x)
                track_y = np.interp(t_out, t_an, med_y)
                sigma = 0.7 * fps
                radius = int(3 * sigma)
                kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
                kernel /= kernel.sum()
                track_x = np.convolve(np.pad(track_x, radius, mode="edge"), kernel, mode="valid")
                track_y = np.convolve(np.pad(track_y, radius, mode="edge"), kernel, mode="valid")
            for j, k in enumerate(range(o0, o1)):
                x0 = float(np.clip(track_x[j] - crop_w / 2, 0, src_w - crop_w))
                fx = (track_x[j] - size / 2 - x0) * out_scale
                fy = (track_y[j] - size / 2) * out_scale
                frames[k] = {"mode": "crop", "x0": round(x0, 2),
                             "face": [round(fx, 1), round(fy, 1), round(size * out_scale, 1), round(size * out_scale, 1)],
                             "seg": seg_id}
            segments_out.append({"start_frame": o0, "end_frame": o1, "mode": "crop"})
            seg_id += 1
    last = None
    for k in range(n_out):
        if frames[k] is None:
            frames[k] = dict(last) if last else {"mode": "fit", "x0": 0.0, "face": None, "seg": seg_id}
        last = frames[k]
    return frames, segments_out, crop_w


def render_frame(frame, plan, crop_w, src_h):
    if plan["mode"] == "crop":
        s = OUT_H / src_h
        m = np.float32([[s, 0, -plan["x0"] * s], [0, s, 0]])
        return cv2.warpAffine(frame, m, (OUT_W, OUT_H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    h, w = frame.shape[:2]
    cover = max(OUT_W / w, OUT_H / h)
    small = cv2.resize(frame, (int(w * cover / 10), int(h * cover / 10)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 6)
    bg = cv2.resize(small, (int(w * cover), int(h * cover)), interpolation=cv2.INTER_LINEAR)
    oy, ox = (bg.shape[0] - OUT_H) // 2, (bg.shape[1] - OUT_W) // 2
    out = (bg[oy:oy + OUT_H, ox:ox + OUT_W].astype(np.float32) * 0.45).astype(np.uint8)
    fg_h = int(round(h * OUT_W / w))
    fg = cv2.resize(frame, (OUT_W, fg_h), interpolation=cv2.INTER_AREA)
    out[FIT_TOP:FIT_TOP + fg_h] = fg
    return out


def cut_stream(work, source, name, clip, settings, layout, start, end, fps, src_w, detectors, started):
    duration = end - start
    cam_crop, game_crop = stream_crops(layout, clip, settings)
    face, seen = stream_face(source, start, duration, cam_crop, src_w, layout["source_size"][1], detectors)
    if face is None:
        fx0, fy0, fx1, fy1 = layout["cam_faces"]
        ps = OUT_W / (cam_crop[2] - cam_crop[0])
        face = [round((fx0 - cam_crop[0]) * ps, 1), round((fy0 - cam_crop[1]) * ps, 1),
                round((fx1 - fx0) * ps * 0.45, 1), round((fy1 - fy0) * ps * 0.6, 1)]
    n_out = int(duration * fps)
    out_path = os.path.join(work, "cuts", f"{name}.mp4")
    audio = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", source]
    writer = open_writer(out_path, audio, fps=fps, crf=14)
    count = 0
    for frame in read_frames(source, start, duration, fps):
        if count >= n_out:
            break
        writer.stdin.write(compose_stream(frame, cam_crop, game_crop).tobytes())
        count += 1
    close_writer(writer)
    frames = [{"mode": "stream", "x0": 0.0, "face": face, "seg": 0} for _ in range(count)]
    save_json(os.path.join(work, "cuts", f"{name}.json"),
              {"fps": fps, "start": start, "end": end, "frames": frames,
               "segments": [{"start_frame": 0, "end_frame": count, "mode": "stream"}],
               "stream": {"cam_h": CAM_H, "divider": DIVIDER, "game_h": GAME_H, "cam_crop": cam_crop,
                          "game_crop": game_crop},
               "peaks": clip_peaks(clip, start, end)})
    print(f"{name}: {duration:.1f} s, stream layout, face found in {seen} of {int(duration * 2)} samples, "
          f"{time.time() - started:.0f} s to cut")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--only", type=int, default=None)
    a = ap.parse_args()
    work = a.workdir
    source = os.path.join(work, "source.mp4")
    meta = load_json(os.path.join(work, "meta.json"))
    words = load_json(os.path.join(work, "transcript.json"))
    data = load_clips(work)
    os.makedirs(os.path.join(work, "cuts"), exist_ok=True)
    fps = output_fps(meta["fps"])
    detectors = face_detector()
    src_w, src_h = meta["width"], meta["height"]
    layout_path = os.path.join(work, "layout.json")
    layout = load_json(layout_path) if os.path.exists(layout_path) else {"mode": "talk"}
    for index, clip in enumerate(data["clips"]):
        if a.only and index + 1 != a.only:
            continue
        started = time.time()
        here = layout
        for part in layout.get("windows") or []:
            if part["at"] <= clip["start"] < part["at"] + part["length"]:
                here = part
        stream = clip.get("layout", here.get("mode")) == "stream" and "cam_crop" in here
        soft = stream or clip.get("peak") is not None or clip.get("peaks")
        start, end = snap_soft(clip, words) if soft else snap_to_words(clip, words)
        clip["start"], clip["end"] = start, end
        duration = end - start
        name = clip_name(index)
        if stream:
            cut_stream(work, source, name, clip, data["settings"], here, start, end, fps, src_w, detectors, started)
            continue
        smalls, grays, detections = analyse(source, start, duration, detectors)
        n_out = int(duration * fps)
        frames, segments, crop_w = plan_clip(smalls, grays, detections, src_w, src_h, n_out, fps)
        out_path = os.path.join(work, "cuts", f"{name}.mp4")
        audio = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", source]
        writer = open_writer(out_path, audio, fps=fps, crf=14)
        count = 0
        for frame in read_frames(source, start, duration, fps):
            if count >= n_out:
                break
            writer.stdin.write(render_frame(frame, frames[count], crop_w, src_h).tobytes())
            count += 1
        close_writer(writer)
        frames = frames[:count]
        save_json(os.path.join(work, "cuts", f"{name}.json"),
                  {"fps": fps, "start": start, "end": end, "frames": frames, "segments": segments})
        modes = sum(1 for f in frames if f["mode"] == "crop") / max(1, len(frames))
        print(f"{name}: {duration:.1f} s, {len(segments)} framings, {modes:.0%} face crop, "
              f"{time.time() - started:.0f} s to cut")
    save_json(os.path.join(work, "clips.json"), data)


if __name__ == "__main__":
    main()
