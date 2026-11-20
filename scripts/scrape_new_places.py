#!/usr/bin/env python3
"""Finds restaurants, cafés and bars that opened recently, for the "New to try" card.

    python3 scripts/scrape_new_places.py feed/events.json            # report only
    python3 scripts/scrape_new_places.py feed/events.json --apply

Two public state lists on data.texas.gov, merged by address:
  * Texas Comptroller sales-tax permits (every place that sells food needs one). The outlet's
    "first sales date" is its opening day; a date a few weeks ahead means it is about to open.
    Food trucks, caterers and contract kitchens are left out.
  * Texas Alcoholic Beverage Commission on-premise licenses (catches bars and restaurants the
    first list files under a holding company).
A new permit can also mean new owners at an old place, so a candidate is dropped when the
Comptroller's full history shows the same name selling at the same address before the window.
Local places are listed before chain locations.

"In the news" adds recent opening stories from local food coverage (WordPress sites with a public
posts API): WhatNow San Antonio for both towns and The Boerne Life's food and business posts.
"""
import difflib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

UA = "nb-events-feed-bot/1.0"
API = "https://data.texas.gov/resource/"
PERMITS, HISTORY, TABC = "jrea-zgmq", "3kx8-uryv", "7hf9-qc9f"
PAGES = {"comptroller": "https://data.texas.gov/Government-and-Taxes/Active-Sales-Tax-Permit-Holders/jrea-zgmq",
         "tabc": "https://data.texas.gov/Government-and-Taxes/TABC-License-Information/7hf9-qc9f"}
DAYS, AHEAD, LIMIT = 120, 30, 12
CITY = {"new-braunfels": "NEW BRAUNFELS", "boerne": "BOERNE"}
NAICS = {"722511": "restaurant", "722513": "quick bite", "722515": "coffee & treats", "722410": "bar"}
TABC_TYPES = {"MB": "bar & restaurant", "BG": "restaurant", "BE": "restaurant", "BP": "brewpub"}
# Not somewhere you'd go "to try": hotels, venues, caterers, stores, institutions.
SKIP = re.compile(r"\b(suites|inn|hotel|marriott|hilton|hampton|holiday inn|catering|caterer|event (center|group)|"
                  r"venue|wedding|country club|golf club|hospital|church|grocery|market #|h-e-b|walmart|"
                  r"cinema|theat(er|re)s?|food service|jail|school|isd|vending)\b", re.I)
# Names that are only a company, not a place people would recognize.
ENTITY = re.compile(r"\b(holdings?|ventures?|enterprises?|group|partners|management|investments?|capital)\b|"
                    r"\b[a-z]+\d+\b", re.I)
CHAINS = re.compile(r"chipotle|little caesars|panda express|potbelly|whataburger|starbucks|mcdonald|wendy|taco bell|"
                    r"subway|domino|pizza hut|sonic|chick-fil-a|raising cane|jersey mike|dutch bros|7 brew|scooter|"
                    r"smoothie king|tropical smoothie|wingstop|popeyes|burger king|jack in the box|dairy queen|cava\b|"
                    r"crumbl|nothing bundt|bad ass coffee|marco'?s|papa john|jimmy john|firehouse|freddy|whataburger|"
                    r"dunkin|krispy|shipley|bahama buck|andy'?s|culver|in-n-out|torchy|kolache factory|tiff'?s", re.I)
SMALL = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "n", "'n", "y", "de", "la"}


def query(dataset, **params):
    url = API + dataset + ".json?" + urllib.parse.urlencode({"$" + k: v for k, v in params.items()})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
        return json.load(r)


def tidy(name):
    name = re.sub(r"\s+", " ", name).strip()
    for _ in range(2):      # "Los Jimadores Grill and Bar LLC #2", "Chipotle Mexican Grill #5720"
        name = re.sub(r"\s*#\s*\d+$", "", name).strip(" ,.-")
        name = re.sub(r",?\s*\b(l\.?l\.?c\.?|inc\.?|ltd\.?|limited liability company)$", "", name, flags=re.I).strip(" ,.-")
    letters = [c for c in name if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        words = name.lower().split(" ")
        name = " ".join(w if i and w in SMALL else w[:1].upper() + w[1:] for i, w in enumerate(words))
        name = re.sub(r"'S\b", "'s", name)
    return name


def tidy_address(a):
    a = re.sub(r"\s+", " ", a).strip()
    a = re.sub(r"\b(STE|Ste|Bldg|BLDG|Suite|Unit|#)\b.*$", "", a).strip(" ,")
    if a.isupper():
        a = " ".join(w if re.fullmatch(r"(FM|IH|TX|US|CR|SH|RR|[NSEW]|\d\w*)(-\d+)?", w) else w.capitalize() for w in a.split())
    return a


def addr_key(a):
    """'1310 E COMMON ST' and '1310 E. Common Street' -> '1310 common'."""
    words = re.sub(r"[^a-z0-9 ]", " ", a.lower()).split()
    drop = {"n", "s", "e", "w", "north", "south", "east", "west", "st", "street", "rd", "road", "ave", "avenue",
            "dr", "drive", "ln", "lane", "blvd", "pkwy", "parkway", "hwy", "highway", "way", "trl", "plz", "plaza"}
    words = [w for w in words if w not in drop]
    return " ".join(words[:3])


def name_key(n):
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", n.lower()).split() if w not in SMALL and w not in ("restaurant", "bar", "grill", "cafe")]
    return " ".join(words)


def same_place(a, b):
    """'Duesy's Tavern' = 'DUESY'S TAVERN'; 'Thai Racha' != 'Thai Pepper' (a shared cuisine word isn't enough)."""
    return bool(a and b) and (a == b or a.startswith(b) or b.startswith(a) or
                              difflib.SequenceMatcher(None, a, b).ratio() >= 0.8)


NEWS = {
    "new-braunfels": [("WhatNow San Antonio", "https://whatnow.com/wp-json/wp/v2/posts", {"search": "New Braunfels"}, r"New Braunfels|Gruene")],
    "boerne": [("The Boerne Life", "https://theboernelife.com/wp-json/wp/v2/posts", {"categories": "4,241"}, r""),
               ("WhatNow San Antonio", "https://whatnow.com/wp-json/wp/v2/posts", {"search": "Boerne"}, r"Boerne|Fair Oaks")],
}
FOOD = re.compile(r"restaurant|coffee|caf[eé]|bbq|barbe?cue|taco|pizz|sushi|\bbar\b|brew|bakery|do-?nut|kitchen|eatery|"
                  r"grill|sandwich|candy|sweet|dining|burger|chicken|wine|tea\b|ice cream|bistro|cantina|pub\b|food", re.I)
OPENING = re.compile(r"open|coming|plans?\b|debut|new\b|now serving|set to|arriv|expand", re.I)
NOT_NEWS = re.compile(r"thanksgiving|christmas|holiday|where to get|guide|things to do|closure|closing|closes", re.I)


def opening_news(city, today):
    """Up to 4 recent stories about food places opening in this town."""
    after = (today - timedelta(days=90)).isoformat() + "T00:00:00"
    items = []
    for source, api, params, where in NEWS.get(city, []):
        try:
            q = urllib.parse.urlencode({**params, "per_page": "30", "after": after, "_fields": "date,title,link"})
            with urllib.request.urlopen(urllib.request.Request(api + "?" + q, headers={"User-Agent": UA}), timeout=40) as r:
                posts = json.load(r)
        except Exception as ex:
            print(f"! {source}: {ex}")
            continue
        for p in posts:
            title = html.unescape(re.sub(r"<[^>]+>", "", p["title"]["rendered"])).strip()
            if (where and not re.search(where, title)) or not FOOD.search(title) or not OPENING.search(title) or NOT_NEWS.search(title):
                continue
            if any(title.lower() == i["title"].lower() for i in items):
                continue
            items.append({"title": title, "url": p["link"], "source": source, "date": p["date"][:10]})
    return sorted(items, key=lambda i: i["date"], reverse=True)[:4]


def main(path, apply=False):
    feed = json.load(open(path, encoding="utf-8"))
    city = (feed.get("city") or {}).get("id", "new-braunfels")
    if city not in CITY:
        return
    today = date.today()
    since, until = (today - timedelta(days=DAYS)).isoformat(), (today + timedelta(days=AHEAD)).isoformat()
    found = {}

    def add(name, kind, address, day, source):
        name, address = tidy(name), tidy_address(address)
        if not name or SKIP.search(name) or ENTITY.search(name):
            return
        key = addr_key(address) or name.lower()
        have = found.get(key)
        if have:
            have["sources"].add(source)
            have["since"] = min(have["since"], day) if day <= today.isoformat() else have["since"]
            if have["kind"] == "restaurant" and "bar" in kind: have["kind"] = kind
            return
        found[key] = {"name": name, "kind": kind, "address": address, "since": day, "sources": {source}}

    rows = query(PERMITS, select="outlet_name,outlet_address,outlet_naics_code,outlet_first_sales_date",
                 where=f"outlet_city='{CITY[city]}' AND outlet_first_sales_date between '{since}' and '{until}' "
                       f"AND outlet_naics_code in({','.join(NAICS)})",
                 order="outlet_first_sales_date DESC", limit="200")
    for r in rows:
        if re.search(r"\bAPT\b|\bUNIT\b", r.get("outlet_address", ""), re.I):
            continue        # run from a home address: not a place to visit
        add(r.get("outlet_name", ""), NAICS[str(r["outlet_naics_code"])], r.get("outlet_address", ""),
            r["outlet_first_sales_date"][:10], "comptroller")

    rows = query(TABC, select="trade_name,license_type,original_issue_date,address",
                 where=f"upper(city)='{CITY[city]}' AND license_type in({','.join(repr(t) for t in TABC_TYPES)}) "
                       f"AND original_issue_date > '{since}T00:00:00' AND license_status='Active'",
                 order="original_issue_date DESC", limit="60")
    for r in rows:
        add(r.get("trade_name", ""), TABC_TYPES[r["license_type"]], r.get("address", ""),
            r["original_issue_date"][:10], "tabc")

    # New owners at an old place: same address and same leading name word, selling before the window.
    history = query(HISTORY, select="loc_name,address_number,address_text,first_sale_date",
                    where=f"loc_city='{CITY[city]}' AND first_sale_date < '{since}' AND starts_with(naics, '722')",
                    limit="20000")
    older = {}
    for h in history:
        older.setdefault(addr_key(f"{h.get('address_number', '')} {h.get('address_text', '')}"), set()).add(name_key(tidy(h.get("loc_name", ""))))
    places = [p for p in found.values()
              if not any(same_place(name_key(p["name"]), o) for o in older.get(addr_key(p["address"]), set()))]
    for p in found.values():
        if p not in places: print(f"  skip (operating before): {p['name']} at {p['address']}")
    dropped = len(found) - len(places)

    # Locals first, then chains; newest first within each. Already-open places before "opening soon".
    places.sort(key=lambda p: (bool(CHAINS.search(p["name"])), p["since"] > today.isoformat(),
                               -int(p["since"].replace("-", ""))))
    opened = [p for p in places if p["since"] <= today.isoformat()]
    soon = sorted((p for p in places if p["since"] > today.isoformat() and not CHAINS.search(p["name"])),
                  key=lambda p: p["since"])
    out = []
    for p in soon[:3] + opened[:LIMIT]:
        srcs = sorted(p.pop("sources"))
        p["source"] = " and ".join({"comptroller": "Texas Comptroller sales-tax permits",
                                    "tabc": "Texas Alcoholic Beverage Commission"}[s] for s in srcs)
        p["url"] = PAGES[srcs[0]]
        out.append(p)
    for p in out:
        print(f"  {p['since']}  {p['name']:<36} {p['kind']:<16} {p['address']:<28} {p['source'][:20]}")
    print(f"{len(out)} new place(s) in {city} ({dropped} skipped as new owners at an existing place).")
    news = opening_news(city, today)
    for n in news:
        print(f"  news {n['date']}  {n['title'][:80]}  ({n['source']})")
    if apply:
        if out or not feed.get("newPlaces"):          # keep the last list if the sources are empty for a day
            feed["newPlaces"] = out
        if news or not feed.get("newPlacesNews"):
            feed["newPlacesNews"] = news
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(feed, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], apply="--apply" in sys.argv)
