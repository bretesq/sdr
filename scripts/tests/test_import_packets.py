#!/usr/bin/env python3
"""Tests for the packet-data importer.

The properties that matter here are the ones whose failure is SILENT: a row
that asserts more than the radio said, a re-import that doubles history, or a
response PDU whose octet 1 gets stored as if it were a service. Each has a test
below, and the fixtures are real frames captured off site 13.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import import_packets as I
import p25_packet as P
import sdr_db

# A real confirmed-data frame (LRRP request) and a real response frame.
RAW_LRRP = (
    '5575f5ff77ff1bdcec1231d9994ac422b762e29e535220e92f2c2b2220ccf62dcd22fd3eb91'
    '732ae323c27e086202f2dd2f4331a2f253224bbc572eff2d7742be3e714eb47b27e7c737265'
    'a7f0ed0e227209daaada0dbad28a8bcccc53dadb28219c6367e7487eeb76f04388571a52fa2'
    '20000000027adbaaaaaaa575d00000000fb64aaaaaaaa5730000000')
RAW_RESPONSE = (
    '5575f5ff77ff1bdcec1231d9994a2522dd12221121222749222799122e622223ff92cc92222'
    '5500000000000')

STAMP = '09/04/26 09:24:08.182374'


def log(raw, stamp=STAMP, rx=12):
    return f'{stamp} [{rx}] NAC 0x1bd PDU raw: bits=0 blocks=0 : {raw}\n'


def write_log(lines):
    fd, path = tempfile.mkstemp(suffix='.log')
    with os.fdopen(fd, 'w') as fh:
        fh.writelines(lines)
    return path


class TimestampParsing(unittest.TestCase):

    def test_op25s_local_stamp(self):
        # Same format import_grants.py parses, so packets and grants land on a
        # comparable clock -- the whole point of storing ts at all.
        self.assertAlmostEqual(I.parse_ts('09/04/26 09:24:08.182374') % 1,
                               0.182374, places=5)

    def test_a_line_with_no_stamp_is_skipped_not_dated_zero(self):
        path = write_log([f'NAC 0x1bd PDU raw: bits=0 blocks=0 : {RAW_LRRP}\n'])
        try:
            rows, stats = I.rows_from([path], None)
            self.assertEqual(rows, [])
            self.assertEqual(stats['no_timestamp'], 1)
        finally:
            os.unlink(path)


class RowContents(unittest.TestCase):

    def _one(self, raw):
        path = write_log([log(raw)])
        try:
            rows, _ = I.rows_from([path], session_id=7)
            self.assertEqual(len(rows), 1)
            return rows[0]
        finally:
            os.unlink(path)

    def test_a_data_pdu_carries_its_ip_and_app_layers(self):
        r = self._one(RAW_LRRP)
        (ts, sess, llid, nac, fmt, sap, claimed, got,
         src, dst, proto, sport, dport, clear, app, kind, payload) = r
        self.assertEqual(sess, 7)
        self.assertEqual(nac, 0x1bd)
        self.assertEqual(fmt, 0x16)
        self.assertEqual(sap, 0x00)
        self.assertEqual((claimed, got), (4, 4))
        self.assertEqual(src, '10.51.1.10')
        self.assertEqual(proto, 17)
        self.assertEqual(dport, 4001)
        self.assertEqual(clear, 1)
        self.assertEqual(app, 'LRRP')
        self.assertEqual(kind, 'triggered location start request')
        self.assertTrue(payload)

    def test_a_response_pdu_stores_no_sap(self):
        # THE SILENT-FAILURE CASE. Octet 1 of a response PDU is response
        # class/type/status, so storing its value would make a query for
        # "SAP 8 traffic" return rows that are nothing of the kind.
        r = self._one(RAW_RESPONSE)
        fmt, sap = r[4], r[5]
        self.assertEqual(fmt, 0x03)
        self.assertIsNone(sap)

    def test_clear_is_zero_when_nothing_validated(self):
        r = self._one(RAW_RESPONSE)
        self.assertEqual(r[13], 0)
        self.assertIsNone(r[14])            # no app protocol claimed either

    def test_a_header_that_fails_crc_produces_no_row(self):
        bad = bytearray(bytes.fromhex(RAW_LRRP))
        bad[20] ^= 0xff
        bad[21] ^= 0xff
        path = write_log([log(bytes(bad).hex())])
        try:
            rows, _ = I.rows_from([path], None)
            self.assertEqual(rows, [], 'an unvalidated header must not become a row')
        finally:
            os.unlink(path)


class Deduplication(unittest.TestCase):

    def test_the_raw_and_decoded_lines_of_one_frame_make_one_row(self):
        # op25 emits both. The decoded one comes from its rate-1/2-only path,
        # so importing both would store the same frame twice -- once correct,
        # once claiming zero recovered blocks.
        pdu = P.parse_raw_line(log(RAW_LRRP))
        decoded = (f'{STAMP} [12] NAC 0x1bd PDU: fmt=16 sap=00 blks=0 hdr='
                   + ' '.join(f'{b:02x}' for b in pdu.hdr) + ' : \n')
        path = write_log([log(RAW_LRRP), decoded])
        try:
            rows, stats = I.rows_from([path], None)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][7], 4, 'must keep the properly decoded one')
        finally:
            os.unlink(path)

    def test_a_decoded_line_with_no_raw_twin_is_still_imported(self):
        # Logs from before the raw dump existed must not be silently dropped.
        pdu = P.parse_raw_line(log(RAW_LRRP))
        decoded = (f'{STAMP} [12] NAC 0x1bd PDU: fmt=16 sap=00 blks=0 hdr='
                   + ' '.join(f'{b:02x}' for b in pdu.hdr) + ' : \n')
        path = write_log([decoded])
        try:
            rows, stats = I.rows_from([path], None)
            self.assertEqual(len(rows), 1)
            self.assertEqual(stats['decoded_only'], 1)
        finally:
            os.unlink(path)


class ReimportIsIdempotent(unittest.TestCase):

    def test_running_twice_does_not_double_the_history(self):
        fd, db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        path = write_log([log(RAW_LRRP), log(RAW_RESPONSE, stamp='09/04/26 09:24:09.000001')])
        argv = sys.argv
        try:
            for _ in range(2):
                sys.argv = ['import_packets.py', path, '--db', db_path]
                self.assertEqual(I.main(), 0)
            db = sqlite3.connect(db_path)
            n = db.execute('SELECT COUNT(*) FROM packets').fetchone()[0]
            db.close()
            self.assertEqual(n, 2, 'the second run must replace, not append')
        finally:
            sys.argv = argv
            os.unlink(path)
            os.unlink(db_path)

    def test_it_only_clears_its_own_time_span(self):
        # A neighbouring capture's rows must survive an import of this one.
        fd, db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        db = sdr_db.connect(db_path)
        db.execute('INSERT INTO packets (ts, clear) VALUES (?, 0)', (1.0,))
        db.commit()
        db.close()
        path = write_log([log(RAW_LRRP)])
        argv = sys.argv
        try:
            sys.argv = ['import_packets.py', path, '--db', db_path]
            self.assertEqual(I.main(), 0)
            db = sqlite3.connect(db_path)
            kept = db.execute('SELECT COUNT(*) FROM packets WHERE ts = 1.0').fetchone()[0]
            db.close()
            self.assertEqual(kept, 1, 'an unrelated capture was deleted')
        finally:
            sys.argv = argv
            os.unlink(path)
            os.unlink(db_path)


class SchemaShape(unittest.TestCase):

    def test_the_table_exists_with_the_columns_the_importer_writes(self):
        fd, db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            db = sdr_db.connect(db_path)
            cols = {r[1] for r in db.execute('PRAGMA table_info(packets)')}
            db.close()
            for needed in ('ts', 'session_id', 'llid', 'nac', 'fmt', 'sap',
                           'blks_claimed', 'blks_recovered', 'src_ip', 'dst_ip',
                           'proto', 'sport', 'dport', 'clear', 'app',
                           'app_kind', 'app_payload'):
                self.assertIn(needed, cols)
        finally:
            os.unlink(db_path)

    def test_clear_defaults_to_zero_rather_than_null(self):
        # A NULL here would make `WHERE clear = 0` miss rows, quietly
        # understating how much is NOT proven cleartext.
        fd, db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            db = sdr_db.connect(db_path)
            db.execute('INSERT INTO packets (ts) VALUES (1.0)')
            self.assertEqual(
                db.execute('SELECT clear FROM packets').fetchone()[0], 0)
            db.close()
        finally:
            os.unlink(db_path)


if __name__ == '__main__':
    unittest.main()
