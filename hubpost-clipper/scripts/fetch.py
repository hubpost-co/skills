import argparse
import json
import os
import shutil
import subprocess
import sys


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
         "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    info = {"duration": float(data["format"]["duration"]), "has_audio": False}
    for s in data["streams"]:
        if s["codec_type"] == "video" and "width" not in info:
            num, den = s["r_frame_rate"].split("/")
            info.update(width=s["width"], height=s["height"], fps=round(float(num) / float(den), 3))
        if s["codec_type"] == "audio":
            info["has_audio"] = True
    return info


def is_url(text):
    return text.startswith("http://") or text.startswith("https://")


def ytdlp_command():
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def download(url, target):
    fmt = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b"
    cmd = ytdlp_command() + ["-f", fmt, "--merge-output-format", "mp4", "--no-playlist", "--no-progress", "-o", target, url]
    subprocess.run(cmd, check=True)


def bring_local(path, target):
    if path.lower().endswith(".mp4"):
        try:
            os.symlink(os.path.abspath(path), target)
            return
        except OSError:
            shutil.copy2(path, target)
            return
    copy = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", path, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
         "-movflags", "+faststart", target])
    if copy.returncode != 0:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", path, "-map", "0:v:0", "-map", "0:a:0?",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "192k", target],
            check=True)


def to_seconds(text):
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def trim(target, start, end):
    full = target.replace("source.mp4", "source-full.mp4")
    os.replace(target, full)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", full, "-t", f"{end - start:.3f}",
           "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", target]
    subprocess.run(cmd, check=True)
    os.remove(full)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("workdir")
    ap.add_argument("--range", default=None)
    a = ap.parse_args()
    src, work = a.source, a.workdir
    os.makedirs(work, exist_ok=True)
    target = os.path.join(work, "source.mp4")
    if os.path.lexists(target):
        os.remove(target)
    if is_url(src):
        download(src, target)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from common import source_info
        _, credit = source_info(src)
        if credit:
            with open(os.path.join(work, "source.json"), "w") as f:
                json.dump(credit, f, indent=2, ensure_ascii=False)
            print(f"made by {credit['name']} ({credit['url']})")
    else:
        if not os.path.isfile(src):
            print(f"file not found: {src}")
            sys.exit(1)
        bring_local(src, target)
    if a.range:
        start_text, end_text = a.range.split("-")
        trim(target, to_seconds(start_text), to_seconds(end_text))
    info = probe(target)
    info["input"] = src
    with open(os.path.join(work, "meta.json"), "w") as f:
        json.dump(info, f, indent=2)
    if not info["has_audio"]:
        print("warning: this video has no audio track, so there is nothing to transcribe")
    minutes = info["duration"] / 60
    print(f"source.mp4 ready: {minutes:.1f} min, {info['width']}x{info['height']} at {info['fps']} fps")


if __name__ == "__main__":
    main()
