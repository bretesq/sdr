#!/usr/bin/env bash
# Listen to LWIN with SEVERAL receivers at once, across both bands.
#
# Unlike lwin_listen.sh (op25 rx.py, ONE receiver that must leave the control
# channel to hear a call, so it captures ~25% of calls and an incomplete grant
# census), this runs op25 multi_rx.py with a pool of receivers:
#
#   HackRF One, 8 Msps @ 771.4185 MHz   1 receiver PINNED to the control
#                                       channel (773.05625) + 3 voice
#   HackRF Pro, 12 Msps @ 855.7250 MHz  5 voice receivers
#
# Site 13 splits its voice across 769-772 and 851-860 MHz, 87 MHz apart, which
# no single HackRF sample rate spans — hence two radios. The pinned control
# receiver never retunes, so a run produces audio AND a 100% grant census at
# the same time, which OBSERVATIONS.md §3.3 records as impossible with one
# receiver.
#
# Requires patches/op25-tk_p25-release-unreachable-grant.patch: without it a
# receiver handed a grant on the other band claims the call and records
# silence. Checked at startup.
#
# GRANT CENSUS. The pinned receiver sees every grant, but op25 only LOGS them
# at -v 10, so this script runs at -v 10 by default and imports them into
# sdr.db when the run ends. That makes `calls` (what we recorded) and `grants`
# (what was announced) available from one session — OBSERVATIONS.md §3.3
# records those as mutually exclusive with a single receiver. Costs ~10x the
# log volume (~24 MB/hour); --no-census drops to -v 2 and skips the import.
#
# See docs/2026-08-31-wideband-multichannel.md.
#
# Usage: lwin_listen_multi.sh [options] [seconds]
#   --legs 700|800|700,800   which bands (default 700,800)
#   --n-voice-700 N          voice receivers on the One (default 3)
#   --n-voice-800 N          voice receivers on the Pro (default 7 -- raised
#                            from 5: measured peak concurrency on this leg is
#                            5 of 5, hit 17 times in 7,136 calls; see
#                            scripts/make_multirx_cfg.py's LEG_800 comment)
#   --stt                    transcribe new .wav files as they land
#   --ess                    op25 -v 10 so ESS (algid/keyid/mi) is logged
#   --no-census              skip the grant import at exit (see below)
#   everything else          the same talkgroup-selection flags as
#                            lwin_listen.sh, passed to make_whitelist.py
#   -h, --help               this help
#
# Examples
#   ./scripts/lwin_listen_multi.sh --pd-all --include-partial --list
#   ./scripts/lwin_listen_multi.sh --pd-all --include-partial 300
#   ./scripts/lwin_listen_multi.sh --legs 700 --n-voice-700 3 120
set -u
R=/home/besquivel/rtl
A=$R/src/op25/op25/gr-op25_repeater/apps
. "$R/scripts/radios.sh"          # HRF_*_ARGS / HRF_*_GAINS: address radios by serial
BASE_PORT=23460
WL=$R/lwin_active_whitelist.txt
CCWL=$R/lwin_nofollow.txt
CFG=$R/lwin_both.json
LOG=$R/results/op25_multi.log
LEGS=700,800
SECS=0
STT=0
ESS=0
CENSUS=1
NV700=""
NV800=""
GEN=()

usage() { sed -n '2,/^set -u/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --legs)              LEGS="$2"; shift ;;
    --n-voice-700)       NV700="$2"; shift ;;
    --n-voice-800)       NV800="$2"; shift ;;
    --stt)               STT=1 ;;
    --ess)               ESS=1 ;;
    --no-census)         CENSUS=0 ;;
    --pd)                GEN+=(--preset pd) ;;
    --pd-all)            GEN+=(--preset pd-all) ;;
    --fire)              GEN+=(--preset fire) ;;
    --fire-all)          GEN+=(--preset fire-all) ;;
    --ems)               GEN+=(--preset ems) ;;
    --interop)           GEN+=(--preset interop) ;;
    --preset)            GEN+=(--preset "$2"); shift ;;
    --tag)               GEN+=(--tag "$2"); shift ;;
    --tg)                GEN+=(--tg "$2"); shift ;;
    --match)             GEN+=(--match "$2"); shift ;;
    --all-areas)         GEN+=(--all-areas) ;;
    --include-partial)   GEN+=(--include-partial) ;;
    --include-encrypted) GEN+=(--include-encrypted) ;;
    --list)              GEN+=(--list); LIST=1 ;;
    -h|--help)           usage ;;
    -*)                  echo "unknown option: $1" >&2; exit 1 ;;
    *)                   SECS="$1" ;;
  esac
  shift
done

# The op25 patches are not tracked by git (src/ is gitignored), so a re-clone
# or reset silently removes them. Both matter, and both fail QUIETLY: without
# them cross-band grants are eaten rather than erroring.
check_patch() {   # <marker> <patch name> <why>
  if ! grep -q "$1" "$A/tk_p25.py" 2>/dev/null; then
    echo "ERROR: op25 is missing patches/$2" >&2
    echo "       $3" >&2
    echo "       Re-apply per patches/README.md." >&2
    return 1
  fi
}
PATCHES_OK=1
check_patch "leaving it unclaimed" \
  "op25-tk_p25-multiband-receiver-pool.patch" \
  "Without it a receiver handed a grant on the other band claims the call, records silence, and blocks anyone else from taking it." \
  || PATCHES_OK=0
check_patch "def can_reach" \
  "op25-tk_p25-multiband-receiver-pool.patch" \
  "Without it receivers keep claiming grants on the band they cannot reach: measured 1,300 tune attempts for 6 calls on the 700 leg." \
  || PATCHES_OK=0
[ "$PATCHES_OK" -eq 1 ] || exit 1

python3 "$R/scripts/make_whitelist.py" "${GEN[@]+"${GEN[@]}"}" -o "$WL" || exit $?
[ -n "${LIST:-}" ] && exit 0

# The control receiver's whitelist holds one talkgroup that does not exist, so
# find_talkgroup never matches for it and it never leaves the control channel.
echo 999999 > "$CCWL"

CFG_ARGS=(--legs "$LEGS" --whitelist "$WL" --cc-whitelist "$CCWL"
          --base-port "$BASE_PORT" -o "$CFG")
[ -n "$NV700" ] && CFG_ARGS+=(--n-voice-700 "$NV700")
[ -n "$NV800" ] && CFG_ARGS+=(--n-voice-800 "$NV800")
python3 "$R/scripts/make_multirx_cfg.py" "${CFG_ARGS[@]}" || exit $?

# One recorder per VOICE channel. Ports and receiver ids come from the config
# itself rather than being recomputed here, so the two can never drift: the
# receiver id is the channel's index, which is what op25 uses as msgq_id
# (multi_rx.py:configure_channels assigns msgq_id = len(self.channels)).
mapfile -t RX_PORTS < <(python3 - "$CFG" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
for i, ch in enumerate(cfg['channels']):
    if ch['name'] == 'CC':
        continue                      # pinned to the control channel; no audio
    print(i, ch['destination'].rsplit(':', 1)[1], ch['device'], ch['name'])
PY
) || exit $?

mkdir -p "$R/recordings" "$R/results"
[ "$SECS" -eq 0 ] 2>/dev/null && RUN=99999 || RUN=$SECS

# ROTATE, don't truncate. This line used to be `: > "$LOG"`, and at -v 10 that
# file is the ONLY record of CIPHERTXT and ESS lines -- encrypted traffic leaves
# no usable recording, because op25 -n silences it. So starting a session to
# listen destroyed every previous session's evidence. One capture was lost that
# way, including the only ciphertext for keyid 0x22.
#
# Kept here rather than beside the `script` invocation below: script(1) also
# truncates, so rotation has to happen before ANY writer touches the file, and a
# rotation placed later simply finds it already empty.
if [ -s "$LOG" ]; then
  ROTATED="$LOG.$(date -r "$LOG" +%Y%m%d-%H%M%S 2>/dev/null || date +%Y%m%d-%H%M%S)"
  mv "$LOG" "$ROTATED"
  echo "rotated previous log -> $(basename "$ROTATED")"
  # Keep the 20 most recent; at ~24 MB/hour this is bounded but generous.
  ls -1t "$LOG".2* 2>/dev/null | tail -n +21 | xargs -r rm -f
fi
: > "$LOG"

REC_PIDS=()
cleanup() {
  echo; echo "stopping..."
  [ -n "${OP25_PID:-}" ] && kill "$OP25_PID" 2>/dev/null
  # Scoped to THIS launcher's own PID namespace (final-review.md C1). This
  # file predates the capture container: it was written when there was only
  # ever one PID namespace on the machine, so an unscoped `pkill -f` could
  # only ever match this launcher's own op25 -- there was nothing else on the
  # box for it to reach. That stopped being true the moment a second op25
  # could run inside the `capture` container: the host is unconfined, so it
  # can SIGNAL into any container (verified live: a harmless SIGCONT from the
  # host to a container process at uid 1000 was delivered, not refused), and
  # host pgrep/pkill -f already sees a container's processes by host pid
  # regardless of PID namespace. So a bare `pkill -f "python3 multi_rx\.py"`
  # run from a HOST launcher's cleanup -- which fires on every ordinary exit
  # (Ctrl-C, SIGTERM, or the duration simply expiring; see the trap below and
  # the direct call after `wait`) -- can kill a perfectly healthy delegated
  # capture running in the container, not just this launcher's own op25.
  # `--ns $$ --nslist pid` (procps-ng 4.0.4, verified present both on this
  # host and inside the capture image) restricts the match to processes in
  # the SAME pid namespace as this script, which a container's op25 never is
  # -- so this can only ever reach what it always meant to reach: this
  # launcher's own op25. Do NOT simplify this back to a bare `-f` match.
  pkill --ns $$ --nslist pid -f "python3 multi_rx\.py" 2>/dev/null
  for p in "${REC_PIDS[@]+"${REC_PIDS[@]}"}"; do kill -INT "$p" 2>/dev/null; done
  [ -n "${STT_PID:-}" ] && kill -INT "$STT_PID" 2>/dev/null
  wait 2>/dev/null
  if [ "$CENSUS" -eq 1 ]; then
    echo
    # setsid puts the import in its OWN process group.
    #
    # The console's Stop path (server/utils/processes.ts stopListening) SIGINTs
    # this script's whole group, then waits for the radio to be released, then
    # SIGKILLs the group after 8 s. By the time we get here we have already
    # pkill-ed multi_rx, so the radio reads as free — but bash is still inside
    # this trap, so the wait loop never breaks and burns the full budget before
    # SIGKILLing the group, which would take the import with it.
    #
    # Measured, the import is ~0.12 s for a 26,738-line log (the link query
    # uses idx_calls_tgid), so today it finishes ~67x inside the budget and
    # this is insurance rather than a live bug. It stops being insurance on a
    # long unbounded session, which is the console's default (RUN=99999).
    setsid python3 "$R/scripts/import_grants.py" "$LOG" || \
      echo "  (grant import failed; the log is still at $LOG)"
  fi
  n=$(ls -1 "$R"/recordings/TG*.wav 2>/dev/null | wc -l)
  echo "-> $n call(s) total in $R/recordings/"
  exit 0
}
trap cleanup INT TERM

for row in "${RX_PORTS[@]}"; do
  set -- $row
  python3 "$R/scripts/udp_audio_record.py" --rx-id "$1" \
          "$2" "$RUN" "$R/recordings" "$LOG" &
  REC_PIDS+=($!)
done
sleep 2

if [ "$STT" -eq 1 ]; then
  # Do not start a SECOND watcher. One may already be running independently of
  # any session (the console can start it, and it is the recommended way — see
  # server/utils/transcriber.ts). Two would double CPU and race each other on
  # the same .txt writes. A watcher we did not start is also not ours to kill,
  # so STT_PID stays unset and cleanup leaves it alone.
  if pgrep -f "stt_watch\.py" >/dev/null 2>&1; then
    echo "STT watcher already running; leaving it alone"
  else
    # Best-effort: the watcher falls back to the CPU binary if this fails, so a
    # missing docker costs throughput rather than transcription. Idempotent.
    "$R/scripts/stt_server.sh" start || echo "STT server unavailable; watcher will use CPU"
    python3 "$R/scripts/stt_watch.py" --dir "$R/recordings" &
    STT_PID=$!
    echo "STT watcher started (whisper medium.en via GPU server)"
  fi
fi

# Under a pty so the log is written in real time (python3 -u breaks op25 on 3.14).
# -v 10 is required for the grant census: at -v 2 op25 logs no grant lines at
# all (measured: 0 `set tgid=`, 0 `TSBK: op=`). --ess wants the same level.
VERBOSITY=2
[ "$CENSUS" -eq 1 ] && VERBOSITY=10
[ "$ESS" -eq 1 ] && VERBOSITY=10
OP25_CMD="cd $A && exec python3 multi_rx.py -c $CFG -v $VERBOSITY"

script -q -f -c "$OP25_CMD" "$LOG" >/dev/null 2>&1 &
OP25_PID=$!

echo
echo "LWIN Baton Rouge Simulcast — legs $LEGS, ${#RX_PORTS[@]} voice receiver(s)"
echo "whitelist: $(wc -l < "$WL") talkgroups -> $R/recordings/"
for row in "${RX_PORTS[@]}"; do
  set -- $row
  echo "  receiver $1  udp/$2  $3  $4"
done
[ "$SECS" -eq 0 ] 2>/dev/null && echo "Ctrl-C to stop." || echo "running ${SECS}s."

wait "${REC_PIDS[0]}"
cleanup
