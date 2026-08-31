#!/usr/bin/env python3
"""SQLite schema and helpers for the SDR lab — shared by the Python tooling.

WHY A DATABASE
--------------
Everything used to live in flat files, and two of them were actively unsafe:

  recordings/calls.json      udp_audio_record.py rewrote it in its `finally`
                             block with ONLY the current session's calls, a
                             truncating write that discarded every earlier
                             recording's metadata. A 60-second session took it
                             from 2,953 entries to 7.
  transcripts                stt_watch.py merged transcripts into the same file,
                             which udp_audio_record.py then clobbered — which is
                             why no transcript ever survived in it.

An INSERT cannot truncate history, and two writers can share a database. The
reference data (4,163 talkgroups, 149 sites, 243 categories) moves in too so
there is one place to query rather than three JSON files plus a directory scan.

WHAT STAYS A FILE
-----------------
op25 reads `lwin_active_whitelist.txt` and `lwin_active.tsv` from disk and that
is not negotiable. The database is the source of truth; those files are
GENERATED from it by make_whitelist.py. Same for the .wav and .txt files
themselves — the DB indexes them, it does not contain them.

Usage:
    from sdr_db import connect, SCHEMA
    with connect() as db:
        db.execute('INSERT INTO calls (...) VALUES (...)')
"""
from __future__ import annotations

import os
import sqlite3

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(R, 'sdr.db')

SCHEMA = """
PRAGMA journal_mode = WAL;          -- readers never block the recorder
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;         -- the web app and the recorder both write

-- ---------------------------------------------------------------- reference
-- Rebuilt wholesale from reference/*.json; safe to DELETE and re-import.

CREATE TABLE IF NOT EXISTS talkgroups (
  tgid        INTEGER PRIMARY KEY,
  alpha       TEXT,
  description TEXT,                 -- `desc` is a SQL keyword; mapped at the edge
  cat         TEXT,
  tag         TEXT,
  enc         TEXT CHECK (enc IN ('clear', 'partial', 'full')),
  mode        TEXT,
  hex         TEXT,
  tgcat       TEXT
);
CREATE INDEX IF NOT EXISTS idx_tg_cat ON talkgroups(cat);
CREATE INDEX IF NOT EXISTS idx_tg_tag ON talkgroups(tag);
CREATE INDEX IF NOT EXISTS idx_tg_enc ON talkgroups(enc);

-- A site is identified by (rfss, site_dec), NOT site_dec alone: the 149 sites
-- share only 67 distinct site_dec values. Site 13 is Baton Rouge Simulcast in
-- RFSS 1, Hahnville in RFSS 2, Greenwood in RFSS 3 and Opelousas in RFSS 4.
-- A site_dec primary key silently collapsed the table to 67 rows and made
-- "site 13" resolve to whichever RFSS was imported last.
CREATE TABLE IF NOT EXISTS sites (
  rfss        INTEGER NOT NULL,
  site_dec    INTEGER NOT NULL,
  site_hex    TEXT,
  nac         TEXT,
  name_county TEXT,
  control     TEXT,                 -- JSON array of control-channel freqs
  freqs       TEXT,                 -- JSON array of all freqs on the site
  PRIMARY KEY (rfss, site_dec)
);

CREATE TABLE IF NOT EXISTS categories (
  name  TEXT PRIMARY KEY,
  tgcat TEXT
);

-- ------------------------------------------------------------------- calls
-- Append-only in normal operation. `file` is UNIQUE so re-importing or a
-- double-flush is idempotent rather than duplicating a row.

CREATE TABLE IF NOT EXISTS calls (
  id         INTEGER PRIMARY KEY,
  file       TEXT    NOT NULL UNIQUE,
  tgid       INTEGER,
  start      REAL    NOT NULL,      -- unix seconds, local-clock derived
  dur        REAL    NOT NULL DEFAULT 0,
  transcript TEXT,
  session_id INTEGER REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_calls_start ON calls(start DESC);
CREATE INDEX IF NOT EXISTS idx_calls_tgid  ON calls(tgid);

-- Full-text over transcripts. The web app was doing String.includes across
-- 3,220 transcripts on every keystroke; this makes it an indexed lookup.
CREATE VIRTUAL TABLE IF NOT EXISTS calls_fts USING fts5(
  transcript,
  content = 'calls',
  content_rowid = 'id'
);

CREATE TRIGGER IF NOT EXISTS calls_ai AFTER INSERT ON calls BEGIN
  INSERT INTO calls_fts(rowid, transcript) VALUES (new.id, new.transcript);
END;
CREATE TRIGGER IF NOT EXISTS calls_ad AFTER DELETE ON calls BEGIN
  INSERT INTO calls_fts(calls_fts, rowid, transcript) VALUES('delete', old.id, old.transcript);
END;
CREATE TRIGGER IF NOT EXISTS calls_au AFTER UPDATE ON calls BEGIN
  INSERT INTO calls_fts(calls_fts, rowid, transcript) VALUES('delete', old.id, old.transcript);
  INSERT INTO calls_fts(rowid, transcript) VALUES (new.id, new.transcript);
END;

-- ---------------------------------------------------------------- sessions
-- Replaces web/listen.{pid,config.json,started}. `proc_start` is
-- /proc/<pid>/stat field 22: a pid alone is not an identity, because the kernel
-- recycles pid numbers and a stale record could otherwise make Stop signal an
-- unrelated process group.

CREATE TABLE IF NOT EXISTS sessions (
  id         INTEGER PRIMARY KEY,
  pid        INTEGER,
  proc_start INTEGER,
  config     TEXT,                  -- JSON of the ListenOptions used
  started_at REAL NOT NULL,
  ended_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    """Open the database, creating and migrating the schema if needed."""
    db = sqlite3.connect(path, timeout=5.0)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def upsert_call(db: sqlite3.Connection, *, file: str, tgid: int | None,
                start: float, dur: float, session_id: int | None = None) -> None:
    """Record one finished call.

    ON CONFLICT keeps whatever transcript is already there — stt_watch.py may
    have written one before the recorder's own row landed, and losing it is
    exactly the bug this table exists to prevent.
    """
    db.execute(
        """INSERT INTO calls (file, tgid, start, dur, session_id)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(file) DO UPDATE SET
             tgid       = excluded.tgid,
             start      = excluded.start,
             dur        = excluded.dur,
             session_id = COALESCE(excluded.session_id, calls.session_id)""",
        (file, tgid, start, dur, session_id),
    )


def set_transcript(db: sqlite3.Connection, file: str, transcript: str) -> None:
    """Attach a transcript, creating a stub row if the call is not indexed yet."""
    cur = db.execute('UPDATE calls SET transcript = ? WHERE file = ?', (transcript, file))
    if cur.rowcount == 0:
        db.execute(
            'INSERT OR IGNORE INTO calls (file, start, dur, transcript) VALUES (?, 0, 0, ?)',
            (file, transcript),
        )
