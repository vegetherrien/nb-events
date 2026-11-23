#!/usr/bin/env python3
"""Builds Corinth's area research files from the city's public map layers.

    python3 scripts/build_corinth_areas.py

Reads research/local/corinth/raw/*.json|geojson (saved with outSR=4326):
- City of Corinth GIS, CorServices/ExtraLayers/FeatureServer: 2 Subdivisions, 3 TrashPickupDay,
  4 School District (the Denton ISD / Lake Dallas ISD split inside the city), 6 City Limits.
- Texas Education Agency School_Districts__2026 (whole-district outlines).
Writes city.json, schools.json and communities-city.json next to raw/. merge_local.py turns
those into feed/corinth-local.json.
"""
import json, re
from collections import defaultdict

F = "research/local/corinth/"
SKIP = re.compile(r"commercial|office|business|industrial|retail|center\b|centre|plaza|shopping|storage|church|"
                  r"school|hospital|medical|\bisd\b|city of|addition\b|\bpark$|\bunit\b|tract|survey|replat|"
                  r"\bbank\b|station|pharmacy|dental|clinic|\binn\b|hotel|restaurant|college|nctc|\blot\b|"
                  r"walmart|albertson|kroger|\bih\b|i-35|fm ?2181|fm ?2499|professional|self|auto|marine|"
                  r"veterinar|daycare|academy|apartments?|senior living|assisted|mini|warehouse|corner|market|"
                  r"\bford\b|public works|co-?op|\bcvs\b|huffines|35e|teasley crossing|millennium|parkway district|eblount", re.I)
MIN_SQFT = 12 * 43560          # about 12 acres: skips one-building plats


def rings_esri(geom, digits=5):
    out = []
    for ring in (geom or {}).get("rings", []):
        pts = []
        for x, y in ring:
            p = [round(y, digits), round(x, digits)]
            if not pts or pts[-1] != p: pts.append(p)
        if len(pts) >= 4: out.append(pts)
    return out


def rings_geojson(geom, digits=5):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [[[round(y, digits), round(x, digits)] for x, y in poly[0]] for poly in polys]


def simplify(ring, tol=0.0004):
    """Douglas-Peucker; tol in degrees (0.0004 is about 40 m). District outlines don't need more."""
    def dist(p, a, b):
        (y, x), (y1, x1), (y2, x2) = p, a, b
        dx, dy = x2 - x1, y2 - y1
        if dx == dy == 0: return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
        t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        return ((x - x1 - t * dx) ** 2 + (y - y1 - t * dy) ** 2) ** 0.5
    keep = [False] * len(ring); keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        a, b = stack.pop()
        worst, at = 0, None
        for i in range(a + 1, b):
            d = dist(ring[i], ring[a], ring[b])
            if d > worst: worst, at = d, i
        if at is not None and worst > tol:
            keep[at] = True; stack += [(a, at), (at, b)]
    return [p for p, k in zip(ring, keep) if k]


def load(name):
    return json.load(open(F + "raw/" + name))


def main():
    city = rings_esri(load("city-limits.json")["features"][0]["geometry"])
    split = {f["attributes"]["SCHOOLDIST"]: rings_esri(f["geometry"]) for f in load("school-district.json")["features"]}
    ld = [simplify(r, 0.0002) for r in rings_geojson(load("district-lake-dallas.geojson")["features"][0]["geometry"])]
    den = [simplify(r) for r in rings_geojson(load("district-denton.geojson")["features"][0]["geometry"])]
    den_corinth = split["Denton ISD"]

    # --- city.json: limits and trash days (Community Waste Disposal; recycling the same day) ---
    feats = [{"layer": "city", "id": "city-corinth", "name": "City of Corinth", "tags": ["city:corinth"],
              "polygons": city, "confidence": "official-gis", "info": {"url": "https://www.corinthtx.gov/"}}]
    for f in sorted(load("trash.json")["features"], key=lambda f: int(f["attributes"]["DayOrder"])):
        day = f["attributes"]["PickupDay"]
        feats.append({"layer": "trash", "id": "trash-" + day[:3].lower(), "name": f"{day} trash & recycling",
                      "tags": ["trash:" + day[:3].lower()], "polygons": rings_esri(f["geometry"]),
                      "confidence": "official-gis",
                      "info": {"trash": day, "day": f"{day} · recycling the same day",
                               "url": "https://experience.arcgis.com/experience/b102a62340ba4fbeb0f6a4c82b7ed6f4"}})
    json.dump({"source": "City of Corinth GIS (City Limits, TrashPickupDay)", "features": feats},
              open(F + "city.json", "w"), ensure_ascii=False)

    # --- schools.json ---
    LD, DN = "district:lake-dallas-isd", "district:denton-isd"
    RANK = "https://app.rankone.com/Schedules/View_Schedule_All_Web.aspx?P=0&D={}&S={}&Mt=0"

    def school(layer, sid, name, short, mascot, addr, url, cal, district, polys, conf, athletics=None):
        info = {"short": short, "mascot": mascot, "address": addr, "url": url, "calendar": cal}
        if athletics: info["athletics"] = athletics
        return {"layer": layer, "id": sid, "name": name, "tags": ["school:" + sid, district], "polygons": polys,
                "confidence": conf, "info": info}

    schools = [
        {"layer": "district", "id": "lake-dallas-isd", "name": "Lake Dallas Independent School District", "tags": [LD],
         "polygons": ld, "confidence": "official-gis",
         "info": {"short": "Lake Dallas ISD", "url": "https://www.ldisd.net/", "address": "104 Swisher Rd, Lake Dallas, TX 75065"}},
        {"layer": "district", "id": "denton-isd", "name": "Denton Independent School District", "tags": [DN],
         "polygons": den, "confidence": "official-gis",
         "info": {"short": "Denton ISD", "url": "https://www.dentonisd.org/", "calendar": "https://www.dentonisd.org/fs/elements/73575", "address": "1307 N Locust St, Denton, TX 76201"}},
        # Lake Dallas ISD has one high school and one middle school, so the district outline is their zone.
        school("school-hs", "lake-dallas-hs", "Lake Dallas High School", "Lake Dallas", "Falcons",
               "3016 Parkridge Dr, Corinth, TX 76210", "https://ldhs.ldisd.net/", "https://ldhs.ldisd.net/fs/elements/25048",
               LD, ld, "official-gis", RANK.format("49C22C3B-F8C8-4316-BB3C-D8F9A4CBD557", 1245)),
        school("school-ms", "lake-dallas-ms", "Lake Dallas Middle School", "Lake Dallas MS", "Falcons",
               "325 E Hundley Dr, Lake Dallas, TX 75065", "https://ldms.ldisd.net/", "https://ldms.ldisd.net/fs/elements/28011",
               LD, ld, "official-gis"),
        # Its three elementary zones aren't published as map data: choose yours by hand.
        school("school-es", "corinth-es", "Corinth Elementary", "Corinth", "Cougars",
               "3501 Cliff Oaks Dr, Corinth, TX 76210", "https://ce.ldisd.net/", "https://ce.ldisd.net/fs/elements/20117", LD, [], "pick-only"),
        school("school-es", "lake-dallas-es", "Lake Dallas Elementary", "Lake Dallas", "Falcons",
               "401 Main St, Lake Dallas, TX 75065", "https://lde.ldisd.net/", "https://lde.ldisd.net/fs/elements/22235", LD, [], "pick-only"),
        school("school-es", "shady-shores-es", "Shady Shores Elementary", "Shady Shores", "Sharks",
               "300 Dobbs Rd, Corinth, TX 76210", "https://sse.ldisd.net/", "https://sse.ldisd.net/fs/elements/27588", LD, [], "pick-only"),
        # The Denton ISD side of Corinth (the city's School District layer). The campus list is the
        # usual feeder for that side; boundaries can shift, so these stay changeable in the app.
        school("school-hs", "guyer-hs", "Guyer High School", "Guyer", "Wildcats",
               "7501 Teasley Ln, Denton, TX 76210", "https://guyerhs.dentonisd.org/", "https://guyerhs.dentonisd.org/fs/elements/17440",
               DN, den_corinth, "approximate", RANK.format("730FD002-2D34-4C63-8FFD-03CCDD940EFB", 54)),
        school("school-ms", "crownover-ms", "Crownover Middle School", "Crownover", "Cougars",
               "1901 Creekside Dr, Corinth, TX 76210", "https://crownoverms.dentonisd.org/",
               "https://crownoverms.dentonisd.org/fs/elements/11464", DN, den_corinth, "approximate"),
        school("school-es", "hawk-es", "Hawk Elementary", "Hawk", "Hawks",
               "2300 Oakmont Dr, Corinth, TX 76210", "https://hawkes.dentonisd.org/", "https://hawkes.dentonisd.org/fs/elements/18934",
               DN, den_corinth, "approximate"),
        school("school-es", "nelson-es", "Nelson Elementary", "Nelson", "Mustangs",
               "3909 Teasley Ln, Denton, TX 76210", "https://nelsones.dentonisd.org/", "https://nelsones.dentonisd.org/fs/elements/24910",
               DN, [], "pick-only"),
        school("school-es", "mcnair-es", "McNair Elementary", "McNair", "Mustangs",
               "1212 Hickory Creek Rd, Denton, TX 76210", "https://mcnaires.dentonisd.org/", "https://mcnaires.dentonisd.org/fs/elements/22669",
               DN, [], "pick-only"),
    ]
    json.dump({"source": "TEA district outlines; City of Corinth School District layer; district websites",
               "features": schools}, open(F + "schools.json", "w"), ensure_ascii=False)

    # --- communities-city.json: residential subdivisions, phases merged by name ---
    groups = defaultdict(lambda: {"rings": [], "area": 0.0})
    for f in load("subs-0.json")["features"]:
        a = f["attributes"]
        raw = re.sub(r"\s+", " ", a.get("Name") or "").strip()
        name = re.sub(r"\s+(PH(ASE)?\.?|SEC(TION)?\.?|UNIT|INST(ALLMENT)?\.?|ADDN?\.?|ADDITION|NO\.?|#)\s*[0-9IVX]+[A-Z]?\b.*$", "", raw, flags=re.I)
        name = re.sub(r"\s+(I{1,3}|IV|V|[0-9]+|[A-D])$", "", name)
        name = re.sub(r"\s+(SUBDIVISION|ADDN?\.?|ADDITION|ACRE LOTS|RESIDENTIAL)$", "", name, flags=re.I)
        name = re.sub(r"\bEST\.?$", "Estates", name, flags=re.I).strip(" ,-").title().replace("'S", "'s")
        name = re.sub(r"\b(At|Of|The|On)\b", lambda m: m.group(0).lower(), name)
        name = name[:1].upper() + name[1:]
        if not name: continue
        groups[name]["rings"] += rings_esri(f["geometry"], 4)
        groups[name]["area"] += a.get("Shape__Area") or 0
    feats = [{"layer": "community", "id": re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-"), "name": n,
              "tags": ["community:" + re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")], "polygons": g["rings"],
              "confidence": "official-gis", "info": {"status": "none"}}
             for n, g in sorted(groups.items()) if g["rings"] and g["area"] >= MIN_SQFT and not SKIP.search(n)]
    json.dump({"source": "City of Corinth GIS, Subdivisions layer", "features": feats},
              open(F + "communities-city.json", "w"), ensure_ascii=False)
    print(f"city + {len(load('trash.json')['features'])} trash zones; {len(schools)} school features "
          f"(Denton ISD outline {sum(len(r) for r in den)} pts); {len(feats)} of {len(groups)} subdivisions kept")
    print(", ".join(f["name"] for f in feats))


if __name__ == "__main__":
    main()
