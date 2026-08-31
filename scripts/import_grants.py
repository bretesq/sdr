#!/usr/bin/env python3
"""Import control-channel grant events from an op25 log into sdr.db.

WHY THIS IS SEPARATE FROM THE RECORDER
--------------------------------------
A single radio can either stay on the control channel and see every grant while
hearing none, or follow calls and hear audio while missing the grants issued
while it is away. README section 7 measures the gap: a 6-minute control-channel
run logged 3,765 grants across 33 talkgroups, against the 9 talkgroups seen
while recording audio.

So `calls` (what we recorded) and `grants` (what was announced) are genuinely
different observations, and neither is a subset of the other. This imports the
second from a control-channel log, e.g. one produced by:

    ./scripts/lwin_cdr_run.sh 360

Grants are linked to a recorded call when one exists for the same talkgroup at
the same moment, so a query can ask "which announced calls did we actually
capture?" — but a link is never invented: the window is deliberately tight.

    python3 scripts/import_grants.py results/lwin_cdr.log
    python3 scripts/import_grants.py --dry-run results/lwin_cdr.log
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sdr_db import connect  # noqa: E402

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')
TS = r'(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d+)'

# op25 logs a grant as "set tgid=N, srcaddr=M" on the line it acts on it.
GRANT = re.compile(TS + r'[^\n]*?set tgid=(\d+), srcaddr=(\d+)')
# The paired talkgroup/frequency form. ch may be an unresolved channel id
# ("ID-0x485") or a real frequency ("858.237500"); only the latter is useful.
CHAN = re.compile(r'ga(\d): (\d+)')
CHAN_FREQ = re.compile(r'ch(\d): ([\d.]+) ga\d: (\d+)')
SITE = re.compile(r'rfss_sts_bcst:\s*syid:\s*([0-9a-f]+)\s*rfid:\s*(\d+)\s*stid:\s*(\d+)')
OPCODE = re.compile(TS + r'[^\n]*?TSBK: op=([0-9a-fx]+)')

# A recorded call and its grant should be within a couple of seconds. Wider and
# a busy talkgroup's grants start attaching to the wrong recording.
LINK_WINDOW_S = 4.0


def parse_ts(s: str) -> float:
    """op25 stamps local time as MM/DD/YY HH:MM:SS.ffffff."""
    return dt.datetime.strptime(s, '%m/%d/%y %H:%M:%S.%f').timestamp()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('log', nargs='?', default=os.path.join(R, 'results', 'lwin_cdr.log'))
    ap.add_argument('--dry-run', action='store_true', help='report only; write nothing')
    a = ap.parse_args()

    try:
        raw = ANSI.sub('', open(a.log, errors='ignore').read())
    except OSError as e:
        print(f'cannot read {a.log}: {e}', file=sys.stderr)
        return 1

    # Serving site, if the log carries it. Constant for one receiver location.
    site_m = SITE.search(raw)
    sysid, rfss, stid = (int(site_m.group(1), 16), int(site_m.group(2)),
                         int(site_m.group(3))) if site_m else (None, None, None)

    # tgid -> most recently announced voice frequency, in Hz.
    freq_for: dict[int, int] = {}
    for _ch, mhz, tg in CHAN_FREQ.findall(raw):
        try:
            freq_for[int(tg)] = int(round(float(mhz) * 1e6))
        except ValueError:
            continue

    grants = []
    for m in GRANT.finditer(raw):
        try:
            ts = parse_ts(m.group(1))
        except ValueError:
            continue
        tgid = int(m.group(2))
        src = int(m.group(3)) or None      # 0 means "not reported", not radio 0
        grants.append((ts, tgid, src, freq_for.get(tgid), rfss, stid))

    with_src = sum(1 for g in grants if g[2])
    distinct_src = len({g[2] for g in grants if g[2]})
    distinct_tg = len({g[1] for g in grants})

    print(f'log            {a.log}')
    print(f'grants         {len(grants)}')
    print(f'  with srcaddr {with_src}  ({distinct_src} distinct radios)')
    print(f'  talkgroups   {distinct_tg}')
    print(f'  site         rfss {rfss} site {stid} sysid 0x{sysid:x}' if site_m else '  site         not in this log')
    print(f'  voice freqs  {len(freq_for)}')

    if a.dry_run:
        print('(dry run: nothing written)')
        return 0
    if not grants:
        print('nothing to import')
        return 0

    db = connect()
    linked = 0
    try:
        # Clear only this log's time span, so re-importing one capture does not
        # duplicate its rows and does not touch any other capture's.
        lo, hi = min(g[0] for g in grants), max(g[0] for g in grants)
        db.execute('DELETE FROM grants WHERE ts BETWEEN ? AND ?', (lo, hi))

        for ts, tgid, src, freq, rf, st in grants:
            row = db.execute(
                """SELECT id FROM calls
                    WHERE tgid = ? AND ABS(start - ?) <= ?
                 ORDER BY ABS(start - ?) LIMIT 1""",
                (tgid, ts, LINK_WINDOW_S, ts)).fetchone()
            call_id = row['id'] if row else None
            if call_id:
                linked += 1
            db.execute(
                """INSERT INTO grants (ts, tgid, src_addr, freq, rfss, site, call_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (ts, tgid, src, freq, rf, st, call_id))
        db.commit()
    finally:
        db.close()

    print(f'imported       {len(grants)} grants, {linked} linked to a recorded call')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
