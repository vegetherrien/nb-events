# nb-events

The event feed for the **NB Events** iPhone app (New Braunfels, TX).

The app downloads `events.json` from this repo when it opens and on pull to refresh, so changes here show up on phones without an app update. GitHub caches the file for about five minutes.

## Calendar subscription

Anyone can subscribe to the events in Apple or Google Calendar with this link:
`webcal://vegetherrien.github.io/nb-events/nb-events.ics`

After editing `events.json`, rebuild the calendar file so it stays in sync:
`python3 scripts/build_ics.py events.json nb-events.ics`, then commit both.

## Editing

1. Open `events.json` and click the pencil icon to edit.
2. Add or change events, then bump `"updated"` at the top to today's date.
3. Commit. That's it.

Each event needs `id`, `category`, `title` and `startDate`. Times are Central, 24 hour (`"18:00"`). Keep an event's `id` the same once it's published, because saved events and reminders use it.

Make sure the file is still valid JSON before committing (a missing comma breaks the feed, and the app falls back to its built-in copy).

## Finding new events

`scripts/scrape.py` reads the public calendar feeds from Gruene Hall, Brauntex Theatre, Gruene,
Castell St. Market and Veramendi, and lists anything not already in `events.json`:

    python3 scripts/scrape.py events.json            # preview
    python3 scripts/scrape.py events.json --apply    # add them
    python3 scripts/validate_feed.py events.json
    python3 scripts/build_ics.py events.json nb-events.ics

Daytime Gruene Hall sets and dance lessons are skipped on purpose (they're in the weekly notes).
