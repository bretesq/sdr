#!/usr/bin/env python3
"""Rebuild recordings/calls.json from the .wav files on disk.

WHY THIS EXISTS
---------------
udp_audio_record.py writes calls.json in its `finally` block as

    json.dump(calls, open(f'{OUT}/calls.json', 'w'), indent=1)

where `calls` holds ONLY the calls from the session that is ending. It is a
truncating write, not a merge, so every recording session silently discards the
metadata for every call recorded before it. A 60-second test run took the file
from 2,953 entries to 7; the next 12-second run took it to 1.

Everything in an entry is derivable, so nothing is permanently lost:

    file   the filename
    tgid   parsed from the filename
    start  parsed from the filename stamp (local wall-clock, matching
           udp_audio_record.py's own strftime and Python's local .timestamp())
    dur    wav frames / sample rate
    alpha, desc, cat, enc   looked up in reference/lwin_talkgroups.json by tgid

The only fidelity loss versus an original entry is sub-second precision on
`start`, because the filename carries whole seconds.

Merges by default: existing entries win, so a freshly-written session's exact
floats are preserved and only missing files are filled in.

    python3 scripts/rebuild_calls_json.py            # merge, write
    python3 scripts/rebuild_calls_json.py --dry-run  # report, write nothing
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import wave

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = os.path.join(R, 'recordings')
CALLS = os.path.join(REC, 'calls.json')
TGDB = os.path.join(R, 'reference', 'lwin_talkgroups.json')

NAME = re.compile(r'^TG(\d+)_.+_(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(?:_\d+)?\.wav$')


def parse_name(fname: str) -> tuple[int, float] | None:
    m = NAME.match(fname)
    if not m:
        return None
    tg, y, mo, d, h, mi, s = m.groups()
    # Local wall-clock: udp_audio_record.py stamps with local strftime and
    # records `start` from a local .timestamp().
    stamp = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    return int(tg), stamp.timestamp()


def wav_duration(path: str) -> float:
    try:
        with wave.open(path) as w:
            rate = w.getframerate()
            return round(w.getnframes() / rate, 2) if rate else 0.0
    except (wave.Error, OSError, EOFError):
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true',
                    help='report what would change; write nothing')
    ap.add_argument('--overwrite', action='store_true',
                    help='rebuild every entry instead of keeping existing ones')
    a = ap.parse_args()

    try:
        with open(TGDB) as f:
            tgdb = json.load(f)
    except OSError as e:
        print(f'cannot read {TGDB}: {e}', file=sys.stderr)
        return 1

    existing: dict[str, dict] = {}
    if not a.overwrite:
        try:
            with open(CALLS) as f:
                for e in json.load(f):
                    if isinstance(e, dict) and 'file' in e:
                        existing[e['file']] = e
        except (OSError, json.JSONDecodeError):
            pass  # absent or corrupt: rebuild from scratch

    out, kept, rebuilt, skipped = [], 0, 0, 0
    for fname in sorted(os.listdir(REC)):
        if not fname.endswith('.wav'):
            continue
        if fname in existing:
            out.append(existing[fname])
            kept += 1
            continue

        parsed = parse_name(fname)
        if parsed is None:
            skipped += 1
            continue
        tgid, start = parsed
        tg = tgdb.get(str(tgid), {})
        out.append({
            'file': fname,
            'tgid': tgid,
            'alpha': tg.get('alpha'),
            'desc': tg.get('desc'),
            'enc': tg.get('enc'),
            'cat': tg.get('cat'),
            'start': start,
            'dur': wav_duration(os.path.join(REC, fname)),
        })
        rebuilt += 1

    out.sort(key=lambda e: e.get('start') or 0)

    print(f'kept {kept} existing, rebuilt {rebuilt}, skipped {skipped} unparseable'
          f'  -> {len(out)} entries')
    if a.dry_run:
        print('(dry run: nothing written)')
        return 0

    if os.path.exists(CALLS):
        backup = CALLS + '.bak'
        os.replace(CALLS, backup)
        print(f'previous file moved to {backup}')
    with open(CALLS, 'w') as f:
        json.dump(out, f, indent=1)
    print(f'wrote {CALLS}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
