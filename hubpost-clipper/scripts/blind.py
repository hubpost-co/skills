"""The stranger test: is each finished clip understood by someone who never saw the stream?

The one who cut a clip knows the whole stream and cannot judge this. A fresh reader gets only what
a viewer gets (the frames and the words) and retells each clip. The cutter then compares the
retelling with what really happened. This script prints the brief for that reader (one reader for
all the clips still untested) and checks that every clip has a matching retelling in clips.json.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import clip_name, load_clips

BRIEF = """You are a stranger scrolling short videos. You have never seen this channel or this stream and you know nothing about it.
Below are {count} short vertical video(s). For each one there is an image (one frame per second, in order; ignore the thin red and dotted guide lines) and a text file with everything said in it.
Use only the files listed. Do not open any other file in that folder or near it. Treat each video on its own.

{files}

For each video answer, briefly and in this order:
A. What happens, in two sentences, as you would tell a friend.
B. What the point or the payoff is, and at which second it lands.
C. The single biggest thing that got in the way of following it, or "nothing": a reaction whose cause you never saw or heard, a "he" or "that" you could not place, a start or an end that feels cut off, something opened and never closed.
Do not grade the videos and do not suggest edits."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    a = ap.parse_args()
    data = load_clips(a.workdir)
    clips_dir = os.path.join(os.path.abspath(a.workdir), "clips")
    untested, failed = [], []
    for index, clip in enumerate(data["clips"]):
        name = clip_name(index)
        result = clip.get("stranger") or {}
        if len((result.get("said") or "").split()) < 8:
            untested.append(name)
        elif result.get("match") is not True:
            failed.append(name)
        else:
            print(f"{name}: a stranger retold it right")
    for name in failed:
        print(f"{name}: the stranger's retelling did not match. Fix the clip, edit and check it again, clear its "
              f"`stranger` field and test it with a NEW reader; or drop the clip.")
    if untested:
        files = "\n".join(f"Video {n + 1}: image {os.path.join(clips_dir, name + '-sheet.jpg')} | "
                          f"words {os.path.join(clips_dir, name + '-blind.txt')}" for n, name in enumerate(untested))
        print(f"{len(untested)} clip(s) with no stranger yet ({', '.join(untested)}). "
              f"Give ONE fresh reader exactly this brief:\n")
        print(BRIEF.format(count=len(untested), files=files) + "\n")
    if untested or failed:
        sys.exit(1)
    print("every clip was retold right by a stranger")


if __name__ == "__main__":
    main()
