#!/usr/bin/env python3
"""Import SNDCP packet data from op25's log into sdr.db's `packets` table.

The counterpart to import_grants.py, and it exists for the same reason: until
now everything this project learned about LWIN's packet data lived in log files
and a CLI script, so a log rotation took the history with it and none of it
could be joined against the voice side.

WHAT GETS A ROW
---------------
A PDU whose HEADER passed CRC16. Nothing weaker: a header that will not
validate tells us nothing trustworthy about the radio, the format or the
length, and a row asserting otherwise would be worse than no row.

`clear` is 1 only when the payload's own IPv4 header checksum validated -- a
16-bit check the decoder cannot satisfy by accident. So the column means
"proved cleartext", never "looked plausible".

RE-RUNNING IS SAFE
------------------
Rows are cleared over the time span of the logs being imported, then written,
inside one transaction -- the same approach import_grants.py uses. Re-importing
a capture therefore replaces its rows rather than duplicating them, and does
not touch any other capture's. A UNIQUE constraint was considered instead and
rejected: the natural key would be (ts, llid), and two PDUs sharing a
microsecond would then be silently dropped rather than replaced.

WHY THE PAYLOAD IS STORED
-------------------------
LRRP token bodies and the ARS flag nibble are deliberately undecoded (see
scripts/p25_apps.py). Keeping the application bytes means a better parser can
be run over the whole history later without re-capturing anything -- which
matters because the interesting payload is rare and the capture is live.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(R, 'scripts'))

import p25_apps                                             # noqa: E402
import p25_packet                                           # noqa: E402
from import_grants import TaggedStream                      # noqa: E402
from sdr_db import DB_PATH, connect                         # noqa: E402

# op25 stamps local time as MM/DD/YY HH:MM:SS.ffffff, at the head of the line
# and before the [receiver] tag. Same format import_grants.py parses.
TS = re.compile(r'^(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d+)')


def parse_ts(s: str) -> float:
    return dt.datetime.strptime(s, '%m/%d/%y %H:%M:%S.%f').timestamp()


def rows_from(paths: list[str], session_id: int | None):
    """Every importable PDU across these logs, as insert tuples."""
    out = []
    stats = {'lines': 0, 'raw': 0, 'decoded_only': 0, 'no_timestamp': 0,
             'clear': 0, 'app': 0}
    for path in paths:
        # Deduplicate per file, not globally: the raw dump and the decoded line
        # are adjacent within one log, and a rotated log may legitimately
        # repeat a header seen in another.
        seen_hdrs: set[bytes] = set()
        with open(path, errors='ignore') as fh:
            for line in fh:
                stats['lines'] += 1
                pdu = p25_packet.parse_raw_line(line)
                if pdu is not None:
                    seen_hdrs.add(pdu.hdr)
                    stats['raw'] += 1
                else:
                    pdu = p25_packet.parse_log_line(line)
                    if pdu is None:
                        continue
                    if pdu.hdr in seen_hdrs:
                        continue        # same frame, already imported properly
                    stats['decoded_only'] += 1

                m = TS.match(line)
                if not m:
                    # Every op25 line carries one; a line without it is a
                    # truncated write, and a row with no time is unjoinable.
                    stats['no_timestamp'] += 1
                    continue
                ts = parse_ts(m.group(1))

                verdict = p25_packet.classify(pdu)
                ip = verdict.detail.get('ip') or {}
                udp = ip.get('udp') or {}
                app = None
                if udp and verdict.clear:
                    app = p25_apps.parse(udp.get('sport'), udp.get('dport'),
                                         udp.get('data', b''))
                if verdict.clear:
                    stats['clear'] += 1
                if app is not None:
                    stats['app'] += 1

                out.append((
                    ts, session_id, pdu.llid, pdu.nac, pdu.fmt,
                    # NULL for a response PDU: octet 1 is not a SAP there, and
                    # storing the number would read as a service.
                    pdu.sap if pdu.sap_valid else None,
                    pdu.hdr_blks, pdu.blks,
                    ip.get('src'), ip.get('dst'), ip.get('protocol'),
                    udp.get('sport'), udp.get('dport'),
                    1 if verdict.clear else 0,
                    app.protocol if app else None,
                    app.kind if app else None,
                    udp.get('data', b'').hex() if udp else None,
                ))
    return out, stats


def run(a) -> int:
    paths = [p for p in a.log if os.path.exists(p)]
    missing = [p for p in a.log if not os.path.exists(p)]
    for p in missing:
        sys.stderr.write(f'import_packets: no such log: {p}\n')
    if not paths:
        sys.stderr.write('import_packets: nothing to read\n')
        return 1

    rows, stats = rows_from(paths, a.session_id)
    print(f'logs           {len(paths)}')
    print(f'lines read     {stats["lines"]}')
    print(f'  raw frames   {stats["raw"]}')
    print(f'  header-only  {stats["decoded_only"]}  (no raw dump; payload unavailable)')
    if stats['no_timestamp']:
        print(f'  SKIPPED      {stats["no_timestamp"]} with no parsable timestamp')
    print(f'importable     {len(rows)} PDUs')
    print(f'  cleartext    {stats["clear"]}  (IPv4 checksum validated)')
    print(f'  with app msg {stats["app"]}')

    if not rows:
        return 0
    if a.dry_run:
        print('dry run: nothing written')
        return 0

    db = connect(a.db)
    try:
        lo = min(r[0] for r in rows)
        hi = max(r[0] for r in rows)
        with db:
            # Clear only this span, so re-importing one capture replaces its
            # own rows and leaves every other capture alone.
            before = db.execute(
                'SELECT COUNT(*) FROM packets WHERE ts BETWEEN ? AND ?',
                (lo, hi)).fetchone()[0]
            db.execute('DELETE FROM packets WHERE ts BETWEEN ? AND ?', (lo, hi))
            db.executemany(
                """INSERT INTO packets
                     (ts, session_id, llid, nac, fmt, sap,
                      blks_claimed, blks_recovered,
                      src_ip, dst_ip, proto, sport, dport, clear,
                      app, app_kind, app_payload)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        print(f'imported       {len(rows)} PDUs (replaced {before} in the same span)')

        radios = db.execute(
            'SELECT COUNT(DISTINCT llid) FROM packets WHERE llid IS NOT NULL'
        ).fetchone()[0]
        both = db.execute(
            """SELECT COUNT(DISTINCT p.llid) FROM packets p
               WHERE p.llid IN (SELECT src_addr FROM calls
                                WHERE src_addr IS NOT NULL AND src_addr != 0)"""
        ).fetchone()[0]
        print(f'  radios       {radios} total, {both} also heard on voice')
        for r in db.execute(
                """SELECT app, app_kind, COUNT(*) n FROM packets
                   WHERE app IS NOT NULL GROUP BY 1,2 ORDER BY n DESC"""):
            print(f'    {r["n"]:6d}  {r["app"]}: {r["app_kind"]}')
    finally:
        db.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('log', nargs='*',
                    default=[os.path.join(R, 'results', 'op25_multi.log')])
    ap.add_argument('--db', default=DB_PATH)
    ap.add_argument('--session-id', type=int, default=None,
                    help='tag rows with this sessions.id')
    ap.add_argument('--dry-run', action='store_true',
                    help='report only; write nothing')
    ap.add_argument('--tag', default=None,
                    help='stamp every output line with this token, so two '
                         'interleaved imports stay separable in one log file')
    a = ap.parse_args()

    if not a.tag:
        return run(a)

    # Same discipline, and the same reasoning, as import_grants.py: the
    # launcher appends this program's output to a shared file from a detached
    # process, and an import can still be running when the next capture starts.
    # A token on the HEADER alone is not enough -- grouping needs the token on
    # the lines being grouped, or one import's summary lands under another's
    # header and reads as that import's result.
    #
    # Saved and restored rather than replaced outright: a leaked wrapper would
    # stamp later output with a stale tag, which matters because this is also
    # called in-process by scripts/tests/test_import_packets.py.
    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout = TaggedStream(real_stdout, a.tag)
    sys.stderr = TaggedStream(real_stderr, a.tag)
    status = 1
    try:
        status = run(a)
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
        # The END line is what distinguishes "failed" from "killed before it
        # could report" when reading the shared log, so it is printed through
        # the tagged stream and then the wrapper comes off.
        print(f'=== END {a.tag} exit {status}', file=TaggedStream(real_stdout, a.tag))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
