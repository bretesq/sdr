#!/usr/bin/env bash
# Record BRPD dispatch (partial) talkgroups with audio + log encrypted CIPHERTXT frames.
# The 4 partial dispatch TGs carry mostly clear audio (recorded to WAV) plus the
# occasional encrypted bursts (CIPHERTXT in the -v 9 log). This gives a real known
# plaintext (from the WAV) to pair with a CIPHERTXT frame for ADP key recovery.
set -u
R=/home/besquivel/rtl
A=$R/src/op25/op25/gr-op25_repeater/apps
WL=/tmp/brpd_wl.txt
TSV=$R/lwin_brpd_audio.tsv
PORT=23456
SECS=${1:-300}

printf 'Sysname\tControl Channel List\tOffset\tNAC\tModulation\tTGID Tags File\tWhitelist\tBlacklist\tCenter Frequency\n' > "$TSV"
printf 'LWIN-BRPD\t773.05625\t0\t0x1bd\tfsk4\t\t%s\t\t\n' "$WL" >> "$TSV"

mkdir -p "$R/recordings" "$R/results"
[ "$SECS" -eq 0 ] 2>/dev/null && RUN=99999 || RUN=$SECS

: > "$R/results/op25_brpd_audio.log"

python3 "$R/scripts/udp_audio_record.py" $PORT "$RUN" "$R/recordings" \
        "$R/results/op25_brpd_audio.log" &
REC_PID=$!
sleep 2

# -v 9 logs IMBE CIPHERTXT lines (the encrypted bursts); -w emits audio over UDP.
# NO -n: we want the CIPHERTXT lines. No -2 (Phase I IMBE). No key loaded, so the
# encrypted bursts still produce (garbage) audio in the WAV, but the CIPHERTXT + MI
# in the log give us the ciphertext; the clear segments of the WAV give the real IMBE
# plaintext (encode the clear audio to 11-byte IMBE codeword).
OP25_CMD="cd $A && exec python3 rx.py --args soapy=0,driver=hackrf -N AMP:0,LNA:40,VGA:44 -S 2000000 -q 0 -o 25000 -T $TSV -V -w -u $PORT -v 9"
script -q -f -c "$OP25_CMD" "$R/results/op25_brpd_audio.log" >/dev/null 2>&1 &
OP25_PID=$!

echo "Recording BRPD dispatch (partial) talkgroups for $SECS s"
echo "  whitelist: $WL ($(wc -l < $WL) TGs)"
echo "  audio -> $R/recordings/  log -> results/op25_brpd_audio.log"
[ "$SECS" -eq 0 ] 2>/dev/null && echo "Ctrl-C to stop." || echo "running ${SECS}s."
wait $REC_PID
kill $OP25_PID 2>/dev/null
wait 2>/dev/null
echo "-> $(ls -1 "$R"/recordings/TG*.wav 2>/dev/null | wc -l) call(s) in recordings/"
