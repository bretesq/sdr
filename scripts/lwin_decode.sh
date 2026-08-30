#!/usr/bin/env bash
# LWIN P25 capture -> decode. Usage: lwin_decode.sh [seconds] [cc_hz]
set -e
SECS=${1:-40}; CC=${2:-773056250}
TUNE=772800000                       # offset-tuned: keeps CC off the DC spike
FS=2000000
R=/home/besquivel/rtl
RAW=$R/captures/lwin_live.cs8
CF=$R/captures/lwin_live.cfile
echo "[1/3] capturing ${SECS}s @ $((FS/1000000)) Msps, tuned $((TUNE/1000000)) MHz ..."
hackrf_transfer -r "$RAW" -f $TUNE -s $FS -l 40 -g 44 -a 0 -n $((FS*SECS)) >/dev/null 2>&1
echo "[2/3] shifting CC ($CC) to baseband, decimating to 1 Msps ..."
python3 - "$RAW" "$CF" $FS $TUNE $CC <<'PY'
import sys, numpy as np
from scipy import signal as sg
raw_p,out_p,FS,TUNE,CC = sys.argv[1],sys.argv[2],int(sys.argv[3]),int(sys.argv[4]),int(sys.argv[5])
r=np.fromfile(raw_p,dtype=np.int8)
x=r[0::2].astype(np.float32)+1j*r[1::2].astype(np.float32)
n=np.arange(len(x)); x*=np.exp(-2j*np.pi*(CC-TUNE)*n/FS)
y=sg.decimate(x,2,ftype='fir',zero_phase=False)
y/=(np.percentile(np.abs(y),99)+1e-9)          # normalize — op25 expects ~full scale
o=np.empty(2*len(y),dtype=np.float32); o[0::2]=y.real; o[1::2]=y.imag
o.tofile(out_p); print(f"      {len(y):,} samples @1 Msps, rms={np.sqrt(np.mean(np.abs(y)**2)):.3f}")
PY
echo "[3/3] decoding with op25 ..."
cd $R/src/op25/op25/gr-op25_repeater/apps
timeout $((SECS+40)) python3 rx.py -F "$CF" -S 1000000 -o 0 -q 0 -v 10 2>&1 \
  | sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g' -e 's/\x1b[()][A-Z0-9]//g' > $R/results/lwin_live.clean || true
echo "done -> results/lwin_live.clean"
