#!/usr/bin/env python3
"""Match a low-energy (silence) audio segment to its LDU2 CIPHERTXT frame by timestamp.

The capture run records audio (via -w + udp_audio_record.py) AND logs LDU2 CIPHERTXT +
ESS lines at -v 10. A silence/gap frame's audio is low-energy; we find the low-energy
audio segment, match it to the CIPHERTXT line at the same instant, and emit the
(9-byte MI, 11-byte ciphertext, fixed IMBE silence codeword) triple for adp_brute.
"""
import sys, os, re, struct, wave

R = '/home/besquivel/rtl'
LOG = f'{R}/results/lwin_enc_capture.log'
REC_DIR = f'{R}/recordings'
OUT = f'{R}/results/silence_pair2.txt'

IMBE_SIL = [0x04, 0x0C, 0xFD, 0x7B, 0xFB, 0x7D, 0xF2, 0x7B, 0x3D, 0x9E, 0x44]

def rms(samples):
    n = len(samples)
    if n == 0: return 0.0
    return (sum(s * s for s in samples) / n) ** 0.5

# Parse the op25 log for LDU2 CIPHERTXT lines with their timestamps (08/30/26 18:11:41.011056).
raw = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]', '', open(LOG, errors='ignore').read())
ts_re = re.compile(r'(\d\d/\d\d/\d\d\d\d \d\d:\d\d:\d\d\.\d+)')
ct_re = re.compile(r'IMBE \(CIPHERTXT\) ((?:[0-9a-fA-F]{2}\s*){11})\s*errs\s*(\d+)')
ess_re = re.compile(r'ESS:\s*algid=aa,\s*keyid=8,\s*mi=((?:[0-9a-fA-F]{2}\s*){9}),\s*rs_errs=(-?\d+)')

# Build a time-indexed list of (timestamp, ciphertext) for CIPHERTXT lines, and the most
# recent ADP ESS MI that preceded each CIPHERTXT.
ct_events = []   # (ts_str, mi(9 bytes), ct(11 bytes))
last_mi = None
for line in raw.splitlines():
    tm = ts_re.search(line)
    ts = tm.group(1) if tm else None
    em = ess_re.search(line)
    if em:
        mi = [int(x, 16) for x in em.group(1).split()]
        if len(mi) == 9:
            last_mi = mi
    cm = ct_re.search(line)
    if cm and last_mi is not None:
        ct = [int(x, 16) for x in cm.group(1).split()]
        if len(ct) == 11 and int(cm.group(2)) <= 2:
            ct_events.append((ts, last_mi, ct))

# Parse recorded WAV files: find low-RMS (silence) segments.
def parse_ts(ts_str):
    # "08/30/26 18:11:41.011056" -> seconds within the capture window
    try:
        h, m, sfrac = ts_str.split(' ')[1].split(':')
        s, ms = sfrac.split('.')
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1e6
    except Exception:
        return None

# Scan WAVs for low-RMS windows and report which correspond to silence frames.
import glob, collections
low_rms_events = []
for wpath in sorted(glob.glob(f'{REC_DIR}/TG*.wav')):
    try:
        with wave.open(wpath, 'rb') as w:
            sr = w.getframerate()
            n = w.getnframes()
            raw_bytes = w.readframes(n)
        samples = struct.unpack('<' + 'h' * n, raw_bytes)
        # 200 ms windows
        win = int(sr * 0.2)
        for start in range(0, max(1, n - win), win):
            seg = samples[start:start + win]
            r = rms(seg)
            if r < 300:  # low-energy threshold (S16LE, ~8 kHz)
                low_rms_events.append((wpath, start / sr, r))
    except Exception:
        continue

# Match: for each CIPHERTXT event, check if there is a low-RMS window at the same instant.
print(f"CIPHERTXT events: {len(ct_events)}")
print(f"Low-RMS windows found: {len(low_rms_events)}")

# Find the CIPHERTXT event whose timestamp coincides with a low-RMS window => silence frame.
silence_pair = None
for ts, mi, ct in ct_events:
    t = parse_ts(ts)
    if t is None: continue
    for wpath, t2, r in low_rms_events:
        if abs(t - t2) < 0.3:  # within 300 ms
            silence_pair = (mi, ct)
            break
    if silence_pair: break

with open(OUT, 'w') as f:
    if silence_pair:
        mi, ct = silence_pair
        f.write(f"# LWIN EBR Sheriff ADP/RC4 - verified silence frame (low-RMS audio match)\n")
        f.write(f"MI   {' '.join('%02x' % b for b in mi)}\n")
        f.write(f"CT   {' '.join('%02x' % b for b in ct)}\n")
        f.write(f"PT   {' '.join('%02x' % b for b in IMBE_SIL)}\n")
        print(f"\nWrote {OUT}")
        mi_s = ' '.join('%02x' % b for b in mi)
        ct_s = ' '.join('%02x' % b for b in ct)
        pt_s = ' '.join('%02x' % b for b in IMBE_SIL)
        print(f"\nRun: /home/besquivel/rtl/adp_brute \"{mi_s}\" \"{ct_s}\" \"{pt_s}\" 384   (on wopr)")
    else:
        f.write("# No low-RMS audio window matched a CIPHERTXT event in this capture window.\n")
        f.write(f"# CIPHERTXT events: {len(ct_events)}; low-RMS windows: {len(low_rms_events)}\n")
        print(f"\nNo verified silence frame matched in this window; use the repeated-ciphertext heuristic (adp_silence_scan.py) or recapture during a gap.")
