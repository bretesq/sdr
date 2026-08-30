#!/usr/bin/env python3
"""List recorded LWIN calls with talkgroup, duration and agency."""
import json, os, glob, re, wave, datetime, collections
R='/home/besquivel/rtl'
try: calls=json.load(open(f'{R}/recordings/calls.json'))
except Exception: calls=[]
if not calls:                                  # fall back to filenames
    db=json.load(open(f'{R}/reference/lwin_talkgroups.json'))
    for f in sorted(glob.glob(f'{R}/recordings/TG*.wav')):
        m=re.match(r'TG(\d+|unknown)_',os.path.basename(f))
        tg=int(m.group(1)) if m and m.group(1).isdigit() else None
        w=wave.open(f); d=w.getnframes()/float(w.getframerate()); w.close()
        e=db.get(str(tg),{})
        calls.append({'file':os.path.basename(f),'tgid':tg,'alpha':e.get('alpha'),
                      'desc':e.get('desc'),'enc':e.get('enc'),'cat':e.get('cat'),
                      'start':os.path.getmtime(f),'dur':round(d,2)})
if not calls: print("no recordings"); raise SystemExit
print(f"{'file':<46}{'when':<10}{'sec':>6}  {'enc':<8}description")
print('-'*104)
for c in sorted(calls,key=lambda c:c['start']):
    w=datetime.datetime.fromtimestamp(c['start']).strftime('%H:%M:%S')
    print(f"{c['file'][:45]:<46}{w:<10}{c['dur']:>6.1f}  {str(c.get('enc')):<8}{(c.get('desc') or '?')[:44]}")
agg=collections.defaultdict(lambda:[0,0.0,None])
for c in calls:
    a=agg[c['tgid']]; a[0]+=1; a[1]+=c['dur']; a[2]=c
print(f"\n{len(calls)} calls, {sum(c['dur'] for c in calls):.1f}s audio, {len(agg)} talkgroups\n")
for tg,(n,d,c) in sorted(agg.items(),key=lambda kv:-kv[1][1]):
    print(f"  TG {str(tg):<7}{n:>3} calls {d:>6.1f}s  {(c.get('alpha') or '?')[:20]:<21}{(c.get('desc') or '')[:40]}")
bad=[c['tgid'] for c in calls if c.get('enc') not in ('clear',None)]
if bad: print(f"\nWARNING: non-clear talkgroups recorded: {sorted(set(bad))}")
