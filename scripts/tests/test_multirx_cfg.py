#!/usr/bin/env python3
"""Tests for the multi_rx config generator.

These are the assertions that would otherwise be discovered on the air as
"Unable to tune", as a channel silently paying an arb_resampler, or as a DC
spike sitting in a voice channel's passband. The numbers come from
docs/2026-08-31-wideband-multichannel.md and from op25's own
p25_demodulator_dev.get_decim / set_relative_frequency.
"""
from __future__ import annotations

import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import make_multirx_cfg as M


class TestDecimationAgreesWithOp25(unittest.TestCase):
    """A wrong if_rate is silent: every channel just gets an extra resampler."""

    def test_8_msps_resolves_via_25000(self):
        self.assertEqual(M.get_decim(8_000_000), (80, 4))
        self.assertEqual(M.if_rate_for(8_000_000), 25000)

    def test_12_msps_resolves_via_24000(self):
        self.assertEqual(M.get_decim(12_000_000), (125, 4))
        self.assertEqual(M.if_rate_for(12_000_000), 24000)

    def test_16_msps_resolves_via_25000(self):
        self.assertEqual(M.if_rate_for(16_000_000), 25000)

    def test_2_msps_matches_what_rx_py_uses_today(self):
        self.assertEqual(M.get_decim(2_000_000), (20, 4))

    def test_an_odd_quotient_is_refused(self):
        """25000 x 41: divisible, but get_decim skips odd quotients (`q & 1`).

        This is the branch that actually rejects rates, and it is easy to
        assume divisibility is sufficient.
        """
        self.assertIsNone(M.get_decim(1_025_000))
        with self.assertRaises(ValueError):
            M.if_rate_for(1_025_000)

    def test_a_rate_divisible_by_nothing_is_refused(self):
        self.assertIsNone(M.get_decim(7_000_001))
        with self.assertRaises(ValueError):
            M.if_rate_for(7_000_001)

    def test_7_msps_is_supported_despite_looking_odd(self):
        """Pinned because it surprised us: 7e6/25000 = 280, even -> (70, 4).

        Most round rates pass. Do not assume an unusual-looking rate fails;
        ask get_decim.
        """
        self.assertEqual(M.get_decim(7_000_000), (70, 4))


class TestUsableHalfSpan(unittest.TestCase):
    """p25_demodulator_dev bounds on if_rate (24-25 kHz), NOT if1 (96-100 kHz).

    multi_rx.py:62 imports p25_demodulator_dev, whose set_relative_frequency
    subtracts if_rate/2. The non-_dev p25_demodulator.py subtracts if1/2 and is
    NOT the module in use -- getting these confused makes every window figure
    50 kHz too pessimistic.
    """

    def test_8_msps(self):
        self.assertAlmostEqual(
            M.usable_half_span(8_000_000, 0.85, 25000), 3_387_500.0, places=1)

    def test_12_msps(self):
        self.assertAlmostEqual(
            M.usable_half_span(12_000_000, 0.85, 24000), 5_088_000.0, places=1)

    def test_10_msps_cannot_reach_the_800_leg(self):
        need = M.widest_offset(M.LEG_800)
        self.assertLess(M.usable_half_span(10_000_000, 0.85, 25000), need)

    def test_each_leg_fits_its_own_configured_rate(self):
        for leg in (M.LEG_700, M.LEG_800):
            with self.subTest(leg=leg['name']):
                limit = M.usable_half_span(
                    leg['rate'], 0.85, M.if_rate_for(leg['rate']))
                self.assertLess(M.widest_offset(leg), limit)


class TestTwoDeviceConfig(unittest.TestCase):
    CFG = None

    @classmethod
    def setUpClass(cls):
        cls.CFG = M.build([M.LEG_700, M.LEG_800],
                          whitelist='/tmp/wl.txt',
                          cc_whitelist='/tmp/cc.txt',
                          tgid_tags='')

    def test_two_devices_one_per_leg(self):
        self.assertEqual(len(self.CFG['devices']), 2)
        self.assertEqual({d['name'] for d in self.CFG['devices']}, {'one', 'pro'})

    def test_both_devices_are_not_tunable(self):
        """multi_rx.py:754 drops every channel after the first on a tunable device."""
        for d in self.CFG['devices']:
            with self.subTest(dev=d['name']):
                self.assertFalse(d['tunable'])

    def test_devices_are_selected_by_serial_not_index(self):
        """A replug reorders indices; verified both open with soapy=0 + serial."""
        for d in self.CFG['devices']:
            with self.subTest(dev=d['name']):
                self.assertIn('serial=00000000000000', d['args'])
                self.assertTrue(d['args'].startswith('soapy=0,driver=hackrf,'))

    def test_each_radio_keeps_its_own_measured_gains(self):
        by = {d['name']: d for d in self.CFG['devices']}
        self.assertEqual(by['one']['gains'], 'AMP:0,LNA:40,VGA:20')
        self.assertEqual(by['pro']['gains'], 'AMP:0,LNA:40,VGA:44')

    def test_rates_and_centres_match_the_plan(self):
        by = {d['name']: d for d in self.CFG['devices']}
        self.assertEqual((by['one']['rate'], by['one']['frequency']),
                         (8_000_000, 771_418_500))
        self.assertEqual((by['pro']['rate'], by['pro']['frequency']),
                         (12_000_000, 855_725_000))

    def test_exactly_one_pinned_control_channel(self):
        cc = [c for c in self.CFG['channels'] if c['whitelist'] == '/tmp/cc.txt']
        self.assertEqual(len(cc), 1)
        self.assertEqual(cc[0]['frequency'], 773_056_250)

    def test_the_control_channel_lives_on_the_700_device(self):
        """773.05625 is in the 700 leg; the 800 leg has no live control channel."""
        cc = next(c for c in self.CFG['channels']
                  if c['whitelist'] == '/tmp/cc.txt')
        self.assertEqual(cc['device'], 'one')

    def test_voice_channels_exist_on_both_devices(self):
        devs = {c['device'] for c in self.CFG['channels']
                if c['whitelist'] == '/tmp/wl.txt'}
        self.assertEqual(devs, {'one', 'pro'})

    def test_every_channel_uses_ITS_OWN_devices_if_rate(self):
        """The whole point of two devices at different rates."""
        rate_by_dev = {d['name']: d['rate'] for d in self.CFG['devices']}
        for ch in self.CFG['channels']:
            with self.subTest(chan=ch['name']):
                self.assertEqual(ch['if_rate'],
                                 M.if_rate_for(rate_by_dev[ch['device']]))

    def test_the_two_devices_really_do_want_different_if_rates(self):
        """If these ever matched, the per-device plumbing would be untested."""
        self.assertNotEqual(M.if_rate_for(8_000_000), M.if_rate_for(12_000_000))

    def test_udp_ports_are_unique_and_spaced_at_least_two_apart(self):
        ports = sorted(int(c['destination'].rsplit(':', 1)[1])
                       for c in self.CFG['channels'])
        self.assertEqual(len(ports), len(set(ports)))
        for a, b in zip(ports, ports[1:]):
            self.assertGreaterEqual(b - a, 2)

    def test_destinations_are_loopback_not_0_0_0_0(self):
        for ch in self.CFG['channels']:
            with self.subTest(chan=ch['name']):
                self.assertTrue(ch['destination'].startswith('udp://127.0.0.1:'))

    def test_crypt_behavior_records_encrypted_calls_as_silence(self):
        """2 would make find_talkgroup skip them; 1 matches today's rx.py -n."""
        for ch in self.CFG['channels']:
            with self.subTest(chan=ch['name']):
                self.assertEqual(ch['crypt_behavior'], 1)

    def test_one_trunked_system_covering_both_legs(self):
        chans = self.CFG['trunking']['chans']
        self.assertEqual(len(chans), 1)
        self.assertEqual(chans[0]['nac'], '0x1bd')

    def test_only_live_control_channels_are_in_the_rotation(self):
        """851.0375/851.4875 measured +0.5 dB, 0% continuity: dead.

        Listing a dead control channel makes op25's next_cc rotation stall on
        it for seconds at a time.
        """
        ccl = self.CFG['trunking']['chans'][0]['control_channel_list']
        self.assertIn('773.05625', ccl)
        self.assertNotIn('851.0375', ccl)
        self.assertNotIn('851.4875', ccl)

    def test_no_audio_section_this_host_has_no_sound_card(self):
        self.assertNotIn('audio', self.CFG)

    def test_it_validates(self):
        M.validate(self.CFG, [M.LEG_700, M.LEG_800])

    def test_channel_count(self):
        """1 pinned control + n_voice per leg."""
        want = 1 + M.LEG_700['n_voice'] + M.LEG_800['n_voice']
        self.assertEqual(len(self.CFG['channels']), want)


class TestSingleLegStillWorks(unittest.TestCase):
    """The 700-leg-only config, for bring-up with one radio."""

    def test_one_leg_builds_and_validates(self):
        cfg = M.build([M.LEG_700], whitelist='/tmp/wl.txt',
                      cc_whitelist='/tmp/cc.txt', tgid_tags='')
        self.assertEqual(len(cfg['devices']), 1)
        M.validate(cfg, [M.LEG_700])

    def test_a_leg_with_no_control_channel_alone_is_rejected(self):
        """The 800 leg cannot stand alone: nothing would hold the CC."""
        with self.assertRaises(ValueError) as e:
            M.build([M.LEG_800], whitelist='/tmp/wl.txt',
                    cc_whitelist='/tmp/cc.txt', tgid_tags='')
        self.assertIn('control', str(e.exception).lower())


class TestValidationCatchesRealMistakes(unittest.TestCase):

    def _cfg(self, legs):
        return M.build(legs, whitelist='/tmp/wl.txt',
                       cc_whitelist='/tmp/cc.txt', tgid_tags='')

    def test_a_frequency_outside_its_device_window_is_rejected(self):
        leg = dict(M.LEG_700, voice=M.LEG_700['voice'] + [860_237_500])
        with self.assertRaises(ValueError) as e:
            M.validate(self._cfg([leg]), [leg])
        self.assertIn('outside', str(e.exception).lower())

    def test_a_centre_sitting_on_a_channel_is_rejected(self):
        leg = dict(M.LEG_700, centre=M.LEG_700['voice'][0])
        with self.assertRaises(ValueError) as e:
            M.validate(self._cfg([leg]), [leg])
        self.assertIn('dc', str(e.exception).lower())

    def test_a_rate_too_narrow_for_its_leg_is_rejected(self):
        leg = dict(M.LEG_800, rate=10_000_000)
        with self.assertRaises(ValueError) as e:
            M.validate(self._cfg([M.LEG_700, leg]), [M.LEG_700, leg])
        self.assertIn('outside', str(e.exception).lower())

    def test_a_tunable_device_with_several_channels_is_rejected(self):
        cfg = self._cfg([M.LEG_700])
        cfg['devices'][0]['tunable'] = True
        with self.assertRaises(ValueError) as e:
            M.validate(cfg, [M.LEG_700])
        self.assertIn('tunable', str(e.exception).lower())

    def test_a_mismatched_if_rate_is_rejected(self):
        cfg = self._cfg([M.LEG_700])
        cfg['channels'][1]['if_rate'] = 24000       # 8 Msps wants 25000
        with self.assertRaises(ValueError) as e:
            M.validate(cfg, [M.LEG_700])
        self.assertIn('if_rate', str(e.exception))

    def test_colliding_udp_ports_are_rejected(self):
        cfg = self._cfg([M.LEG_700])
        cfg['channels'][1]['destination'] = cfg['channels'][0]['destination']
        with self.assertRaises(ValueError) as e:
            M.validate(cfg, [M.LEG_700])
        self.assertIn('port', str(e.exception).lower())

    def test_two_devices_must_not_share_a_serial(self):
        cfg = self._cfg([M.LEG_700, M.LEG_800])
        cfg['devices'][1]['args'] = cfg['devices'][0]['args']
        with self.assertRaises(ValueError) as e:
            M.validate(cfg, [M.LEG_700, M.LEG_800])
        self.assertIn('serial', str(e.exception).lower())

    def test_zero_voice_channels_is_rejected(self):
        leg = dict(M.LEG_700, n_voice=0)
        with self.assertRaises(ValueError):
            self._cfg([leg])


if __name__ == '__main__':
    unittest.main()
