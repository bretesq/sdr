#!/usr/bin/env python3
"""One-shot import of every flat-file data source into sdr.db.

Idempotent — safe to re-run. Reference tables are replaced wholesale; calls are
upserted on their unique filename, so re-running never duplicates a row and
never drops a transcript.

Sources:
    reference/lwin_talkgroups.json   4,163 talkgroups (object keyed by tgid;
                                     the value objects carry NO tgid field)
    reference/lwin_sites.json          149 sites
    reference/lwin_categories.json     243 categories
    recordings/calls.json            call metadata, as far as it survives
    recordings/*.wav                 the authoritative list of calls
    recordings/*.txt                 transcripts

`recordings/*.wav` is the source of truth for WHICH calls exist, not
calls.json — because udp_audio_record.py truncates that file to the current
session on every run, so it is routinely missing almost everything. Anything
absent from it is reconstructed: tgid and start from the filename, duration
from the WAV header, and the rest from the talkgroups table.

    python3 scripts/import_to_sqlite.py --dry-run
    python3 scripts/import_to_sqlite.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sdr_db import DB_PATH, connect, set_transcript  # noqa: E402

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = os.path.join(R, 'recordings')
REF = os.path.join(R, 'reference')

NAME = re.compile(r'^TG(\d+)_.+_(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(?:_\d+)?\.wav$')


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def parse_name(fname):
    m = NAME.match(fname)
    if not m:
        return None, 0.0
    tg, y, mo, d, h, mi, s = m.groups()
    # Local wall-clock: udp_audio_record.py stamps with local strftime.
    return int(tg), dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s)).timestamp()


def wav_duration(path):
    try:
        with wave.open(path) as w:
            rate = w.getframerate()
            return round(w.getnframes() / rate, 2) if rate else 0.0
    except Exception:
        return 0.0


def import_talkgroups(db, dry):
    raw = load_json(os.path.join(REF, 'lwin_talkgroups.json'), {})
    rows = [
        (int(k), v.get('alpha'), v.get('desc'), v.get('cat'), v.get('tag'),
         v.get('enc'), v.get('mode'), v.get('hex'), v.get('tgcat'))
        for k, v in raw.items() if str(k).lstrip('-').isdigit()
    ]
    if not dry:
        db.execute('DELETE FROM talkgroups')
        db.executemany(
            """INSERT INTO talkgroups
               (tgid, alpha, description, cat, tag, enc, mode, hex, tgcat)
               VALUES (?,?,?,?,?,?,?,?,?)""", rows)
    return len(rows)


def import_sites(db, dry):
    raw = load_json(os.path.join(REF, 'lwin_sites.json'), [])
    rows = [
        (s.get('rfss'), s.get('site_dec'), s.get('site_hex'), s.get('nac'),
         s.get('name_county'), json.dumps(s.get('control')), json.dumps(s.get('freqs')))
        for s in raw
        if isinstance(s, dict) and s.get('site_dec') is not None and s.get('rfss') is not None
    ]
    if not dry:
        db.execute('DELETE FROM sites')
        db.executemany(
            """INSERT OR REPLACE INTO sites
               (rfss, site_dec, site_hex, nac, name_county, control, freqs)
               VALUES (?,?,?,?,?,?,?)""", rows)
    return len(rows)


def import_categories(db, dry):
    raw = load_json(os.path.join(REF, 'lwin_categories.json'), {})
    rows = [(str(k), str(v)) for k, v in raw.items()] if isinstance(raw, dict) else []
    if not dry:
        db.execute('DELETE FROM categories')
        db.executemany('INSERT OR REPLACE INTO categories (name, tgcat) VALUES (?,?)', rows)
    return len(rows)


def import_calls(db, dry):
    # calls.json is whatever survived the last truncating write; the .wav files
    # are the real inventory.
    meta = {}
    for e in load_json(os.path.join(REC, 'calls.json'), []):
        if isinstance(e, dict) and e.get('file'):
            meta[e['file']] = e

    inserted = transcripts = 0
    try:
        files = sorted(f for f in os.listdir(REC) if f.endswith('.wav'))
    except OSError:
        return 0, 0

    for fname in files:
        e = meta.get(fname, {})
        tgid, start = parse_name(fname)
        tgid = e.get('tgid') or tgid
        start = e.get('start') or start
        dur = e.get('dur') or wav_duration(os.path.join(REC, fname))

        txt = None
        tpath = os.path.join(REC, fname[:-4] + '.txt')
        try:
            with open(tpath, errors='replace') as f:
                txt = f.read().strip() or None
        except OSError:
            pass

        if not dry:
            # Metadata first, transcript second: set_transcript() derives
            # transcript_norm/codes_text/codes_set_id/codes_rev and the
            # call_codes rows from `txt`, so it must run through it rather
            # than writing the raw column directly — otherwise this
            # recovery path (named as such by stt_watch.py) leaves every
            # derived column NULL and the FTS rebuild below indexes nothing
            # for these rows.
            db.execute(
                """INSERT INTO calls (file, tgid, start, dur)
                   VALUES (?,?,?,?)
                   ON CONFLICT(file) DO UPDATE SET
                     tgid  = COALESCE(excluded.tgid, calls.tgid),
                     start = CASE WHEN excluded.start > 0 THEN excluded.start ELSE calls.start END,
                     dur   = CASE WHEN excluded.dur   > 0 THEN excluded.dur   ELSE calls.dur   END""",
                (fname, tgid, start, dur))
            if txt:
                set_transcript(db, fname, txt)
        inserted += 1
        if txt:
            transcripts += 1

    return inserted, transcripts


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='count only; write nothing')
    a = ap.parse_args()

    db = connect()
    try:
        tg = import_talkgroups(db, a.dry_run)
        st = import_sites(db, a.dry_run)
        ct = import_categories(db, a.dry_run)
        ca, tr = import_calls(db, a.dry_run)
        if not a.dry_run:
            db.commit()
            db.execute("INSERT INTO calls_fts(calls_fts) VALUES('rebuild')")
            db.commit()
    finally:
        db.close()

    print(f'talkgroups {tg}\nsites      {st}\ncategories {ct}')
    print(f'calls      {ca}  ({tr} with a transcript)')
    print('(dry run: nothing written)' if a.dry_run else f'-> {DB_PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
