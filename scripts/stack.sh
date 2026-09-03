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
  #
  # op25 now runs two possible places -- directly on the host (the original
  # setup) or inside the `capture` container (server/utils/processes.ts
  # delegates there once it can no longer reach the HackRFs itself) -- and
  # BOTH are visible right here: the host's own /proc always sees every
  # process on the machine regardless of which container's PID namespace it
  # lives in, `capture` having no `pid: host` of its own notwithstanding. A
  # single "running"/"stopped" line would double-count or silently misattribute
  # which half actually owns the radio, so each matching pid is resolved to
  # host or container by checking whether its cgroup names a docker container
  # (`/proc/<pid>/cgroup` contains a `docker-<id>.scope` or `/docker/<id>`
  # segment for a containerised process; a bare host process has neither).
  op25_pids="$(ps -eo pid,comm,args --no-headers | awk '$2=="python3" && /multi_rx/ {print $1}')"
  if [ -z "$op25_pids" ]; then
    echo "  op25          stopped"
  else
    host_n=0
    container_n=0
    for pid in $op25_pids; do
      if grep -Eq 'docker[-/]' "/proc/$pid/cgroup" 2>/dev/null; then
        container_n=$((container_n + 1))
      else
        host_n=$((host_n + 1))
      fi
    done
    if [ "$container_n" -gt 0 ] && [ "$host_n" -gt 0 ]; then
      echo "  op25          running (${container_n} in the capture container, ${host_n} on the host -- unexpected, check both)"
    elif [ "$container_n" -gt 0 ]; then
      echo "  op25          running (in the capture container)"
    else
      echo "  op25          running (on the host)"
    fi
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
  # The command manual recovery actually reaches for. Recovery here is
  # deliberately manual — Docker restart policies fire on container EXIT, not
  # on an `unhealthy` health status, so nothing brings whisper back on its
  # own when it wedges (see docker-compose.yml's comment on stt-watch). This
  # verb is the safe, named thing to run instead: `docker compose restart`
  # stops and starts the container(s) IN PLACE, on the same compose-managed
  # network (rtl-console_default) and with the same config — unlike
  # scripts/stt_server.sh's `restart`, which `docker rm -f`s the container and
  # `docker run`s a replacement with no --network at all, silently dropping
  # it onto the default bridge where `http://whisper:8081` no longer resolves
  # for stt-watch or web. Takes an optional service name (e.g. `whisper` or
  # `stt-watch`); with none, every compose service restarts.
  restart)
    if [ -n "${2:-}" ]; then
      docker compose restart "$2"
    else
      docker compose restart
    fi
    ;;
  status)
    echo "CONTAINERS"
    docker compose ps --format '  {{.Service}}\t{{.Status}}' 2>/dev/null \
      || echo "  compose is not running"
    echo
    host_status
    ;;
  *) echo "usage: $0 {up|down|restart [service]|status}" >&2; exit 1 ;;
esac
