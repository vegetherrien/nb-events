#!/usr/bin/env python3
"""Finds restaurants and bars that opened recently, for the "New to try" card.

    python3 scripts/scrape_new_places.py feed/events.json            # report only
    python3 scripts/scrape_new_places.py feed/events.json --apply

Source: the Texas Alcoholic Beverage Commission's public license list on data.texas.gov. A place
counts when its first on-premise permit (mixed beverage, wine & beer, beer, brewpub) was issued in
the last DAYS days and is active. That catches most new sit-down spots; it misses places that
don't serve alcohol, and a new permit can also mean an existing place changed owners, which is
why the card says "newly licensed".
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

UA = "nb-events-feed-bot/1.0"
DATASET = "https://data.texas.gov/resource/7hf9-qc9f.json"
PAGE = "https://data.texas.gov/Government-and-Taxes/TABC-License-Information/7hf9-qc9f"
DAYS = 120
LIMIT = 8
CITY = {"new-braunfels": "NEW BRAUNFELS", "boerne": "BOERNE"}
TYPES = {"MB": "bar & restaurant", "BG": "restaurant", "BE": "restaurant", "BP": "brewpub"}
# Not somewhere you'd go "to try": hotels, event venues, caterers, stores.
SKIP = re.compile(r"\b(suites|inn|hotel|marriott|hilton|hampton|holiday inn|catering|caterer|event center|"
                  r"venue|wedding|country club|golf club|hospital|church|grocery|market #|h-e-b|walmart|"
                  r"cinema|theat(er|re)s?)\b", re.I)
SMALL = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "n", "'n"}


def tidy(name):
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\s*#\d+$", "", name)                 # "Chipotle Mexican Grill #5720"
    letters = [c for c in name if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        words = name.lower().split(" ")
        name = " ".join(w if i and w in SMALL else w[:1].upper() + w[1:] for i, w in enumerate(words))
    return name


def tidy_address(a):
    a = re.sub(r"\s+", " ", a).strip()
    return re.sub(r"\b(STE|Ste|Bldg|BLDG|Suite)\b.*$", "", a).strip(" ,")


def main(path, apply=False):
    feed = json.load(open(path, encoding="utf-8"))
    city = (feed.get("city") or {}).get("id", "new-braunfels")
    if city not in CITY:
        return
    since = (date.today() - timedelta(days=DAYS)).isoformat()
    where = (f"upper(city)='{CITY[city]}' AND license_type in({','.join(repr(t) for t in TYPES)}) "
             f"AND original_issue_date > '{since}T00:00:00' AND license_status='Active'")
    q = urllib.parse.urlencode({"$select": "trade_name,license_type,original_issue_date,address",
                                "$where": where, "$order": "original_issue_date DESC", "$limit": "60"})
    req = urllib.request.Request(DATASET + "?" + q, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        rows = json.load(r)
    out, seen = [], set()
    for row in rows:
        name = tidy(row.get("trade_name", ""))
        if not name or SKIP.search(name) or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "kind": TYPES.get(row["license_type"], "restaurant"),
                    "address": tidy_address(row.get("address", "")), "since": row["original_issue_date"][:10],
                    "source": "Texas Alcoholic Beverage Commission", "url": PAGE})
        if len(out) >= LIMIT:
            break
    for p in out:
        print(f"  {p['since']}  {p['name']:<34} {p['kind']:<16} {p['address']}")
    print(f"{len(out)} new place(s) in {city}.")
    if apply:
        if out or not feed.get("newPlaces"):          # keep the last list if the source is empty for a day
            feed["newPlaces"] = out
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(feed, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], apply="--apply" in sys.argv)
