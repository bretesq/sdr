#!/usr/bin/env python3
"""Tests for the encrypted known-plaintext pair extractor (enc_pair.py).

These pin the ONE thing the old extract_enc_pair.py got wrong: which MI keyed
a given ciphertext codeword. The ground truth is op25's own decrypt path
(p25p1_fdma.cc process_LDU2 / process_voice), not the TIA standard:

  * process_LDU2 prints "ESS: mi=<next_mi>", then calls process_voice (which
    keys the voice with the OLD ess_mi), then does ess_mi = next_mi.
  * So the MI announced in an LDU2 applies to the NEXT superframe's codewords,
    never to the codewords printed directly beneath it.
  * HDU announces the MI for the first superframe; LDU1 never announces one.
  * Every log line is tagged "[N]" with the receiver id; multi_rx funnels many
    receivers into one log, so pairing must be per-receiver.

Fixture line SHAPES come from op25's exact fprintf format strings
(p25p1_fdma.cc:279 HDU ESS, :348 LDU2 ESS, :601 IMBE CIPHERTXT) since we have
no *encrypted* capture yet -- the real captures are all algid=80 (clear). MI
and codeword bytes are invented; only the line shape is real.
"""
from __future__ import annotations

import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import enc_pair


# --- fixture builders (op25 -v 10 line shapes) -----------------------------

def hdu(mi, rx=4, algid='aa', keyid='8'):
    return (f'08/31/26 18:51:19.000000 [{rx}] NAC 0x1bd HDU:  '
            f'ESS: tgid=17169, mfid=90, algid={algid}, keyid={keyid}, '
            f'mi={mi}, gly_errs=0, rs_errs=0')


def ldu2_ess(mi, rx=4, algid='aa', keyid='8'):
    return (f'08/31/26 18:51:20.100000 [{rx}] NAC 0x1bd LDU2: '
            f'ESS: algid={algid}, keyid={keyid}, mi={mi}, rs_errs=0')


def ldu1(rx=4):
    return (f'08/31/26 18:51:20.200000 [{rx}] NAC 0x1bd LDU1: '
            f'LCW: ec=0, pb=0, sf=0, lco=0 : 00 00 04 00 43 11 98 9a 83')


def ct(cw, rx=4, errs=0):
    return f'08/31/26 18:51:20.150000 [{rx}] IMBE (CIPHERTXT) {cw} errs {errs}'


def tdu15(rx=4):
    return f'08/31/26 18:51:24.000000 [{rx}] NAC 0x1bd TDU15:  '


MI_H = '11 11 11 11 11 11 11 11 00'
MI_1 = '22 22 22 22 22 22 22 22 00'
MI_2 = '33 33 33 33 33 33 33 33 00'

CT_A = '01 02 03 04 05 06 07 08 09 0a 0b'
CT_B = '11 12 13 14 15 16 17 18 19 1a 1b'
CT_C = '21 22 23 24 25 26 27 28 29 2a 2b'
CT_D = '31 32 33 34 35 36 37 38 39 3a 3b'


def tuples(pairs):
    """Reduce Pair objects to comparable tuples for assertions."""
    return [(p.rx_id, p.frame, p.position, ' '.join(p.mi), ' '.join(p.ct))
            for p in pairs]


class TestMiOffByOne(unittest.TestCase):
    def test_ldu2_codewords_use_previous_announced_mi_not_current(self):
        """The MI beneath an LDU2's ESS is next_mi; its codewords use the prior
        MI. HDU seeds the first superframe; LDU1 inherits the running MI."""
        log = '\n'.join([
            hdu(MI_H),
            ldu1(),         ct(CT_A),           # LDU1#1 -> MI_H
            ldu2_ess(MI_1), ct(CT_B),           # LDU2#1 -> MI_H (NOT MI_1)
            ldu1(),         ct(CT_C),           # LDU1#2 -> MI_1
            ldu2_ess(MI_2), ct(CT_D),           # LDU2#2 -> MI_1 (NOT MI_2)
        ])
        got = tuples(enc_pair.extract_pairs(log))
        self.assertEqual(got, [
            (4, 'LDU1', 0, MI_H, CT_A),
            (4, 'LDU2', 0, MI_H, CT_B),
            (4, 'LDU1', 0, MI_1, CT_C),
            (4, 'LDU2', 0, MI_1, CT_D),
        ])


class TestCodewordIndex(unittest.TestCase):
    def test_position_increments_within_a_frame(self):
        log = '\n'.join([
            hdu(MI_H),
            ldu2_ess(MI_1),
            ct(CT_A), ct(CT_B), ct(CT_C),       # positions 0, 1, 2
        ])
        got = [(p.frame, p.position) for p in enc_pair.extract_pairs(log)]
        self.assertEqual(got, [('LDU2', 0), ('LDU2', 1), ('LDU2', 2)])


class TestErrorFilter(unittest.TestCase):
    def test_nonzero_errs_codeword_is_dropped(self):
        """FEC-corrected codewords may be miscorrected; a KPA needs errs==0."""
        log = '\n'.join([
            hdu(MI_H),
            ldu2_ess(MI_1),
            ct(CT_A, errs=1),                   # dropped
            ct(CT_B, errs=0),                   # kept, at index 1
        ])
        got = tuples(enc_pair.extract_pairs(log))
        self.assertEqual(got, [(4, 'LDU2', 1, MI_H, CT_B)])


class TestPerReceiver(unittest.TestCase):
    def test_ciphertext_pairs_only_with_its_own_receiver_mi(self):
        """Two receivers in one log must not cross-pair."""
        log = '\n'.join([
            hdu(MI_H, rx=4),
            hdu(MI_2, rx=7),
            ldu2_ess(MI_1, rx=4),               # rx4 announces MI_1 (for next)
            ldu1(rx=7),
            ct(CT_A, rx=7),                     # rx7, under MI_2
            ct(CT_B, rx=4),                     # rx4, under MI_H (not MI_1)
        ])
        got = tuples(enc_pair.extract_pairs(log))
        self.assertCountEqual(got, [
            (4, 'LDU2', 0, MI_H, CT_B),
            (7, 'LDU1', 0, MI_2, CT_A),
        ])


class TestChainBreakOnNonTargetEss(unittest.TestCase):
    def test_unencrypted_ess_breaks_future_pairs_but_not_current_frame(self):
        """A non-ADP ESS announces the NEXT frame is clear; the current LDU2's
        codewords stay under the prior key, later frames are dropped."""
        log = '\n'.join([
            hdu(MI_H),
            ldu1(),                  ct(CT_A),  # MI_H
            ldu2_ess('00 00 00 00 00 00 00 00 00', algid='80', keyid='0'),
            ct(CT_B),                           # still MI_H (current frame)
            ldu1(),                  ct(CT_C),  # dropped: chain broken
        ])
        got = tuples(enc_pair.extract_pairs(log))
        self.assertEqual(got, [
            (4, 'LDU1', 0, MI_H, CT_A),
            (4, 'LDU2', 0, MI_H, CT_B),
        ])


class TestTerminatorReset(unittest.TestCase):
    def test_new_call_after_terminator_does_not_inherit_old_mi(self):
        log = '\n'.join([
            hdu(MI_H),
            ldu2_ess(MI_1), ct(CT_A),           # MI_H
            tdu15(),                            # call ends
            hdu(MI_2),                          # new call
            ldu1(), ct(CT_B),                   # MI_2, never MI_1
        ])
        got = tuples(enc_pair.extract_pairs(log))
        self.assertEqual(got, [
            (4, 'LDU2', 0, MI_H, CT_A),
            (4, 'LDU1', 0, MI_2, CT_B),
        ])


class TestUnencryptedCallProducesNothing(unittest.TestCase):
    def test_all_clear_call_yields_no_pairs(self):
        log = '\n'.join([
            hdu('00 00 00 00 00 00 00 00 00', algid='80', keyid='0'),
            ldu1(),
            ldu2_ess('00 00 00 00 00 00 00 00 00', algid='80', keyid='0'),
        ])
        self.assertEqual(enc_pair.extract_pairs(log), [])


class TxIndex(unittest.TestCase):
    """Codeword index since the HDU, used to rank known-plaintext candidates.

    A radio is keyed before the operator speaks, so the earliest codewords of a
    transmission are the likeliest to carry the idle codeword — the only known
    plaintext available against encrypted traffic.
    """

    def test_counts_from_the_hdu(self):
        log = '\n'.join([hdu(MI_H), ldu1(), ct(CT_A), ct(CT_B), ct(CT_C)])
        pairs = enc_pair.extract_pairs(log, algid=0xAA, keyid=8)
        self.assertEqual([p.tx_index for p in pairs], [0, 1, 2])

    def test_a_new_hdu_restarts_the_count(self):
        log = '\n'.join([hdu(MI_H), ldu1(), ct(CT_A), ct(CT_B),
                         tdu15(),
                         hdu(MI_1), ldu1(), ct(CT_C)])
        pairs = enc_pair.extract_pairs(log, algid=0xAA, keyid=8)
        self.assertEqual([p.tx_index for p in pairs], [0, 1, 0])

    def test_codewords_with_no_preceding_hdu_are_unranked(self):
        # op25 routinely joins a call already in progress. Those codewords may
        # still be idle, but nothing about their position argues for it, so they
        # must not compete with a real transmission start.
        log = '\n'.join([ldu2_ess(MI_1), ldu1(), ct(CT_A)])
        pairs = enc_pair.extract_pairs(log, algid=0xAA, keyid=8)
        self.assertTrue(all(p.tx_index == -1 for p in pairs), tuples(pairs))


class TxFromEnd(unittest.TestCase):
    """Distance to the end of the transmission, for ranking idle candidates.

    A radio stays keyed briefly after the operator stops talking, so the tail of
    a transmission carries idle frames for the same reason the head does. Unlike
    tx_index this survives op25 joining a call in progress, which is the case
    for every keyid 0x1 pair in the corpus (133 of 133 have tx_index -1).
    """

    def test_counts_backwards_from_the_last_codeword(self):
        log = '\n'.join([hdu(MI_H), ldu1(), ct(CT_A), ct(CT_B), ct(CT_C), tdu15()])
        pairs = enc_pair.extract_pairs(log, algid=0xAA, keyid=8)
        self.assertEqual([p.tx_from_end for p in pairs], [2, 1, 0])

    def test_a_transmission_with_no_observed_end_is_unranked(self):
        # The log stops mid-call: there is no tail, and inventing one would rank
        # arbitrary codewords as strong candidates.
        log = '\n'.join([hdu(MI_H), ldu1(), ct(CT_A), ct(CT_B)])
        pairs = enc_pair.extract_pairs(log, algid=0xAA, keyid=8)
        self.assertTrue(all(p.tx_from_end == -1 for p in pairs))

    def test_works_without_an_hdu(self):
        # The case that matters: joined in progress, so tx_index is unavailable,
        # but the TDU still marks the end.
        log = '\n'.join([ldu2_ess(MI_1), ldu1(), ct(CT_A), ct(CT_B), tdu15()])
        pairs = enc_pair.extract_pairs(log, algid=0xAA, keyid=8)
        self.assertTrue(all(p.tx_index == -1 for p in pairs))
        self.assertEqual([p.tx_from_end for p in pairs], [1, 0])

    def test_each_receiver_is_ranked_independently(self):
        log = '\n'.join([hdu(MI_H, rx=4), ldu1(rx=4), ct(CT_A, rx=4),
                         hdu(MI_1, rx=5), ldu1(rx=5), ct(CT_B, rx=5), ct(CT_C, rx=5),
                         tdu15(rx=5), tdu15(rx=4)])
        pairs = enc_pair.extract_pairs(log, algid=0xAA, keyid=8)
        by_rx = {}
        for p in pairs:
            by_rx.setdefault(p.rx_id, []).append(p.tx_from_end)
        self.assertEqual(by_rx[4], [0])
        self.assertEqual(by_rx[5], [1, 0])


if __name__ == '__main__':
    unittest.main()
