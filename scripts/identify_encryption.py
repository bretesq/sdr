#!/usr/bin/env python3
"""Identify P25 encryption algorithms in use from cleartext ESS metadata.

The ESS (Encryption Sync Sequence) in the LDU2 voice frame carries ALGID, KID and the
message indicator IN THE CLEAR. Reading it identifies which cipher a talkgroup uses.
This performs NO decryption and recovers no key material.

Run op25 following the talkgroups of interest with -V -v 10, then point this at the log.
"""
import re, json, sys, collections
R='/home/besquivel/rtl'
log = sys.argv[1] if len(sys.argv)>1 else f'{R}/results/enc_id.log'
db  = json.load(open(f'{R}/reference/lwin_talkgroups.json'))
ALG={0x80:'CLEAR (unencrypted)',0x81:'DES-OFB',0x83:'Triple DES (3DES)',0x84:'AES-256',
     0x85:'AES-128',0xaa:'ADP / RC4 (Motorola, 40-bit)',0x9f:'CLEAR (alt)'}
raw=re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]','',open(log,errors='ignore').read())
tok=re.compile(r'(?:voice update:\s+tg\((\d+)\)|hold active tg\((\d+)\)|set tgid=(\d+)'
               r'|ESS: (?:tgid=(\d+), mfid=[0-9a-f]+, )?algid=([0-9a-f]+), keyid=([0-9a-f]+))')
cur=None; per=collections.defaultdict(collections.Counter); tot=collections.Counter()
for m in tok.finditer(raw):
    a,b,c,esstg,alg,kid=m.groups()
    if a or b or c: cur=int(a or b or c); continue
    tg=int(esstg) if esstg else cur
    k=(int(alg,16),int(kid,16)); tot[k]+=1
    if tg: per[tg][k]+=1
print("ALGID / KEYID observed (cleartext ESS metadata; no decryption performed)")
for (alg,kid),n in tot.most_common():
    print(f"  algid=0x{alg:02X} keyid=0x{kid:X}  x{n:<5} {ALG.get(alg,'UNKNOWN / likely bit error')}")
print(f"\n{'TG':>7} {'RRflag':<9}{'alpha':<18}observed")
for tg,cnt in sorted(per.items(),key=lambda kv:-sum(kv[1].values())):
    e=db.get(str(tg),{})
    print(f"{tg:>7} {e.get('enc','?'):<9}{(e.get('alpha') or '?')[:18]:<18}"
          + "  ".join(f"0x{a:02X}/k{k:X}:{n}" for (a,k),n in cnt.most_common()))
json.dump({'totals':{f"0x{a:02X}/0x{k:X}":n for (a,k),n in tot.items()},
           'per_talkgroup':{str(t):{f"0x{a:02X}/0x{k:X}":n for (a,k),n in c.items()} for t,c in per.items()}},
          open(f'{R}/results/encryption_survey.json','w'),indent=1)
print("\nsaved -> results/encryption_survey.json")
