import argparse
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (CAM_H, OUT_H, OUT_W, compose_stream, game_crop_in, grab_frame, load_json, save_json)

AN_W, AN_H = 960, 540
SAMPLES = 80
MIN_PRESENCE = 0.2
MAX_CAM_FACE = 0.3
MAX_CAM_AREA = 0.4
MIN_GAME_ACTIVE = 0.45
EDGE_SNAP = 0.08
STATIC_EDGE = 0.45
HEADROOM = 0.27
OVERLAY_MIN = 0.05
OVERLAY_RATIO = 2.5


def detectors():
    base = cv2.data.haarcascades
    return (cv2.CascadeClassifier(os.path.join(base, "haarcascade_frontalface_default.xml")),
            cv2.CascadeClassifier(os.path.join(base, "haarcascade_profileface.xml")))


def find_faces(dets, gray):
    frontal, profile = dets
    side = int(AN_H * 0.045)
    found = [tuple(int(v) for v in f) for f in
             frontal.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(side, side))]
    for flipped, img in ((False, gray), (True, cv2.flip(gray, 1))):
        for x, y, w, h in profile.detectMultiScale(img, scaleFactor=1.1, minNeighbors=7, minSize=(side, side)):
            if flipped:
                x = AN_W - x - w
            box = (int(x), int(y), int(w), int(h))
            if not any(abs(box[0] - f[0]) < f[2] * 0.5 and abs(box[1] - f[1]) < f[3] * 0.5 for f in found):
                found.append(box)
    return found


def sample(source, duration, start=0.0, count=SAMPLES):
    times = start + np.linspace(min(2.0, duration * 0.02), duration - 0.5, count)
    frames = []
    for t in times:
        img = grab_frame(source, float(t), AN_W, AN_H)
        if img is not None:
            frames.append((float(t), img))
    return frames


def cluster(detections, total):
    clusters = []
    for i, box in detections:
        cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
        best = None
        for c in clusters:
            tol = 0.9 * c["size"]
            if abs(cx - c["cx"]) < tol and abs(cy - c["cy"]) < tol and abs(box[3] - c["size"]) < 0.6 * c["size"]:
                best = c
                break
        if best is None:
            clusters.append({"cx": cx, "cy": cy, "size": box[3], "items": [(i, box)]})
        else:
            best["items"].append((i, box))
            best["cx"] = float(np.median([b[0] + b[2] / 2 for _, b in best["items"]]))
            best["cy"] = float(np.median([b[1] + b[3] / 2 for _, b in best["items"]]))
            best["size"] = float(np.median([b[3] for _, b in best["items"]]))
    for c in clusters:
        c["presence"] = len({i for i, _ in c["items"]}) / max(1, total)
    return sorted(clusters, key=lambda c: -c["presence"])


def static_edges(grays):
    acc = np.zeros(grays[0].shape, np.float32)
    for g in grays:
        acc += (cv2.Canny(g, 60, 150) > 0).astype(np.float32)
    return acc / len(grays)


def snap_edge(edges, axis, lo, hi, span0, span1):
    lo, hi = int(max(1, lo)), int(min((edges.shape[1] if axis == "x" else edges.shape[0]) - 2, hi))
    span0, span1 = int(max(0, span0)), int(span1)
    best, best_score = None, STATIC_EDGE
    for p in range(lo, hi + 1):
        if axis == "x":
            band = edges[span0:span1, p - 1:p + 2]
        else:
            band = edges[p - 1:p + 2, span0:span1]
        if band.size == 0:
            continue
        score = float(np.mean(np.max(band, axis=1 if axis == "x" else 0)))
        if score > best_score:
            best, best_score = p, score
    return best


def cam_box_from(members, edges):
    boxes = [b for c in members for _, b in c["items"]]
    xs0 = np.percentile([b[0] for b in boxes], 10)
    ys0 = np.percentile([b[1] for b in boxes], 10)
    xs1 = np.percentile([b[0] + b[2] for b in boxes], 90)
    ys1 = np.percentile([b[1] + b[3] for b in boxes], 90)
    fw = float(np.median([b[2] for b in boxes]))
    fh = float(np.median([b[3] for b in boxes]))
    x0, x1 = xs0 - 0.6 * fw, xs1 + 0.6 * fw
    y0, y1 = ys0 - 0.9 * fh, ys1 + 1.6 * fh
    snapped = {}
    left = snap_edge(edges, "x", x0 - fw, xs0 - 0.25 * fw, ys0, ys1 + fh)
    right = snap_edge(edges, "x", xs1 + 0.25 * fw, x1 + fw, ys0, ys1 + fh)
    top = snap_edge(edges, "y", y0 - fh, ys0 - 0.25 * fh, xs0, xs1)
    bottom = snap_edge(edges, "y", ys1 + 0.5 * fh, y1 + fh, xs0, xs1)
    if left is not None:
        x0, snapped["left"] = left + 2, True
    if right is not None:
        x1, snapped["right"] = right - 1, True
    if top is not None:
        y0, snapped["top"] = top + 2, True
    if bottom is not None:
        y1, snapped["bottom"] = bottom - 1, True
    if not snapped.get("left") and x0 < EDGE_SNAP * AN_W:
        x0 = 0
    if not snapped.get("right") and x1 > (1 - EDGE_SNAP) * AN_W:
        x1 = AN_W
    if not snapped.get("top") and y0 < EDGE_SNAP * AN_H:
        y0 = 0
    if not snapped.get("bottom") and y1 > (1 - EDGE_SNAP) * AN_H:
        y1 = AN_H
    box = [max(0.0, x0), max(0.0, y0), min(float(AN_W), x1), min(float(AN_H), y1)]
    faces = [float(xs0), float(ys0), float(xs1), float(ys1)]
    return box, faces, sorted(snapped)


def activity(grays):
    return np.stack(grays).astype(np.float32).std(axis=0) > 18


def game_area(active, cam=None):
    valid = np.ones(active.shape, bool)
    if cam is not None:
        bx0, by0, bx1, by1 = [int(v) for v in cam]
        valid[by0:by1, bx0:bx1] = False
    hits = active & valid
    rows = hits.sum(axis=1) / np.maximum(1, valid.sum(axis=1))
    cols = hits.sum(axis=0) / np.maximum(1, valid.sum(axis=0))
    y0 = int(np.argmax(rows > 0.4)) if (rows > 0.4).any() else 0
    y1 = AN_H - int(np.argmax(rows[::-1] > 0.4)) if (rows > 0.4).any() else AN_H
    x0 = int(np.argmax(cols > 0.4)) if (cols > 0.4).any() else 0
    x1 = AN_W - int(np.argmax(cols[::-1] > 0.4)) if (cols > 0.4).any() else AN_W
    if (x1 - x0) < AN_W * 0.5 or (y1 - y0) < AN_H * 0.5:
        x0, y0, x1, y1 = 0, 0, AN_W, AN_H
    return [x0, y0, x1, y1]


def game_region(area, cam):
    gx0, gy0, gx1, gy1 = area
    cx0, cy0, cx1, cy1 = cam
    options = []
    if cx1 <= gx0 or cx0 >= gx1 or cy1 <= gy0 or cy0 >= gy1:
        options.append([gx0, gy0, gx1, gy1])
    options += [[max(gx0, cx1), gy0, gx1, gy1], [gx0, gy0, min(gx1, cx0), gy1],
                [gx0, gy0, gx1, min(gy1, cy0)], [gx0, max(gy0, cy1), gx1, gy1]]
    best, best_key = None, None
    centre = (gx0 + gx1) / 2
    for r in options:
        if r[2] - r[0] < 10 or r[3] - r[1] < 10:
            continue
        crop = game_crop_in(r, area)
        h = crop[3] - crop[1]
        key = (round(h / (gy1 - gy0), 2), -abs((crop[0] + crop[2]) / 2 - centre))
        if best_key is None or key > best_key:
            best, best_key = r, key
    if best is None or best_key[0] < 0.5:
        return [gx0, gy0, gx1, gy1], False
    return best, True


def overlay_mask(edges, area, cam):
    gx0, gy0, gx1, gy1 = [int(v) for v in area]
    cell = 24
    blocks = []
    for y in range(gy0, gy1 - cell + 1, cell):
        for x in range(gx0, gx1 - cell + 1, cell):
            blocks.append((x, y, float(edges[y:y + cell, x:x + cell].mean())))
    mask = np.zeros(edges.shape, bool)
    if not blocks:
        return mask
    floor = max(OVERLAY_MIN, OVERLAY_RATIO * float(np.median([b[2] for b in blocks])))
    for x, y, v in blocks:
        if v > floor:
            mask[y:y + cell, x:x + cell] = True
    bx0, by0, bx1, by1 = [int(v) for v in cam]
    mask[by0:by1, bx0:bx1] = False
    return cv2.dilate(mask.astype(np.uint8), np.ones((17, 17), np.uint8)) > 0


def best_game_crop(region, area, mask):
    best, best_cost = (None, 1.0), None
    centre = (area[0] + area[2]) / 2
    for zoom in (1.0, 1.05, 1.1, 1.15):
        for gx in np.linspace(0, 1, 21):
            x0, y0, x1, y1 = [int(round(v)) for v in game_crop_in(region, area, gx, zoom)]
            covered = float(mask[y0:y1, x0:x1].mean()) if y1 > y0 and x1 > x0 else 1.0
            cost = 5.0 * covered + 0.12 * (zoom - 1.0) + 0.02 * abs((x0 + x1) / 2 - centre) / AN_W
            if best_cost is None or cost < best_cost - 1e-6:
                best, best_cost = (round(float(gx), 2), zoom), cost
    return best


def cam_crop_in(box, faces, bordered_top):
    x0, y0, x1, y1 = box
    aspect = OUT_W / CAM_H
    w, h = x1 - x0, y1 - y0
    fcx = (faces[0] + faces[2]) / 2
    if w / h > aspect:
        cw, ch = h * aspect, h
    else:
        cw, ch = w, w / aspect
    cx = min(max(fcx, x0 + cw / 2), x1 - cw / 2)
    top = min(faces[1] - HEADROOM * ch, y1 - ch)
    top = max(y0 if bordered_top else 0.0, top)
    top = min(top, AN_H - ch)
    return [cx - cw / 2, top, cx + cw / 2, top + ch]


def scale_box(box, s):
    return [float(round(float(v) * s, 1)) for v in box]


def preview(source, t, layout, path, mask=None):
    frame = grab_frame(source, t)
    if frame is None:
        return
    shown = frame.copy()
    if mask is not None and mask.any():
        big = cv2.resize(mask.astype(np.uint8), (shown.shape[1], shown.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        shown[big] = (shown[big] * 0.5 + np.array([0, 0, 255]) * 0.5).astype(np.uint8)
    colours = {"cam_box": (0, 220, 255), "cam_crop": (0, 140, 255), "game_crop": (80, 255, 80), "game_area": (255, 120, 0)}
    for key, colour in colours.items():
        if layout.get(key):
            x0, y0, x1, y1 = [int(v) for v in layout[key]]
            cv2.rectangle(shown, (x0, y0), (x1, y1), colour, 4)
            cv2.putText(shown, key, (x0 + 8, y0 + 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, colour, 2)
    left = cv2.resize(shown, (1280, int(1280 * shown.shape[0] / shown.shape[1])), interpolation=cv2.INTER_AREA)
    stacked = compose_stream(frame, layout["cam_crop"], layout["game_crop"])
    right = cv2.resize(stacked, (int(OUT_W * left.shape[0] * 1.6 / OUT_H), int(left.shape[0] * 1.6)))
    canvas_h = max(left.shape[0], right.shape[0])
    canvas = np.full((canvas_h, left.shape[1] + right.shape[1] + 20, 3), 24, np.uint8)
    canvas[:left.shape[0], :left.shape[1]] = left
    canvas[:right.shape[0], left.shape[1] + 20:] = right
    cv2.imwrite(path, canvas, [cv2.IMWRITE_JPEG_QUALITY, 85])


def analyse(source, src_w, src_h, start, length, mode, count=SAMPLES):
    """Decides talk or stream for one stretch of the video and finds the cam and gameplay areas in it."""
    s = src_w / AN_W
    frames = sample(source, length, start, count)
    grays = [cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) for _, img in frames]
    dets = detectors()
    detections = []
    for i, g in enumerate(grays):
        for f in find_faces(dets, cv2.equalizeHist(g)):
            detections.append((i, f))
    clusters = cluster(detections, len(frames))
    stable = [c for c in clusters if c["presence"] >= MIN_PRESENCE and c["size"] < MAX_CAM_FACE * AN_H]
    active = activity(grays)
    area = game_area(active)
    layout = {"mode": "talk", "source_size": [src_w, src_h], "samples": len(frames)}
    mask = None
    reason = ""
    if not stable:
        reason = "no face stays in one place across the video"
    else:
        main_c = stable[0]
        members = [main_c] + [c for c in clusters[1:] if c["presence"] >= MIN_PRESENCE * 0.5
                              and c["size"] < MAX_CAM_FACE * AN_H
                              and abs(c["cx"] - main_c["cx"]) < 3.5 * main_c["size"]
                              and abs(c["cy"] - main_c["cy"]) < 2.0 * main_c["size"]]
        edges = static_edges(grays)
        box, faces, snapped = cam_box_from(members, edges)
        area_ratio = (box[2] - box[0]) * (box[3] - box[1]) / (AN_W * AN_H)
        bx0, by0, bx1, by1 = [int(v) for v in box]
        outside = active.copy()
        outside[by0:by1, bx0:bx1] = False
        cells = active.size - active[by0:by1, bx0:bx1].size
        outside_active = float(outside.sum()) / max(1, cells)
        layout.update(presence=round(main_c["presence"], 2), people=len(members), cam_area=round(area_ratio, 3),
                      active_outside=round(outside_active, 3), snapped_edges=snapped)
        looks_stream = area_ratio <= MAX_CAM_AREA and outside_active >= MIN_GAME_ACTIVE
        if mode == "stream" or looks_stream:
            area = game_area(active, box)
            region, clean = game_region(area, box)
            mask = overlay_mask(edges, area, box)
            game_x, game_zoom = best_game_crop(region, area, mask)
            crop = game_crop_in(region, area, game_x, game_zoom)
            gx0, gy0, gx1, gy1 = [int(round(v)) for v in crop]
            layout.update(
                mode="stream",
                cam_box=scale_box(box, s), cam_faces=scale_box(faces, s),
                cam_crop=scale_box(cam_crop_in(box, faces, "top" in snapped), s),
                game_area=scale_box(area, s), game_region=scale_box(region, s),
                game_x=game_x, game_zoom=game_zoom,
                game_crop=scale_box(crop, s),
                game_avoids_cam=clean,
                overlay_share=round(float(mask.mean()), 3),
                overlay_in_game_crop=round(float(mask[gy0:gy1, gx0:gx1].mean()), 3))
            reason = (f"a face stays in the same {area_ratio:.0%} of the frame in {main_c['presence']:.0%} of samples "
                      f"while {outside_active:.0%} of the rest keeps changing")
            if mode == "stream" and not looks_stream:
                reason = "chosen by the person; " + reason
        else:
            reason = (f"the steady face covers {area_ratio:.0%} of the frame and only {outside_active:.0%} of the rest "
                      f"changes, so this looks like a talking-head video")
    layout["reason"] = reason
    best = max(range(len(frames)), key=lambda i: sum(1 for j, _ in detections if j == i)) if frames else 0
    return layout, mask, (frames[best][0] if frames else start)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--mode", choices=["auto", "stream", "talk"], default="auto")
    a = ap.parse_args()
    started = time.time()
    work = a.workdir
    source = os.path.join(work, "source.mp4")
    meta = load_json(os.path.join(work, "meta.json"))
    src_w, src_h = meta["width"], meta["height"]
    out_path = os.path.join(work, "layout.json")
    if a.mode == "talk":
        save_json(out_path, {"mode": "talk", "reason": "chosen by the person"})
        print("mode: talk (chosen)")
        return
    windows = meta.get("windows") or []
    layout, mask, shot = analyse(source, src_w, src_h, 0.0, meta["duration"], a.mode)
    reason = layout["reason"]
    if len(windows) > 1:
        # A stream changes scene (gameplay, just chatting, watching a video): each stretch gets its own layout.
        layout["windows"] = []
        for n, w in enumerate(windows, 1):
            part, part_mask, part_shot = analyse(source, src_w, src_h, w["at"], w["length"], a.mode, 36)
            part.update(at=w["at"], length=w["length"])
            layout["windows"].append(part)
            print(f"stretch {n} ({w['at']:.0f}-{w['at'] + w['length']:.0f} s): {part['mode']} ({part['reason']})")
            if part["mode"] == "stream":
                preview(source, part_shot, part, os.path.join(work, f"layout-preview-{n:02d}.jpg"), part_mask)
    save_json(out_path, layout)
    print(f"mode: {layout['mode']} ({reason})")
    if layout["mode"] == "stream":
        preview(source, shot, layout, os.path.join(work, "layout-preview.jpg"), mask)
        print(f"cam box {layout['cam_box']}, game crop {layout['game_crop']}")
        if not layout["game_avoids_cam"]:
            print("warning: the gameplay crop could not avoid the cam box; look at layout-preview.jpg")
        print("wrote layout.json and layout-preview.jpg")
    elif a.mode == "stream":
        print("no facecam found, so clips use the talking-head framing")
    print(f"{time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
