#!/usr/bin/env python3
"""Round-trip tests for the SQLite layer the recorder writes through.

These cover the functions standing between the operator and another
metadata-loss incident. The bug they exist to prevent: udp_audio_record.py used
to rewrite recordings/calls.json in its `finally` block with only the current
session's calls — a truncating write that took the file from 2,953 entries to 7
on a single 60-second run, and to 1 on the run after that. stt_watch.py's
transcript merges went into the same file and were clobbered a few minutes
later, which is why no transcript ever survived there.

Everything runs against a temporary database, never the real sdr.db.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sdr_db  # noqa: E402


class TestSchema(unittest.TestCase):
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

    def tables(self) -> set[str]:
        return {r[0] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    def test_connect_creates_the_schema(self):
        for t in ('talkgroups', 'sites', 'categories', 'calls', 'grants',
                  'sessions', 'algorithms'):
            self.assertIn(t, self.tables())

    def test_connect_is_idempotent(self):
        # connect() runs the schema script every time; a second open of the same
        # file must not fail or wipe anything.
        self.db.execute("INSERT INTO calls (file, start, dur) VALUES ('a.wav', 1, 1)")
        self.db.commit()
        second = sdr_db.connect(self.tmp.name)
        try:
            self.assertEqual(
                second.execute('SELECT COUNT(*) FROM calls').fetchone()[0], 1)
        finally:
            second.close()

    def test_sites_are_keyed_on_rfss_and_site(self):
        """site_dec alone is NOT unique: 149 sites share 67 site_dec values."""
        self.db.execute("INSERT INTO sites (rfss, site_dec, name_county) VALUES (1, 13, 'Baton Rouge')")
        self.db.execute("INSERT INTO sites (rfss, site_dec, name_county) VALUES (4, 13, 'Opelousas')")
        self.db.commit()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sites').fetchone()[0], 2)

    def test_enc_check_constraint_rejects_the_wrong_vocabulary(self):
        """'encrypted' is not a value in this data; 'full' is."""
        self.db.execute("INSERT INTO talkgroups (tgid, enc) VALUES (1, 'full')")
        with self.assertRaises(Exception):
            self.db.execute("INSERT INTO talkgroups (tgid, enc) VALUES (2, 'encrypted')")

    def test_algorithms_lookup_is_populated(self):
        row = self.db.execute('SELECT name FROM algorithms WHERE algid = 170').fetchone()
        self.assertEqual(row['name'], 'ADP / RC4')
        row = self.db.execute('SELECT name FROM algorithms WHERE algid = 128').fetchone()
        self.assertEqual(row['name'], 'Unencrypted')


class TestUpsertCall(unittest.TestCase):
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

    def count(self) -> int:
        return self.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]

    def row(self, file='a.wav'):
        return self.db.execute('SELECT * FROM calls WHERE file = ?', (file,)).fetchone()

    def test_insert(self):
        sdr_db.upsert_call(self.db, file='a.wav', tgid=17165, start=100.0, dur=1.5)
        self.db.commit()
        r = self.row()
        self.assertEqual((r['tgid'], r['start'], r['dur']), (17165, 100.0, 1.5))

    def test_appends_rather_than_replacing(self):
        """The whole point: a second call must not remove the first."""
        sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=1.0)
        sdr_db.upsert_call(self.db, file='b.wav', tgid=2, start=200.0, dur=2.0)
        self.db.commit()
        self.assertEqual(self.count(), 2)

    def test_reinsert_is_idempotent(self):
        for _ in range(3):
            sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=1.0)
        self.db.commit()
        self.assertEqual(self.count(), 1)

    def test_reinsert_never_drops_a_transcript(self):
        """stt_watch.py can write the transcript before the recorder's row lands."""
        sdr_db.set_transcript(self.db, 'a.wav', 'hello there')
        sdr_db.upsert_call(self.db, file='a.wav', tgid=17165, start=100.0, dur=1.5)
        self.db.commit()
        r = self.row()
        self.assertEqual(r['transcript'], 'hello there')
        self.assertEqual(r['tgid'], 17165)

    def test_reinsert_never_nulls_existing_metadata(self):
        """A later row carrying less detail must not erase what is known."""
        sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=1.0,
                           freq=851837500, algid=0xAA, keyid=8, mi='aabb')
        sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=1.0)
        self.db.commit()
        r = self.row()
        self.assertEqual(r['freq'], 851837500)
        self.assertEqual(r['algid'], 0xAA)
        self.assertEqual(r['keyid'], 8)
        self.assertEqual(r['mi'], 'aabb')

    def test_metadata_defaults_to_null_not_zero(self):
        """None must mean 'not observed', never a value."""
        sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=1.0)
        self.db.commit()
        r = self.row()
        for col in ('freq', 'algid', 'keyid', 'mi', 'rfss', 'site',
                    'nac', 'wacn', 'sysid', 'src_addr', 'ended_at'):
            self.assertIsNone(r[col], f'{col} should be NULL when not observed')

    def test_records_the_full_p25_metadata(self):
        sdr_db.upsert_call(self.db, file='a.wav', tgid=17165, start=100.0, dur=1.8,
                           ended_at=101.8, freq=858237500, algid=0x80, keyid=0,
                           mi='000000000000000000', rfss=1, site=13,
                           nac=0x1bd, sysid=0x1bd, src_addr=221920)
        self.db.commit()
        r = self.row()
        self.assertEqual(r['freq'], 858237500)
        self.assertEqual(r['nac'], 0x1bd)
        self.assertEqual(r['rfss'], 1)
        self.assertEqual(r['site'], 13)
        self.assertEqual(r['src_addr'], 221920)

    def test_tgid_may_be_null(self):
        """udp_audio_record.py emits TGunknown_*.wav when no grant matched."""
        sdr_db.upsert_call(self.db, file='TGunknown_x.wav', tgid=None,
                           start=100.0, dur=1.0)
        self.db.commit()
        self.assertIsNone(self.row('TGunknown_x.wav')['tgid'])


class TestTranscripts(unittest.TestCase):
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

    def test_set_transcript_updates_an_existing_call(self):
        sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=1.0)
        sdr_db.set_transcript(self.db, 'a.wav', 'dispatch to unit 12')
        self.db.commit()
        r = self.db.execute("SELECT transcript FROM calls WHERE file='a.wav'").fetchone()
        self.assertEqual(r['transcript'], 'dispatch to unit 12')

    def test_set_transcript_creates_a_stub_when_the_call_is_not_indexed_yet(self):
        """The watcher can win the race against the recorder."""
        sdr_db.set_transcript(self.db, 'orphan.wav', 'text')
        self.db.commit()
        r = self.db.execute("SELECT transcript FROM calls WHERE file='orphan.wav'").fetchone()
        self.assertEqual(r['transcript'], 'text')

    def test_transcripts_are_searchable_through_fts(self):
        sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=1.0)
        sdr_db.set_transcript(self.db, 'a.wav', 'looking for five five')
        self.db.commit()
        n = self.db.execute(
            "SELECT COUNT(*) FROM calls_fts WHERE calls_fts MATCH 'looking'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_fts_follows_an_updated_transcript(self):
        """The AFTER UPDATE trigger must remove the stale index entry."""
        sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=1.0)
        sdr_db.set_transcript(self.db, 'a.wav', 'aardvark')
        self.db.commit()
        sdr_db.set_transcript(self.db, 'a.wav', 'buffalo')
        self.db.commit()
        hits = lambda term: self.db.execute(  # noqa: E731
            'SELECT COUNT(*) FROM calls_fts WHERE calls_fts MATCH ?', (term,)).fetchone()[0]
        self.assertEqual(hits('buffalo'), 1)
        self.assertEqual(hits('aardvark'), 0, 'stale FTS entry left behind')


if __name__ == '__main__':
    unittest.main()


class TestSessions(unittest.TestCase):
    """The sessions table, which replaced web/listen.{pid,config,started}."""

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

    def open_session(self, config='{}', started=100.0):
        self.db.execute(
            'INSERT INTO sessions (config, started_at) VALUES (?, ?)',
            (config, started))
        return self.db.execute('SELECT last_insert_rowid()').fetchone()[0]

    def test_calls_are_attributed_to_a_session(self):
        sid = self.open_session()
        sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=1.0,
                           session_id=sid)
        self.db.commit()
        r = self.db.execute("SELECT session_id FROM calls WHERE file='a.wav'").fetchone()
        self.assertEqual(r['session_id'], sid)

    def test_a_call_without_a_session_is_allowed(self):
        """lwin_listen.sh run by hand sets no SDR_SESSION_ID; still record it."""
        sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=1.0,
                           session_id=None)
        self.db.commit()
        r = self.db.execute("SELECT session_id FROM calls WHERE file='a.wav'").fetchone()
        self.assertIsNone(r['session_id'])

    def test_reinsert_does_not_orphan_a_call_from_its_session(self):
        """COALESCE keeps the session when a later upsert carries none."""
        sid = self.open_session()
        sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=1.0,
                           session_id=sid)
        sdr_db.upsert_call(self.db, file='a.wav', tgid=1, start=100.0, dur=2.0)
        self.db.commit()
        r = self.db.execute("SELECT session_id, dur FROM calls WHERE file='a.wav'").fetchone()
        self.assertEqual(r['session_id'], sid)
        self.assertEqual(r['dur'], 2.0)

    def test_only_one_session_is_open_at_a_time_in_practice(self):
        """`ended_at IS NULL` is how the server finds the live session."""
        first = self.open_session(started=100.0)
        self.db.execute('UPDATE sessions SET ended_at = ? WHERE id = ?', (150.0, first))
        second = self.open_session(started=200.0)
        self.db.commit()
        open_rows = self.db.execute(
            'SELECT id FROM sessions WHERE ended_at IS NULL').fetchall()
        self.assertEqual([r['id'] for r in open_rows], [second])

    def test_session_history_survives_being_closed(self):
        """The point of a row over a pidfile: what ran, when, with what config."""
        sid = self.open_session(config='{"preset":"pd"}', started=100.0)
        self.db.execute('UPDATE sessions SET ended_at = ? WHERE id = ?', (160.0, sid))
        self.db.commit()
        r = self.db.execute('SELECT * FROM sessions WHERE id = ?', (sid,)).fetchone()
        self.assertEqual(r['config'], '{"preset":"pd"}')
        self.assertEqual(r['ended_at'] - r['started_at'], 60.0)
