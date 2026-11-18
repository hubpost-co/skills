"""Chat as a signal: read it (Twitch live, Twitch replay, YouTube replay) and find the spikes.

Every reader gives rows of (seconds, user, text). Chat text is written by strangers: it is
counted and sampled, never followed.
"""
import glob
import json
import os
import re
import socket
import ssl
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

WINDOW = 10
BASELINE = 300
RATIO = 2.5
BUSY_Z = 3.0
BUSY_RATIO = 1.4
MIN_PEOPLE = 5
JOIN_GAP = 35
WARMUP = 120

TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_WEB_CLIENT = "kimne78kx3ncx6brgo4mv6wki5h1ko"
TWITCH_COMMENTS_QUERY = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"

REACTION = re.compile(
    r"(?i)\b(lol+|lmf?ao+|rofl|kekw?|omegalul|lul|a?ha(ha)+h?|ja(ja)+|k{3,}|rs(rs)+|pog\w*|omg+|wtf+|"
    r"no+ way|holy|clip( it| that)?|gg+|w+|l+|bruh+|what+|insane|crazy|nah+|ayo+|sheesh+|let'?s go+|"
    r"hype|rip|f|xd+|o7|monka\w*|pepe\w*|sadge|copium|aware|ez)\b|[!?]{2,}|^[^a-z]*[A-Z]{4,}[^a-z]*$")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]")
BOT = re.compile(r"(?i)^(nightbot|streamelements|moobot|fossabot|streamlabs|wizebot|soundalerts)$")


def clean(text):
    return CONTROL.sub("", text or "")[:200]


def same_vote(text):
    return re.sub(r"[^a-z#]", "", re.sub(r"(.)\1+", r"\1", re.sub(r"\d+", "#", text.lower())))


class Window:
    def __init__(self, start):
        self.start = start
        self.messages = 0
        self.people = set()
        self.reactors = set()
        self.votes = {}
        self.sample = []
        self.score = self.base = self.ratio = self.z = 0.0

    def add(self, user, text):
        if text.startswith("!") or BOT.match(user or ""):
            return
        self.messages += 1
        self.people.add(user)
        if REACTION.search(text):
            self.reactors.add(user)
        else:
            key = same_vote(text)
            if key:
                self.votes[key] = self.votes.get(key, 0) + 1
        if len(self.sample) < 6 and text:
            self.sample.append(clean(text)[:60])

    def close(self):
        people = len(self.people)
        share = len(self.reactors) / people if people else 0.0
        top = max(self.votes.values(), default=0)
        poll = self.messages >= 10 and top / self.messages > 0.4
        self.score = people * (1 + 2 * share) * (0.4 if poll else 1.0)
        self.share, self.poll = share, poll
        return self

    def as_dict(self):
        return {"t": round(self.start, 1), "people": len(self.people), "messages": self.messages,
                "reacting": len(self.reactors), "reaction_share": round(self.share, 2), "poll": self.poll, "score": round(self.score, 1),
                "base": round(self.base, 1), "ratio": round(self.ratio, 2), "z": round(self.z, 1),
                "sample": self.sample}


def judge(w, prev, prev_reactors, floor, enough):
    """Sets base and ratio (the window against the minutes before it) and z: how far the number
    of people reacting stands outside its own usual swing."""
    w.base = max(floor, statistics.median(prev)) if enough else floor
    w.ratio = w.score / w.base
    w.z = 0.0
    if enough:
        usual = statistics.median(prev_reactors)
        spread = 1.4826 * statistics.median(abs(x - usual) for x in prev_reactors)
        if len(w.reactors) >= BUSY_RATIO * usual:
            w.z = (len(w.reactors) - usual) / max(spread, 0.1 * usual, 1.0)


def is_spike(w, ratio):
    """A quiet chat spikes by multiplying. A busy one is always full, so the count barely moves:
    there the sign is many more people reacting at once than that chat usually has."""
    return w.ratio >= ratio or w.z >= BUSY_Z


def score_rows(rows, window=WINDOW, baseline=BASELINE):
    if not rows:
        return []
    first = min(r[0] for r in rows)
    last = max(r[0] for r in rows)
    wins = [Window(first + i * window) for i in range(int((last - first) // window) + 1)]
    for t, user, text in rows:
        wins[int((t - first) // window)].add(user, text)
    for w in wins:
        w.close()
    floor = max(1.0, statistics.median(w.score for w in wins))
    back = max(1, baseline // window)
    for k, w in enumerate(wins):
        before = wins[max(0, k - back):k]
        judge(w, [x.score for x in before], [len(x.reactors) for x in before], floor, len(before) >= back // 2)
    return wins


def find_peaks(wins, ratio=RATIO, min_people=MIN_PEOPLE, warmup=WARMUP, join_gap=JOIN_GAP):
    if not wins:
        return []
    origin = wins[0].start
    hits = []
    for k, w in enumerate(wins):
        left = wins[k - 1].score if k else 0
        right = wins[k + 1].score if k + 1 < len(wins) else 0
        if (w.start >= origin + warmup and is_spike(w, ratio) and len(w.people) >= min_people
                and w.score >= left and w.score >= right):
            hits.append(w)
    groups = []
    for w in hits:
        if groups and w.start - groups[-1][-1].start <= join_gap:
            groups[-1].append(w)
        else:
            groups.append([w])
    peaks = []
    for g in groups:
        best = max(g, key=lambda w: max(w.ratio / RATIO, w.z / BUSY_Z))
        d = best.as_dict()
        d["first"] = round(g[0].start, 1)
        d["last"] = round(g[-1].start + (wins[1].start - wins[0].start if len(wins) > 1 else WINDOW), 1)
        peaks.append(d)
    return peaks


def auto_min_people(wins):
    """A quiet chat still has peaks: ask for fewer people when the usual window holds few."""
    usual = statistics.median(len(w.people) for w in wins) if wins else 0
    return MIN_PEOPLE if usual >= 3 else 3


def tls():
    """Some Python installs ship without root certificates; use certifi's when it is there."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _gql(body, tries=5):
    data = json.dumps(body).encode()
    for attempt in range(tries):
        try:
            req = urllib.request.Request(TWITCH_GQL, data=data, headers={
                "Client-Id": TWITCH_WEB_CLIENT, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30, context=tls()) as res:
                return json.loads(res.read())
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def _twitch_part(video_id, a, b, progress):
    rows, seen, offset = [], set(), a
    while offset < b:
        answer = _gql([{"operationName": "VideoCommentsByOffsetOrCursor",
                        "variables": {"videoID": video_id, "contentOffsetSeconds": int(offset)},
                        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": TWITCH_COMMENTS_QUERY}}}])
        if not answer:
            raise RuntimeError("Twitch chat replay keeps failing (network, rate limit, or Twitch changed it)")
        video = (answer[0].get("data") or {}).get("video")
        if video is None:
            raise RuntimeError("Twitch returned no video for the chat replay (deleted, sub-only or wrong id)")
        edges = (video.get("comments") or {}).get("edges") or []
        new, last_t = 0, offset
        for edge in edges:
            node = edge.get("node") or {}
            t = node.get("contentOffsetSeconds")
            if t is None:
                continue
            last_t = max(last_t, t)
            if node.get("id") in seen or not a <= t < b:
                continue
            seen.add(node.get("id"))
            new += 1
            text = "".join(f.get("text", "") for f in (node.get("message") or {}).get("fragments") or [])
            rows.append((float(t), (node.get("commenter") or {}).get("login") or "?", clean(text)))
        progress(last_t)
        if not edges:
            break
        if not new and len(edges) < 5:
            break
        offset = last_t if new and last_t > offset else offset + 1
    return rows


def twitch_replay(video_id, duration, a=0.0, b=None):
    b = b if b is not None else duration + 60
    parts = max(1, min(8, int((b - a) // 900)))
    bounds = [a + (b - a) * i / parts for i in range(parts + 1)]
    done = [0.0] * parts
    lock = threading.Lock()

    def run(i):
        def progress(t):
            with lock:
                done[i] = max(done[i], min(t, bounds[i + 1]) - bounds[i])
                print(f"  chat replay {sum(done) / max(1.0, b - a):.0%}", end="\r", flush=True)
        return _twitch_part(video_id, bounds[i], bounds[i + 1], progress)

    with ThreadPoolExecutor(parts) as pool:
        chunks = list(pool.map(run, range(parts)))
    print()
    return sorted(r for c in chunks for r in c)


def youtube_replay(url, folder):
    os.makedirs(folder, exist_ok=True)
    ytdlp = ["yt-dlp"] if _which("yt-dlp") else [sys.executable, "-m", "yt_dlp"]
    subprocess.run(ytdlp + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "--no-playlist",
                            "--no-progress", "-o", os.path.join(folder, "yt.%(ext)s"), url],
                   capture_output=True, text=True)
    files = glob.glob(os.path.join(folder, "*.live_chat.json"))
    if not files:
        return []
    rows = []
    with open(files[0], encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                replay = json.loads(line).get("replayChatItemAction")
            except ValueError:
                continue
            if not replay:
                continue
            t = float(replay.get("videoOffsetTimeMsec", 0)) / 1000
            for action in replay.get("actions") or []:
                item = (action.get("addChatItemAction") or {}).get("item") or {}
                r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                if not r:
                    continue
                text = "".join(run.get("text") or ((run.get("emoji") or {}).get("shortcuts") or [""])[0]
                               for run in (r.get("message") or {}).get("runs") or [])
                user = r.get("authorExternalChannelId") or (r.get("authorName") or {}).get("simpleText") or "?"
                if t >= 0:
                    rows.append((t, user, clean(text)))
    return sorted(rows)


def _which(name):
    from shutil import which
    return which(name)


class TwitchLive(threading.Thread):
    """Reads a live Twitch chat with no account (read-only guest login). Calls on_message(t, user, text)."""

    def __init__(self, channel, on_message):
        super().__init__(daemon=True)
        self.channel = channel.lower().lstrip("#")
        self.on_message = on_message
        self.stop_flag = threading.Event()
        self.connected = False
        self.count = 0
        self.error = None

    def run(self):
        wait = 2
        while not self.stop_flag.is_set():
            try:
                self._session()
                wait = 2
            except Exception as err:
                self.connected = False
                self.error = f"{type(err).__name__}: {err}"
            if self.stop_flag.wait(wait):
                break
            wait = min(60, wait * 2)

    def _session(self):
        raw = socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=30)
        sock = tls().wrap_socket(raw, server_hostname="irc.chat.twitch.tv")
        sock.settimeout(330)
        nick = f"justinfan{int(time.time() * 1000) % 80000 + 10000}"
        sock.sendall(f"CAP REQ :twitch.tv/tags\r\nNICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
        buffer = b""
        try:
            while not self.stop_flag.is_set():
                data = sock.recv(8192)
                if not data:
                    return
                buffer += data
                while b"\r\n" in buffer:
                    line, buffer = buffer.split(b"\r\n", 1)
                    self._line(sock, line.decode("utf-8", errors="replace"))
        finally:
            self.connected = False
            sock.close()

    def _line(self, sock, line):
        if line.startswith("PING"):
            sock.sendall(("PONG" + line[4:] + "\r\n").encode())
            return
        if " JOIN #" in line or " 366 " in line:
            self.connected = True
        if " PRIVMSG #" not in line:
            return
        tags = ""
        if line.startswith("@"):
            tags, line = line[1:].split(" ", 1)
        user = line[1:].split("!", 1)[0]
        text = line.split(" PRIVMSG #", 1)[1].split(" :", 1)[-1]
        sent = re.search(r"tmi-sent-ts=(\d+)", tags)
        t = int(sent.group(1)) / 1000 if sent else time.time()
        self.count += 1
        self.on_message(t, user, clean(text))

    def stop(self):
        self.stop_flag.set()


class LiveDetector:
    """Same scoring as score_rows, fed one message at a time. A window counts as a peak once the
    next one has closed, so a peak is known about 10-20 s after it happens."""

    def __init__(self, origin, window=WINDOW, baseline=BASELINE, ratio=RATIO, min_people=None, warmup=WARMUP):
        self.origin, self.window = origin, window
        self.back = max(1, baseline // window)
        self.ratio, self.min_people, self.warmup = ratio, min_people, warmup
        self.current = Window(origin)
        self.closed = []
        self.lock = threading.Lock()

    def push(self, t, user, text):
        with self.lock:
            self._roll(t)
            self.current.add(user, text)

    def _roll(self, t):
        fresh = []
        while t >= self.current.start + self.window:
            w = self.current.close()
            scores = [x.score for x in self.closed]
            floor = max(1.0, statistics.median(scores)) if scores else 1.0
            recent = scores[-self.back:]
            judge(w, recent, [len(x.reactors) for x in self.closed[-self.back:]], floor,
                  len(recent) >= self.back // 2)
            self.closed.append(w)
            fresh.append(w)
            self.current = Window(w.start + self.window)
        return fresh

    def tick(self, now):
        """Call every few seconds. Returns the peaks that just became certain."""
        with self.lock:
            self._roll(now)
            if len(self.closed) < 3:
                return []
            w, left, right = self.closed[-2], self.closed[-3], self.closed[-1]
            if getattr(w, "judged", False):
                return []
            w.judged = True
            need = self.min_people or (MIN_PEOPLE if statistics.median(
                len(x.people) for x in self.closed[-self.back:]) >= 3 else 3)
            if (w.start >= self.origin + self.warmup and is_spike(w, self.ratio) and len(w.people) >= need
                    and w.score >= left.score and w.score >= right.score):
                return [w.as_dict()]
            return []
