#!/usr/bin/env python3
"""Builds feed/local.json from research/local/*.json and merges the local events into feed/events.json."""
import json, re, sys
R = 'research/local/'
KEEP = ['short', 'mascot', 'url', 'calendar', 'athletics', 'address', 'member', 'day', 'status']
feats = []
for name in ['communities', 'nbisd', 'comal-isd', 'city']:
    for f in json.load(open(R + name + '.json'))['features']:
        i = f.get('info', {})
        if f['layer'] == 'council':
            i['short'] = f['name'].replace('Council ', '')
        if f['layer'] == 'trash':
            rec = i['recycling_day'].split(',')[0]
            i['short'] = i['day'] = f"{i['trash_day'].split(' ')[0]} · recycling {rec}s, Week {i['week']}"
        if f['layer'] == 'city':
            i['short'] = 'Inside city limits'
        f['info'] = {k: i[k] for k in KEEP if i.get(k)}
        f['polygons'] = [[[round(p[0], 5), round(p[1], 5)] for p in r] for r in f['polygons'] if len(r) >= 4]
        feats.append(f)
ids = [f['id'] for f in feats]
assert len(ids) == len(set(ids)), 'duplicate feature ids'
json.dump({'version': 1, 'updated': '2026-11-18', 'features': feats}, open('feed/local.json', 'w'),
          ensure_ascii=False, separators=(',', ':'))

feed = json.load(open('feed/events.json'))
cats = {c['id'] for c in feed['categories']}
for c in feed['categories']:
    if c['id'] == 'hood': c['name'] = 'My neighborhood'
if 'school' not in cats:
    feed['categories'].insert(1, {'id': 'school', 'name': 'My schools', 'light': '#1C5CA3', 'dark': '#84B3EE',
                                  'symbol': 'graduationcap.fill'})
    cats.add('school')
have = {e['id'] for e in feed['events']}
seen = {(e['title'].lower(), e['startDate']) for e in feed['events']}
added = 0
for name in ['nbisd-events', 'comal-isd-events', 'communities-events']:
    for e in json.load(open(R + name + '.json'))['events']:
        if e['id'] in have or (e['title'].lower(), e['startDate']) in seen: continue
        if e['category'] not in cats: sys.exit('unknown category ' + e['category'])
        if 'community:gruene' in e.get('tags', []): e.pop('audience', None)   # Gruene is for everyone
        elif e['category'] != 'school': e['category'] = 'hood'
        e['id'] = e['id'][:80]
        feed['events'].append(e); have.add(e['id']); added += 1
feed['events'].sort(key=lambda e: (e['startDate'], e.get('startTime') or ''))
json.dump(feed, open('feed/events.json', 'w'), ensure_ascii=False, indent=1)
open('feed/events.json', 'a').write('\n')
print('features', len(feats), 'events added', added, 'total', len(feed['events']))
