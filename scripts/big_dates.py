#!/usr/bin/env python3
"""Decides which events are "Big dates" (the `featured` flag), by a written rule.

    python3 scripts/big_dates.py feed/events.json            # report only
    python3 scripts/big_dates.py feed/events.json --apply

An event is a Big date when it is open to everyone and any of these is true:
  1. It is one of the town's named annual traditions (TRADITIONS below).
  2. It runs two or more days (up to 16) and isn't a regular repeat: its title appears at
     most twice in the feed, so monthly markets and weekly shows stay out.
  3. It is a rivalry, holiday or playoff game for a local team (GAMES below, or a high
     school playoff game tagged with one of the town's schools).
Everything else is not, including ordinary concerts. To force one in or out by hand, give the
event "pin": true or "pin": false; the script leaves pinned events alone.
"""
import json
import re
import sys
from collections import Counter
from datetime import date

TRADITIONS = {
    "new-braunfels": r"wurstfest|wassailfest|weihnachtsmarkt(?! gala)|downtown lighting|comal county fair|"
                     r"fourth of july|july 4|independence day|new year'?s eve|santa'?s ranch|parade|"
                     r"christmas market days|d[ií]a de los muertos|saengerfest|folkfest|gruene music (&|and) wine",
    "boerne": r"dickens on main|weihnachts|berges ?fest|kendall county fair(?!grounds)|parade|new year'?s eve|"
              r"chocolate walk|wassail walk|stocking stroll",
}
GAMES = (r"lone star showdown|red river|\(thanksgiving\)|christmas day|i-35 (rivalry|showdown)|"
         r"texas (vs\.|at) texas a&m|texas a&m (vs\.|at) texas\b|(bobcats|texas state) (vs\.|at) utsa|utsa (vs\.|at) (bobcats|texas state)|"
         r"\bplayoffs?\b(?!:)|conference (final|championship)|bowl\b")
OPEN_CATEGORIES = {"fest", "holiday", "market", "music", "nature"}
# Multi-day listings that aren't occasions.
NOT_AN_OCCASION = re.compile(r"\bsale\b|workshop|\bclass\b|\bcamp\b|exhibit|registration|photos with", re.I)
# Rivalry and holiday games count for football; playoffs count in any sport.
FOOTBALL = {"horns", "aggies", "txst", "utsa", "cowboys", "texans", "hsfb"}


def days(e):
    return (date.fromisoformat(e.get("endDate", e["startDate"])) - date.fromisoformat(e["startDate"])).days + 1


def main(path, apply=False):
    feed = json.load(open(path, encoding="utf-8"))
    city = (feed.get("city") or {}).get("id", "new-braunfels")
    groups = {c["id"]: c.get("group") for c in feed["categories"]}
    counts = Counter(e["title"].lower() for e in feed["events"])
    tradition = re.compile(TRADITIONS.get(city, r"$^"), re.I)
    game = re.compile(GAMES, re.I)
    changed = []
    for e in feed["events"]:
        if "pin" in e:
            want, why = bool(e["pin"]), "pinned by hand"
        elif e.get("audience") == "local":
            want, why = False, "residents only"
        else:
            sport = groups.get(e["category"]) in ("Pro sports", "College sports") or e["category"] == "hsfb"
            if not sport and tradition.search(e["title"]):
                want, why = True, "named tradition"
            elif (not sport and e["category"] in OPEN_CATEGORIES and 2 <= days(e) <= 16
                  and counts[e["title"].lower()] <= 2 and not NOT_AN_OCCASION.search(e["title"])):
                want, why = True, "multi-day, not a regular repeat"
            elif sport and ((e["category"] in FOOTBALL and game.search(e["title"]))
                            or re.search(r"\bplayoffs?\b(?!:)", e["title"], re.I)
                            or (e["category"] == "hsfb" and e.get("tags"))):
                want, why = True, "rivalry, holiday or playoff game"
            else:
                want, why = False, ""
        if want != bool(e.get("featured")):
            changed.append((e["startDate"], e["title"], want, why))
        if want: e["featured"] = True
        else: e.pop("featured", None)
    # The same game can be on two teams' schedules; keep one card for it.
    seen = set()
    for e in feed["events"]:
        if not e.get("featured") or "pin" in e: continue
        teams = re.sub(r"\(.*?\)", "", e["title"].lower())
        key = (e["startDate"], frozenset(w for w in re.split(r"\s+(?:vs\.|at)\s+", teams.strip()) if w))
        if len(key[1]) == 2:
            if key in seen:
                e.pop("featured", None)
                changed[:] = [c for c in changed if not (c[0] == e["startDate"] and c[1] == e["title"])]
            seen.add(key)
    today = date.today().isoformat()
    for d, t, want, why in sorted(changed):
        if d >= today or want: print(f"  {'+' if want else '-'} {d}  {t}" + (f"  ({why})" if why else ""))
    upcoming = [e for e in feed["events"] if e.get("featured") and e.get("endDate", e["startDate"]) >= today]
    print(f"{len(upcoming)} upcoming Big dates in {city}:")
    for e in sorted(upcoming, key=lambda e: e["startDate"]): print(f"    {e['startDate']}  {e['title']}")
    if apply:
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(feed, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], apply="--apply" in sys.argv)
