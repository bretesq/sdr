#!/usr/bin/env python3
"""Build an op25 talkgroup whitelist from the LWIN reference DB.

Safety: only `clear` talkgroups are selected by default. `--include-partial` adds
partially-encrypted talkgroups (which carry mostly clear traffic — see OBSERVATIONS.md §5);
op25's -n still silences any encrypted frames. `--include-encrypted` adds fully-encrypted
talkgroups, which will record as silence.
"""
import json, os, re, sys, argparse, collections

# SDR_ROOT so the script can be exercised against a worktree or a test fixture
# instead of only the live tree. enc_harvest.py and stt_backend.py already
# resolve their root this way; unset, the behaviour is unchanged.
R = os.environ.get('SDR_ROOT', '/home/besquivel/rtl')
BR_AREA = ('East Baton Rouge', 'Baton Rouge', 'LSU', 'Southern University',
           'State Police - Troop A', 'West Baton Rouge', 'Livingston', 'Ascension',
           'Iberville', 'Feliciana', 'Pointe Coupee', 'EMS Agencies',
           'Wildlife and Fisheries')

PRESETS = {
    'pd':        ['Law Dispatch'],
    'pd-all':    ['Law Dispatch', 'Law Talk', 'Law Tac'],
    'fire':      ['Fire Dispatch'],
    'fire-all':  ['Fire Dispatch', 'Fire-Tac', 'Fire-Talk'],
    'ems':       ['EMS Dispatch', 'EMS-Tac', 'EMS-Talk', 'Hospital'],
    'interop':   ['Interop', 'Emergency Ops', 'Multi-Tac', 'Multi-Dispatch'],
    'schools':   ['Schools'],
    'publicworks': ['Public Works', 'Utilities', 'Transportation'],
    'all':       None,
}

ap = argparse.ArgumentParser(description='Build an op25 whitelist from the LWIN reference DB')
ap.add_argument('-p', '--preset', choices=sorted(PRESETS), default='all')
ap.add_argument('-t', '--tag', help='comma-separated tag(s), e.g. "Law Dispatch,Law Talk"')
ap.add_argument('-g', '--tg', help='comma-separated talkgroup IDs (overrides other filters)')
ap.add_argument('--add-tg', help='comma-separated talkgroup IDs to ADD to whatever the '
                                 'preset/tag/match selected, including IDs absent from the '
                                 'reference DB')
ap.add_argument('-m', '--match', help='regex matched against alpha, description and category')
ap.add_argument('--all-areas', action='store_true', help='statewide, not just Baton Rouge area')
ap.add_argument('--include-partial', action='store_true')
ap.add_argument('--include-encrypted', action='store_true')
ap.add_argument('-o', '--out', default=f'{R}/lwin_clear_whitelist.txt')
ap.add_argument('-l', '--list', action='store_true', help='print selection, do not write')
a = ap.parse_args()

db = json.load(open(f'{R}/reference/lwin_talkgroups.json'))

# Overlay reviewed reclassifications onto the in-memory copy.
#
# RadioReference's `enc` says how a talkgroup is DOCUMENTED, not what it
# transmits: 367 of 377 calls on talkgroups flagged 'full' carry real speech,
# and TG17282 is flagged 'clear' while carrying encrypted traffic. Overrides are
# how a human records the observed truth without editing the upstream scrape.
#
# Applied here rather than at each `v['enc']` site so the filter, the listing
# and the summary counters all agree. Safe to mutate: this script only ever
# reads lwin_talkgroups.json, so the overlay cannot reach the file on disk.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import enc_harvest                                            # noqa: E402
_overrides = enc_harvest.load_overrides()
for _k, _v in db.items():
    _ov = _overrides.get(int(_k))
    if _ov is not None:
        _v['enc'] = _ov

allowed = {'clear'}
if a.include_partial:   allowed.add('partial')
if a.include_encrypted: allowed.add('full')

sel = []
if a.tg:
    want = {int(x) for x in re.split(r'[,\s]+', a.tg.strip()) if x}
    sel = [(int(k), v) for k, v in db.items() if int(k) in want]
    missing = want - {t for t, _ in sel}
    if missing: print(f"warning: not in reference DB: {sorted(missing)}", file=sys.stderr)
else:
    tags = None
    if a.tag:                       tags = [t.strip() for t in a.tag.split(',')]
    elif PRESETS[a.preset]:         tags = PRESETS[a.preset]
    rx = re.compile(a.match, re.I) if a.match else None
    for k, v in db.items():
        if not a.all_areas and not any(b in v['cat'] for b in BR_AREA):      continue
        if tags and v['tag'] not in tags:                                    continue
        if rx and not rx.search(' '.join(str(v.get(f, '')) for f in ('alpha','desc','cat','tag'))):
            continue
        sel.append((int(k), v))

kept    = [(t, v) for t, v in sel if v['enc'] in allowed]
dropped = [(t, v) for t, v in sel if v['enc'] not in allowed]

# --add-tg: union extra talkgroups onto whatever was selected above.
#
# Two things it does that --tg cannot, both of which came from a real request
# to follow two busy talkgroups the preset was skipping:
#
#   1. It ADDS. `--tg` replaces the selection entirely (`if a.tg: ... else:`),
#      so using it to pick up two talkgroups silently drops the preset's other
#      222.
#   2. It honours an ID the reference DB has never heard of. `--tg` selects
#      FROM the DB, so an unknown ID produces a stderr warning and no entry --
#      you ask to follow a talkgroup and get nothing. TG 20000 is exactly that
#      case: the second-busiest talkgroup on the air here and absent from
#      RadioReference. The receiver does not need a name to follow a number.
#
# These bypass the `enc` filter deliberately. An explicitly named talkgroup is
# an instruction, not a suggestion, and --include-encrypted should not have to
# be repeated to honour it.
if a.add_tg:
    extra = {int(x) for x in re.split(r'[,\s]+', a.add_tg.strip()) if x}
    have = {t for t, _ in kept}
    for tg in sorted(extra - have):
        meta = db.get(str(tg))
        kept.append((tg, meta or {
            'alpha': '', 'desc': 'not in the reference DB', 'cat': '',
            'tag': '', 'enc': 'clear',
        }))

kept.sort()

if a.list or not kept:
    print(f"{'TG':>7} {'enc':<8}{'tag':<15}{'alpha':<20}{'description':<38}category")
    for t, v in kept:
        print(f"{t:>7} {v['enc']:<8}{v['tag'][:15]:<15}{v['alpha'][:20]:<20}"
              f"{v['desc'][:38]:<38}{v['cat'][:34]}")

enc_c = collections.Counter(v['enc'] for _, v in kept)
print(f"\nselected {len(kept)} talkgroups  ({dict(enc_c)})", file=sys.stderr)
if dropped:
    d = collections.Counter(v['enc'] for _, v in dropped)
    print(f"excluded {len(dropped)} by encryption filter ({dict(d)}) — "
          f"--include-partial / --include-encrypted to add", file=sys.stderr)
if not kept:
    print("nothing selected; refusing to write an empty whitelist", file=sys.stderr); sys.exit(2)
if not a.list:
    open(a.out, 'w').write("\n".join(str(t) for t, _ in kept) + "\n")
    print(f"wrote {a.out}", file=sys.stderr)
