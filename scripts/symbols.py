import numpy as np
from scipy import signal as sg
FS=2_000_000; CENTER=771_881_250; TGT=772_257_350
raw=np.fromfile('captures/lwin_new_771881k_s2M.cs8',dtype=np.int8,count=2*FS*18)
x=raw[0::2].astype(np.float32)+1j*raw[1::2].astype(np.float32)
n=np.arange(len(x)); bb=x*np.exp(-2j*np.pi*(TGT-CENTER)*n/FS)
# 2 MHz -> 48 kHz = 10 samples/symbol at 4800 baud
y=sg.resample_poly(bb,12,500)          # 2e6*12/500 = 48000
fs=48000.0; SPS=10
y=sg.lfilter(sg.firwin(201,6000,fs=fs),1,y)
d=np.angle(y[1:]*np.conj(y[:-1]))*fs/(2*np.pi)
d-=np.median(d)
scale=np.percentile(np.abs(d),95)
d/=scale                                # roughly +/-1.4 for outer symbols
print(f"demod samples={len(d):,}  scale(95pct)={scale:.0f} Hz")

# --- symbol timing: pick phase maximizing 4-level clustering ---
best=None
for ph in range(SPS):
    s=d[ph::SPS]
    # quality = how tightly samples cluster near 4 levels
    lv=np.array([-1.5,-0.5,0.5,1.5])*(np.percentile(np.abs(s),90)/1.5)
    err=np.min(np.abs(s[:,None]-lv[None,:]),axis=1)
    q=1.0/(np.mean(err**2)+1e-9)
    if best is None or q>best[1]: best=(ph,q,s)
ph,q,sym=best
print(f"best symbol phase={ph}  clustering quality={q:.2f}  symbols={len(sym):,}")
pk=np.percentile(np.abs(sym),90)
h,e=np.histogram(sym,bins=40,range=(-2.2,2.2)); mx=h.max()
print("symbol-sampled histogram (4-FSK -> FOUR clean peaks):")
for i in range(40):
    print(f"  {(e[i]+e[i+1])/2:+5.2f} |{'#'*int(44*h[i]/mx)}")

# --- correlate against P25 frame sync ---
# P25 FS = 0x5575F5FF77FF ; dibit->level 01:+3 00:+1 10:-1 11:-3
bits=bin(int('5575F5FF77FF',16))[2:].zfill(48)
m={'01':3,'00':1,'10':-1,'11':-3}
fsym=np.array([m[bits[i:i+2]] for i in range(0,48,2)],dtype=float)
fsym/=3.0
c=np.correlate(sym/ (np.std(sym)+1e-9), fsym/np.linalg.norm(fsym), mode='valid')
c=np.abs(c)/np.sqrt(len(fsym))
thr=np.percentile(c,99.99)
hits=np.where(c>max(thr,0.55))[0]
print(f"\nP25 frame-sync correlation: max={c.max():.3f} mean={c.mean():.3f} (1.0=perfect)")
print(f"  hits above 0.55: {len(hits)}")
if len(hits)>2:
    gaps=np.diff(hits); g=gaps[(gaps>100)&(gaps<2000)]
    if len(g): print(f"  median gap between syncs: {np.median(g):.0f} symbols (P25 frame = 864 symbols/180ms)")
