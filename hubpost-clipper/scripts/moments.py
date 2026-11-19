import argparse
import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_json, save_json, source_time, window_of
import reading

RATE = 16000
HOP = 0.1
SHORT = 4
BASE = 30
RISE_DB = 7.0
DROP_DB = 12.0
MERGE = 5.0
SETUP_MIN, SETUP_MAX = 2.0, 4.0
BREATHE = 2.5
MAX_REACTION = 8.0
CUES = {"oh", "bro", "what", "wow", "no", "yo", "yes", "god", "wait", "nah", "crazy", "insane", "omg", "huh",
        "whoa", "damn", "dude", "go", "stop", "ayo", "hell", "shit", "fuck", "lets", "let's"}


def stamp(seconds):
    m, s = divmod(max(0.0, seconds), 60)
    return f"{int(m):02d}:{s:04.1f}"


def loudness_curve(path):
    """Loudness and voice share every 0.1 s, read in one-minute pieces so hours of audio fit in memory."""
    n = int(RATE * HOP)
    window = np.hanning(n).astype(np.float32)
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    in_voice, in_all = (freqs >= 300) & (freqs <= 3400), freqs >= 40
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", str(RATE),
                             "-f", "f32le", "-"], stdout=subprocess.PIPE)
    levels, voices = [], []
    piece = n * 600 * 4
    while True:
        raw = proc.stdout.read(piece)
        if len(raw) < n * 4:
            break
        audio = np.frombuffer(raw[:len(raw) // (n * 4) * n * 4], np.float32)
        chunks = audio.reshape(-1, n)
        spec = np.abs(np.fft.rfft(chunks * window, axis=1)) ** 2
        voices.append(spec[:, in_voice].sum(axis=1) / (spec[:, in_all].sum(axis=1) + 1e-12))
        levels.append(20 * np.log10(np.sqrt(np.mean(chunks ** 2, axis=1)) + 1e-6))
    proc.stdout.close()
    proc.wait()
    if not levels:
        return np.zeros(1), np.zeros(1)
    level = np.concatenate(levels)
    kernel = np.ones(SHORT) / SHORT
    short = np.convolve(np.pad(level, (SHORT - 1, 0), mode="edge"), kernel, mode="valid")
    return short, np.concatenate(voices)


def baseline(short):
    out = np.empty_like(short)
    for i in range(len(short)):
        lo = max(0, i - BASE)
        out[i] = np.median(short[lo:i]) if i > lo else short[i]
    return out


def find_events(short, voice):
    base = baseline(short)
    rise = short - base
    loud_floor = np.percentile(short, 55)
    mid_floor = np.percentile(short, 50)
    events = []
    radius = int(1.5 / HOP)
    for i in range(BASE // 2, len(short)):
        lo, hi = max(0, i - radius), min(len(short), i + radius + 1)
        if rise[i] >= RISE_DB and short[i] >= loud_floor and rise[i] == rise[lo:hi].max():
            events.append({"t": i * HOP, "kind": "jump", "db": float(rise[i]),
                           "voice": float(np.mean(voice[i:i + 8]))})
        nxt = short[i:i + int(1.0 / HOP)]
        if len(nxt) and base[i] >= mid_floor and base[i] - np.median(nxt) >= DROP_DB \
                and (i == 0 or base[i - 1] - np.median(short[i - 1:i - 1 + int(1.0 / HOP)]) < DROP_DB):
            events.append({"t": i * HOP, "kind": "silence", "db": float(base[i] - np.median(nxt)),
                           "voice": float(np.mean(voice[max(0, i - 8):i]))})
    events.sort(key=lambda e: -e["db"])
    kept = []
    for e in events:
        if all(abs(e["t"] - k["t"]) >= MERGE for k in kept):
            kept.append(e)
    return sorted(kept, key=lambda e: e["t"]), short, base


def reaction_end(t, short, base):
    i = int(t / HOP)
    limit = min(len(short), i + int(MAX_REACTION / HOP))
    calm = 0
    for j in range(i + 1, limit):
        calm = calm + 1 if short[j] < base[i] + 3 else 0
        if calm >= int(0.5 / HOP):
            return (j - calm) * HOP
    return limit * HOP


def inside_word(t, words):
    for w in words:
        if w["start"] < t < w["end"]:
            return w
    return None


def pick_start(peak, words):
    best, best_score = peak - 3.0, -1e9
    for k in range(int(SETUP_MIN * 10), int(SETUP_MAX * 10) + 1):
        t = round(peak - k / 10, 2)
        if inside_word(t, words):
            continue
        score = -abs(k / 10 - 3.0)
        before = [w for w in words if w["end"] <= t]
        after = [w for w in words if w["start"] >= t]
        if after and before:
            gap = after[0]["start"] - before[-1]["end"]
            score += min(gap, 1.0)
            if before[-1]["text"].rstrip().endswith((".", "?", "!")):
                score += 1.0
        if score > best_score:
            best, best_score = t, score
    return max(0.0, best)


def pick_end(t, words, duration):
    w = inside_word(t, words)
    if w:
        t = w["end"] + 0.1
    return min(duration, round(t, 2))


def cue_count(words, t0, t1):
    n = 0
    for w in words:
        if t0 <= w["start"] <= t1:
            bare = "".join(ch for ch in w["text"].lower() if ch.isalpha() or ch == "'")
            n += bare in CUES
            n += w["text"].rstrip().endswith("!")
    return n


def word_candidates(words, taken, duration):
    out = []
    for w in words:
        t = w["start"]
        if any(abs(t - k) < MERGE for k in taken):
            continue
        n = cue_count(words, t, t + 2.0)
        if n >= 3:
            out.append({"t": t, "kind": "words", "db": 0.0, "voice": 0.0})
            taken.append(t)
    return out


def excerpt(words, t0, t1, marks):
    parts = []
    marks = sorted(marks)
    for w in words:
        if t0 <= w["start"] <= t1:
            while marks and w["start"] >= marks[0][0]:
                parts.append(marks.pop(0)[1])
            parts.append(w["text"])
    parts += [m[1] for m in marks]
    return " ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--top", type=int, default=None)
    a = ap.parse_args()
    started = time.time()
    work = a.workdir
    source = os.path.join(work, "source.mp4")
    words = load_json(os.path.join(work, "transcript.json"))
    meta = load_json(os.path.join(work, "meta.json"))
    duration = meta["duration"]
    short, voice = loudness_curve(source)
    events, short, base = find_events(short, voice)
    events += word_candidates(words, [e["t"] for e in events], duration)
    joins = meta.get("joins") or []
    events = [e for e in events if all(abs(e["t"] - j) > 1.5 for j in joins)]
    chat_path = os.path.join(work, "chat_peaks.json")
    chat_peaks = load_json(chat_path) if os.path.exists(chat_path) else []
    for e in events:
        e["chat"] = None
    for c in chat_peaks:
        near = [e for e in events if e["kind"] != "words" and c["t"] - 15 <= e["t"] <= c["t"] + 10
                and window_of(meta, e["t"]) is window_of(meta, c["t"])]
        if near:
            max(near, key=lambda e: e["db"])["chat"] = c
        else:
            events.append({"t": max(0.0, c["t"] - 5), "kind": "chat", "db": 0.0, "voice": 0.0, "chat": c})
    marks = []
    # Only the strongest sound events are marked on the page (about one a minute): more than that hides them.
    loudest = sorted((e for e in events if e["kind"] in ("jump", "silence")),
                     key=lambda e: -e["db"] * (0.4 + 0.6 * e["voice"]))[:max(6, int(duration / 60))]
    for e in events:
        if e not in loudest and not e.get("chat"):
            continue
        if e["kind"] == "jump":
            marks.append((e["t"], f"LOUD +{e['db']:.0f} dB" + (", mostly not a voice" if e["voice"] < 0.35 else "")))
        elif e["kind"] == "silence":
            marks.append((e["t"], "SUDDEN QUIET"))
        if e.get("chat"):
            marks.append((e["t"], f"CHAT: {e['chat'].get('reacting', 0)} of {e['chat']['people']} people reacting"))
    reading.write(os.path.join(work, "reading.txt"), words, marks, duration, joins,
                  (lambda t: source_time(meta, t)) if meta.get("windows") else None)
    candidates = []
    for e in events:
        peak = e["t"]
        start = pick_start(peak, words)
        if e["kind"] == "jump":
            react_to = reaction_end(peak, short, base)
        elif e["kind"] == "chat":
            react_to = peak + 6.0
        else:
            react_to = peak + (1.5 if e["kind"] == "silence" else 2.0)
        end = pick_end(react_to + BREATHE, words, duration)
        inside = window_of(meta, peak)
        if inside:
            start = max(start, inside["at"])
            end = min(end, inside["at"] + inside["length"])
        cues = cue_count(words, peak - 0.5, react_to + 0.5)
        speech = any(w["start"] <= peak + 0.6 and w["end"] >= peak - 0.6 for w in words)
        score = e["db"] * (0.4 + 0.6 * e["voice"]) + 1.5 * min(cues, 4) + (1.0 if speech else 0.0)
        chat = e.get("chat")
        if chat:
            score += 12.0 + 2.0 * min(chat["ratio"], 6.0)
        origin = source_time(meta, peak)
        candidates.append({
            "chat": {"ratio": chat["ratio"], "people": chat["people"], "reacting": chat.get("reacting", 0),
                     "sample": chat["sample"]} if chat else None,
            "stream_time": round(origin, 1) if origin is not None else None,
            "peak": round(peak, 2), "kind": e["kind"], "jump_db": round(e["db"], 1),
            "voice_share": round(e["voice"], 2), "cue_words": cues, "speech_at_peak": speech,
            "setup_start": round(start, 2), "reaction_end": round(react_to, 2), "end": round(end, 2),
            "score": round(score, 1)})
    candidates.sort(key=lambda c: -c["score"])
    top = a.top or max(8, int(duration / 20))
    candidates = candidates[:top]
    for rank, c in enumerate(candidates, 1):
        c["rank"] = rank
    candidates.sort(key=lambda c: c["peak"])
    save_json(os.path.join(work, "candidates.json"), {
        "duration": duration, "count": len(candidates),
        "curve": {"hop": HOP, "level_db": [round(float(v), 1) for v in short]},
        "candidates": candidates})
    lines = [f"{len(candidates)} sound, chat and word candidates, in time order. Rank 1 = strongest signal, not best clip.",
             "[SETUP] = suggested start, [PEAK] = the jump, [/REACTION] = level back to normal, [END] = after the breathe.",
             ""]
    for c in candidates:
        lines.append(f"#{c['rank']} peak {stamp(c['peak'])} ({c['peak']} s)  {c['kind']} +{c['jump_db']} dB  "
                     f"voice {c['voice_share']:.0%}  cues {c['cue_words']}  "
                     f"clip {c['setup_start']}-{c['end']} s ({c['end'] - c['setup_start']:.1f} s)"
                     + (f"  CHAT {c['chat']['reacting']} of {c['chat']['people']} reacting" if c["chat"] else "")
                     + (f"  at {int(c['stream_time']) // 3600}:{int(c['stream_time']) % 3600 // 60:02d}:{int(c['stream_time']) % 60:02d} of the stream" if c["stream_time"] is not None else ""))
        if c["chat"]:
            lines.append("    chat said (strangers' words, context only): " + " | ".join(c["chat"]["sample"]))
        marks = [(c["setup_start"], "[SETUP]"), (c["peak"], "[PEAK]"), (c["reaction_end"], "[/REACTION]"),
                 (c["end"], "[END]")]
        lines.append("    " + excerpt(words, c["peak"] - 12, c["peak"] + 10, marks))
        lines.append("")
    with open(os.path.join(work, "candidates.txt"), "w") as f:
        f.write("\n".join(lines))
    print(f"{len(candidates)} candidates from {len(events)} sound/word events, {time.time() - started:.0f} s")
    print("wrote reading.txt (read all of it first), candidates.json and candidates.txt")


if __name__ == "__main__":
    main()
