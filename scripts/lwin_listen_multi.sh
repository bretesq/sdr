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
# Where the backgrounded grant census import writes its summary and any
# failure. A separate file rather than this script's stdout because the import
# outlives the launcher now (see cleanup()): its output would otherwise land on
# the container's stdout after this script had exited, with nothing to
# attribute it to. Appended to, never rotated here -- one short entry per
# session stop. results/*.log is the established place for this kind of
# per-tool log in this repo.
IMPORT_LOG=$R/results/grant_import.log
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
check_patch "def tune_data_receivers" \
  "op25-tk_p25-follow-sndcp-data-grants.patch" \
  "Without it the data receiver never moves, and LWIN spreads SNDCP grants over 19 frequencies: it would see ~4% of them and the rest would look like a system with no packet data." \
  || PATCHES_OK=0

# The C++ patch is checked against the INSTALLED LIBRARY, not the source.
#
# Source and binary disagree here in a way that is invisible: `patch` restores
# lib/*.cc without rebuilding, and multi_rx loads
# libgnuradio-op25_repeater.so, so an un-rebuilt tree runs the OLD decoder
# while the source looks correct. Every failure mode of that patch is a silent
# absence of packet data, which is exactly what a grep of the source would
# reassure us about. So grep the artifact that actually runs.
check_lib() {   # <string> <patch name> <why>
  local so
  so=$(ldconfig -p 2>/dev/null | awk '/libgnuradio-op25_repeater\.so /{print $NF; exit}')
  [ -n "$so" ] || so=/usr/local/lib/x86_64-linux-gnu/libgnuradio-op25_repeater.so
  if [ ! -r "$so" ]; then
    echo "ERROR: cannot read $so to verify patches/$2" >&2
    return 1
  fi
  # `grep -a` on the binary, NOT `strings | grep`. The capture container does
  # not ship binutils, so `strings` is absent there: the pipeline produced
  # nothing, grep found nothing, and this guard reported the patch missing and
  # refused to start a container-hosted capture that was in fact correctly
  # patched. Verified by md5: the container bind-mounts this exact file
  # read-only from the host, so it always sees whatever `make install` put
  # there. grep is in coreutils and present in both places.
  if ! grep -qa "$1" "$so" 2>/dev/null; then
    echo "ERROR: installed op25 library is missing patches/$2" >&2
    echo "       $3" >&2
    echo "       Re-apply per patches/README.md, then REBUILD:" >&2
    echo "       cd src/op25/build && make -j8 && sudo make install" >&2
    return 1
  fi
}
check_lib "PDU: process_PDU entered" \
  "op25-p25p1-read-sndcp-packet-data.patch" \
  "Without it every packet-data PDU is discarded before it is logged, in three separate places, and the system looks like it carries no data at all." \
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
    # BACKGROUNDED, AND THAT IS THE POINT. `setsid ... &`, not `setsid ...`.
    #
    # WHY. This used to run synchronously, and the comment that stood here
    # said so plainly: "It stops being insurance on a long unbounded session,
    # which is the console's default." That came true. The console's Stop path
    # SIGINTs this script's whole process group and waits for it to empty
    # before escalating to SIGKILL -- but bash sits inside THIS trap until the
    # import returns, so the import's runtime was charged directly against
    # that budget. With the console on the `pd-all` preset (222 talkgroups, 10
    # recorders) every Stop was blowing the budget and escalating, which
    # SIGKILLs the group partway through this very trap and abandons the
    # recorder cleanup above -- see scripts/capture_control.py's
    # STOP_SIGINT_TIMEOUT_SEC for the measurements (~20-25 s of cleanup on a
    # 3-hour session, of which the import is nearly all).
    #
    # Raising that budget was the stopgap; this is the actual fix. The import
    # is ALREADY in its own process group thanks to setsid -- precisely so it
    # can outlive a group SIGKILL -- so bash had no reason to be waiting on it
    # in the first place. The `&` removes the dominant term from the shutdown
    # critical path outright. The console's default duration is 86400 s, whose
    # log no timeout was ever going to cover; this is why the answer could not
    # keep being a bigger number.
    #
    # WHY BACKGROUNDING IS SAFE HERE, AND WHAT WOULD BREAK IT
    # -------------------------------------------------------
    # The import can still be running when the NEXT capture starts, so it has
    # to survive that. It does, because the session log is ROTATED, NOT
    # TRUNCATED: the startup path above is `mv "$LOG" "$ROTATED"` and only
    # THEN `: > "$LOG"`, so the `mv` renames the inode this import is reading
    # and the `: >` creates a brand-new one. An open descriptor follows the
    # inode, not the path, so the import goes on reading a complete, stable
    # file that nothing will ever write to again. (Commit e741d6f is what made
    # that true; it was `: > "$LOG"` before, for the reasons the rotation
    # comment above gives.)
    #
    # DO NOT restore `: > "$LOG"` in place of that rotation. Truncating the
    # live inode would zero the file out from under an import already reading
    # it, and the failure would be SILENT -- the import would report a
    # plausible-looking small number rather than an error. There is a test
    # asserting the `mv` still precedes the `: >` for exactly this reason.
    #
    # Residual, bounded, and detectable: python opens the file ~100 ms after
    # being forked, so a new session's `mv` landing inside that window would
    # have this import open the fresh empty log instead. It needs a capture to
    # start within ~100 ms of a stop, which the console's own Stop-then-Start
    # gating makes very hard to hit -- and if it ever is hit, a 0-grant entry
    # for a multi-hour session is visibly wrong in the import log below rather
    # than something that has to be inferred.
    #
    # WHERE THE OUTPUT GOES. Synchronously (instant, no blocking) write a
    # dated header, then let the import append its own summary
    # ("imported N grants, M linked to a recorded call") and, on failure, its
    # traceback or error to the same file via 2>&1. The header is what makes a
    # backgrounded run attributable to a session at all; without it the
    # summary would arrive on the container's stdout after the launcher had
    # already exited, interleaved with the next thing to speak and belonging
    # to nothing.
    #
    # HOW TO READ results/grant_import.log, AND WHY THE TAG IS REQUIRED
    # -----------------------------------------------------------------
    # The rule used to be "an entry with a header and no `imported` line is a
    # failed import, and the reason is right underneath it." Both halves were
    # false, and both for reasons this very comment block states elsewhere:
    #
    #   * "The import can still be running when the NEXT capture starts", so
    #     two imports append to this one file at once -- and every header was
    #     the identical string, because `$LOG` is a constant path, not a
    #     session identity. Session A finishing after session B wrote its
    #     header filed A's `imported N` under B's header. A reader applying
    #     the rule concluded B succeeded when B had failed.
    #   * `setsid` protects the import from a GROUP SIGKILL. It does not
    #     protect it from the PID namespace going away, which is what
    #     `docker compose stop capture` does (capture_control.py's shutdown
    #     handler os._exit()s, tini goes with it, the namespace and everything
    #     in it is torn down). That left a header, no summary, and NO REASON
    #     -- an unexplained gap rather than a diagnosable failure.
    #
    # So each import now mints a token unique to itself: the wall-clock time
    # it was launched plus this launcher's pid. It goes in the header after
    # BEGIN, and `--tag` makes import_grants.py stamp it on EVERY line it
    # writes, including a final "=== END <tag> exit N". Reading the file is
    # then: pick a BEGIN line, grep its token, and
    #
    #   END exit 0 + an `imported` line   -> succeeded
    #   END exit N (N != 0)               -> failed; the reason is on the
    #                                        tagged lines above the END
    #   no END line at all                -> killed before it could report,
    #                                        which is the container-teardown
    #                                        case above and NOT the same
    #                                        diagnosis as a failure
    #
    # That rule stays true when two imports interleave, because the token is
    # on the lines and not merely on the header. The token is MINTED here
    # rather than derived from anything on disk on purpose: nothing that
    # happens after this printf can invalidate it. The rotated-log basename
    # this session's evidence will end up under is a genuinely useful pointer
    # -- it is what the NEXT session's rotation will name this log -- but it
    # is computed from the log's mtime, so it is recorded as a clearly
    # labelled hint that can go stale, never as the key the rule keys on.
    #
    # No pid is reported for the import itself: `setsid` execs its program
    # directly when the caller is not already a process-group leader and forks
    # when it is, so `$!` cannot be relied on to be python's own pid. `pgrep
    # -f import_grants.py` answers "is it still running" without this script
    # having to guess.
    IMPORT_TAG="$(date -Is)#$$"
    printf '=== %s  BEGIN %s  %s  (evidence will rotate to: %s)\n' \
      "$(date -Is)" "$IMPORT_TAG" "$LOG" \
      "$(basename "$LOG").$(date -r "$LOG" +%Y%m%d-%H%M%S 2>/dev/null || echo unknown)" \
      >> "$IMPORT_LOG"
    setsid python3 "$R/scripts/import_grants.py" --tag "$IMPORT_TAG" "$LOG" >> "$IMPORT_LOG" 2>&1 &
    echo "  grant import running in the background -> $IMPORT_LOG"
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
