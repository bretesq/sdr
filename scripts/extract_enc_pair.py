#!/usr/bin/env python3
"""Extract known-plaintext/ciphertext pairs from an op25 -v 10 log.

Thin CLI over enc_pair.extract_pairs (the pairing logic lives there so it can be
imported and unit-tested; this file runs at import and cannot be).

Each pair binds an 11-byte ciphertext codeword to the MI that ACTUALLY keyed it,
per op25's decrypt sequencing -- the MI announced inside an LDU2 keys the *next*
superframe, not the codewords beneath it. See enc_pair.py for the full rationale.

ONE FILE PER KEY ID
-------------------
This used to target algid 0xAA / keyid 0x8 only, and wrote a single
results/enc_pair.txt. Five ADP key ids are actually in use -- 0x22 (63 calls),
0x8 (21), 0x2F08 (5), 0x1 (4), 0x2EF4 (1) -- so the other four were silently
dropped. Worse, a pooled file would be actively harmful: each KID is a different
key, so a brute-force run over mixed pairs searches for a key that does not
exist. Groups are discovered from the log and written separately.

ORDINARY LOGS WORK
------------------
Any op25 log at -v 9 or higher carries CIPHERTXT and ESS, and
lwin_listen_multi.sh already runs -v 10 for the grant census -- results/
op25_multi.log holds 3,249 CIPHERTXT lines from a normal listening session. That
matters because encrypted traffic is largely absent from the RECORDINGS: op25 -n
silences it, so TG19014 shows 90 ADP headers against 3 recorded calls. The
ciphertext exists only in the logs.

Usage:
  extract_enc_pair.py [LOG]        default: results/lwin_enc_capture.log
"""
import os
import sys
from collections import Counter

sys.path.insert(0, __file__.rsplit('/', 1)[0])
import enc_pair  # noqa: E402
import enc_harvest  # noqa: E402

R = os.environ.get('SDR_ROOT', '/home/besquivel/rtl')
log = sys.argv[1] if len(sys.argv) > 1 else f'{R}/results/lwin_enc_capture.log'

text = open(log, errors='ignore').read()


def brute_flags(p):
    """adp_brute keystream selector for this codeword: the frame sets the base
    (LDU1=0, LDU2=101) and the index sets the stride, so BOTH must be passed --
    LDU1 index 3 is offset 300, not 267."""
    return f"--frame {p.frame.lower()} --position {p.position}"


# A silence IMBE frame is the most reliable known plaintext: its 11-byte
# codeword is a fixed constant the imbe_vocoder emits for a zero-WAV. But we
# CANNOT tell from the log which codewords are silence (i=0 frames in this
# capture carry voice), so this is only usable against a codeword you have
# independent reason to believe is silent -- it is not a default.
SIL_PT = ['01', '50', '20', '00', '00', '00', '00', '00', '00', '00', '00']

groups = enc_harvest.enc_pair_keys(text)
if not groups:
    print(f"no encrypted key groups with clean ESS in {log}")
    sys.exit(0)

print(f"{len(groups)} key group(s) in {os.path.basename(log)}")
last = None
for algid, keyid in groups:
    pairs = enc_pair.extract_pairs(text, algid=algid, keyid=keyid)
    out = f'{R}/results/enc_pair_0x{algid:02X}_0x{keyid:X}.txt'
    with open(out, 'w') as f:
        f.write(f"# LWIN ADP/RC4 algid 0x{algid:02X}, keyid 0x{keyid:X} - "
                f"{len(pairs)} encrypted codeword pair(s)\n")
        f.write("# Each block: the MI that keyed this codeword (op25 chaining,\n"
                "# not the co-located ESS), the frame type, the codeword index,\n"
                "# and the --frame/--position flags to pass adp_brute.\n"
                "# One file per key id: each KID is a DIFFERENT key, so pairs\n"
                "# must never be pooled across them.\n\n")
        for p in pairs:
            f.write(f"rx={p.rx_id} frame={p.frame} index={p.position} "
                    f"{brute_flags(p)}\n")
            f.write(f"MI  {' '.join(p.mi)}\n")
            f.write(f"CT  {' '.join(p.ct)}\n\n")
    print(f"  algid=0x{algid:02X} keyid=0x{keyid:X}: {len(pairs)} pair(s) -> "
          f"{os.path.basename(out)}")
    if pairs:
        print(f"    by frame: {dict(Counter(p.frame for p in pairs))}"
              f"  by receiver: {dict(Counter(p.rx_id for p in pairs))}")
        last = (algid, keyid, pairs[-1])

if last is None:
    print("\nkey groups were seen in the ESS, but no clean codeword paired with "
          "one. Nothing to brute-force from this log.")
    sys.exit(0)

# Show a concrete command for one real pair. The plaintext MUST be the real
# plaintext of THIS exact codeword (same rx/frame/index); the silence constant
# only works if this codeword is genuinely silent.
algid, keyid, p = last
mi = ' '.join(p.mi)
ct = ' '.join(p.ct)
pt = ' '.join(SIL_PT)
print(f"\nmost recent pair (algid=0x{algid:02X} keyid=0x{keyid:X}): "
      f"rx={p.rx_id} {p.frame} index={p.position}"
      f"\n  MI={mi}\n  CT={ct}")
print(f"\nadp_brute_cuda command (GPU) for this codeword:\n"
      f"  {R}/adp_brute_cuda \"{mi}\" \"{ct}\" \"<PT for this codeword>\" "
      f"512 {brute_flags(p)}")
print(f"\nSupply the 11-byte IMBE plaintext for this exact codeword. If (and "
      f"only if) it is a silence frame, that plaintext is:\n  {pt}")
