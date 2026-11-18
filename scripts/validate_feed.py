"""Checks events.json so a typo can't break the NB Events app.

Run: python3 scripts/validate_feed.py events.json
Exits non-zero and prints every problem it finds.
"""
import json
import re
import sys
from datetime import date

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def valid_date(s):
    if not isinstance(s, str) or not DATE.match(s):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def main(path):
    problems = []

    try:
        with open(path, encoding="utf-8") as f:
            feed = json.load(f)
    except json.JSONDecodeError as e:
        print(f"::error file={path},line={e.lineno},col={e.colno}::Not valid JSON: {e.msg}")
        print(f"\n{path} is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}")
        print("Look for a missing comma or quote just before that spot.")
        return 1

    def bad(where, msg):
        problems.append(f"{where}: {msg}")

    for key in ("schemaVersion", "updated", "categories", "events"):
        if key not in feed:
            bad("top level", f"missing \"{key}\"")
    if "updated" in feed and not valid_date(feed["updated"]):
        bad("top level", f"\"updated\" should be a date like 2026-11-18, got {feed['updated']!r}")

    cats = feed.get("categories", [])
    cat_ids = set()
    for i, c in enumerate(cats):
        where = f"category #{i + 1} ({c.get('id', '?')})"
        for key in ("id", "name", "light", "dark", "symbol"):
            if key not in c:
                bad(where, f"missing \"{key}\"")
        for key in ("light", "dark"):
            if key in c and not COLOR.match(str(c[key])):
                bad(where, f"\"{key}\" should be a color like #1C5CA3, got {c[key]!r}")
        if c.get("id") in cat_ids:
            bad(where, "duplicate category id")
        cat_ids.add(c.get("id"))

    seen = set()
    for i, e in enumerate(feed.get("events", [])):
        where = f"event #{i + 1} ({e.get('title', e.get('id', '?'))})"
        for key in ("id", "category", "title", "startDate"):
            if not e.get(key):
                bad(where, f"missing \"{key}\"")
        if e.get("id") in seen:
            bad(where, f"duplicate id {e.get('id')!r} (ids must be unique)")
        seen.add(e.get("id"))
        if e.get("category") and e["category"] not in cat_ids:
            bad(where, f"category {e['category']!r} isn't in the categories list")
        if "startDate" in e and not valid_date(e["startDate"]):
            bad(where, f"\"startDate\" should look like 2026-11-21, got {e['startDate']!r}")
        if "endDate" in e:
            if not valid_date(e["endDate"]):
                bad(where, f"\"endDate\" should look like 2026-11-22, got {e['endDate']!r}")
            elif valid_date(e.get("startDate")) and e["endDate"] < e["startDate"]:
                bad(where, "\"endDate\" is before \"startDate\"")
        for key in ("startTime", "endTime"):
            if key in e and not TIME.match(str(e[key])):
                bad(where, f"\"{key}\" should be 24-hour like 18:30, got {e[key]!r}")

    for section in ("weekly", "notDated"):
        for i, item in enumerate(feed.get(section, [])):
            where = f"{section} #{i + 1} ({item.get('title', '?')})"
            if not item.get("title"):
                bad(where, "missing \"title\"")
            if item.get("category") and item["category"] not in cat_ids:
                bad(where, f"category {item['category']!r} isn't in the categories list")

    if problems:
        print(f"{path} has {len(problems)} problem(s):\n")
        for p in problems:
            print(f"  - {p}")
            print(f"::error file={path}::{p}")
        return 1

    print(f"{path} looks good: {len(feed['events'])} events, {len(cats)} categories.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "events.json"))
