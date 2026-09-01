#!/usr/bin/env python3
"""Parsing and binding tests for the encryption-fact harvester.

Every log fragment below is copied verbatim from results/op25_multi.log. The
formats are load-bearing: two spaces after "voice update:", MHz floats for freq,
and a trailing rs_errs the ESS line does not always carry.

Radio unit IDs are the one thing not copied — rid(0) here is invented. Real
srcaddr values identify real radios and have leaked from this repo once
already; see the note at the top of test_op25_log.py.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import enc_log  # noqa: E402

GRANT = ('09/01/26 12:00:41.896175 [9] voice update:  tg(17051), rid(0), '
         'freq(851.837500), slot(-), prio(3)\n')
ESS_ENC = ('09/01/26 12:00:43.585551 [10] NAC 0x1bd LDU2: ESS: algid=aa, '
           'keyid=22, mi=e0 99 ec a0 6b 7f 72 1a 00, rs_errs=4\n')
ESS_CLEAR = ('09/01/26 12:00:44.131919 [9] NAC 0x1bd LDU2: ESS: algid=80, '
             'keyid=0, mi=00 00 00 00 00 00 00 00 00, rs_errs=0\n')


class ParseLog(unittest.TestCase):
    def test_parses_a_grant_with_mhz_freq_as_hz(self):
        grants, _ = enc_log.parse_log(GRANT)
        self.assertEqual(len(grants), 1)
        g = grants[0]
        self.assertEqual(g.rx_id, 9)
        self.assertEqual(g.tgid, 17051)
        # calls.freq is Hz; this log line is MHz.
        self.assertEqual(g.freq, 851837500)

    def test_parses_ess_fields(self):
        _, obs = enc_log.parse_log(ESS_ENC)
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual((o.rx_id, o.algid, o.keyid, o.rs_errs), (10, 0xAA, 0x22, 4))
        self.assertEqual(o.mi, 'e0 99 ec a0 6b 7f 72 1a 00')

    def test_timestamps_are_local_epoch_seconds_and_ordered(self):
        _, obs = enc_log.parse_log(ESS_ENC + ESS_CLEAR)
        self.assertEqual(len(obs), 2)
        self.assertLess(obs[0].ts, obs[1].ts)
        self.assertGreater(obs[0].ts, 1_700_000_000)   # a real epoch, not 1970

    def test_strips_ansi_so_a_coloured_log_still_parses(self):
        # op25 runs under `script`, which preserves terminal escapes.
        coloured = '\x1b[0m' + GRANT.replace('[9]', '\x1b[32m[9]\x1b[0m')
        grants, _ = enc_log.parse_log(coloured)
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].rx_id, 9)

    def test_ess_without_rs_errs_defaults_to_zero(self):
        line = ('09/01/26 12:00:44.131919 [9] NAC 0x1bd LDU2: ESS: algid=80, '
                'keyid=0, mi=00 00 00 00 00 00 00 00 00\n')
        _, obs = enc_log.parse_log(line)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0].rs_errs, 0)

    def test_lines_without_a_receiver_id_are_ignored(self):
        # Binding is per-receiver; an observation that cannot be attributed to a
        # receiver cannot be attributed to a call either.
        _, obs = enc_log.parse_log(ESS_ENC.replace('[10] ', ''))
        self.assertEqual(obs, [])


class RealLog(unittest.TestCase):
    """Guards against op25 changing its log format under us.

    Points at the main checkout, since results/ is gitignored and absent from a
    worktree. Skipped when the log is missing so the suite still runs on a clean
    clone.
    """

    LOG = '/home/besquivel/rtl/results/op25_multi.log'

    def setUp(self):
        if not os.path.exists(self.LOG):
            self.skipTest('results/op25_multi.log not present')
        with open(self.LOG, errors='ignore') as f:
            self.grants, self.obs = enc_log.parse_log(f.read())

    def test_finds_grants_and_ess_in_the_real_log(self):
        self.assertGreater(len(self.grants), 100)
        self.assertGreater(len(self.obs), 100)

    def test_real_observations_carry_plausible_algids(self):
        algids = {o.algid for o in self.obs}
        # 0x80 clear and 0xAA ADP are both known present in this corpus.
        self.assertIn(0x80, algids)
        self.assertIn(0xAA, algids)

    def test_real_grants_carry_plausible_talkgroups_and_receivers(self):
        self.assertTrue(all(1 <= g.tgid < 100000 for g in self.grants))
        # multi_rx funnels several receivers into one log.
        self.assertGreater(len({g.rx_id for g in self.grants}), 1)


if __name__ == '__main__':
    unittest.main()
