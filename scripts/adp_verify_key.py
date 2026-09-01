#!/usr/bin/env python3
"""Check a recovered ADP key against harvested ciphertext.

A brute force reports a key when ONE codeword matches the guessed plaintext.
That is 2^-88 by chance, so it is almost certainly right -- but "almost
certainly" is not a verification, and two of the three plaintexts this project
previously trusted were wrong. This decrypts every harvested codeword and asks
whether the results look like IMBE.

The test that carries the weight is not the idle-codeword count. It is that a
CORRECT key turns ciphertext into the codewords radios actually transmit, while
a wrong key yields uniform random bytes. The reference set is measured from
IMBE (CLEARTEXT) lines op25 decoded off the air.

Usage:
  adp_verify_key.py <key_hex> <superframe.txt> [more.txt ...]
  adp_verify_key.py 00 00 00 07 01 --files results/adp_sf_0xAA_0x8_*.txt
"""
from __future__ import annotations

import argparse
import glob
import re
import sys

# Six-byte prefixes of the most common codewords seen over the air. Prefixes,
# not whole codewords: the trailing bytes carry the fine detail that varies
# between otherwise-identical frames.
KNOWN_PREFIXES = {
    '04 0c fd 7b fb 7d',   # idle / silence, 811 occurrences
    '18 3e 92 32 1e 40',   # 222
    '18 40 6f cf e1 ff',   # 196
    'fc 00 00 00 00 00',   # 146
}
IDLE = '04 0c fd 7b fb 7d f2 7b 3d 9e 44'


def keystream(key5: list, mi: list) -> list:
    """op25's ADP schedule: 5 key bytes then 8 MI bytes, cycled over 256."""
    sched = key5 + mi[:8]
    K = [sched[i % 13] for i in range(256)]
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + K[i]) & 0xFF
        S[i], S[j] = S[j], S[i]
    out = []
    ii = jj = 0
    for _ in range(469):
        ii = (ii + 1) & 0xFF
        jj = (jj + S[ii]) & 0xFF
        S[ii], S[jj] = S[jj], S[ii]
        out.append(S[(S[ii] + S[jj]) & 0xFF])
    return out


def offset(frame: str, position: int) -> int:
    """base + position*11 + 267 (+2 at position 8); LDU1 base 0, LDU2 base 101."""
    return (0 if frame == 'ldu1' else 101) + position * 11 + 267 + (2 if position >= 8 else 0)


def check(key5: list, paths: list) -> dict:
    idle = plausible = total = 0
    mis = set()
    for path in paths:
        text = open(path).read()
        m = re.search(r'# MI (.+)', text)
        if not m:
            continue
        mi = [int(x, 16) for x in m.group(1).split()]
        mis.add(tuple(mi))
        ks = keystream(key5, mi)
        for line in text.splitlines():
            if not line.startswith('ldu'):
                continue
            fr, pos, *ct = line.split()
            ct = [int(x, 16) for x in ct]
            o = offset(fr, int(pos))
            pt = ' '.join('%02x' % (ct[n] ^ ks[o + n]) for n in range(11))
            total += 1
            if pt == IDLE:
                idle += 1
            if pt[:17] in KNOWN_PREFIXES:
                plausible += 1
    return {'idle': idle, 'plausible': plausible, 'total': total, 'mis': len(mis)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('key', nargs='+', help='5 key bytes in hex')
    ap.add_argument('--files', nargs='+', required=True)
    a = ap.parse_args()

    key5 = [int(x, 16) for x in a.key]
    if len(key5) != 5:
        sys.stderr.write('key must be 5 bytes\n')
        return 1
    paths = [p for pat in a.files for p in sorted(glob.glob(pat))]
    if not paths:
        sys.stderr.write('no superframe files matched\n')
        return 1

    good = check(key5, paths)
    # Same key with the last byte flipped: the control. Anything a correct key
    # scores has to be compared against what an incorrect one scores, or the
    # number means nothing.
    bad = check(key5[:4] + [key5[4] ^ 1], paths)

    kh = ' '.join('%02x' % b for b in key5)
    print(f'{good["total"]} codewords across {good["mis"]} distinct MI(s)\n')
    print(f'  key {kh}          idle {good["idle"]:>4}   plausible IMBE '
          f'{good["plausible"]:>4}/{good["total"]} ({100*good["plausible"]/good["total"]:.0f}%)')
    print(f'  same key, 1 bit off  idle {bad["idle"]:>4}   plausible IMBE '
          f'{bad["plausible"]:>4}/{bad["total"]} ({100*bad["plausible"]/bad["total"]:.0f}%)')
    verdict = good['plausible'] > 4 * max(bad['plausible'], 1)
    print(f'\n{"VERIFIED" if verdict else "NOT VERIFIED"}: a correct key should '
          f'produce real IMBE codewords far more often than a wrong one.')
    return 0 if verdict else 2


if __name__ == '__main__':
    sys.exit(main())
