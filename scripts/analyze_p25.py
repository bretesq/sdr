#!/usr/bin/env python3
"""Characterize a suspected P25 control channel from an offset-tuned cs8 capture.

Evidence gathered:
  1. Is the carrier present at the expected offset, and how strong in 12.5 kHz BW?
  2. Is the occupied bandwidth consistent with P25 (~12.5 kHz)?
  3. Does the signal show a 4800 baud symbol rate (P25 C4FM/CQPSK)?
"""
import numpy as np
from scipy import signal as sg

FS = 2_000_000
OFFSET = 200_000          # CC sits +200 kHz from tuned center
path = 'captures/lwin_cc_771881k_s2M.cs8'

raw = np.fromfile(path, dtype=np.int8, count=2*FS*18)
x = raw[0::2].astype(np.float32) + 1j*raw[1::2].astype(np.float32)
print(f"samples: {len(x):,}  ({len(x)/FS:.1f} s)")
print(f"clipping: {100*np.mean(np.abs(raw)>=127):.4f}%  rms={np.sqrt(np.mean(np.abs(x)**2)):.1f}/127")

# --- 1. wideband PSD, locate real peak near expected offset -------------
f, P = sg.welch(x, FS, nperseg=65536, return_onesided=False)
f = np.fft.fftshift(f); P = np.fft.fftshift(P); PdB = 10*np.log10(P+1e-20)
band = (f > OFFSET-60e3) & (f < OFFSET+60e3)
pk = np.argmax(PdB[band]); pk_f = f[band][pk]
floor = np.median(PdB)
print(f"\nnoise floor (median PSD): {floor:.1f} dB")
print(f"peak near +200k offset  : {pk_f:+.0f} Hz  ({PdB[band][pk]-floor:+.1f} dB over floor)")
print(f"  -> absolute freq      : {(771_881_250+pk_f)/1e6:.5f} MHz  (nominal 772.08125)")
print(f"  -> freq error         : {771_881_250+pk_f-772_081_250:+.0f} Hz")

# --- 2. shift to baseband, decimate to 50 kHz, measure occupied BW ------
n = np.arange(len(x))
bb = x * np.exp(-2j*np.pi*pk_f*n/FS)
dec = 40                                  # 2 MHz -> 50 kHz
bb = sg.decimate(bb, 8, ftype='fir', zero_phase=True)
bb = sg.decimate(bb, 5, ftype='fir', zero_phase=True)
fs2 = FS/dec
f2, P2 = sg.welch(bb, fs2, nperseg=4096, return_onesided=False)
f2 = np.fft.fftshift(f2); P2dB = 10*np.log10(np.fft.fftshift(P2)+1e-20)
pk2 = P2dB.max(); fl2 = np.median(P2dB)
occ = f2[P2dB > pk2-20]
print(f"\nbaseband SNR (in {fs2/1e3:.0f} kHz): {pk2-fl2:+.1f} dB")
if len(occ): print(f"occupied BW (-20 dB)   : {occ.min():+.0f} .. {occ.max():+.0f} Hz  = {occ.max()-occ.min():.0f} Hz")
print(f"  (P25 expects ~12500 Hz)")

# --- 3. symbol rate: FM-demod then look for 4800 baud line -------------
ph = np.angle(bb[1:]*np.conj(bb[:-1]))     # instantaneous freq
ph -= ph.mean()
# nonlinearity exposes symbol-clock line
sq = np.abs(ph)
S = np.abs(np.fft.rfft(sq*np.hanning(len(sq)), n=1<<18))
fr = np.fft.rfftfreq(1<<18, 1/fs2)
sel = (fr > 2000) & (fr < 12000)
top = np.argsort(S[sel])[-5:][::-1]
print(f"\nsymbol-clock candidates (2-12 kHz), P25 = 4800 baud:")
for i in top:
    print(f"   {fr[sel][i]:8.1f} Hz   rel {20*np.log10(S[sel][i]/np.median(S[sel])):5.1f} dB")
