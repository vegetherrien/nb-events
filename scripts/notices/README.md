# Notice collectors

Each file here is one collector module for the "Good to know" card in the app. The runner
(`scripts/scrape_notices.py`) imports every module and calls `collect(city_id)`.

## Contract

```python
def collect(city_id: str) -> list[dict]:
    """city_id is "new-braunfels" or "boerne". Return [] when there is nothing to say or a source fails."""
```

Each notice is a dict:

| key | required | meaning |
|---|---|---|
| `id` | yes | stable, unique, lowercase-hyphenated, e.g. `nb-water-stage` |
| `kind` | yes | one of `water`, `burn-ban`, `fireworks`, `pollen`, `school`, `trash`, `road`, `agenda`, `pets`, `score`, `opening` |
| `title` | yes | short headline, at most 60 characters, plain words ("Stage 2 watering rules") |
| `detail` | yes | one or two plain sentences a resident can act on, at most 220 characters |
| `level` | yes | `info`, `warn` or `alert` |
| `url` | yes | the official page the fact came from |
| `source` | yes | who published it ("New Braunfels Utilities") |
| `asOf` | yes | `YYYY-MM-DD`, the day the fact was read from the source |
| `until` | no | `YYYY-MM-DD`; the app hides the notice after this day |
| `tags` | no | home-area tags it applies to, e.g. `["school:canyon-hs"]`, `["district:nbisd"]`, `["trash:12"]`, `["council:3"]`. Tag ids are in `feed/local.json` and `feed/boerne-local.json`. |
| `audience` | no | `"local"`: show only to people whose home area has one of `tags` |

## Rules

- Never invent a fact. Every notice comes from a page or API the collector read in that run.
- Standard library only (urllib, json, re, html, datetime, xml). No third-party packages.
- Use `User-Agent: nb-events-feed-bot/1.0`. If a source refuses that client, do not disguise
  the client; leave the source out and say so in the notes file.
- One failing source must not break the others: wrap each source in try/except and return what worked.
- A "nothing to report" state (no burn ban, no closures) returns no notice for that topic,
  unless residents would want to know the all-clear (then `level: "info"`).
- Run standalone for testing: `python3 scripts/notices/<module>.py new-braunfels` should print the notices as JSON.
