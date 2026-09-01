#!/usr/bin/env python3
"""Harvester tests: log text plus a temporary database, no radio and no files.

Never touches the real sdr.db.
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import enc_harvest  # noqa: E402
import sdr_db  # noqa: E402

# A grant on rx 9 for TG17051, a clear ESS on rx 9, then an ESS on rx 10 that
# belongs to no grant at all. Verbatim line shapes from results/op25_multi.log.
LOG = (
    '09/01/26 12:00:41.896175 [9] voice update:  tg(17051), rid(0), '
    'freq(851.837500), slot(-), prio(3)\n'
    '09/01/26 12:00:42.100000 [9] NAC 0x1bd LDU2: ESS: algid=80, keyid=0, '
    'mi=00 00 00 00 00 00 00 00 00, rs_errs=0\n'
    '09/01/26 12:00:42.200000 [10] NAC 0x1bd LDU2: ESS: algid=aa, keyid=22, '
    'mi=e0 99 ec a0 6b 7f 72 1a 00, rs_errs=0\n'
)


def _epoch(ts: str) -> float:
    return datetime.datetime.strptime(ts, '%m/%d/%y %H:%M:%S.%f').timestamp()


class Harvest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db = sdr_db.connect(self.tmp.name)
        sdr_db.upsert_call(
            self.db, file='TG17051_A_20260901-120041.wav', tgid=17051,
            start=_epoch('09/01/26 12:00:41.900000'), dur=3.0, freq=851837500)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        for suffix in ('', '-wal', '-shm'):
            try:
                os.unlink(self.tmp.name + suffix)
            except OSError:
                pass

    def row(self):
        return self.db.execute(
            'SELECT enc_observed, enc_evidence, enc_source, algid '
            'FROM calls WHERE tgid = 17051').fetchone()

    def test_binds_the_clear_ess_to_the_call(self):
        enc_harvest.harvest(self.db, LOG)
        r = self.row()
        self.assertEqual(r['enc_observed'], 'clear')
        self.assertEqual(r['enc_evidence'], 'ess')
        self.assertEqual(r['enc_source'], 'harvest')
        self.assertEqual(r['algid'], 0x80)

    def test_the_other_receivers_ess_is_unbound_not_borrowed(self):
        # The whole point: rx 10's 0xAA must not reach rx 9's clear call.
        stats = enc_harvest.harvest(self.db, LOG)
        self.assertEqual(stats['unbound'], 1)
        self.assertEqual(self.row()['enc_observed'], 'clear')

    def test_is_idempotent(self):
        enc_harvest.harvest(self.db, LOG)
        first = dict(self.row())
        enc_harvest.harvest(self.db, LOG)
        self.assertEqual(dict(self.row()), first)

    def test_speech_alone_marks_a_call_clear_when_no_ess_exists(self):
        sdr_db.upsert_call(
            self.db, file='TG17166_B_20260901-120100.wav', tgid=17166,
            start=_epoch('09/01/26 12:01:00.000000'), dur=2.0)
        sdr_db.set_transcript(self.db, 'TG17166_B_20260901-120100.wav',
                              '10-4, we are en route to the scene.')
        self.db.commit()
        enc_harvest.harvest(self.db, LOG)
        r = self.db.execute('SELECT enc_observed, enc_evidence FROM calls '
                            'WHERE tgid = 17166').fetchone()
        self.assertEqual(r['enc_observed'], 'clear')
        self.assertEqual(r['enc_evidence'], 'speech')

    def test_an_artifact_transcript_is_not_speech_evidence(self):
        sdr_db.upsert_call(
            self.db, file='TG17167_C_20260901-120200.wav', tgid=17167,
            start=_epoch('09/01/26 12:02:00.000000'), dur=1.0)
        sdr_db.set_transcript(self.db, 'TG17167_C_20260901-120200.wav',
                              'Thank you.')
        self.db.commit()
        enc_harvest.harvest(self.db, LOG)
        r = self.db.execute('SELECT enc_observed FROM calls '
                            'WHERE tgid = 17167').fetchone()
        self.assertIsNone(r['enc_observed'])


class Reconcile(unittest.TestCase):
    """Proposals only. Nothing here writes a reclassification."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db = sdr_db.connect(self.tmp.name)

    def tearDown(self):
        self.db.close()
        for suffix in ('', '-wal', '-shm'):
            try:
                os.unlink(self.tmp.name + suffix)
            except OSError:
                pass

    def add(self, tgid, n, observed, start=1788282000.0):
        """Speech-backed calls. ESS evidence comes from a log, not the database."""
        for i in range(n):
            f = f'TG{tgid}_X_{i}.wav'
            sdr_db.upsert_call(self.db, file=f, tgid=tgid, start=start + i, dur=1.0)
            self.db.execute('UPDATE calls SET enc_observed=?, enc_evidence=? '
                            'WHERE file=?', (observed, 'speech', f))
        self.db.commit()

    def ess_log(self, tgid, n, algid='aa', minute=5):
        """A grant plus n ESS lines for one talkgroup on one receiver."""
        head = (f'09/01/26 12:0{minute}:00.000000 [9] voice update:  tg({tgid}), '
                f'rid(0), freq(851.837500), slot(-), prio(3)\n')
        body = ''.join(
            f'09/01/26 12:0{minute}:0{i + 1}.000000 [9] NAC 0x1bd LDU2: '
            f'ESS: algid={algid}, keyid=8, '
            f'mi=00 00 00 00 00 00 00 00 00, rs_errs=0\n' for i in range(n))
        return head + body

    def test_proposes_clear_for_a_full_flagged_tg_observed_clear(self):
        self.add(17166, 21, 'clear')
        out = enc_harvest.reconcile(self.db, {'17166': {'enc': 'full'}}, min_obs=5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['proposed'], 'clear')

    def test_below_the_evidence_gate_nothing_is_proposed(self):
        # Small-N conclusions are not trustworthy.
        self.add(17166, 2, 'clear')
        self.assertEqual(
            enc_harvest.reconcile(self.db, {'17166': {'enc': 'full'}}, min_obs=5), [])

    def test_a_tg_carrying_both_is_proposed_partial_not_clear(self):
        self.add(17086, 20, 'clear')
        out = enc_harvest.reconcile(
            self.db, {'17086': {'enc': 'full'}}, min_obs=5,
            log_text=self.ess_log(17086, 4, 'aa'))
        self.assertEqual(out[0]['proposed'], 'partial')

    def test_agreement_is_not_reported(self):
        out = enc_harvest.reconcile(
            self.db, {'17053': {'enc': 'full'}}, min_obs=5,
            log_text=self.ess_log(17053, 8, 'aa'))
        self.assertEqual(out, [])


class Overrides(unittest.TestCase):
    def test_missing_file_is_empty_not_an_error(self):
        self.assertEqual(enc_harvest.load_overrides('/nonexistent.json'), {})

    def test_loads_int_keyed_enc_values(self):
        import json
        p = os.path.join(tempfile.mkdtemp(), 'o.json')
        with open(p, 'w') as f:
            json.dump({'17166': {'enc': 'clear', 'why': 'x', 'reviewed': 'y'}}, f)
        self.assertEqual(enc_harvest.load_overrides(p), {17166: 'clear'})

    def test_underscore_keys_are_documentation_not_talkgroups(self):
        import json
        p = os.path.join(tempfile.mkdtemp(), 'o.json')
        with open(p, 'w') as f:
            json.dump({'_comment': 'notes', '_example': {'enc': 'clear'},
                       '17166': {'enc': 'clear'}}, f)
        self.assertEqual(enc_harvest.load_overrides(p), {17166: 'clear'})


class ResolveEnc(unittest.TestCase):
    """The override layer, isolated from file and CLI concerns."""

    def test_override_wins_over_the_scraped_flag(self):
        ref = {'17166': {'enc': 'full'}}
        self.assertEqual(
            enc_harvest.resolve_enc(17166, ref, {17166: 'clear'}), 'clear')

    def test_without_an_override_the_scrape_stands(self):
        ref = {'17166': {'enc': 'full'}}
        self.assertEqual(enc_harvest.resolve_enc(17166, ref, {}), 'full')

    def test_unknown_talkgroup_is_none(self):
        self.assertIsNone(enc_harvest.resolve_enc(999, {}, {}))


class ApplyOverrides(unittest.TestCase):
    """Copies a reviewed decision onto the derived talkgroups table."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db = sdr_db.connect(self.tmp.name)
        self.db.execute(
            "INSERT INTO talkgroups (tgid, alpha, enc) VALUES (17166, 'TLK1', 'full')")
        self.db.commit()
        import json
        self.ov = os.path.join(tempfile.mkdtemp(), 'o.json')
        with open(self.ov, 'w') as f:
            json.dump({'_comment': 'docs',
                       '17166': {'enc': 'clear', 'why': 'x', 'reviewed': 'y'}}, f)

    def tearDown(self):
        self.db.close()
        for suffix in ('', '-wal', '-shm'):
            try:
                os.unlink(self.tmp.name + suffix)
            except OSError:
                pass

    def test_writes_the_reviewed_class_and_marks_it(self):
        n = enc_harvest.apply_overrides(self.db, self.ov)
        self.assertEqual(n, 1)
        r = self.db.execute('SELECT enc, enc_overridden FROM talkgroups '
                            'WHERE tgid = 17166').fetchone()
        self.assertEqual(r['enc'], 'clear')
        self.assertEqual(r['enc_overridden'], 1)

    def test_a_talkgroup_with_no_override_is_untouched(self):
        self.db.execute(
            "INSERT INTO talkgroups (tgid, alpha, enc) VALUES (17053, 'SO', 'full')")
        self.db.commit()
        enc_harvest.apply_overrides(self.db, self.ov)
        r = self.db.execute('SELECT enc, enc_overridden FROM talkgroups '
                            'WHERE tgid = 17053').fetchone()
        self.assertEqual(r['enc'], 'full')
        self.assertIsNone(r['enc_overridden'])


# A grant for TG19014 followed by ADP headers, and NO recorded call — the shape
# encrypted traffic actually takes: op25 -n silences the audio, so the recorder
# often produces nothing for the ESS to attach to.
ENCRYPTED_UNRECORDED = (
    '09/01/26 12:05:00.000000 [9] voice update:  tg(19014), rid(0), '
    'freq(851.837500), slot(-), prio(3)\n'
    '09/01/26 12:05:00.500000 [9] NAC 0x1bd LDU2: ESS: algid=aa, keyid=22, '
    'mi=e0 99 ec a0 6b 7f 72 1a 00, rs_errs=0\n'
    '09/01/26 12:05:01.000000 [9] NAC 0x1bd LDU2: ESS: algid=aa, keyid=22, '
    'mi=e0 99 ec a0 6b 7f 72 1a 00, rs_errs=0\n'
)


class TalkgroupEss(unittest.TestCase):
    """Per-talkgroup ESS evidence, independent of whether a call was recorded.

    This is the evidence call-binding discards. Measured on the real log: 168 of
    214 unbound observations are ADP, and TG19014 — which RadioReference calls
    'clear' — carries 90 of them against 3 recorded calls.
    """

    def test_counts_algids_for_a_talkgroup_with_no_recorded_calls(self):
        out = enc_harvest.talkgroup_ess(ENCRYPTED_UNRECORDED)
        self.assertEqual(out[19014][0xAA], 2)

    def test_attribution_is_still_per_receiver(self):
        # An ESS on a receiver with no grant belongs to no talkgroup.
        text = ENCRYPTED_UNRECORDED.replace('[9] NAC', '[11] NAC')
        self.assertEqual(enc_harvest.talkgroup_ess(text), {})

    def test_bit_error_algids_are_excluded(self):
        text = ENCRYPTED_UNRECORDED.replace('algid=aa', 'algid=0e')
        self.assertEqual(enc_harvest.talkgroup_ess(text), {})


class ReconcileWithUnrecordedEncryption(unittest.TestCase):
    """The TG19014 case: encrypted traffic that never lands in the database."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db = sdr_db.connect(self.tmp.name)

    def tearDown(self):
        self.db.close()
        for suffix in ('', '-wal', '-shm'):
            try:
                os.unlink(self.tmp.name + suffix)
            except OSError:
                pass

    def test_a_clear_flagged_tg_transmitting_adp_is_reported(self):
        # Nothing in `calls` at all — the whole point.
        out = enc_harvest.reconcile(
            self.db, {'19014': {'enc': 'clear'}}, min_obs=2,
            log_text=ENCRYPTED_UNRECORDED)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['tgid'], 19014)
        self.assertEqual(out[0]['proposed'], 'full')
        self.assertIn('ess', out[0]['evidence'])

    def test_ess_is_not_double_counted_when_a_call_was_recorded(self):
        sdr_db.upsert_call(self.db, file='TG19014_A.wav', tgid=19014,
                           start=1788282000.0, dur=1.0)
        self.db.execute("UPDATE calls SET enc_observed='encrypted', "
                        "enc_evidence='ess' WHERE tgid=19014")
        self.db.commit()
        out = enc_harvest.reconcile(
            self.db, {'19014': {'enc': 'clear'}}, min_obs=2,
            log_text=ENCRYPTED_UNRECORDED)
        # Two ESS lines in the log; the recorded call must not add a third.
        self.assertEqual(out[0]['encrypted'], 2)

    def test_speech_evidence_still_counts_without_a_log(self):
        for i in range(6):
            f = f'TG17166_{i}.wav'
            sdr_db.upsert_call(self.db, file=f, tgid=17166,
                               start=1788282000.0 + i, dur=1.0)
            self.db.execute("UPDATE calls SET enc_observed='clear', "
                            "enc_evidence='speech' WHERE file=?", (f,))
        self.db.commit()
        out = enc_harvest.reconcile(self.db, {'17166': {'enc': 'full'}}, min_obs=5)
        self.assertEqual(out[0]['proposed'], 'clear')


class PairKeys(unittest.TestCase):
    """Which (algid, keyid) groups are worth a brute-force run.

    Five distinct ADP key ids appear in this corpus (0x22, 0x8, 0x2F08, 0x1,
    0x2EF4). Each is a different key, so pooling their pairs into one run
    searches for a key that does not exist.
    """

    def line(self, algid, keyid, rs, ts='12:00:42.100000'):
        return (f'09/01/26 {ts} [9] NAC 0x1bd LDU2: ESS: algid={algid}, '
                f'keyid={keyid}, mi=00 00 00 00 00 00 00 00 00, rs_errs={rs}\n')

    def test_groups_each_key_id_separately(self):
        text = (self.line('aa', '22', 0, '12:00:42.100000')
                + self.line('aa', '22', 0, '12:00:43.100000')
                + self.line('aa', '8', 0, '12:00:44.100000')
                + self.line('aa', '8', 0, '12:00:45.100000'))
        self.assertEqual(sorted(enc_harvest.enc_pair_keys(text, min_obs=2)),
                         [(0xAA, 0x8), (0xAA, 0x22)])

    def test_clear_is_never_a_brute_force_target(self):
        text = self.line('80', '0', 0) + self.line('80', '0', 0, '12:00:43.100000')
        self.assertEqual(enc_harvest.enc_pair_keys(text, min_obs=2), [])

    def test_a_lone_key_id_seen_with_bit_errors_is_excluded(self):
        # 0x2EF4 appears once, with rs_errs set. Grouping on a corrupted KID
        # both invents a bogus run and strands real pairs away from a good one.
        text = (self.line('aa', '22', 0, '12:00:42.100000')
                + self.line('aa', '22', 0, '12:00:43.100000')
                + self.line('aa', '2ef4', 3, '12:00:44.100000'))
        self.assertEqual(enc_harvest.enc_pair_keys(text, min_obs=2),
                         [(0xAA, 0x22)])

    def test_a_single_clean_observation_is_below_the_gate(self):
        text = self.line('aa', '22', 0)
        self.assertEqual(enc_harvest.enc_pair_keys(text, min_obs=2), [])


if __name__ == '__main__':
    unittest.main()
