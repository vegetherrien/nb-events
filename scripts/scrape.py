"""Finds new events from local venues' public calendar feeds.

    python3 scripts/scrape.py events.json            # list what's new, change nothing
    python3 scripts/scrape.py events.json --apply    # add them to events.json

Each source below publishes an iCalendar (.ics) feed. Events already in events.json
(same day, similar title) are skipped, so it's safe to run any time.
"""
import json, os
import difflib
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta

UA = "Mozilla/5.0 (compatible; nb-events-feed-bot)"

# Special screenings (movie in the park, library movie parties, film nights). Regular theater
# showtimes are deliberately not collected.
MOVIE = r"\b(movies?|films?|cinema|screenings?|flicks)\b"

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
    # Visit New Braunfels (the chamber's tourism calendar): Krause's, the caverns, Renaissance Faire,
    # downtown and Gruene events. No feed, so its listing pages are read; weekly regulars are skipped.
    dict(name="Visit New Braunfels", visitnb="https://www.visitnbtx.com/upcoming-events", location="New Braunfels",
         category_map=[("Concerts & live music", "music"), ("Markets & shopping", "market"), ("Holiday", "holiday"),
                       ("Major events & festivals", "fest"), ("Community", "fest"), ("Food & drink", "fest"),
                       ("Outdoor", "fest"), ("History & heritage", "fest"), ("Arts & culture", "arts")],
         drop=r"dance lessons?|karaoke|yoga|open mic|trivia|bingo", weekly=True),
    # McKenna Children's Museum (Tockify). Daily drop-in programs are skipped; specials stay.
    dict(name="McKenna Children's Museum", url="https://tockify.com/api/feeds/ics/mckennamuseum", category="fest",
         location="McKenna Children's Museum, 801 W San Antonio St",
         drop=r"wondershop|growing minds|sale\b|members only|toy drive|admission|tasty tuesday|thankful thursday|"
              r"come play|paint & play|workshop|bingo"),
    # Circle Arts Theatre's shows, from its ticket site (runs of a few weekends each).
    dict(name="Circle Arts Theatre", ttc="https://buy.ticketstothecity.com/venue.php?org_id=37", category="arts",
         location="Circle Arts Theatre, 124 Elizabeth Ave"),
    # Public library (LibCal): hundreds of storytimes and clubs, so only screenings.
    dict(name="New Braunfels Public Library", libcal="https://nbtexas.libcal.com", category="movies",
         location="New Braunfels Public Library", keep=MOVIE),
    # City of New Braunfels calendar (all departments). Mostly meetings; keep the public events.
    # catIDs: 14 main, 74 master, 22 downtown, 65 parks & rec, 42 special events.
    *[dict(name="City of New Braunfels",
         url=f"https://www.newbraunfels.gov/common/modules/iCalendar/iCalendar.aspx?catID={c}&feed=calendar",
         category="fest", location="New Braunfels",
         drop=r"meeting|council|commission|board\b|committee|holiday:|training|workshop|hearing|session|budget|"
              r"drop[- ]?off|closed|zoning|advisory|cdbg|town hall|bond|open house|presentation|101\b|recycling|mulching|pick ?up")
      for c in (14, 74, 22, 65, 42)],
    dict(name="Veramendi", url="https://lifeatveramendi.org/events/?ical=1", category="hood",
         location="Veramendi", drop=r"\b(closed|closure)\b",
         # Resident events: only people whose home area is Veramendi see them.
         tags=["community:veramendi"], audience="local"),
]

BOERNE_NOISE = (r"library|storytime|story time|trivia|karaoke|happy hour|networking|mastermind|meeting|book ?club|"
                r"mahjong|yoga|tech (time|help)|game on|pok[eé]mon|open play|class\b|lessons?\b|run club|bingo|singo|"
                r"burger night|ladies night|crafternoon|bookworms|memory lab|drawing group|potpourri|toastmasters|"
                r"support group|workshop|webinar|cigar|wing bar|mixer|downtown diy|crafts & corks|clay earrings|"
                r"ornament painting|bangle|dis-carded|lobby stop|burger king|watercolor|volunteer|clean-?up|trailblazers|"
                r"nature makers|author alley|drop-off|dinner|singer/songwriter|live music with|name that tune|"
                r"council|commission|board\b|closed|office hours|tour\b|jazz night|brunch|tasting|sold out|case sale|spades")

BOERNE_SOURCES = [
    dict(name="Boerne Community Theatre", ttc="https://buy.ticketstothecity.com/venue.php?org_id=389", category="arts",
         location="Boerne Community Theatre, 907 E Blanco Rd"),
    # The City of Boerne's "Visit Boerne" calendar (Timely). Covers the whole town.
    dict(name="Visit Boerne", url="https://events.timely.fun/api/calendars/54730033/export?format=ics",
         category="fest", location="Boerne", drop=BOERNE_NOISE, recurring=2),
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
              r"nature makers|author alley|drop-off|dinner|singer/songwriter|live music with|closed|sold out|case sale|name that tune|spades",
         recurring=2),
]

SOURCES = {"new-braunfels": NB_SOURCES, "boerne": BOERNE_SOURCES}

# Title words that mean the event belongs in another category.
CATEGORY_HINTS = [
    (MOVIE, "movies"),
    (r"\b(the musical|a play|comedy show|stand-?up|improv|ballet(?! texas presents the nutcracker))\b", "arts"),
    (r"music in the cave|live music|concert", "music"),
    (r"market days|vendor|craft fair|antique", "market"),
    (r"christmas|holiday|santa|wassail|lighting|nutcracker|polar express|new year", "holiday"),
    (r"ladies night|shopping|sale\b|5k|fun run|festival|faire?\b|blood drive", "fest"),
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


VNB_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "research", "cache", "visitnb-places.json")


def _vnb_when(text):
    """'November 20, 2026 | 6:00pm - 9:00pm' or '... | 6:00pm - January 3, 2027 | 10:00pm'."""
    def d(x): return datetime.strptime(x.strip(), "%B %d, %Y").strftime("%Y-%m-%d")
    def t(x):
        x = x.strip().lower()
        return datetime.strptime(x, "%I:%M%p").strftime("%H:%M") if re.fullmatch(r"\d{1,2}:\d{2}[ap]m", x) else None
    parts = [p.strip() for p in re.split(r"\s+-\s+", text, maxsplit=1)]
    sd, _, st = parts[0].partition("|")
    start = (d(sd), t(st))
    if len(parts) == 2 and "|" in parts[1]:
        ed, _, et = parts[1].partition("|")
        end = (d(ed), t(et))
    else:
        end = (start[0], t(parts[1]) if len(parts) == 2 else None)
    return start, end


def fetch_visitnb(src):
    """Visit New Braunfels listing pages (12 a page; the site starts repeating past the end). The venue
    comes from each event's own page, cached so the daily run only opens new ones."""
    import html as H
    try:
        cache = json.load(open(VNB_CACHE))
    except Exception:
        cache = {}
    out, seen = [], set()
    for page in range(1, 40):
        text = fetch(src["visitnb"] + ("" if page == 1 else f"/p{page}"))
        cards = re.findall(r"<article.*?</article>", text, re.S)
        fresh = 0
        for a in cards:
            url = re.search(r'href="(https://www\.visitnbtx\.com/events/[^"]+)"', a)
            title = re.search(r"<h1[^>]*>(.*?)</h1>", a, re.S)
            when = re.search(r'text-body-sm[^>]*>([^<]*)<', a)
            if not (url and title and when):
                continue
            key = (url[1], when[1].strip())
            if key in seen:
                continue
            seen.add(key); fresh += 1
            cat = re.search(r"rounded-full[^>]*>([^<]*)<", a)
            try:
                (sd, st), (ed, et) = _vnb_when(H.unescape(when[1]))
            except ValueError:
                continue
            out.append({"SUMMARY": unescape_html(re.sub(r"<[^>]+>", "", title[1])), "DTSTART": (sd, st),
                        "DTEND": (ed, et), "URL": url[1],
                        "CATEGORIES": [H.unescape(cat[1]).strip()] if cat else []})
        if not fresh:
            break
    if src.get("weekly"):
        out = drop_weekly(out)
    for v in out:
        u = v["URL"]
        if u not in cache:
            try:
                page = fetch(u)
                flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
                m = re.search(r"Visit Website Location (.+?) Contact us", flat) or re.search(r" Location (.+?) Contact us", flat)
                cache[u] = unescape_html(m[1]) if m else ""
            except Exception:
                cache[u] = ""
        # "Krause's Cafe & Biergarten 148 S Castell Ave New Braunfels, TX 78130" -> name, street
        loc = re.sub(r"\s*New Braunfels,? TX\s*\d{5}.*$", "", cache[u]).strip()
        loc = re.sub(r"^(.*?)\s+(\d+ .*)$", r"\1, \2", loc)
        v["LOCATION"] = loc
    os.makedirs(os.path.dirname(VNB_CACHE), exist_ok=True)
    json.dump(cache, open(VNB_CACHE, "w"), indent=0, ensure_ascii=False)
    return out


def drop_weekly(raw):
    """Series that repeat every week or so: a months-long regular (dance lessons, the Friday Afternoon
    Club, a weekly market) is dropped; a short run (Christmas at the Caverns, a Renaissance Faire's
    weekends) becomes one multi-day event. Monthly ones like First Friday are left alone."""
    from collections import defaultdict
    groups = defaultdict(list)
    for v in raw:
        groups[clean_title(v.get("SUMMARY", "")).lower()].append(v)
    out = []
    for t, vs in groups.items():
        ds = sorted({v["DTSTART"][0] for v in vs})
        gaps = sorted((date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in zip(ds, ds[1:]))
        if len(ds) < 3 or gaps[len(gaps) // 2] > 8 or re.search(MOVIE, t, re.I):
            out.extend(vs)
            continue
        if (date.fromisoformat(ds[-1]) - date.fromisoformat(ds[0])).days > 45:
            continue                                   # a standing weekly regular
        first = min(vs, key=lambda v: v["DTSTART"])
        last = max(vs, key=lambda v: v["DTSTART"])
        merged = dict(first)
        merged["DTSTART"] = (ds[0], None)
        merged["DTEND"] = (ds[-1], None)
        merged["MULTI"] = True                         # inclusive end date, like the JSON API
        times = {(v["DTSTART"][1], v["DTEND"][1]) for v in vs}
        if len(times) == 1 and first["DTSTART"][1]:
            hours = _ampm(first["DTSTART"][1]) + (" to " + _ampm(first["DTEND"][1]) if first["DTEND"][1] else "")
        else:
            hours = "times vary"
        merged["DESCRIPTION"] = f"Select dates {DayLabel(ds[0])} to {DayLabel(ds[-1])}, {hours}. Check the source for each day."
        out.append(merged)
    return out


def DayLabel(d):
    return datetime.strptime(d, "%Y-%m-%d").strftime("%b %-d")


def _ampm(t):
    return datetime.strptime(t, "%H:%M").strftime("%-I:%M %p").replace(":00", "")


def fetch_ttc(src):
    """A TicketstotheCity venue page: each show is its title then '(November 20, 2026 - November 22, 2026)'."""
    text = fetch(src["ttc"])
    parts = [unescape_html(x) for x in re.split(r"<[^>]+>", text)]
    parts = [x for x in parts if x]
    out = []
    for i, x in enumerate(parts):
        m = re.fullmatch(r"\((\w+ \d{1,2}, \d{4})(?: - (\w+ \d{1,2}, \d{4}))?\)", x)
        if not m or i == 0:
            continue
        d = lambda y: datetime.strptime(y, "%B %d, %Y").strftime("%Y-%m-%d")
        out.append({"SUMMARY": parts[i - 1], "DTSTART": (d(m[1]), None), "DTEND": (d(m[2] or m[1]), None),
                    "MULTI": True, "LOCATION": "", "URL": src["ttc"],
                    "DESCRIPTION": "Several performances; pick a date and time on the ticket page."})
    return out


def fetch_libcal(src):
    """Upcoming events from a LibCal site's public list, normalized like parse_ics()."""
    out = []
    for page in range(1, 8):
        data = json.loads(fetch(f"{src['libcal']}/ajax/calendar/list?c=-1&date=0000-00-00&perpage=100&page={page}"))
        for e in data.get("results", []):
            sd, ed = e.get("startdt", ""), e.get("enddt", "")
            allday = bool(e.get("all_day"))
            out.append({
                "SUMMARY": unescape_html(e.get("title", "")),
                "DTSTART": (sd[:10], None if allday else sd[11:16]),
                "DTEND": (ed[:10], None if allday else ed[11:16]),
                "LOCATION": unescape_html(f"{src['location']}, {e['location']}" if e.get("location") else ""),
                "DESCRIPTION": strip_html(e.get("shortdesc") or e.get("description") or ""),
                "URL": e.get("url"),
            })
        if page * 100 >= int(data.get("total_results") or 0):
            break
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


def fetch_ics(url, start, end):
    """An .ics feed. The Events Calendar's plain feed stops after 30 items, so for those sites
    each month up to the end date is read too."""
    events, seen = [], set()
    urls = [url]
    if "/events/?ical=1" in url:
        y, m = int(start[:4]), int(start[5:7])
        while f"{y}-{m:02d}" <= end[:7]:
            urls.append(url.replace("/events/?ical=1", f"/events/month/{y}-{m:02d}/?ical=1"))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for i, u in enumerate(urls):
        try:
            batch = parse_ics(fetch(u))
        except Exception:
            if i == 0: raise
            continue          # a month page that doesn't exist is fine
        for v in batch:
            key = (v.get("UID"), v.get("DTSTART"), v.get("SUMMARY"))
            if key not in seen:
                seen.add(key); events.append(v)
    return events


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def norm(title):
    t = re.sub(r"[^a-z0-9 ]", " ", title.lower())
    stop = {"the", "a", "an", "and", "with", "at", "in", "of", "live", "presents",
            # too common in local titles to mean two events are the same
            "new", "braunfels", "boerne", "gruene", "annual", "christmas", "holiday", "holidays", "downtown", "nb"}
    return [w for w in t.split() if w not in stop and not re.fullmatch(r"\d+(st|nd|rd|th)?", w)][:3]


def is_duplicate(feed_events, day, title):
    key = norm(title)
    for e in feed_events:
        if e["startDate"] <= day <= e.get("endDate", e["startDate"]):
            other = norm(e["title"])
            if not (key and other):
                continue
            if re.search(MOVIE, title, re.I):
                # Screenings often share a holiday word with other events that day; need two words in common.
                if len(set(key) & set(other)) >= 2:
                    return True
            elif same_word(key[0], other[0]) or any(same_word(a, b) for a in key[:2] for b in other[:2]):
                return True
    return False


def same_word(a, b):
    """Equal, or a long word spelled slightly differently (Christkindlmarkt / Christkindlemarkt)."""
    return a == b or (min(len(a), len(b)) >= 7 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.85)


def clean_title(t):
    t = re.sub(r"\s+", " ", t).strip()
    # Some venues shout their titles; turn "THE POLAR EXPRESS EXPERIENCE" into "The Polar Express Experience".
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        small = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "with", "w"}
        words = t.lower().split(" ")
        t = " ".join(w if (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))
    t = re.sub(r"\s*[-–|]\s*(Gruene Hall|Brauntex.*)$", "", t, flags=re.I)
    # Venue prefixes ("Gruene Hall Presents: X", "Brauntex Theatre: X", "Holidays in Gruene: X").
    t = re.sub(r"^(the )?(brauntex( performing arts)? theatre( presents)?|gruene hall presents|holidays in gruene)\s*:\s*",
               "", t, flags=re.I)
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
            raw = (fetch_api(src, today, until) if src.get("api") else fetch_libcal(src) if src.get("libcal")
                   else fetch_visitnb(src) if src.get("visitnb") else fetch_ttc(src) if src.get("ttc")
                   else fetch_ics(src["url"], today, until))
        except Exception as ex:  # one bad source shouldn't stop the rest
            print(f"! {src['name']}: {ex}")
            continue
        if src.get("recurring"):
            # Weekly regulars (trivia, wine-bar gigs) belong in "Every week" notes, not the calendar.
            from collections import Counter
            counts = Counter(clean_title(v.get("SUMMARY", "")).lower() for v in raw)
            raw = [v for v in raw if counts[clean_title(v.get("SUMMARY", "")).lower()] <= src["recurring"]
                   or re.search(MOVIE, v.get("SUMMARY", ""), re.I)]
        for v in raw:
            if "DTSTART" not in v or not v.get("SUMMARY"):
                continue
            day, start = v["DTSTART"]
            if not day or day < today or day > until:
                continue
            title = clean_title(v["SUMMARY"])
            if src.get("keep") and not re.search(src["keep"], title, re.I):
                continue
            if src.get("drop") and re.search(src["drop"], title, re.I) and not re.search(MOVIE, title, re.I):
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
                last = end_day if (src.get("api") or v.get("MULTI")) else (date.fromisoformat(end_day) - timedelta(days=1)).isoformat()
                if last > day:
                    e["endDate"] = last
            e["location"] = tidy_location(v.get("LOCATION"), src["location"])
            desc = re.sub(r"\s+", " ", v.get("DESCRIPTION", "")).strip()
            if desc:
                e["description"] = (desc[:217] + "…") if len(desc) > 220 else desc
            e["sourceURL"] = v.get("URL") or (src.get("url") or src.get("libcal") or src.get("visitnb") or src.get("ttc") or src.get("api", "")).split("?")[0]
            if src.get("tags"): e["tags"] = list(src["tags"])
            if src.get("audience"): e["audience"] = src["audience"]
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
