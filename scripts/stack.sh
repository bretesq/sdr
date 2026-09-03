#!/usr/bin/env bash
# One view over both halves of the console.
#
# The supervised half runs under compose; op25, the recorders and the HackRFs
# run on the host. Reporting only the containers is how a dead transcription
# watcher goes unnoticed for hours, so `status` always prints both.
set -euo pipefail
# readlink -f resolves BASH_SOURCE through any symlinks before we derive the
# repo root from it. Without this, symlinking stack.sh into a PATH directory
# (an ordinary way to make an ops script runnable from anywhere) makes $R the
# symlink's own directory instead of the repo's: docker compose then finds no
# compose file there and status falsely reports "compose is not running" —
# or worse, opens whatever sdr.db happens to sit next to the symlink. A
# status view reached through a symlink must still report on the repo it
# belongs to, not on wherever it was invoked from.
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
R="$(cd "$(dirname "$SELF")/.." && pwd)"
cd "$R"

host_status() {
  echo "HOST"

  # Match on the process NAME, never on the full argv.
  #
  # `pgrep -f "multi_rx.py"` matches its own command line, so it reports the
  # radio as running whenever this script is the only thing running. That bug
  # cost this project a stalled overnight harvest and, later, a status report
  # that claimed a capture was live twenty-five minutes after op25 had died.
  if ps -eo comm,args --no-headers | awk '$1=="python3" && /multi_rx/' | grep -q .; then
    echo "  op25          running"
  else
    echo "  op25          stopped"
  fi

  printf '  recorders     %s\n' "$(ps -eo args --no-headers | grep -c '[u]dp_audio_record' || true)"

  python3 - <<'PY'
import sqlite3, time
# The try covers connect() AND both queries. A query-level failure (schema
# drift, a renamed/missing column, a locked or mid-checkpoint database) is
# just as much "the corpus can't be read right now" as a connect failure, and
# under `set -euo pipefail` an uncaught exception here would kill the whole
# script mid-report — after CONTAINERS/op25/recorders have already printed —
# leaving a truncated status plus a raw traceback that reads like missing
# services rather than a crashed status script.
try:
    c = sqlite3.connect('file:sdr.db?mode=ro', uri=True).cursor()
    t = c.execute('SELECT MAX(start) FROM calls').fetchone()[0]
    tot, tr = c.execute(
        "SELECT COUNT(*), SUM(CASE WHEN transcript IS NOT NULL "
        "AND LENGTH(TRIM(transcript)) > 0 THEN 1 ELSE 0 END) "
        "FROM calls WHERE start > strftime('%s','now','-30 minutes')").fetchone()
except Exception as e:
    print(f"  corpus        unreadable ({e})")
    raise SystemExit(0)
if t is None:
    print("  newest call   none")
else:
    print(f"  newest call   {(time.time() - t) / 60:.0f} min ago")
print(f"  transcripts   {tr or 0}/{tot} in the last 30 min")
PY
}

case "${1:-status}" in
  up)     docker compose up -d && echo && host_status ;;
  down)   docker compose down ;;
  status)
    echo "CONTAINERS"
    docker compose ps --format '  {{.Service}}\t{{.Status}}' 2>/dev/null \
      || echo "  compose is not running"
    echo
    host_status
    ;;
  *) echo "usage: $0 {up|down|status}" >&2; exit 1 ;;
esac
