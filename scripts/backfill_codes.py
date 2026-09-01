#!/usr/bin/env python3
"""Re-derive transcript_norm, codes_text and call_codes for every call.

This is how a newly-sourced or corrected code set reaches history. Because
extraction is fully re-derivable, the unresolved-code report this prints is a
work queue rather than a defect list: source one more agency's codes, run
--only-stale, and past calls retroactively improve.

Usage:
  backfill_codes.py [--only-stale] [--report] [--db PATH]
"""
from __future__ import annotations

import argparse
import collections
import sys

import sdr_db


def backfill(db, only_stale: bool = False) -> dict[str, int]:
    # Initialised explicitly: Counter returns 0 for a missing key but does not
    # create it, so dict(stats) would omit 'updated' entirely on a run where
    # everything was skipped, and callers reading stats['updated'] would raise.
    stats = collections.Counter({'scanned': 0, 'updated': 0, 'skipped': 0})
    rows = db.execute(
        'SELECT file, transcript, codes_set_id, codes_rev FROM calls '
        "WHERE transcript IS NOT NULL AND trim(transcript) <> ''"
    ).fetchall()

    for row in rows:
        stats['scanned'] += 1
        if only_stale:
            set_id, _resolved, rev = sdr_db.code_context(
                db, sdr_db.tgid_from_filename(row['file']))
            if row['codes_set_id'] == set_id and row['codes_rev'] == rev:
                stats['skipped'] += 1
                continue
        sdr_db.set_transcript(db, row['file'], row['transcript'])
        stats['updated'] += 1

    db.commit()
    stats['mentions'] = db.execute(
        'SELECT count(*) AS n FROM call_codes').fetchone()['n']
    return dict(stats)


def report(db) -> str:
    """Resolved vs unresolved per agency, and the unresolved worklist."""
    lines = ['', 'Resolved / unresolved by agency', '-' * 58]
    for r in db.execute("""
        SELECT COALESCE(t.cat, '(unknown)') AS cat,
               SUM(cc.meaning IS NOT NULL) AS resolved,
               SUM(cc.meaning IS NULL)     AS unresolved
          FROM call_codes cc
          JOIN calls c      ON c.id = cc.call_id
          LEFT JOIN talkgroups t ON t.tgid = c.tgid
         GROUP BY 1 ORDER BY 2 DESC, 3 DESC"""):
        lines.append(f'{r["cat"][:44]:44} {r["resolved"]:>5} {r["unresolved"]:>5}')

    lines += ['', 'Unresolved codes — sourcing worklist', '-' * 58]
    for r in db.execute("""
        SELECT cc.canonical, cc.kind, COALESCE(c.codes_set_id, '?') AS set_id,
               count(*) AS n
          FROM call_codes cc
          JOIN calls c ON c.id = cc.call_id
         WHERE cc.meaning IS NULL
         GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 40"""):
        lines.append(f'{r["canonical"]:14} {r["kind"]:9} {r["set_id"]:20} {r["n"]:>4}')
    return '\n'.join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--db', default=sdr_db.DB_PATH)
    p.add_argument('--only-stale', action='store_true',
                   help='recompute only rows whose set id or rev has changed')
    p.add_argument('--report', action='store_true',
                   help='print resolved/unresolved counts and the worklist')
    a = p.parse_args()

    db = sdr_db.connect(a.db)
    try:
        stats = backfill(db, a.only_stale)
        print(f'{stats["scanned"]} scanned, {stats["updated"]} updated, '
              f'{stats.get("skipped", 0)} skipped, '
              f'{stats["mentions"]} code mentions stored')
        if a.report:
            print(report(db))
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
