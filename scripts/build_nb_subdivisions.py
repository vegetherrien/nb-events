#!/usr/bin/env python3
"""Builds New Braunfels neighborhoods from the city's official Subdivisions layer.

    python3 scripts/build_nb_subdivisions.py

Reads research/local/raw/subs-*.json (downloaded from the City of New Braunfels open data server,
OpenData/AddressesBoundaries/MapServer/8, with outSR=4326). Each plat carries a NeighborhoodName;
plats are grouped by that name. Writes research/local/communities-city.json and gives the
hand-researched neighborhoods in communities.json their official outlines where the names match.
"""
import glob, json, re
from collections import defaultdict

SKIP = re.compile(r"commercial|office|business|industrial|reserve\b|retail|center\b|centre|plaza|shopping|"
                  r"addition\b|\bpark$|storage|church|school|hospital|medical|airport|\bisd\b|city of|subdivision no|"
                  r"\bunit\b|\blot\b|tract|survey|replat|\bhwy\b|highway|\bih\b|loop 337|fm \d", re.I)
MIN_LOTS, MIN_PLATS = 40, 5


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def lots(a):
    try: return int(float(a.get("ResidentialLotNo") or 0))
    except (TypeError, ValueError): return 0


def area(ring):
    return abs(sum(ring[i][1] * ring[(i + 1) % len(ring)][0] - ring[(i + 1) % len(ring)][1] * ring[i][0]
                   for i in range(len(ring)))) / 2


def main():
    groups = defaultdict(lambda: {"plats": 0, "lots": 0, "rings": []})
    for path in sorted(glob.glob("research/local/raw/subs-*.json")):
        for f in json.load(open(path))["features"]:
            a = f["attributes"]
            name = re.sub(r"\s+", " ", (a.get("NeighborhoodName") or a.get("PlatName") or "")).strip().title()
            name = re.sub(r"\bAt\b", "at", name).replace("'S", "'s")
            if not name: continue
            g = groups[name]
            g["plats"] += 1; g["lots"] += lots(a)
            for ring in (f.get("geometry") or {}).get("rings", []):
                pts = []
                for x, y in ring:                                             # the server gives lon, lat
                    p = [round(y, 4), round(x, 4)]                            # 4 decimals is about 10 m
                    if not pts or pts[-1] != p: pts.append(p)
                if len(pts) >= 4 and area(pts) > 2e-8:                        # drop slivers (about 200 m²)
                    g["rings"].append(pts)

    keep = {n: g for n, g in groups.items()
            if g["rings"] and not SKIP.search(n) and (g["lots"] >= MIN_LOTS or (g["plats"] >= MIN_PLATS and g["lots"] >= 15))}   # skips one-family plats

    # Hand-researched neighborhoods keep their websites and calendars; give them the official outline.
    hand_path = "research/local/communities.json"
    hand = json.load(open(hand_path))
    used = set()
    for f in hand["features"]:
        names = [p.strip() for p in re.split(r"/|\(", f["name"])] + [f["name"]]
        match = next((n for n in groups if any(slug(n) == slug(x) for x in names) and groups[n]["rings"]), None)
        if match:
            f["polygons"], f["confidence"] = groups[match]["rings"], "official-gis"
            used.add(match)
    json.dump(hand, open(hand_path, "w"), ensure_ascii=False)

    taken = {f["id"] for f in hand["features"]}
    feats = []
    for n in sorted(keep):
        if n in used or slug(n) in taken: continue
        feats.append({"layer": "community", "id": slug(n), "name": n, "tags": ["community:" + slug(n)],
                      "polygons": keep[n]["rings"], "confidence": "official-gis",
                      "info": {"status": "none"}})
    json.dump({"source": "City of New Braunfels open data, Subdivisions layer", "features": feats},
              open("research/local/communities-city.json", "w"), ensure_ascii=False)
    pts = sum(len(r) for f in feats for r in f["polygons"])
    print(f"{len(groups)} names in the city layer; {len(feats)} new neighborhoods ({pts} points); "
          f"{len(used)} hand-researched ones got official outlines: {sorted(used)}")


if __name__ == "__main__":
    main()
