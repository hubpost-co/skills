import json
import os
import subprocess

import cv2
import numpy as np

OUT_W, OUT_H, OUT_FPS = 1080, 1920, 30


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_clips(work):
    data = load_json(os.path.join(work, "clips.json"))
    if isinstance(data, list):
        return {"settings": {}, "clips": data}
    data.setdefault("settings", {})
    return data


def clip_name(index):
    return f"clip-{index + 1:02d}"


def probe_size(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split("\n")[0].split(",")
    return int(w), int(h)


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def read_frames(path, start, duration, fps, width=None, height=None):
    src_w, src_h = probe_size(path)
    w, h = width or src_w, height or src_h
    vf = f"fps={fps}"
    if (w, h) != (src_w, src_h):
        vf += f",scale={w}:{h}:flags=area"
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * 3 * 4)
    size = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def open_writer(path, audio_args, fps=OUT_FPS, crf=18, extra_audio_filter=None, preset="veryfast"):
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}", "-r", str(fps), "-i", "-"]
    cmd += audio_args
    cmd += ["-map", "0:v:0", "-map", "1:a:0?", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    if extra_audio_filter:
        cmd += ["-af", extra_audio_filter]
    cmd += ["-shortest", "-movflags", "+faststart", path]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def close_writer(proc):
    proc.stdin.close()
    code = proc.wait()
    if code != 0:
        raise RuntimeError("ffmpeg failed while writing the video")


CAM_H = 760
DIVIDER = 8
GAME_H = OUT_H - CAM_H - DIVIDER
DIVIDER_BGR = (12, 12, 12)


def grab_frame(path, t, width=None, height=None):
    vf = []
    if width and height:
        vf = ["-vf", f"scale={width}:{height}:flags=area"]
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1", "-an"]
    cmd += vf + ["-f", "image2pipe", "-vcodec", "png", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    if not raw:
        return None
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def compose_stream(frame, cam_crop, game_crop):
    out = np.empty((OUT_H, OUT_W, 3), np.uint8)
    x0, y0, x1, y1 = [int(round(v)) for v in cam_crop]
    cam = frame[y0:y1, x0:x1]
    up = OUT_W > cam.shape[1]
    out[:CAM_H] = cv2.resize(cam, (OUT_W, CAM_H), interpolation=cv2.INTER_CUBIC if up else cv2.INTER_AREA)
    out[CAM_H:CAM_H + DIVIDER] = DIVIDER_BGR
    x0, y0, x1, y1 = [int(round(v)) for v in game_crop]
    game = frame[y0:y1, x0:x1]
    up = OUT_W > game.shape[1]
    out[CAM_H + DIVIDER:] = cv2.resize(game, (OUT_W, GAME_H), interpolation=cv2.INTER_CUBIC if up else cv2.INTER_AREA)
    return out


def game_crop_in(region, game_area, game_x=None, zoom=1.0):
    rx0, ry0, rx1, ry1 = region
    gx0, gy0, gx1, gy1 = game_area
    aspect = OUT_W / GAME_H
    rw, rh = rx1 - rx0, ry1 - ry0
    if rw / rh > aspect:
        ch, cw = rh, rh * aspect
    else:
        cw, ch = rw, rw / aspect
    zoom = max(1.0, float(zoom or 1.0))
    cw, ch = cw / zoom, ch / zoom
    if game_x is None:
        cx = min(max((gx0 + gx1) / 2, rx0 + cw / 2), rx1 - cw / 2)
    else:
        cx = rx0 + cw / 2 + float(game_x) * (rw - cw)
    cy = min(max((gy0 + gy1) / 2, ry0 + ch / 2), ry1 - ch / 2)
    return [round(cx - cw / 2, 1), round(cy - ch / 2, 1), round(cx + cw / 2, 1), round(cy + ch / 2, 1)]


def ytdlp_command():
    import shutil
    import sys
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def source_info(url):
    """Who made the video: used for the on-screen credit and the credit line of every post."""
    res = subprocess.run(ytdlp_command() + ["-J", "--no-playlist", "--no-warnings", url],
                         capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        return None, None
    info = json.loads(res.stdout)
    site = (info.get("extractor_key") or info.get("extractor") or "").lower()
    name = info.get("uploader") or info.get("channel") or info.get("uploader_id") or ""
    handle = info.get("uploader_id") or ""
    link = info.get("uploader_url") or info.get("channel_url") or ""
    if "twitch" in site:
        login = (handle or name).lower()
        link = f"https://www.twitch.tv/{login}"
    elif "youtube" in site and handle.startswith("@"):
        link = f"https://www.youtube.com/{handle}"
    credit = {"name": name, "url": link, "text": credit_text(link, name),
              "video_url": info.get("webpage_url") or url, "title": info.get("title") or ""}
    return info, credit


def credit_text(link, name):
    if not link:
        return name
    short = link.split("://", 1)[-1]
    if short.startswith("www."):
        short = short[4:]
    return short.rstrip("/")


def window_of(meta, t):
    for w in meta.get("windows") or []:
        if w["at"] <= t < w["at"] + w["length"]:
            return w
    return None


def source_time(meta, t):
    """Seconds in the assembled source.mp4 -> seconds in the original stream (None when not assembled)."""
    w = window_of(meta, t)
    return None if w is None else w["src_start"] + (t - w["at"])
