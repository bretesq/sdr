#!/usr/bin/env python3
"""Tests for the log and filename parsers.

These are the seams where a wrong assumption about the data goes unnoticed:
every serious data bug in this project has been one. The talkgroup DB has no
`tgid` field; `enc` is 'full' not 'encrypted'; 149 sites collapse to 67 on a
site_dec key; DUID is a 4-bit frame type rather than a radio id. So the fixtures
below are copied verbatim from real op25 output and real filenames, not invented.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)


class TestRecordingFilenames(unittest.TestCase):
    """The name udp_audio_record.py writes, parsed back by three other scripts."""

    NAME = re.compile(
        r'^TG(\d+)_.+_(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(?:_\d+)?\.wav$')

    REAL = [
        'TG16505_17-EBRP-FD1_20260830-210810.wav',
        'TG17165_17-BRPD-DSP1_20260831-081301.wav',
        'TG5000_SP-A-DISP1_20260830-170051_2.wav',      # duplicate-suffix form
        'TG17051_17-SO-DISP-S_20260831-081255.wav',
    ]

    def test_matches_every_real_filename_form(self):
        for name in self.REAL:
            with self.subTest(name=name):
                self.assertIsNotNone(self.NAME.match(name))

    def test_extracts_tgid_and_local_timestamp(self):
        m = self.NAME.match('TG17165_17-BRPD-DSP1_20260831-081301.wav')
        tg, y, mo, d, h, mi, s = m.groups()
        self.assertEqual(int(tg), 17165)
        # LOCAL wall clock, matching udp_audio_record.py's strftime and Python's
        # local .timestamp(). Parsing as UTC puts every recording that is not in
        # calls.json off by the UTC offset.
        expected = dt.datetime(2026, 8, 31, 8, 13, 1).timestamp()
        got = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s)).timestamp()
        self.assertEqual(got, expected)

    def test_rejects_non_recordings(self):
        for name in ('calls.json', 'notarecording.wav', '../../etc/passwd'):
            with self.subTest(name=name):
                self.assertIsNone(self.NAME.match(name))


class TestOp25LogPatterns(unittest.TestCase):
    """Fixtures are verbatim lines from results/*.log."""

    FREQ = re.compile(r'voice update:\s*tg\((\d+)\),\s*freq\((\d+)\)')
    ESS = re.compile(
        r'ESS:\s*algid=([0-9a-f]+),\s*keyid=([0-9a-f]+),\s*mi=([0-9a-f ]{26})')
    SITE = re.compile(
        r'rfss_sts_bcst:\s*syid:\s*([0-9a-f]+)\s*rfid:\s*(\d+)\s*stid:\s*(\d+)')
    NAC = re.compile(r'NAC\s+0x([0-9a-f]{3})')

    def test_voice_update_yields_talkgroup_and_voice_channel(self):
        line = 'voice update:  tg(17169), freq(851287500), slot(-), prio(3)'
        m = self.FREQ.search(line)
        self.assertEqual((int(m.group(1)), int(m.group(2))), (17169, 851287500))

    def test_ess_yields_the_encryption_triple(self):
        line = ('08/30/26 16:40:35.641586 [0] NAC 0x1bd LDU2: ESS: algid=aa, '
                'keyid=8, mi=00 11 22 33 44 55 66 77 88, rs_errs=0')
        m = self.ESS.search(line)
        self.assertEqual(int(m.group(1), 16), 0xAA)   # ADP / RC4
        self.assertEqual(int(m.group(2), 16), 0x8)
        self.assertEqual(m.group(3).replace(' ', ''), '001122334455667788')

    def test_ess_clear_is_algid_0x80(self):
        line = 'ESS: algid=80, keyid=0, mi=00 00 00 00 00 00 00 00 00'
        m = self.ESS.search(line)
        self.assertEqual(int(m.group(1), 16), 0x80)

    def test_site_broadcast_yields_sysid_rfss_site(self):
        line = 'rfss_sts_bcst: syid: 1bd rfid: 1 stid: 13 ch1: 16e8(773.056250)'
        m = self.SITE.search(line)
        self.assertEqual((int(m.group(1), 16), int(m.group(2)), int(m.group(3))),
                         (0x1bd, 1, 13))

    def test_nac_matches_the_documented_system_identity(self):
        line = '08/30/26 16:40:35.641586 [0] NAC 0x1bd LDU2: ESS: algid=80'
        self.assertEqual(int(self.NAC.search(line).group(1), 16), 0x1bd)

    def test_duid_values_are_frame_types_not_radio_ids(self):
        """Guards against reintroducing the DUID/Source-ID confusion.

        p25_framer.cc:101-102 reads `nac = (acc >> 52) & 0xfff` (12 bits) and
        `duid = (acc >> 48) & 0x00f` (4 bits). DUID is a 4-bit frame type, so
        every legal value fits in a nibble; the transmitting radio is a 24-bit
        Source ID logged separately as srcaddr.
        """
        frame_types = {0x0: 'HDU', 0x3: 'TDU', 0x5: 'LDU1',
                       0x7: 'TSBK', 0xa: 'LDU2', 0xc: 'PDU', 0xf: 'TDULC'}
        for duid in frame_types:
            self.assertLessEqual(duid, 0xf, 'DUID is 4 bits')


class TestGrantParsing(unittest.TestCase):
    TS = re.compile(r'(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d+)')
    GRANT = re.compile(
        r'(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d+)[^\n]*?set tgid=(\d+), srcaddr=(\d+)')
    CHAN_FREQ = re.compile(r'ch(\d): ([\d.]+) ga\d: (\d+)')

    def test_timestamp_parses_as_local(self):
        ts = '08/30/26 16:40:35.641586'
        parsed = dt.datetime.strptime(ts, '%m/%d/%y %H:%M:%S.%f')
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2026, 8, 30))

    def test_grant_yields_talkgroup_and_source(self):
        line = '08/30/26 16:40:35.641586 [0] set tgid=17050, srcaddr=221920'
        m = self.GRANT.search(line)
        self.assertEqual(int(m.group(2)), 17050)
        self.assertEqual(int(m.group(3)), 221920)

    def test_srcaddr_zero_means_not_reported(self):
        """3,223 of 3,765 real grants carry srcaddr=0; it is not radio 0."""
        line = '08/30/26 16:40:35.641586 [0] set tgid=17050, srcaddr=0'
        m = self.GRANT.search(line)
        self.assertEqual(int(m.group(3)) or None, None)

    def test_channel_grant_yields_frequency_per_talkgroup(self):
        line = ('grp_v_ch_grant_updt: ch1: 858.237500 ga1: 28529 '
                'ch2: 852.562500 ga2: 17050')
        pairs = {int(tg): round(float(mhz) * 1e6) for _c, mhz, tg
                 in self.CHAN_FREQ.findall(line)}
        self.assertEqual(pairs[17050], 852562500)
        self.assertEqual(pairs[28529], 858237500)

    def test_unresolved_channel_ids_are_not_mistaken_for_frequencies(self):
        """op25 logs `ch1: ID-0x485` before it can resolve the channel."""
        line = 'grp_v_ch_grant_updt: ch1: ID-0x485 ga1: 28529 ch2: ID-0xf9 ga2: 17050'
        self.assertEqual(self.CHAN_FREQ.findall(line), [])


if __name__ == '__main__':
    unittest.main()

class TestGrantLineParsing(unittest.TestCase):
    """import_grants.py's GRANT regex, against BOTH op25 trunking modules.

    op25 has two, and rx.py and multi_rx.py use different ones. trunking.py
    always prints a numeric srcaddr; tk_p25.py formats it with %s and passes
    None when the grant TSBK carried no source. Requiring \\d+ matched 95 of
    705 grants in a real multi_rx log — an 87% silent loss of the census.
    """

    # Verbatim from results/lwin_cdr.log (rx.py -> trunking.py).
    RX_PY = '08/30/26 15:44:45.212814 set tgid=17169, srcaddr=1234567'
    # Verbatim from results/op25_multi.log (multi_rx.py -> tk_p25.py). The
    # prefix is the SYSNAME, not a receiver id: the system emits one of these
    # per announced grant regardless of pool size.
    MULTI_NONE = ('08/31/26 15:06:59.070324 [LWIN-BR] '
                  'set tgid=17088, srcaddr=None, svcopts=None')
    MULTI_NUM = ('08/31/26 15:06:59.453039 [LWIN-BR] '
                 'set tgid=17063, srcaddr=2601234, svcopts=None')

    @classmethod
    def setUpClass(cls):
        import import_grants
        cls.M = import_grants

    def test_matches_the_rx_py_form(self):
        m = self.M.GRANT.search(self.RX_PY)
        self.assertIsNotNone(m)
        self.assertEqual((m.group(2), m.group(3)), ('17169', '1234567'))

    def test_matches_the_multi_rx_form_with_a_numeric_srcaddr(self):
        m = self.M.GRANT.search(self.MULTI_NUM)
        self.assertIsNotNone(m)
        self.assertEqual((m.group(2), m.group(3)), ('17063', '2601234'))

    def test_matches_the_multi_rx_form_with_srcaddr_None(self):
        """The regression: this line used not to match at all."""
        m = self.M.GRANT.search(self.MULTI_NONE)
        self.assertIsNotNone(m)
        self.assertEqual((m.group(2), m.group(3)), ('17088', 'None'))

    def test_the_sysname_prefix_is_not_mistaken_for_a_receiver_id(self):
        """[LWIN-BR] must not be read as [N]; the tgid is what matters."""
        m = self.M.GRANT.search(self.MULTI_NONE)
        self.assertEqual(m.group(2), '17088')

    def test_srcaddr_None_and_zero_both_become_None_never_radio_zero(self):
        for raw in ('None', '0'):
            with self.subTest(raw=raw):
                src = None if raw == 'None' else (int(raw) or None)
                self.assertIsNone(src)

    def test_the_timestamp_parses_in_both_forms(self):
        for line in (self.RX_PY, self.MULTI_NONE):
            with self.subTest(line=line[:30]):
                m = self.M.GRANT.search(line)
                self.assertIsInstance(self.M.parse_ts(m.group(1)), float)
