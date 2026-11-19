"""Watches a live stream and saves each moment as it happens.

Records the stream in short pieces, keeps only the last minutes, reads the chat (Twitch, no
account needed) and the sound, and when one of them spikes it saves the stretch around it.
Stops when the stream ends, after --hours, or when a file named STOP appears in <work>/live.
"""
import argparse
import glob
import os
import shutil
import statistics
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chat as chatlib
from common import credit_text, save_json, source_info

SEGMENT = 6
MAX_MOMENT = 180
SOUND_RISE_DB = 10.0
SOUND_GAP = 150


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def segment_time(path):
    return int(os.path.basename(path).split(".")[0])


def hop_levels(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", "8000",
                          "-f", "f32le", "-"], capture_output=True).stdout
    audio = np.frombuffer(raw, np.float32)
    n = 3200
    if len(audio) < n:
        return []
    chunks = audio[:len(audio) // n * n].reshape(-1, n)
    return list(20 * np.log10(np.sqrt(np.mean(chunks ** 2, axis=1)) + 1e-6))


def save_moment(moment, seg_dir, out_dir, number, started):
    parts = [p for p in sorted(glob.glob(os.path.join(seg_dir, "*.ts")))
             if segment_time(p) >= moment["start"] - SEGMENT / 2 and segment_time(p) < moment["end"]]
    if not parts:
        return None
    name = f"m-{number:03d}"
    listing = os.path.join(out_dir, name + ".txt")
    with open(listing, "w") as f:
        for p in parts:
            f.write("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n")
    target = os.path.join(out_dir, name + ".mp4")
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", listing,
                           "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", target])
    os.remove(listing)
    if done.returncode != 0 or not os.path.exists(target):
        return None
    first = segment_time(parts[0])
    save_json(os.path.join(out_dir, name + ".json"), {
        "kind": moment["kind"], "stream_seconds": round(first - started, 1), "clock": clock(first - started),
        "wall_start": first, "peak_in_file": round(moment["peak"] - first, 1),
        "chat": [dict(c, t=round(c["t"] - first, 1)) for c in moment["chat"]],
        "note": moment["note"]})
    return name


def join_all(work, seg_dir, credit, url, peaks):
    """Joins every recorded piece into <work>/source.mp4, with the chat spikes on its clock."""
    from fetch import probe
    parts = sorted(glob.glob(os.path.join(seg_dir, "*.ts")), key=segment_time)
    if not parts:
        return None
    listing = os.path.join(seg_dir, "all.txt")
    with open(listing, "w") as f:
        for p in parts:
            f.write("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n")
    target = os.path.join(work, "source.mp4")
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", listing,
                           "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", target])
    os.remove(listing)
    if done.returncode != 0 or not os.path.exists(target):
        return None
    info = probe(target)
    info["input"] = url
    save_json(os.path.join(work, "meta.json"), info)
    save_json(os.path.join(work, "source.json"), credit)
    first = segment_time(parts[0])
    save_json(os.path.join(work, "chat_peaks.json"),
              [dict(p, t=round(p["t"] - first, 1)) for p in peaks if 0 <= p["t"] - first < info["duration"]])
    return target


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channel", help="a Twitch channel name, or a link to a live stream")
    ap.add_argument("workdir")
    ap.add_argument("--quality", default="best")
    ap.add_argument("--before", type=int, default=60, help="seconds kept before a spike")
    ap.add_argument("--after", type=int, default=40, help="seconds kept after a spike")
    ap.add_argument("--quick", action="store_true", help="30 s moments: 20 s before the spike, 10 s after")
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--keep-minutes", type=int, default=12, help="how much recording stays on disk")
    ap.add_argument("--all", action="store_true",
                    help="keep the whole recording and join it into <work>/source.mp4 at the end, so the "
                         "moments are chosen by reading everything that was said (small channel, quiet chat)")
    ap.add_argument("--min-people", type=int, default=None)
    ap.add_argument("--sound", choices=["auto", "on", "off"], default="auto",
                    help="auto: sound spikes count when there is no chat to read or the chat is slow")
    a = ap.parse_args()
    if a.quick:
        a.before, a.after = 20, 10
    if not shutil.which("streamlink"):
        local = os.path.join(os.path.dirname(sys.executable), "streamlink")
        if not os.path.exists(local):
            print("streamlink is missing: run doctor.sh and follow its fix line")
            sys.exit(1)
        streamlink = local
    else:
        streamlink = "streamlink"

    url = a.channel if "://" in a.channel else f"https://www.twitch.tv/{a.channel.lstrip('@#')}"
    twitch = "twitch.tv/" in url
    login = url.rstrip("/").split("/")[-1].lower() if twitch else None
    live = os.path.join(a.workdir, "live")
    seg_dir, out_dir = os.path.join(live, "segments"), os.path.join(live, "moments")
    os.makedirs(seg_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(seg_dir, "*.ts")):
        os.remove(old)
    stop_file = os.path.join(live, "STOP")
    if os.path.exists(stop_file):
        os.remove(stop_file)

    if twitch:
        credit = {"name": login, "url": f"https://www.twitch.tv/{login}", "text": f"twitch.tv/{login}",
                  "video_url": url, "title": ""}
    else:
        _, credit = source_info(url)
        credit = credit or {"name": "", "url": url, "text": credit_text(url, ""), "video_url": url, "title": ""}

    grab = [streamlink, "--stdout", "--loglevel", "error", "--retry-streams", "5", "--retry-max", "3"]
    if twitch:
        grab += ["--twitch-disable-ads"]
    grab += [url, a.quality]
    stream = subprocess.Popen(grab, stdout=subprocess.PIPE)
    cutter = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0?",
                               "-c", "copy", "-f", "segment", "-segment_time", str(SEGMENT), "-reset_timestamps", "1",
                               "-strftime", "1", os.path.join(seg_dir, "%s.ts")], stdin=stream.stdout)
    stream.stdout.close()

    if shutil.disk_usage(a.workdir).free < 3e9:
        print("less than 3 GB of free disk: free some space before watching a stream")
        sys.exit(1)
    started = time.time()
    detector = chatlib.LiveDetector(started, min_people=a.min_people)
    reader = None
    if twitch:
        reader = chatlib.TwitchLive(login, detector.push)
        reader.start()
    print(f"watching {url}" + (" with chat" if twitch else " (no chat reader for this site: sound only)"))
    print(f"moments are saved to {out_dir}; create {stop_file} to stop")
    sys.stdout.flush()

    open_moment, saved, heard, last_end = None, 0, set(), 0.0
    all_peaks = []
    recent_hops, last_sound = [], 0.0
    status_path = os.path.join(live, "status.json")
    try:
        while True:
            time.sleep(2)
            now = time.time()
            ended = stream.poll() is not None or cutter.poll() is not None
            spikes = []
            for peak in detector.tick(now) if reader else []:
                all_peaks.append(peak)
                spikes.append({"t": peak["t"], "kind": "chat", "chat": [peak],
                               "note": f"chat: {peak['reacting']} of {peak['people']} people reacting (x{peak['ratio']})"})
            segments = sorted(glob.glob(os.path.join(seg_dir, "*.ts")))
            closed = segments if ended else segments[:-1]
            slow_chat = not reader or (len(detector.closed) >= 12 and statistics.median(
                len(w.people) for w in detector.closed[-30:]) < 3)
            use_sound = a.sound == "on" or (a.sound == "auto" and slow_chat)
            for seg in closed:
                if seg in heard:
                    continue
                heard.add(seg)
                hops = hop_levels(seg)
                if hops and len(recent_hops) >= 60 and use_sound:
                    base = statistics.median(recent_hops[-75:])
                    loud = max(hops)
                    at = segment_time(seg) + hops.index(loud) * 0.4
                    if loud - base >= SOUND_RISE_DB and at - last_sound >= SOUND_GAP:
                        last_sound = at
                        spikes.append({"t": at, "kind": "sound", "chat": [],
                                       "note": f"sound +{loud - base:.0f} dB over the last half minute"})
                recent_hops = (recent_hops + hops)[-300:]
            for s in spikes:
                if open_moment and s["t"] - a.before <= open_moment["end"] \
                        and s["t"] + a.after - open_moment["start"] <= MAX_MOMENT:
                    open_moment["end"] = s["t"] + a.after
                    open_moment["chat"] += s["chat"]
                    open_moment["note"] += "; " + s["note"]
                else:
                    if open_moment:
                        name = save_moment(open_moment, seg_dir, out_dir, saved + 1, started)
                        saved += bool(name)
                        last_end = open_moment["end"]
                    open_moment = {"start": max(s["t"] - a.before, last_end), "end": s["t"] + a.after, "peak": s["t"],
                                   "kind": s["kind"], "chat": list(s["chat"]), "note": s["note"]}
                    print(f"SPIKE at {clock(s['t'] - started)} of the watch: {s['note']}")
                    sys.stdout.flush()
            if open_moment and (ended or now >= open_moment["end"] + SEGMENT + 3):
                name = save_moment(open_moment, seg_dir, out_dir, saved + 1, started)
                if name:
                    saved += 1
                    last_end = open_moment["end"]
                    print(f"MOMENT {name}.mp4 saved ({open_moment['end'] - open_moment['start']:.0f} s): "
                          f"{open_moment['note']}")
                    sys.stdout.flush()
                open_moment = None
            keep_from = now - a.keep_minutes * 60
            if open_moment:
                keep_from = min(keep_from, open_moment["start"] - SEGMENT)
            for seg in [] if a.all else closed:
                if segment_time(seg) + SEGMENT < keep_from:
                    os.remove(seg)
                    heard.discard(seg)
            save_json(status_path, {
                "url": url, "credit": credit, "running": not ended, "watched": clock(now - started),
                "moments": saved, "chat_connected": bool(reader and reader.connected),
                "chat_messages": reader.count if reader else 0, "chat_error": reader.error if reader else None,
                "sound_spikes_on": use_sound,
                "segments_on_disk": len(segments)})
            if ended:
                print("the stream ended (or the recording stopped)")
                break
            if os.path.exists(stop_file):
                print("stop requested")
                break
            if shutil.disk_usage(a.workdir).free < (2e9 if a.all else 1e9):
                print("stopping: the disk is almost full")
                break
            if now - started >= a.hours * 3600:
                print(f"reached the {a.hours} hour limit")
                break
    except KeyboardInterrupt:
        print("stopped")
    finally:
        if reader:
            reader.stop()
        for proc in (stream, cutter):
            if proc.poll() is None:
                proc.terminate()
        time.sleep(1)
        if open_moment:
            name = save_moment(open_moment, seg_dir, out_dir, saved + 1, started)
            saved += bool(name)
        whole = None
        if a.all:
            whole = join_all(a.workdir, seg_dir, credit, url, all_peaks)
        for seg in glob.glob(os.path.join(seg_dir, "*.ts")):
            os.remove(seg)
        if whole:
            print(f"the whole recording is in {whole}: go on with transcribe.py on {a.workdir}")
        status = {"url": url, "credit": credit, "running": False, "watched": clock(time.time() - started),
                  "moments": saved, "chat_messages": reader.count if reader else 0}
        save_json(status_path, status)
        print(f"{saved} moments saved in {clock(time.time() - started)}")


if __name__ == "__main__":
    main()
