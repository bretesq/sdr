#!/usr/bin/env python3
"""Tests for the P25 packet-data PDU reader.

The load-bearing claim in p25_packet.py is that a validating IPv4 header
checksum PROVES the payload is cleartext. So the checksum implementation is
pinned against a PUBLISHED vector rather than against itself -- if it were
tested only by building a header with the same function that checks it, every
test would pass with the algorithm entirely wrong, and the module would confidently
report encrypted traffic as clear.
"""
from __future__ import annotations

import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import p25_packet as P


# The worked example from RFC 1071 / the IPv4 header-checksum literature, used
# verbatim. Its stored checksum is 0xb861 and, because a correct header sums to
# zero WITH its checksum in place, this doubles as the external reference.
REFERENCE_HEADER = bytes.fromhex(
    '4500' '0073' '0000' '4000' '4011' 'b861' 'c0a80001' 'c0a800c7')


def ipv4_udp(src='10.0.0.5', dst='10.0.0.1', sport=4001, dport=4001,
             data=b'hello') -> bytes:
    """Build a well-formed IPv4/UDP datagram, checksum filled in."""
    udp = (sport.to_bytes(2, 'big') + dport.to_bytes(2, 'big')
           + (8 + len(data)).to_bytes(2, 'big') + b'\x00\x00' + data)
    total = 20 + len(udp)
    header = (bytes([0x45, 0x00]) + total.to_bytes(2, 'big')
              + b'\x00\x01' + b'\x00\x00' + bytes([64, 17]) + b'\x00\x00'
              + bytes(int(o) for o in src.split('.'))
              + bytes(int(o) for o in dst.split('.')))
    ck = P.ones_complement_checksum(header)
    header = header[:10] + ck.to_bytes(2, 'big') + header[12:]
    return header + udp


def log_line(payload: bytes, *, sap=0x04, fmt=0x16, blks=6, llid=0x0356eb) -> str:
    # The 12-octet header block, with the radio id at LLID_OCTETS and the rest
    # left as filler -- this module does not read the other fields yet.
    hdr = bytearray(12)
    hdr[0], hdr[1] = fmt, sap
    hdr[P.LLID_OCTETS] = llid.to_bytes(3, 'big')
    return ('09/03/26 21:09:18.336754 [0] '
            f'NAC 0x1bd PDU: fmt={fmt:02x} sap={sap:02x} blks={blks} '
            f'hdr={" ".join(f"{b:02x}" for b in hdr)} : '
            + ' '.join(f'{b:02x}' for b in payload))


class ChecksumIsCorrect(unittest.TestCase):
    """Pinned to an external vector, because everything else trusts it."""

    def test_reference_header_sums_to_zero(self):
        self.assertEqual(P.ones_complement_checksum(REFERENCE_HEADER), 0)

    def test_computing_it_from_scratch_reproduces_the_published_value(self):
        zeroed = REFERENCE_HEADER[:10] + b'\x00\x00' + REFERENCE_HEADER[12:]
        self.assertEqual(P.ones_complement_checksum(zeroed), 0xb861)

    def test_a_single_flipped_bit_breaks_it(self):
        for i in (0, 5, 12, 19):
            corrupt = bytearray(REFERENCE_HEADER)
            corrupt[i] ^= 0x01
            with self.subTest(byte=i):
                self.assertNotEqual(P.ones_complement_checksum(bytes(corrupt)), 0)

    def test_odd_length_input_is_padded_not_dropped(self):
        # RFC 1071 pads to a 16-bit boundary. Truncating instead would silently
        # ignore a byte, and would do it only for odd-length payloads.
        self.assertEqual(P.ones_complement_checksum(b'\x00'),
                         P.ones_complement_checksum(b'\x00\x00'))


class LogLineParsing(unittest.TestCase):
    """The seam with the C++ patch. If this drifts, we see 'no data'."""

    def test_parses_the_documented_format(self):
        pdu = P.parse_log_line(log_line(b'\x45\x00'))
        self.assertIsNotNone(pdu)
        self.assertEqual(pdu.nac, 0x1bd)
        self.assertEqual(pdu.fmt, 0x16)
        self.assertEqual(pdu.sap, 0x04)
        self.assertEqual(pdu.blks, 6)
        self.assertEqual(pdu.llid, 0x0356eb)
        self.assertEqual(pdu.payload, b'\x45\x00')

    def test_ignores_unrelated_op25_lines(self):
        for line in (
            '09/03/26 21:09:18 [0] tsbk(0x16) sndcp_data_ch: ch1: 14cc ch2: ffff',
            '09/03/26 21:09:18 [0] NAC 0x1bd TSBK: op=3c : 3c 00 00 31',
            '09/03/26 21:09:18 [0] NAC 0x1bd PDU:  non-MBT message ignored',
            '',
        ):
            with self.subTest(line=line[:40]):
                self.assertIsNone(P.parse_log_line(line))

    def test_single_byte_payload_is_accepted(self):
        # The regex must not require a multi-byte payload: a one-block PDU is
        # short, and rejecting it would drop exactly the smallest messages.
        pdu = P.parse_log_line(log_line(b'\xff'))
        self.assertEqual(pdu.payload, b'\xff')

    def test_header_only_pdu_is_captured_not_skipped(self):
        # An SNDCP control PDU can carry no data blocks at all, and since
        # process_blocks started keeping partial decodes, a frame whose only
        # good block was the header reaches us too. The line must still parse;
        # classify() then reports 'empty' rather than pretending it failed.
        line = log_line(b'')
        pdu = P.parse_log_line(line)
        self.assertIsNotNone(pdu, line)
        self.assertEqual(pdu.payload, b'')
        self.assertEqual(pdu.llid, 0x0356eb)
        self.assertEqual(P.classify(pdu).kind, 'empty')

    def test_llid_and_block_counts_match_the_first_real_header(self):
        """Pinned to the actual header decoded off LWIN on 2026-09-04.

        The LLID offset was a GUESS (octets 2-4) until this header arrived and
        showed it had to be 3-5. Pinning the real bytes means the next change to
        LLID_OCTETS has to explain itself against observed data.
        """
        real = bytes.fromhex('76c0000355da840d4802d84e')
        pdu = P.Pdu(nac=0x1bd, fmt=0x16, sap=0x00, blks=0, hdr=real, payload=b'')
        self.assertEqual(pdu.llid, 0x0355da)          # 218586, in the known fleet
        self.assertEqual(pdu.hdr_blks, 4)             # sender claimed 4
        self.assertEqual(pdu.blks, 0)                 # we recovered none
        self.assertEqual(pdu.blocks_lost, 4)
        self.assertEqual(pdu.sap_name, 'unencrypted user data')

    def test_a_response_pdu_does_not_claim_to_have_a_sap(self):
        """Real fmt=03 headers off LWIN reported sap=0c and sap=0d.

        Those are not SAPs -- octet 1 of a response PDU carries response
        class/type/status. Reporting them as "unknown SAP 12" would invent a
        service that does not exist.
        """
        real = bytes.fromhex('230c0003595b800000 06 2c'.replace(' ', ''))
        pdu = P.Pdu(nac=0x1bd, fmt=0x03, sap=0x0c, blks=0, hdr=real, payload=b'')
        self.assertFalse(pdu.sap_valid)
        self.assertIn('not a SAP', pdu.sap_name)
        self.assertEqual(pdu.llid, 0x03595b)

    def test_a_data_pdu_does_have_a_sap(self):
        real = bytes.fromhex('76c00003595b830a48028f91')
        pdu = P.Pdu(nac=0x1bd, fmt=0x16, sap=0x00, blks=0, hdr=real, payload=b'')
        self.assertTrue(pdu.sap_valid)
        self.assertEqual(pdu.sap_name, 'unencrypted user data')

    def test_sap_is_named_not_guessed(self):
        self.assertEqual(P.parse_log_line(log_line(b'\x00', sap=4)).sap_name,
                         'packet data')
        self.assertIn('unknown SAP 42',
                      P.parse_log_line(log_line(b'\x00', sap=42)).sap_name)


class ClearTextIsProvedNotAssumed(unittest.TestCase):

    def test_a_real_ipv4_udp_datagram_is_reported_clear(self):
        pdu = P.parse_log_line(log_line(ipv4_udp()))
        v = P.classify(pdu)
        self.assertEqual(v.kind, 'ipv4')
        self.assertTrue(v.clear)
        self.assertEqual(v.detail['ip']['src'], '10.0.0.5')
        self.assertEqual(v.detail['ip']['udp']['dport'], 4001)

    def test_well_known_ports_are_hinted_but_not_decoded(self):
        v = P.classify(P.parse_log_line(
            log_line(ipv4_udp(sport=51000, dport=4005))))
        self.assertEqual(v.detail['ip']['udp']['hint'], 'ARS (registration)')
        # And the application bytes are handed back untouched -- this module
        # deliberately does not interpret them yet.
        self.assertEqual(v.detail['ip']['udp']['data'], b'hello')

    def test_the_destination_port_names_the_service(self):
        # Both ends of these conversations use known ports, so "which port do
        # we believe" is a real decision. The destination names the service
        # being addressed; taking the source instead labelled an ARS message
        # LRRP, which is how this test came to exist.
        v = P.classify(P.parse_log_line(
            log_line(ipv4_udp(sport=4001, dport=4005))))
        self.assertEqual(v.detail['ip']['udp']['hint'],
                         'ARS (registration) <- LRRP (location)')

    def test_a_source_only_match_still_hints(self):
        v = P.classify(P.parse_log_line(
            log_line(ipv4_udp(sport=4001, dport=51000))))
        self.assertEqual(v.detail['ip']['udp']['hint'], 'LRRP (location)')

    def test_no_known_port_gets_no_hint(self):
        v = P.classify(P.parse_log_line(
            log_line(ipv4_udp(sport=51000, dport=52000))))
        self.assertNotIn('hint', v.detail['ip']['udp'])

    def test_ciphertext_is_not_reported_clear(self):
        # Deterministic pseudo-random bytes: encrypted payload should fail the
        # structural checks, and on the rare occasion it looks IPv4-shaped it
        # must still fail the checksum.
        import random
        rng = random.Random(1)
        for trial in range(500):
            blob = bytes(rng.randrange(256) for _ in range(48))
            v = P.classify(P.parse_log_line(log_line(blob)))
            with self.subTest(trial=trial):
                self.assertFalse(v.clear)

    def test_sap_1_is_believed_without_inspecting_bytes(self):
        # A payload that WOULD pass as clear IPv4, but the system labelled it
        # encrypted user data. The label wins.
        v = P.classify(P.parse_log_line(log_line(ipv4_udp(), sap=1)))
        self.assertFalse(v.clear)
        self.assertIn('SAP 1', v.reason)

    def test_a_corrupted_header_is_not_reported_clear(self):
        good = bytearray(ipv4_udp())
        good[12] ^= 0xff                      # break the source address only
        v = P.classify(P.parse_log_line(log_line(bytes(good))))
        self.assertFalse(v.clear)
        self.assertIn('checksum failed', v.reason)

    def test_truncated_reassembly_is_rejected(self):
        # total_length longer than the bytes we actually have means blocks are
        # missing. Reporting that as clear would claim we read a packet we did
        # not finish reading.
        short = ipv4_udp()[:24]
        v = P.classify(P.parse_log_line(log_line(short)))
        self.assertFalse(v.clear)

    def test_padding_after_the_datagram_is_tolerated(self):
        # PDUs are padded to a block boundary, so trailing bytes are NORMAL and
        # must not invalidate an otherwise good packet.
        v = P.classify(P.parse_log_line(log_line(ipv4_udp() + b'\x00' * 11)))
        self.assertTrue(v.clear)
        self.assertEqual(v.detail['ip']['udp']['data'], b'hello')

    def test_confirmed_blocks_are_found_by_trying_both_readings(self):
        # A confirmed-format PDU: every 12-byte block carries 2 octets of DBSN
        # and CRC9 before its 10 octets of user data. The datagram is only
        # readable once those are stripped, so this pins that classify() finds
        # it without being told which format the header claims.
        dgram = ipv4_udp(data=b'position-report-payload')
        blocks = b''
        for i in range(0, len(dgram), 10):
            blocks += b'\xab\xcd' + dgram[i:i + 10].ljust(10, b'\x00')
        v = P.classify(P.parse_log_line(log_line(blocks)))
        self.assertTrue(v.clear, v.reason)
        self.assertEqual(v.detail['block_format'], 'confirmed')
        self.assertEqual(v.detail['ip']['udp']['data'], b'position-report-payload')

    def test_unconfirmed_blocks_still_read_as_unconfirmed(self):
        # The converse: trying both readings must not relabel a plain payload.
        v = P.classify(P.parse_log_line(log_line(ipv4_udp())))
        self.assertTrue(v.clear)
        self.assertEqual(v.detail['block_format'], 'unconfirmed')

    def test_empty_payload_is_its_own_verdict(self):
        pdu = P.Pdu(nac=0x1bd, fmt=0x16, sap=4, blks=0, hdr=bytes(12), payload=b'')
        self.assertEqual(P.classify(pdu).kind, 'empty')


class ScanOverAWholeLog(unittest.TestCase):

    def test_picks_pdus_out_of_surrounding_noise(self):
        lines = [
            '09/03/26 21:09:18 [0] tsbk(0x14) unhandled: 0x14000114ccffff0356eb0000',
            log_line(ipv4_udp(), llid=0x0356eb),
            '09/03/26 21:09:19 [0] NAC 0x1bd TSBK: op=02 : 02 00',
            log_line(b'\x99' * 40, llid=0x035a32),
        ]
        got = P.scan(lines)
        self.assertEqual(len(got), 2)
        self.assertEqual([p.llid for p, _ in got], [0x0356eb, 0x035a32])
        self.assertEqual([v.clear for _, v in got], [True, False])


if __name__ == '__main__':
    unittest.main()
