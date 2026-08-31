#!/usr/bin/env bash
# Control-channel-only LWIN logging: never retunes, so every grant is captured.
SECS=${1:-360}
R=/home/besquivel/rtl; A=$R/src/op25/op25/gr-op25_repeater/apps
. "$R/scripts/radios.sh"          # HRF_*_ARGS / HRF_*_GAINS: address radios by serial
cd "$A"
timeout "$SECS" python3 rx.py --args "$HRF_PRO_ARGS" \
  -N "$HRF_PRO_GAINS" -S 2000000 -q 0 -o 25000 \
  -T "$R/lwin_cdr.tsv" -v 10 > "$R/results/lwin_cdr.log" 2>&1 || true
cd "$R" && python3 scripts/lwin_cdr.py
