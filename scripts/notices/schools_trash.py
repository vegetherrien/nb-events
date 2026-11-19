#!/usr/bin/env python3
"""School closures/delays, planned no-school days, and holiday trash changes.

collect(city_id) -> list of notices (see README.md in this folder).

Topics
  1. Unplanned school closures, delays, early dismissals (kind "school", level "alert")
     NBISD      WordPress REST posts + home page banner scan
     Comal ISD  Edlio news RSS + home page banner scan
     Boerne ISD ParentSquare Smart Site popup-alerts API + home page news cards
  2. Planned student holidays / early release in the next 7 days (kind "school", level "info"),
     read only from feed/events.json and feed/boerne-events.json (no network).
  3. Holiday trash/recycling changes (kind "trash"). NB: parsed live from the city's holiday
     page, with a hand-read table as fallback. Boerne: no published rule; city alert/news RSS
     is scanned for a holiday-collection item.

Notes: research/notices/schools_trash.md
Standard library only. Every source is wrapped so one failure never breaks the others.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime

UA = "nb-events-feed-bot/1.0"
TIMEOUT = 20
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FEED = os.path.join(ROOT, "feed")


def _today():
    # NOTICES_TODAY=YYYY-MM-DD lets tests replay a past or future day.
    v = os.environ.get("NOTICES_TODAY")
    return dt.date.fromisoformat(v) if v else dt.date.today()


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace")


def _text(s):
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s or "")
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def _clip(s, n):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip(" ,;:.") + "…"


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50]


def _fmt(d):
    return d.strftime("%b ") + str(d.day)


def _log(msg):
    print(f"[schools_trash] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 1. Unplanned closures / delays
# ---------------------------------------------------------------------------
# A title must be ABOUT a closure/delay/early dismissal/cancellation...
CLOSURE_RE = re.compile(
    r"\b(clos(ed|ure|ures|ing|es)|delay(ed|s)?|late start|delayed start|early (release|dismissal)|"
    r"dismiss(ed|al|ing) early|release early|cancel(l?ed|l?ing|lations?|s)?|"
    r"no school|remote learning day|virtual learning day|school(s)? (are )?closed)\b",
    re.I,
)
# ...or a weather/emergency headline whose opening text says schools are closed/delayed.
WEATHER_RE = re.compile(
    r"\b(weather|winter storm|ice|icy|freez\w*|snow\w*|sleet|flood\w*|power outage|storm|"
    r"hail|tornado|emergency|boil water|water outage|wildfire|smoke)\b",
    re.I,
)
BODY_CLOSED_RE = re.compile(
    r"\b(schools?|campuses|classes|district|offices)\b[^.]{0,80}\b(will be|are|is|remain)\s+"
    r"(closed|cancel+ed|delayed|dismiss\w* early|releas\w* early)\b|"
    r"\b(two|2|one|1|three|3)[- ]hour (delay|late start|delayed start)\b|"
    r"\b(delayed start|late start|early dismissal|closed (today|tomorrow|on))\b",
    re.I,
)
BODY_OPEN_RE = re.compile(r"\b(open as (planned|scheduled|normal)|normal schedule|regular schedule)\b", re.I)
# ...and must not be about something else that happens to use those words.
EXCLUDE_RE = re.compile(
    r"\b(break|holidays?|spring break|summer|july|thanksgiving|christmas|winter break|"
    r"playoffs?|games?|match(es)?|tournament|meet|staar|results|meals?|road|traffic|lanes?|"
    r"streets?|parking|detour|board|briefs|art show|concert|registration|enrollment|"
    r"opening of schools|construction|bond|early release days|during closure|"
    r"mlk|martin luther king|labor day|memorial day|presidents'? day|veterans day|good friday|easter)\b",
    re.I,
)

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _dates_in(text, ref):
    """Dates mentioned as 'Jan. 15', 'January 15', '1/15', '1-15-24'. Year inferred near ref."""
    out = []
    for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", text, re.I):
        out.append((_MONTHS[m.group(1)[:3].lower()], int(m.group(2)), None))
    for m in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text):
        y = m.group(3)
        out.append((int(m.group(1)), int(m.group(2)), (int(y) + 2000 if y and len(y) == 2 else int(y)) if y else None))
    res = []
    for mo, d, y in out:
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        try:
            cands = [dt.date(y, mo, d)] if y else [dt.date(ref.year + k, mo, d) for k in (-1, 0, 1)]
        except ValueError:
            continue
        res.append(min(cands, key=lambda c: abs((c - ref).days)))
    return res


_PLANNED_CACHE = {}


def _planned_off_days(city_id):
    """{district: set(dates)} of calendar no-school / early-release days from the event feed."""
    if city_id not in _PLANNED_CACHE:
        res = {}
        try:
            for e in _load_events(city_id):
                t = e.get("title") or ""
                if e.get("category") != "school" or not (NO_SCHOOL_RE.search(t) or EARLY_RE.search(t)):
                    continue
                for tag in e.get("tags") or []:
                    if tag.startswith("district:"):
                        res.setdefault(tag.split(":", 1)[1], set()).update(_days(e))
        except Exception as e:  # noqa: BLE001
            _log(f"planned days: {e}")
        _PLANNED_CACHE[city_id] = res
    return _PLANNED_CACHE[city_id]


def is_closure(title, body=""):
    """The closure-detection rule (title-first). Returns True when an item announces a closure/delay."""
    t = title or ""
    if EXCLUDE_RE.search(t):
        return False
    if CLOSURE_RE.search(t):
        return True
    if WEATHER_RE.search(t):
        lead = (body or "")[:700]
        return bool(BODY_CLOSED_RE.search(lead)) and not BODY_OPEN_RE.search(lead)
    return False


def closure_notice(city_id, district, source, title, body, link, published, today):
    """Apply recency + date rules; return a notice dict or None."""
    if published is None or (today - published).days > 2 or published > today + dt.timedelta(days=1):
        return None
    if not is_closure(title, body):
        return None
    dates = _dates_in(title + " " + (body or "")[:300], published)
    if dates and max(dates) < today:
        return None  # announcement is about a day already past
    planned = _planned_off_days(city_id).get(district, set())
    if dates and all(d in planned or d.weekday() >= 5 for d in dates):
        return None  # only names days already on the district calendar as holidays
    until = max(dates) if dates else max(today, published + dt.timedelta(days=1))
    detail = _clip(_text(body) or title, 220)
    return {
        "id": f"{city_id}-school-{district}-closure-{_slug(title)}",
        "kind": "school",
        "title": _clip(_text(title), 60),
        "detail": detail,
        "level": "alert",
        "url": link,
        "source": source,
        "asOf": today.isoformat(),
        "until": until.isoformat(),
        "tags": [f"district:{district}"],
        "audience": "local",
    }


BANNER_RE = re.compile(
    r'(?is)<(div|section|aside|p)\b[^>]*(?:class|id)="[^"]*(emergency|alert-banner|site-alert|alert-bar|'
    r'notification-bar|alertbar|urgent|closure-banner)[^"]*"[^>]*>(.*?)</\1>'
)


def _banner_items(page_html):
    """Text of any emergency/alert banner element on a home page (none on a normal day)."""
    out = []
    for m in BANNER_RE.finditer(page_html):
        txt = _text(m.group(3))
        if 10 < len(txt) < 600:
            out.append(txt)
    return out


def _closures_nbisd(city_id, today):
    out = []
    src = "New Braunfels ISD"
    after = (today - dt.timedelta(days=3)).isoformat() + "T00:00:00"
    url = ("https://nbisd.org/wp-json/wp/v2/posts?per_page=20&_fields=date,title,link,content&after="
           + urllib.parse.quote(after))
    try:
        for p in json.loads(_get(url)):
            pub = dt.date.fromisoformat(p["date"][:10])
            n = closure_notice(city_id, "nbisd", src, html.unescape(p["title"]["rendered"]),
                               _text(p["content"]["rendered"]), p["link"], pub, today)
            if n:
                out.append(n)
    except Exception as e:  # noqa: BLE001
        _log(f"nbisd posts: {e}")
    try:
        for txt in _banner_items(_get("https://nbisd.org/")):
            n = closure_notice(city_id, "nbisd", src, txt[:120], txt, "https://nbisd.org/", today, today)
            if n:
                out.append(n)
    except Exception as e:  # noqa: BLE001
        _log(f"nbisd home: {e}")
    return out


def _rss_items(xml_text, limit=40):
    for it in re.findall(r"(?s)<item>(.*?)</item>", xml_text)[:limit]:
        def tag(name):
            m = re.search(rf"(?s)<{name}>(.*?)</{name}>", it)
            return html.unescape(m.group(1).replace("<![CDATA[", "").replace("]]>", "")).strip() if m else ""
        pub = None
        try:
            pub = parsedate_to_datetime(tag("pubDate")).date()
        except Exception:  # noqa: BLE001
            pass
        yield tag("title"), _text(tag("description")), tag("link"), pub


def _closures_comal(city_id, today):
    out = []
    src = "Comal ISD"
    try:
        for title, desc, link, pub in _rss_items(_get("https://www.comalisd.org/apps/news/rss"), 30):
            n = closure_notice(city_id, "comal-isd", src, title, desc, link.replace("http://", "https://"), pub, today)
            if n:
                out.append(n)
    except Exception as e:  # noqa: BLE001
        _log(f"comal rss: {e}")
    try:
        for txt in _banner_items(_get("https://www.comalisd.org/")):
            n = closure_notice(city_id, "comal-isd", src, txt[:120], txt, "https://www.comalisd.org/", today, today)
            if n:
                out.append(n)
    except Exception as e:  # noqa: BLE001
        _log(f"comal home: {e}")
    return out


def _since_to_days(s):
    """ParentSquare 'timeSince' strings like '3 hours ago', '2 days ago'. Unknown -> 0."""
    m = re.search(r"(\d+)\s*(minute|hour|day|week|month|year)", s or "", re.I)
    if not m:
        return 0
    n, u = int(m.group(1)), m.group(2).lower()
    return {"minute": 0, "hour": n // 24, "day": n, "week": 7 * n, "month": 30 * n, "year": 365 * n}[u]


def _closures_boerne(city_id, today):
    out = []
    src = "Boerne ISD"
    base = "https://www.boerneisd.net"
    try:
        data = json.loads(_get(base + "/api/popup-alerts"))
        for a in data.get("alerts") or []:
            pub = today - dt.timedelta(days=_since_to_days(a.get("timeSince")))
            link = a.get("link") or base + "/"
            if link.startswith("/"):
                link = base + link
            n = closure_notice(city_id, "boerne-isd", src, a.get("title") or "", _text(a.get("message")),
                               link, pub, today)
            if n:
                out.append(n)
    except Exception as e:  # noqa: BLE001
        _log(f"boerne popup-alerts: {e}")
    try:
        page = _get(base + "/")
        for m in re.finditer(
                r'(?s)stack-news-grid-date[^>]*>\s*([A-Za-z]+ \d{1,2}, \d{4})\s*</p>.*?'
                r'<a class="ss-post-feed-link" href="([^"]+)">(.*?)</a>.*?'
                r'(?:news-article-description[^>]*>(.*?)</p>)?', page):
            try:
                pub = dt.datetime.strptime(m.group(1), "%B %d, %Y").date()
            except ValueError:
                continue
            link = html.unescape(m.group(2))
            if link.startswith("/"):
                link = base + link
            n = closure_notice(city_id, "boerne-isd", src, _text(m.group(3)), _text(m.group(4) or ""),
                               link, pub, today)
            if n:
                out.append(n)
    except Exception as e:  # noqa: BLE001
        _log(f"boerne home: {e}")
    return out


# ---------------------------------------------------------------------------
# 2. Planned no-school / early release from the app's own event feed
# ---------------------------------------------------------------------------
NO_SCHOOL_RE = re.compile(
    r"\b(no school|student holiday|students? (&|and) staff holiday|student/staff holiday|"
    r"thanksgiving break|winter break|holiday break|spring break|fall break)\b", re.I)
EARLY_RE = re.compile(r"\bearly release\b", re.I)
NOT_PLANNED_RE = re.compile(r"\b(return|begins?|ends?)\b", re.I)

DISTRICT_NAMES = {"nbisd": "NBISD", "comal-isd": "Comal ISD", "boerne-isd": "Boerne ISD"}
EVENT_FILES = {"new-braunfels": "events.json", "boerne": "boerne-events.json"}


def _load_events(city_id):
    with open(os.path.join(FEED, EVENT_FILES[city_id]), encoding="utf-8") as f:
        d = json.load(f)
    return d.get("events", []) if isinstance(d, dict) else d


def _days(e):
    s = dt.date.fromisoformat(e["startDate"][:10])
    en = dt.date.fromisoformat((e.get("endDate") or e["startDate"])[:10])
    return [s + dt.timedelta(days=i) for i in range((en - s).days + 1)]


def _runs(dates):
    """Group school days off into runs; weekends between two off days don't break a run."""
    runs = []
    for d in sorted(set(x for x in dates if x.weekday() < 5)):
        if runs:
            gap = [runs[-1][-1] + dt.timedelta(days=i) for i in range(1, (d - runs[-1][-1]).days)]
            if all(g.weekday() >= 5 for g in gap):
                runs[-1].append(d)
                continue
        runs.append([d])
    return runs


def _range_str(a, b):
    if a == b:
        return _fmt(a)
    if a.month == b.month:
        return f"{_fmt(a)} to {b.day}"
    return f"{_fmt(a)} to {_fmt(b)}"


def _planned(city_id, today):
    out = []
    window_end = today + dt.timedelta(days=7)
    try:
        events = _load_events(city_id)
    except Exception as e:  # noqa: BLE001
        _log(f"events file: {e}")
        return out
    # key = tag ("district:x" or "school:y"); value = {"off": {date: event}, "early": {date: event}}
    by_tag = {}
    for e in events:
        if e.get("category") != "school" or not e.get("startDate"):
            continue
        t = e.get("title") or ""
        if NOT_PLANNED_RE.search(t) and not NO_SCHOOL_RE.search(t):
            continue
        kind = "off" if NO_SCHOOL_RE.search(t) else "early" if EARLY_RE.search(t) else None
        if not kind:
            continue
        try:
            days = [d for d in _days(e) if today <= d <= today + dt.timedelta(days=30)]
        except ValueError:
            continue
        for tag in e.get("tags") or []:
            if tag.startswith(("district:", "school:")):
                slot = by_tag.setdefault(tag, {"off": {}, "early": {}})
                for d in days:
                    slot[kind].setdefault(d, []).append(e)
    district_off = {}
    for tag, slot in sorted(by_tag.items()):
        is_district = tag.startswith("district:")
        key = tag.split(":", 1)[1]
        off_runs = [r for r in _runs(slot["off"]) if r[0] <= window_end and r[-1] >= today]
        early = sorted(d for d in slot["early"] if today <= d <= window_end
                       and not any(d in r for r in off_runs))
        if not is_district:
            # campus-only days: skip anything the district already covers
            covered = district_off.get("any", set()) | district_off.get("early", set())
            off_runs = [r for r in off_runs if not set(r) <= covered]
            early = [d for d in early if d not in covered]
        if not off_runs and not early:
            continue
        if is_district:
            district_off.setdefault("any", set()).update(d for r in off_runs for d in r)
            district_off.setdefault("early", set()).update(early)
        name = DISTRICT_NAMES.get(key) or key.replace("-", " ").title().replace(" Es", " ES").replace(" Ms", " MS").replace(" Hs", " HS")
        parts, first_ev = [], None
        for r in off_runs:
            parts.append(f"no school {_range_str(r[0], r[-1])}")
            first_ev = first_ev or slot["off"][r[0]][0]
        for d in early:
            parts.append(f"early release {d.strftime('%a')} {_fmt(d)}")
            first_ev = first_ev or slot["early"][d][0]
        headline = parts[0][0].upper() + parts[0][1:]
        title = _clip(f"{headline} ({name})", 60)
        names = []
        for ev in [x for r in off_runs for d in r for x in slot["off"][d]] + [x for d in early for x in slot["early"][d]]:
            if ev.get("title") and ev["title"] not in names:
                names.append(ev["title"])
        detail = _clip(f"{name} calendar: " + "; ".join(parts) + ". (" + "; ".join(n for n in names if n) + ")", 220)
        last = max([r[-1] for r in off_runs] + early)
        first = min([r[0] for r in off_runs] + early)
        out.append({
            "id": f"{city_id}-school-{_slug(key)}-planned-{first.isoformat()}",
            "kind": "school",
            "title": title,
            "detail": detail,
            "level": "info",
            "url": first_ev.get("sourceURL") or "",
            "source": name if is_district else f"{name} campus calendar",
            "asOf": today.isoformat(),
            "until": last.isoformat(),
            "tags": [tag],
            "audience": "local",
        })
    return out


# ---------------------------------------------------------------------------
# 3. Holiday trash / recycling
# ---------------------------------------------------------------------------
NB_HOLIDAY_URL = "https://www.newbraunfels.gov/3974/Holiday-Trash-Recycling"
BOERNE_TRASH_URL = "https://www.ci.boerne.tx.us/1284/Garbage-and-Recycling"

# Hand-read fallback, used only when the live page can't be fetched or parsed.
# Read 2026-11-18 from NB_HOLIDAY_URL ("Residential Collection" section).
# day -> shifted day (None = no change). Boerne: no specific rule published (see notes).
TRASH_LEAD_DAYS = 8
NB_TABLE_READ = "2026-11-18"
NB_TABLE = [
    {"holiday": "Thanksgiving", "day": "2026-11-26", "moveTo": "2026-11-27"},
    {"holiday": "Christmas", "day": "2026-12-25", "moveTo": None},
    {"holiday": "New Year's Day", "day": "2027-01-01", "moveTo": None},
]

_NB_SHIFT_RE = re.compile(
    r"(Thanksgiving|Christmas|New Year'?s Day|New Year'?s|Memorial Day|Independence Day|July 4th|Labor Day|"
    r"Veterans Day|MLK Day|Martin Luther King[^:]{0,15}|Presidents'? Day|Good Friday|Juneteenth)\s*(\d{4})?:\s*"
    r"If your Garbage (?:&|and) Recycling day falls on [^(]*\((\w+),\s*([A-Za-z]+)\.?\s*(\d{1,2})\),\s*"
    r"then your collection day will move to (\w+),\s*([A-Za-z]+)\.?\s*(\d{1,2})", re.I)
_NB_NOCHANGE_RE = re.compile(
    r"(Thanksgiving|Christmas|New Year'?s Day|Memorial Day|Independence Day|Labor Day)\s*(\d{4})?:\s*"
    r"There will be no changes[^(]*\((\w+),\s*([A-Za-z]+)\.?\s*(\d{1,2})\)", re.I)


def _mkdate(mon, day, year_hint, today):
    mo = _MONTHS[mon[:3].lower()]
    y = int(year_hint) if year_hint else today.year
    d = dt.date(y, mo, int(day))
    if not year_hint and d < today - dt.timedelta(days=180):
        d = dt.date(y + 1, mo, int(day))
    return d


def nb_rules_from_page(page_html, today):
    text = _text(page_html)
    res = text.split("Commercial Collection")[0]  # residential rules come first
    rules = []
    for m in _NB_SHIFT_RE.finditer(res):
        rules.append({"holiday": m.group(1), "day": _mkdate(m.group(4), m.group(5), m.group(2), today),
                      "moveTo": _mkdate(m.group(7), m.group(8), m.group(2), today)})
    for m in _NB_NOCHANGE_RE.finditer(res):
        rules.append({"holiday": m.group(1), "day": _mkdate(m.group(4), m.group(5), m.group(2), today),
                      "moveTo": None})
    return rules


def _nb_zones():
    with open(os.path.join(FEED, "local.json"), encoding="utf-8") as f:
        return [x for x in json.load(f)["features"] if x.get("layer") == "trash"]


def _week_letter(day, week_a_start):
    mon = day - dt.timedelta(days=day.weekday())
    return "A" if ((mon - week_a_start).days // 7) % 2 == 0 else "B"


def _nb_affected_tags(day):
    """Trash tags whose garbage day or (this week's) recycling day is `day`."""
    wd = day.strftime("%A")
    tags = set()
    for z in _nb_zones():
        info = z.get("info") or {}
        if info.get("weekday") == wd:
            tags.update(z.get("tags") or [])
        if info.get("recycleWeekday") == wd:
            try:
                a = dt.date.fromisoformat(info.get("weekAStart"))
            except (TypeError, ValueError):
                a = None
            # The A/B calendar is reissued each January; outside its year, include both weeks.
            if a is None or a.year != day.year or _week_letter(day, a) == info.get("recycleWeek"):
                tags.update(z.get("tags") or [])
    return sorted(tags, key=lambda t: (len(t), t))


def _trash_nb(city_id, today):
    rules, as_of = None, today
    try:
        rules = nb_rules_from_page(_get(NB_HOLIDAY_URL), today)
        if not rules:
            _log("nb holiday page: no rules matched; using table")
    except Exception as e:  # noqa: BLE001
        _log(f"nb holiday page: {e}; using table")
    if not rules:
        as_of = dt.date.fromisoformat(NB_TABLE_READ)
        rules = [{"holiday": r["holiday"], "day": dt.date.fromisoformat(r["day"]),
                  "moveTo": dt.date.fromisoformat(r["moveTo"]) if r["moveTo"] else None} for r in NB_TABLE]
    out = []
    for r in rules:
        day, to = r["day"], r["moveTo"]
        # Shown from 8 days ahead (so it is up before the previous week's same-day pickup) to the moved day.
        if not to or not (day - dt.timedelta(days=TRASH_LEAD_DAYS) <= today <= to):
            continue
        hol = r["holiday"].replace("New Years", "New Year's")
        wd, wt = day.strftime("%A"), to.strftime("%A")
        try:
            tags = _nb_affected_tags(day)
        except Exception as e:  # noqa: BLE001
            _log(f"nb zones: {e}")
            tags = []
        n = {
            "id": f"{city_id}-trash-{_slug(hol)}-{day.isoformat()}",
            "kind": "trash",
            "title": _clip(f"{hol}: {wd} pickup moves to {wt}", 60),
            "detail": _clip(f"If your garbage & recycling day is {wd}, {_fmt(day)}, it moves to {wt}, {_fmt(to)}. "
                            "Carts at the curb by 6 a.m.; other days are unchanged.", 220),
            "level": "info",
            "url": NB_HOLIDAY_URL,
            "source": "City of New Braunfels Solid Waste",
            "asOf": as_of.isoformat(),
            "until": to.isoformat(),
        }
        if tags:
            n["tags"] = tags
            n["audience"] = "local"
        out.append(n)
    return out


TRASH_WORDS = re.compile(r"\b(trash|garbage|recycl\w*|solid waste|collection|pickup|pick-up|WM)\b", re.I)
CHANGE_WORDS = re.compile(r"\b(holiday|thanksgiving|christmas|new year|delay\w*|shift\w*|no collection|"
                          r"suspend\w*|cancel\w*|resum\w*|schedule change|weather)\b", re.I)


def _trash_city_rss(city_id, today, base, source, feeds):
    """Holiday/weather collection-change items posted to a CivicPlus city's alert or news RSS."""
    out = []
    for path in feeds:
        try:
            for title, desc, link, pub in _rss_items(_get(base + path), 40):
                if pub is None or (today - pub).days > 10 or not title:
                    continue
                if not (TRASH_WORDS.search(title) and CHANGE_WORDS.search(title)):
                    continue
                out.append({
                    "id": f"{city_id}-trash-news-{_slug(title)}",
                    "kind": "trash",
                    "title": _clip(title, 60),
                    "detail": _clip(desc or title, 220),
                    "level": "info",
                    "url": link or base,
                    "source": source,
                    "asOf": today.isoformat(),
                    "until": (pub + dt.timedelta(days=14)).isoformat(),
                })
        except Exception as e:  # noqa: BLE001
            _log(f"{base}{path}: {e}")
    return out


CIVIC_FEEDS = ["/RSSFeed.aspx?ModID=63&CID=All-0", "/RSSFeed.aspx?ModID=1&CID=All-newsflash.xml"]


# ---------------------------------------------------------------------------
def collect(city_id):
    today = _today()
    out = []
    steps = []
    if city_id == "new-braunfels":
        steps = [
            lambda: _closures_nbisd(city_id, today),
            lambda: _closures_comal(city_id, today),
            lambda: _planned(city_id, today),
            lambda: _trash_nb(city_id, today),
            lambda: _trash_city_rss(city_id, today, "https://www.newbraunfels.gov", "City of New Braunfels", CIVIC_FEEDS),
        ]
    elif city_id == "boerne":
        steps = [
            lambda: _closures_boerne(city_id, today),
            lambda: _planned(city_id, today),
            lambda: _trash_city_rss(city_id, today, "https://www.ci.boerne.tx.us", "City of Boerne", CIVIC_FEEDS),
        ]
    for step in steps:
        try:
            out.extend(step())
        except Exception as e:  # noqa: BLE001
            _log(f"step failed: {e}")
    seen, uniq = set(), []
    for n in out:
        if n["id"] not in seen:
            seen.add(n["id"])
            uniq.append(n)
    return uniq


if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "new-braunfels"
    print(json.dumps(collect(city), indent=2, ensure_ascii=False))
