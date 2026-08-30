"""Scan 768-776 MHz for P25: find real carriers, then test each for a 4800 baud clock."""
import numpy as np
from scipy import signal as sg
FS=8_000_000; CENTER=772_000_000
raw=np.fromfile('captures/psband_772M_s8M.cs8',dtype=np.int8,count=2*FS*9)
x=raw[0::2].astype(np.float32)+1j*raw[1::2].astype(np.float32)
print(f"{len(x)/FS:.1f} s  clip={100*np.mean(np.abs(raw)>=127):.3f}%")

f,P=sg.welch(x,FS,nperseg=32768,return_onesided=False)
f=np.fft.fftshift(f);PdB=10*np.log10(np.fft.fftshift(P)+1e-20);fl=np.median(PdB)
idx=np.where(PdB>fl+6)[0];groups=[]
for i in idx:
    if groups and i-groups[-1][-1]<=4: groups[-1].append(i)
    else: groups.append([i])
cands=[]
for g in groups:
    bw=f[g[-1]]-f[g[0]]
    if len(g)>=6 and 4e3<bw<30e3:            # narrowband channel-like
        pk=g[int(np.argmax(PdB[g]))]
        cands.append((CENTER+f[pk], PdB[pk]-fl, bw))
cands.sort(key=lambda t:-t[1])
print(f"\nnarrowband carriers found: {len(cands)}  (floor {fl:.1f} dB)")
print(f"{'MHz':>12}{'SNR':>7}{'BW kHz':>8}{'4800 clk dB':>13}{'clk Hz':>9}  verdict")
print("-"*66)
n=np.arange(len(x))
for fq,snr,bw in cands[:14]:
    bb=x*np.exp(-2j*np.pi*(fq-CENTER)*n/FS)
    y=sg.resample_poly(bb,3,500)                    # 8e6*3/500 = 48000
    fs2=48000.0
    y=sg.lfilter(sg.firwin(201,6000,fs=fs2),1,y)
    a=np.abs(y); gate=a>np.percentile(a,55)
    d=np.angle(y[1:]*np.conj(y[:-1]))
    d=np.where(gate[1:],d,0.0); d-=d.mean()
    s=np.abs(d)
    S=np.abs(np.fft.rfft((s-s.mean())*np.hanning(len(s)),n=1<<18))
    fr=np.fft.rfftfreq(1<<18,1/fs2); sel=(fr>3000)&(fr<9000)
    j=int(np.argmax(S[sel])); clk=fr[sel][j]
    rel=20*np.log10(S[sel][j]/np.median(S[sel]))
    v="P25/4-FSK (4800)" if abs(clk-4800)<60 and rel>12 else ("DMR/other" if rel>12 else "no symbol clock")
    print(f"{fq/1e6:12.5f}{snr:7.1f}{bw/1e3:8.1f}{rel:13.1f}{clk:9.0f}  {v}")
