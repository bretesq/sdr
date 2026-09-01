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
import re
import sqlite3

import tencodes
import tencode_sets

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
  ended_at   REAL,                  -- from TDU/TDULC (DUID 0x3 / 0xf)
  transcript TEXT,
  session_id INTEGER REFERENCES sessions(id),

  -- ---- derived code annotation ------------------------------------------
  -- All re-derivable from the .txt files by scripts/backfill_codes.py.
  -- `transcript` above is only ever raw whisper output and is never written
  -- by the annotation layer.
  transcript_norm TEXT,
  codes_text      TEXT,
  codes_set_id    TEXT,
  codes_rev       TEXT,

  -- ---- P25 per-call metadata -------------------------------------------
  -- All nullable: none of it is guaranteed present on a given call, and a
  -- column that is null 86% of the time must not be modelled as required.

  -- The transmitting radio (24-bit Source ID / SUID). NOT the DUID: that is a
  -- 4-bit Data Unit ID identifying the FRAME TYPE (HDU 0x0, LDU1 0x5,
  -- LDU2 0xa, TDU 0x3, TSBK 0x7, TDULC 0xf), which is why p25p1_fdma.cc tests
  -- `framer->duid == 0x3 || framer->duid == 0xf` to detect voice termination.
  -- Measured on a real 6-minute capture: 3,765 grants carried srcaddr, but
  -- 3,223 of those were 0 — only 542 populated, across 101 distinct radios.
  src_addr   INTEGER,

  -- ESS, read in the clear from the LDU2 frame (p25p1_fdma.cc:348). This is
  -- how the cipher is identified WITHOUT any decryption.
  --   algid 0x80 = unencrypted, 0xAA = ADP/RC4, 0x81/0x83 = DES,
  --   0x84 = AES-256, 0x85 = AES-128
  algid      INTEGER,
  keyid      INTEGER,
  mi         TEXT,                  -- 9-byte message indicator, lowercase hex

  -- Serving site and channel. (rfss, site) joins to sites.
  --
  -- rfss/site are NULL on essentially every recorded call, and that is
  -- structural rather than a gap to fill. They come from rfss_sts_bcst, a
  -- CONTROL-channel broadcast: 891 occurrences in a control-channel-only
  -- capture, 0 in a voice-following log. Populating them per call would need
  -- the control channel watched WHILE voice is followed, i.e. two receivers —
  -- and README section 7 measures both RTL-SDRs at +2.7 dB on the control
  -- channel against the ~15 dB P25 needs. The grants table carries the site
  -- for control-channel captures; do not backfill it onto calls from a
  -- constant, because "observed" and "assumed" must not look identical.
  rfss       INTEGER,
  site       INTEGER,
  freq       INTEGER,               -- voice channel, Hz

  -- System identity. Constant for LWIN (NAC 0x1bd, WACN 0xbee00) but recorded
  -- per call so a capture from another system is not silently mislabelled.
  nac        INTEGER,
  wacn       INTEGER,
  sysid      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_calls_start ON calls(start DESC);
CREATE INDEX IF NOT EXISTS idx_calls_tgid  ON calls(tgid);
CREATE INDEX IF NOT EXISTS idx_calls_src   ON calls(src_addr) WHERE src_addr IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_calls_algid ON calls(algid);
CREATE INDEX IF NOT EXISTS idx_calls_site  ON calls(rfss, site);

-- --------------------------------------------------------------- grants
-- Every grant seen on the control channel, including ones no audio was
-- recorded for. This is the "widest view" of README section 7: a radio parked
-- on the control channel sees every call but hears none, while a radio
-- following calls hears audio but misses grants issued while it is away. A
-- 6-minute control-channel run logged 3,765 grants across 33 talkgroups
-- against the 9 seen while recording audio.
CREATE TABLE IF NOT EXISTS grants (
  id         INTEGER PRIMARY KEY,
  ts         REAL    NOT NULL,
  tgid       INTEGER,
  src_addr   INTEGER,
  freq       INTEGER,
  rfss       INTEGER,
  site       INTEGER,
  opcode     INTEGER,               -- TSBK opcode
  call_id    INTEGER REFERENCES calls(id)   -- set if audio was captured
);
CREATE INDEX IF NOT EXISTS idx_grants_ts   ON grants(ts DESC);
CREATE INDEX IF NOT EXISTS idx_grants_tgid ON grants(tgid);
CREATE INDEX IF NOT EXISTS idx_grants_src  ON grants(src_addr) WHERE src_addr IS NOT NULL;

-- --------------------------------------------------------- P25 algorithms
-- So a report can say "ADP" rather than "170", without hardcoding the mapping
-- in three different places.
CREATE TABLE IF NOT EXISTS algorithms (
  algid INTEGER PRIMARY KEY,
  name  TEXT NOT NULL
);
INSERT OR IGNORE INTO algorithms (algid, name) VALUES
  (128, 'Unencrypted'),        -- 0x80
  (129, 'DES-OFB'),            -- 0x81
  (131, 'DES-XL'),             -- 0x83
  (132, 'AES-256'),            -- 0x84
  (133, 'AES-128'),            -- 0x85
  (170, 'ADP / RC4');          -- 0xaa

-- Full-text over transcripts. The web app was doing String.includes across
-- 3,220 transcripts on every keystroke; this makes it an indexed lookup.
CREATE VIRTUAL TABLE IF NOT EXISTS calls_fts USING fts5(
  transcript_norm, codes_text,
  content = 'calls',
  content_rowid = 'id'
);

CREATE TRIGGER IF NOT EXISTS calls_ai AFTER INSERT ON calls BEGIN
  INSERT INTO calls_fts(rowid, transcript_norm, codes_text)
  VALUES (new.id, new.transcript_norm, new.codes_text);
END;
CREATE TRIGGER IF NOT EXISTS calls_ad AFTER DELETE ON calls BEGIN
  INSERT INTO calls_fts(calls_fts, rowid, transcript_norm, codes_text)
  VALUES ('delete', old.id, old.transcript_norm, old.codes_text);
END;
CREATE TRIGGER IF NOT EXISTS calls_au AFTER UPDATE ON calls BEGIN
  INSERT INTO calls_fts(calls_fts, rowid, transcript_norm, codes_text)
  VALUES ('delete', old.id, old.transcript_norm, old.codes_text);
  INSERT INTO calls_fts(rowid, transcript_norm, codes_text)
  VALUES (new.id, new.transcript_norm, new.codes_text);
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

CREATE TABLE IF NOT EXISTS call_codes (
  id         INTEGER PRIMARY KEY,
  call_id    INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  raw        TEXT NOT NULL,
  canonical  TEXT NOT NULL,
  kind       TEXT NOT NULL CHECK (kind IN ('ten', 'signal', 'response')),
  meaning    TEXT,
  set_id     TEXT,
  confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
  off_start  INTEGER NOT NULL,
  off_end    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_call_codes_call  ON call_codes(call_id);
CREATE INDEX IF NOT EXISTS idx_call_codes_canon ON call_codes(canonical, call_id);
"""


# Bumped when the FTS layout changes. `CREATE TABLE IF NOT EXISTS` cannot
# express "add a column to an existing table", and ALTER TABLE is not
# idempotent, so this is the minimum migration mechanism the schema needs.
_USER_VERSION = 1

_DERIVED_COLUMNS = (
    ('transcript_norm', 'TEXT'),
    ('codes_text', 'TEXT'),
    ('codes_set_id', 'TEXT'),
    ('codes_rev', 'TEXT'),
    # Observed encryption, written by enc_harvest.py from op25 logs. Distinct
    # from talkgroups.enc, which is a RadioReference label describing how a
    # talkgroup is documented rather than what a transmission carried: 367 of
    # 377 calls on talkgroups flagged 'full' contain real speech.
    ('enc_observed', 'TEXT'),   # 'clear' | 'encrypted' | 'mixed' | NULL unseen
    ('enc_evidence', 'TEXT'),   # 'ess' | 'speech' | 'both' | NULL
    ('enc_source', 'TEXT'),     # 'harvest' (authoritative) | 'live' (hint)
)


def _migrate(db: sqlite3.Connection) -> None:
    """Add derived columns and move the FTS index onto them.

    Runs on every connect and must stay cheap and idempotent: after the first
    pass it is two PRAGMA reads.
    """
    have = {r[1] for r in db.execute('PRAGMA table_info(calls)')}
    for name, decl in _DERIVED_COLUMNS:
        if name not in have:
            db.execute(f'ALTER TABLE calls ADD COLUMN {name} {decl}')

    if db.execute('PRAGMA user_version').fetchone()[0] >= _USER_VERSION:
        return

    # calls_fts is an external-content table, so its columns must be columns of
    # `calls`. Indexing transcript_norm rather than transcript is deliberate:
    # normalization only ever rewrites code tokens, and codes_text carries the
    # raw forms too, so nothing becomes unsearchable.
    #
    # Everything below runs inside one BEGIN IMMEDIATE .. COMMIT: dropping and
    # recreating calls_fts as separate autocommitted statements leaves a
    # window where a concurrent reader (the recording pipeline is always
    # live) sees "no such table: calls_fts" — a SQLITE_ERROR that
    # busy_timeout does nothing for, since that only retries SQLITE_BUSY.
    #
    # The UPDATE seeds transcript_norm from the existing raw transcript
    # BEFORE the rebuild. Without it, every pre-migration row has
    # transcript_norm = NULL, so 'rebuild' would index nothing for any of
    # them and search on the whole existing corpus would go dark from the
    # moment this migration lands until a later backfill task overwrites
    # these placeholder values with normalized text. Seeding first means
    # search keeps working (over raw text) the instant the migration runs.
    #
    # The UPDATE runs AFTER the DROP TRIGGERs/TABLE, not before: SCHEMA above
    # uses `CREATE TRIGGER IF NOT EXISTS` for all three triggers, so on a
    # database where SCHEMA's own run left any one of the three legacy
    # triggers missing (by name) while calls_fts itself still exists old-
    # shaped, SCHEMA would have just (re)created that one trigger against the
    # NEW column names — a trigger that would then fire on this UPDATE
    # against the OLD, not-yet-recreated calls_fts and fail with "table
    # calls_fts has no column named transcript_norm". Dropping first removes
    # every trigger before anything touches `calls`, so the UPDATE can never
    # be caught between two mismatched schema generations.
    db.executescript(f"""
        BEGIN IMMEDIATE;

        DROP TRIGGER IF EXISTS calls_ai;
        DROP TRIGGER IF EXISTS calls_au;
        DROP TRIGGER IF EXISTS calls_ad;
        DROP TABLE IF EXISTS calls_fts;

        UPDATE calls SET transcript_norm = transcript
          WHERE transcript_norm IS NULL AND transcript IS NOT NULL;

        CREATE VIRTUAL TABLE calls_fts USING fts5(
          transcript_norm, codes_text,
          content = 'calls', content_rowid = 'id'
        );

        CREATE TRIGGER calls_ai AFTER INSERT ON calls BEGIN
          INSERT INTO calls_fts(rowid, transcript_norm, codes_text)
          VALUES (new.id, new.transcript_norm, new.codes_text);
        END;
        CREATE TRIGGER calls_ad AFTER DELETE ON calls BEGIN
          INSERT INTO calls_fts(calls_fts, rowid, transcript_norm, codes_text)
          VALUES ('delete', old.id, old.transcript_norm, old.codes_text);
        END;
        CREATE TRIGGER calls_au AFTER UPDATE ON calls BEGIN
          INSERT INTO calls_fts(calls_fts, rowid, transcript_norm, codes_text)
          VALUES ('delete', old.id, old.transcript_norm, old.codes_text);
          INSERT INTO calls_fts(rowid, transcript_norm, codes_text)
          VALUES (new.id, new.transcript_norm, new.codes_text);
        END;

        INSERT INTO calls_fts(calls_fts) VALUES('rebuild');

        PRAGMA user_version = {_USER_VERSION};

        COMMIT;
    """)


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    """Open the database, creating and migrating the schema if needed."""
    db = sqlite3.connect(path, timeout=5.0)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    _migrate(db)
    return db


def upsert_call(db: sqlite3.Connection, *, file: str, tgid: int | None,
                start: float, dur: float, session_id: int | None = None,
                ended_at: float | None = None, freq: int | None = None,
                algid: int | None = None, keyid: int | None = None,
                mi: str | None = None, rfss: int | None = None,
                site: int | None = None, nac: int | None = None,
                wacn: int | None = None, sysid: int | None = None,
                src_addr: int | None = None) -> None:
    """Record one finished call with whatever P25 metadata was observed.

    Every metadata argument is optional and defaults to None. op25 emits these
    asynchronously and the caller drops anything stale, so a missing value means
    "not observed for this call" — never a zero or a guess.

    ON CONFLICT uses COALESCE throughout so a re-insert can only ADD detail: it
    keeps an existing transcript (stt_watch.py may have written one before the
    recorder's row landed) and keeps any field the new row does not carry.
    """
    db.execute(
        """INSERT INTO calls
             (file, tgid, start, dur, session_id, ended_at, freq,
              algid, keyid, mi, rfss, site, nac, wacn, sysid, src_addr)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(file) DO UPDATE SET
             tgid       = COALESCE(excluded.tgid,     calls.tgid),
             start      = excluded.start,
             dur        = excluded.dur,
             session_id = COALESCE(excluded.session_id, calls.session_id),
             ended_at   = COALESCE(excluded.ended_at, calls.ended_at),
             freq       = COALESCE(excluded.freq,     calls.freq),
             algid      = COALESCE(excluded.algid,    calls.algid),
             keyid      = COALESCE(excluded.keyid,    calls.keyid),
             mi         = COALESCE(excluded.mi,       calls.mi),
             rfss       = COALESCE(excluded.rfss,     calls.rfss),
             site       = COALESCE(excluded.site,     calls.site),
             nac        = COALESCE(excluded.nac,      calls.nac),
             wacn       = COALESCE(excluded.wacn,     calls.wacn),
             sysid      = COALESCE(excluded.sysid,    calls.sysid),
             src_addr   = COALESCE(excluded.src_addr, calls.src_addr)""",
        (file, tgid, start, dur, session_id, ended_at, freq,
         algid, keyid, mi, rfss, site, nac, wacn, sysid, src_addr),
    )


_TG_PREFIX = re.compile(r'^TG(\d+)_')


def tgid_from_filename(file: str) -> int | None:
    """Recordings are named TG16505_17-EBRP-FD1_20260830-210810.wav.

    Reading the talkgroup from the name rather than the row matters because
    set_transcript creates a stub row when the recorder's row has not landed
    yet, and at that moment calls.tgid is NULL.
    """
    m = _TG_PREFIX.match(os.path.basename(file))
    return int(m.group(1)) if m else None


def code_context(db: sqlite3.Connection, tgid: int | None) -> tuple[str, dict, str]:
    """(set_id, resolved set, rev) for a talkgroup."""
    cat = tag = None
    if tgid is not None:
        row = db.execute(
            'SELECT cat, tag FROM talkgroups WHERE tgid = ?', (tgid,)).fetchone()
        if row is not None:
            cat, tag = row['cat'], row['tag']
    set_id = tencode_sets.resolve_set_id(cat, tag)
    resolved = tencode_sets.resolve(set_id)
    return set_id, resolved, tencode_sets.set_rev(resolved, tencodes.EXTRACTOR_VERSION)


def set_transcript(db: sqlite3.Connection, file: str, transcript: str) -> None:
    """Attach a transcript and its derived codes, creating a stub row if needed.

    transcript, transcript_norm, codes_text, codes_set_id and codes_rev are
    written in ONE statement so the calls_au trigger fires once with every
    column populated. `transcript` itself is only ever the raw whisper output —
    the .txt file remains the durable copy and everything else is derived.
    """
    set_id, resolved, rev = code_context(db, tgid_from_filename(file))
    norm, mentions = tencodes.extract(transcript, resolved)
    blob = tencodes.codes_text(mentions)

    cur = db.execute(
        """UPDATE calls
              SET transcript = ?, transcript_norm = ?, codes_text = ?,
                  codes_set_id = ?, codes_rev = ?
            WHERE file = ?""",
        (transcript, norm, blob, set_id, rev, file),
    )
    if cur.rowcount == 0:
        db.execute(
            """INSERT OR IGNORE INTO calls
                 (file, start, dur, transcript, transcript_norm, codes_text,
                  codes_set_id, codes_rev)
               VALUES (?, 0, 0, ?, ?, ?, ?, ?)""",
            (file, transcript, norm, blob, set_id, rev),
        )

    row = db.execute('SELECT id FROM calls WHERE file = ?', (file,)).fetchone()
    if row is None:
        return
    call_id = row['id']
    db.execute('DELETE FROM call_codes WHERE call_id = ?', (call_id,))
    db.executemany(
        """INSERT INTO call_codes
             (call_id, raw, canonical, kind, meaning, set_id, confidence,
              off_start, off_end)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [(call_id, m.raw, m.canonical, m.kind, m.meaning, m.set_id,
          m.confidence, m.off_start, m.off_end) for m in mentions],
    )
