#!/usr/bin/env python3
"""Tests for the ARS/LRRP application decoders.

Every payload below was captured off LWIN site 13 on 2026-09-04 and is used
verbatim. That matters more here than usual: the field layouts came from
SDRTrunk's bit-index arrays, and the only independent check that they were read
correctly is that real messages self-describe consistently -- a header parsed
at the wrong offset is very unlikely to also produce a length field that agrees
with the byte count. Several tests below assert exactly that agreement.
"""
from __future__ import annotations

import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import p25_apps as A

# Real payloads, with their observed frequencies over 21 log files.
ARS_ACK_FF = bytes.fromhex('0002ff00')                      # x52
ARS_ACK_BF = bytes.fromhex('0002bf08')                      # x22
ARS_ACK_LONG = bytes.fromhex('0007bf08046a9adacf')          # x1 (3 variants)
LRRP_START_A = bytes.fromhex('090f2203ffff025244643a646257344a02')
LRRP_START_B = bytes.fromhex('090f2203ffffee5244643a64625734311e')


class ArsDecodesRealMessages(unittest.TestCase):

    def test_the_common_acknowledgement(self):
        m = A.parse_ars(ARS_ACK_FF)
        self.assertEqual(m.protocol, 'ARS')
        self.assertEqual(m.kind, 'registration acknowledgement')
        self.assertEqual(m.fields['flags'], '0xf')
        self.assertNotIn('LENGTH_MISMATCH', m.fields)

    def test_the_other_flag_variant_is_the_same_message_type(self):
        # 0xff and 0xbf differ only in the flag nibble; both are type 0x0f.
        # If the type field had been read at the wrong offset these would very
        # likely have decoded as two different messages.
        m = A.parse_ars(ARS_ACK_BF)
        self.assertEqual(m.kind, 'registration acknowledgement')
        self.assertEqual(m.fields['flags'], '0xb')

    def test_the_length_field_agrees_with_the_byte_count(self):
        # THE LAYOUT CHECK. Bits 0-15 are the length of everything after the
        # length field, per SDRTrunk's ARSHeader. Agreement on a 2-octet and a
        # 7-octet message is evidence the offset is right.
        for payload, want in ((ARS_ACK_FF, 2), (ARS_ACK_BF, 2), (ARS_ACK_LONG, 7)):
            with self.subTest(payload=payload.hex()):
                m = A.parse_ars(payload)
                self.assertEqual(m.fields['declared_len'], want)
                self.assertEqual(m.fields['actual_len'], want)
                self.assertNotIn('LENGTH_MISMATCH', m.fields)

    def test_a_length_disagreement_is_flagged_not_hidden(self):
        bad = bytearray(ARS_ACK_LONG)
        bad[1] = 0x20                       # claim 32 octets, supply 7
        m = A.parse_ars(bytes(bad))
        self.assertTrue(m.fields['LENGTH_MISMATCH'])

    def test_an_unknown_type_is_reported_as_unknown(self):
        # 0x0a is not in the recovered table. Naming it something plausible
        # would invent a service.
        m = A.parse_ars(bytes.fromhex('0002fa00'))
        self.assertIn('unknown ARS type 10', m.kind)

    def test_raw_bytes_are_always_kept(self):
        m = A.parse_ars(ARS_ACK_LONG)
        self.assertEqual(m.raw, ARS_ACK_LONG)
        self.assertEqual(m.undecoded, ARS_ACK_LONG[3:])

    def test_a_runt_payload_is_refused(self):
        self.assertIsNone(A.parse_ars(b'\x00\x02'))


class LrrpDecodesRealMessages(unittest.TestCase):

    def test_both_observed_payloads_are_start_requests(self):
        for payload in (LRRP_START_A, LRRP_START_B):
            with self.subTest(payload=payload.hex()):
                m = A.parse_lrrp(payload)
                self.assertEqual(m.protocol, 'LRRP')
                self.assertEqual(m.kind, 'triggered location start request')

    def test_the_length_field_agrees(self):
        # Same layout check as ARS: octet 1 is the token-block length.
        for payload in (LRRP_START_A, LRRP_START_B):
            with self.subTest(payload=payload.hex()):
                m = A.parse_lrrp(payload)
                self.assertEqual(m.fields['declared_len'], 15)
                self.assertEqual(m.fields['actual_len'], 15)
                self.assertNotIn('LENGTH_MISMATCH', m.fields)

    def test_a_request_is_marked_outbound(self):
        # Consistency with everything else we know: all 86 readable datagrams
        # were system -> radio. A response type appearing here would mean our
        # understanding of the direction is wrong, so the field is worth having.
        self.assertEqual(A.parse_lrrp(LRRP_START_A).fields['direction'],
                         'system->radio')

    def test_a_response_type_is_marked_inbound(self):
        # We cannot hear these -- they travel at 799-805 MHz -- so this branch
        # exists to make it obvious if one ever shows up.
        m = A.parse_lrrp(bytes([13, 0]))            # 13 = triggered location
        self.assertEqual(m.kind, 'triggered location')
        self.assertEqual(m.fields['direction'], 'radio->system')

    def test_tokens_are_left_undecoded_and_returned_whole(self):
        # Deliberate: a coordinate would come from these, and working out the
        # token layout from two samples is how wrong answers get published.
        m = A.parse_lrrp(LRRP_START_A)
        self.assertEqual(m.undecoded, LRRP_START_A[2:])
        self.assertEqual(len(m.undecoded), 15)

    def test_an_unknown_type_is_reported_as_unknown(self):
        # 0x03 masks to 3, which is not in the recovered table. Masking must
        # not turn an unrecognised type into a recognised one.
        m = A.parse_lrrp(bytes([0x03, 0]))
        self.assertIn('unknown LRRP type 3', m.kind)

    def test_high_bits_of_octet_zero_are_flags_not_the_type(self):
        """Both payloads captured off LWIN, differing ONLY in octet 0.

        0x69 arrived once against 0x09's 161 times, and its remaining sixteen
        bytes are byte-identical -- request token and all. A genuine change of
        message type cannot produce an identical body, so 0x69 is type 9 with
        flags. Reading the whole octet reported a "type 105" that does not
        exist.
        """
        plain = bytes.fromhex('090f2203ffffee5244643a64625734311e')
        flagged = bytes.fromhex('690f2203ffffee5244643a64625734311e')
        self.assertEqual(plain[1:], flagged[1:], 'fixtures must differ only in octet 0')

        a, b = A.parse_lrrp(plain), A.parse_lrrp(flagged)
        self.assertEqual(a.kind, 'triggered location start request')
        self.assertEqual(b.kind, a.kind)
        self.assertEqual(b.undecoded, a.undecoded)

    def test_the_flags_are_surfaced_but_not_interpreted(self):
        # Losing them would hide the only thing distinguishing this message
        # from the other 161. Naming them would be inventing a meaning.
        flagged = bytes.fromhex('690f2203ffffee5244643a64625734311e')
        m = A.parse_lrrp(flagged)
        self.assertEqual(m.fields['flags'], '0x3')
        self.assertNotIn('flags', A.parse_lrrp(LRRP_START_A).fields,
                         'an unflagged message should not carry an empty flags field')


class DispatchPrefersTheDestinationPort(unittest.TestCase):

    def test_ars_and_lrrp_are_routed_by_port(self):
        self.assertEqual(A.parse(49516, 4005, ARS_ACK_FF).protocol, 'ARS')
        self.assertEqual(A.parse(4001, 4001, LRRP_START_A).protocol, 'LRRP')

    def test_destination_wins_when_both_ports_are_known(self):
        # Same rule as p25_packet's port hints, and for the same reason: both
        # ends of these conversations use well-known ports.
        self.assertEqual(A.parse(4001, 4005, ARS_ACK_FF).protocol, 'ARS')

    def test_an_unknown_service_returns_nothing_rather_than_guessing(self):
        self.assertIsNone(A.parse(51000, 52000, b'\x00\x01\x02\x03'))

    def test_no_parser_is_wired_for_traffic_never_observed(self):
        # TMS ports are in p25_packet's hint table but have produced zero
        # packets, so there is nothing to test a parser against.
        self.assertNotIn(4007, A.PARSERS)
        self.assertNotIn(4008, A.PARSERS)


class RecoveredTablesAreNotInvented(unittest.TestCase):
    """Pin the values taken from SDRTrunk's bytecode."""

    def test_ars_types(self):
        self.assertEqual(A.ARS_PDU_TYPES[0], 'device registration')
        self.assertEqual(A.ARS_PDU_TYPES[15], 'registration acknowledgement')
        # Gaps are real: 2 and 3 are absent from the enum, so they must not be
        # quietly filled in.
        self.assertNotIn(2, A.ARS_PDU_TYPES)
        self.assertNotIn(3, A.ARS_PDU_TYPES)

    def test_lrrp_types_and_their_direction_split(self):
        self.assertEqual(A.LRRP_PACKET_TYPES[9], 'triggered location start request')
        self.assertEqual(A.LRRP_PACKET_TYPES[13], 'triggered location')
        for t in A.LRRP_RESPONSE_TYPES:
            with self.subTest(t=t):
                self.assertIn(t, A.LRRP_PACKET_TYPES)
                self.assertNotIn('request', A.LRRP_PACKET_TYPES[t])


if __name__ == '__main__':
    unittest.main()
