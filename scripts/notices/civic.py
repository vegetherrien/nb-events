#!/usr/bin/env python3
"""Civic notices for the "Good to know" card: road closures, council/board meetings,
shelter pets, high school scores and business openings.

Contract: scripts/notices/README.md. Notes on every source: research/notices/civic.md.
Standard library only. Every source is wrapped so one failure never breaks the others.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

try:
    from zoneinfo import ZoneInfo
    CENTRAL = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    CENTRAL = None

UA = "nb-events-feed-bot/1.0"
TIMEOUT = 20
CITIES = ("new-braunfels", "boerne")


# ---------------------------------------------------------------- helpers

def today() -> dt.date:
    return dt.datetime.now(CENTRAL).date() if CENTRAL else dt.date.today()


def fetch(url: str, data: dict | None = None) -> str:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def strip_tags(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= n:
        return s
    cut = s[: n - 1].rsplit(" ", 1)[0].rstrip(",;:-– ")
    return cut + "…"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def fmt_day(d: dt.date) -> str:
    return f"{d.strftime('%a %b')} {d.day}"


def notice(**kw) -> dict:
    kw["title"] = clip(kw["title"], 60)
    kw["detail"] = clip(kw["detail"], 220)
    kw.setdefault("asOf", today().isoformat())
    return kw


MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
MON_RE = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
DATE_RE = re.compile(MON_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s*(\d{4}))?", re.I)


def month_num(name: str) -> int:
    name = name.lower().rstrip(".")
    for full, n in MONTHS.items():
        if full.startswith(name[:3]):
            return n
    return 0


def dates_in(text: str, ref: dt.date) -> list[tuple[int, dt.date]]:
    out = []
    for m in DATE_RE.finditer(text):
        mo, day = month_num(m.group(1)), int(m.group(2))
        yr = int(m.group(3)) if m.group(3) else ref.year
        try:
            d = dt.date(yr, mo, day)
        except ValueError:
            continue
        if not m.group(3) and d < ref - dt.timedelta(days=180):
            d = d.replace(year=yr + 1)
        out.append((m.start(), d))
    return out


def rss_items(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        pub = it.findtext("pubDate") or ""
        try:
            pd = dt.datetime.strptime(pub[:25].strip(), "%a, %d %b %Y %H:%M:%S").date()
        except ValueError:
            pd = None
        items.append({"title": (it.findtext("title") or "").strip(),
                      "link": (it.findtext("link") or "").strip(),
                      "desc": strip_tags(it.findtext("description") or ""),
                      "date": pd})
    return items


# ---------------------------------------------------------------- city config

CFG = {
    "new-braunfels": {
        "prefix": "nb",
        "city_name": "City of New Braunfels",
        "news_feeds": [
            "https://www.newbraunfels.gov/RSSFeed.aspx?ModID=1&CID=2026-Traffic-Alerts-39",
            "https://www.newbraunfels.gov/RSSFeed.aspx?ModID=1&CID=All-newsflash.xml",
        ],
        "txdot_counties": ["Comal", "Guadalupe"],
        # Roads that run through the city; a DriveTexas record counts as "in town" when
        # its route is in `routes`, or its limits name one of `cross`, and nothing in `exclude`.
        "routes": {"IH0035", "SL0337", "FM0306", "FM0725", "FM0758", "FM1044", "FM1101", "BU0081", "SH0046"},
        "cross": {"IH0035", "SL0337", "FM0306", "FM0725", "FM0758", "FM1044", "FM1101", "FM0482"},
        "exclude": {"US0281", "FM3009", "FM1103", "FM2252", "FM1863", "SH0123", "FM0078", "BS0123B",
                    "SH0080", "FM0464", "FM0465", "IH0010", "UA0090", "FM1150", "FM1104", "FM2438"},
        "legistar": "newbraunfels",
        "council": "New Braunfels City Council",
        "boards": [("nbisd", "NBISD board", "New Braunfels ISD", 1512),
                   ("comal-isd", "Comal ISD board", "Comal ISD", 1083)],
        "tags_file": "feed/local.json",
        "scores": [
            ("new-braunfels-hs", "New Braunfels HS", "00A83BA0-61D5-48DF-9AF9-48E7C4B9A71C", 2647),
            ("long-creek-hs", "Long Creek HS", "00A83BA0-61D5-48DF-9AF9-48E7C4B9A71C", 7400),
            ("canyon-hs", "Canyon HS", "9A2BC5C8-0EE5-45D9-8A26-0970995E25C8", 657),
            ("canyon-lake-hs", "Canyon Lake HS", "9A2BC5C8-0EE5-45D9-8A26-0970995E25C8", 656),
            ("davenport-hs", "Davenport HS", "9A2BC5C8-0EE5-45D9-8A26-0970995E25C8", 6779),
        ],
    },
    "boerne": {
        "prefix": "boerne",
        "city_name": "City of Boerne",
        "news_feeds": [
            "https://www.ci.boerne.tx.us/RSSFeed.aspx?ModID=63&CID=All-0",
            "https://www.ci.boerne.tx.us/RSSFeed.aspx?ModID=1&CID=All-newsflash.xml",
        ],
        "txdot_counties": ["Kendall"],
        "routes": {"US0087", "SH0046", "FM1376", "RM1376", "BU0087"},
        "cross": {"US0087", "BU0087", "SH0046", "FM1376", "RM1376", "FM3351", "RM3351", "FM0474", "RM0474"},
        "exclude": {"FM0473", "SH0027", "FM0289", "FM1621", "US0290"},
        "legistar": "boerne",
        "council": "Boerne City Council",
        "boards": [("boerne-isd", "Boerne ISD board", "Boerne ISD", 1350)],
        "tags_file": "feed/boerne-local.json",
        "scores": [
            ("boerne-hs", "Boerne HS", "01118F4F-56CF-4098-98B8-373A1B3CC6C5", 5304),
            ("champion-hs", "Champion HS", "01118F4F-56CF-4098-98B8-373A1B3CC6C5", 5305),
        ],
    },
}


# ---------------------------------------------------------------- 1. roads

CLOSE_WORDS = re.compile(r"\b(clos(e|ed|es|ing|ure|ures)|detour|lanes? (?:will be )?(?:closed|reduced)|road ?work)\b", re.I)
NEWS_HINT = re.compile(r"clos|detour|traffic|parade|lighting|construction|road|street|festival|market days|race|5k|marathon|parking", re.I)


def city_closures(city: str) -> list[dict]:
    cfg, t0 = CFG[city], today()
    horizon = t0 + dt.timedelta(days=7)
    seen, cands = set(), []
    for feed in cfg["news_feeds"]:
        try:
            for it in rss_items(fetch(feed)):
                if it["link"] in seen:
                    continue
                seen.add(it["link"])
                if it["date"] and it["date"] < t0 - dt.timedelta(days=45):
                    continue
                if NEWS_HINT.search(it["title"] + " " + it["desc"]):
                    cands.append(it)
        except Exception:
            continue
    out = []
    for it in cands[:8]:
        try:
            page = fetch(it["link"])
        except Exception:
            continue
        m = re.search(r"(?s)Posted on.*?</", page)
        body = page[m.start():] if m else page
        body = re.split(r"Related News|Other News in", body)[0]
        text = strip_tags(body)
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"])", text)
        hits = []
        for i, s in enumerate(sentences):
            if not CLOSE_WORDS.search(s) or re.search(r"\bnot expected to\b|\bwill not\b", s, re.I):
                continue
            # Date in the sentence itself, otherwise the closest date in the two sentences before it.
            found = dates_in(s, t0)
            if not found:
                for back in (1, 2):
                    if i - back >= 0:
                        found = dates_in(sentences[i - back], t0)
                        if found:
                            found = found[-1:]
                            break
            for _, d in found:
                if t0 <= d <= horizon:
                    hits.append((d, s))
                    break
        if not hits:
            continue
        d, s = hits[0]
        full = bool(re.search(r"\b(streets?|road|roads)\b.*\bclose|full closure|closed to (all )?traffic", s, re.I))
        subject = re.sub(r"\s+(to|will|-|–|:).*$", "", it["title"]) if len(it["title"]) > 36 else it["title"]
        out.append(notice(
            id=f"{cfg['prefix']}-road-{slug(subject)[:30]}-{d.isoformat()}",
            kind="road",
            title=f"Street closure {fmt_day(d)}: {subject}",
            detail=f"{fmt_day(d)}: {s}",
            level="warn" if full else "info",
            url=it["link"].replace("http://", "https://"),
            source=cfg["city_name"],
            until=d.isoformat(),
            _rank=0 if full else 2,
        ))
    return out


ROUTE_RE = re.compile(r"^([A-Z]{2})0*(\d+)([A-Z]?)$")
ROUTE_NAMES = {"IH": "I-", "SL": "Loop ", "FM": "FM ", "RM": "RM ", "SH": "SH ", "US": "US ", "BU": "Bus. ", "SP": "Spur ", "UA": "US "}


def pretty_route(r: str) -> str:
    m = ROUTE_RE.match(r.strip())
    if not m:
        return r
    return f"{ROUTE_NAMES.get(m.group(1), m.group(1) + ' ')}{m.group(2)}{m.group(3)}"


def pretty_limits(s: str) -> str:
    return re.sub(r"\b([A-Z]{2}0*\d+[A-Z]?)\b", lambda m: pretty_route(m.group(1)), s)


def txdot_closures(city: str) -> list[dict]:
    cfg, t0 = CFG[city], today()
    horizon = t0 + dt.timedelta(days=7)
    url = "http://conditions.drivetexas.org/current/conditions.asp"
    out = []
    def get(county):
        try:
            return fetch(url, {"rdwy": "", "cnty": county, "cnstrnt": "", "website": "dot", "Submit": "Submit"})
        except Exception:
            return ""
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        pages = list(ex.map(get, cfg["txdot_counties"]))
    for page in pages:
        if not page:
            continue
        text = strip_tags(page)
        for block in text.split("District:")[1:]:
            head, _, rest = block.partition("Description:")
            lines = [l.strip() for l in head.split("Limits:", 1)[-1].split("\n") if l.strip()]
            if len(lines) < 7:
                continue
            _, cty, cond, route, direction, span, limits = lines[:7]
            tm = re.match(r"(\d{4}-\d\d-\d\d).*?to\s+(\d{4}-\d\d-\d\d)", span)
            if not tm or not re.match(r"^[A-Z]{2}\d{4}[A-Z]?$", route):
                continue
            start, end = tm.groups()
            desc = re.split(r"Search Again|Disclaimer:", rest)[0]
            desc = " ".join(l.strip().rstrip(".") + "." for l in desc.split("\n") if l.strip())
            desc = re.sub(r"\s*\((Delay|Detour)\)\.?", "", desc).replace("..", ".").strip()
            codes = set(re.findall(r"\b[A-Z]{2}\d{4}[A-Z]?\b", limits)) | {route}
            if codes & cfg["exclude"]:
                continue
            if route not in cfg["routes"] and not (codes & cfg["cross"]):
                continue
            if route == "SH0046" and city == "new-braunfels" and not (codes & cfg["cross"] - {"SH0046"}):
                continue
            if re.search(r"shoulder closure only|SHOULDER CLOSURE", desc) and "lane" not in desc.lower().replace("shoulder", ""):
                continue
            s_d, e_d = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
            if s_d > horizon or e_d < t0:
                continue
            main_closed = bool(re.search(r"main lanes closed|road closed|all lanes closed|full closure", desc, re.I)) or cond == "Closed"
            multi = bool(re.search(r"multiple|alternating|reduced to one lane", desc, re.I))
            first = re.split(r"(?<=\.)\s", desc)[0].rstrip(".") if desc else cond
            short = f"{pretty_route(route)}: {first.lower()}"
            long_term = (e_d - s_d).days > 60
            when = f"through {fmt_day(e_d)}" + (f", {e_d.year}" if e_d.year != t0.year else "")
            detail = f"{direction}, {pretty_limits(limits)}, {when}. {desc}"
            rank = 1 if main_closed else (3 if multi and not long_term else 4 if multi else 5)
            out.append(notice(
                id=f"{cfg['prefix']}-road-txdot-{route.lower()}-{slug(direction)[:12]}-{start}",
                kind="road",
                title=short,
                detail=detail,
                level="warn" if main_closed else "info",
                url="http://conditions.drivetexas.org/current/",
                source="TxDOT DriveTexas",
                until=min(e_d, horizon).isoformat(),
                _rank=rank,
            ))
    return out


def collect_roads(city: str) -> list[dict]:
    items = []
    for fn in (city_closures, txdot_closures):
        try:
            items += fn(city)
        except Exception:
            pass
    items.sort(key=lambda n: (n["_rank"], n.get("until", "")))
    seen, out = set(), []
    for n in items:
        if n["id"] in seen:
            continue
        seen.add(n["id"])
        out.append(n)
    return out[:4]


# ---------------------------------------------------------------- 2. agendas

STOP_SINGLE = {"dean", "kraft", "navarro", "plantation", "equinox", "glendale", "parkside", "cornerstone",
               "maldonado", "fellers", "laubach", "kohlenberg", "northridge", "northwoods", "morningstar",
               "greystone", "bentwood", "ridgemont", "navarro", "dauer", "esperanza", "regent", "southglen",
               "stonegate", "waterstone", "saddlehorn", "miralomas", "sablechase", "isbell", "front gate"}
SINGLE_OK = {"veramendi", "mayfair", "summerwood", "gruene", "havenwood", "kyndwood", "gruenefield",
             "saengerhalle", "heatherfield", "cordillera", "tapatio"}


def area_matchers(city: str) -> tuple[list[tuple[re.Pattern, str]], set[str]]:
    """Patterns for neighborhood names and the set of council districts, from the tag files."""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", CFG[city]["tags_file"])
    with open(path) as f:
        feats = json.load(f)["features"]
    pats, councils = [], set()
    for ft in feats:
        layer, tags = ft.get("layer"), ft.get("tags") or []
        if layer == "council":
            councils |= {t.split(":", 1)[1] for t in tags if t.startswith("council:")}
        if layer != "community":
            continue
        tag = next((t for t in tags if t.startswith("community:")), None)
        if not tag:
            continue
        name = re.sub(r"\(.*?\)", "", ft.get("name", "")).strip()
        for alt in [a.strip() for a in name.split("/") if a.strip()]:
            low = alt.lower()
            words = low.split()
            if len(words) == 1 and (low in STOP_SINGLE or low not in SINGLE_OK):
                continue
            if low in STOP_SINGLE:
                continue
            pats.append((re.compile(r"\b" + re.escape(alt) + r"\b", re.I), tag))
    return pats, councils


def match_area(title: str, pats, councils) -> list[str]:
    tags = []
    for m in re.finditer(r"\b(?:council\s+)?district\s+(?:no\.\s*)?(\d)\b", title, re.I):
        if m.group(1) in councils and "school district" not in title[max(0, m.start() - 20):m.end()].lower():
            tags.append(f"council:{m.group(1)}")
    for p, tag in pats:
        if p.search(title):
            tags.append(tag)
    return sorted(set(tags))


def area_notices(prefix, body_slug, when_label, d, items, pats, councils, url, source) -> list[dict]:
    out = []
    for title in items:
        tags = match_area(title, pats, councils)
        if not tags:
            continue
        out.append(notice(
            id=f"{prefix}-agenda-{body_slug}-{d.isoformat()}-{slug(title)[:24]}",
            kind="agenda",
            title=f"On the {when_label} agenda near you",
            detail=f"{when_label} agenda item: “{clip(title, 170)}”",
            level="info", url=url, source=source, until=d.isoformat(),
            tags=tags, audience="local",
        ))
        if len(out) >= 3:
            break
    return out


def legistar_meetings(city: str, pats, councils) -> list[dict]:
    cfg, t0 = CFG[city], today()
    client = cfg["legistar"]
    end = t0 + dt.timedelta(days=7)
    q = (f"https://webapi.legistar.com/v1/{client}/events?$filter="
         + urllib.parse.quote(f"EventDate ge datetime'{t0.isoformat()}' and EventDate le datetime'{end.isoformat()}'")
         + "&$orderby=EventDate")
    events = json.loads(fetch(q))
    out = []
    for e in events:
        if (e.get("EventBodyName") or "").strip() != "City Council":
            continue
        blob = " ".join(str(e.get(k) or "") for k in ("EventLocation", "EventComment", "EventAgendaStatusName"))
        if re.search(r"quorum|cancel", blob, re.I):
            continue
        d = dt.date.fromisoformat(e["EventDate"][:10])
        tm = (e.get("EventTime") or "").lstrip("0")
        url = e.get("EventInSiteURL") or e.get("EventAgendaFile") or f"https://{client}.legistar.com/Calendar.aspx"
        loc = e.get("EventLocation") or "location on the agenda"
        agenda = e.get("EventAgendaFile")
        out.append(notice(
            id=f"{cfg['prefix']}-agenda-council-{d.isoformat()}",
            kind="agenda",
            title=f"City Council meets {fmt_day(d)}, {tm}".rstrip(", "),
            detail=f"{cfg['council']} regular meeting {fmt_day(d)} at {tm}, {loc}. "
                   + ("The agenda is posted; tap to read it." if agenda else "The agenda has not been posted yet."),
            level="info", url=agenda or url, source=cfg["city_name"], until=d.isoformat(),
        ))
        try:
            items = json.loads(fetch(f"https://webapi.legistar.com/v1/{client}/events/{e['EventId']}/eventitems"))
            titles = [(i.get("EventItemTitle") or "").strip() for i in items if i.get("EventItemMatterFile")]
            out += area_notices(cfg["prefix"], "council", f"{fmt_day(d)} council", d, titles, pats, councils,
                                agenda or url, cfg["city_name"])
        except Exception:
            pass
        break  # next regular meeting only
    return out


BB_ROW_DATE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4}) at (\d{1,2}:\d\d [AP]M) - ([^<\n]+)")


def boardbook_meetings(city: str, pats, councils) -> list[dict]:
    cfg, t0 = CFG[city], today()
    end = t0 + dt.timedelta(days=7)
    out = []
    for dslug, label, source, org in cfg["boards"]:
        try:
            page = fetch(f"https://meetings.boardbook.org/Public/Organization/{org}")
        except Exception:
            continue
        best = None
        for row in page.split("row-for-board")[1:]:
            if "meeting-is-cancelled" in row[:1500]:
                continue
            m = BB_ROW_DATE.search(row)
            ty = re.search(r"Meeting Type:</b>\s*([^<]+)", row)
            if not m or not ty or ty.group(1).strip() != "Regular":
                continue
            d = dt.datetime.strptime(m.group(1), "%B %d, %Y").date()
            if not (t0 <= d <= end):
                continue
            loc = [html.unescape(x).strip(" ,") for x in re.findall(r"id='location[^']*-(?:description|line1)'>([^<]*)<", row)]
            ag = re.search(r'href="(/Public/Agenda/\d+\?meeting=\d+)"', row)
            if best is None or d < best[0]:
                best = (d, m.group(2), html.unescape(m.group(3)).strip(), ", ".join(x for x in loc if x), ag and ag.group(1))
        if not best:
            continue
        d, tm, name, loc, ag = best
        url = f"https://meetings.boardbook.org{ag}" if ag else f"https://meetings.boardbook.org/Public/Organization/{org}"
        out.append(notice(
            id=f"{cfg['prefix']}-agenda-{dslug}-{d.isoformat()}",
            kind="agenda",
            title=f"{label} meets {fmt_day(d)}, {tm}",
            detail=f"{source} board of trustees {name.lower()} {fmt_day(d)} at {tm}"
                   + (f", {loc}" if loc else "") + ". The agenda is posted; tap to read it.",
            level="info", url=url, source=f"{source} (BoardBook)", until=d.isoformat(),
        ))
        if ag:
            try:
                apage = fetch(url)
                titles = [strip_tags(t) for t in re.findall(
                    r"\d+(?:\.[A-Z0-9]+)*\.\s*(?:<u>)?\s*<strong>(.*?)</strong>", apage, re.S)]
                out += area_notices(cfg["prefix"], dslug, f"{fmt_day(d)} {label}", d, titles, pats, councils,
                                    url, f"{source} (BoardBook)")
            except Exception:
                pass
    return out


def collect_agendas(city: str) -> list[dict]:
    try:
        pats, councils = area_matchers(city)
    except Exception:
        pats, councils = [], set()
    out = []
    for fn in (legistar_meetings, boardbook_meetings):
        try:
            out += fn(city, pats, councils)
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- 3. pets

def split_breed(b: str) -> str:
    return re.sub(r"([a-z])([A-Z])", r"\1/\2", b.strip())


def hsnba_pets() -> list[dict]:
    pets = []
    for kind, path in (("dog", "adopt-a-dog"), ("cat", "adopt-a-cat")):
        try:
            page = fetch(f"https://hsnba.org/adopt/{path}/")
        except Exception:
            continue
        for m in re.finditer(r'<div class="content-wrap">(.*?)</div>\s*</div>', page, re.S):
            blk = m.group(1)
            a = re.search(r'<div class="name"><a href="([^"]+)">([^<]+)</a>', blk)
            if not a:
                continue
            get = lambda c: strip_tags((re.search(rf'<div class="{c}">(.*?)</div>', blk, re.S) or [None, ""])[1])
            pid = re.search(r"/listing/(\d+)", a.group(1))
            pets.append({"id": pid.group(1) if pid else slug(a.group(2)), "url": a.group(1),
                         "name": re.split(r"\s*\(", html.unescape(a.group(2)))[0].strip(),
                         "age": re.split(r"\s*\(", get("age"))[0].strip().lower(),
                         "breed": split_breed(get("breed")), "sex": get("gender"), "species": kind})
    return pets


def adoptapet_pets(shelter_id: int) -> list[dict]:
    page = fetch(f"https://www.adoptapet.com/shelter/{shelter_id}/available-pets").replace('\\"', '"')
    pets = {}
    for m in re.finditer(r'"__typename":"PetCardResource","petId":(\d+).*?"name":"([^"]*)","clanId":(\d+),'
                         r'"breed":"([^"]*)","sex":"(\w*)","age":"([^"]*)".*?"pdpRoute":"([^"]*)"', page):
        pid, name, clan, breed, sex, age, url = m.groups()
        age = age.strip()
        am = re.match(r"(\d+)\s*(mo|yr|wk)", age)
        if am:
            unit = {"mo": "month", "yr": "year", "wk": "week"}[am.group(2)]
            age = f"{am.group(1)}-{unit}-old"
        pets[pid] = {"id": pid, "url": url, "name": name.strip(), "age": age.lower(),
                     "breed": breed.replace("/", "/").strip(), "species": {"1": "dog", "2": "cat"}.get(clan, "pet"),
                     "sex": {"m": "Male", "f": "Female"}.get(sex, "")}
    return list(pets.values())


def collect_pets(city: str) -> list[dict]:
    cfg, t0 = CFG[city], today()
    try:
        if city == "new-braunfels":
            pets, source, where = hsnba_pets(), "Humane Society of the New Braunfels Area", "HSNBA, 3353 Morningside Dr"
        else:
            pets, source, where = adoptapet_pets(63932), "City of Boerne Animal Shelter (Adopt-a-Pet)", "City of Boerne Animal Shelter, 330 S. Esser Rd"
    except Exception:
        return []
    pets = sorted((p for p in pets if p["name"]), key=lambda p: p["id"])
    if not pets:
        return []
    start = (t0.toordinal() * 3) % len(pets)
    picks = [pets[(start + i) % len(pets)] for i in range(min(3, len(pets)))]
    out = []
    for p in picks:
        desc = " ".join(x for x in (p["age"], p["breed"].lower() if p["species"] == "dog" else p["breed"]) if x)
        title = f"Adoptable: {p['name']}, {desc}" if desc else f"Adoptable: {p['name']}"
        if len(title) > 60:
            title = f"Adoptable: {p['name']}, {p['breed']}"
        sex = p["sex"].lower() if p["sex"] else ""
        out.append(notice(
            id=f"{cfg['prefix']}-pet-{p['id']}",
            kind="pets",
            title=title,
            detail=f"{(sex + ' ' + p['species']).strip().capitalize()} available for adoption at {where}. Photos and adoption steps are on the listing.",
            level="info", url=p["url"], source=source,
            until=(t0 + dt.timedelta(days=1)).isoformat(),
        ))
    return out


# ---------------------------------------------------------------- 4. scores

SCORE_RE = re.compile(r"\b(W|L|T|Win|Loss|Final)\b[\s:,]*(\d{1,3})\s*[-–]\s*(\d{1,3})\b")


def ical_events(text: str) -> list[dict]:
    text = re.sub(r"\r?\n[ \t]", "", text)
    evs = []
    for blk in text.split("BEGIN:VEVENT")[1:]:
        ev = {}
        for line in blk.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                ev[k.split(";")[0]] = v.replace("\\n", " ").replace("\\,", ",").strip()
        evs.append(ev)
    return evs


def collect_scores(city: str) -> list[dict]:
    """Rank One feeds carry schedules only as of 2026-11-18 (no result field in iCal, and the
    HTML Score column is empty). This reads the iCal and only emits a notice if a result
    pattern ever appears, so it returns [] today."""
    cfg, t0 = CFG[city], today()
    out = []
    for sid, sname, dguid, scode in cfg["scores"]:
        try:
            feed = fetch(f"https://app.rankone.com/Schedules/iCalFeed.aspx?P=0&D={dguid}&S={scode}")
        except Exception:
            continue
        got, seen = [], set()
        for ev in ical_events(feed):
            summ, desc = ev.get("SUMMARY", ""), ev.get("DESCRIPTION", "")
            if "- Varsity" not in summ:
                continue
            m = SCORE_RE.search(desc) or SCORE_RE.search(summ)
            st = ev.get("DTSTART", "")
            if not m or len(st) < 8:
                continue
            try:
                when = dt.datetime.strptime(st[:15], "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc)
                d = when.astimezone(CENTRAL).date() if CENTRAL else when.date()
            except ValueError:
                continue
            if not (t0 - dt.timedelta(days=4) <= d <= t0) or summ in seen:
                continue
            seen.add(summ)
            got.append((d, summ, m.group(0)))
        got.sort(reverse=True)
        for d, summ, res in got[:2]:
            parts = [p.strip() for p in summ.split(" - ")]
            sport = parts[1] if len(parts) > 1 else "Varsity"
            out.append(notice(
                id=f"{cfg['prefix']}-score-{sid}-{d.isoformat()}-{slug(sport)[:16]}",
                kind="score", title=f"{sname} {sport}: {res}",
                detail=f"{parts[0]} ({fmt_day(d)}): {res}, as posted on the Rank One schedule.",
                level="info",
                url=f"https://app.rankone.com/Schedules/View_Schedule_All_Web.aspx?P=0&D={dguid}&S={scode}",
                source="Rank One schedules", until=(d + dt.timedelta(days=4)).isoformat(),
                tags=[f"school:{sid}"], audience="local",
            ))
    return out


# ---------------------------------------------------------------- 5. openings

def boerne_chamber(t0: dt.date) -> list[dict]:
    base = "https://business.boerne.org/events/search?q="
    found = {}
    for q in ("ribbon", "grand+opening"):
        try:
            page = fetch(base + q)
        except Exception:
            continue
        for card in page.split("gz-events-card ")[1:]:
            a = re.search(r'<h5 class="card-title gz-card-title">\s*<a href="([^"]+)">([^<]+)</a>', card)
            d = re.search(r'<span content="(\d{4}-\d\d-\d\dT\d\d:\d\d)', card)
            if not a or not d:
                continue
            title = html.unescape(a.group(2)).strip()
            if not re.search(r"ribbon cutting|grand opening", title, re.I):
                continue
            found[a.group(1)] = (dt.datetime.fromisoformat(d.group(1)), title, a.group(1))
    out = []
    for when, title, url in sorted(found.values()):
        if not (t0 <= when.date() <= t0 + dt.timedelta(days=14)):
            continue
        name = re.sub(r"^(ribbon cutting|grand opening)( (for|at|of))?\s*", "", title, flags=re.I).strip()
        kind = "Grand opening" if re.search(r"grand opening", title, re.I) else "Ribbon cutting"
        addr = ""
        try:
            det = fetch(url)
            sa = re.search(r'itemprop="streetAddress">([^<]+)<', det)
            loc = re.search(r'itemprop="addressLocality">([^<]+)<', det)
            if sa:
                addr = html.unescape(sa.group(1)).strip() + (f", {html.unescape(loc.group(1)).strip()}" if loc else "")
        except Exception:
            pass
        tm = when.strftime("%I:%M %p").lstrip("0")
        out.append(notice(
            id=f"boerne-opening-{slug(name)[:30]}-{when.date().isoformat()}",
            kind="opening", title=f"{kind}: {name}",
            detail=f"Greater Boerne Chamber {kind.lower()} {fmt_day(when.date())} at {tm}" + (f", {addr}." if addr else "."),
            level="info", url=url, source="Greater Boerne Chamber of Commerce", until=when.date().isoformat(),
        ))
        if len(out) >= 3:
            break
    return out


def legistar_openings(city: str, t0: dt.date) -> list[dict]:
    """Ribbon cuttings that the city posts as a 'Notice of Possible Quorum' (council may attend)."""
    cfg = CFG[city]
    client = cfg["legistar"]
    end = t0 + dt.timedelta(days=14)
    q = (f"https://webapi.legistar.com/v1/{client}/events?$filter="
         + urllib.parse.quote(f"EventDate ge datetime'{t0.isoformat()}' and EventDate le datetime'{end.isoformat()}'")
         + "&$orderby=EventDate")
    out = []
    for e in json.loads(fetch(q)):
        loc, com = (e.get("EventLocation") or "").strip(), (e.get("EventComment") or "").strip()
        blob = f"{e.get('EventBodyName')} {loc} {com}"
        if "quorum" not in blob.lower() or not re.search(r"ribbon cutting|grand opening", blob, re.I):
            continue
        d = dt.date.fromisoformat(e["EventDate"][:10])
        tm = (e.get("EventTime") or "").lstrip("0")
        src = loc if re.search(r"ribbon cutting|grand opening", loc, re.I) else com
        kind = "Grand opening" if re.search(r"grand opening", src, re.I) else "Ribbon cutting"
        name = re.sub(r"\s*(ribbon cutting|grand opening)\s*", " ", src, flags=re.I).strip(" -:")
        addr = com if src is loc else loc
        out.append(notice(
            id=f"{cfg['prefix']}-opening-{slug(name)[:30]}-{d.isoformat()}",
            kind="opening", title=f"{kind}: {name}",
            detail=f"{kind} {fmt_day(d)} at {tm}" + (f", {addr}" if addr else "")
                   + f". Posted by the {cfg['city_name']} as a notice that council members may attend.",
            level="info", url=e.get("EventAgendaFile") or e.get("EventInSiteURL") or "",
            source=cfg["city_name"], until=d.isoformat(),
        ))
    return out


def collect_openings(city: str) -> list[dict]:
    t0 = today()
    out = []
    if city == "boerne":
        try:
            out += boerne_chamber(t0)
        except Exception:
            pass
    # The Greater New Braunfels Chamber calendar (GrowthZone) sits behind a Cloudflare
    # browser challenge and refuses this client, so NB uses the city's quorum notices only.
    try:
        names = {re.sub(r"[^a-z]", "", n["title"].split(":", 1)[-1].lower()) for n in out}
        for n in legistar_openings(city, t0):
            if re.sub(r"[^a-z]", "", n["title"].split(":", 1)[-1].lower()) not in names:
                out.append(n)
    except Exception:
        pass
    out.sort(key=lambda n: n.get("until", ""))
    return out[:3]


# ---------------------------------------------------------------- entry point

def collect(city_id: str) -> list[dict]:
    if city_id not in CFG:
        return []
    topics = (collect_roads, collect_agendas, collect_pets, collect_scores, collect_openings)
    results: list[dict] = []
    ex = cf.ThreadPoolExecutor(max_workers=len(topics))
    futs = [ex.submit(fn, city_id) for fn in topics]
    # Overall budget: whatever is not finished in 50 s is dropped (a hung source never blocks the rest).
    done, _ = cf.wait(futs, timeout=50)
    for f in futs:
        if f in done:
            try:
                results += f.result()
            except Exception:
                pass
    ex.shutdown(wait=False, cancel_futures=True)
    for n in results:
        n.pop("_rank", None)
    return results


if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "new-braunfels"
    try:
        data = collect(city)
    except Exception:
        data = []
    print(json.dumps(data, indent=2, ensure_ascii=False))
    sys.stdout.flush()
    import os
    os._exit(0)  # do not wait for a source that is still hanging past the budget
