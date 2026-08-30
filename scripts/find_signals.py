#!/usr/bin/env python3
"""Find real narrowband carriers in a wide hackrf_sweep, using a ROLLING-MEDIAN
baseline so a locally-elevated noise floor isn't mistaken for signal."""
import sys, csv, collections
import numpy as np
from scipy import ndimage

path = sys.argv[1]
thresh = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
win_mhz = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0   # baseline window

acc = collections.defaultdict(list)
for r in csv.reader(open(path)):
    if len(r) < 7: continue
    try:
        lo, bw = float(r[2]), float(r[4]); vals = [float(x) for x in r[6:]]
    except ValueError: continue
    for i, v in enumerate(vals): acc[round(lo + bw*(i+0.5))].append(v)

f = np.array(sorted(acc))
m = np.array([np.mean(acc[k]) for k in f])
s = np.array([np.std(acc[k])  for k in f])
binhz = np.median(np.diff(f))
w = max(11, int(win_mhz*1e6/binhz) | 1)                 # odd window
base = ndimage.median_filter(m, size=w, mode='nearest') # local floor
excess = m - base
# local noise scale for a z-like score
mad = ndimage.median_filter(np.abs(m - base), size=w, mode='nearest') + 1e-9

print(f"span {f[0]/1e6:.1f}-{f[-1]/1e6:.1f} MHz  bins={len(f)}  bin={binhz/1e3:.0f} kHz")
print(f"rolling baseline window = {win_mhz} MHz ({w} bins)\n")

cand = np.where(excess > thresh)[0]
groups = []
for i in cand:
    if groups and i - groups[-1][-1] <= 2: groups[-1].append(i)
    else: groups.append([i])

print(f"{'MHz':>11}{'excess':>8}{'z':>6}{'BW kHz':>8}{'std':>6}  verdict")
print("-"*62)
out = []
for g in groups:
    pk = g[int(np.argmax(excess[g]))]
    bwk = (f[g[-1]] - f[g[0]] + binhz)/1e3
    z = excess[pk]/mad[pk]
    if z < 4: continue
    v = "CONTINUOUS (control/data)" if s[pk] < 2.5 else ("bursty (voice)" if s[pk] < 6 else "very bursty")
    out.append((f[pk], excess[pk], z, bwk, s[pk], v))
for fq, ex, z, bwk, st, v in sorted(out, key=lambda t: -t[1])[:32]:
    print(f"{fq/1e6:11.4f}{ex:8.1f}{z:6.1f}{bwk:8.1f}{st:6.2f}  {v}")
print(f"\n{len(out)} real carriers (rolling baseline, z>=4)")
