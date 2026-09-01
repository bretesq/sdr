#!/usr/bin/env python3
"""Harvest observed encryption facts from op25 logs into sdr.db.

Reads logs, binds ESS observations to the calls they actually belong to, and
writes calls.enc_observed / enc_evidence / enc_source. Runs outside the
recording path, so it cannot disturb capture, and is re-runnable, so it
backfills history.

WHY THIS IS NEEDED
------------------
A recording can report its talkgroup "fully encrypted" while playing clear
voice. That label is talkgroups.enc, scraped from RadioReference, describing how
a talkgroup is documented rather than what a transmission carried: 367 of 377
calls on talkgroups flagged 'full' contain real speech. Encryption in P25 is
per-transmission, announced by an ESS ALGID sent in the clear.

Usage:
  enc_harvest.py [LOG ...] [--db PATH]

With no LOG, reads results/op25_multi.log.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import enc_log
import sdr_db

R = os.environ.get('SDR_ROOT', '/home/besquivel/rtl')
DEFAULT_LOG = f'{R}/results/op25_multi.log'

# How far outside a call's [start, start+dur] window an observation may fall and
# still belong to it. Log timestamps and calls.start agree to within ~1.6 s in
# practice; this absorbs that without letting one call claim its neighbour.
SLACK = 2.0

OVERRIDES = f'{R}/reference/enc_overrides.json'


def _calls_by_tgid(db) -> dict:
    """Every call's (id, start, end) grouped by talkgroup.

    The interval is start .. start+dur, NOT start .. ended_at: ended_at is NULL
    on 3,247 of 4,606 rows, so binding on it would skip most of the corpus.
    """
    out = collections.defaultdict(list)
    for r in db.execute('SELECT id, tgid, start, dur FROM calls '
                        'WHERE tgid IS NOT NULL AND start IS NOT NULL'):
        out[r['tgid']].append((r['id'], r['start'], r['start'] + (r['dur'] or 0.0)))
    for v in out.values():
        v.sort(key=lambda t: t[1])
    return out


def harvest(db, log_text: str) -> dict:
    """Bind observations from `log_text` to calls and write the columns."""
    grants, obs = enc_log.parse_log(log_text)
    pairs = enc_log.attribute(grants, obs)
    by_tg = _calls_by_tgid(db)

    per_call = collections.defaultdict(list)      # call_id -> [(algid, keyid, mi)]
    stats = collections.Counter({'bound': 0, 'unbound': 0, 'updated': 0,
                                 'speech_only': 0})

    for o, g in pairs:
        if g is None:
            stats['unbound'] += 1
            continue
        hit = None
        for call_id, start, end in by_tg.get(g.tgid, ()):
            if start - SLACK <= o.ts <= end + SLACK:
                hit = call_id
                break
        if hit is None:
            # Attributable to a talkgroup but to no recorded call — counted and
            # reported, never attached to the nearest one.
            stats['unbound'] += 1
            continue
        stats['bound'] += 1
        per_call[hit].append((o.algid, o.keyid, o.mi))

    for call_id, seen in per_call.items():
        state = enc_log.classify([a for a, _, _ in seen])
        if state is None:
            continue
        algid, keyid, mi = seen[0]
        db.execute(
            'UPDATE calls SET enc_observed=?, enc_evidence=?, enc_source=?, '
            'algid=?, keyid=?, mi=? WHERE id=?',
            (state, 'ess', 'harvest', algid, keyid, mi, call_id))
        stats['updated'] += 1

    # Speech evidence for calls no ESS covered. This is the only evidence
    # available for talkgroups like 17166, which has 21 calls and no ESS at all.
    for r in db.execute(
            "SELECT id, transcript FROM calls "
            "WHERE enc_observed IS NULL AND trim(coalesce(transcript,'')) <> ''"
    ).fetchall():
        if enc_log.is_speech(r['transcript']):
            db.execute('UPDATE calls SET enc_observed=?, enc_evidence=?, '
                       'enc_source=? WHERE id=?',
                       ('clear', 'speech', 'harvest', r['id']))
            stats['speech_only'] += 1

    db.commit()
    return dict(stats)


def load_overrides(path: str = OVERRIDES) -> dict:
    """Reviewed reclassifications, keyed by tgid. Absent file means none.

    Keys beginning with '_' are documentation, not talkgroups.
    """
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return {int(k): v['enc'] for k, v in json.load(f).items()
                if not k.startswith('_')}


def apply_overrides(db, path: str = OVERRIDES) -> int:
    """Copy reviewed overrides onto talkgroups.enc so the web layer sees them.

    reference/lwin_talkgroups.json stays untouched — it is the upstream scrape,
    and fetch_lwin_db.py must be able to re-run without clobbering a decision.
    The talkgroups TABLE is derived (import_to_sqlite.py rebuilds it from that
    JSON), so it is the right place for a human decision to land, and
    enc_overridden marks it as one rather than as scraped fact.
    """
    n = 0
    for tgid, enc in load_overrides(path).items():
        cur = db.execute(
            'UPDATE talkgroups SET enc = ?, enc_overridden = 1 WHERE tgid = ?',
            (enc, tgid))
        n += cur.rowcount
    db.commit()
    return n


def resolve_enc(tgid: int, ref: dict, overrides: dict) -> str | None:
    """The encryption class to act on: a reviewed override, else the scrape."""
    if tgid in overrides:
        return overrides[tgid]
    return (ref.get(str(tgid)) or {}).get('enc')


def reconcile(db, ref: dict, *, min_obs: int = 5) -> list:
    """Talkgroups whose observed behaviour contradicts RadioReference.

    Only disagreements clear the report, and only above `min_obs` observations:
    ESS reaches 19% of calls, so small-N conclusions are not trustworthy. This
    proposes; it never writes. A human copies accepted rows into
    reference/enc_overrides.json.
    """
    rows = db.execute(
        "SELECT tgid, enc_observed, enc_evidence, count(*) AS n FROM calls "
        "WHERE enc_observed IS NOT NULL AND tgid IS NOT NULL "
        "GROUP BY tgid, enc_observed, enc_evidence").fetchall()

    agg = collections.defaultdict(collections.Counter)
    evid = collections.defaultdict(set)
    for r in rows:
        agg[r['tgid']][r['enc_observed']] += r['n']
        evid[r['tgid']].add(r['enc_evidence'])

    out = []
    for tgid, counts in sorted(agg.items()):
        total = sum(counts.values())
        if total < min_obs:
            continue
        clear = counts['clear']
        enc = counts['encrypted'] + counts['mixed']
        if enc == 0:
            proposed = 'clear'
        elif clear == 0:
            proposed = 'full'
        else:
            proposed = 'partial'
        rr = (ref.get(str(tgid)) or {}).get('enc')
        if rr == proposed:
            continue
        out.append({'tgid': tgid, 'rr': rr, 'proposed': proposed,
                    'clear': clear, 'encrypted': enc,
                    'evidence': ','.join(sorted(evid[tgid]))})
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('logs', nargs='*')
    p.add_argument('--db', default=sdr_db.DB_PATH)
    p.add_argument('--report', action='store_true',
                   help='print talkgroups whose observed behaviour disagrees')
    p.add_argument('--min-obs', type=int, default=5,
                   help='minimum observations before proposing a change')
    a = p.parse_args()

    db = sdr_db.connect(a.db)
    try:
        total = collections.Counter()
        for path in (a.logs or [DEFAULT_LOG]):
            if not os.path.exists(path):
                print(f'skip (missing): {path}')
                continue
            with open(path, errors='ignore') as f:
                s = harvest(db, f.read())
            print(f'{os.path.basename(path)}: {s["bound"]} bound, '
                  f'{s["unbound"]} unbound, {s["updated"]} calls updated, '
                  f'{s["speech_only"]} from speech')
            total.update(s)
        print(f'TOTAL: {dict(total)}')

        if a.report:
            with open(f'{R}/reference/lwin_talkgroups.json') as f:
                ref = json.load(f)
            props = reconcile(db, ref, min_obs=a.min_obs)
            print(f'\n{len(props)} talkgroup(s) disagree with RadioReference '
                  f'(>= {a.min_obs} observations)')
            print(f'{"TG":>7} {"RR":<9}{"proposed":<10}{"clear":>6}{"enc":>5}  evidence')
            for p_ in props:
                print(f'{p_["tgid"]:>7} {str(p_["rr"]):<9}{p_["proposed"]:<10}'
                      f'{p_["clear"]:>6}{p_["encrypted"]:>5}  {p_["evidence"]}')
            print('\nAccept by adding entries to reference/enc_overrides.json')
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
