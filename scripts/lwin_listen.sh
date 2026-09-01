#!/usr/bin/env bash
# Listen to LWIN (Baton Rouge Simulcast) and record calls as
# TG<id>_<Alpha-Tag>_<timestamp>.wav in recordings/
#
# Usage: lwin_listen.sh [options] [seconds]
#
# Talkgroup selection (default: every clear talkgroup in the Baton Rouge area)
#   --pd                police / sheriff DISPATCH        (tag "Law Dispatch")
#   --pd-all            police dispatch + talk + tac
#   --fire              fire dispatch          --fire-all  fire dispatch + tac + talk
#   --ems               EMS + hospital         --interop   interop / emergency ops
#   --preset NAME       any of: pd pd-all fire fire-all ems interop schools
#                               publicworks all
#   --tag "TAG[,TAG]"   select by tag, e.g. --tag "Law Dispatch,Law Talk"
#   --tg 17165,17139    explicit talkgroup IDs
#   --match REGEX       regex over alpha / description / category
#   --all-areas         statewide instead of Baton Rouge area
#
# Encryption (recording is clear-only by default)
#   --include-partial   also follow partially-encrypted talkgroups. They carry mostly
#                       clear traffic (see OBSERVATIONS.md §5); op25 -n still silences
#                       the encrypted bursts. Needed for BRPD / EBR Sheriff dispatch.
#   --include-encrypted also follow fully-encrypted talkgroups (records silence).
#
# STT (speech-to-text)
#   --ess               raise op25 to -v 10 so it prints the ESS header
#                       (algid/keyid/mi) for every voice frame. That is the
#                       AUTHORITATIVE per-call encryption signal, independent of
#                       the reference DB's static enc flag — which is known to
#                       disagree: TG 17086 is flagged 'full' upstream but
#                       transmitted algid 0x80 (clear) in all 23 observations.
#                       Costs roughly 10x the log volume (~24 MB/hour against
#                       ~2.4 MB/hour) and is off by default because the 800 MHz
#                       voice leg here is marginal and extra I/O is not free.
#   --stt                 launch stt_watch.py in parallel with the recorder. New
#                       .wav files are transcribed as they land, and transcripts
#                       are merged into calls.json.
#
# Other
#   --list              show the selected talkgroups and exit
#   -h, --help          this help
#
# Examples
#   ./scripts/lwin_listen.sh --pd --list
#   ./scripts/lwin_listen.sh --pd --include-partial 600
#   ./scripts/lwin_listen.sh --stt --tg 17165,17167,17169,17171 --include-partial
set -u
R=/home/besquivel/rtl
A=$R/src/op25/op25/gr-op25_repeater/apps
. "$R/scripts/radios.sh"          # HRF_*_ARGS / HRF_*_GAINS: address radios by serial
PORT=23456
WL=$R/lwin_active_whitelist.txt
TSV=$R/lwin_active.tsv
SECS=0
STT=0
GEN=()          # args passed through to make_whitelist.py
ESS=0           # --ess: -v 10 so op25 prints ESS encryption headers

usage() { sed -n '2,/^set -u/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
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
    --stt)               STT=1 ;;
    --ess)               ESS=1 ;;
    --list)              GEN+=(--list) ; LIST=1 ;;
    -h|--help)           usage ;;
    -*)                  echo "unknown option: $1" >&2; exit 1 ;;
    *)                   SECS="$1" ;;
  esac
  shift
done

# build the whitelist
python3 "$R/scripts/make_whitelist.py" "${GEN[@]+"${GEN[@]}"}" -o "$WL" || exit $?
[ -n "${LIST:-}" ] && exit 0

printf 'Sysname\tControl Channel List\tOffset\tNAC\tModulation\tTGID Tags File\tWhitelist\tBlacklist\tCenter Frequency\n' > "$TSV"
printf 'LWIN-BR\t773.05625\t0\t0x1bd\tfsk4\t\t%s\t\t\n' "$WL" >> "$TSV"

mkdir -p "$R/recordings" "$R/results"
[ "$SECS" -eq 0 ] 2>/dev/null && RUN=99999 || RUN=$SECS

cleanup() {
  echo; echo "stopping..."
  [ -n "${OP25_PID:-}" ] && kill "$OP25_PID" 2>/dev/null
  pkill -f "python3 rx\.py --args" 2>/dev/null
  [ -n "${REC_PID:-}"  ] && kill -INT "$REC_PID" 2>/dev/null
  [ -n "${STT_PID:-}"  ] && kill -INT "$STT_PID" 2>/dev/null
  wait 2>/dev/null
  n=$(ls -1 "$R"/recordings/TG*.wav 2>/dev/null | wc -l)
  echo "-> $n call(s) in $R/recordings/"
  exit 0
}
trap cleanup INT TERM

: > "$R/results/op25_record.log"

python3 "$R/scripts/udp_audio_record.py" $PORT "$RUN" "$R/recordings" \
        "$R/results/op25_record.log" &
REC_PID=$!
sleep 2

# Optional: launch the STT watcher so new .wav files are transcribed on arrival.
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

# op25 under a pty so its log is written in real time (python3 -u breaks op25 on 3.14).
# -w = audio over UDP (no sound card); -n = silence encrypted; no -2 (system is Phase I).
VERBOSITY=2
[ "$ESS" -eq 1 ] && VERBOSITY=10

OP25_CMD="cd $A && exec python3 rx.py --args $HRF_PRO_ARGS -N $HRF_PRO_GAINS -S 2000000 -q 0 -o 25000 -T $TSV -V -w -u $PORT -n -v $VERBOSITY"
script -q -f -c "$OP25_CMD" "$R/results/op25_record.log" >/dev/null 2>&1 &
OP25_PID=$!

echo "LWIN Baton Rouge Simulcast — control channel 773.05625 MHz"
echo "whitelist: $(wc -l < "$WL") talkgroups -> $R/recordings/"
[ "$SECS" -eq 0 ] 2>/dev/null && echo "Ctrl-C to stop." || echo "running ${SECS}s."

wait $REC_PID
kill $OP25_PID 2>/dev/null; pkill -f "python3 rx\.py --args" 2>/dev/null
wait 2>/dev/null
n=$(ls -1 "$R"/recordings/TG*.wav 2>/dev/null | wc -l)
echo "-> $n call(s) in $R/recordings/"
