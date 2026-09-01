#!/usr/bin/env python3
"""Tests for the op25 stderr log parser.

Fixture line SHAPES are copied verbatim from a real op25 log in results/, or
built from the exact format string in op25's source where we have no capture
yet (tk_p25.py:2623). Guessing at the shape is how the FREQPAT bug survived:
op25 has two trunking modules whose `voice update` lines differ in three ways,
and the regex only ever matched one of them.

Radio unit IDs (`rid`, `srcaddr`) are the one thing NOT copied: they are real
24-bit identifiers of real radios, this is a public repo, and they have leaked
once already — it cost a history rewrite plus a repo recreate to purge (see
.gitignore:31 and the privacy note in README.md). Those values are invented
here. The parsers do not care what the number is.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import op25_log


# Verbatim from results/op25_record.log (rx.py -> trunking.py:1874).
RX_PY_VOICE = (
    '08/31/26 13:38:35.183953 voice update:  '
    'tg(17051), freq(852912500), slot(-), prio(3)\n'
)
# Verbatim from results/op25_record.log: p25p1_fdma.cc:327 prints
# "<ts> [id] NAC 0x1bd LDU2: " with NO newline, then :348 appends the ESS text,
# so the receiver id sits on the same line about 20 characters earlier.
ESS_RX0 = (
    '08/31/26 13:38:35.898304 [0] NAC 0x1bd LDU2: '
    'ESS: algid=80, keyid=0, mi=00 00 00 00 00 00 00 00 00, rs_errs=0\n'
)
# Verbatim from results/op25_record.log.
RFSS = ('08/31/26 13:38:30.000000 rfss_sts_bcst: '
        'syid: 1bd rfid: 1 stid: 13 ch1: 16e8(773.056250)\n')

# NO REAL UNIT IDs: rid() carries a real 24-bit radio unit ID and this is a
# public repo (see .gitignore:31). The values below are invented; only the line
# SHAPE comes from op25.
#
# Built from the exact format string at tk_p25.py:2623 --
#   "%s [%d] voice update:  tg(%d), rid(%d), freq(%f), slot(%s), prio(%d)\n"
# with freq passed as freq/1e6 (so MHz, not Hz) and get_slot(None) == '-'.
MULTI_RX2 = (
    '08/31/26 14:15:04.747731 [2] voice update:  '
    'tg(6848), rid(1234567), freq(769.593750), slot(-), prio(3)\n'
)
MULTI_RX3 = (
    '08/31/26 14:15:05.100000 [3] voice update:  '
    'tg(17165), rid(7654321), freq(772.681250), slot(-), prio(2)\n'
)
ESS_RX3 = (
    '08/31/26 14:15:05.200000 [3] NAC 0x1bd LDU2: '
    'ESS: algid=aa, keyid=8, mi=11 22 33 44 55 66 77 88 99, rs_errs=0\n'
)


def tail_over(text: str, rx_id=None) -> op25_log.LogTail:
    """One poll over a fixture, then close the handle.

    LogTail holds the file open to follow it; these tests poll once, so the
    handle is closed here rather than leaking a ResourceWarning per test.
    metadata() and current() read only in-memory state afterwards.
    """
    fh = tempfile.NamedTemporaryFile('w', suffix='.log', delete=False)
    fh.write(text)
    fh.close()
    try:
        t = op25_log.LogTail(fh.name, rx_id=rx_id)
        t.poll()
        if t.fh is not None:
            t.fh.close()
            t.fh = None
        return t
    finally:
        os.unlink(fh.name)


class TestRxPyFormat(unittest.TestCase):
    """The format lwin_listen.sh produces today must keep working."""

    def test_talkgroup_from_voice_update(self):
        self.assertEqual(tail_over(RX_PY_VOICE).current(), 17051)

    def test_frequency_in_hz_is_read_as_hz(self):
        self.assertEqual(tail_over(RX_PY_VOICE).metadata()['freq'], 852912500)

    def test_ess_is_read(self):
        md = tail_over(ESS_RX0).metadata()
        self.assertEqual((md['algid'], md['keyid']), (0x80, 0))
        self.assertEqual(md['mi'], '00' * 9)

    def test_site_identity_is_read(self):
        md = tail_over(RFSS).metadata()
        self.assertEqual((md['sysid'], md['rfss'], md['site']), (0x1bd, 1, 13))

    def test_stale_values_are_dropped_not_guessed(self):
        t = tail_over(RX_PY_VOICE)
        t.tg_t = time.time() - (op25_log.TG_TTL + 1)
        self.assertIsNone(t.current())


class TestMultiRxFormat(unittest.TestCase):
    """tk_p25.py adds rid(), logs freq in MHz, and prefixes the receiver id."""

    def test_mhz_float_frequency_is_converted_to_hz(self):
        self.assertEqual(tail_over(MULTI_RX2).metadata()['freq'], 769593750)

    def test_rid_becomes_src_addr(self):
        self.assertEqual(tail_over(MULTI_RX2).metadata()['src_addr'], 1234567)

    def test_talkgroup_is_read_despite_the_rid_field(self):
        self.assertEqual(tail_over(MULTI_RX2).current(), 6848)


class TestReceiverFiltering(unittest.TestCase):
    """N recorders share one log file; each must see only its own channel."""

    BOTH = MULTI_RX2 + MULTI_RX3

    def test_rx2_sees_only_its_own_call(self):
        t = tail_over(self.BOTH, rx_id=2)
        self.assertEqual(t.current(), 6848)
        self.assertEqual(t.metadata()['freq'], 769593750)

    def test_rx3_sees_only_its_own_call(self):
        t = tail_over(self.BOTH, rx_id=3)
        self.assertEqual(t.current(), 17165)
        self.assertEqual(t.metadata()['freq'], 772681250)

    def test_no_rx_id_sees_the_last_line_of_either(self):
        self.assertEqual(tail_over(self.BOTH).current(), 17165)

    def test_ess_is_attributed_by_receiver_across_the_ldu2_prefix(self):
        both = ESS_RX0 + ESS_RX3
        self.assertEqual(tail_over(both, rx_id=3).metadata()['algid'], 0xaa)
        self.assertEqual(tail_over(both, rx_id=0).metadata()['algid'], 0x80)

    def test_a_receiver_with_no_lines_reports_nothing_rather_than_guessing(self):
        t = tail_over(self.BOTH, rx_id=5)
        self.assertIsNone(t.current())
        self.assertIsNone(t.metadata()['freq'])

    def test_site_identity_is_shared_not_filtered(self):
        """Only the control receiver decodes rfss_sts_bcst; voice must inherit it."""
        t = tail_over(RFSS + self.BOTH, rx_id=3)
        self.assertEqual(t.metadata()['site'], 13)

    def test_two_digit_receiver_ids_do_not_collide(self):
        """[1] must not match a line belonging to [12], nor [12] to [2]."""
        line12 = MULTI_RX2.replace('[2]', '[12]')
        self.assertIsNone(tail_over(line12, rx_id=1).current())
        self.assertIsNone(tail_over(line12, rx_id=2).current())
        self.assertEqual(tail_over(line12, rx_id=12).current(), 6848)

    def test_an_ess_line_does_not_leak_across_a_newline(self):
        """rx 3's ESS must not be attached to rx 0 via the preceding line."""
        interleaved = (
            '08/31/26 14:15:05.100000 [0] NAC 0x1bd LDU2: \n'
            '08/31/26 14:15:05.200000 [3] NAC 0x1bd LDU2: '
            'ESS: algid=aa, keyid=8, mi=11 22 33 44 55 66 77 88 99, rs_errs=0\n'
        )
        self.assertIsNone(tail_over(interleaved, rx_id=0).metadata()['algid'])
        self.assertEqual(tail_over(interleaved, rx_id=3).metadata()['algid'], 0xaa)


# An encrypted call, then a grant for a DIFFERENT talkgroup. The ESS precedes
# the new grant, so it described the call that just ended.
ESS_THEN_NEW_GRANT = (
    '08/31/26 13:38:35.100000 [0] voice update:  '
    'tg(17086), freq(851837500), slot(-), prio(3)\n'
    '08/31/26 13:38:35.200000 [0] NAC 0x1bd LDU2: '
    'ESS: algid=aa, keyid=8, mi=00 00 00 00 00 00 00 00 00, rs_errs=0\n'
    '08/31/26 13:38:36.000000 [0] voice update:  '
    'tg(6848), freq(851837500), slot(-), prio(3)\n'
)
# The same lines, but the ESS arrives AFTER the new grant, so it is this call's.
NEW_GRANT_THEN_ESS = (
    '08/31/26 13:38:35.100000 [0] voice update:  '
    'tg(17086), freq(851837500), slot(-), prio(3)\n'
    '08/31/26 13:38:36.000000 [0] voice update:  '
    'tg(6848), freq(851837500), slot(-), prio(3)\n'
    '08/31/26 13:38:36.200000 [0] NAC 0x1bd LDU2: '
    'ESS: algid=aa, keyid=8, mi=00 00 00 00 00 00 00 00 00, rs_errs=0\n'
)


class TestEssDoesNotCrossCalls(unittest.TestCase):
    """The ESS line carries no tgid, so position is the only thing binding it.

    TG_TTL is 12 s, so without this an encrypted call's ALGID is still "fresh"
    when the next clear call starts and gets recorded against it. Measured in
    the corpus: 61 calls hold a non-clear algid while their transcripts are
    ordinary dispatch speech.

    poll() runs one finditer pass per pattern, which discards line ordering, so
    the fix compares buffer offsets rather than clearing inside the tg loop —
    that would simply be undone by the ESS loop later in the same poll.
    """

    def test_ess_before_a_new_grant_is_dropped(self):
        t = tail_over(ESS_THEN_NEW_GRANT, rx_id=0)
        self.assertEqual(t.current(), 6848)
        self.assertIsNone(t.metadata()['algid'])

    def test_ess_after_a_new_grant_is_kept(self):
        t = tail_over(NEW_GRANT_THEN_ESS, rx_id=0)
        self.assertEqual(t.current(), 6848)
        self.assertEqual(t.metadata()['algid'], 0xAA)

    def test_an_ess_with_no_grant_change_is_untouched(self):
        t = tail_over(ESS_RX0, rx_id=0)
        self.assertEqual(t.metadata()['algid'], 0x80)


if __name__ == '__main__':
    unittest.main()
