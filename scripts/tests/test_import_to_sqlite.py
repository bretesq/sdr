#!/usr/bin/env python3
"""Coverage for import_to_sqlite.py's recovery path writing derived columns.

stt_watch.py names this script as its recovery path when a transcript never
makes it into the database through the normal watch loop. import_calls() used
to write `calls.transcript` directly, bypassing set_transcript() entirely:
transcript_norm/codes_text/codes_set_id/codes_rev stayed NULL and no
call_codes rows were created, then the FTS rebuild indexed those NULLs —
silently sinking any call recovered this way. import_calls() now routes the
transcript write through set_transcript() so recovered calls get the same
derived columns and call_codes rows a live transcript would.

Runs entirely against a temporary database and a temporary recordings
directory — never the real sdr.db or recordings/.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import import_to_sqlite  # noqa: E402
import sdr_db  # noqa: E402


def _write_silent_wav(path: str, seconds: float = 1.0, rate: int = 8000) -> None:
    with wave.open(path, 'w') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b'\x00\x00' * int(rate * seconds))


class TestImportCallsWritesDerivedColumns(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp_db.close()
        self.rec_dir = tempfile.mkdtemp()
        self._orig_rec = import_to_sqlite.REC
        import_to_sqlite.REC = self.rec_dir

        self.file = 'TG17170_17-BRPD-TLK3_20260830-210810.wav'
        _write_silent_wav(os.path.join(self.rec_dir, self.file))
        with open(os.path.join(self.rec_dir, self.file[:-4] + '.txt'), 'w') as f:
            f.write('Zachary, 43 is 1042.')

        self.db = sdr_db.connect(self.tmp_db.name)
        self.db.execute(
            "INSERT INTO talkgroups (tgid, alpha, cat, tag) VALUES "
            "(17170, '17-BRPD TLK3', "
            "'East Baton Rouge Parish (17) - Baton Rouge Police', 'Law Talk')")
        self.db.commit()

    def tearDown(self):
        import_to_sqlite.REC = self._orig_rec
        self.db.close()
        os.unlink(self.tmp_db.name)
        shutil.rmtree(self.rec_dir)

    def test_import_calls_populates_derived_columns(self):
        import_to_sqlite.import_calls(self.db, False)
        self.db.commit()
        row = self.db.execute(
            'SELECT transcript, transcript_norm, codes_set_id, codes_rev '
            'FROM calls WHERE file = ?', (self.file,)).fetchone()
        self.assertEqual(row['transcript'], 'Zachary, 43 is 1042.')
        self.assertEqual(row['transcript_norm'], 'Zachary, 43 is 10-42.')
        self.assertEqual(row['codes_set_id'], 'la-brpd-law')
        self.assertTrue(row['codes_rev'])

    def test_import_calls_writes_call_codes_rows(self):
        import_to_sqlite.import_calls(self.db, False)
        self.db.commit()
        rows = self.db.execute(
            'SELECT raw, canonical FROM call_codes').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['raw'], '1042')
        self.assertEqual(rows[0]['canonical'], '10-42')

    def test_import_calls_leaves_the_row_searchable_by_code_meaning(self):
        import_to_sqlite.import_calls(self.db, False)
        self.db.commit()
        self.db.execute("INSERT INTO calls_fts(calls_fts) VALUES('rebuild')")
        self.db.commit()
        hit = self.db.execute(
            "SELECT rowid FROM calls_fts WHERE calls_fts MATCH 'duty'").fetchone()
        self.assertIsNotNone(hit)

    def test_dry_run_writes_nothing(self):
        inserted, transcripts = import_to_sqlite.import_calls(self.db, True)
        self.assertEqual((inserted, transcripts), (1, 1))
        row = self.db.execute(
            'SELECT * FROM calls WHERE file = ?', (self.file,)).fetchone()
        self.assertIsNone(row)


if __name__ == '__main__':
    unittest.main()
