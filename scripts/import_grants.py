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
#
# srcaddr may be the literal string "None": tk_p25.py (the trunking module
# multi_rx.py uses) formats the field with %s and passes None when the grant
# TSBK carried no source address. Requiring \d+ here matched only 95 of 705
# grants in a real multi_rx log — silently dropping 87% of the census — because
# trunking.py (the rx.py path) always prints a number and tk_p25 usually does
# not.
#
# WHAT A ROW IN `grants` ACTUALLY IS
#
# One decoded grant TSBK EVENT -- not one announced call. `set tgid=` is written
# from update_voice_frequency (tk_p25.py:1637), which is reached from ~19
# TSBK/LCW handlers including the GRANT_UPDATE opcodes that repeat for the whole
# life of a call, and the multi-channel forms (tk_p25.py:1170, 1209) emit 2-3
# lines per single TSBK.
#
# Measured: 10,019 rows collapse to 320 calls when grouped on (tgid, freq) with
# a 3 s gap -- a 31x ratio. The pre-existing rx.py census had the same shape
# (3,765 rows for ~111 calls), so this is the table's long-standing meaning, not
# something the srcaddr=None fix changed. But it means a raw COUNT(*) is NOT a
# count of announced calls, and `call_id` is set per event, so one recorded call
# legitimately collects dozens of grant rows.
#
# Group before comparing "announced" against "captured". See
# docs/2026-08-31-wideband-multichannel.md section 2 for the grouping used
# there.
#
# The sysname prefix ("[LWIN-BR]") does mean these come from the SYSTEM rather
# than a receiver, so pool size does not multiply them -- that part needs no
# dedup.
GRANT = re.compile(TS + r'[^\n]*?set tgid=(\d+), srcaddr=(\d+|None)')
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
        # 0 and "None" both mean "not reported" — never radio 0.
        raw_src = m.group(3)
        src = None if raw_src == 'None' else (int(raw_src) or None)
        grants.append((ts, tgid, src, freq_for.get(tgid), rfss, stid))

    with_src = sum(1 for g in grants if g[2])
    distinct_src = len({g[2] for g in grants if g[2]})
    distinct_tg = len({g[1] for g in grants})

    print(f'log            {a.log}')
    print(f'grants         {len(grants)}')
    print(f'  with srcaddr {with_src}  ({distinct_src} distinct radios)')
    print(f'  talkgroups   {distinct_tg}')
    # Reported alongside the raw count because the raw count is ~31x the number
    # of announced calls and has been read as a call census more than once.
    grouped, seen = 0, {}
    for ts, tgid, _src, freq, _rf, _st in grants:
        key = (tgid, freq)
        if key not in seen or ts - seen[key] > 3.0:
            grouped += 1
        seen[key] = ts
    print(f'  ^ these are grant TSBK EVENTS; grouped into distinct calls '
          f'(tgid+freq, 3 s gap): {grouped}')
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
        # TWO PHASES, AND THE ORDER IS LOAD-BEARING: every link lookup runs
        # BEFORE the write transaction is opened.
        #
        # This used to be one loop -- DELETE first, then SELECT-then-INSERT per
        # grant -- and that shape held SQLite's single write lock for the whole
        # run. Python's sqlite3 opens its implicit transaction on the first
        # DML statement (isolation_level='' by default), so the DELETE opened
        # it and nothing closed it until the commit ~156,000 SELECT+INSERT
        # pairs later. Measured on a real 1,964,517-line log
        # (results/op25_multi.log.20260902-182333): the link SELECTs alone are
        # ~8.7s at 55.4us each, so the write lock was held for the better part
        # of 15 seconds.
        #
        # sdr.db is WAL with busy_timeout=5000 (see sdr_db.SCHEMA): readers
        # never block, but a SECOND writer that waits longer than 5s gets
        # `database is locked`. Everything else that writes this database --
        # udp_audio_record.py per recorded call, stt_watch.py per transcript,
        # the web app -- therefore had a ~15s window in which its write could
        # fail. udp_audio_record.py catches that and prints
        # "WARNING: could not record ... in the database", so the .wav survives
        # on disk while the `calls` row silently does not: a recorded call
        # missing from the console's Recordings list.
        #
        # This was ALREADY true before the import moved off the shutdown
        # critical path -- stt_watch and the web app write continuously and do
        # not care what a capture is doing. Backgrounding the import (see
        # lwin_listen_multi.sh's cleanup()) widened the blast radius to the
        # NEXT session's recorders, which is what made it worth fixing rather
        # than what created it.
        #
        # The fix is a reorder, deliberately NOT a rewrite: the link query
        # below is byte-for-byte the one that was here before, still
        # per-grant, still "nearest call within LINK_WINDOW_S, ordered by
        # ABS(start - ts)". That tightness is deliberate (see LINK_WINDOW_S)
        # and a set-based rewrite would have quietly changed which call a
        # grant attaches to. Chunked commits were the other option and were
        # rejected too: they would give up the atomicity that makes the
        # span-DELETE below safe to re-run.
        #
        # PHASE 1 -- resolve links. Read-only, in autocommit, so no write lock
        # is held. This is the slow phase and now it costs other writers
        # nothing. It reads `calls`; phase 2 writes `grants` -- different
        # tables, so doing the reads first cannot change the outcome.
        rows = []
        for ts, tgid, src, freq, rf, st in grants:
            row = db.execute(
                """SELECT id FROM calls
                    WHERE tgid = ? AND ABS(start - ?) <= ?
                 ORDER BY ABS(start - ?) LIMIT 1""",
                (tgid, ts, LINK_WINDOW_S, ts)).fetchone()
            call_id = row['id'] if row else None
            if call_id:
                linked += 1
            rows.append((ts, tgid, src, freq, rf, st, call_id))

        # PHASE 2 -- one short write transaction: clear this log's span, insert
        # the resolved rows, commit. Still atomic, so a crash leaves the span
        # either fully replaced or untouched, never half-imported.
        #
        # MEASURED, and worth being precise about because it is easy to credit
        # the wrong change: on a temporary database with the same 156,794 rows,
        # this phase takes 0.27s -- and the OLD per-row execute() loop took
        # 0.30s for the identical inserts. Batching them is NOT what fixed
        # anything. The entire win is the REORDER above: the write lock used to
        # span the ~8.7s of link SELECTs as well, so it was held ~9.0s and is
        # now held ~0.27s, roughly 33x shorter. executemany() is used here
        # simply because phase 1 already produced the complete row list, so it
        # is the natural way to write it -- not as the optimisation.
        #
        # Clear only this log's time span, so re-importing one capture does not
        # duplicate its rows and does not touch any other capture's.
        lo, hi = min(g[0] for g in grants), max(g[0] for g in grants)
        db.execute('DELETE FROM grants WHERE ts BETWEEN ? AND ?', (lo, hi))
        db.executemany(
            """INSERT INTO grants (ts, tgid, src_addr, freq, rfss, site, call_id)
               VALUES (?,?,?,?,?,?,?)""",
            rows)
        db.commit()
    finally:
        db.close()

    print(f'imported       {len(grants)} grants, {linked} linked to a recorded call')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
