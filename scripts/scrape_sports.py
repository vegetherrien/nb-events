"""Groups the college and pro teams under one "Sports" filter group and refreshes schedules that
come from a team's own website.

    python3 scripts/scrape_sports.py events.json            # report only
    python3 scripts/scrape_sports.py events.json --apply

Sources are official athletics pages that publish schedule data in the page (schema.org
SportsEvent). Sites that refuse automated requests are not used.
"""
import json
import re
import sys
import urllib.request
from datetime import date

UA = "nb-events-feed-bot/1.0"
GROUP = "Sports"
# Team categories that belong in the Sports group (college and pro teams from around here).
TEAM_IDS = ["spurs", "horns", "txst", "txst-bb", "utsa", "aggies", "cowboys", "hsfb"]

# (category, nickname, schedule page, team name as the page writes it, home venue, only for these city ids)
SOURCES = [
    dict(category="txst-bb", name="Texas State basketball", nick="Bobcats", cities=["new-braunfels"],
         url="https://txst.com/sports/mens-basketball/schedule", school="Texas State University",
         home="Strahan Arena, San Marcos", symbol="basketball.fill", light="#6B2D1A", dark="#D9A441",
         after="spurs"),
]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def games(src, today):
    out = []
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', fetch(src["url"]), re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for g in (data if isinstance(data, list) else [data]):
            if not isinstance(g, dict) or g.get("@type") != "SportsEvent" or not g.get("startDate"):
                continue
            day, clock = g["startDate"][:10], g["startDate"][11:16]
            m = re.match(re.escape(src["school"]) + r"\s+(at|vs\.?)\s+(.+)", g.get("name", ""), re.I)
            if day < today or not m or m.group(2).strip().upper() in ("TBD", "TBA"):
                continue
            home = m.group(1).lower().startswith("vs")
            opponent = re.sub(r"\s+", " ", m.group(2)).strip()
            place = ((g.get("location") or {}).get("name") or "").strip()
            e = {"id": f"{day}-{src['category']}-{re.sub(r'[^a-z0-9]+', '-', opponent.lower()).strip('-')}"[:80],
                 "category": src["category"], "title": f"{src['nick']} {'vs.' if home else 'at'} {opponent}",
                 "startDate": day, "location": src["home"] if home else (place or opponent),
                 "tag": "Home" if home else "Away", "sourceURL": src["url"]}
            if clock and clock != "00:00":
                e["startTime"] = clock
            out.append(e)
    return out


def main(path, apply=False):
    feed = json.load(open(path, encoding="utf-8"))
    city = (feed.get("city") or {}).get("id", "new-braunfels")
    today = date.today().isoformat()
    cats = feed["categories"]
    for src in SOURCES:
        if city not in src["cities"]:
            continue
        try:
            found = games(src, today)
        except Exception as ex:
            print(f"! {src['name']}: {ex}")
            continue
        print(f"  {src['name']}: {len(found)} upcoming game(s)")
        if not found:
            continue          # keep what we had rather than wipe a season on a bad day
        if not any(c["id"] == src["category"] for c in cats):
            at = next((i for i, c in enumerate(cats) if c["id"] == src["after"]), len(cats) - 1) + 1
            cats.insert(at, {"id": src["category"], "name": src["name"], "light": src["light"],
                             "dark": src["dark"], "symbol": src["symbol"]})
        feed["events"] = [e for e in feed["events"] if e["category"] != src["category"]] + found
    # One Sports group, teams in a fixed order, placed where the first of them was.
    teams = sorted((c for c in cats if c["id"] in TEAM_IDS), key=lambda c: TEAM_IDS.index(c["id"]))
    if teams:
        at = min(cats.index(c) for c in teams)
        rest = [c for c in cats if c["id"] not in TEAM_IDS]
        for c in teams:
            c["group"] = GROUP
        feed["categories"] = rest[:at] + teams + rest[at:]
    feed["events"].sort(key=lambda e: (e["startDate"], e.get("startTime") or ""))
    if apply:
        feed["updated"] = date.today().isoformat()
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(feed, indent=1, ensure_ascii=False) + "\n")
        print(f"Wrote {len(feed['events'])} events to {path}.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], apply="--apply" in sys.argv)
