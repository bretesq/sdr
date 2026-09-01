#!/usr/bin/env python3
"""Extract known-plaintext-attack pairs from an op25 -v 10 log.

Split out of extract_enc_pair.py so it can be imported and tested (the CLI
executes at module load; test_static.py pins that pattern).

The whole point is to bind each ciphertext codeword to the MI that ACTUALLY
keyed it, matching op25's decrypt sequencing in p25p1_fdma.cc rather than the
naive "most recent ESS" the first version used. In op25:

  process_HDU:   ess_mi = <HDU MI>                          (seeds the call)
  process_LDU1:  process_voice(FT_LDU1)  # keys with ess_mi (unchanged)
  process_LDU2:  print ESS mi=<next_mi>
                 process_voice(FT_LDU2)  # keys with the OLD ess_mi
                 ess_mi = next_mi        # only NOW does it advance

So the MI printed inside an LDU2 applies to the *next* superframe; the codewords
beneath it were keyed by the MI announced one superframe earlier (HDU for the
first). This module mirrors that exactly, per receiver ("[N]" tag), and:

  * records frame type (LDU1/LDU2) and codeword index 0..8 -- both are needed to
    pick the adp_brute keystream offset (LDU1 base 267, LDU2 base 368);
  * accepts only errs==0 codewords (a FEC miscorrection silently corrupts a
    ciphertext byte and a known-plaintext search then fails with no signal);
  * resets on a call terminator (TDU/TDU3/TDU15) and on any non-target ESS,
    rather than letting a stale MI bleed into the next call.

Conservative by design: when an LDU2's ESS fails to decode (op25 would advance
the MI via cycle_p25_mi), the chain is marked unknown and later codewords are
dropped instead of guessed. A KPA needs only one correct pair, so never
emitting a wrong one matters more than emitting every possible one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Strip the terminal control sequences `script`/op25 leave in the log.
ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]')

# Every op25 log segment is prefixed by log_ts::get(id) -> "<ts> [id]".
RXID = re.compile(r'\[(\d+)\]')

_MI = r'(?:[0-9a-f]{2}\s+){8}[0-9a-f]{2}'   # 9 MI bytes
_CW = r'(?:[0-9a-f]{2}\s+){10}[0-9a-f]{2}'  # 11-byte IMBE codeword

# One alternation over the four event kinds. HDU ESS (has tgid/mfid) is listed
# before the LDU2 ESS so the richer line matches its own branch. Terminators
# (TDU15/TDU3/TDU) are longest-first so "TDU15" is not shortened to "TDU".
TOKEN = re.compile(
    r'(?P<hdr>NAC\s+0x[0-9a-f]+\s+(?P<hdrtype>HDU|LDU1|LDU2|TDU15|TDU3|TDU):)'
    r'|(?P<esshdu>ESS:\s*tgid=\d+,\s*mfid=[0-9a-f]+,\s*algid=(?P<ha>[0-9a-f]+),'
    r'\s*keyid=(?P<hk>[0-9a-f]+),\s*mi=(?P<hmi>' + _MI + r'))'
    r'|(?P<essldu2>ESS:\s*algid=(?P<la>[0-9a-f]+),\s*keyid=(?P<lk>[0-9a-f]+),'
    r'\s*mi=(?P<lmi>' + _MI + r'))'
    r'|(?P<ct>IMBE \(CIPHERTXT\)\s+(?P<ctb>' + _CW + r')\s+errs\s+(?P<cte>\d+))',
    re.IGNORECASE,
)


@dataclass
class Pair:
    rx_id: int          # op25 receiver id ([N]); -1 when the log carries none
    frame: str          # 'LDU1' or 'LDU2'
    position: int       # codeword index 0..8 within the frame
    mi: list            # 9 MI bytes (2-char lowercase hex) that keyed this codeword
    ct: list            # 11 ciphertext bytes (2-char lowercase hex)
    algid: int          # encryption algorithm id (target only, e.g. 0xAA)
    keyid: int          # key id (target only, e.g. 0x8)


class _RxState:
    """Per-receiver mirror of op25's ess_mi chaining."""
    __slots__ = ('key_mi', 'pending', 'frame', 'position')

    def __init__(self):
        self.key_mi = None      # MI applicable to the current frame's codewords
        self.pending = None     # LDU2's announced next MI (list), or 'BREAK'
        self.frame = None       # current frame type
        self.position = 0       # next codeword index within the frame

    def commit(self):
        """Close the current frame, advancing the MI the way op25 does after
        process_voice: an LDU2 replaces key_mi with its announced MI; a missed
        or non-target ESS breaks the chain."""
        if self.frame == 'LDU2':
            self.key_mi = self.pending if isinstance(self.pending, list) else None
            self.pending = None


def _bytes(field: str) -> list:
    return field.lower().split()


def extract_pairs(log_text: str, *, algid: int = 0xAA, keyid: int = 8) -> list:
    """Return the (MI, ciphertext) pairs for `algid`/`keyid` in an op25 log."""
    text = ANSI.sub('', log_text)
    states: dict[int, _RxState] = {}
    pairs: list = []

    for line in text.splitlines():
        ids = [(m.start(), int(m.group(1))) for m in RXID.finditer(line)]

        def rx_for(pos: int) -> int:
            # The receiver that owns a token is the nearest [N] at or before it
            # (op25 concatenates a frame header and its IMBE line, each carrying
            # its own [N]); fall back to the line's first id, else -1.
            chosen = -1
            for start, rid in ids:
                if start <= pos:
                    chosen = rid
                else:
                    break
            if chosen == -1 and ids:
                chosen = ids[0][1]
            return chosen

        for m in TOKEN.finditer(line):
            rx = rx_for(m.start())
            st = states.setdefault(rx, _RxState())

            if m.group('hdr'):
                st.commit()
                htype = m.group('hdrtype')
                if htype in ('TDU', 'TDU3', 'TDU15'):
                    st.key_mi = None
                    st.pending = None
                    st.frame = None
                else:
                    st.frame = htype       # HDU / LDU1 / LDU2
                    st.pending = None
                    st.position = 0

            elif m.group('esshdu'):
                # HDU MI seeds the call; it applies immediately to the frames
                # that follow (there are no codewords in the HDU itself).
                if int(m.group('ha'), 16) == algid and int(m.group('hk'), 16) == keyid:
                    st.key_mi = _bytes(m.group('hmi'))
                else:
                    st.key_mi = None

            elif m.group('essldu2'):
                # Announced MI applies to the NEXT frame, so stage it as pending
                # and leave key_mi (this frame's key) untouched.
                if int(m.group('la'), 16) == algid and int(m.group('lk'), 16) == keyid:
                    st.pending = _bytes(m.group('lmi'))
                else:
                    st.pending = 'BREAK'

            elif m.group('ct'):
                if st.frame in ('LDU1', 'LDU2'):
                    if st.key_mi is not None and int(m.group('cte')) == 0:
                        pairs.append(Pair(rx, st.frame, st.position,
                                          st.key_mi, _bytes(m.group('ctb')),
                                          algid, keyid))
                    st.position += 1

    return pairs
