"""Remembers how the person likes their clips, so the questions are asked once."""
import argparse
import json
import os

PATH = os.path.join(os.path.expanduser("~"), ".config", "clipper", "preferences.json")
KEYS = {"count": int, "min_seconds": int, "max_seconds": int, "color": str, "sfx": bool, "zoom": bool, "language": str,
        "output": str, "after": str, "moments": str}


def load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def parse(key, value):
    kind = KEYS[key]
    if kind is bool:
        return value.lower() in ("1", "true", "yes", "on")
    return kind(value)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["show", "set", "taste", "untaste", "forget"])
    ap.add_argument("pairs", nargs="*", help="key=value for set; one sentence for taste; a number for untaste")
    a = ap.parse_args()
    prefs = load()
    if a.action == "show":
        if not prefs:
            print("no saved preferences")
        else:
            print("saved preferences (" + PATH + "):")
            for k, v in prefs.items():
                if k != "taste":
                    print(f"  {k}: {v}")
            if prefs.get("taste"):
                print("what this person likes and dislikes in a clip (follow these when picking and editing):")
                for n, line in enumerate(prefs["taste"], 1):
                    print(f"  {n}. {line}")
        return
    if a.action in ("taste", "untaste"):
        notes = prefs.get("taste", [])
        if a.action == "taste":
            notes.append(" ".join(a.pairs).strip())
        else:
            for n in sorted((int(x) for x in a.pairs), reverse=True):
                if 1 <= n <= len(notes):
                    notes.pop(n - 1)
        prefs["taste"] = [n for n in notes if n]
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with open(PATH, "w") as f:
            json.dump(prefs, f, indent=2, ensure_ascii=False)
        print(f"{len(prefs['taste'])} taste notes saved")
        return
    if a.action == "forget":
        if os.path.exists(PATH):
            os.remove(PATH)
        print("preferences forgotten")
        return
    for pair in a.pairs:
        key, _, value = pair.partition("=")
        if key not in KEYS:
            print(f"unknown key {key}; known: {', '.join(KEYS)}")
            continue
        prefs[key] = parse(key, value)
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w") as f:
        json.dump(prefs, f, indent=2)
    print("saved: " + ", ".join(f"{k}={v}" for k, v in prefs.items() if k != "taste"))


if __name__ == "__main__":
    main()
