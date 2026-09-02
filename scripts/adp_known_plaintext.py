#!/usr/bin/env python3
"""Rank encrypted codewords by how likely their plaintext is the P25 idle frame.

THE KNOWN PLAINTEXT
-------------------
Recovering an ADP key needs a (MI, ciphertext, plaintext) triple, and the log
gives only the first two. The plaintext has to come from somewhere else: the
P25 idle/silence codeword, which a radio transmits whenever the vocoder has no
speech to send.

That codeword is measured here, not assumed. Across 18,558 IMBE (CLEARTEXT)
codewords op25 decoded off the air:

    04 0c fd 7b fb 7d f2 7b 3d 9e 44    811 occurrences, the single most common
    01 50 20 00 00 00 00 00 00 00 00      0 occurrences

The second value is what scripts/imbe_encode converges to when fed a synthetic
all-zero WAV, and earlier work adopted it as the known plaintext. Real radios
never transmit it: a live microphone carries room noise, and IMBE encodes noise
into varying codewords rather than the zero-input steady state. Any search using
it is looking for a key that cannot exist.

RANKING
-------
A radio is keyed before the operator speaks, so the first frames of a
transmission are usually idle. Measured on clear transmissions in the same logs,
the first voice codeword after an HDU is the idle frame about half the time,
against a 4.3% base rate across all codewords. tx_index therefore orders the
candidates, and a full 2^40 search costs ~3.2 h on this GPU, so ordering is
what makes the search affordable.

Usage:
  adp_known_plaintext.py [LOG] [--keyid 0x2F08] [--top N]
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import enc_harvest
import enc_pair

R = os.environ.get('SDR_ROOT', '/home/besquivel/rtl')

# The P25 idle/silence codeword, as transmitted. See the module docstring for
# the measurement; do NOT substitute the imbe_encode zero-WAV value.
IDLE_PT = '04 0c fd 7b fb 7d f2 7b 3d 9e 44'


def _distance(p) -> int:
    """Codewords between this one and the nearest end of its transmission.

    Both ends are idle-rich: a radio is keyed before the operator speaks and
    stays keyed after they stop. tx_index measures from the head, tx_from_end
    from the tail, and whichever is smaller is the better argument for this
    codeword being idle. Missing values do not compete -- an unseen HDU or an
    unseen TDU means no evidence, not evidence of distance.
    """
    both = [d for d in (p.tx_index, p.tx_from_end) if d >= 0]
    return min(both) if both else (1 << 30)


def rank(pairs: list) -> list:
    """Candidates ordered by how likely their plaintext is the idle codeword.

    Nearest to either end of a transmission first. A codeword with neither an
    HDU nor a TDU in the log sorts last: it may still be idle, but nothing
    about its position argues for it.

    Ranking from the tail as well as the head matters for exactly the case that
    stalled keyid 0x1 -- all 133 of its pairs have tx_index -1, because op25
    joined every one of those calls in progress. The TDU is still there.
    """
    return sorted(pairs, key=_distance)


def _write_superframes(algid: int, keyid: int, pairs: list, limit: int) -> None:
    """Write one adp_brute_cuda --pairs file per superframe.

    Codewords sharing an MI belong to one superframe, and adp_brute_cuda tests
    every codeword in a --pairs file against the SAME keystream. The RC4 PRGA
    runs to byte 469 whatever offset is wanted, so 18 candidates cost what 1
    does — measured 3.99 s vs 4.01 s per 400M keys. One superframe per run is
    therefore ~18 chances at the idle codeword for the price of one.

    Superframes are ordered by their earliest tx_index: a radio is keyed before
    the operator speaks, so the transmission's first superframe is the likeliest
    to contain an idle frame.
    """
    by_mi: dict = collections.OrderedDict()
    for p in pairs:
        by_mi.setdefault(' '.join(p.mi), []).append(p)

    def earliest(item):
        return min((_distance(q) for q in item[1]), default=1 << 30)

    for n, (mi, group) in enumerate(sorted(by_mi.items(), key=earliest)[:limit]):
        out = f'{R}/results/adp_sf_0x{algid:02X}_0x{keyid:X}_{n:02d}.txt'
        idxs = [_distance(q) for q in group if _distance(q) < (1 << 30)]
        with open(out, 'w') as f:
            f.write(f'# superframe {n}: {len(group)} codeword(s) sharing one MI\n')
            f.write(f'# MI {mi}\n')
            f.write(f'# closest to a transmission edge: '
                    f'{min(idxs) if idxs else "unknown"}\n')
            f.write(f'# run:\n#   {R}/adp_brute_cuda "{mi}" '
                    f'"{"00 " * 10}00" "{IDLE_PT}" 512 --pairs {out} --progress\n')
            for q in group:
                f.write(f'{q.frame.lower()} {q.position} {" ".join(q.ct)}\n')
        print(f'  superframe {n}: {len(group):>2} codewords, '
              f'edge distance={min(idxs) if idxs else "-"} -> {os.path.basename(out)}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('log', nargs='?', default=f'{R}/results/op25_multi.log')
    ap.add_argument('--keyid', default=None,
                    help='target key id, e.g. 0x2F08; default: every group found')
    ap.add_argument('--top', type=int, default=8,
                    help='candidates to print per key group')
    ap.add_argument('--superframes', type=int, default=5,
                    help='how many superframe --pairs files to write per key group')
    a = ap.parse_args()

    with open(a.log, errors='ignore') as f:
        text = f.read()

    groups = enc_harvest.enc_pair_keys(text)
    if a.keyid is not None:
        want = int(a.keyid, 0)
        groups = [g for g in groups if g[1] == want]
    if not groups:
        print(f'no matching key groups in {a.log}')
        return 0

    for algid, keyid in groups:
        pairs = enc_pair.extract_pairs(text, algid=algid, keyid=keyid)
        ranked = rank(pairs)
        _write_superframes(algid, keyid, pairs, a.superframes)
        starts = [p for p in ranked if 0 <= p.tx_index < 4]
        print(f'\n=== algid 0x{algid:02X} keyid 0x{keyid:X}: {len(pairs)} pair(s), '
              f'{len(starts)} within 4 codewords of a transmission start ===')
        print(f'tx_index spread: '
              f'{dict(collections.Counter(min(p.tx_index, 9) for p in ranked).most_common(6))}')
        out = f'{R}/results/adp_kp_0x{algid:02X}_0x{keyid:X}.txt'
        with open(out, 'w') as f:
            f.write(f'# ADP known-plaintext candidates, algid 0x{algid:02X} '
                    f'keyid 0x{keyid:X}\n')
            f.write(f'# PT is the P25 idle codeword measured off the air '
                    f'(811 of 18,558 cleartext codewords):\n#   {IDLE_PT}\n')
            f.write('# Ordered by tx_index: a radio is keyed before the operator\n'
                    '# speaks, so the earliest codewords are the likeliest idle.\n'
                    '# PT is a HYPOTHESIS the search tests, not a harvested fact.\n\n')
            for p in ranked:
                f.write(f'tx_index={p.tx_index} rx={p.rx_id} frame={p.frame} '
                        f'index={p.position}\n')
                f.write(f'MI  {" ".join(p.mi)}\n')
                f.write(f'CT  {" ".join(p.ct)}\n')
                f.write(f'CMD {R}/adp_brute_cuda "{" ".join(p.mi)}" '
                        f'"{" ".join(p.ct)}" "{IDLE_PT}" 512 '
                        f'--frame {p.frame.lower()} --position {p.position}\n\n')
        print(f'-> {out}')
        for p in ranked[:a.top]:
            print(f'  tx_index={p.tx_index:>3} rx={p.rx_id} {p.frame} idx={p.position}  '
                  f'MI={" ".join(p.mi[:4])}...  CT={" ".join(p.ct[:4])}...')
    return 0


if __name__ == '__main__':
    sys.exit(main())
