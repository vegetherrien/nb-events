#!/usr/bin/env python3
"""Builds a city's local.json from its research files and adds neighborhood events to its feed.

    python3 scripts/merge_local.py research/local feed/local.json feed/events.json
    python3 scripts/merge_local.py research/local/boerne feed/boerne-local.json feed/boerne-events.json

Every *.json in the research folder with a "features" list goes into local.json. Events come only
from communities-events.json; school events are added by scrape_schools.py instead.
"""
import glob, json, os, sys
from datetime import date

KEEP = ['short', 'mascot', 'url', 'calendar', 'athletics', 'address', 'member', 'day', 'status']
ORDER = ['communities', 'nbisd', 'comal-isd', 'boerne-isd', 'city']


def main(research, local_out, events_path):
    files = sorted(glob.glob(os.path.join(research, '*.json')),
                   key=lambda p: next((i for i, n in enumerate(ORDER) if os.path.basename(p).startswith(n)), 99))
    feats = []
    for path in files:
        for f in json.load(open(path)).get('features', []):
            i = dict(f.get('info', {}))
            if f['layer'] == 'council' and not i.get('short'):
                i['short'] = f['name'].replace('Council ', '')
            if f['layer'] == 'trash' and not i.get('day') and i.get('trash_day'):
                rec = i['recycling_day'].split(',')[0]
                i['day'] = f"{i['trash_day'].split(' ')[0]} · recycling {rec}s, Week {i['week']}"
            if f['layer'] == 'trash' and i.get('day'):
                i['short'] = i['day']
            if f['layer'] == 'city':
                i['short'] = 'Inside city limits'
            f['info'] = {k: i[k] for k in KEEP if i.get(k)}
            digits = 4 if f['layer'] == 'community' else 5      # neighborhoods don't need metre precision
            f['polygons'] = [[[round(p[0], digits), round(p[1], digits)] for p in r] for r in f['polygons'] if len(r) >= 4]
            feats.append(f)
    ids = [f['id'] for f in feats]
    assert len(ids) == len(set(ids)), 'duplicate feature ids'
    json.dump({'version': 1, 'updated': date.today().isoformat(), 'features': feats}, open(local_out, 'w'),
              ensure_ascii=False, separators=(',', ':'))

    feed = json.load(open(events_path))
    cats = {c['id'] for c in feed['categories']}
    for c in feed['categories']:
        if c['id'] == 'hood': c['name'] = 'My neighborhood'
    for new in ({'id': 'hood', 'name': 'My neighborhood', 'light': '#3B7833', 'dark': '#82C676', 'symbol': 'house.fill'},
                {'id': 'school', 'name': 'My schools', 'light': '#1C5CA3', 'dark': '#84B3EE', 'symbol': 'graduationcap.fill'}):
        if new['id'] not in cats:
            feed['categories'].insert(0 if new['id'] == 'hood' else 1, new); cats.add(new['id'])
    have = {e['id'] for e in feed['events']}
    seen = {(e['title'].lower(), e['startDate']) for e in feed['events']}
    added = 0
    src = os.path.join(research, 'communities-events.json')
    for e in (json.load(open(src))['events'] if os.path.exists(src) else []):
        e['id'] = e['id'][:80]
        if e['id'] in have or (e['title'].lower(), e['startDate']) in seen: continue
        if e['category'] not in cats: sys.exit('unknown category ' + e['category'])
        if e.get('audience') == 'local': e['category'] = 'hood'     # resident-only: file under My neighborhood
        feed['events'].append(e); have.add(e['id']); added += 1
    feed['events'].sort(key=lambda e: (e['startDate'], e.get('startTime') or ''))
    feed['updated'] = date.today().isoformat()
    json.dump(feed, open(events_path, 'w'), ensure_ascii=False, indent=1)
    open(events_path, 'a').write('\n')
    print('features', len(feats), 'events added', added, 'total', len(feed['events']))


if __name__ == '__main__':
    main(*sys.argv[1:4])
