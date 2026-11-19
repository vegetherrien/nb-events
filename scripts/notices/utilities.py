"""Utility and outdoor-safety notices: water restrictions, burn bans, fireworks, pollen.

Contract: see README.md in this folder. Standard library only.

Sources (all read live in each run):
  water / New Braunfels  https://www.nbutexas.com/current-drought-stage/
  water / Boerne         https://www.ci.boerne.tx.us/1947/Water-Restrictions
  burn-ban               Texas A&M Forest Service BurnBan FeatureServer + each county's page
  fireworks              NB Fire Marshal's Office page; City of Boerne FAQ (QID=66)
  pollen                 none (no daily count source allows an automated client; see research notes)
"""

import datetime as _dt
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover - zoneinfo data missing
    _TZ = None

UA = "nb-events-feed-bot/1.0"
TIMEOUT = 25

NBU_STAGE_URL = "https://www.nbutexas.com/current-drought-stage/"
NBU_HOME_URL = "https://www.nbutexas.com/"
BOERNE_WATER_URL = "https://www.ci.boerne.tx.us/1947/Water-Restrictions"
BOERNE_UTIL_URL = "https://www.ci.boerne.tx.us/2293/Utilities"

TFS_BURNBAN_QUERY = "https://gis.tfs.tamu.edu/arcgis/rest/services/EOC/BurnBan/FeatureServer/0/query"
COUNTY = {
    "new-braunfels": {
        "county": "Comal",
        "page": "https://www.comalcounty.gov/AlertCenter.aspx?CID=Burn-Ban-6",
        "source": "Comal County",
        "prefix": "nb",
    },
    "boerne": {
        "county": "Kendall",
        "page": "https://www.kendallcountytx.gov/432/Burn-Ban",
        "source": "Kendall County",
        "prefix": "boerne",
    },
}

NB_FIREWORKS_URL = "https://www.newbraunfels.gov/313/Fire-Marshals-Office"
BOERNE_FIREWORKS_URL = "https://www.ci.boerne.tx.us/FAQ.aspx?QID=66"

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


# ---------------------------------------------------------------- helpers

def _log(msg):
    print(f"[utilities] {msg}", file=sys.stderr)


def _today():
    override = os.environ.get("NOTICES_TODAY")  # testing only: YYYY-MM-DD
    if override:
        return _dt.date.fromisoformat(override)
    now = _dt.datetime.now(_TZ) if _TZ else _dt.datetime.now()
    return now.date()


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def _text(page_html):
    """HTML -> plain text, one block per line, whitespace collapsed."""
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", page_html)
    s = re.sub(r"<[^>]+>", "\n", s)
    s = html.unescape(s).replace("\xa0", " ")
    lines = (re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def _flat(text):
    return re.sub(r"\s+", " ", text).strip()


def _clip(s, n):
    s = _flat(s)
    return s if len(s) <= n else s[: n - 1].rstrip(" ,;") + "…"


def _notice(**kw):
    kw["title"] = _clip(kw["title"], 60)
    kw["detail"] = _clip(kw["detail"], 220)
    return kw


def _water_level(stage):
    """stage: int, or None for an emergency stage."""
    if stage is None or stage >= 4:
        return "alert"
    if stage >= 2:
        return "warn"
    return "info"


def _short_time(t):
    # "10 a.m." -> "10am"; "7 p.m." -> "7pm"; "midnight" stays
    t = t.strip().lower().replace(".", "").replace(" ", "")
    return t


def _day_table(text):
    """Find 'address ending 0 or 1 -> Monday' pairs; returns '0-1 Mon, 2-3 Tue, ...' or ''."""
    pairs = re.findall(r"\b(\d) or (\d)\b\W{0,5}(?:Water on\s+)?(" + "|".join(DAYS) + r")", text)
    seen, out = set(), []
    for a, b, day in pairs:
        if a in seen:
            continue
        seen.add(a)
        out.append(f"{a}-{b} {day[:3]}")
    return ", ".join(out) if len(out) >= 3 else ""


# ---------------------------------------------------------------- water

def _water_nb(today):
    text = _text(_get(NBU_STAGE_URL))
    m = re.search(r"Current\s+Conservation\s+Status:\s*(Stage\s*(\d)|[A-Za-z ]+)", text, re.I)
    if not m:
        raise ValueError("NBU: 'Current Conservation Status' not found")
    if m.group(2):
        stage = int(m.group(2))
        label = f"Stage {stage}"
    else:
        raw = m.group(1).strip()
        if re.search(r"emergency", raw, re.I):
            stage, label = None, "Emergency"
        elif re.search(r"\b(none|no restrictions|stage 0)\b", raw, re.I):
            stage, label = 0, "No stage"
        else:
            raise ValueError(f"NBU: unrecognised status {raw!r}")

    # The section that describes the current stage runs until the PDF guide / next heading.
    start = m.end()
    end_m = re.search(r"Watering Guide|Protecting the Community", text[start:])
    section = text[start : start + end_m.start()] if end_m else text[start : start + 1500]
    sec = _flat(section)

    eff = re.search(r"Effective\s+([A-Z][a-z]+ \d{1,2}, \d{4})", sec)
    parts = []
    if re.search(r"irrigation system or sprinkler[^.]*?once per week", sec, re.I):
        days = _day_table(text)
        parts.append("Sprinklers once a week by address" + (f" ({days})" if days else ""))
    elif re.search(r"once every other week", sec, re.I):
        parts.append("Sprinklers once every other week by address")
    hrs = re.search(r"Irrigation is permitted only before ([\d:]+ [ap]\.m\.) and after ([\d:]+ [ap]\.m\.)", sec, re.I)
    if hrs and parts:
        parts[-1] += f", before {_short_time(hrs.group(1))} or after {_short_time(hrs.group(2))}"
    if re.search(r"hand-held hose[^.]*?allowed any day,? anytime", sec, re.I):
        parts.append("hand watering any time")
    elif re.search(r"hand-held hose[^.]*?allowed any day before", sec, re.I):
        parts.append("hand watering any day in those hours")
    if parts:
        detail = "; ".join(parts) + "."
    else:
        detail = f"NBU is in {label}. Check the page for your watering day and hours."
    if eff and len(detail) + len(eff.group(1)) + 20 <= 220:
        detail = f"{detail} In effect since {eff.group(1)}."

    return _notice(
        id="nb-water-stage",
        kind="water",
        title=f"NBU {label} watering rules" if stage != 0 else "NBU: no watering stage in effect",
        detail=detail,
        level=_water_level(stage),
        url=NBU_STAGE_URL,
        source="New Braunfels Utilities",
        asOf=today.isoformat(),
    )


def _water_nb_fallback(today):
    """Header badge on every NBU page: 'Drought Stage N Alert' / 'Conservation STAGE N'."""
    page = _get(NBU_HOME_URL)
    m = re.search(r'aria-label="Drought Stage (\d)', page) or re.search(r"Conservation\s*<span[^>]*>\s*STAGE (\d)", page)
    if not m:
        raise ValueError("NBU home: stage badge not found")
    stage = int(m.group(1))
    return _notice(
        id="nb-water-stage",
        kind="water",
        title=f"NBU Stage {stage} watering rules",
        detail=f"New Braunfels Utilities is in Stage {stage}. See NBU's current-stage page for your watering day and hours.",
        level=_water_level(stage),
        url=NBU_STAGE_URL,
        source="New Braunfels Utilities",
        asOf=today.isoformat(),
    )


def _water_boerne(today):
    text = _text(_get(BOERNE_WATER_URL))
    flat = _flat(text)
    m = re.search(r"currently in Stage (\d) Water Restrictions(?:, effective (?:[A-Z][a-z]+, )?([A-Z][a-z]+ \d{1,2}, \d{4}))?", flat)
    stage = None
    if m:
        stage = int(m.group(1))
        label = f"Stage {stage}"
        eff = m.group(2)
    elif re.search(r"currently (?:not under|in no) (?:any )?(?:water )?restrictions", flat, re.I):
        stage, label, eff = 0, "No stage", None
    elif re.search(r"currently in (?:an? )?(?:Stage \w+ )?Emergency", flat, re.I):
        stage, label, eff = None, "Emergency", None
    else:
        raise ValueError("Boerne: current stage sentence not found")

    detail = ""
    if stage:
        # Section for the current stage: from its heading to the next stage heading or FAQ.
        sm = re.search(rf"Stage {stage} Water Restrictions\nStage {stage} Water Restrictions\n(.*?)(?=\nStage \d Water Restrictions\n|\nFrequently Asked)", text, re.S)
        sec = _flat(sm.group(1)) if sm else ""
        parts = []
        if re.search(r"Lawn irrigation with sprinklers[^.]*NOT allowed", sec, re.I):
            parts.append("No lawn sprinklers")
            if re.search(r"hand-held hose[^.]*ONE DAY PER WEEK", sec, re.I):
                parts.append("hand-water other plants one day a week by address")
        else:
            freq = re.search(r"on (ONE DAY (?:PER WEEK|EVERY OTHER WEEK))", sec)
            if freq:
                days = _day_table(sec)
                parts.append("Sprinklers " + freq.group(1).lower().replace("one day per week", "one day a week")
                             + " by address" + (f" ({days})" if days else ""))
        hrs = re.search(r"only allowed from (.+?) on your designated day", sec)
        if hrs and parts:
            h = re.sub(r"(\d+) ([ap])\.m\.", lambda x: x.group(1) + x.group(2) + "m", hrs.group(1))
            h = h.replace(" to ", "-").replace(" and from ", " or ")
            parts[-1] += f", {h}"
        if re.search(r"Watering on weekends is not allowed", sec):
            parts.append("no weekends")
        if re.search(r"Watering by hand with a hose or bucket allowed any time", sec):
            parts.append("hand watering any time")
        if parts:
            detail = "; ".join(parts) + "."
    if not detail:
        detail = (f"Boerne Utilities is in {label}. Check the page for your watering day and hours."
                  if stage != 0 else "Boerne Utilities customers are not under water restrictions right now.")
    if eff and len(detail) + len(eff) + 20 <= 220:
        detail = f"{detail} In effect since {eff}."

    return _notice(
        id="boerne-water-stage",
        kind="water",
        title=f"Boerne {label} watering rules" if stage != 0 else "Boerne: no watering restrictions",
        detail=detail,
        level=_water_level(stage),
        url=BOERNE_WATER_URL,
        source="City of Boerne (Boerne Utilities)",
        asOf=today.isoformat(),
    )


def _water_boerne_fallback(today):
    text = _flat(_text(_get(BOERNE_UTIL_URL)))
    m = re.search(r"currently in Stage (\d) Water Restrictions", text) or re.search(r"remain in Stage (\d) Water Restrictions", text, re.I)
    if not m:
        raise ValueError("Boerne Utilities page: stage not found")
    stage = int(m.group(1))
    return _notice(
        id="boerne-water-stage",
        kind="water",
        title=f"Boerne Stage {stage} watering rules",
        detail=f"Boerne Utilities customers are in Stage {stage} water restrictions. See the city's page for your watering day and hours.",
        level=_water_level(stage),
        url=BOERNE_WATER_URL,
        source="City of Boerne (Boerne Utilities)",
        asOf=today.isoformat(),
    )


def _water(city_id, today):
    primary, fallback = {
        "new-braunfels": (_water_nb, _water_nb_fallback),
        "boerne": (_water_boerne, _water_boerne_fallback),
    }[city_id]
    for fn in (primary, fallback):
        try:
            return [fn(today)]
        except Exception as e:
            _log(f"water {city_id} {fn.__name__}: {e}")
    return []


# ---------------------------------------------------------------- burn ban

def _tfs_burnban(county):
    """Returns (on: bool, start: date|None) from the Texas A&M Forest Service map layer."""
    q = urllib.parse.urlencode({
        "where": f"County='{county}'",
        "outFields": "County,BurnBan,StartDate",
        "returnGeometry": "false",
        "f": "json",
    })
    data = json.loads(_get(f"{TFS_BURNBAN_QUERY}?{q}"))
    feats = data.get("features") or []
    if not feats:
        raise ValueError(f"TFS: no row for {county} ({data.get('error')})")
    a = feats[0]["attributes"]
    val = (a.get("BurnBan") or "").strip().lower()
    if val not in ("yes", "no"):
        raise ValueError(f"TFS: unexpected BurnBan value {a.get('BurnBan')!r}")
    start = None
    if a.get("StartDate"):
        ts = _dt.datetime.fromtimestamp(a["StartDate"] / 1000, _dt.timezone.utc)
        start = (ts.astimezone(_TZ) if _TZ else ts).date()
    return val == "yes", start


def _county_burnban(url):
    """Reads the county's own page; returns True (ban on) / False (off)."""
    text = _flat(_text(_get(url)))
    m = re.search(r"(?i:burn ban)\s+(?:(?i:is)\s+)?(?:(?i:now)\s+)?(ON|OFF|On|Off|(?i:in effect)|(?i:lifted))\b", text)
    if not m:
        raise ValueError("county page: no ON/OFF status found")
    return m.group(1).lower() in ("on", "in effect")


def _burnban(city_id, today):
    c = COUNTY[city_id]
    county_on = tfs_on = None
    start = None
    try:
        county_on = _county_burnban(c["page"])
    except Exception as e:
        _log(f"burn-ban {c['county']} county page: {e}")
    try:
        tfs_on, start = _tfs_burnban(c["county"])
    except Exception as e:
        _log(f"burn-ban {c['county']} TFS: {e}")
    if county_on is not None and tfs_on is not None and county_on != tfs_on:
        _log(f"burn-ban {c['county']}: county page says {county_on}, TFS map says {tfs_on}; using county page")
    # The county issues the order, so its own page wins; the TFS map is the fallback.
    on = county_on if county_on is not None else tfs_on
    if not on:
        return []
    since = f" since {start.strftime('%B %-d')}" if start and tfs_on else ""
    source = c["source"] if county_on is not None else f"Texas A&M Forest Service ({c['county']} County order)"
    return [_notice(
        id=f"{c['prefix']}-burn-ban",
        kind="burn-ban",
        title=f"{c['county']} County burn ban in effect",
        detail=f"{c['county']} County {'has been' if since else 'is'} under an outdoor burn ban{since}. Don't burn outdoors; see the county order for exceptions.",
        level="warn",
        url=c["page"],
        source=source,
        asOf=today.isoformat(),
    )]


# ---------------------------------------------------------------- fireworks

def _fireworks_window(today):
    """Two weeks before New Year's Eve (through Jan 1) and before July 4. Returns 'until' date or None."""
    y = today.year
    windows = [
        (_dt.date(y, 12, 17), _dt.date(y + 1, 1, 1)),
        (_dt.date(y - 1, 12, 17), _dt.date(y, 1, 1)),
        (_dt.date(y, 6, 20), _dt.date(y, 7, 4)),
    ]
    for start, end in windows:
        if start <= today <= end:
            return end
    return None


_FW_BAN = re.compile(r"\b(?:not allowed|prohibited|illegal|banned)\b", re.I)


def _sentences(text):
    """Split page text (one block per line) into sentences; headings stay separate lines."""
    for line in text.split("\n"):
        for s in re.split(r"(?<=[.!?])\s+", line):
            if s.strip():
                yield s.strip()


def _fireworks(city_id, today):
    until = _fireworks_window(today)
    if not until:
        return []
    if city_id == "new-braunfels":
        url, source, town, nid = NB_FIREWORKS_URL, "New Braunfels Fire Marshal's Office", "New Braunfels", "nb-fireworks"
    else:
        url, source, town, nid = BOERNE_FIREWORKS_URL, "City of Boerne", "Boerne", "boerne-fireworks"
    try:
        text = _text(_get(url))
    except Exception as e:
        _log(f"fireworks {city_id}: {e}")
        return []
    rule = None
    for s in _sentences(text):
        if re.search(r"(?i)\bfireworks?\b", s) and _FW_BAN.search(s) and re.search(town, s, re.I) and re.search(r"(?i)city", s):
            rule = s
            break
    if not rule:
        _log(f"fireworks {city_id}: no rule sentence found on {url}; returning nothing")
        return []
    detail = f"Official rule: “{_clip(rule, 200)}”"
    return [_notice(
        id=nid,
        kind="fireworks",
        title=f"Fireworks not allowed in {town} city limits",
        detail=detail,
        level="warn",
        url=url,
        source=source,
        asOf=today.isoformat(),
        until=until.isoformat(),
    )]


# ---------------------------------------------------------------- pollen

def _pollen(city_id, today):
    # No source of a daily local pollen count could be read by an honestly-named client
    # (tested 2026-11-18: KVUE article 403, KXAN 403, KSAT posts the count only as an image,
    # Open-Meteo pollen is Europe-only and modelled, pollen.com API refuses plain clients).
    # The contract forbids estimating, so this topic returns nothing until a source is found.
    return []


# ---------------------------------------------------------------- entry point

def collect(city_id: str) -> list:
    if city_id not in COUNTY:
        return []
    today = _today()
    out = []
    for topic in (_water, _burnban, _fireworks, _pollen):
        try:
            out.extend(topic(city_id, today))
        except Exception as e:  # one topic must never break the others
            _log(f"{topic.__name__} {city_id}: {e}")
    return out


if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "new-braunfels"
    print(json.dumps(collect(city), indent=2, ensure_ascii=False))
