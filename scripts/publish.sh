#!/bin/sh
# Copies the app's feeds into this repo, rebuilds the calendars and pushes changed files.
# Usage: scripts/publish.sh <path to NBEvents> "<commit message>"
set -e
N="$1"; MSG="$2"; cd "$(dirname "$0")/.."
cp "$N/feed/events.json" events.json; cp "$N/feed/local.json" local.json
cp "$N/feed/boerne-events.json" boerne/events.json; cp "$N/feed/boerne-local.json" boerne/local.json
cp "$N"/scripts/*.py scripts/   # run this copy (in the feed repo), not one inside the app project
# Stop before anything is pushed if either feed fails its checks.
python3 scripts/validate_feed.py events.json >/dev/null || { echo "events.json failed validation; nothing published"; python3 scripts/validate_feed.py events.json | tail -5; exit 1; }
python3 scripts/validate_feed.py boerne/events.json >/dev/null || { echo "boerne/events.json failed validation; nothing published"; python3 scripts/validate_feed.py boerne/events.json | tail -5; exit 1; }
python3 scripts/build_ics.py events.json nb-events.ics 2>/dev/null
python3 scripts/build_ics.py boerne/events.json boerne/events.ics 2>/dev/null
export GH_TOKEN=$(gh auth token -u vegetherrien)
for f in events.json local.json nb-events.ics boerne/events.json boerne/local.json boerne/events.ics privacy.html deal.html stats.html scripts/*.py scripts/publish.sh; do
  # Look up the current file (retry: right after wake the network can be flaky). A 404 means a new file.
  sha=""; for try in 1 2 3 4 5; do
    out=$(gh api "repos/vegetherrien/nb-events/contents/$f" --jq .sha 2>&1) && { sha=$out; break; }
    case "$out" in *"Not Found"*|*404*) break ;; esac
    sleep $((try * 10))
  done
  local_sha=$(git hash-object "$f")
  [ "$sha" = "$local_sha" ] && continue
  python3 - "$f" "$sha" "$MSG" > .body.json <<'P'
import base64, json, sys
f, sha, msg = sys.argv[1], sys.argv[2], sys.argv[3]
b = {"message": msg, "content": base64.b64encode(open(f, "rb").read()).decode()}
if sha and not sha.startswith("{"): b["sha"] = sha
print(json.dumps(b))
P
  gh api -X PUT "repos/vegetherrien/nb-events/contents/$f" --input .body.json --jq '.content.path'
done
rm -f .body.json events.json
