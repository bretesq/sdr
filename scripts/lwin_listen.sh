#!/usr/bin/env bash
# One-command LWIN listener: records CLEAR talkgroups to per-call WAVs named
# with the talkgroup, e.g. TG17345_17-SGFD-Ops_20260830-160251.wav
#
#   ./scripts/lwin_listen.sh            # run until Ctrl-C
#   ./scripts/lwin_listen.sh 600        # run for 600 seconds
#
# Requires: HackRF (SoapySDR), op25 built, reference/lwin_talkgroups.json
set -u
R=/home/besquivel/rtl
A=$R/src/op25/op25/gr-op25_repeater/apps
SECS=${1:-0}                       # 0 = until Ctrl-C
PORT=23456
[ "$SECS" -eq 0 ] 2>/dev/null && RUN=99999 || RUN=$SECS

mkdir -p "$R/recordings" "$R/results"

cleanup() {
  echo; echo "stopping..."
  [ -n "${OP25_PID:-}" ] && kill "$OP25_PID" 2>/dev/null
  pkill -f "gr-op25_repeater/apps/rx.py" 2>/dev/null
  [ -n "${REC_PID:-}"  ] && kill -INT "$REC_PID" 2>/dev/null
  wait 2>/dev/null
  echo
  ls -1 "$R"/recordings/TG*.wav 2>/dev/null | tail -20
  n=$(ls -1 "$R"/recordings/TG*.wav 2>/dev/null | wc -l)
  echo "-> $n call(s) in $R/recordings/"
  exit 0
}
trap cleanup INT TERM

: > "$R/results/op25_record.log"

# audio recorder first, so it is listening before op25 starts sending
python3 "$R/scripts/udp_audio_record.py" $PORT "$RUN" "$R/recordings" \
        "$R/results/op25_record.log" &
REC_PID=$!
sleep 2

# op25 is run under a pty (script -q -f) so Python line-buffers its output and the
# recorder can read talkgroups live. `python3 -u` does NOT work here: op25 reconfigures
# stdout and unbuffered mode breaks it ('_io.FileIO' object has no attribute 'detach').
# -w sends audio over UDP (no sound card needed); -n silences encrypted traffic;
# whitelist restricts to clear talkgroups. Do NOT add -2 (this system is Phase I).
OP25_CMD="cd $A && exec python3 rx.py --args soapy=0,driver=hackrf -N AMP:0,LNA:40,VGA:44 -S 2000000 -q 0 -o 25000 -T $R/lwin_record.tsv -V -w -u $PORT -n -v 2"
script -q -f -c "$OP25_CMD" "$R/results/op25_record.log" >/dev/null 2>&1 &
OP25_PID=$!

echo "listening on LWIN Baton Rouge Simulcast (CC 773.05625 MHz)"
echo "clear talkgroups only; recordings -> $R/recordings/"
[ "$SECS" -eq 0 ] 2>/dev/null && echo "Ctrl-C to stop." || echo "running ${SECS}s."

wait $REC_PID
kill $OP25_PID 2>/dev/null
wait 2>/dev/null
n=$(ls -1 "$R"/recordings/TG*.wav 2>/dev/null | wc -l)
echo "-> $n call(s) in $R/recordings/"
