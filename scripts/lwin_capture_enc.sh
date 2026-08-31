#!/usr/bin/env bash
# Capture an encrypted LDU2 frame (ciphertext) + its cleartext MI for ADP key recovery.
#
# Targets the East Baton Rouge Parish Sheriff talkgroups (the encrypted ones).
# op25 runs at -v 9 so it logs both:
#   - "IMBE (CIPHERTXT) xx xx ..."  (the 11-byte encrypted LDU2 codeword)
#   - "ESS: algid=..., keyid=..., mi=..."  (the cleartext MI + KID + ALGID)
#
# Usage: lwin_capture_enc.sh [seconds]
set -u
R=/home/besquivel/rtl
A=$R/src/op25/op25/gr-op25_repeater/apps
WL=$R/lwin_enc_tgs.txt
TSV=$R/lwin_enc.tsv
SECS=${1:-180}

# Whitelist of the encrypted EBR Sheriff + BRPD talkgroups (KID 0x8, ALGID 0xAA).
# Generated from the reference DB (enc != clear).
# --include-encrypted + --include-partial so the encrypted/partial TGs are selected
# (the safety default only keeps clear talkgroups; we specifically WANT the encrypted ones).
python3 "$R/scripts/make_whitelist.py" \
  -g 17053,17054,17055,17056,17062,17065,17066,17067,17073,17075,17077,17080,17081,17086,17087,17088,17089,17090,17093,17094,17097,17098,17209,17210,17229,17322,17161,17164,17165,17166,17167,17168,17169,17170,17171,17172,17186,17187,17312,17313,17314,17315,17316,17317,17554,17555,17556,17557 \
  --include-encrypted --include-partial \
  -o "$WL" || exit $?

printf 'Sysname\tControl Channel List\tOffset\tNAC\tModulation\tTGID Tags File\tWhitelist\tBlacklist\tCenter Frequency\n' > "$TSV"
printf 'LWIN-ENC\t773.05625\t0\t0x1bd\tfsk4\t\t%s\t\t\n' "$WL" >> "$TSV"

mkdir -p "$R/results"
: > "$R/results/lwin_enc_capture.log"

# -w = op25 emits 320-byte S16LE PCM @ 8 kHz over UDP; udp_audio_record.py receives it,
# splits calls on a 2 s gap and saves them. Combined with -v 10, we get BOTH the LDU2
# CIPHERTXT/ESS lines AND the audio, so we can match a low-energy (silence) audio segment to
# its LDU2 frame and use the fixed IMBE silence codeword as the known plaintext.
PORT=23457
OP25_CMD="cd $A && exec python3 rx.py --args soapy=0,driver=hackrf -N AMP:0,LNA:40,VGA:44 -S 2000000 -q 0 -o 25000 -T $TSV -V -w -u $PORT -v 10"
script -q -f -c "$OP25_CMD" "$R/results/lwin_enc_capture.log" >/dev/null 2>&1 &
OP25_PID=$!
# Run the audio recorder in parallel (same pattern as lwin_listen.sh).
mkdir -p "$R/recordings"
python3 "$R/scripts/udp_audio_record.py" $PORT "$SECS" "$R/recordings" "$R/results/lwin_enc_capture.log" &
REC_PID=$!
echo "capturing encrypted EBR Sheriff frames + audio for $SECS s"
timeout "$SECS" tail --pid="$OP25_PID" -f /dev/null 2>/dev/null
kill "$OP25_PID" 2>/dev/null
kill -INT "$REC_PID" 2>/dev/null
wait 2>/dev/null
echo "extracting MI + ciphertext -> results/enc_pair.txt"
python3 "$R/scripts/extract_enc_pair.py" "$R/results/lwin_enc_capture.log"
