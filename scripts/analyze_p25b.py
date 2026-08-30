#!/usr/bin/env python3
import numpy as np
from scipy import signal as sg
FS=2_000_000; CENTER=771_881_250
raw=np.fromfile('captures/lwin_cc_771881k_s2M.cs8',dtype=np.int8,count=2*FS*18)
x=raw[0::2].astype(np.float32)+1j*raw[1::2].astype(np.float32)

f,P=sg.welch(x,FS,nperseg=16384,return_onesided=False)
f=np.fft.fftshift(f); PdB=10*np.log10(np.fft.fftshift(P)+1e-20)
floor=np.median(PdB)
print(f"capture center {CENTER/1e6:.5f} MHz, span +/-1 MHz, floor={floor:.1f} dB\n")
print("ALL signals >6 dB over floor:")
print(f"{'abs MHz':>12} {'offset kHz':>11} {'SNR dB':>7}  {'BW@floor+6 (kHz)':>17}")
idx=np.where(PdB>floor+6)[0]
groups=[]
for i in idx:
    if groups and i-groups[-1][-1]<=3: groups[-1].append(i)
    else: groups.append([i])
for g in groups:
    if len(g)<2: continue
    pk=g[int(np.argmax(PdB[g]))]
    bw=(f[g[-1]]-f[g[0]])/1e3
    print(f"{(CENTER+f[pk])/1e6:12.5f} {f[pk]/1e3:11.1f} {PdB[pk]-floor:7.1f}  {bw:17.1f}")

# strongest signal -> detailed
pk=idx[int(np.argmax(PdB[idx]))]; pkf=f[pk]
print(f"\n--- strongest: {(CENTER+pkf)/1e6:.5f} MHz ---")
n=np.arange(len(x)); bb=x*np.exp(-2j*np.pi*pkf*n/FS)
for d1,d2 in [(8,5)]:
    y=sg.decimate(sg.decimate(bb,d1,ftype='fir',zero_phase=True),d2,ftype='fir',zero_phase=True)
fs2=FS/(8*5)
f2,P2=sg.welch(y,fs2,nperseg=8192,return_onesided=False)
f2=np.fft.fftshift(f2); P2dB=10*np.log10(np.fft.fftshift(P2)+1e-20)
fl2=np.median(P2dB); pk2=P2dB.max()
print(f"SNR={pk2-fl2:.1f} dB")
for th,lbl in [(3,'floor+3dB'),(6,'floor+6dB'),(10,'floor+10dB')]:
    sel=f2[P2dB>fl2+th]
    if len(sel): print(f"  BW @{lbl:11s}: {sel.max()-sel.min():8.0f} Hz")
print("  (P25 narrowband = 12500 Hz; NFM voice = 12500; LTE = 1.4-20 MHz)")

# symbol clock via FM demod
ph=np.angle(y[1:]*np.conj(y[:-1])); ph-=ph.mean()
for name,sig in [("|freq|",np.abs(ph)),("freq^2",ph**2)]:
    S=np.abs(np.fft.rfft(sig*np.hanning(len(sig)),n=1<<19))
    fr=np.fft.rfftfreq(1<<19,1/fs2); sel=(fr>1500)&(fr<15000)
    j=np.argmax(S[sel])
    print(f"  {name}: strongest clock line {fr[sel][j]:7.1f} Hz  ({20*np.log10(S[sel][j]/np.median(S[sel])):.1f} dB)  [P25=4800]")
