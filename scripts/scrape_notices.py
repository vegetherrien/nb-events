#!/usr/bin/env python3
"""Refreshes the "Good to know" notices in a feed.

    python3 scripts/scrape_notices.py feed/events.json            # report only
    python3 scripts/scrape_notices.py feed/events.json --apply

Runs every collector in scripts/notices/ (see the README there) for the feed's city and
replaces the feed's `notices` list. A collector that fails keeps its previous notices, so one
bad source doesn't wipe the card; the app hides anything that hasn't been re-checked recently.
"""
import importlib.util
import glob
import json
import os
import sys
from datetime import date

REQUIRED = ("id", "kind", "title", "detail", "level", "url", "source", "asOf")
HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    spec = importlib.util.spec_from_file_location("notices_" + os.path.basename(path)[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(feed_path, apply=False):
    feed = json.load(open(feed_path, encoding="utf-8"))
    city = (feed.get("city") or {}).get("id", "new-braunfels")
    old = feed.get("notices", [])
    out, seen = [], set()
    for path in sorted(glob.glob(os.path.join(HERE, "notices", "*.py"))):
        name = os.path.basename(path)[:-3]
        try:
            found = load(path).collect(city)
        except Exception as ex:
            kept = [n for n in old if n.get("collector") == name]
            print(f"! {name}: {ex}; keeping {len(kept)} earlier notice(s)")
            found = kept
        good = []
        for n in found:
            missing = [k for k in REQUIRED if not n.get(k)]
            if missing or n["id"] in seen:
                print(f"  skipped {n.get('id')}: {'missing ' + ', '.join(missing) if missing else 'duplicate id'}")
                continue
            if n.get("until") and n["until"] < date.today().isoformat():
                continue
            n["title"], n["detail"] = n["title"][:70], n["detail"][:240]
            n["collector"] = name
            seen.add(n["id"]); good.append(n)
        print(f"  {name}: {len(good)} notice(s)")
        out += good
    for n in out:
        print(f"    [{n['level']:<5}] {n['kind']:<9} {n['title']}")
    if apply:
        feed["notices"] = out
        with open(feed_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(feed, indent=1, ensure_ascii=False) + "\n")
        print(f"Wrote {len(out)} notice(s) to {feed_path}.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], apply="--apply" in sys.argv)
