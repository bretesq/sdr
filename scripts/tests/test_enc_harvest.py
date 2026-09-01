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
        for i in range(n):
            f = f'TG{tgid}_X_{i}.wav'
            sdr_db.upsert_call(self.db, file=f, tgid=tgid, start=start + i, dur=1.0)
            self.db.execute('UPDATE calls SET enc_observed=?, enc_evidence=? '
                            'WHERE file=?', (observed, 'ess', f))
        self.db.commit()

    def test_proposes_clear_for_a_full_flagged_tg_observed_clear(self):
        self.add(17166, 21, 'clear')
        out = enc_harvest.reconcile(self.db, {'17166': {'enc': 'full'}}, min_obs=5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['proposed'], 'clear')

    def test_below_the_evidence_gate_nothing_is_proposed(self):
        # ESS reaches 19% of calls; small-N conclusions are not trustworthy.
        self.add(17166, 2, 'clear')
        self.assertEqual(
            enc_harvest.reconcile(self.db, {'17166': {'enc': 'full'}}, min_obs=5), [])

    def test_a_tg_carrying_both_is_proposed_partial_not_clear(self):
        self.add(17086, 20, 'clear')
        self.add(17086, 4, 'encrypted', start=1788283000.0)
        out = enc_harvest.reconcile(self.db, {'17086': {'enc': 'full'}}, min_obs=5)
        self.assertEqual(out[0]['proposed'], 'partial')

    def test_agreement_is_not_reported(self):
        self.add(17053, 10, 'encrypted')
        self.assertEqual(
            enc_harvest.reconcile(self.db, {'17053': {'enc': 'full'}}, min_obs=5), [])


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


if __name__ == '__main__':
    unittest.main()
