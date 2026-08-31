#!/usr/bin/env python3
"""Extract a known-plaintext/ciphertext pair from an op25 -v 10 log.

Finds the LDU2 ESS (cleartext MI, ALGID, KID) and the IMBE CIPHERTXT
(11-byte LDU2 codeword) for the same frame, and writes them out so
adp_brute can recover the 5-byte ADP key.
"""
import re, sys, os

R = '/home/besquivel/rtl'
log = sys.argv[1] if len(sys.argv) > 1 else f'{R}/results/lwin_enc_capture.log'
out = f'{R}/results/enc_pair.txt'

raw = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]', '', open(log, errors='ignore').read())

# ESS line: "ESS: algid=aa, keyid=8, mi=xx xx xx xx xx xx xx xx xx, rs_errs=0"
ess_re = re.compile(
    r'ESS:\s*algid=([0-9a-fA-F]+),\s*keyid=([0-9a-fA-F]+),\s*mi=((?:[0-9a-fA-F]{2}\s*){9}),\s*rs_errs=(-?\d+)')
# CIPHERTXT line: "IMBE (CIPHERTXT) xx xx ... xx errs N" (11 hex bytes)
ct_re = re.compile(r'IMBE \(CIPHERTXT\) ((?:[0-9a-fA-F]{2}\s*){11})\s*errs\s*(\d+)')

pairs = []
# Walk the log; pair each CIPHERTXT with the most recent ESS for the same call.
last_ess = None
for line in raw.splitlines():
    m = ess_re.search(line)
    if m:
        alg = int(m.group(1), 16); kid = int(m.group(2), 16)
        mi = m.group(3).split()
        if len(mi) == 9 and alg == 0xAA and kid == 8:
            last_ess = (alg, kid, mi)
            continue
    m = ct_re.search(line)
    if m and last_ess is not None:
        ct = m.group(1).split()
        if len(ct) == 11 and int(m.group(2)) <= 2:  # accept only low-error codewords
            alg, kid, mi = last_ess
            pairs.append({'algid': f'0x{alg:02X}', 'keyid': f'0x{kid:X}',
                          'mi': mi, 'ct': ct})

with open(out, 'w') as f:
    f.write(f"# LWIN EBR Sheriff ADP/RC4 (algid 0xAA, keyid 0x8) - {len(pairs)} encrypted LDU2 pair(s)\n")
    for p in pairs:
        f.write(f"ESS  mi={ ' '.join(p['mi']) }\n")
        f.write(f"CT   { ' '.join(p['ct']) }\n\n")

# Known-plaintext options. A silence IMBE frame is the most reliable known plaintext:
# its 11-byte codeword is a fixed constant (see p25p1_fdma.cc, the d_behavior==-1 block).
SIL_PT = ['04','0c','fd','7b','fb','7d','f2','7b','3d','9e','44']

print(f"extracted {len(pairs)} MI + ciphertext pair(s) -> {out}")
if pairs:
    p = pairs[-1]
    mi = ' '.join(p['mi'])
    ct = ' '.join(p['ct'])
    pt = ' '.join(SIL_PT)
    print(f"most recent: MI={mi}\nciphertext: {ct}\n"
          f"silence plaintext: {pt}")
    print(f"\nTry the silence known-plaintext first:\n"
          f"  /home/besquivel/rtl/adp_brute \"{mi}\" \"{ct}\" \"{pt}\" $(nproc)")
    print(f"If that fails, supply a real known phrase as the 11-byte IMBE plaintext.")
