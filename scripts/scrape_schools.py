"""Refreshes school events (campus calendars and varsity games) in events.json.

    python3 scripts/scrape_schools.py events.json local.json            # report only
    python3 scripts/scrape_schools.py events.json local.json --apply    # rewrite the school events

Schools and their feed URLs come from local.json (info.calendar, info.athletics). Every school
event is tagged with its school and marked "audience": "local", so only families zoned to that
school see it. Each run replaces all scraped school events (ids starting "sch-"), so time changes
and cancellations carry through. Hand-entered district items (ids starting "nbisd-") are kept.
"""
import json
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UA = "Mozilla/5.0 (compatible; nb-events-feed-bot)"
CENTRAL = ZoneInfo("America/Chicago")
CALENDAR_DAYS = 90     # how far ahead campus calendars are read
GAME_DAYS = 60         # how far ahead varsity schedules are read

# Calendar filler that isn't an event a family would go to or plan around.
DROP = re.compile(r"appreciation|awareness|\bmonth\b|\bweek\b(?!end)|national .* day|staff (meeting|development)|"
                  r"\bplc\b|faculty|lunch menu|\bA day\b|\bB day\b|birthday|tutorial|duty", re.I)
HOLIDAY = re.compile(r"holiday|break|no school|early release|first day|last day|staff development|workday", re.I)
SPORTS = {"(F)": "girls ", "(M)": "boys ", "(C)": ""}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def unescape(s):
    return re.sub(r"[ \t]+", " ", s.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
                  .replace("\\;", ";").replace("\\\\", "\\")).strip()


def parse_ics(text):
    """VEVENTs as dicts: start/end are (date, 'HH:MM' or None) in Central time; days = length for all-day."""
    out, cur = [], None
    for line in re.sub(r"\r?\n[ \t]", "", text).splitlines():
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur and cur.get("start"):
                out.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            prop, _, value = line.partition(":")
            key = prop.split(";")[0]
            if key in ("DTSTART", "DTEND"):
                cur["start" if key == "DTSTART" else "end"] = parse_dt(prop, value)
            elif key == "DURATION":
                m = re.fullmatch(r"P(\d+)D", value.strip())
                if m: cur["days"] = int(m.group(1))
            elif key == "RRULE":
                m = re.search(r"FREQ=DAILY;COUNT=(\d+)", value)
                cur["repeat"] = int(m.group(1)) if m else -1     # -1: a repeat we don't expand
            elif key in ("SUMMARY", "LOCATION", "DESCRIPTION", "URL", "STATUS"):
                cur[key] = unescape(value)
    return out


def parse_dt(prop, value):
    v = value.strip()
    if "VALUE=DATE" in prop or re.fullmatch(r"\d{8}", v):
        return date(int(v[:4]), int(v[4:6]), int(v[6:8])), None
    m = re.match(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})", v)
    if not m:
        return None
    dt = datetime(*map(int, m.groups()))
    if v.endswith("Z"):
        dt = dt.replace(tzinfo=timezone.utc).astimezone(CENTRAL)
    return dt.date(), dt.strftime("%H:%M")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def tidy(title):
    t = re.sub(r"\s+", " ", title).strip(" -")
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8 and len(t) > 6:
        t = t.title()
    return t[:90]


def calendar_events(school, today):
    """Upcoming items from one campus (or district) calendar."""
    items = []
    for v in parse_ics(fetch(school["info"]["calendar"])):
        title = tidy(v.get("SUMMARY", ""))
        if not title or v.get("STATUS") == "CANCELLED" or v.get("repeat") == -1 or DROP.search(title):
            continue
        day, start = v["start"]
        if start is None:
            if v.get("repeat"):
                last = day + timedelta(days=v["repeat"] - 1)
            elif v.get("end"):
                last = v["end"][0] - timedelta(days=1)      # all-day DTEND is exclusive
            else:
                last = day + timedelta(days=v.get("days", 1) - 1)
        else:
            last = day
        last = max(last, day)
        if last < today or day > today + timedelta(days=CALENDAR_DAYS) or (last - day).days > 21:
            continue
        e = {"title": title, "startDate": day.isoformat()}
        if last > day: e["endDate"] = last.isoformat()
        if start and start != "00:00":
            e["startTime"] = start
            if v.get("end") and v["end"][0] == day and v["end"][1] and v["end"][1] > start:
                e["endTime"] = v["end"][1]
        e["location"] = (v.get("LOCATION") or "").split("\n")[0][:90] or school["name"]
        e["sourceURL"] = v.get("URL") or school["info"].get("url", "")
        items.append(e)
    return items


def game_events(school, today):
    """Upcoming varsity games from a Rank One schedule feed."""
    url = school["info"]["athletics"].replace("View_Schedule_All_Web.aspx", "iCalFeed.aspx").replace("&Mt=0", "")
    text = fetch(url)
    if "BEGIN:VCALENDAR" not in text:
        return []        # "schedule has not been made public" comes back as a web page
    short, items, seen = school["info"].get("short", school["name"]), [], set()
    for v in parse_ics(text):
        desc = v.get("DESCRIPTION", "")
        m = re.match(r".*? - (.+?) (\([FMC]\)) - Varsity\b", desc.split("\n")[0])
        day, start = v["start"]
        if not m or not (today <= day <= today + timedelta(days=GAME_DAYS)):
            continue
        sport = SPORTS[m.group(2)] + m.group(1).lower()
        opp = re.search(r"^(vs\.?|at) (.+)$", desc, re.M)
        opponent = re.sub(r"\b(High School|HS)\b", "", opp.group(2)).strip() if opp else ""
        words = opponent.split()
        if len(words) > 1 and words[-1] in words[:-1]: words.pop()       # "Davenport Davenport"
        opponent = " ".join(words) or "TBA"
        where = v.get("LOCATION", "")
        home = where.lower().startswith("home")
        venue = where.split("-", 1)[1].strip() if "-" in where else ""
        title = f"{short} {sport} {'vs.' if home else 'at'} {opponent}"
        if (title, day) in seen:
            continue
        seen.add((title, day))
        e = {"title": tidy(title), "startDate": day.isoformat(), "tag": "Home" if home else "Away",
             "location": venue or (school["name"] if home else opponent),
             "sourceURL": school["info"]["athletics"]}
        if start and start != "00:00": e["startTime"] = start
        items.append(e)
    return items


def main(feed_path, local_path, apply=False):
    feed = json.load(open(feed_path, encoding="utf-8"))
    schools = [f for f in json.load(open(local_path, encoding="utf-8"))["features"]
               if f["layer"] in ("district", "school-es", "school-ms", "school-hs")]
    today = date.today()
    kept = [e for e in feed["events"] if not e["id"].startswith(("sch-", "comal-")) and
            not (e["id"].startswith("nbisd-") and e.get("tags") != ["district:nbisd"])]
    found, by_key = [], defaultdict(list)

    for s in schools:
        district = next((t for t in s["tags"] if t.startswith("district:")), None)
        own = next((t for t in s["tags"] if t.startswith("school:")), district)
        for kind, getter in (("calendar", calendar_events), ("athletics", game_events)):
            if not s["info"].get(kind) or (kind == "athletics" and s["layer"] != "school-hs"):
                continue
            try:
                items = getter(s, today)
            except Exception as ex:          # one bad feed shouldn't stop the rest
                print(f"! {s['name']} {kind}: {ex}")
                continue
            print(f"  {s['id']:<22} {kind:<9} {len(items)}")
            for e in items:
                e.update(category="school", tags=[own], audience="local", _school=s["id"], _district=district)
                by_key[(district, e["title"].lower(), e["startDate"])].append(e)

    for (district, _, _), group in by_key.items():
        e = group[0]
        # The same item on three or more campus calendars (or on the district's) is district-wide.
        if len(group) >= 3 or any(g["tags"] == [district] for g in group):
            e["tags"], e["location"], e["_school"] = [district], "All campuses", district.split(":")[1]
            group = [e]
        elif len(group) == 2 and group[0].get("tag"):
            e["tags"] = sorted({t for g in group for t in g["tags"]})     # two of our schools playing each other
            group = [e]
        for g in group:
            day = g["startDate"]
            # Skip holidays a hand-entered district item already covers.
            if HOLIDAY.search(g["title"]) and any(
                    k.get("tags") == [g["_district"]] and k["startDate"] <= day <= k.get("endDate", k["startDate"])
                    for k in kept):
                continue
            g["id"] = f"sch-{g.pop('_school')}-{day}-{slug(g['title'])}"[:80]
            g.pop("_district")
            found.append(g)

    ids = set()
    found = [e for e in found if not (e["id"] in ids or ids.add(e["id"]))]
    found.sort(key=lambda e: (e["startDate"], e.get("startTime", "")))
    print(f"\n{len(found)} school event(s) through {today + timedelta(days=CALENDAR_DAYS)}; "
          f"{sum(1 for e in found if e.get('tag'))} are varsity games.")
    if apply:
        feed["events"] = sorted(kept + found, key=lambda e: (e["startDate"], e.get("startTime") or ""))
        feed["updated"] = today.isoformat()
        with open(feed_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(feed, indent=1, ensure_ascii=False) + "\n")
        print(f"Wrote {len(feed['events'])} events to {feed_path}.")
    return found


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], args[1], apply="--apply" in sys.argv)
