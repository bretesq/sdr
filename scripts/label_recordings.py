#!/usr/bin/env python3
"""Label recorded calls with talkgroup names by correlating WAV start times
against op25's timestamped 'tg(NNN)' log lines."""
import json, re, os, glob, wave, datetime, sys

R = '/home/besquivel/rtl'
db = json.load(open(f'{R}/reference/lwin_talkgroups.json'))
log = f'{R}/results/op25_record.log'

# build (epoch, tgid) timeline from op25 log
events = []
raw = open(log, errors='ignore').read()
raw = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', raw)
for m in re.finditer(r'(\d\d)/(\d\d)/(\d\d) (\d\d):(\d\d):(\d\d)\.(\d+)[^\n]*?tg\((\d+)\)', raw):
    mo, d, y, H, M, S, us, tg = m.groups()
    try:
        t = datetime.datetime(2000 + int(y), int(mo), int(d), int(H), int(M), int(S),
                              int(us[:6].ljust(6, '0')))
        events.append((t.timestamp(), int(tg)))
    except ValueError:
        continue
events.sort()

rows = []
for f in sorted(glob.glob(f'{R}/recordings/call-*.wav')):
    st = float(re.search(r'call-([\d.]+)\.wav', f).group(1))
    w = wave.open(f); dur = w.getnframes() / float(w.getframerate()); w.close()
    # nearest tg event within +/- 6 s of the call start
    cands = [(abs(t - st), tg) for t, tg in events if abs(t - st) < 6.0]
    tg = min(cands)[1] if cands else None
    rows.append((f, st, dur, tg))

if not rows:
    print("no recordings found"); raise SystemExit

print(f"{'file':<30}{'when':<10}{'sec':>6}  {'TG':>6}  {'alpha':<18} description")
print('-' * 104)
tot = 0.0
seen = {}
for f, st, dur, tg in rows:
    e = db.get(str(tg), {}) if tg else {}
    when = datetime.datetime.fromtimestamp(st).strftime('%H:%M:%S')
    print(f"{os.path.basename(f):<30}{when:<10}{dur:>6.1f}  {str(tg or '?'):>6}  "
          f"{e.get('alpha','?')[:18]:<18} {e.get('desc','(unmatched)')[:40]}")
    tot += dur
    if tg: seen.setdefault(tg, [0, 0.0, e]); seen[tg][0] += 1; seen[tg][1] += dur
print(f"\n{len(rows)} calls, {tot:.1f}s of audio")
print("\nby talkgroup:")
for tg, (n, d, e) in sorted(seen.items(), key=lambda kv: -kv[1][1]):
    enc = e.get('enc', '?')
    print(f"  TG {tg:<7}{n:>3} calls {d:>6.1f}s  {e.get('alpha','?')[:20]:<21}{e.get('desc','')[:38]:<39}[{enc}]")
bad = [tg for tg in seen if seen[tg][2].get('enc') not in ('clear', None)]
if bad: print(f"\nWARNING: non-clear talkgroups present: {bad}")
