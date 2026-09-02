#!/usr/bin/env bash
# Self-test for adp_brute_cuda --pairs: plant a key, encrypt ONE idle codeword
# inside an otherwise-random 18-codeword superframe, and confirm the search
# recovers both the key and WHICH codeword was idle.
#
# Complements adp_selftest.sh, which covers the single-codeword path. The thing
# under test here is that testing a whole superframe per key is both correct and
# free: the RC4 PRGA runs to byte 469 whatever offset is wanted, so 18
# candidates cost what 1 does.
set -e
R="${SDR_ROOT:-/home/besquivel/rtl}"
BIN="$R/adp_brute_cuda"
[ -x "$BIN" ] || { echo "no $BIN — build it first"; exit 1; }

SF=$(mktemp /tmp/adp_sf_XXXX.txt)
META=$(mktemp /tmp/adp_meta_XXXX.txt)
trap 'rm -f "$SF" "$META"' EXIT

# Key index kept small so a bounded shard reaches it in seconds.
KEYIDX=${KEYIDX:-987654}
python3 - "$KEYIDX" "$SF" "$META" <<'PY'
import secrets, sys
KEYIDX, sf_path, meta_path = int(sys.argv[1]), sys.argv[2], sys.argv[3]
# The P25 idle codeword as measured off the air (see adp_known_plaintext.py).
IDLE = [0x04,0x0c,0xfd,0x7b,0xfb,0x7d,0xf2,0x7b,0x3d,0x9e,0x44]
key5 = [(KEYIDX >> (8*i)) & 0xFF for i in range(5)]
mi = list(secrets.token_bytes(8)) + [0x00]
K = [(key5 + mi[:8])[i % 13] for i in range(256)]
S = list(range(256)); j = 0
for i in range(256):
    j = (j + S[i] + K[i]) & 0xFF; S[i], S[j] = S[j], S[i]
ks = []; ii = jj = 0
for _ in range(469):
    ii = (ii+1) & 0xFF; jj = (jj+S[ii]) & 0xFF
    S[ii], S[jj] = S[jj], S[ii]
    ks.append(S[(S[ii]+S[jj]) & 0xFF])
off = lambda fr, p: (0 if fr == 'ldu1' else 101) + p*11 + 267 + (2 if p >= 8 else 0)
# Exactly one idle codeword; the other 17 are random, as in a superframe of speech.
IDLE_FRAME, IDLE_POS = 'ldu2', 5
with open(sf_path, 'w') as f:
    for fr in ('ldu1', 'ldu2'):
        for p in range(9):
            o = off(fr, p)
            pt = IDLE if (fr == IDLE_FRAME and p == IDLE_POS) else list(secrets.token_bytes(11))
            ct = [pt[n] ^ ks[o+n] for n in range(11)]
            f.write(f"{fr} {p} {' '.join('%02x' % b for b in ct)}\n")
with open(meta_path, 'w') as f:
    f.write(' '.join('%02x' % b for b in mi) + '\n')
    f.write(' '.join('%02x' % b for b in IDLE) + '\n')
    f.write(' '.join('%02x' % b for b in key5) + '\n')
    f.write(f'{IDLE_FRAME} position {IDLE_POS}\n')
PY

MI=$(sed -n 1p "$META"); PT=$(sed -n 2p "$META")
WANT_KEY=$(sed -n 3p "$META"); WANT_CAND=$(sed -n 4p "$META")
DUMMY="00 00 00 00 00 00 00 00 00 00 00"

OUT=$("$BIN" "$MI" "$DUMMY" "$PT" 512 --pairs "$SF" --start 0 --count 2000000 2>&1)
echo "$OUT" | sed 's/^/  /'

GOT_KEY=$(echo "$OUT" | sed -n 's/^KEY FOUND: //p' | tr -s ' ' | sed 's/ $//')
GOT_CAND=$(echo "$OUT" | sed -n 's/^MATCHED CANDIDATE: //p')
fail=0
[ "$GOT_KEY" = "$WANT_KEY" ]   || { echo "FAIL key: want '$WANT_KEY' got '$GOT_KEY'"; fail=1; }
[ "$GOT_CAND" = "$WANT_CAND" ] || { echo "FAIL candidate: want '$WANT_CAND' got '$GOT_CAND'"; fail=1; }
[ $fail -eq 0 ] && echo "PASS: recovered the key and identified the idle codeword among 18"
exit $fail
