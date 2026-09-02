#!/usr/bin/env bash
# One view over both halves of the console.
#
# The supervised half runs under compose; op25, the recorders and the HackRFs
# run on the host. Reporting only the containers is how a dead transcription
# watcher goes unnoticed for hours, so `status` always prints both.
set -euo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
try:
    c = sqlite3.connect('file:sdr.db?mode=ro', uri=True).cursor()
except Exception as e:
    print(f"  corpus        unreadable ({e})")
    raise SystemExit(0)
t = c.execute('SELECT MAX(start) FROM calls').fetchone()[0]
if t is None:
    print("  newest call   none")
else:
    print(f"  newest call   {(time.time() - t) / 60:.0f} min ago")
tot, tr = c.execute(
    "SELECT COUNT(*), SUM(CASE WHEN transcript IS NOT NULL "
    "AND LENGTH(TRIM(transcript)) > 0 THEN 1 ELSE 0 END) "
    "FROM calls WHERE start > strftime('%s','now','-30 minutes')").fetchone()
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
