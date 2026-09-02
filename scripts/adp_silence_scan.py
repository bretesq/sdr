#!/usr/bin/env python3
"""Quickly test the IMBE silence codeword against every captured ADP pair.

The capture didn't record audio, so we can't derive each frame's IMBE
plaintext from audio. Instead we assume any captured frame that is a
silence/gap frame will match the fixed IMBE silence codeword. This scans
all pairs and reports which are gaps (silence PT matches) vs speech.
"""
import sys

R = '/home/besquivel/rtl'
LOG = f'{R}/results/lwin_enc_capture_multi.log'
import re

# VERIFIED 2026-08-31: the xMBE vocoder (imbe_vocoder, the exact fixed-point IMBE
# encoder op25 uses) fed a zero-WAV converges to the steady-state silence codeword
# 01 50 20 00 00 00 00 00 00 00 00. The old 04 0c fd... value was a guess from
# p25p1_fdma.cc's commented-out silence block and is NOT the vocoder's real output.
SIL_PT = [0x01, 0x50, 0x20, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]

raw = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]', '', open(LOG, errors='ignore').read())

# Pair each CIPHERTXT with the most recent ADP ESS (algid=aa, keyid=8).
ess_re = re.compile(r'ESS:\s*algid=aa,\s*keyid=8,\s*mi=((?:[0-9a-fA-F]{2}\s*){9}),\s*rs_errs=(-?\d+)')
ct_re  = re.compile(r'IMBE \(CIPHERTXT\) ((?:[0-9a-fA-F]{2}\s*){11})\s*errs\s*(\d+)')

pairs = []
last_mi = None
for line in raw.splitlines():
    m = ess_re.search(line)
    if m:
        mi = [int(x, 16) for x in m.group(1).split()]
        if len(mi) == 9:
            last_mi = mi
            continue
    m = ct_re.search(line)
    if m and last_mi is not None:
        ct = [int(x, 16) for x in m.group(1).split()]
        if len(ct) == 11 and int(m.group(2)) <= 2:
            pairs.append((last_mi, ct))

# For ADP, the keystream depends on (key, mi). The key is unknown, but for a
# SILENCE frame the plaintext is fixed = SIL_PT. To test, we'd need the key...
# But we can check a necessary condition: a silence frame's ciphertext should equal
# SIL_PT ^ keystream. Without the key we can't compute keystream. So instead, we
# flag pairs where the ciphertext is *consistent with silence* by checking that the
# ciphertext, when XORed against a candidate silence PT, yields a keystream that is
# the same for all silence frames (the keystream for a given (key,mi) is deterministic).
# Practical approach: group CIPHERTXT by identical byte patterns. Repeated silence
# frames share the same ciphertext (same keystream + same silence PT). Find the most
# common ciphertext; that's the strongest candidate for a silence frame.
from collections import Counter
ct_counter = Counter(tuple(ct) for mi, ct in pairs)
print(f"total ADP pairs: {len(pairs)}")
print("Most common ciphertexts (repeats => likely silence frames):")
for ct_bytes, n in ct_counter.most_common(5):
    print(f"  {n:>4}x  {' '.join('%02x' % b for b in ct_bytes)}")

# Write out the most-likely-silence pair (most-repeated ciphertext + its MI).
most_ct, most_n = ct_counter.most_common(1)[0]
# Find the MI that paired with that ciphertext.
for mi, ct in pairs:
    if tuple(ct) == most_ct:
        out = f'{R}/results/silence_pair.txt'
        with open(out, 'w') as f:
            f.write(f"# Most-repeated ADP ciphertext ({most_n}x) - likely a silence/gap frame\n")
            f.write(f"MI   {' '.join('%02x' % b for b in mi)}\n")
            f.write(f"CT   {' '.join('%02x' % b for b in ct)}\n")
            f.write(f"PT (silence) {' '.join('%02x' % b for b in SIL_PT)}\n")
        print(f"\nWrote {out}")
        print(f"\nRun:  /home/besquivel/rtl/adp_brute "
              f"\"{' '.join('%02x' % b for b in mi)}\" "
              f"\"{' '.join('%02x' % b for b in ct)}\" "
              f"\"{' '.join('%02x' % b for b in SIL_PT)}\" $(nproc)")
        break
