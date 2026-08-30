#!/usr/bin/env python3
"""Definitive P25 test on the actual control channel at +199.5 kHz offset.
P25 C4FM: 4800 baud, 4-level FSK with deviations at -1800,-600,+600,+1800 Hz."""
import numpy as np
from scipy import signal as sg
FS=2_000_000; CENTER=771_881_250; TARGET=772_080_710
off=TARGET-CENTER
raw=np.fromfile('captures/lwin_cc_771881k_s2M.cs8',dtype=np.int8,count=2*FS*18)
x=raw[0::2].astype(np.float32)+1j*raw[1::2].astype(np.float32)
n=np.arange(len(x)); bb=x*np.exp(-2j*np.pi*off*n/FS)
# 2 MHz -> 48 kHz, tight 12.5 kHz channel filter
y=sg.decimate(sg.decimate(bb,10,ftype='fir',zero_phase=True),5,ftype='fir',zero_phase=True)
fs2=FS/50
b=sg.firwin(301,6250,fs=fs2); y=sg.lfilter(b,1,y)
p=np.abs(y)**2; print(f"fs={fs2:.0f} Hz  channel-filtered to +/-6.25 kHz")
f2,P2=sg.welch(y,fs2,nperseg=4096,return_onesided=False)
f2=np.fft.fftshift(f2);P2dB=10*np.log10(np.fft.fftshift(P2)+1e-20)
fl=np.median(P2dB);print(f"in-channel SNR: {P2dB.max()-fl:.1f} dB")

ph=np.angle(y[1:]*np.conj(y[:-1]))*fs2/(2*np.pi)   # instantaneous freq in Hz
ph=ph[np.abs(y[1:])>np.percentile(np.abs(y),40)]   # gate on signal present
print(f"inst-freq samples (gated): {len(ph):,}")
print(f"  mean={ph.mean():+.0f} Hz  std={ph.std():.0f} Hz")
print(f"  percentiles 5/25/50/75/95: "+" ".join(f"{np.percentile(ph,q):+.0f}" for q in (5,25,50,75,95)))
h,edges=np.histogram(ph,bins=80,range=(-4000,4000))
print("\ninst-freq histogram (C4FM expects 4 peaks near -1800/-600/+600/+1800):")
mx=h.max()
for i in range(0,80,2):
    c=(edges[i]+edges[i+2])/2; v=h[i]+h[i+1]
    print(f"  {c:+6.0f} Hz |{'#'*int(50*v/mx)}")
# symbol clock
for nm,s in [("|f|",np.abs(ph-ph.mean())),("f^2",(ph-ph.mean())**2)]:
    S=np.abs(np.fft.rfft(s*np.hanning(len(s)),n=1<<19));fr=np.fft.rfftfreq(1<<19,1/fs2)
    sel=(fr>2000)&(fr<12000);j=np.argmax(S[sel])
    print(f"{nm} clock: {fr[sel][j]:.1f} Hz ({20*np.log10(S[sel][j]/np.median(S[sel])):.1f} dB)  [P25=4800]")
