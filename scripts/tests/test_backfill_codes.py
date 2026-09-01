#!/usr/bin/env python3
"""Backfill re-derivation tests.

The property under test is the one the whole design rests on: every derived
artifact can be recomputed from scratch, so a corrected code meaning
retroactively improves history.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backfill_codes  # noqa: E402
import sdr_db  # noqa: E402


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db = sdr_db.connect(self.tmp.name)
        self.db.execute(
            "INSERT INTO talkgroups (tgid, alpha, cat, tag) VALUES "
            "(17170, '17-BRPD TLK3', "
            "'East Baton Rouge Parish (17) - Baton Rouge Police', 'Law Talk')")
        for n, text in enumerate(['Zachary, 43 is 1042.', '10-4, 4-25', 'nothing here']):
            self.db.execute(
                'INSERT INTO calls (file, start, dur, transcript) VALUES (?,?,?,?)',
                (f'TG17170_x_2026083{n}-000000.wav', 0, 0, text))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def _snapshot(self):
        return (
            self.db.execute(
                'SELECT file, transcript, transcript_norm, codes_text, '
                'codes_set_id, codes_rev FROM calls ORDER BY file').fetchall(),
            self.db.execute(
                'SELECT raw, canonical, kind, meaning, confidence, off_start, '
                'off_end FROM call_codes ORDER BY call_id, off_start').fetchall(),
        )

    def test_backfill_populates_derived_columns(self):
        backfill_codes.backfill(self.db)
        row = self.db.execute(
            "SELECT transcript_norm FROM calls WHERE transcript = 'Zachary, 43 is 1042.'"
        ).fetchone()
        self.assertEqual(row['transcript_norm'], 'Zachary, 43 is 10-42.')

    def test_backfill_never_alters_the_raw_transcript(self):
        before = self.db.execute(
            'SELECT file, transcript FROM calls ORDER BY file').fetchall()
        backfill_codes.backfill(self.db)
        after = self.db.execute(
            'SELECT file, transcript FROM calls ORDER BY file').fetchall()
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in after])

    def test_backfill_is_idempotent(self):
        backfill_codes.backfill(self.db)
        first = [[tuple(r) for r in part] for part in self._snapshot()]
        backfill_codes.backfill(self.db)
        second = [[tuple(r) for r in part] for part in self._snapshot()]
        self.assertEqual(first, second)

    def test_full_rederivation_from_a_wiped_state(self):
        backfill_codes.backfill(self.db)
        expected = [[tuple(r) for r in part] for part in self._snapshot()]

        self.db.execute('DELETE FROM call_codes')
        self.db.execute('UPDATE calls SET transcript_norm = NULL, '
                        'codes_text = NULL, codes_set_id = NULL, codes_rev = NULL')
        self.db.commit()

        backfill_codes.backfill(self.db)
        got = [[tuple(r) for r in part] for part in self._snapshot()]
        self.assertEqual(got, expected)

    def test_only_stale_skips_current_rows(self):
        backfill_codes.backfill(self.db)
        stats = backfill_codes.backfill(self.db, only_stale=True)
        self.assertEqual(stats['updated'], 0)
        self.assertEqual(stats['skipped'], 3)

    def test_only_stale_repairs_a_row_with_a_wrong_rev(self):
        backfill_codes.backfill(self.db)
        self.db.execute("UPDATE calls SET codes_rev = 'stale' "
                        "WHERE transcript = '10-4, 4-25'")
        self.db.commit()
        stats = backfill_codes.backfill(self.db, only_stale=True)
        self.assertEqual(stats['updated'], 1)


if __name__ == '__main__':
    unittest.main()
