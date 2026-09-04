#!/usr/bin/env python3
"""Application-layer decoders for the packet data carried over LWIN.

scripts/p25_packet.py gets a datagram off the air and proves it is cleartext.
This module says what the datagram MEANS -- but only as far as the evidence
goes, which is the whole design of the file.

WHAT IS SOURCED, AND FROM WHERE
-------------------------------
Every constant here was recovered from SDRTrunk's own bytecode (it ships a
mature Motorola ARS/LRRP implementation) with the class-file parsers used for
the trellis tables, NOT written from memory. Field positions come from
SDRTrunk's bit-index arrays, so they are the positions its decoder actually
uses rather than a layout reconstructed from prose:

    ARSHeader:  LENGTH = bits 0-15,  PDU_TYPE = bits 20-23
    ARSPDUType: 0 DEVICE_REGISTRATION, 1 DEVICE_DEREGISTRATION, 4 QUERY,
                5 USER_REGISTRATION, 6 USER_DEREGISTRATION,
                7 USER_REGISTRATION_ACKNOWLEDGEMENT,
                15 REGISTRATION_ACKNOWLEDGEMENT
    LRRPPacketType: 5 IMMEDIATE_LOCATION_REQUEST, 7 IMMEDIATE_LOCATION_RESPONSE,
                9 TRIGGERED_LOCATION_START_REQUEST,
                11 TRIGGERED_LOCATION_START_RESPONSE, 13 TRIGGERED_LOCATION,
                15 TRIGGERED_LOCATION_STOP_REQUEST,
                17 TRIGGERED_LOCATION_STOP_RESPONSE,
                20 PROTOCOL_VERSION_REQUEST, 21 PROTOCOL_VERSION_RESPONSE

WHAT IS DELIBERATELY LEFT RAW
-----------------------------
LRRP token bodies and the ARS flag nibble. Both are decodable in principle and
neither is decoded here, because working out a field layout from four captured
samples is how this project produces confident wrong answers -- it has done so
four times in this line of work already (a pinned data channel from a
35-minute sample, an LLID offset, a copied bit offset, a SAP read for the wrong
packet format). Raw bytes are always returned alongside any interpretation, so
a wrong reading here is visible rather than silently authoritative.

WHAT THE TRAFFIC IS
-------------------
Measured over 21 log files: every readable datagram is OUTBOUND, system
(10.51.1.10) to radio (172.16.x.y). ARS is registration acknowledgement; LRRP
is the system asking radios to START periodic location reporting. The reports
themselves travel INBOUND at 799-805 / 806-824 MHz and no receiver in this
config can tune them, so this module will never see a coordinate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Recovered from ARSPDUType's <clinit>. Values, not ordinals.
ARS_PDU_TYPES = {
    0:  'device registration',
    1:  'device deregistration',
    4:  'query',
    5:  'user registration',
    6:  'user deregistration',
    7:  'user registration acknowledgement',
    15: 'registration acknowledgement',
}

# Recovered from LRRPPacketType's <clinit>.
LRRP_PACKET_TYPES = {
    5:  'immediate location request',
    7:  'immediate location response',
    9:  'triggered location start request',
    11: 'triggered location start response',
    13: 'triggered location',
    15: 'triggered location stop request',
    17: 'triggered location stop response',
    20: 'protocol version request',
    21: 'protocol version response',
}

# A response type means a radio is reporting; a request means the system is
# asking. Only the latter can appear on the downlink, so seeing a response
# here would mean something about our understanding is wrong.
LRRP_RESPONSE_TYPES = frozenset({7, 11, 13, 17, 21})


@dataclass
class AppMessage:
    """One decoded application payload, with the undecoded parts preserved."""
    protocol: str                       # 'ARS' | 'LRRP'
    kind: str                           # human-readable message type
    raw: bytes
    fields: dict = field(default_factory=dict)
    undecoded: bytes = b''

    def __str__(self) -> str:
        bits = [f'{self.protocol}: {self.kind}']
        for k, v in self.fields.items():
            bits.append(f'{k}={v}')
        if self.undecoded:
            bits.append(f'undecoded={self.undecoded.hex(" ")}')
        return '  '.join(bits)


def parse_ars(payload: bytes) -> AppMessage | None:
    """ARS (UDP 4005). Layout from SDRTrunk's ARSHeader bit arrays.

        bits  0-15   length of everything after the length field
        bits 16-19   flags -- NOT decoded, see the module docstring
        bits 20-23   PDU type
    """
    if len(payload) < 3:
        return None
    declared = int.from_bytes(payload[0:2], 'big')
    flags = (payload[2] >> 4) & 0x0f
    pdu_type = payload[2] & 0x0f
    body = payload[3:]

    fields = {
        'declared_len': declared,
        'actual_len': len(payload) - 2,
        'flags': f'0x{flags:x}',
    }
    # The length field is a real consistency check, not decoration: a header
    # that decoded wrongly is unlikely to also self-describe correctly.
    if declared != len(payload) - 2:
        fields['LENGTH_MISMATCH'] = True
    return AppMessage(
        protocol='ARS',
        kind=ARS_PDU_TYPES.get(pdu_type, f'unknown ARS type {pdu_type}'),
        raw=payload,
        fields=fields,
        undecoded=body,
    )


def parse_lrrp(payload: bytes) -> AppMessage | None:
    """LRRP (UDP 4001).

        octet 0   packet type
        octet 1   length of the token block that follows
        octets 2+ tokens -- NOT decoded, see the module docstring

    Token parsing is where a coordinate would come from, and it is the one
    thing this module cannot usefully do: position tokens travel in LRRP
    RESPONSES, which are inbound, and nothing here can hear inbound.
    """
    if len(payload) < 2:
        return None
    ptype = payload[0]
    declared = payload[1]
    tokens = payload[2:]

    fields = {
        'declared_len': declared,
        'actual_len': len(tokens),
        'direction': 'radio->system' if ptype in LRRP_RESPONSE_TYPES else 'system->radio',
    }
    if declared != len(tokens):
        fields['LENGTH_MISMATCH'] = True
    return AppMessage(
        protocol='LRRP',
        kind=LRRP_PACKET_TYPES.get(ptype, f'unknown LRRP type {ptype}'),
        raw=payload,
        fields=fields,
        undecoded=tokens,
    )


# UDP port -> parser. Ports come from p25_packet.UDP_PORT_HINTS; only the two
# services actually observed on this system are wired up, because a parser for
# traffic nobody has seen cannot be tested against anything.
PARSERS = {
    4001: parse_lrrp,
    4005: parse_ars,
}


def parse(sport: int, dport: int, payload: bytes) -> AppMessage | None:
    """Decode by port, destination first -- same rule as the port hints."""
    for port in (dport, sport):
        if port in PARSERS:
            return PARSERS[port](payload)
    return None
