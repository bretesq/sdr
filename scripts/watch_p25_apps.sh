#!/usr/bin/env bash
# Emit a line ONLY when the packet-data decode shows something new.
#
# Why not tail the op25 log directly: op25's own `PDU: fmt=` line comes from
# its rate-1/2-only path, so for a confirmed data packet it always reports
# blks=0 and carries NO payload. Tailing it surfaces exactly the least
# interesting traffic (12-octet response blocks) and is blind to every real
# datagram, which only exists after scripts/p25_packet.py decodes the raw dump.
# So poll the decoder instead and report first sightings.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
seen=/tmp/p25_seen_kinds.$$
: > "$seen"
trap 'rm -f "$seen"' EXIT

while true; do
  # `|| true`: a truncated log or a rotation mid-read must not kill the watch.
  out=$(python3 scripts/p25_packet.py results/op25_multi.log 2>/dev/null || true)

  # Any application message type we have not reported before.
  printf '%s\n' "$out" | sed -n 's/^ *[0-9]\+  \(.*\)$/\1/p' | while read -r kind; do
    [ -n "$kind" ] || continue
    if ! grep -qxF "$kind" "$seen"; then
      echo "$kind" >> "$seen"
      echo "NEW application message type: $kind"
    fi
  done

  # TMS is the open question, so call it out explicitly rather than relying on
  # someone noticing a new line.
  if printf '%s\n' "$out" | grep -qiE 'TMS|4007|4008'; then
    echo "TMS TRAFFIC SEEN -- this would be the first on this site"
  fi
  sleep 120
done
