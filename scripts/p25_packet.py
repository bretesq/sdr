#!/usr/bin/env python3
"""Parse P25 packet-data PDUs out of op25's log, and say whether they are clear.

WHY THIS EXISTS
---------------
LWIN site 13 runs integrated voice and data. The control channel announces
data-capable channels (TSBK 0x16) and grants one to a specific radio (TSBK
0x14). Measured over 11 hours on 2026-09-04: 8,084 grants to 984 distinct
radios across 19 DIFFERENT channels, 78% of them on the 800 MHz leg.

That spread matters, and an earlier 35-minute sample got it badly wrong: in
that window all 362 grants named 769.68125, which looked like a fixed data
channel. It is not. LWIN allocates data channels dynamically out of the
ordinary traffic-channel pool, exactly as it allocates voice, and 769.68125
carries only about 4% of the traffic. Any receiver strategy that pins one
frequency sees almost none of it.

None of the payload was readable either, because op25 discarded it twice over
-- p25_framer.cc capped DUID 0x0c at 962 bits (header + 3 blocks) and
p25p1_fdma.cc dropped anything that was not SAP 61 trunking -- plus a third
way found later: process_blocks threw away every decoded block, header
included, as soon as one block failed, which is the normal outcome for a frame
correctly truncated mid-block at the end of a burst.

With those two limits lifted, the reassembled bytes arrive here.

WHAT THIS DELIBERATELY DOES *NOT* DO
------------------------------------
It does not decode LRRP position reports, ARS registrations or TMS text
messages. Those are the interesting payloads, and writing their parsers before
seeing a single real byte would be writing them against assumptions rather than
against the system -- the exact failure this project keeps recording (see
scripts/tests/test_parsers.py's docstring). Application-layer decoding is a
second pass, written against captured bytes.

What it does instead is answer the ONE question that gates all of that:

    IS THE PACKET DATA ENCRYPTED?

And it answers it without needing to understand a single application byte,
because IPv4 carries its own proof. The header checksum is a 16-bit one's
complement sum over the header, so on plaintext it validates and on ciphertext
it validates with probability 2^-16. Add the structural checks (version == 4,
sane IHL, total_length consistent with the payload) and a false "clear" verdict
is implausible. That makes `classify()` a self-validating test: we are not
asserting the data is readable, we are letting the packet prove it.

A negative is weaker than a positive and is reported as such -- `not-ipv4` may
mean encrypted, or merely that this PDU was not carrying IP.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import p25_apps

# ---------------------------------------------------------------------------
# The op25 log line this module reads.
#
# THE PATCH TO lib/p25p1_fdma.cc MUST EMIT EXACTLY THIS. It is stated here, in
# the consumer, because the producer is C++ in a vendored tree that gets
# re-cloned: patches/README.md exists precisely because those edits vanish.
# If the two ever disagree, this regex silently matches nothing and the answer
# looks like "no data on the system" -- the same false negative that started
# this work. scripts/tests/test_p25_packet.py pins the format.
#
#   09/03/26 21:09:18.336754 [0] NAC 0x1bd PDU: fmt=16 sap=04 blks=6 \
#       hdr=16 04 03 56 eb 86 00 00 00 00 00 00 : 45 00 00 38 ...
#
# fmt/sap are hex, blks decimal, and `hdr` is the WHOLE 12-byte header block.
#
# The header is emitted raw, rather than the C++ picking the radio id out of
# it, on purpose. Field offsets inside a P25 PDU header are the part of this
# work least supported by evidence -- op25 itself reads blks from octet 6,
# which does not agree with the layout the standard is usually quoted as
# having, and nobody here has seen a real header yet. Getting an offset wrong
# in C++ costs a rebuild and a capture outage; getting it wrong here costs an
# edit. So the C++ hands over bytes and this module does the interpreting.
# ---------------------------------------------------------------------------
PDU_LINE = re.compile(
    r'NAC 0x(?P<nac>[0-9a-f]+) PDU: '
    r'fmt=(?P<fmt>[0-9a-f]{2}) '
    r'sap=(?P<sap>[0-9a-f]{2}) '
    r'blks=(?P<blks>\d+) '
    r'hdr=(?P<hdr>[0-9a-f]{2}(?: [0-9a-f]{2}){11}) : '
    # Payload may be EMPTY: an SNDCP control PDU can be header-only, and
    # since process_blocks began keeping partially-decoded blocks a frame
    # whose only good block was the header lands here too. classify()
    # already reports that as its own 'empty' verdict rather than a
    # failure, so the line is worth capturing.
    r'(?P<payload>(?:[0-9a-f]{2}(?: [0-9a-f]{2})*)?)\s*$'
)

# ---------------------------------------------------------------------------
# The `PDU raw:` line, and the two trellis codes needed to read it.
#
#   09/04/26 09:41:02.1 [12] NAC 0x1bd PDU raw: bits=700 blocks=3 : 5575f5ff...
#
# Bits are packed MSB-first; blocks start at bit 112 (48 frame sync + 64 NID)
# every 196 bits. The C++ emits this and interprets none of it -- see the
# module docstring.
# ---------------------------------------------------------------------------
PDU_RAW_LINE = re.compile(
    r'NAC 0x(?P<nac>[0-9a-f]+) PDU raw: '
    r'bits=(?P<bits>\d+) blocks=(?P<blocks>\d+) : (?P<hex>[0-9a-f]+)\s*$'
)

# The 196-bit block interleaver, identical for both trellis rates.
# Copied from op25's block_deinterleave (p25p1_fdma.cc), which is itself from
# wireshark's packet-p25cai.c.
DEINTERLEAVE = (
      0,  1,  2,  3,  52, 53, 54, 55, 100,101,102,103, 148,149,150,151,
      4,  5,  6,  7,  56, 57, 58, 59, 104,105,106,107, 152,153,154,155,
      8,  9, 10, 11,  60, 61, 62, 63, 108,109,110,111, 156,157,158,159,
     12, 13, 14, 15,  64, 65, 66, 67, 112,113,114,115, 160,161,162,163,
     16, 17, 18, 19,  68, 69, 70, 71, 116,117,118,119, 164,165,166,167,
     20, 21, 22, 23,  72, 73, 74, 75, 120,121,122,123, 168,169,170,171,
     24, 25, 26, 27,  76, 77, 78, 79, 124,125,126,127, 172,173,174,175,
     28, 29, 30, 31,  80, 81, 82, 83, 128,129,130,131, 176,177,178,179,
     32, 33, 34, 35,  84, 85, 86, 87, 132,133,134,135, 180,181,182,183,
     36, 37, 38, 39,  88, 89, 90, 91, 136,137,138,139, 184,185,186,187,
     40, 41, 42, 43,  92, 93, 94, 95, 140,141,142,143, 188,189,190,191,
     44, 45, 46, 47,  96, 97, 98, 99, 144,145,146,147, 192,193,194,195,
     48, 49, 50, 51)

# Rate 1/2: 4 states x 4 dibit inputs -> 4-bit codeword. 12 octets out.
# Used by TSBKs and by the PDU HEADER block. Identical to op25's own table.
TRELLIS_1_2 = (
    (0x2, 0xC, 0x1, 0xF),
    (0xE, 0x0, 0xD, 0x3),
    (0x9, 0x7, 0xA, 0x4),
    (0x5, 0xB, 0x6, 0x8),
)

# Rate 3/4: 8 states x 8 tribit inputs -> 4-bit codeword. 18 octets out.
# Used by PDU DATA blocks, which is why op25 could never read one: it only
# implements the table above, so every data block failed while headers decoded
# perfectly (0 header CRC failures in 29 PDUs).
#
# PROVENANCE, because a wrong FEC table produces confident garbage: recovered
# from SDRTrunk's P25_3_4_Node bytecode with a class-file parser, NOT written
# from memory. The same parser was pointed at P25_1_2_Node first and returned
# the table above byte-for-byte identical to op25's independent copy -- getting
# a known-correct answer out is what licenses trusting the unknown one.
TRELLIS_3_4 = (
    (2, 13, 14,  1,  7,  8, 11,  4),
    (14, 1,  7,  8, 11,  4,  2, 13),
    (10, 5,  6,  9, 15,  0,  3, 12),
    (6,  9, 15,  0,  3, 12, 10,  5),
    (15, 0,  3, 12, 10,  5,  6,  9),
    (3, 12, 10,  5,  6,  9, 15,  0),
    (7,  8, 11,  4,  2, 13, 14,  1),
    (11, 4,  2, 13, 14,  1,  7,  8),
)

BLOCK_BITS = 196          # one coded block, either rate
FRAME_PREAMBLE_BITS = 112 # 48 frame sync + 64 NID

# Each data block spends its first 2 octets on a data-block serial number and
# a CRC9, leaving 16 of user data.
DATA_BLOCK_HEADER_LEN = 2

# And the reassembled user data opens with a 2-octet SNDCP header before the
# IP header. MEASURED: parse_ipv4 finds nothing at offset 0 and validates at
# offset 2 on every packet observed, so the earlier "not IPv4" verdicts were
# honest but were reading two octets too early.
#
# WHAT IT IS. Recovered from SDRTrunk's SNDCPPacketHeader: four 4-bit fields
# across those 16 bits, named PDU_TYPE, OUTBOUND_UNKNOWN,
# PACKET_HEADER_COMPRESSION and DATAGRAM_HEADER_COMPRESSION. On this system it
# is CONSTANT -- `51 00` on all 283 checksum-valid datagrams -- so the nibbles
# read 5, 1, 0, 0.
#
# Which name sits at which position is INFERRED from the order the fields are
# declared, and nothing here can check that. What supports it: both
# compression fields land on 0, and an uncompressed IPv4 header is exactly
# what follows. That is a weak check (two of four fields are zero either way),
# so the mapping is recorded as a lead rather than decoded into `fields`.
#
# Being constant is also why it cannot be worked out from our own data: there
# is no variation to correlate against anything.
SNDCP_PREFIX_LEN = 2


def _bits(packed: bytes) -> list[int]:
    return [(b >> (7 - j)) & 1 for b in packed for j in range(8)]


def crc16_p25(buf: bytes, length: int) -> int:
    """op25's crc16, used to validate a PDU header block. 0 means valid."""
    poly = (1 << 12) + (1 << 5) + (1 << 0)
    crc = 0
    for i in range(length):
        for j in range(8):
            crc = ((crc << 1) | ((buf[i] >> (7 - j)) & 1)) & 0x1ffff
            if crc & 0x10000:
                crc = (crc & 0xffff) ^ poly
    return (crc ^ 0xffff) & 0xffff


_POPCOUNT = tuple(bin(i).count('1') for i in range(16))


def _decode_block(bv: list[int], start: int, table, sym_bits: int,
                  out_len: int):
    """Viterbi trellis decode. Returns (bytes, bit_errors_corrected).

    op25's block_deinterleave -- and the first version of this function, ported
    from it -- is GREEDY: it takes the closest symbol at each step, keeps no
    path history, and returns failure the moment two candidates tie. That
    throws away the error-correcting power of the code, because a tie at one
    symbol is usually resolvable by the symbols that follow.

    MEASURED on 541 real frames, greedy against this:

        headers passing CRC16          495 -> 539   (+44)
        checksum-valid datagrams       162 -> 181   (+19)

    The rescued headers had path costs of 1-11 bit errors and every one of them
    PASSES CRC16 -- an independent 16-bit check the decoder has no way to
    satisfy by accident, which is what makes this a correction rather than a
    decoder that has learned to accept noise.

    The tradeoff: a path search never fails, so this always returns something.
    Callers must gate on an independent check -- crc16_p25 for a header, the
    sender's own block count plus the IPv4 checksum for data. `bit_errors` is
    returned as a quality signal for the same reason.
    """
    if start + BLOCK_BITS > len(bv):
        return None, None
    n = len(table)
    inf = float('inf')
    cost = [0.0] + [inf] * (n - 1)          # the encoder starts in state 0
    back: list[list[int]] = []
    for b in range(0, BLOCK_BITS, 4):
        cw = ((bv[start + DEINTERLEAVE[b + 0]] << 3)
              + (bv[start + DEINTERLEAVE[b + 1]] << 2)
              + (bv[start + DEINTERLEAVE[b + 2]] << 1)
              + bv[start + DEINTERLEAVE[b + 3]])
        nxt = [inf] * n
        prev = [0] * n
        for s in range(n):
            if cost[s] == inf:
                continue
            row, base = table[s], cost[s]
            for i in range(n):
                c = base + _POPCOUNT[cw ^ row[i]]
                if c < nxt[i]:
                    nxt[i], prev[i] = c, s
        cost, _unused = nxt, back.append(prev)
    end = min(range(n), key=lambda s: cost[s])
    errors = cost[end]

    # Traceback. The state ENTERED at step t is the input decoded at step t,
    # because this encoder's next state is its input.
    states = [0] * len(back)
    s = end
    for t in range(len(back) - 1, -1, -1):
        states[t] = s
        s = back[t][s]

    keep = out_len * 8 // sym_bits          # the last symbol is a flush
    bits: list[int] = []
    for st in states[:keep]:
        for k in range(sym_bits - 1, -1, -1):
            bits.append((st >> k) & 1)
    out = bytearray(out_len)
    for i, bit in enumerate(bits[:out_len * 8]):
        if bit:
            out[i >> 3] |= 1 << (7 - (i & 7))
    return bytes(out), errors


def decode_header_block(bv: list[int]) -> bytes | None:
    """The PDU header: rate 1/2, 12 octets. Caller must check crc16_p25."""
    out, _errors = _decode_block(bv, FRAME_PREAMBLE_BITS, TRELLIS_1_2, 2, 12)
    return out


def decode_data_block(bv: list[int], index: int, fmt: int = 0x16):
    """One data block. Returns (bytes, rate) or (None, None).

    THE RATE DEPENDS ON THE PACKET FORMAT, which cost a bug to learn. A
    confirmed data packet (fmt 0x16) carries rate-3/4 blocks of 18 octets; a
    RESPONSE packet (fmt 0x03) carries rate-1/2 blocks of 12.

    Evidence, since this mapping is inferred rather than read out of the
    standard. Three fmt=03 blocks decoded cleanly at rate 1/2 and FAILED at
    3/4:

        sap=03 : fc ff ff ff ff ff ff ff bd 1d fc 83
        sap=00 : fc ff ff ff ff ff ff ff bd 1d fc 83
        sap=04 : f8 ff ff ff ff ff ff ff d7 5b 92 1c

    The first two are byte-identical, which looked at first like a decoder
    artifact -- a degenerate convergence over padding. It is not: the 196
    on-air bits behind them are themselves bit-identical (72 ones each), so it
    is the same message twice. The third differs, so they are not one constant.

    All three share a shape -- 8 octets of near-all-ones then 4 varying bytes
    -- consistent with a block-acknowledgement bitmap (all ones = everything
    acked) plus a CRC32, which is what a 12-octet response block should look
    like. Worth knowing for whoever writes the response parser.

    An earlier version of this function applied 3/4 unconditionally and would
    have turned those 12 octets into garbage while reporting success.

    Both rates are attempted regardless, format-indicated one first, because
    the mapping above is inferred from observation rather than read out of the
    standard -- and a block that decodes under only one rate tells us which it
    was. `index` is 1-based.
    """
    start = FRAME_PREAMBLE_BITS + index * BLOCK_BITS
    orders = ([('1/2', TRELLIS_1_2, 2, 12), ('3/4', TRELLIS_3_4, 3, 18)]
              if fmt == Pdu.FMT_RESPONSE else
              [('3/4', TRELLIS_3_4, 3, 18), ('1/2', TRELLIS_1_2, 2, 12)])
    for rate, table, sym, out_len in orders:
        got, _errors = _decode_block(bv, start, table, sym, out_len)
        if got is not None:
            return got, rate
    return None, None


def parse_raw_line(line: str) -> Pdu | None:
    """Decode a `PDU raw:` line all the way to a Pdu with its payload.

    Returns None when the header block will not decode or fails CRC -- there is
    nothing trustworthy to report in that case, and saying so is the point.
    """
    m = PDU_RAW_LINE.search(line)
    if not m:
        return None
    bv = _bits(bytes.fromhex(m['hex']))
    hdr = decode_header_block(bv)
    if hdr is None or crc16_p25(hdr, 12) != 0:
        return None
    fmt = hdr[0] & 0x1f
    n_blocks = (len(bv) - FRAME_PREAMBLE_BITS) // BLOCK_BITS
    # STOP AT THE SENDER'S OWN COUNT. Anything past it is padding, and since
    # the Viterbi decoder never fails it would decode that padding into
    # plausible-looking bytes and append them to the datagram. Measured before
    # this cap: 798 "decoded" blocks against 756 actually claimed.
    claimed = hdr[HDR_BLKS_OCTET] & 0x7f
    if claimed:
        n_blocks = min(n_blocks, claimed + 1)
    payload = b''
    recovered = 0
    rates = []
    for i in range(1, n_blocks):
        blk, rate = decode_data_block(bv, i, fmt)
        if blk is None:
            break                      # keep what decoded, same as the C++
        payload += blk[DATA_BLOCK_HEADER_LEN:]
        recovered += 1
        rates.append(rate)
    pdu = Pdu(nac=int(m['nac'], 16), fmt=fmt, sap=hdr[1] & 0x3f,
              blks=recovered, hdr=hdr, payload=payload)
    pdu.block_rates = rates
    return pdu


# Octets 3-5 of the PDU header: the 24-bit logical link id, i.e. the radio.
#
# CONFIRMED against the first real header decoded off the air (2026-09-04):
#     76 c0 00 03 55 da 84 0d 48 02 d8 4e
#     octets 2-4 -> 0x000355 =     853   implausible
#     octets 3-5 -> 0x0355da = 218,586   <- matches the fleet
#     octets 4-6 -> 0x55da84 = 5,626,500 implausible
# Known radio ids from TSBK 0x14 targets run 0x0355xx-0x0368xx, so only one of
# those readings is a radio. This was slice(2, 5) on a guess and was wrong --
# exactly why it lives here and not in the C++, where fixing it costs a rebuild
# and a capture outage.
LLID_OCTETS = slice(3, 6)

# Octet 6, low 7 bits: how many data blocks the SENDER says follow the header.
# Distinct from the count we actually recovered -- the C++ log line's `blks=`
# field is the RECOVERED count, so comparing the two says how much of the
# packet was lost. First real header claimed 4 and we recovered 0.
HDR_BLKS_OCTET = 6

# TIA-102.BAEA service access points. Only the ones this system can plausibly
# emit are named; anything else is reported by number rather than guessed at.
SAP_NAMES = {
    0:  'unencrypted user data',
    1:  'encrypted user data',
    2:  'circuit data',
    3:  'circuit data control',
    4:  'packet data',
    5:  'address resolution',
    6:  'SNDCP packet data control',
    31: 'registration and authorisation',
    61: 'trunking control (MBT)',
    63: 'protected trunking control',
}

# UDP ports seen on Motorola ASTRO 25 data. NAMING ONLY -- this module does not
# decode any of these, and a port number is a hint, not an identification.
UDP_PORT_HINTS = {
    4001: 'LRRP (location)',
    4005: 'ARS (registration)',
    4007: 'TMS (text messaging)',
    4008: 'TMS (text messaging)',
}


@dataclass
class Pdu:
    """One reassembled packet-data PDU, as read off the log line."""
    nac: int
    fmt: int
    sap: int
    blks: int
    hdr: bytes
    payload: bytes
    # Which trellis rate each recovered data block decoded under, in order.
    # Empty for a Pdu built from the decoded `PDU: fmt=` line, which carries
    # no blocks. See decode_data_block on why this is worth recording.
    block_rates: list = field(default_factory=list)

    # Packet formats. fmt is octet 0 & 0x1f, as op25 reads it.
    FMT_RESPONSE = 0x03
    FMT_CONFIRMED = 0x16

    @property
    def sap_valid(self) -> bool:
        """Is the `sap` field on the log line actually a SAP?

        NO for a response PDU. The C++ reads octet 1 & 0x3f as SAP for EVERY
        format, but a response PDU (fmt 0x03) does not carry a SAP there --
        that octet holds response class/type/status. Observed off the air:
        fmt=03 headers reported sap=0c and sap=0d, which are not SAPs at all,
        while fmt=16 headers reported 00 and 06, which are.

        Not fixed in the C++ on purpose: the raw header is on the log line, so
        this is an interpretation question, and interpretation lives here.
        """
        return self.fmt != self.FMT_RESPONSE

    @property
    def sap_name(self) -> str:
        if not self.sap_valid:
            return f'n/a (fmt 0x{self.fmt:02x} response PDU; octet 1 is not a SAP)'
        return SAP_NAMES.get(self.sap, f'unknown SAP {self.sap}')

    @property
    def llid(self) -> int:
        """The transmitting/receiving radio. See LLID_OCTETS on why this is
        derived here rather than in the C++."""
        return int.from_bytes(self.hdr[LLID_OCTETS], 'big')

    @property
    def hdr_blks(self) -> int:
        """Data blocks the SENDER claimed, vs `blks` = blocks we recovered."""
        return self.hdr[HDR_BLKS_OCTET] & 0x7f

    @property
    def blocks_lost(self) -> int:
        """How much of this packet never made it through FEC."""
        return max(0, self.hdr_blks - self.blks)


@dataclass
class Verdict:
    """What we can honestly say about one PDU's payload."""
    kind: str                       # 'ipv4' | 'not-ipv4' | 'empty'
    clear: bool                     # True only when IPv4 PROVED it
    reason: str                     # why, in one line
    detail: dict = field(default_factory=dict)


def parse_log_line(line: str) -> Pdu | None:
    """Pull one PDU out of an op25 log line, or None if the line is not one."""
    m = PDU_LINE.search(line)
    if not m:
        return None
    return Pdu(
        nac=int(m['nac'], 16),
        fmt=int(m['fmt'], 16),
        sap=int(m['sap'], 16),
        blks=int(m['blks']),
        hdr=bytes.fromhex(m['hdr'].replace(' ', '')),
        payload=bytes.fromhex(m['payload'].replace(' ', '')),
    )


def ones_complement_checksum(data: bytes) -> int:
    """The 16-bit one's complement sum used by IPv4/UDP (RFC 1071).

    Over a header whose own checksum field is still in place, a correct header
    sums to 0. That property is the whole basis of `classify()`.
    """
    if len(data) % 2:
        data += b'\x00'
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
        total = (total & 0xffff) + (total >> 16)     # fold carries as we go
    return (~total) & 0xffff


def parse_ipv4(payload: bytes) -> dict | None:
    """Decode an IPv4 header, or None if this cannot be one.

    Structural checks first, checksum last: a wrong length would otherwise make
    the checksum a sum over the wrong bytes, and a coincidental pass there would
    be far more surprising than a coincidental pass over the right ones.
    """
    if len(payload) < 20:
        return None
    version = payload[0] >> 4
    ihl = (payload[0] & 0x0f) * 4
    if version != 4 or ihl < 20 or ihl > len(payload):
        return None
    total_length = int.from_bytes(payload[2:4], 'big')
    # The PDU is padded up to a block boundary, so the datagram may be SHORTER
    # than the payload we were handed -- but never longer.
    if total_length < ihl or total_length > len(payload):
        return None

    header = payload[:ihl]
    out = {
        'ihl': ihl,
        'total_length': total_length,
        'protocol': payload[9],
        'src': '.'.join(str(b) for b in payload[12:16]),
        'dst': '.'.join(str(b) for b in payload[16:20]),
        'checksum_ok': ones_complement_checksum(header) == 0,
        'body': payload[ihl:total_length],
    }
    if out['protocol'] == 17 and len(out['body']) >= 8:
        b = out['body']
        out['udp'] = {
            'sport': int.from_bytes(b[0:2], 'big'),
            'dport': int.from_bytes(b[2:4], 'big'),
            'length': int.from_bytes(b[4:6], 'big'),
            'data': b[8:],
        }
        # DESTINATION PORT FIRST, deliberately: it names the service being
        # addressed. Checking the source first mislabels a request sent from an
        # ephemeral port that happens to collide with a known one, and on this
        # system both ends of a conversation use these ports, so the ambiguity
        # is real rather than theoretical. When both are known and disagree,
        # say so instead of silently picking one.
        d, s = out['udp']['dport'], out['udp']['sport']
        if d in UDP_PORT_HINTS and s in UDP_PORT_HINTS and d != s:
            out['udp']['hint'] = f'{UDP_PORT_HINTS[d]} <- {UDP_PORT_HINTS[s]}'
        elif d in UDP_PORT_HINTS:
            out['udp']['hint'] = UDP_PORT_HINTS[d]
        elif s in UDP_PORT_HINTS:
            out['udp']['hint'] = UDP_PORT_HINTS[s]
    return out


BLOCK = 12          # bytes per PDU data block, post-trellis


def candidate_payloads(pdu: Pdu) -> list[tuple[str, bytes]]:
    """The plausible readings of the raw data blocks, best first.

    P25 has two data-block formats and the C++ hands over neither -- it hands
    over the raw 12-byte blocks and leaves the choice here, for the same reason
    the header is emitted raw (see LLID_OCTETS).

      unconfirmed: all 12 bytes are user data.
      confirmed:   the first 2 carry a data-block serial number and a CRC9,
                   leaving 10 bytes of user data per block.

    Which one applies is a bit in the header whose position is not something
    anyone here has verified against a real PDU. Rather than guess it, try
    both: the IPv4 checksum is decisive enough that a wrong reading fails and a
    right one proves itself. Once real traffic says which it is, this collapses
    to one branch and the header bit gets documented.
    """
    raw = pdu.payload
    out = []
    # FIRST, because it is the proven layout: a frame decoded from `PDU raw:`
    # already has its per-block DBSN/CRC9 octets removed, and the user data
    # opens with a 2-octet SNDCP prefix ahead of the IP header.
    if len(raw) > SNDCP_PREFIX_LEN:
        out.append(('sndcp', raw[SNDCP_PREFIX_LEN:]))
    out.append(('unconfirmed', raw))
    if len(raw) >= BLOCK:
        stripped = b''.join(raw[i + 2:i + BLOCK]
                            for i in range(0, len(raw) - BLOCK + 1, BLOCK))
        out.append(('confirmed', stripped))
    return out


def classify(pdu: Pdu) -> Verdict:
    """Decide, conservatively, whether this PDU's payload is in the clear."""
    if not pdu.payload:
        return Verdict('empty', False, 'no payload bytes')

    # The system may simply tell us. SAP 1 is encrypted user data by
    # definition, and we believe it without looking at the bytes.
    if pdu.sap == 1:
        return Verdict('not-ipv4', False,
                       'SAP 1: the system labels this encrypted user data')

    ip = None
    block_format = 'unconfirmed'
    for name, candidate in candidate_payloads(pdu):
        got = parse_ipv4(candidate)
        if got is not None and got['checksum_ok']:
            ip, block_format = got, name
            break
        if got is not None and ip is None:
            ip, block_format = got, name       # keep the best near-miss
    if ip is None:
        # HONEST NEGATIVE. Not proof of encryption: a non-IP SNDCP control
        # PDU, a fragment, or a truncated reassembly all land here too.
        return Verdict('not-ipv4', False,
                       'payload is not a well-formed IPv4 header '
                       '(encrypted, non-IP, or truncated -- cannot tell which)')

    if not ip['checksum_ok']:
        return Verdict('not-ipv4', False,
                       'IPv4-shaped but header checksum failed; '
                       'treat as not decoded rather than as cleartext',
                       {'ip': ip})

    what = ip.get('udp', {}).get('hint', f"IP protocol {ip['protocol']}")
    return Verdict('ipv4', True,
                   f"IPv4 header checksum validates -- payload is IN THE CLEAR ({what})",
                   {'ip': ip, 'block_format': block_format})


def scan(lines) -> list[tuple[Pdu, Verdict]]:
    """Read a whole op25 log, returning every PDU with its verdict.

    Prefers the `PDU raw:` line, which carries the whole frame and lets us
    decode the rate-3/4 data blocks op25 cannot. Falls back to the decoded
    `PDU: fmt=` line, which gives the header but -- until the raw dump existed
    -- never any payload.
    """
    out = []
    seen_hdrs: set[bytes] = set()
    for line in lines:
        pdu = parse_raw_line(line)
        if pdu is not None:
            # A raw line and the `PDU: fmt=` line that follows it describe the
            # SAME frame -- the C++ emits the dump inside process_blocks and the
            # decoded line right after. Remember the header so the duplicate can
            # be dropped below.
            seen_hdrs.add(pdu.hdr)
        else:
            pdu = parse_log_line(line)
            if pdu is not None and pdu.hdr in seen_hdrs:
                # DEDUPLICATION, and it matters for more than tidiness. This
                # line comes from op25's own rate-1/2-only path, so for a
                # confirmed data packet it always reports blks=0. Counting it
                # alongside the properly decoded frame double-counted every PDU
                # and dragged the reported block-recovery rate from 85% to 45%
                # -- a statistic invented entirely by the measurement.
                continue
        if pdu is not None:
            out.append((pdu, classify(pdu)))
    return out


def main(argv: list[str]) -> int:
    import collections
    import sys

    if len(argv) < 2:
        sys.stderr.write('usage: p25_packet.py <op25 log> [...]\n')
        return 2

    results = []
    for path in argv[1:]:
        with open(path, errors='ignore') as fh:
            results.extend(scan(fh))

    if not results:
        print('no packet-data PDUs found.')
        print('  If a data receiver was following grants, this means the op25 '
              'PDU patch is not in place -- see patches/README.md. Check the '
              'INSTALLED library, not the source: '
              'strings <libgnuradio-op25_repeater.so> | grep process_PDU')
        return 1

    kinds = collections.Counter(v.kind for _, v in results)
    saps = collections.Counter(p.sap_name for p, _ in results)
    radios = {p.llid for p, _ in results}
    clear = [(p, v) for p, v in results if v.clear]

    print(f'{len(results)} packet-data PDUs, {len(radios)} distinct radios')
    print(f'  by shape: {dict(kinds)}')
    print(f'  by SAP:   {dict(saps)}')
    print(f'  IN THE CLEAR (IPv4 checksum validated): {len(clear)}')
    lost = sum(p.blocks_lost for p, _ in results)
    claimed = sum(p.hdr_blks for p, _ in results)
    if claimed:
        print(f'  data blocks: {claimed - lost}/{claimed} recovered')
    for pdu, verdict in clear[:10]:
        ip = verdict.detail['ip']
        udp = ip.get('udp')
        where = (f" {ip['src']}:{udp['sport']} -> {ip['dst']}:{udp['dport']}"
                 f" [{udp.get('hint', 'udp')}] {len(udp['data'])} B"
                 if udp else f" {ip['src']} -> {ip['dst']} proto {ip['protocol']}")
        print(f"    radio {pdu.llid:06x}{where}")
        if udp:
            msg = p25_apps.parse(udp['sport'], udp['dport'], udp['data'])
            if msg is not None:
                print(f"        {msg}")

    # What the system is DOING, which is the point of decoding any of this.
    kinds = collections.Counter()
    for pdu, verdict in clear:
        udp = verdict.detail['ip'].get('udp')
        if not udp:
            continue
        msg = p25_apps.parse(udp['sport'], udp['dport'], udp['data'])
        if msg is not None:
            kinds[f'{msg.protocol}: {msg.kind}'] += 1
    if kinds:
        print()
        print('  application messages:')
        for k, n in kinds.most_common():
            print(f'    {n:5d}  {k}')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
