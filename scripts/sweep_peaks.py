#!/usr/bin/env python3
"""Parse hackrf_sweep CSV -> per-bin power spectrum, report peaks above noise floor.

hackrf_sweep row: date, time, hz_low, hz_high, hz_bin_width, num_samples, dB...
Each row covers one sub-block; rows accumulate across sweeps, so we average
power per frequency bin before peak-picking.
"""
import sys, csv, collections
import numpy as np

path = sys.argv[1]
thresh_db = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0   # dB above median floor
min_sep_khz = float(sys.argv[3]) if len(sys.argv) > 3 else 150.0

acc = collections.defaultdict(list)
with open(path) as f:
    for row in csv.reader(f):
        if len(row) < 7:
            continue
        try:
            lo, hi, bw = float(row[2]), float(row[3]), float(row[4])
            vals = [float(v) for v in row[6:]]
        except ValueError:
            continue
        for i, v in enumerate(vals):
            acc[round(lo + bw * (i + 0.5))].append(v)

if not acc:
    sys.exit("no data parsed")

freqs = np.array(sorted(acc))
power = np.array([np.mean(acc[f]) for f in freqs])
floor = np.median(power)
mad = np.median(np.abs(power - floor))

print(f"bins={len(freqs)}  span={freqs[0]/1e6:.3f}-{freqs[-1]/1e6:.3f} MHz")
print(f"noise floor (median) = {floor:.1f} dB   MAD = {mad:.1f} dB")
print(f"reporting peaks > floor + {thresh_db} dB\n")

cand = np.where(power > floor + thresh_db)[0]
peaks = []
for i in cand:
    # local maximum within +/- min_sep window
    w = np.abs(freqs - freqs[i]) < min_sep_khz * 1e3
    if power[i] >= power[w].max():
        peaks.append(i)

# de-duplicate peaks closer than min_sep
out = []
for i in sorted(peaks, key=lambda k: -power[k]):
    if all(abs(freqs[i] - freqs[j]) > min_sep_khz * 1e3 for j in out):
        out.append(i)

print(f"{'MHz':>12}  {'dB':>7}  {'above floor':>11}")
for i in sorted(out, key=lambda k: freqs[k]):
    print(f"{freqs[i]/1e6:12.4f}  {power[i]:7.1f}  {power[i]-floor:11.1f}")
print(f"\ntotal peaks: {len(out)}")
