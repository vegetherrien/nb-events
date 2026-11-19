#!/usr/bin/env python3
"""Copies a city's restaurant picks (research/eats/<city>.json) into its feed as "eats".

    python3 scripts/merge_eats.py feed/events.json research/eats/new-braunfels.json --apply

Picks are popular places from local best-of lists, plus labeled sponsored spots. The app shows
the ones within walking distance of an event's venue. Edit the research file, not the feed.
"""
import json, sys

def main(feed_path, picks_path, apply):
    picks = json.load(open(picks_path))
    for p in picks['places']:
        assert p.get('name') and p.get('kind'), p
        if p.get('sponsored'):
            assert p.get('text') and 'lat' in p and 'lon' in p, 'sponsored spots need text and a location: ' + p['name']
    feed = json.load(open(feed_path))
    feed['eats'] = {'note': picks['note'], 'places': picks['places']}
    if apply:
        json.dump(feed, open(feed_path, 'w'), ensure_ascii=False, indent=1)
        open(feed_path, 'a').write('\n')
    print('eats', len(picks['places']), 'sponsored', sum(1 for p in picks['places'] if p.get('sponsored')))

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], '--apply' in sys.argv)
