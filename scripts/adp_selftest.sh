#!/usr/bin/env bash
# Self-test for adp_brute: generate a random 5-byte key + 8-byte MI, build the
# keystream with the exact op25 KSA, encrypt a fake 11-byte LDU2 codeword, then
# hand adp_brute the MI + ciphertext + plaintext and confirm it recovers the key.
set -e
R=/home/besquivel/rtl
BIN=$R/adp_brute

# 1) random 5-byte key + 9-byte MI (hex strings, space-separated).
# adp_brute demands a 9-byte MI: it copies the first 8 bytes into the 13-byte
# RC4 schedule; the 9th byte is the trailing 0x00 that op25's ESS logs as the
# final MI byte.
KEY=$(python3 -c "import secrets;print(' '.join('%02X' % b for b in secrets.token_bytes(5)))")
MI=$(python3 -c "import secrets;print(' '.join('%02X' % b for b in secrets.token_bytes(9)))")

# 2) keystream[368..378] via the same KSA op25 uses
CS=$(python3 - "$KEY" "$MI" <<'PY'
import sys
key_hex = sys.argv[1].split()
mi_hex = sys.argv[2].split()
adp_key = [0]*13
for i, v in enumerate(key_hex):
    adp_key[i] = int(v, 16)
for i in range(5, 13):
    adp_key[i] = int(mi_hex[i-5], 16)
K = [adp_key[i % 13] for i in range(256)]
S = list(range(256))
j = 0
for i in range(256):
    j = (j + S[i] + K[i]) & 0xFF
    S[i], S[j] = S[j], S[i]
i = j = 0
ks = []
for _ in range(469):
    i = (i+1) & 0xFF
    j = (j + S[i]) & 0xFF
    S[i], S[j] = S[j], S[i]
    ks.append(S[(S[i]+S[j]) & 0xFF])
print(' '.join('%02X' % ks[368+n] for n in range(11)))
PY
)

# 3) fake 11-byte plaintext -> ciphertext = pt ^ keystream
PT=$(python3 -c "import secrets;print(' '.join('%02X' % b for b in secrets.token_bytes(11)))")
CT=$(python3 - "$PT" "$CS" <<'PY'
import sys
pt = [int(x, 16) for x in sys.argv[1].split()]
cs = [int(x, 16) for x in sys.argv[2].split()]
print(' '.join('%02X' % (pt[i] ^ cs[i]) for i in range(11)))
PY
)

echo "expected key: $KEY"
echo "MI: $MI"
echo "CT: $CT"
echo "PT: $PT"
echo "--- running 2^40 brute force (8 threads) ---"
# Quote each hex field: adp_brute parses one space-separated hex string per arg.
time "$BIN" "$MI" "$CT" "$PT" 8
echo "selftest done"
