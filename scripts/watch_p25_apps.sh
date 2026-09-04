#!/usr/bin/env bash
# Emit a line ONLY when the packet-data decode shows something new.
#
# Why not tail the op25 log directly: op25's own `PDU: fmt=` line comes from
# its rate-1/2-only path, so for a confirmed data packet it always reports
# blks=0 and carries NO payload. Tailing it surfaces exactly the least
# interesting traffic (12-octet response blocks) and is blind to every real
# datagram, which only exists after scripts/p25_packet.py decodes the raw dump.
# So poll the decoder instead and report first sightings.
#
# WHY SIGNATURES RATHER THAN MESSAGE KINDS. This watcher found a real parser
# bug by reporting an unfamiliar kind ("unknown LRRP type 105", which was
# really type 9 with two flag bits set). Fixing the parser then made that
# variant report as an ordinary "triggered location start request" -- a kind
# already seen -- so the NEXT flagged message would have gone unnoticed. The
# fix silently removed the observability that found the bug. `--signatures`
# includes the flag bits, so a flagged message is distinct from an unflagged
# one and the 1-in-162 case stays visible.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
seen=/tmp/p25_seen_sigs.$$
: > "$seen"
trap 'rm -f "$seen"' EXIT

while true; do
  # `|| true`: a truncated log or a rotation mid-read must not kill the watch.
  out=$(python3 scripts/p25_packet.py --signatures results/op25_multi.log 2>/dev/null || true)

  while IFS= read -r sig; do
    [ -n "$sig" ] || continue
    if ! grep -qxF "$sig" "$seen"; then
      echo "$sig" >> "$seen"
      echo "NEW: $sig"
    fi
  done <<< "$out"

  # TMS is the open question, so call it out explicitly rather than relying on
  # someone noticing a new line. Zero TMS packets have been seen on this
  # system, so the first one is worth interrupting for.
  if printf '%s\n' "$out" | grep -qiE 'TMS'; then
    echo "TMS TRAFFIC SEEN -- this would be the first on this site"
  fi
  sleep 120
done
