#!/usr/bin/env bash
# Control-channel-only LWIN logging: never retunes, so every grant is captured.
SECS=${1:-360}
R=/home/besquivel/rtl; A=$R/src/op25/op25/gr-op25_repeater/apps
cd "$A"
timeout "$SECS" python3 rx.py --args 'soapy=0,driver=hackrf' \
  -N 'AMP:0,LNA:40,VGA:44' -S 2000000 -q 0 -o 25000 \
  -T "$R/lwin_cdr.tsv" -v 10 > "$R/results/lwin_cdr.log" 2>&1 || true
cd "$R" && python3 scripts/lwin_cdr.py
