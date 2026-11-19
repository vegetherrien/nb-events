"""Keeps the college and pro team schedules current and sorts the teams into filter groups.

    python3 scripts/scrape_sports.py events.json            # report only
    python3 scripts/scrape_sports.py events.json --apply

Sources are official ones that allow an honestly-named client: team sites that publish their
schedule in the page (schema.org SportsEvent) and Major League Baseball's public schedule API.
Sites that refuse automated requests are not used; those teams are entered from
research/sports/*.json instead (see merge at the bottom).
"""
import glob
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

UA = "nb-events-feed-bot/1.0"
CENTRAL = ZoneInfo("America/Chicago")
RANGE_END = "2027-04-30"

# Filter groups, in display order. Teams not in the feed are skipped.
GROUPS = {
    "Pro sports": ["spurs", "mavs", "rockets", "astros", "rangers", "cowboys", "texans",
                   "safc", "austinfc", "dynamo", "fcdallas", "dash", "locomotive"],
    "College sports": ["horns", "horns-bb", "txst", "txst-bb", "utsa", "utsa-bb", "aggies"],
    "My schools": ["hsfb"],
}
BOTH = ["new-braunfels", "boerne"]

# hidden=True: the team starts switched off (people opt in), like the Cowboys.
SOURCES = [
    dict(kind="page", category="txst-bb", name="Texas State basketball", nick="Bobcats", cities=["new-braunfels"],
         url="https://txst.com/sports/mens-basketball/schedule", team="Texas State University",
         home="Strahan Arena, San Marcos", home_marks=["Strahan", "San Marcos"], symbol="basketball.fill", light="#6B2D1A", dark="#D9A441"),
    dict(kind="ics", category="horns-bb", name="Longhorns basketball", nick="Texas", cities=BOTH,
         url="https://texaslonghorns.com/calendar.ashx/calendar.ics?sport_id=4",
         link="https://texaslonghorns.com/sports/mens-basketball/schedule",
         team="University of Texas Men's Basketball", home="Moody Center, Austin",
         symbol="basketball.fill", light="#BF5700", dark="#F0A060"),
    dict(kind="page", category="utsa-bb", name="UTSA basketball", nick="UTSA", cities=BOTH, hidden=True,
         url="https://goutsa.com/sports/mens-basketball/schedule", team="UTSA",
         home="Convocation Center, San Antonio", symbol="basketball.fill", light="#0C2340", dark="#F15A22"),
    dict(kind="page", category="texans", name="Texans football", nick="Texans", cities=BOTH, hidden=True,
         url="https://www.houstontexans.com/schedule/", team="Houston Texans", short_opponent=True,
         home="NRG Stadium, Houston", home_marks=["Reliant", "NRG", "Houston"], symbol="football.fill", light="#03202F", dark="#C8414B"),
    dict(kind="mlb", category="astros", name="Astros baseball", nick="Astros", cities=BOTH, team_id=117,
         symbol="baseball.fill", light="#EB6E1F", dark="#F4A261"),
    dict(kind="mlb", category="rangers", name="Rangers baseball", nick="Rangers", cities=BOTH, team_id=140,
         hidden=True, symbol="baseball.fill", light="#003278", dark="#7FA8E8"),
]
# Colors and symbols for teams entered from research files.
EXTRA = {
    "spurs": ("Spurs basketball", "basketball.fill", "#55616B", "#B8C2CC", False),
    "mavs": ("Mavericks basketball", "basketball.fill", "#00538C", "#6CB4EE", True),
    "rockets": ("Rockets basketball", "basketball.fill", "#CE1141", "#F27A8E", True),
    "safc": ("San Antonio FC soccer", "soccerball", "#1B1B1B", "#C8C8C8", False),
    "austinfc": ("Austin FC soccer", "soccerball", "#00823C", "#00B140", False),
    "dynamo": ("Houston Dynamo soccer", "soccerball", "#F4911E", "#F9B560", True),
    "fcdallas": ("FC Dallas soccer", "soccerball", "#BF0D3E", "#EE6A88", True),
    "dash": ("Houston Dash soccer", "soccerball", "#F36018", "#8AB7E9", True),
    "locomotive": ("El Paso Locomotive soccer", "soccerball", "#0E2240", "#7FB2E5", True),
}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def event(src, day, clock, home, opponent, place, listed=""):
    # "vs" somewhere other than the home city is a neutral-site game, not a home game.
    marks = src.get("home_marks") or [src.get("home", "").split(", ")[-1]]
    neutral = bool(home and listed and not any(m.lower() in listed.lower() for m in marks if m))
    if neutral:
        place = listed
    e = {"id": f"{day}-{src['category']}-{'vs' if home else 'at'}-{slug(opponent)}"[:80],
         "category": src["category"], "title": f"{src['nick']} {'vs.' if home else 'at'} {opponent}",
         "startDate": day, "location": place, "tag": "Neutral site" if neutral else "Home" if home else "Away", "sourceURL": src.get("link", src.get("url"))}
    if clock and clock != "00:00":
        e["startTime"] = clock
    return e


def local_time(stamp):
    """('YYYY-MM-DD', 'HH:MM') in Central. Stamps ending in Z are UTC; bare ones are already local."""
    if stamp.endswith("Z"):
        dt = datetime.fromisoformat(stamp[:19]).replace(tzinfo=timezone.utc).astimezone(CENTRAL)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    return stamp[:10], stamp[11:16]


def ics_games(src, today):
    """Games from a team's public calendar subscription (.ics, times in UTC)."""
    out = []
    text = re.sub(r"\r?\n[ \t]", "", fetch(src["url"]))
    for block in text.split("BEGIN:VEVENT")[1:]:
        f = dict(re.findall(r"^(DTSTART|SUMMARY|LOCATION|DESCRIPTION)[^:\n]*:(.*)$", block, re.M))
        m = re.search(re.escape(src["team"]) + r"\s+(at|vs\.?)\s+(.+)", f.get("SUMMARY", ""), re.I)
        t = re.match(r"(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)", f.get("DTSTART", ""))
        if not m or not t:
            continue
        day, clock = local_time("%s-%s-%sT%s:%s:00Z" % t.groups())
        if day < today:
            continue
        home = m.group(1).lower().startswith("vs")
        opponent = re.sub(r"\s+-\s+.*$", "", m.group(2).strip().rstrip("\r")).strip()     # drop sponsor / series names
        opponent = re.sub(r"^#\d+\s+", "", opponent)
        if opponent.upper() in ("TBD", "TBA"):
            continue
        place = f.get("LOCATION", "").replace("\\,", ",").strip().rstrip("\r")
        parts = [x.strip() for x in place.split(",")]
        away_place = ", ".join(parts[2:] + parts[:1]) if len(parts) >= 3 else place     # "Arena, City"
        e = event(src, day, clock, home, opponent, src["home"] if home else (away_place or opponent), place)
        tv = re.search(r"TV:\s*([^\\\n]+?)\s*(\\n|$)", f.get("DESCRIPTION", ""))
        if tv and " or " not in tv.group(1):
            e["tv"] = tv.group(1).strip()
        out.append(e)
    return out


def page_games(src, today):
    """Games from schema.org SportsEvent data in a team's schedule page."""
    out = []
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', fetch(src["url"]), re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for g in items:
            if not isinstance(g, dict) or g.get("@type") not in ("SportsEvent", "Event") or not g.get("startDate"):
                continue
            day, clock = local_time(g["startDate"])
            m = re.match(re.escape(src["team"]) + r"\s+(at|vs\.?)\s+(.+)", g.get("name", ""), re.I)
            if day < today or not m or m.group(2).strip().upper() in ("TBD", "TBA"):
                continue
            home = m.group(1).lower().startswith("vs")
            opponent = re.sub(r"\s+", " ", m.group(2)).strip()
            if src.get("short_opponent"):
                opponent = opponent.split(" ")[-1]          # "Indianapolis Colts" -> "Colts"
            place = ((g.get("location") or {}).get("name") or "").strip()
            out.append(event(src, day, clock, home, opponent, src["home"] if home else (place or opponent), place))
    return out


def mlb_games(src, today):
    """Regular-season games from MLB's public schedule API."""
    year = RANGE_END[:4]
    url = (f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId={src['team_id']}&season={year}"
           f"&gameType=R&startDate={today}&endDate={RANGE_END}&hydrate=team,venue(location)")
    src["link"] = f"https://www.mlb.com/{src['nick'].lower()}/schedule"
    out = []
    for d in json.loads(fetch(url)).get("dates", []):
        for g in d["games"]:
            when = datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00")).astimezone(CENTRAL)
            home = g["teams"]["home"]["team"]["id"] == src["team_id"]
            other = g["teams"]["away" if home else "home"]["team"]
            venue = g.get("venue", {})
            city = (venue.get("location") or {}).get("city", "")
            place = ", ".join(x for x in [venue.get("name"), city] if x)
            tbd = (g.get("status") or {}).get("startTimeTBD")
            e = event(src, when.strftime("%Y-%m-%d"), None if tbd else when.strftime("%H:%M"), home,
                      other.get("teamName") or other["name"], place)
            if any(x["id"] == e["id"] for x in out):          # doubleheader
                e["id"] += "-2"
            out.append(e)
    return out


def main(path, apply=False):
    feed = json.load(open(path, encoding="utf-8"))
    city = (feed.get("city") or {}).get("id", "new-braunfels")
    today = date.today().isoformat()
    cats = feed["categories"]

    def ensure(cid, name, symbol, light, dark, hidden):
        if not any(c["id"] == cid for c in cats):
            c = {"id": cid, "name": name, "light": light, "dark": dark, "symbol": symbol}
            if hidden: c["defaultHidden"] = True
            cats.append(c)

    def replace(cid, found):
        feed["events"] = [e for e in feed["events"] if e["category"] != cid] + found

    for src in SOURCES:
        if city not in src["cities"]:
            continue
        try:
            found = {"page": page_games, "ics": ics_games, "mlb": mlb_games}[src["kind"]](src, today)
        except Exception as ex:
            print(f"! {src['name']}: {ex}")
            continue
        found = [e for e in found if e["startDate"] <= RANGE_END]
        print(f"  {src['name']}: {len(found)} upcoming game(s)")
        if found:       # keep what we had rather than wipe a season on a bad day
            ensure(src["category"], src["name"], src["symbol"], src["light"], src["dark"], src.get("hidden"))
            replace(src["category"], found)

    # Teams whose sites refuse automated requests: entered by hand into research/sports/*.json.
    by_cat = {}
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "research", "sports", "*.json")
    for f in glob.glob(here):
        for e in json.load(open(f)).get("events", []):
            if today <= e["startDate"] <= RANGE_END and e["category"] in EXTRA:
                by_cat.setdefault(e["category"], []).append(e)
    for cid, found in by_cat.items():
        name, symbol, light, dark, hidden = EXTRA[cid]
        print(f"  {name}: {len(found)} game(s) from research file")
        ensure(cid, name, symbol, light, dark, hidden)
        replace(cid, found)

    # Sort teams into their groups, each group placed where its first team was (or at the end).
    order = [cid for ids in GROUPS.values() for cid in ids]
    group_of = {cid: g for g, ids in GROUPS.items() for cid in ids}
    teams = sorted((c for c in cats if c["id"] in group_of), key=lambda c: order.index(c["id"]))
    rest = [c for c in cats if c["id"] not in group_of]
    for c in teams:
        c["group"] = group_of[c["id"]]
    school_at = max((i for i, c in enumerate(rest) if c.get("group") == "My schools"), default=-1) + 1
    schools = [c for c in teams if c["group"] == "My schools"]
    feed["categories"] = rest[:school_at] + schools + rest[school_at:] + [c for c in teams if c["group"] != "My schools"]
    for c in feed["categories"]:
        if c["id"] == "hsfb": c["name"] = "Area high school football"

    if feed.get("rangeEnd", "") < RANGE_END:
        feed["rangeEnd"] = RANGE_END
    feed["events"].sort(key=lambda e: (e["startDate"], e.get("startTime") or ""))
    if apply:
        feed["updated"] = date.today().isoformat()
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(feed, indent=1, ensure_ascii=False) + "\n")
        print(f"Wrote {len(feed['events'])} events to {path}.")
    return feed


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], apply="--apply" in sys.argv)
