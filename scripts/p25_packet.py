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

    @property
    def sap_name(self) -> str:
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
    out = [('unconfirmed', raw)]
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
    """Read a whole op25 log, returning every PDU with its verdict."""
    out = []
    for line in lines:
        pdu = parse_log_line(line)
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
        print('  If the receiver was on the data channel, this means the op25 '
              'PDU patch is not in place -- see patches/README.md.')
        return 1

    kinds = collections.Counter(v.kind for _, v in results)
    saps = collections.Counter(p.sap_name for p, _ in results)
    radios = {p.llid for p, _ in results}
    clear = [(p, v) for p, v in results if v.clear]

    print(f'{len(results)} packet-data PDUs, {len(radios)} distinct radios')
    print(f'  by shape: {dict(kinds)}')
    print(f'  by SAP:   {dict(saps)}')
    print(f'  IN THE CLEAR (IPv4 checksum validated): {len(clear)}')
    for pdu, verdict in clear[:10]:
        ip = verdict.detail['ip']
        udp = ip.get('udp')
        where = (f" {ip['src']}:{udp['sport']} -> {ip['dst']}:{udp['dport']}"
                 f" [{udp.get('hint', 'udp')}] {len(udp['data'])} B"
                 if udp else f" {ip['src']} -> {ip['dst']} proto {ip['protocol']}")
        print(f"    radio {pdu.llid:06x}{where}")
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
