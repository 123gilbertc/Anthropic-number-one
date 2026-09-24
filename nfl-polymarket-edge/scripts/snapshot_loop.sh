#!/usr/bin/env sh
# Record Polymarket NFL prices on a fixed cadence so edge and closing-line value can be measured
# on real fills instead of assumptions. Run under docker-compose (service nfl-edge-snapshotter) or cron.
set -eu
INTERVAL="${SNAPSHOT_INTERVAL_SECONDS:-900}"
while true; do
  python -m nfl_edge.cli snapshot || echo "snapshot failed at $(date -u +%FT%TZ)" >&2
  sleep "$INTERVAL"
done
