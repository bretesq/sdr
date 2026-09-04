#!/usr/bin/env python3
"""Pick a low-error (MI, CT) pair from a multi-radio ADP capture log.

The wopr full 2^40 sweep over a stale hand-curated pair found no key, which
points to a mismatched MI/CT. This tool re-picks the pair directly from the
FRESH multi-radio capture log (results/lwin_enc_capture_multi.log), taking the
most recent ADP (algid=aa) ESS MI and pairing it with the immediately following
low-error CIPHERTXT. Prints an adp_brute command ready to paste.

Usage: adp_pick_pair.py [logfile]
"""
import re, sys

R = '/home/besquivel/rtl'
LOG = sys.argv[1] if len(sys.argv) > 1 else f'{R}/results/lwin_enc_capture_multi.log'
SIL_PT = ' '.join(['01', '50', '20', '00', '00', '00', '00', '00', '00', '00', '00'])

raw = open(LOG, errors='ignore').read()
clean = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]', '', raw)

# ADP ESS (algid=aa) with 9-byte MI and keyid
ess_re = re.compile(
    r'ESS:\s*algid=aa,\s*keyid=([0-9a-fA-F]+),\s*mi=((?:[0-9a-fA-F]{2}\s*){9}),\s*rs_errs=(-?\d+)')
ct_re = re.compile(r'IMBE \(CIPHERTXT\) ((?:[0-9a-fA-F]{2}\s*){11})\s*errs\s*(\d+)')

pairs = []
last = None
for line in clean.splitlines():
    m = ess_re.search(line)
    if m:
        mi = m.group(2).split()
        if len(mi) == 9:
            last = mi
            continue
    m = ct_re.search(line)
    if m and last is not None:
        ct = m.group(1).split()
        if len(ct) == 11 and int(m.group(2)) <= 2:
            pairs.append((last, ct))

if not pairs:
    print('no ADP MI+CT pairs found in', LOG)
    sys.exit(1)

# Use the most recent pair (fresh capture).
mi, ct = pairs[-1]
mi_s = ' '.join(mi)
ct_s = ' '.join(ct)
cmd = f'/home/besquivel/rtl/adp_brute "{mi_s}" "{ct_s}" "{SIL_PT}" $(nproc)'
print(f'MI ({len(mi)} bytes): {mi_s}')
print(f'CT  ({len(ct)} bytes): {ct_s}')
print(f'PT (verified silence): {SIL_PT}')
print()
print('adp_brute command:')
print(f'  {cmd}')
print()
print(f'wopr: ssh wopr "cd ~/wopr_adp && ./adp_brute \"{mi_s}\" \"{ct_s}\" \"{SIL_PT}\" 256"')
