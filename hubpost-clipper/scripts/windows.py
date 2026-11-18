"""Turns the chosen stretches of a long stream into one short source.mp4 the rest of the pipeline works on.

VOD:  windows.py <work>            downloads only the stretches listed in <work>/windows.json
Live: windows.py <work> --live     gathers the moments watch.py saved into <work>/batch-NN/
"""
import argparse
import glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_json, probe_duration, save_json, ytdlp_command

FORMAT = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]/b"


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def download_window(url, start, end, target):
    cmd = ytdlp_command() + ["-f", FORMAT, "--merge-output-format", "mp4", "--no-playlist", "--no-progress",
                             "--download-sections", f"*{clock(start)}-{clock(end)}", "-o", target, url]
    return subprocess.run(cmd).returncode == 0 and os.path.exists(target)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    import json
    info = {"has_audio": False}
    for s in json.loads(out)["streams"]:
        if s["codec_type"] == "video" and "width" not in info:
            num, den = s["r_frame_rate"].split("/")
            info.update(width=s["width"], height=s["height"], fps=round(float(num) / float(den), 3))
        if s["codec_type"] == "audio":
            info["has_audio"] = True
    return info


def join(parts, target, folder):
    """Stream copy first; if the pieces do not line up, re-encode once so every later step sees clean time."""
    clean = []
    for i, p in enumerate(parts):
        out = os.path.join(folder, f"part-{i:02d}.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-fflags", "+genpts", "-i", p, "-map", "0:v:0",
                        "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags",
                        "+faststart", out], check=True)
        clean.append(out)
    lengths = [probe_duration(p) for p in clean]
    listing = os.path.join(folder, "parts.txt")
    with open(listing, "w") as f:
        for p in clean:
            f.write("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", listing,
                    "-c", "copy", "-movflags", "+faststart", target])
    good = os.path.exists(target) and abs(probe_duration(target) - sum(lengths)) < 1.0 + 0.2 * len(parts)
    if not good:
        print("pieces did not line up with a plain copy, re-encoding the joined source once")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", listing,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart", target], check=True)
        total = probe_duration(target)
        scale = total / sum(lengths) if sum(lengths) else 1.0
        lengths = [x * scale for x in lengths]
    for p in clean:
        os.remove(p)
    os.remove(listing)
    return lengths


def write_work(work, pieces, credit, origin):
    """pieces: [{file, src_start, chat: [peak dicts with t relative to the piece], label}]"""
    lengths = join([p["file"] for p in pieces], os.path.join(work, "source.mp4"), work)
    windows, joins, chat, at = [], [], [], 0.0
    for piece, length in zip(pieces, lengths):
        windows.append({"at": round(at, 2), "length": round(length, 2), "src_start": piece["src_start"],
                        "label": piece.get("label", "")})
        for c in piece.get("chat") or []:
            if 0 <= c["t"] < length:
                chat.append(dict(c, t=round(at + c["t"], 1)))
        at += length
        joins.append(round(at, 2))
    info = probe(os.path.join(work, "source.mp4"))
    info.update(duration=round(at, 2), input=origin, windows=windows, joins=joins[:-1])
    save_json(os.path.join(work, "meta.json"), info)
    save_json(os.path.join(work, "chat_peaks.json"), chat)
    if credit:
        save_json(os.path.join(work, "source.json"), credit)
    print(f"source.mp4 ready: {len(pieces)} stretches, {at / 60:.1f} min, {info['width']}x{info['height']} "
          f"at {info['fps']} fps, {len(chat)} chat peaks carried over")


def free_gb(path):
    import shutil
    return shutil.disk_usage(path).free / 1e9


def from_vod(work):
    plan = load_json(os.path.join(work, "windows.json"))
    scout = load_json(os.path.join(work, "scout.json"))
    need = sum(w["end"] - w["start"] for w in plan["windows"]) * 0.0025 + 1.0
    if free_gb(work) < need:
        print(f"not enough free disk: about {need:.1f} GB needed for these stretches, {free_gb(work):.1f} GB free. "
              "Free some space or keep fewer stretches in windows.json.")
        sys.exit(1)
    url = scout["url"]
    folder = os.path.join(work, "windows")
    os.makedirs(folder, exist_ok=True)
    pieces = []
    for n, w in enumerate(plan["windows"], 1):
        target = os.path.join(folder, f"w{n:02d}.mp4")
        if not os.path.exists(target):
            print(f"downloading stretch {n}/{len(plan['windows'])}: {clock(w['start'])}-{clock(w['end'])}")
            if not download_window(url, w["start"], w["end"], target):
                print(f"  could not download this stretch, skipping it")
                continue
        chat = [dict(c, t=c["t"] - w["start"]) for c in scout.get("chat_peaks") or []
                if w["start"] <= c["t"] < w["end"]]
        pieces.append({"file": target, "src_start": w["start"], "chat": chat, "label": clock(w["start"])})
    if not pieces:
        print("nothing was downloaded")
        sys.exit(1)
    write_work(work, pieces, scout.get("credit"), url)
    for p in pieces:
        os.remove(p["file"])


def from_live(work):
    live = os.path.join(work, "live")
    status = load_json(os.path.join(live, "status.json"))
    used_path = os.path.join(live, "used.json")
    used = load_json(used_path) if os.path.exists(used_path) else []
    fresh = [p for p in sorted(glob.glob(os.path.join(live, "moments", "m-*.json")))
             if os.path.basename(p) not in used and os.path.exists(p[:-5] + ".mp4")]
    if not fresh:
        print("no new moments saved yet")
        sys.exit(1)
    batch = os.path.join(work, f"batch-{len(glob.glob(os.path.join(work, 'batch-*'))) + 1:02d}")
    os.makedirs(batch)
    pieces = []
    for p in fresh:
        m = load_json(p)
        pieces.append({"file": p[:-5] + ".mp4", "src_start": m["stream_seconds"], "label": m["clock"],
                       "chat": m.get("chat") or []})
    write_work(batch, pieces, status.get("credit"), status.get("url"))
    for p in pieces:
        os.remove(p["file"])
    save_json(used_path, used + [os.path.basename(p) for p in fresh])
    print(f"work folder for these {len(pieces)} moments: {batch}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    from_live(a.workdir) if a.live else from_vod(a.workdir)


if __name__ == "__main__":
    main()
