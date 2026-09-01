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


if __name__ == '__main__':
    unittest.main()
