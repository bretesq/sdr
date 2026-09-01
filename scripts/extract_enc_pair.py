#!/usr/bin/env python3
"""Extract known-plaintext/ciphertext pairs from an op25 -v 10 log.

Thin CLI over enc_pair.extract_pairs (the pairing logic lives there so it can be
imported and unit-tested; this file runs at import and cannot be). It writes the
pairs to results/enc_pair.txt and prints a ready-to-run adp_brute invocation.

Each pair binds an 11-byte ciphertext codeword to the MI that ACTUALLY keyed it,
per op25's decrypt sequencing -- the MI announced inside an LDU2 keys the *next*
superframe, not the codewords beneath it. See enc_pair.py for the full rationale.
"""
import sys
from collections import Counter

sys.path.insert(0, __file__.rsplit('/', 1)[0])
import enc_pair  # noqa: E402

R = '/home/besquivel/rtl'
log = sys.argv[1] if len(sys.argv) > 1 else f'{R}/results/lwin_enc_capture.log'
out = f'{R}/results/enc_pair.txt'

pairs = enc_pair.extract_pairs(open(log, errors='ignore').read())


def brute_flags(p):
    """adp_brute keystream selector for this codeword: the frame sets the base
    (LDU1=0, LDU2=101) and the index sets the stride, so BOTH must be passed --
    LDU1 index 3 is offset 300, not 267."""
    return f"--frame {p.frame.lower()} --position {p.position}"


with open(out, 'w') as f:
    f.write(f"# LWIN EBR Sheriff ADP/RC4 (algid 0xAA, keyid 0x8) - "
            f"{len(pairs)} encrypted codeword pair(s)\n")
    f.write("# Each block: the MI that keyed this codeword (op25 chaining, not\n"
            "# the co-located ESS), the frame type, the codeword index, and the\n"
            "# --frame/--position flags to pass adp_brute.\n\n")
    for p in pairs:
        f.write(f"rx={p.rx_id} frame={p.frame} index={p.position} "
                f"{brute_flags(p)}\n")
        f.write(f"MI  {' '.join(p.mi)}\n")
        f.write(f"CT  {' '.join(p.ct)}\n\n")

# A silence IMBE frame is the most reliable known plaintext: its 11-byte
# codeword is a fixed constant the imbe_vocoder emits for a zero-WAV. But we
# CANNOT tell from the log which codewords are silence (i=0 frames in this
# capture carry voice), so this is only usable against a codeword you have
# independent reason to believe is silent -- it is not a default.
SIL_PT = ['01', '50', '20', '00', '00', '00', '00', '00', '00', '00', '00']

print(f"extracted {len(pairs)} MI + ciphertext pair(s) -> {out}")
if not pairs:
    print("no ADP (algid 0xAA, keyid 0x8) codewords in this log.")
    sys.exit(0)

print(f"  by frame: {dict(Counter(p.frame for p in pairs))}")
print(f"  by receiver: {dict(Counter(p.rx_id for p in pairs))}")

# Show a concrete command for the most recent pair. The plaintext MUST be the
# real plaintext of THIS exact codeword (same rx/frame/index); the silence
# constant only works if this codeword is genuinely silent.
p = pairs[-1]
mi = ' '.join(p.mi)
ct = ' '.join(p.ct)
pt = ' '.join(SIL_PT)
print(f"\nmost recent: rx={p.rx_id} {p.frame} index={p.position}"
      f"\n  MI={mi}\n  CT={ct}")
print(f"\nadp_brute_cuda command (GPU) for this codeword:\n"
      f"  {R}/adp_brute_cuda \"{mi}\" \"{ct}\" \"<PT for this codeword>\" "
      f"512 {brute_flags(p)}")
print(f"\nSupply the 11-byte IMBE plaintext for this exact codeword. If (and "
      f"only if) it is a silence frame, that plaintext is:\n  {pt}")
