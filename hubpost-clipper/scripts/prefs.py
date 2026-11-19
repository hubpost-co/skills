"""Remembers how the person likes their clips, so the questions are asked once."""
import argparse
import json
import os

PATH = os.path.join(os.path.expanduser("~"), ".config", "clipper", "preferences.json")
KEYS = {"count": int, "min_seconds": int, "max_seconds": int, "color": str, "sfx": bool, "zoom": bool, "language": str,
        "output": str, "after": str, "credit": str}


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
    ap.add_argument("action", choices=["show", "set", "forget"])
    ap.add_argument("pairs", nargs="*", help="key=value")
    a = ap.parse_args()
    prefs = load()
    if a.action == "show":
        if not prefs:
            print("no saved preferences")
        else:
            print("saved preferences (" + PATH + "):")
            for k, v in prefs.items():
                print(f"  {k}: {v}")
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
    print("saved: " + ", ".join(f"{k}={v}" for k, v in prefs.items()))


if __name__ == "__main__":
    main()
