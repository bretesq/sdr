#!/usr/bin/env python3
"""Show what has been recorded, from sdr.db.

Was a reader of recordings/calls.json, which udp_audio_record.py truncated to
the current session on every run — so this routinely reported a handful of
calls when thousands were on disk.

Usage:
  list_recordings.py [-n 40] [--tg 17165] [--enc partial] [--search text]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sdr_db import connect  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('-n', '--limit', type=int, default=40, help='rows to show (0 = all)')
    p.add_argument('--tg', type=int, help='only this talkgroup')
    p.add_argument('--enc', choices=['clear', 'partial', 'full'], help='only this encryption class')
    p.add_argument('--search', help='substring of alpha, description or transcript')
    a = p.parse_args()

    where, params = [], []
    if a.tg:
        where.append('c.tgid = ?')
        params.append(a.tg)
    if a.enc:
        where.append('t.enc = ?')
        params.append(a.enc)
    if a.search:
        like = f'%{a.search.lower()}%'
        where.append('(LOWER(t.alpha) LIKE ? OR LOWER(t.description) LIKE ? OR LOWER(c.transcript) LIKE ?)')
        params += [like, like, like]
    clause = f"WHERE {' AND '.join(where)}" if where else ''

    db = connect()
    try:
        total, dur = db.execute(
            f'SELECT COUNT(*), COALESCE(SUM(c.dur),0) FROM calls c '
            f'LEFT JOIN talkgroups t ON t.tgid = c.tgid {clause}', params).fetchone()

        sql = (f'SELECT c.file, c.tgid, c.start, c.dur, c.transcript, '
               f'       t.alpha, t.description, t.enc '
               f'  FROM calls c LEFT JOIN talkgroups t ON t.tgid = c.tgid '
               f'  {clause} ORDER BY c.start DESC')
        if a.limit:
            sql += f' LIMIT {int(a.limit)}'

        rows = db.execute(sql, params).fetchall()
        for r in reversed(rows):
            when = dt.datetime.fromtimestamp(r['start']).strftime('%m-%d %H:%M:%S') if r['start'] else '—'
            enc = (r['enc'] or '?')[:7]
            tx = (r['transcript'] or '').replace('\n', ' ')
            if tx.startswith('[BLANK_AUDIO]'):
                tx = '(silence)'
            print(f"{when}  TG{str(r['tgid'] or '?'):>6}  {(r['alpha'] or '—')[:18]:<18} "
                  f"{r['dur']:5.1f}s  {enc:<7}  {tx[:58]}")

        shown = len(rows)
        print(f"\n{shown} shown of {total} call(s), {dur/60:.1f} min audio total")
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
