#!/usr/bin/env python3
"""Coverage for import_grants.py's two-phase write, and for the launcher
contract that lets it run in the background at all.

WHY THIS FILE EXISTS. lwin_listen_multi.sh's cleanup() used to run the grant
import SYNCHRONOUSLY inside its `trap cleanup INT TERM`, so the import's
runtime was charged against the console's Stop budget -- and on the `pd-all`
preset it blew that budget on every stop, escalating to a SIGKILL that
abandoned the rest of the cleanup trap. The import is now backgrounded. Two
things had to hold for that to be correct rather than merely faster, and both
are asserted here because both are silent when broken:

  1. The import must not hold sdr.db's single write lock for ~15s, because it
     can now overlap the NEXT session's recorders. udp_audio_record.py catches
     `database is locked` and only WARNS, so the .wav survives while the
     `calls` row does not -- a recorded call missing from the console with no
     error anywhere. ImportGrantsWriteLockTest covers the reorder that fixed
     it, and ImportGrantsLinkSemanticsTest covers that the reorder did not
     change which call a grant links to.
  2. The session log must be ROTATED, not TRUNCATED, at startup -- an open
     descriptor follows the inode, so `mv` leaves the in-flight import reading
     a complete file while `: > "$LOG"` would zero it out underneath.
     CleanupBackgroundingTest is the revert detector for that.

Runs entirely against a temporary database -- never the real sdr.db.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import import_grants  # noqa: E402
import sdr_db  # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parent.parent

# op25 stamps local time; parse_ts() is what turns that into an epoch, so the
# fixtures below build `calls.start` values THROUGH parse_ts rather than
# hard-coding epochs. That keeps the tests correct in any timezone, which
# matters because the link window is an absolute-seconds comparison.
BASE_STAMP = "09/03/26 12:00:00.000000"
BASE_EPOCH = import_grants.parse_ts(BASE_STAMP)


def _stamp(offset_sec: float) -> str:
    """A log timestamp `offset_sec` after BASE_STAMP, in op25's own format."""
    import datetime as dt
    return (dt.datetime.fromtimestamp(BASE_EPOCH + offset_sec)
            .strftime('%m/%d/%y %H:%M:%S.%f'))


def _log(*grants: tuple[float, int, str]) -> str:
    """A minimal op25 log carrying just the lines import_grants parses:
    one `set tgid=N, srcaddr=M` per grant, plus a channel/talkgroup pair so
    the frequency column is exercised too."""
    lines = ["ch0: 858.237500 ga0: 17060"]
    for offset, tgid, src in grants:
        lines.append(f"{_stamp(offset)} [LWIN-BR] set tgid={tgid}, srcaddr={src}")
    return "\n".join(lines) + "\n"


class _SpyConn:
    """A recording proxy around a real connection.

    Records, for every statement, the SQL and -- crucially -- whether a write
    transaction was ALREADY OPEN when the statement was issued. That is the
    property under test: python's sqlite3 opens its implicit transaction on
    the first DML statement and holds it until commit, so a link SELECT issued
    with `in_transaction` already True is one running inside the write lock.

    Only the four methods import_grants uses are proxied, deliberately: a
    blanket __getattr__ would let a future change reach the real connection
    through an unrecorded path and quietly stop being observed here.
    """

    def __init__(self, real) -> None:
        self._real = real
        self.statements: list[tuple[str, bool]] = []

    def _record(self, sql: str) -> None:
        self.statements.append((" ".join(sql.split()), self._real.in_transaction))

    def execute(self, sql, params=()):
        self._record(sql)
        return self._real.execute(sql, params)

    def executemany(self, sql, seq):
        seq = list(seq)
        self._record(sql)
        return self._real.executemany(sql, seq)

    def commit(self):
        return self._real.commit()

    def close(self):
        # Deliberately NOT closed: main() closes its connection in a finally,
        # and these tests need the temporary database readable afterwards to
        # assert what was actually written.
        return None


class _ImportGrantsCase(unittest.TestCase):
    """Shared fixture: a temporary sdr.db and a temporary log file, with
    import_grants.connect() redirected at the former."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)  # let sdr_db.connect() create and migrate it
        fd, self.log_path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.db = sdr_db.connect(self.db_path)
        self.addCleanup(self._teardown)

    def _teardown(self) -> None:
        try:
            self.db.close()
        except Exception as exc:  # noqa: BLE001 -- cleanup must not mask a failure
            print(f"test teardown: could not close the temp db: {exc}")
        for path in (self.db_path, self.log_path):
            if os.path.exists(path):
                os.unlink(path)

    def _add_call(self, tgid: int, offset_sec: float, name: str) -> int:
        sdr_db.upsert_call(self.db, file=name, tgid=tgid,
                           start=BASE_EPOCH + offset_sec, dur=2.0)
        self.db.commit()
        return self.db.execute(
            "SELECT id FROM calls WHERE file = ?", (name,)).fetchone()["id"]

    def _run_import(self, log_text: str) -> _SpyConn:
        # main()'s summary is captured rather than left on the suite's stdout:
        # twelve imports' worth of census output would bury the one line that
        # matters when something fails. It is NOT discarded -- on a non-zero
        # return it is printed before the assertion fires, so a failure still
        # shows what the import actually said.
        Path(self.log_path).write_text(log_text)
        spy = _SpyConn(self.db)
        buf = io.StringIO()
        with mock.patch.object(import_grants, "connect", return_value=spy), \
             mock.patch.object(sys, "argv", ["import_grants.py", self.log_path]), \
             contextlib.redirect_stdout(buf):
            rc = import_grants.main()
        self.import_output = buf.getvalue()
        if rc != 0:
            print(self.import_output)
        self.assertEqual(rc, 0, "the import reported failure")
        return spy

    def _grants(self) -> list:
        return self.db.execute(
            "SELECT ts, tgid, src_addr, call_id FROM grants ORDER BY ts").fetchall()


class ImportGrantsLinkSemanticsTest(_ImportGrantsCase):
    """The reorder that shortened the write transaction had to leave link
    semantics byte-identical. LINK_WINDOW_S's own comment says the window is
    deliberately tight ("wider and a busy talkgroup's grants start attaching to
    the wrong recording"), so a silent change here would be worse than the lock
    contention the reorder exists to fix. These are the cases that would move
    if the per-grant "nearest call within the window" query were ever replaced
    with a set-based one."""

    def test_links_a_grant_to_a_call_inside_the_window(self):
        call_id = self._add_call(17060, 1.0, "TG17060_inside.wav")
        self._run_import(_log((0.0, 17060, "1234")))
        rows = self._grants()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["call_id"], call_id)

    def test_does_not_link_a_grant_outside_the_window(self):
        # LINK_WINDOW_S is 4.0s; 10s away must NOT be invented as a link.
        self._add_call(17060, 10.0, "TG17060_outside.wav")
        self._run_import(_log((0.0, 17060, "1234")))
        rows = self._grants()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["call_id"])

    def test_does_not_link_across_talkgroups(self):
        self._add_call(17094, 0.5, "TG17094_other.wav")
        self._run_import(_log((0.0, 17060, "1234")))
        self.assertIsNone(self._grants()[0]["call_id"])

    def test_picks_the_nearest_call_when_two_are_in_the_window(self):
        # This is the `ORDER BY ABS(start - ?) LIMIT 1` clause specifically.
        # Both calls are inside the 4s window, so "any match" would pass; only
        # "nearest match" picks the right one. Deliberately inserted
        # far-then-near, so row order in `calls` cannot be what makes it pass.
        far = self._add_call(17060, 3.5, "TG17060_far.wav")
        near = self._add_call(17060, 0.2, "TG17060_near.wav")
        self._run_import(_log((0.0, 17060, "1234")))
        self.assertEqual(self._grants()[0]["call_id"], near)
        self.assertNotEqual(self._grants()[0]["call_id"], far)

    def test_srcaddr_none_is_stored_as_null_not_dropped(self):
        # tk_p25.py formats a missing source address as the literal "None";
        # requiring \d+ once silently dropped 87% of a census (see GRANT's
        # comment). The grant must still be imported, with a NULL src_addr.
        self._run_import(_log((0.0, 17060, "None")))
        rows = self._grants()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["src_addr"])

    def test_reimporting_the_same_log_replaces_rather_than_duplicates(self):
        # The span DELETE is what makes a re-run idempotent, and phase 2 keeps
        # it atomic with the inserts so a crash can never leave the span
        # half-imported. Chunked commits were rejected for exactly this.
        log = _log((0.0, 17060, "1234"), (1.0, 17094, "5678"))
        self._run_import(log)
        self.assertEqual(len(self._grants()), 2)
        self._run_import(log)
        self.assertEqual(len(self._grants()), 2)


class ImportGrantsWriteLockTest(_ImportGrantsCase):
    """THE REVERT DETECTOR for the two-phase reorder.

    sdr.db is WAL with busy_timeout=5000, so a second writer that waits more
    than 5s gets `database is locked`. The import used to DELETE first and then
    SELECT-then-INSERT per grant, which held the write lock across all ~156,000
    link lookups -- measured at ~8.7s of SELECTs alone on a real 1.96M-line
    log. Every other writer (udp_audio_record.py per call, stt_watch.py per
    transcript, the web app) had that whole window in which to fail.

    These tests assert the SHAPE that fixed it, not a duration, because a
    duration assertion would be a flaky proxy for the thing that actually
    matters: no read is issued while the write transaction is open.
    """

    def test_link_lookups_all_run_before_the_write_transaction_opens(self):
        self._add_call(17060, 0.5, "TG17060_a.wav")
        spy = self._run_import(_log((0.0, 17060, "1"), (1.0, 17060, "2"),
                                    (2.0, 17094, "3")))

        selects = [(sql, in_txn) for sql, in_txn in spy.statements
                   if sql.startswith("SELECT")]
        self.assertEqual(len(selects), 3, "one link lookup per grant, as before")
        for sql, in_txn in selects:
            self.assertFalse(
                in_txn,
                "a link SELECT ran with the write transaction already open -- "
                "that is the shape that held sdr.db's write lock for the whole "
                f"import and starved every other writer: {sql}",
            )

    def test_no_read_is_issued_after_the_delete_opens_the_transaction(self):
        # The same invariant stated positionally, which is how it reads in the
        # source: every SELECT precedes the DELETE. Stated both ways on
        # purpose -- someone could open the transaction with a different DML
        # statement and the in_transaction check above would still be the one
        # that catches it, while this one catches a stray read appended after.
        self._add_call(17060, 0.5, "TG17060_b.wav")
        spy = self._run_import(_log((0.0, 17060, "1"), (1.0, 17060, "2")))

        kinds = [sql.split()[0] for sql, _ in spy.statements]
        self.assertIn("DELETE", kinds)
        delete_at = kinds.index("DELETE")
        self.assertNotIn(
            "SELECT", kinds[delete_at:],
            "no read may be issued once the write transaction is open",
        )
        # And the inserts are one executemany, not N executes. To be precise
        # about what this does and does not buy: measured on a temporary
        # database, 156,794 rows insert in 0.27s via executemany and 0.30s via
        # a per-row loop -- batching is NOT the speed win, the reorder above
        # is (~9.0s of lock held -> ~0.27s). This assertion is here to pin the
        # SHAPE: phase 2 must stay a two-statement span so it cannot quietly
        # regrow into a loop with a read smuggled back inside it.
        self.assertEqual(
            [sql.split()[0] for sql, _ in spy.statements[delete_at:]],
            ["DELETE", "INSERT"],
            "phase 2 must be exactly DELETE then one executemany INSERT",
        )

    def test_the_rows_written_by_executemany_are_still_complete(self):
        # Batching the inserts must not drop or reorder columns. Asserted
        # against the values, not the SQL, so a mismatched parameter order
        # would fail here rather than in production.
        call_id = self._add_call(17060, 0.5, "TG17060_c.wav")
        self._run_import(_log((0.0, 17060, "4242")))
        row = self.db.execute(
            "SELECT ts, tgid, src_addr, freq, call_id FROM grants").fetchone()
        self.assertAlmostEqual(row["ts"], BASE_EPOCH, delta=0.01)
        self.assertEqual(row["tgid"], 17060)
        self.assertEqual(row["src_addr"], 4242)
        # From the `ch0: 858.237500 ga0: 17060` line in the fixture.
        self.assertEqual(row["freq"], 858237500)
        self.assertEqual(row["call_id"], call_id)


class CleanupBackgroundingTest(unittest.TestCase):
    """The launcher-side half of the contract, checked against the shell
    source the way PresetTest in test_capture_control.py checks its own
    cross-file assumptions.

    Neither of these can be caught by running the import: they are properties
    of WHEN and HOW cleanup() invokes it, and both fail silently.
    """

    def setUp(self) -> None:
        self.sh = (SCRIPTS_DIR / "lwin_listen_multi.sh").read_text()
        # Comment lines stripped out for the ordering check below. This is not
        # tidiness: BOTH the rotation statement and the truncation statement
        # are also QUOTED in the comments around them (the rotation comment
        # says "This line used to be `: > \"$LOG\"`", and cleanup()'s
        # backgrounding comment warns against restoring it). Searching the raw
        # text finds those quotations first and the ordering assertion becomes
        # meaningless -- which is exactly how the first draft of this test
        # failed. Only executable lines can carry the invariant.
        self.sh_code = "\n".join(
            line for line in self.sh.splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_the_grant_import_is_backgrounded_and_detached(self):
        # `setsid` puts it in its own process group so a group SIGKILL cannot
        # take it; `&` is what keeps bash from waiting on it inside the trap
        # and so keeps its runtime off the console's Stop budget. Both are
        # required: setsid alone was the old, synchronous shape.
        m = re.search(r"^\s*setsid python3 [^\n]*import_grants\.py[^\n]*$",
                      self.sh_code, re.M)
        self.assertIsNotNone(
            m, "cleanup() must still invoke import_grants.py via setsid")
        line = m.group(0)
        self.assertTrue(
            line.rstrip().endswith("&"),
            "the grant import must be BACKGROUNDED. Without the trailing `&` "
            "bash blocks inside `trap cleanup INT TERM` until the import "
            "finishes, which charges its runtime (~20-25s on a 3-hour pd-all "
            "session) against the console's Stop budget and escalates the stop "
            f"ladder to a SIGKILL that abandons this very trap. Got: {line}",
        )
        self.assertIn(
            '>> "$IMPORT_LOG" 2>&1', line,
            "a backgrounded import outlives the launcher, so its summary AND "
            "its failures must go to IMPORT_LOG -- on stdout they would arrive "
            "after this script exited, attributable to nothing. 2>&1 is what "
            "keeps a failure from vanishing silently.",
        )

    def test_the_session_log_is_rotated_before_it_is_truncated(self):
        # THE REVERT DETECTOR for backgrounding's safety argument. An in-flight
        # import holds an open descriptor on the session log's INODE. `mv`
        # renames that inode and the following `: > "$LOG"` creates a new one,
        # so the import keeps reading a complete, stable file. Restoring
        # `: > "$LOG"` in place of the `mv` would zero the file out from under
        # the import -- and it would fail SILENTLY, reporting a plausible small
        # grant count rather than an error.
        mv_at = self.sh_code.find('mv "$LOG" "$ROTATED"')
        self.assertNotEqual(
            mv_at, -1,
            "the startup path must ROTATE the session log (`mv \"$LOG\" "
            "\"$ROTATED\"`). Truncating the live inode instead destroys both "
            "the previous session's CIPHERTXT/ESS evidence (see the rotation "
            "comment, and commit e741d6f) and any grant import still reading "
            "it.",
        )
        truncate_at = self.sh_code.find(': > "$LOG"')
        self.assertNotEqual(truncate_at, -1)
        self.assertLess(
            mv_at, truncate_at,
            "the rotation must come BEFORE the truncation: the `: >` has to "
            "land on the brand-new inode the `mv` left behind, never on the "
            "one an in-flight grant import is still reading.",
        )

    def test_every_import_entry_carries_a_token_unique_to_that_import(self):
        # THE FIX FOR "a header with no `imported` line is a failed import".
        # Every header used to be the identical string -- `$LOG` is a constant
        # path, not a session identity -- and cleanup()'s own comment says the
        # import "can still be running when the NEXT capture starts". So two
        # imports append to one file and session A's summary lands under
        # session B's header, making a reader conclude B succeeded when B
        # failed. Grouping needs a token ON THE LINES, so the launcher must
        # mint one and hand it to the import.
        m = re.findall(r"^\s*IMPORT_TAG=(.+)$", self.sh_code, re.M)
        self.assertEqual(
            len(m), 1,
            "cleanup() must mint exactly one per-import token (IMPORT_TAG); "
            "without it results/grant_import.log cannot attribute an entry to "
            "a session at all",
        )
        self.assertIn(
            "$$", m[0],
            "the token must include something unique to this launcher run. "
            f"Got {m[0]}, which two concurrent imports could share.",
        )

        # The header printf spans several physical lines. Backslash
        # continuations are folded first so the assertions below see the whole
        # STATEMENT -- matching one physical line would find the format string
        # and miss the arguments, which is where the token actually is.
        joined = re.sub(r"\\\n\s*", " ", self.sh_code)
        header = re.search(r'^\s*printf .*BEGIN.*$', joined, re.M)
        self.assertIsNotNone(
            header, "the header line must carry BEGIN plus the token, so a "
                    "reader has something to grep for")
        self.assertIn(
            '"$IMPORT_TAG"', header.group(0),
            "the header must carry the SAME token the import stamps its lines "
            f"with, or the two cannot be correlated. Got: {header.group(0)}",
        )

        run = re.search(r"^\s*setsid python3 [^\n]*import_grants\.py[^\n]*$",
                        self.sh_code, re.M)
        self.assertIsNotNone(run)
        self.assertIn(
            '--tag "$IMPORT_TAG"', run.group(0),
            "the import must be told the token the header announced. Without "
            "--tag its output is untagged and interleaves inseparably with a "
            f"concurrent import's. Got: {run.group(0)}",
        )

    def test_the_import_log_path_is_defined_once_and_under_results(self):
        # cleanup() references $IMPORT_LOG; if the assignment were ever dropped
        # the redirect would silently write to a file named "" and the output
        # would be lost -- the failure mode the redirect exists to prevent.
        m = re.findall(r"^IMPORT_LOG=(\S+)$", self.sh_code, re.M)
        self.assertEqual(len(m), 1, "IMPORT_LOG must be assigned exactly once")
        self.assertEqual(m[0], "$R/results/grant_import.log")


class TaggedOutputTest(unittest.TestCase):
    """The import's half of the attribution contract.

    Run as a real SUBPROCESS with stderr merged into stdout, because that is
    exactly the shape cleanup() uses (`>> "$IMPORT_LOG" 2>&1`) and the two
    properties under test are properties of that shape: a block-buffered
    stdout racing an unbuffered stderr, and output surviving up to the moment
    the process stops. Calling main() in-process would prove neither.

    No database is touched: --dry-run reaches every census line and returns
    before connect() is called.
    """

    TAG = "2026-09-03T12:00:00-05:00#4242"

    def _run(self, *args: str) -> tuple[int, list[str]]:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "import_grants.py"), *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=60,
        )
        return proc.returncode, proc.stdout.splitlines()

    def test_every_line_carries_the_tag_so_concurrent_imports_stay_separable(self):
        # The launcher appends two imports' output to ONE file. If only the
        # header were tagged, session A's summary under session B's header
        # would still read as B's -- which is the confident wrong answer this
        # whole mechanism exists to stop producing.
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write(_log((0.0, 17060, "1234"), (1.0, 17060, "None")))
            log_path = fh.name
        self.addCleanup(os.unlink, log_path)

        rc, lines = self._run("--tag", self.TAG, "--dry-run", log_path)
        self.assertEqual(rc, 0, "\n".join(lines))
        self.assertGreater(len(lines), 3, "expected the census output")
        for line in lines:
            self.assertTrue(
                line.startswith(f"[{self.TAG}] "),
                f"every line must carry the tag, or it cannot be attributed "
                f"to an import when two interleave. Got: {line!r}",
            )

    def test_a_clean_run_is_closed_by_an_END_line_carrying_its_status(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write(_log((0.0, 17060, "1234")))
            log_path = fh.name
        self.addCleanup(os.unlink, log_path)

        rc, lines = self._run("--tag", self.TAG, "--dry-run", log_path)
        self.assertEqual(rc, 0)
        self.assertEqual(
            lines[-1], f"[{self.TAG}] === END {self.TAG} exit 0",
            "an entry must be CLOSED. Absence of the END line is what now "
            "distinguishes 'killed before it could report' (container "
            "teardown) from 'ran and failed', which used to look identical",
        )

    def test_a_failure_reports_its_reason_and_a_non_zero_END(self):
        rc, lines = self._run("--tag", self.TAG, "/no/such/op25.log")
        self.assertEqual(rc, 1)
        # The reason, tagged, so it is attributable to THIS import even when
        # another import's summary lands between the header and this line.
        self.assertTrue(
            any(line.startswith(f"[{self.TAG}] cannot read /no/such/op25.log")
                for line in lines),
            f"the failure reason must be tagged too. Got: {lines}",
        )
        self.assertEqual(lines[-1], f"[{self.TAG}] === END {self.TAG} exit 1")

    def test_an_untagged_run_is_unchanged(self):
        # --tag is opt-in. `python3 scripts/import_grants.py results/x.log` is
        # documented in this module's own docstring and run by hand; it must
        # not start emitting bracket prefixes and END lines at a human.
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write(_log((0.0, 17060, "1234")))
            log_path = fh.name
        self.addCleanup(os.unlink, log_path)

        rc, lines = self._run("--dry-run", log_path)
        self.assertEqual(rc, 0)
        self.assertTrue(lines[0].startswith("log "), lines[0])
        self.assertFalse(any("=== END" in line for line in lines), lines)


if __name__ == "__main__":
    unittest.main()
