"""Writes everything that was said as one page to read end to end, with sound and chat marks in place.

The page is what the moments are chosen from: a good spoken moment has no loud peak, so it only
shows up to someone who reads the whole thing.
"""
PAUSE = 2.0
QUIET = 8.0
MAX_WORDS = 70


def clock(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def paragraphs(words):
    out, current = [], []
    for w in words:
        if current:
            gap = w["start"] - current[-1]["end"]
            ended = current[-1]["text"].rstrip().endswith((".", "?", "!"))
            if gap >= PAUSE or (len(current) >= MAX_WORDS and ended) or len(current) >= MAX_WORDS * 2:
                out.append(current)
                current = []
        current.append(w)
    if current:
        out.append(current)
    return out


def write(path, words, marks, duration, joins=(), stream_clock=None, intro=(), start=0.0,
          positions="positions in the file; use them for the cut"):
    """marks: (seconds, text) pairs. stream_clock(t) gives the time in the original stream, or None."""
    marks = sorted(marks)
    joins = sorted(joins)
    lines = list(intro) + [
        f"Everything that was said, in order. [seconds] are {positions}.",
        "{...} marks where the sound jumped, went quiet, or the chat reacted. A blank stretch is noted with its length.",
        "No mark does not mean no moment: most spoken moments have none.", ""]
    last_end = start

    def flush_between(until):
        nonlocal marks, joins
        while joins and joins[0] <= until:
            j = joins.pop(0)
            where = stream_clock(j + 0.5) if stream_clock else None
            lines.append(f"=== join at {j:.1f} s: the recording jumps" + (f" to {clock(where)} of the stream" if where else "")
                         + ". Nothing may cross this line. ===")
        loose = [m for m in marks if m[0] < until]
        marks = [m for m in marks if m[0] >= until]
        for t, text in loose:
            lines.append(f"        [{t:.1f}] {{{text}}} with nobody talking")

    for group in paragraphs(words):
        start, end = group[0]["start"], group[-1]["end"]
        if start - last_end >= QUIET:
            lines.append(f"        ... {start - last_end:.0f} s with nobody talking ...")
        flush_between(start)
        parts = []
        for w in group:
            while marks and marks[0][0] <= w["start"]:
                parts.append("{" + marks.pop(0)[1] + "}")
            parts.append(w["text"])
        while marks and marks[0][0] <= end:
            parts.append("{" + marks.pop(0)[1] + "}")
        origin = stream_clock(start) if stream_clock else None
        head = f"[{start:.1f} - {end:.1f}] ({clock(origin if origin is not None else start)})"
        lines.append(f"{head} {' '.join(parts)}")
        last_end = end
    if duration - last_end >= QUIET:
        lines.append(f"        ... {duration - last_end:.0f} s with nobody talking ...")
    flush_between(duration + 1)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
