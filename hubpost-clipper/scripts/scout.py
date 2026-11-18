"""Finds where the moments are in a long stream before any video is downloaded.

Pulls only the audio and the chat replay, measures both across the whole stream, adds the
"most replayed" curve when the site has one, and writes the stretches worth downloading.
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chat as chatlib
from common import load_json, save_json, source_info, ytdlp_command
from moments import find_events, loudness_curve
from windows import clock

PRE, POST = 50, 35
MAX_WINDOW = 200
SOUND_PER_HOUR = 30
SOUND_SPACING = 25


def rank_scores(items, key):
    """0..1 by rank inside one kind of signal, so a loud stream does not drown its chat."""
    order = sorted(items, key=key)
    for i, item in enumerate(order):
        item["strength"] = round((i + 1) / len(order), 3)


def strongest(seeds, count, spacing):
    """A long stream is loud all the time: keep the strongest events, spaced out, not every one."""
    kept = []
    for s in sorted(seeds, key=lambda s: -s["raw"]):
        if all(abs(s["t"] - k["t"]) >= spacing for k in kept):
            kept.append(s)
        if len(kept) >= count:
            break
    return sorted(kept, key=lambda s: s["t"])


def heat_peaks(heatmap):
    if not heatmap:
        return []
    values = np.array([h["value"] for h in heatmap])
    cut = np.percentile(values, 85)
    out = []
    for i, h in enumerate(heatmap):
        if i == 0:
            continue
        left = values[i - 1]
        right = values[i + 1] if i + 1 < len(values) else 0
        if h["value"] >= cut and h["value"] >= left and h["value"] >= right:
            out.append({"t": round((h["start_time"] + h["end_time"]) / 2, 1), "kind": "replayed",
                        "value": round(float(h["value"]), 3)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("workdir")
    ap.add_argument("--top", type=int, default=None, help="how many stretches to keep")
    ap.add_argument("--range", default=None, help="only look at part of the stream, like 1:00:00-3:00:00")
    ap.add_argument("--no-chat", action="store_true")
    a = ap.parse_args()
    started = time.time()
    work = a.workdir
    os.makedirs(work, exist_ok=True)

    info, credit = source_info(a.url)
    if info is None:
        print("could not read this link (private, deleted, sub-only, or the site blocked the request)")
        sys.exit(1)
    if info.get("is_live"):
        print("this stream is live right now: use watch.py to clip it as it happens, or wait for the VOD")
        sys.exit(2)
    duration = float(info.get("duration") or 0)
    site = (info.get("extractor_key") or "").lower()
    lo, hi = 0.0, duration
    if a.range:
        parts = [sum(float(x) * 60 ** i for i, x in enumerate(reversed(p.split(":")))) for p in a.range.split("-")]
        lo, hi = parts[0], min(duration, parts[1])
    print(f"{info.get('title', '')} | {credit['name']} | {clock(duration)}")

    for old in glob.glob(os.path.join(work, "scout-audio.*")):
        os.remove(old)
    print("downloading the audio only (the video is fetched later, just for the stretches you keep)")
    cmd = ytdlp_command() + ["-f", "ba/Audio_Only/audio_only/worst", "--no-playlist", "--no-progress",
                             "-o", os.path.join(work, "scout-audio.%(ext)s")]
    if a.range:
        cmd += ["--download-sections", f"*{clock(lo)}-{clock(hi)}"]
    subprocess.run(cmd + [a.url], check=True)
    audio = glob.glob(os.path.join(work, "scout-audio.*"))[0]
    short, voice = loudness_curve(audio)
    events, _, _ = find_events(short, voice)
    sound = [{"t": round(e["t"] + lo, 1), "kind": "sound-" + e["kind"], "db": round(e["db"], 1),
              "voice": round(e["voice"], 2), "raw": min(e["db"], 40.0) * (0.4 + 0.6 * e["voice"])} for e in events]
    sound = strongest(sound, int(max(12, (hi - lo) / 3600 * SOUND_PER_HOUR)), SOUND_SPACING)
    os.remove(audio)

    chat_peaks, chat_note = [], "no chat replay for this site"
    saved_chat = os.path.join(work, "scout-chat.json")
    if not a.no_chat:
        try:
            rows = []
            if os.path.exists(saved_chat):
                rows = [tuple(r) for r in load_json(saved_chat) if lo <= r[0] < hi]
            elif "twitch" in site:
                rows = chatlib.twitch_replay(re.sub(r"^v", "", str(info["id"])), duration, lo, hi)
            elif "youtube" in site:
                rows = [r for r in chatlib.youtube_replay(a.url, os.path.join(work, "scout-chat")) if lo <= r[0] < hi]
            if rows:
                save_json(saved_chat, rows)
                wins = chatlib.score_rows(rows)
                chat_peaks = chatlib.find_peaks(wins, min_people=chatlib.auto_min_people(wins))
                chat_note = f"{len(rows)} chat messages, {len(chat_peaks)} spikes"
            elif "twitch" in site or "youtube" in site:
                chat_note = "this video has no chat replay"
        except RuntimeError as err:
            chat_note = f"chat replay failed ({err}); going on with sound only"
    print(chat_note)

    replayed = [h for h in heat_peaks(info.get("heatmap")) if lo <= h["t"] < hi]
    seeds = []
    if sound:
        rank_scores(sound, lambda s: s["raw"])
        seeds += [dict(s, span=(s["t"] - PRE, s["t"] + POST)) for s in sound]
    if chat_peaks:
        rank_scores(chat_peaks, lambda c: max(c["ratio"] / chatlib.RATIO, c["z"] / chatlib.BUSY_Z))
        seeds += [{"t": c["t"], "kind": "chat", "ratio": c["ratio"], "people": c["people"],
                   "reacting": c["reacting"], "sample": c["sample"],
                   "strength": c["strength"], "span": (c["first"] - PRE - 10, c["last"] + POST - 10)}
                  for c in chat_peaks]
    if replayed:
        rank_scores(replayed, lambda h: h["value"])
        seeds += [dict(h, span=(h["t"] - PRE, h["t"] + POST)) for h in replayed]

    seeds.sort(key=lambda s: s["span"][0])
    windows = []
    for s in seeds:
        start, end = max(lo, s["span"][0]), min(hi, s["span"][1])
        last = windows[-1] if windows else None
        if last and start <= last["end"] and max(end, last["end"]) - last["start"] <= MAX_WINDOW:
            last["end"] = max(last["end"], end)
            last["seeds"].append(s)
        else:
            if last and start < last["end"]:
                start = last["end"]
            if end - start >= 30:
                windows.append({"start": start, "end": end, "seeds": [s]})
    for w in windows:
        best = {}
        for s in w["seeds"]:
            family = s["kind"].split("-")[0]
            best[family] = max(best.get(family, 0), s["strength"])
        w["score"] = round(sum(best.values()) + 0.5 * (len(best) - 1), 2)
        w["signals"] = sorted(best)
    keep = a.top or int(min(24, max(8, (hi - lo) / 3600 * 6)))
    chosen = sorted(sorted(windows, key=lambda w: -w["score"])[:keep], key=lambda w: w["start"])

    def tidy(w):
        return {"start": int(w["start"]), "end": int(w["end"] + 1), "score": w["score"], "signals": w["signals"],
                "seeds": [{k: v for k, v in s.items() if k not in ("span", "raw")} for s in w["seeds"]]}

    save_json(os.path.join(work, "scout.json"), {
        "url": a.url, "title": info.get("title"), "duration": duration, "credit": credit, "chat_note": chat_note,
        "chat_peaks": chat_peaks, "found": len(windows), "windows": [tidy(w) for w in windows]})
    save_json(os.path.join(work, "windows.json"), {"windows": [
        {"start": int(w["start"]), "end": int(w["end"] + 1)} for w in chosen]})
    save_json(os.path.join(work, "source.json"), credit)

    lines = [f"{info.get('title', '')} | {credit['name']} | {clock(duration)} | {chat_note}",
             f"{len(windows)} stretches with a signal; the {len(chosen)} strongest are in windows.json "
             f"({sum(w['end'] - w['start'] for w in chosen) / 60:.0f} min of video to download).",
             "Score: each kind of signal (sound, chat, replayed) counts 0-1 by its rank, plus 0.5 when two agree.", ""]
    for w in sorted(windows, key=lambda w: -w["score"])[:max(keep * 2, 20)]:
        mark = "KEEP " if w in chosen else "     "
        lines.append(f"{mark}{clock(w['start'])}-{clock(w['end'])}  ({w['end'] - w['start']:.0f} s)  "
                     f"score {w['score']}  {'+'.join(w['signals'])}")
        for s in w["seeds"]:
            if s["kind"] == "chat":
                lines.append(f"       chat: {s['reacting']} of {s['people']} people reacting (x{s['ratio']}) at {clock(s['t'])}; "
                             f"strangers wrote: " + " | ".join(s["sample"]))
            elif s["kind"] == "replayed":
                lines.append(f"       most replayed at {clock(s['t'])}")
            else:
                lines.append(f"       {s['kind']} +{s['db']} dB, voice {s['voice']:.0%}, at {clock(s['t'])}")
    with open(os.path.join(work, "scout.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{len(windows)} stretches found, {len(chosen)} kept, {time.time() - started:.0f} s")
    print("wrote scout.txt (read it), scout.json and windows.json (edit it to change what gets downloaded)")


if __name__ == "__main__":
    main()
