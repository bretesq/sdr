import numpy as np, sys
from scipy import signal as sg
FS=2_000_000; CENTER=771_881_250
raw=np.fromfile('captures/lwin_new_771881k_s2M.cs8',dtype=np.int8,count=2*FS*18)
x=raw[0::2].astype(np.float32)+1j*raw[1::2].astype(np.float32)
for TARGET in (772_080_710, 772_257_350):
    off=TARGET-CENTER; n=np.arange(len(x))
    bb=x*np.exp(-2j*np.pi*off*n/FS)
    y=sg.decimate(sg.decimate(bb,10,ftype='fir',zero_phase=True),5,ftype='fir',zero_phase=True)
    fs2=FS/50
    y=sg.lfilter(sg.firwin(301,6250,fs=fs2),1,y)
    amp=np.abs(y); gate=amp>np.percentile(amp,50)
    ph=np.angle(y[1:]*np.conj(y[:-1]))*fs2/(2*np.pi)
    ph=ph[gate[1:]]
    ph=ph-np.median(ph)                       # remove residual carrier offset
    print(f"\n===== {TARGET/1e6:.5f} MHz =====")
    print(f"  gated samples={len(ph):,}  std={ph.std():.0f} Hz")
    print(f"  deciles: "+" ".join(f"{np.percentile(ph,q):+.0f}" for q in range(10,100,10)))
    h,e=np.histogram(ph,bins=48,range=(-3600,3600)); mx=h.max()
    print("  inst-freq histogram (C4FM -> 4 modes at -1800/-600/+600/+1800):")
    for i in range(48):
        print(f"   {(e[i]+e[i+1])/2:+6.0f} |{'#'*int(46*h[i]/mx)}")
    for nm,s in [("|f|",np.abs(ph)),("f^2",ph**2)]:
        S=np.abs(np.fft.rfft((s-s.mean())*np.hanning(len(s)),n=1<<19))
        fr=np.fft.rfftfreq(1<<19,1/fs2); sel=(fr>2000)&(fr<12000)
        j=int(np.argmax(S[sel]))
        print(f"  {nm} clock: {fr[sel][j]:7.1f} Hz ({20*np.log10(S[sel][j]/np.median(S[sel])):.1f} dB) [P25=4800]")
