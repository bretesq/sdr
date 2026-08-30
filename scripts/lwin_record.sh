#!/usr/bin/env bash
# Record CLEAR (unencrypted) LWIN talkgroups to per-call WAV files.
# Usage: lwin_record.sh [seconds]
#
# Notes learned the hard way:
#  - Must run from op25's apps/ dir: rx.py does sys.path.append('tdma') (relative).
#  - Use SoapySDR, not gr-osmosdr: gr-osmosdr forces 8 Msps on the HackRF Pro and
#    op25 never locks. Soapy allows 2 Msps -> decim=83 -> locks immediately.
#  - Do NOT pass -2: this system is P25 Phase I; -2 sets num_ambe=2 (TDMA) and
#    breaks Phase 1 IMBE voice.
#  - -n silences encrypted traffic; the whitelist restricts to clear talkgroups.
set -e
SECS=${1:-400}
R=/home/besquivel/rtl
A=$R/src/op25/op25/gr-op25_repeater/apps
mkdir -p "$R/recordings"
cd "$A"
rm -f tgid-*.wav
echo "recording clear LWIN talkgroups for ${SECS}s ..."
timeout "$SECS" python3 rx.py --args 'soapy=0,driver=hackrf' \
  -N 'AMP:0,LNA:40,VGA:44' -S 2000000 -q 0 -o 25000 \
  -T "$R/lwin_record.tsv" -V -n -L 4 -v 2 \
  > "$R/results/op25_record.log" 2>&1 || true
n=$(ls tgid-*.wav 2>/dev/null | wc -l)
if [ "$n" -gt 0 ]; then mv tgid-*.wav "$R/recordings/"; fi
echo "captured $n call(s) -> $R/recordings/"
cd "$R" && python3 scripts/label_recordings.py 2>/dev/null || true
