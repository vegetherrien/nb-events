"""Finds new events from local venues' public calendar feeds.

    python3 scripts/scrape.py events.json            # list what's new, change nothing
    python3 scripts/scrape.py events.json --apply    # add them to events.json

Each source below publishes an iCalendar (.ics) feed. Events already in events.json
(same day, similar title) are skipped, so it's safe to run any time.
"""
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta

UA = "Mozilla/5.0 (compatible; nb-events-feed-bot)"

# Sources per city (the feed's city.id picks the list).
# ics sources: url, category, location fallback, keep/drop regexes on titles, after (earliest start time).
# api sources (WordPress "The Events Calendar" JSON): category_map from their categories to ours.
NB_SOURCES = [
    # Gruene Hall plays free daytime sets every day (covered by a weekly note), so only evening shows.
    dict(name="Gruene Hall", url="https://gruenehall.com/events/?ical=1", category="music",
         location="Gruene Hall, 1281 Gruene Rd", after="17:00",
         drop=r"\b(closed|private|rehearsal|dance lessons?)\b"),
    dict(name="Brauntex Theatre", url="https://brauntex.org/events/?ical=1", category="music",
         location="Brauntex Theatre, 290 W San Antonio St", drop=r"\b(subscription|gift card|rental)\b"),
    # Gruene's town calendar is mostly patio music at local restaurants; evenings only.
    dict(name="Gruene", url="https://www.gruenetexas.com/events/?ical=1", category="music",
         location="Gruene Historic District", after="17:00", drop=r"\b(gruene hall|dance lessons?)\b"),
    dict(name="Castell St. Market", url="https://firstprotestant.com/events/?ical=1", category="market",
         location="First Protestant Church, 296 S Seguin Ave", keep=r"\bmarket\b"),
    dict(name="Veramendi", url="https://lifeatveramendi.org/events/?ical=1", category="hood",
         location="Veramendi", drop=r"\b(closed|closure)\b"),
]

BOERNE_SOURCES = [
    dict(name="The Boerne Life", api="https://theboernelife.com/wp-json/tribe/events/v1/events",
         location="Boerne",
         # Their categories → ours, first match wins. Events in none of these are skipped.
         category_map=[("Holiday", "holiday"), ("Music", "music"), ("Shopping", "market"),
                       ("Arts and Culture", "market"), ("Outdoors", "nature"), ("Sports", "fest"),
                       ("Kids and Family", "fest"), ("Fundraiser", "fest"), ("General", "fest")],
         drop=r"library|storytime|story time|trivia|karaoke|happy hour|networking|mastermind|meeting|book ?club|"
              r"mahjong|yoga|tech (time|help)|game on|pok[eé]mon|open play|class\b|lessons?\b|run club|bingo|singo|"
              r"burger night|ladies night|crafternoon|bookworms|memory lab|drawing group|potpourri|toastmasters|"
              r"support group|workshop|webinar|cigar|wing bar|mixer|downtown diy|crafts & corks|clay earrings|"
              r"ornament painting|bangle|dis-carded|lobby stop|burger king|watercolor|volunteer|clean-?up|trailblazers|"
              r"nature makers|author alley|drop-off|dinner|singer/songwriter|live music with",
         recurring=2),
]

SOURCES = {"new-braunfels": NB_SOURCES, "boerne": BOERNE_SOURCES}

# Title words that mean the event belongs in another category.
CATEGORY_HINTS = [
    (r"market days|vendor|craft fair|antique", "market"),
    (r"christmas|holiday|santa|wassail|lighting|nutcracker|polar express|new year", "holiday"),
    (r"ladies night|shopping|sale\b|5k|fun run|festival|blood drive", "fest"),
]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def fetch_api(src, start, end):
    """All events from a The Events Calendar REST API between two dates, normalized like parse_ics()."""
    out, page = [], 1
    while True:
        url = f"{src['api']}?start_date={start}&end_date={end}&per_page=50&page={page}"
        try:
            data = json.loads(fetch(url))
        except Exception:
            break
        for e in data.get("events", []):
            sd, ed = e.get("start_date", ""), e.get("end_date", "")
            venue = e.get("venue") or {}
            loc = ", ".join(x for x in [venue.get("venue"), venue.get("address")] if x) if isinstance(venue, dict) else ""
            out.append({
                "SUMMARY": unescape_html(e.get("title", "")),
                "DTSTART": (sd[:10], None if e.get("all_day") else sd[11:16]),
                "DTEND": (ed[:10], None if e.get("all_day") else ed[11:16]),
                "LOCATION": unescape_html(loc),
                "DESCRIPTION": strip_html(e.get("description", "")),
                "URL": e.get("url"),
                "CATEGORIES": [c.get("name", "") for c in e.get("categories", [])],
            })
        if page >= int(data.get("total_pages") or 1):
            break
        page += 1
    return out


def strip_html(s):
    return unescape_html(re.sub(r"<[^>]+>", " ", s or ""))


def unescape_html(s):
    import html
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def unescape(s):
    return (s.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
             .replace("\\;", ";").replace("\\\\", "\\")).strip()


def parse_dt(prop, value):
    """Returns (date 'YYYY-MM-DD', time 'HH:MM' or None). Times are left as local (Central)."""
    v = value.strip()
    if "VALUE=DATE" in prop or re.fullmatch(r"\d{8}", v):
        return f"{v[:4]}-{v[4:6]}-{v[6:8]}", None
    m = re.match(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})", v)
    if not m:
        return None, None
    y, mo, d, h, mi = map(int, m.groups())
    dt = datetime(y, mo, d, h, mi)
    if v.endswith("Z"):  # UTC: convert to Central (CST in winter, CDT in summer, close enough by month)
        dt -= timedelta(hours=5 if 3 < mo < 11 else 6)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def parse_ics(text):
    events, cur = [], None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur: events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            prop, _, value = line.partition(":")
            key = prop.split(";")[0]
            if key in ("DTSTART", "DTEND"):
                cur[key] = parse_dt(prop, value)
            elif key in ("SUMMARY", "LOCATION", "DESCRIPTION", "URL", "UID"):
                cur[key] = unescape(value)
    return events


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def norm(title):
    t = re.sub(r"[^a-z0-9 ]", " ", title.lower())
    stop = {"the", "a", "an", "and", "with", "at", "in", "of", "live", "presents"}
    return [w for w in t.split() if w not in stop][:3]


def is_duplicate(feed_events, day, title):
    key = norm(title)
    for e in feed_events:
        if e["startDate"] <= day <= e.get("endDate", e["startDate"]):
            other = norm(e["title"])
            if key and other and (key[0] == other[0] or set(key[:2]) & set(other[:2])):
                return True
    return False


def clean_title(t):
    t = re.sub(r"\s+", " ", t).strip()
    # Some venues shout their titles; turn "THE POLAR EXPRESS EXPERIENCE" into "The Polar Express Experience".
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        small = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "with", "w"}
        words = t.lower().split(" ")
        t = " ".join(w if (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))
    t = re.sub(r"\s*[-–|]\s*(Gruene Hall|Brauntex.*)$", "", t, flags=re.I)
    return t


def tidy_location(raw, fallback):
    """'Gristmill River Restaurant & Bar, 1287 Gruene Rd, New Braunfels, TX, 78130, United States'
    becomes 'Gristmill River Restaurant & Bar, 1287 Gruene Rd'. Only trailing city/state/ZIP/country
    parts are trimmed. Vague values ('TX') use the venue's own name instead."""
    loc = (raw or "").split("\n")[0].strip()
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    tail = re.compile(r"^(new braunfels|tx|texas|united states|usa|\d{5}(-\d{4})?)$", re.I)
    while parts and tail.match(parts[-1]):
        parts.pop()
    if not parts or len(" ".join(parts)) < 6:
        return fallback
    return ", ".join(parts)[:90]


def main(path, apply=False):
    feed = json.load(open(path, encoding="utf-8"))
    today = date.today().isoformat()
    until = feed.get("rangeEnd", (date.today() + timedelta(days=90)).isoformat())
    have_ids = {e["id"] for e in feed["events"]}
    found = []
    city = (feed.get("city") or {}).get("id", "new-braunfels")

    for src in SOURCES.get(city, []):
        try:
            raw = fetch_api(src, today, until) if src.get("api") else parse_ics(fetch(src["url"]))
        except Exception as ex:  # one bad source shouldn't stop the rest
            print(f"! {src['name']}: {ex}")
            continue
        if src.get("recurring"):
            # Weekly regulars (trivia, wine-bar gigs) belong in "Every week" notes, not the calendar.
            from collections import Counter
            counts = Counter(clean_title(v.get("SUMMARY", "")).lower() for v in raw)
            raw = [v for v in raw if counts[clean_title(v.get("SUMMARY", "")).lower()] <= src["recurring"]]
        for v in raw:
            if "DTSTART" not in v or not v.get("SUMMARY"):
                continue
            day, start = v["DTSTART"]
            if not day or day < today or day > until:
                continue
            title = clean_title(v["SUMMARY"])
            if src.get("keep") and not re.search(src["keep"], title, re.I):
                continue
            if src.get("drop") and re.search(src["drop"], title, re.I):
                continue
            if src.get("after") and (start is None or start < src["after"]):
                continue
            if is_duplicate(feed["events"] + found, day, title):
                continue
            end_day, end = v.get("DTEND", (None, None))
            if src.get("category_map"):
                cats = v.get("CATEGORIES") or []
                cat = next((ours for theirs, ours in src["category_map"] if theirs in cats), None)
                if not cat:
                    continue
            else:
                cat = src["category"]
            if cat not in ("hood", "nature"):
                for pat, c in CATEGORY_HINTS:
                    if re.search(pat, title, re.I):
                        cat = c
                        break
            e = {"id": f"{day}-{slug(title)}", "category": cat, "title": title, "startDate": day}
            if start and start != "00:00":
                e["startTime"] = start
                if end and end_day == day and end > start:
                    e["endTime"] = end
            elif end_day and start is None:
                # All-day events: DTEND is exclusive in .ics, inclusive in the JSON API.
                last = end_day if src.get("api") else (date.fromisoformat(end_day) - timedelta(days=1)).isoformat()
                if last > day:
                    e["endDate"] = last
            e["location"] = tidy_location(v.get("LOCATION"), src["location"])
            desc = re.sub(r"\s+", " ", v.get("DESCRIPTION", "")).strip()
            if desc:
                e["description"] = (desc[:217] + "…") if len(desc) > 220 else desc
            e["sourceURL"] = v.get("URL") or src["url"].split("?")[0]
            e["source"] = src["name"]  # shown in the report only, removed before saving
            if e["id"] in have_ids:
                continue
            found.append(e)

    found.sort(key=lambda e: (e["startDate"], e.get("startTime", "")))
    print(f"{len(found)} new event(s) between {today} and {until}:\n")
    for e in found:
        print(f"  {e['startDate']} {e.get('startTime', '     ')}  [{e['category']:<7}] {e['title']}  ({e['source']})")

    if apply and found:
        for e in found:
            e.pop("source", None)
        feed["events"].extend(found)
        feed["events"].sort(key=lambda e: e["startDate"])
        feed["updated"] = date.today().isoformat()
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(feed, indent=1, ensure_ascii=False) + "\n")
        print(f"\nAdded {len(found)} event(s) to {path}.")
    return found


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "events.json", apply="--apply" in sys.argv)
