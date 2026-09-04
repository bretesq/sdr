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


def enc_pair_keys(log_text: str, *, min_obs: int = 2) -> list:
    """The (algid, keyid) groups in a log that are worth a brute-force run.

    Clear traffic is not a target. Observations with rs_errs > 0 are not counted
    towards a key id's evidence: the ESS carries Reed-Solomon residuals, and a
    corrupted KID both invents a group and strands real pairs away from the run
    that could use them. Five ADP key ids appear in this corpus — 0x22 (63),
    0x8 (21), 0x2F08 (5), 0x1 (4), 0x2EF4 (1) — and the two rare high values sit
    next to non-zero rs_errs, which is what the gate is for.
    """
    _, obs = enc_log.parse_log(log_text)
    counts = collections.Counter(
        (o.algid, o.keyid) for o in obs
        if o.algid in enc_log.KNOWN_ALGIDS
        and o.algid != enc_log.CLEAR_ALGID
        and o.rs_errs == 0)
    return sorted(k for k, n in counts.items() if n >= min_obs)


def load_overrides(path: str | None = None) -> dict:
    """Reviewed reclassifications, keyed by tgid. Absent file means none.

    Keys beginning with '_' are documentation, not talkgroups.

    `path` resolves at CALL time, not at def time. Written as
    `path: str = OVERRIDES` the default freezes at import, so a caller that
    sets SDR_ROOT or points OVERRIDES at a fixture afterwards is silently
    ignored and reads the real reference/ instead -- the failure mode being a
    test that passes against production data.
    """
    path = path or OVERRIDES
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return {int(k): v['enc'] for k, v in json.load(f).items()
                if not k.startswith('_')}


def apply_overrides(db, path: str | None = None) -> int:
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


def talkgroup_ess(log_text: str) -> dict:
    """Per-talkgroup ALGID counts from grant-attributed ESS.

    Deliberately independent of whether a call was RECORDED. Reconciliation is a
    talkgroup-level question — the grant already says which talkgroup an ESS
    belongs to — and requiring a bound call throws away exactly the evidence
    that matters most.

    Encrypted transmissions produce no audio worth recording, because op25 -n
    silences them, so they often leave no call for an ESS to attach to. Measured
    on results/op25_multi.log: 168 of 214 unbound observations are ADP, and
    TG19014 — which RadioReference labels 'clear' — carries 90 of them against 3
    recorded calls. Under call-binding that talkgroup is invisible, which is the
    opposite of useful when the question is "are we capturing encrypted traffic".
    """
    grants, obs = enc_log.parse_log(log_text)
    out: dict = collections.defaultdict(collections.Counter)
    for o, g in enc_log.attribute(grants, obs):
        if g is None or o.algid not in enc_log.KNOWN_ALGIDS:
            continue
        out[g.tgid][o.algid] += 1
    return dict(out)


def reconcile(db, ref: dict, *, min_obs: int = 5, log_text: str = '') -> list:
    """Talkgroups whose observed behaviour contradicts RadioReference.

    Two evidence streams, deliberately kept separate to avoid double counting:

      ESS      from `log_text`, at talkgroup level, recorded call or not.
      speech   from the database, which is where transcripts live.

    Only disagreements clear the report, and only above `min_obs` observations:
    small-N conclusions are not trustworthy. This proposes; it never writes. A
    human copies accepted rows into reference/enc_overrides.json.
    """
    agg = collections.defaultdict(collections.Counter)
    evid = collections.defaultdict(set)

    # ESS evidence comes from the log only. The database's ess-backed rows are a
    # strict subset of it — every bound observation was also attributed to a
    # grant — so counting both would double every recorded encrypted call.
    for tgid, algids in talkgroup_ess(log_text).items():
        for algid, n in algids.items():
            state = 'clear' if algid == enc_log.CLEAR_ALGID else 'encrypted'
            agg[tgid][state] += n
            evid[tgid].add('ess')

    rows = db.execute(
        "SELECT tgid, enc_observed, count(*) AS n FROM calls "
        "WHERE enc_observed IS NOT NULL AND tgid IS NOT NULL "
        "AND enc_evidence = 'speech' GROUP BY tgid, enc_observed").fetchall()
    for r in rows:
        agg[r['tgid']][r['enc_observed']] += r['n']
        evid[r['tgid']].add('speech')

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
    p.add_argument('--apply', action='store_true',
                   help='copy reference/enc_overrides.json onto talkgroups.enc')
    a = p.parse_args()

    db = sdr_db.connect(a.db)
    try:
        if a.apply:
            # The other half of --report. --report proposes a change and a
            # human writes it into enc_overrides.json; this is what carries an
            # accepted entry into the table the web layer reads, so the console
            # stops labelling a talkgroup by a classification already reviewed
            # and rejected. import_to_sqlite.py does the same after a rebuild.
            n = apply_overrides(db)
            print(f'applied {n} override(s) to talkgroups.enc')

        total = collections.Counter()
        seen_text = []
        for path in (a.logs or [DEFAULT_LOG]):
            if not os.path.exists(path):
                print(f'skip (missing): {path}')
                continue
            with open(path, errors='ignore') as f:
                text = f.read()
            seen_text.append(text)
            s = harvest(db, text)
            print(f'{os.path.basename(path)}: {s["bound"]} bound, '
                  f'{s["unbound"]} unbound, {s["updated"]} calls updated, '
                  f'{s["speech_only"]} from speech')
            total.update(s)
        print(f'TOTAL: {dict(total)}')

        if a.report:
            with open(f'{R}/reference/lwin_talkgroups.json') as f:
                ref = json.load(f)
            # The log, not just the database: a talkgroup whose encrypted
            # traffic never produced a recording has no rows to reconcile from,
            # and those are the ones that matter here.
            props = reconcile(db, ref, min_obs=a.min_obs,
                              log_text='\n'.join(seen_text))
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
