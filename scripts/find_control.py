#!/usr/bin/env python3
"""Find P25/trunking control channels: strong carriers with LOW time-variance.

A control channel transmits continuously -> high mean power, low std-dev.
A voice/traffic channel is bursty -> moderate mean, HIGH std-dev.
Ranking by (mean above floor) while filtering on low std separates them.
"""
import sys, csv, collections
import numpy as np

path = sys.argv[1]
min_db = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0

acc = collections.defaultdict(list)
for r in csv.reader(open(path)):
    if len(r) < 7: continue
    try:
        lo, bw = float(r[2]), float(r[4])
        vals = [float(x) for x in r[6:]]
    except ValueError: continue
    for i, v in enumerate(vals):
        acc[round(lo + bw*(i+0.5))].append(v)

freqs = np.array(sorted(acc))
mean = np.array([np.mean(acc[f]) for f in freqs])
std  = np.array([np.std(acc[f])  for f in freqs])
n    = np.array([len(acc[f])     for f in freqs])
floor = np.median(mean)
print(f"span {freqs[0]/1e6:.3f}-{freqs[-1]/1e6:.3f} MHz  bins={len(freqs)}  "
      f"floor={floor:.1f} dB  (avg {n.mean():.0f} sweeps/bin)\n")

above = mean - floor
cand = np.where(above > min_db)[0]
# collapse to local maxima >=12.5 kHz apart (P25 channel spacing)
picked = []
for i in sorted(cand, key=lambda k: -mean[k]):
    if all(abs(freqs[i]-freqs[j]) > 12500 for j in picked):
        picked.append(i)

print(f"{'MHz':>11} {'mean dB':>8} {'+floor':>7} {'std':>6}  verdict")
print("-"*56)
for i in sorted(picked, key=lambda k: freqs[k]):
    v = "CONTINUOUS (control?)" if std[i] < 2.5 else ("bursty (voice/data)" if std[i] < 6 else "very bursty")
    print(f"{freqs[i]/1e6:11.4f} {mean[i]:8.1f} {above[i]:7.1f} {std[i]:6.2f}  {v}")
print(f"\ncandidates: {len(picked)}")
