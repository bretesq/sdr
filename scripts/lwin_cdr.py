#!/usr/bin/env python3
"""Build a complete LWIN call-detail record from an op25 control-channel log.

Because the whitelist points at a non-existent talkgroup, op25 never leaves the
control channel, so every grant is observed.
"""
import re, json, sys, collections, datetime

R='/home/besquivel/rtl'
log = sys.argv[1] if len(sys.argv)>1 else f'{R}/results/lwin_cdr.log'
db = json.load(open(f'{R}/reference/lwin_talkgroups.json'))
raw = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', open(log, errors='ignore').read())

TS = r'(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d+)'
calls=[]
for m in re.finditer(TS + r' .*?set tgid=(\d+), srcaddr=(\d+)', raw):
    calls.append((m.group(1), int(m.group(2)), int(m.group(3))))
freqs = collections.Counter(re.findall(r'new freq=([\d.]+)', raw))
tsbk  = collections.Counter(re.findall(r'TSBK: op=(\w+)', raw))
tgs   = collections.Counter(c[1] for c in calls)
srcs  = collections.Counter(c[2] for c in calls if c[2])
adj   = set(re.findall(r'adj_sts_bcst: rfid: (\w+) stid: (\w+)', raw))

print("="*86); print("LWIN CALL-DETAIL RECORD (control channel, no retuning)"); print("="*86)
print(f"grant events : {len(calls)}")
print(f"TSBK msgs    : {sum(tsbk.values())}   distinct opcodes: {len(tsbk)}")
print(f"talkgroups   : {len(tgs)}   distinct source radios: {len(srcs)}")
print(f"voice freqs  : {len(freqs)}")

print(f"\n--- TALKGROUP ACTIVITY ({len(tgs)}) ---")
print(f"{'TG':>8}{'grants':>8}  {'enc':<8}{'alpha':<20} description")
clear_n=enc_n=0
for tg,c in tgs.most_common(40):
    e=db.get(str(tg),{})
    en=e.get('enc','?')
    if en=='clear': clear_n+=c
    elif en in ('full','partial'): enc_n+=c
    print(f"{tg:>8}{c:>8}  {en:<8}{e.get('alpha','?')[:20]:<20} {e.get('desc','(not in DB)')[:40]}")
print(f"\ngrants on clear TGs: {clear_n}   on encrypted TGs: {enc_n}")

if freqs:
    print(f"\n--- VOICE CHANNELS USED ---")
    for f,c in freqs.most_common(14): print(f"   {f} MHz   x{c}")
print(f"\n--- ADJACENT SITES ({len(adj)}) ---")
print("   " + ", ".join(f"S{int(s,16) if not s.isdigit() else int(s)}" for _,s in sorted(adj)))
if srcs:
    print(f"\n--- most active source radios (unit IDs) ---")
    for s,c in srcs.most_common(8): print(f"   unit {s}: {c} transmissions")
