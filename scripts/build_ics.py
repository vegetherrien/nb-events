"""Builds nb-events.ics from events.json so people can subscribe in Apple or Google Calendar.

Run: python3 scripts/build_ics.py events.json nb-events.ics
Opt-in categories (defaultHidden) are left out, same as a fresh install of the app.
"""
import json
import sys
from datetime import date, datetime, timedelta

TZ = "America/Chicago"

# Central time rules, so Apple and Google Calendar place events correctly.
VTIMEZONE = """BEGIN:VTIMEZONE
TZID:America/Chicago
X-LIC-LOCATION:America/Chicago
BEGIN:DAYLIGHT
TZOFFSETFROM:-0600
TZOFFSETTO:-0500
TZNAME:CDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0500
TZOFFSETTO:-0600
TZNAME:CST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""


def esc(s):
    return (s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
             .replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line):
    """RFC 5545: lines are at most 75 octets; continuation lines start with a space."""
    out, b = [], line.encode("utf-8")
    while len(b) > 75:
        cut = 75
        while cut > 0 and (b[cut] & 0xC0) == 0x80:  # don't split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b" " + b[cut:]
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


def compact(s):
    return s.replace("-", "").replace(":", "")


def main(src, dst):
    feed = json.load(open(src, encoding="utf-8"))
    hidden = {c["id"] for c in feed["categories"] if c.get("defaultHidden")}
    names = {c["id"]: c["name"] for c in feed["categories"]}
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//NB Events//New Braunfels Event Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:NB Events",
        "X-WR-CALDESC:Festivals, music, markets, holidays and games around New Braunfels, Texas.",
        f"X-WR-TIMEZONE:{TZ}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
        VTIMEZONE,
    ]

    count = 0
    for e in feed["events"]:
        if e["category"] in hidden:
            continue
        start = date.fromisoformat(e["startDate"])
        end = date.fromisoformat(e.get("endDate", e["startDate"]))
        multi = end > start
        ev = ["BEGIN:VEVENT",
              f"UID:{e['id']}@nb-events",
              f"DTSTAMP:{stamp}",
              f"SUMMARY:{esc(e['title'])}"]
        if e.get("startTime") and not multi and not e.get("timeTBA"):
            st = f"{compact(e['startDate'])}T{compact(e['startTime'])}00"
            ev.append(f"DTSTART;TZID={TZ}:{st}")
            if e.get("endTime"):
                ev.append(f"DTEND;TZID={TZ}:{compact(e['startDate'])}T{compact(e['endTime'])}00")
            else:
                h, m = map(int, e["startTime"].split(":"))
                endt = datetime(start.year, start.month, start.day, h, m) + timedelta(hours=2)
                ev.append(f"DTEND;TZID={TZ}:{endt.strftime('%Y%m%dT%H%M%S')}")
        else:
            ev.append(f"DTSTART;VALUE=DATE:{compact(e['startDate'])}")
            ev.append(f"DTEND;VALUE=DATE:{compact((end + timedelta(days=1)).isoformat())}")
        if e.get("location"):
            ev.append(f"LOCATION:{esc(e['location'])}")
        desc = []
        if e.get("description"): desc.append(e["description"])
        if e.get("timeNote"): desc.append("Time: " + e["timeNote"])
        if e.get("price"): desc.append("Price: " + e["price"])
        if e.get("tv"): desc.append("TV: " + e["tv"])
        if multi and e.get("startTime"):
            desc.append(f"Daily {e['startTime']}" + (f"–{e['endTime']}" if e.get("endTime") else ""))
        if e.get("sourceURL"):
            desc.append(e["sourceURL"])
            ev.append(f"URL:{e['sourceURL']}")
        if desc:
            ev.append(f"DESCRIPTION:{esc(chr(10).join(desc))}")
        ev.append(f"CATEGORIES:{esc(names.get(e['category'], e['category']))}")
        ev.append("END:VEVENT")
        lines.extend(ev)
        count += 1

    lines.append("END:VCALENDAR")
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(fold(l) for l in lines) + "\r\n")
    print(f"wrote {dst}: {count} events")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "events.json",
         sys.argv[2] if len(sys.argv) > 2 else "nb-events.ics")
