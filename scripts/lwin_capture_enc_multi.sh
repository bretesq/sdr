#!/usr/bin/env bash
# Multi-radio encrypted (ADP/RC4) capture for key recovery.
#
# Reuses the multi-radio pool (HackRF One @ 8 Msps + HackRF Pro @ 12 Msps) to
# capture LWIN encrypted LDU2 frames: op25 multi_rx.py -v 10 logs BOTH the
# cleartext MI (ESS) and the IMBE CIPHERTXT (11-byte LDU2 codeword), and one
# UDP audio recorder per voice channel so we can derive the known plaintext.
#
# Unlike lwin_capture_enc.sh (single Pro receiver, 773.05625 only), this spans
# BOTH bands at once, so it catches encrypted calls on either leg.
#
# Usage: lwin_capture_enc_multi.sh [seconds]
set -u
R=/home/besquivel/rtl
A=$R/src/op25/op25/gr-op25_repeater/apps
. "$R/scripts/radios.sh"          # HRF_*_ARGS / HRF_*_GAINS: address radios by serial
ENC_WL=$R/lwin_enc_tgs.txt
CCWL=$R/lwin_enc_nofollow.txt
CFG=$R/lwin_enc_multi.json
LOG=$R/results/lwin_enc_capture_multi.log
SECS=${1:-300}
LEGS=700,800
BASE_PORT=23470

# Regenerate the encrypted talkgroup whitelist from the reference DB so it is
# current (enc != clear). The single-radio script hard-codes a TG list; regenerating
# keeps this in sync with the DB.
python3 "$R/scripts/make_whitelist.py" \
  -g 17053,17054,17055,17056,17062,17065,17066,17067,17073,17075,17077,17080,17081,17086,17087,17088,17089,17090,17093,17094,17097,17098,17209,17210,17229,17322,17161,17164,17165,17166,17167,17168,17169,17170,17171,17172,17186,17187,17312,17313,17314,17315,17316,17317,17554,17555,17556,17557 \
  --include-encrypted --include-partial \
  -o "$ENC_WL" || exit $?

# The control receiver's whitelist holds a talkgroup that does not exist, so
# find_talkgroup never matches and it stays pinned on the control channel.
echo 999999 > "$CCWL"

CFG_ARGS=(--legs "$LEGS" --whitelist "$ENC_WL" --cc-whitelist "$CCWL"
          --base-port "$BASE_PORT" -o "$CFG")
python3 "$R/scripts/make_multirx_cfg.py" "${CFG_ARGS[@]}" || exit $?

# One audio recorder per VOICE channel (ports + receiver ids from the config).
mapfile -t RX_PORTS < <(python3 - "$CFG" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
for i, ch in enumerate(cfg['channels']):
    if ch['name'] == 'CC':
        continue
    print(i, ch['destination'].rsplit(':', 1)[1], ch['device'], ch['name'])
PY
) || exit $?

mkdir -p "$R/recordings" "$R/results"
: > "$LOG"

REC_PIDS=()
OP25_PID=""
cleanup() {
  echo; echo "stopping..."
  [ -n "${OP25_PID:-}" ] && kill "$OP25_PID" 2>/dev/null
  pkill -f "python3 multi_rx\.py" 2>/dev/null
  for p in "${REC_PIDS[@]+"${REC_PIDS[@]}"}"; do kill -INT "$p" 2>/dev/null; done
  wait 2>/dev/null
  echo "extracting MI + ciphertext pairs -> results/enc_pair.txt"
  python3 "$R/scripts/extract_enc_pair.py" "$LOG"
  exit 0
}
trap cleanup INT TERM

for row in "${RX_PORTS[@]}"; do
  set -- $row
  python3 "$R/scripts/udp_audio_record.py" --rx-id "$1" "$2" "$SECS" "$R/recordings" "$LOG" &
  REC_PIDS+=($!)
done

# -v 10 is required: op25 logs the LDU2 ESS (cleartext MI) and IMBE CIPHERTXT
# only at that level.
OP25_CMD="cd $A && exec python3 multi_rx.py -c $CFG -v 10"
script -q -f -c "$OP25_CMD" "$LOG" >/dev/null 2>&1 &
OP25_PID=$!

echo
echo "LWIN multi-radio encrypted capture — legs $LEGS, ${#RX_PORTS[@]} voice receiver(s)"
echo "whitelist: $(wc -l < "$ENC_WL") encrypted talkgroups -> $R/recordings/"
[ "$SECS" -eq 0 ] 2>/dev/null && echo "Ctrl-C to stop." || echo "running ${SECS}s."

wait "${REC_PIDS[0]}"
cleanup
