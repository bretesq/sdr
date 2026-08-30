#!/usr/bin/env python3
"""Resolve decoded LWIN talkgroup/site IDs against the local reference DB."""
import json, re, sys, collections, os

log = sys.argv[1] if len(sys.argv) > 1 else 'results/lwin_live.clean'
tg_db = json.load(open('reference/lwin_talkgroups.json')) if os.path.exists('reference/lwin_talkgroups.json') else {}
sites = json.load(open('reference/lwin_sites.json')) if os.path.exists('reference/lwin_sites.json') else []
site_by = {(s['rfss'], s['site_dec']): s for s in sites}

txt = open(log, errors='ignore').read()
tg = collections.Counter(int(m) for m in re.findall(r'ga\d: (\d+)', txt))
adj = set()
for m in re.finditer(r'adj_sts_bcst: rfid: (\w+) stid: (\w+)', txt):
    adj.add((int(m.group(1)), int(m.group(2))))
here = re.search(r'rfss_sts_bcst: syid: (\w+) rfid: (\w+) stid: (\w+) ch1: \w+\(([\d.]+)\)', txt)
net = re.search(r'net_sts_bcst: wacn: (\w+) syid: (\w+)', txt)
nac = collections.Counter(re.findall(r'NAC 0x([0-9a-f]+)', txt))
tsbk = len(re.findall(r'TSBK: op=', txt))

print("="*78)
print("LWIN DECODE — ANNOTATED")
print("="*78)
if net: print(f"WACN 0x{net.group(1).upper()}   SYSID 0x{net.group(2).upper()}   (Louisiana statewide)")
if nac: print(f"NAC  0x{nac.most_common(1)[0][0].upper()}  ({nac.most_common(1)[0][1]} frames)")
if here:
    r, s = int(here.group(2)), int(here.group(3))
    info = site_by.get((r, s))
    print(f"Site RFSS {r} Site {s} (0x{s:02X})  CC {here.group(4)} MHz"
          + (f"  -> {info['name_county']}" if info else ""))
print(f"TSBK messages decoded: {tsbk}")

print(f"\n--- ADJACENT SITES ({len(adj)}) ---")
for r, s in sorted(adj, key=lambda x: x[1]):
    i = site_by.get((r, s))
    print(f"  RFSS {r} Site {s:<3} (0x{s:02X})  " + (f"{i['name_county']:<44} NAC {i['nac']:<4} CTRL {','.join(i['control'][:2])}" if i else "(unknown)"))

print(f"\n--- TALKGROUPS ({len(tg)}) ---")
print(f"{'TG':>7} {'grants':>7}  {'alpha':<20} {'description':<40} {'enc':<8} category")
for t, c in tg.most_common():
    e = tg_db.get(str(t))
    if e:
        print(f"{t:>7} {c:>7}  {e['alpha'][:20]:<20} {e['desc'][:40]:<40} {e['enc']:<8} {e['cat']}")
    else:
        print(f"{t:>7} {c:>7}  {'(not in DB)':<20}")
enc = [t for t in tg if (tg_db.get(str(t)) or {}).get('enc') in ('full','partial')]
if enc: print(f"\nNOTE: {len(enc)} observed talkgroup(s) are flagged encrypted — metadata only, no audio: {enc}")
