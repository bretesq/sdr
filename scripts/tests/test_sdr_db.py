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
import sqlite3
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


class TestCodeMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db = sdr_db.connect(self.tmp.name)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def _columns(self):
        return {r[1] for r in self.db.execute('PRAGMA table_info(calls)')}

    def test_derived_columns_exist(self):
        for col in ('transcript_norm', 'codes_text', 'codes_set_id', 'codes_rev'):
            self.assertIn(col, self._columns())

    def test_call_codes_table_exists(self):
        self.db.execute('SELECT count(*) FROM call_codes')

    def test_migration_is_idempotent(self):
        before = self._columns()
        self.db.close()
        self.db = sdr_db.connect(self.tmp.name)
        self.assertEqual(self._columns(), before)

    def test_fts_indexes_the_derived_columns(self):
        cols = [r[1] for r in self.db.execute('PRAGMA table_info(calls_fts)')]
        self.assertEqual(cols[:2], ['transcript_norm', 'codes_text'])


class TestMigrationFromLegacySchema(unittest.TestCase):
    """Simulates the real sdr.db as it exists today: a `calls` table with no
    derived columns and the old single-column `calls_fts`. This is the branch
    that will actually run against the 3,220-row production database in a
    later task, and TestCodeMigration's tempfile-from-scratch setup never
    exercises it (SCHEMA there creates the new-shape calls_fts directly, so
    _migrate is a no-op ALTER + an already-matching FTS drop/recreate).
    """

    _LEGACY_SCHEMA = """
        CREATE TABLE calls (
          id         INTEGER PRIMARY KEY,
          file       TEXT NOT NULL UNIQUE,
          tgid       INTEGER,
          start      REAL NOT NULL,
          dur        REAL NOT NULL DEFAULT 0,
          transcript TEXT,
          src_addr   INTEGER,
          algid      INTEGER,
          rfss       INTEGER,
          site       INTEGER
        );
        CREATE VIRTUAL TABLE calls_fts USING fts5(
          transcript, content = 'calls', content_rowid = 'id'
        );
        CREATE TRIGGER calls_ai AFTER INSERT ON calls BEGIN
          INSERT INTO calls_fts(rowid, transcript) VALUES (new.id, new.transcript);
        END;
        CREATE TRIGGER calls_ad AFTER DELETE ON calls BEGIN
          INSERT INTO calls_fts(calls_fts, rowid, transcript) VALUES('delete', old.id, old.transcript);
        END;
        CREATE TRIGGER calls_au AFTER UPDATE ON calls BEGIN
          INSERT INTO calls_fts(calls_fts, rowid, transcript) VALUES('delete', old.id, old.transcript);
          INSERT INTO calls_fts(rowid, transcript) VALUES (new.id, new.transcript);
        END;
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        legacy = sqlite3.connect(self.tmp.name)
        legacy.executescript(self._LEGACY_SCHEMA)
        legacy.execute(
            "INSERT INTO calls (file, start, dur, transcript) VALUES "
            "('old.wav', 0, 0, 'Zachary 43 copy')")
        legacy.commit()
        legacy.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_migrating_a_legacy_db_adds_columns_and_preserves_data(self):
        db = sdr_db.connect(self.tmp.name)
        cols = {r[1] for r in db.execute('PRAGMA table_info(calls)')}
        for col in ('transcript_norm', 'codes_text', 'codes_set_id', 'codes_rev'):
            self.assertIn(col, cols)
        fts_cols = [r[1] for r in db.execute('PRAGMA table_info(calls_fts)')]
        self.assertEqual(fts_cols[:2], ['transcript_norm', 'codes_text'])
        row = db.execute("SELECT transcript FROM calls WHERE file = 'old.wav'").fetchone()
        self.assertEqual(row['transcript'], 'Zachary 43 copy')
        self.assertEqual(
            db.execute('PRAGMA user_version').fetchone()[0], sdr_db._USER_VERSION)
        db.close()

    def test_migrating_a_legacy_db_keeps_search_working_immediately(self):
        """The dark-window regression: without seeding transcript_norm from
        transcript before the rebuild, every pre-migration row would have
        transcript_norm = NULL and 'rebuild' would index nothing for any of
        them, making the whole existing corpus unsearchable until a later
        backfill task overwrites the placeholder with normalized text."""
        db = sdr_db.connect(self.tmp.name)
        hit = db.execute(
            "SELECT rowid FROM calls_fts WHERE calls_fts MATCH 'Zachary'").fetchone()
        self.assertIsNotNone(hit)
        db.close()

    def test_fts_is_not_rebuilt_on_a_second_connect(self):
        """PRAGMA user_version alone cannot prove the guard works: an
        unguarded _migrate that re-sets user_version = 1 every time would
        still pass an assertion on that PRAGMA's value. A 'rebuild' command
        recomputes the FTS index purely from the content table, discarding
        any row not backed by one — so a row inserted directly into
        calls_fts, with no matching row in calls, is a canary: it survives
        only if no rebuild fires on the second connect.
        """
        db = sdr_db.connect(self.tmp.name)
        db.execute(
            "INSERT INTO calls_fts(rowid, transcript_norm, codes_text) "
            "VALUES (999999, 'zzzcanary', '')")
        db.commit()
        db.close()

        db = sdr_db.connect(self.tmp.name)
        hit = db.execute(
            "SELECT rowid FROM calls_fts WHERE calls_fts MATCH 'zzzcanary'").fetchone()
        self.assertIsNotNone(hit, 'a second connect rebuilt calls_fts and discarded it')
        db.close()


class TestMigrationFromPartiallyMigratedTriggers(unittest.TestCase):
    """A database with the legacy single-column calls_fts but only ONE of
    the three legacy triggers present by that name.

    This is not expected on the real sdr.db (all three have existed since
    before this feature), but SCHEMA's own `CREATE TRIGGER IF NOT EXISTS`
    runs before _migrate on every connect, so if it ever finds a trigger
    name missing it creates that one against the NEW column names while
    calls_fts is still old-shaped underneath it. If _migrate's own seeding
    UPDATE ran before dropping the mismatched trigger, that trigger would
    fire on the UPDATE and fail with "table calls_fts has no column named
    transcript_norm" — a real failure hit and fixed while adding the
    seeding step, not a hypothetical.
    """

    _PARTIAL_LEGACY_SCHEMA = """
        CREATE TABLE calls (
          id         INTEGER PRIMARY KEY,
          file       TEXT NOT NULL UNIQUE,
          tgid       INTEGER,
          start      REAL NOT NULL,
          dur        REAL NOT NULL DEFAULT 0,
          transcript TEXT,
          src_addr   INTEGER,
          algid      INTEGER,
          rfss       INTEGER,
          site       INTEGER
        );
        CREATE VIRTUAL TABLE calls_fts USING fts5(
          transcript, content = 'calls', content_rowid = 'id'
        );
        CREATE TRIGGER calls_ai AFTER INSERT ON calls BEGIN
          INSERT INTO calls_fts(rowid, transcript) VALUES (new.id, new.transcript);
        END;
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        legacy = sqlite3.connect(self.tmp.name)
        legacy.executescript(self._PARTIAL_LEGACY_SCHEMA)
        legacy.execute(
            "INSERT INTO calls (file, start, dur, transcript) VALUES "
            "('old.wav', 0, 0, 'hello world')")
        legacy.commit()
        legacy.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_migration_does_not_crash_and_keeps_search_working(self):
        db = sdr_db.connect(self.tmp.name)
        row = db.execute(
            "SELECT transcript_norm FROM calls WHERE file = 'old.wav'").fetchone()
        self.assertEqual(row['transcript_norm'], 'hello world')
        hit = db.execute(
            "SELECT rowid FROM calls_fts WHERE calls_fts MATCH 'hello'").fetchone()
        self.assertIsNotNone(hit)
        db.close()


class TestTgidFromFilename(unittest.TestCase):
    def test_parses_the_tg_prefix(self):
        self.assertEqual(
            sdr_db.tgid_from_filename('TG16505_17-EBRP-FD1_20260830-210810.wav'),
            16505)

    def test_returns_none_for_an_unparseable_name(self):
        self.assertIsNone(sdr_db.tgid_from_filename('something-else.wav'))


class TestSetTranscriptWritesCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db = sdr_db.connect(self.tmp.name)
        self.db.execute(
            "INSERT INTO talkgroups (tgid, alpha, cat, tag) VALUES "
            "(17170, '17-BRPD TLK3', "
            "'East Baton Rouge Parish (17) - Baton Rouge Police', 'Law Talk')")
        self.db.commit()
        self.file = 'TG17170_17-BRPD-TLK3_20260830-210810.wav'

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_transcript_column_is_the_raw_text(self):
        sdr_db.set_transcript(self.db, self.file, 'Zachary, 43 is 1042.')
        row = self.db.execute(
            'SELECT transcript, transcript_norm FROM calls WHERE file = ?',
            (self.file,)).fetchone()
        self.assertEqual(row['transcript'], 'Zachary, 43 is 1042.')
        self.assertEqual(row['transcript_norm'], 'Zachary, 43 is 10-42.')

    def test_call_codes_row_is_written(self):
        sdr_db.set_transcript(self.db, self.file, 'Zachary, 43 is 1042.')
        rows = self.db.execute(
            'SELECT raw, canonical, kind, meaning, confidence FROM call_codes'
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['raw'], '1042')
        self.assertEqual(rows[0]['canonical'], '10-42')
        self.assertEqual(rows[0]['confidence'], 'medium')

    def test_reindexing_replaces_rather_than_duplicates(self):
        for _ in range(3):
            sdr_db.set_transcript(self.db, self.file, 'Zachary, 43 is 1042.')
        n = self.db.execute('SELECT count(*) AS n FROM call_codes').fetchone()['n']
        self.assertEqual(n, 1)

    def test_set_id_is_resolved_from_the_filename_not_the_row(self):
        """A transcript can land before the recorder's row, when tgid is NULL."""
        orphan = 'TG17170_17-BRPD-TLK3_20260830-999999.wav'
        sdr_db.set_transcript(self.db, orphan, '10-4')
        row = self.db.execute(
            'SELECT tgid, codes_set_id FROM calls WHERE file = ?',
            (orphan,)).fetchone()
        self.assertIsNone(row['tgid'])
        self.assertEqual(row['codes_set_id'], 'la-brpd-law')

    def test_fts_finds_a_call_by_code_meaning(self):
        sdr_db.set_transcript(self.db, self.file, 'signal 20 on Airline')
        hit = self.db.execute(
            "SELECT rowid FROM calls_fts WHERE calls_fts MATCH 'crash'"
        ).fetchone()
        self.assertIsNotNone(hit)

    def test_codes_rev_is_recorded(self):
        sdr_db.set_transcript(self.db, self.file, '10-4')
        rev = self.db.execute(
            'SELECT codes_rev FROM calls WHERE file = ?',
            (self.file,)).fetchone()['codes_rev']
        self.assertTrue(rev)


class TestObservedEncryptionColumns(unittest.TestCase):
    """Columns enc_harvest.py writes, added by the standard migration.

    Distinct from talkgroups.enc, which is a scraped RadioReference label
    describing a talkgroup in general. These describe one transmission.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()

    def tearDown(self):
        for suffix in ('', '-wal', '-shm'):
            try:
                os.unlink(self.tmp.name + suffix)
            except OSError:
                pass

    def test_observed_encryption_columns_exist(self):
        db = sdr_db.connect(self.tmp.name)
        try:
            cols = {r[1] for r in db.execute('PRAGMA table_info(calls)')}
            self.assertIn('enc_observed', cols)
            self.assertIn('enc_evidence', cols)
            self.assertIn('enc_source', cols)
        finally:
            db.close()

    def test_migration_is_idempotent_for_enc_columns(self):
        """connect() runs on every open, including while the recorder holds it."""
        sdr_db.connect(self.tmp.name).close()
        db = sdr_db.connect(self.tmp.name)   # would raise "duplicate column name"
        try:
            cols = {r[1] for r in db.execute('PRAGMA table_info(calls)')}
            self.assertIn('enc_observed', cols)
        finally:
            db.close()
