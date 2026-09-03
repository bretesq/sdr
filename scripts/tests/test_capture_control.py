#!/usr/bin/env python3
"""Tests for the capture container's control server.

build_args() is the security boundary of the whole feature: it is the only
thing standing between an HTTP request and a subprocess argv on a machine with
SDR hardware. Anything that can reach the web app can reach POST /start, so
every case here is really asking "does an injection attempt reach the command
line", not "does validation work" in the abstract.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
from scripts.capture_control import AlreadyRunning, CaptureState, ValidationError, build_args
from unittest import mock
import http.client
import scripts.capture_control as cc
import signal
import threading
import unittest


class BuildArgsTest(unittest.TestCase):
    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "; rm -rf /"})

    def test_rejects_non_integer_duration(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "durationSec": "10800; id"})

    def test_builds_the_documented_invocation(self):
        args = build_args({
            "mode": "multi",
            "ess": True,
            "includeEncrypted": True,
            "durationSec": 10800,
        })
        self.assertEqual(
            args,
            ["--ess", "--include-encrypted", "--pd", "10800"],
        )

    def test_omits_flags_that_were_not_requested(self):
        self.assertEqual(build_args({"mode": "multi"}), [])

    # The four cases above are the brief's documented contract, verbatim.
    # Everything below hardens the same boundary against inputs the brief
    # didn't spell out but that a hostile or merely careless caller could send.

    def test_rejects_non_dict_request(self):
        with self.assertRaises(ValidationError):
            build_args("mode=multi")
        with self.assertRaises(ValidationError):
            build_args(["multi"])
        with self.assertRaises(ValidationError):
            build_args(None)

    def test_rejects_missing_mode(self):
        with self.assertRaises(ValidationError):
            build_args({})

    def test_rejects_unhashable_mode_without_crashing(self):
        # dict.__contains__ / frozenset membership raises TypeError on an
        # unhashable value (a list, a dict) rather than returning False. json
        # can hand us either as the value of "mode", so this must come back
        # as OUR ValidationError, never an uncaught TypeError that would skip
        # straight past the validator.
        with self.assertRaises(ValidationError):
            build_args({"mode": ["multi"]})
        with self.assertRaises(ValidationError):
            build_args({"mode": {"$ne": None}})

    def test_rejects_unknown_fields(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "preset": "pd"})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "extra_flag": "--foo"})

    def test_rejects_non_boolean_ess(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "ess": "true"})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "ess": 1})

    def test_rejects_non_boolean_include_encrypted(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "includeEncrypted": "yes"})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "includeEncrypted": 0})

    def test_rejects_boolean_duration(self):
        # isinstance(True, int) is True in Python -- without an explicit
        # bool exclusion, {"durationSec": true} would sail through as a
        # 1-second capture instead of being rejected as the wrong type.
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "durationSec": True})

    def test_rejects_out_of_range_duration(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "durationSec": 0})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "durationSec": -1})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "durationSec": 10 ** 9})

    def test_only_ess_omits_pd_and_duration(self):
        self.assertEqual(
            build_args({"mode": "multi", "ess": True}),
            ["--ess"],
        )

    def test_no_string_ever_reaches_the_argument_list_unvalidated(self):
        # Every element build_args can possibly emit is drawn from a fixed
        # set of literals it owns (--ess, --include-encrypted, --pd) or is
        # str(int) of a range-checked integer. Assert that directly: nothing
        # in the request's own string values can appear verbatim in the
        # output.
        malicious = "$(rm -rf /)"
        with self.assertRaises(ValidationError):
            build_args({"mode": malicious})


class _FakeProc:
    """A stand-in for subprocess.Popen that never actually spawns anything.

    Only .pid and .poll() are used by CaptureState -- returncode mimics
    poll()'s contract (None while alive, an int once the process has exited),
    so these tests can drive CaptureState's own bookkeeping (the "already
    running" refusal, reaping a self-terminated capture) without a real
    subprocess anywhere in the picture. This is deliberately how /start's 409
    path gets covered at all: build_args()'s tests can never send it a
    well-formed request (that would start a real capture, which this task
    must not do), so CaptureState is exercised directly instead.
    """

    def __init__(self, pid: int = 4242, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


class CaptureStateTest(unittest.TestCase):
    def test_start_refuses_when_a_capture_is_already_running(self):
        state = CaptureState()
        state.process = _FakeProc()
        state.pgid = state.process.pid
        # A live group -- start()'s reap-then-check must not clear this away
        # before hitting the AlreadyRunning check.
        with mock.patch.object(cc.os, "killpg", return_value=None):
            with self.assertRaises(AlreadyRunning):
                state.start({"mode": "multi", "durationSec": 600})

    def test_start_refuses_while_a_stop_is_in_flight(self):
        # stop() clears self.pgid only after its group has actually exited,
        # but sets `stopping` immediately, before it releases the lock to
        # signal and wait -- a concurrent start() must see THAT flag, not
        # just `pgid is not None`, or it could slip in and spawn a second
        # op25 while the first one is still being torn down.
        state = CaptureState()
        state.stopping = True
        with self.assertRaises(AlreadyRunning):
            state.start({"mode": "multi", "durationSec": 600})

    def test_snapshot_reaps_a_capture_whose_whole_group_has_exited(self):
        # A duration-limited run ends without anyone calling stop(), and
        # every member of its process group -- launcher, op25, all eight
        # recorders -- is actually gone. Without reaping, /status would
        # keep reporting a dead pid as running, and the next legitimate
        # /start would get a spurious 409 for a capture that is, in fact,
        # long gone.
        state = CaptureState()
        state.process = _FakeProc(returncode=0)
        state.pgid = state.process.pid
        state.request = {"mode": "multi"}
        state.started_at = 0.0

        with mock.patch.object(cc.os, "killpg", side_effect=ProcessLookupError):
            snapshot = state.snapshot()

        self.assertEqual(snapshot, {"running": False, "pid": None})
        self.assertIsNone(state.process)
        self.assertIsNone(state.pgid)
        self.assertIsNone(state.request)
        self.assertIsNone(state.started_at)

    def test_status_reports_running_when_the_launcher_exited_but_op25_survives(self):
        # THE stale-state bug: the tracked launcher `bash` can exit (crash,
        # or simply reach the end of its script) while op25 and the
        # recorders it forked -- members of the SAME process group, but not
        # bash's dependents once bash itself exits -- are still alive and
        # still holding the HackRFs. Trusting only `self.process`'s own
        # liveness would report "not running" here: /status would lie,
        # /stop would no-op leaving the radios held, and the next /start
        # would spawn a second op25 onto hardware already in use.
        state = CaptureState()
        state.process = _FakeProc(pid=555, returncode=0)  # the launcher already exited
        state.pgid = 555
        state.request = {"mode": "multi", "durationSec": 600}
        state.started_at = 0.0

        # killpg(555, 0) succeeding (no exception) means the kernel still
        # has at least one member of group 555 -- op25 or a recorder,
        # reparented once the launcher exited.
        with mock.patch.object(cc.os, "killpg", return_value=None) as killpg:
            snapshot = state.snapshot()

        killpg.assert_called_once_with(555, 0)
        self.assertEqual(snapshot["running"], True)
        self.assertEqual(snapshot["pid"], 555)
        self.assertIsNotNone(state.pgid)  # NOT cleared -- the group is still alive

    def test_snapshot_never_crashes_with_nothing_running(self):
        self.assertEqual(CaptureState().snapshot(), {"running": False, "pid": None})

    def test_stop_is_a_no_op_when_nothing_is_running(self):
        result = CaptureState().stop()
        self.assertEqual(result, {"stopped": False, "message": "no capture running"})

    def test_stop_signals_the_group_even_after_the_launcher_itself_is_gone(self):
        # stop() must reach op25/recorders via os.killpg(pgid, ...) using the
        # STORED pgid, not by re-deriving it from a (possibly already-gone)
        # self.process -- otherwise a stop() issued after the launcher has
        # exited, but while the group it started is still alive, would have
        # nothing to signal.
        state = CaptureState()
        state.process = _FakeProc(pid=777, returncode=0)
        state.pgid = 777
        state.request = {"mode": "multi"}
        state.started_at = 0.0

        with mock.patch.object(cc.os, "killpg") as killpg, \
             mock.patch.object(cc, "_wait_for_group_exit", return_value=True):
            # snapshot()/stop() both reap first; make the FIRST killpg call
            # (the reap's aliveness check) say "still alive" so stop()
            # actually proceeds to signal, then let the SIGINT call through.
            killpg.return_value = None
            result = state.stop()

        killpg.assert_any_call(777, signal.SIGINT)
        self.assertEqual(result, {"stopped": True, "pid": 777, "forced": False})
        self.assertIsNone(state.pgid)


class ShutdownSignalTest(unittest.TestCase):
    """capture_control.py is PID 2 under tini (init: true), not PID 1 --
    see the module docstring's SIGNAL HANDLING section. That alone gets
    SIGTERM delivered; these tests cover the other half this file owns:
    that receiving it actually runs the same stop ladder as POST /stop
    before the process exits, rather than a bare default-disposition death
    that skips lwin_listen_multi.sh's cleanup trap.
    """

    def setUp(self):
        # _SHUTTING_DOWN is a module-level singleton flag (it must be, so a
        # real second OS signal can see the first invocation already set
        # it) -- clear it before and after each test so one test's shutdown
        # doesn't leave the next test's _handle_shutdown_signal() call a
        # permanent, silent no-op.
        cc._SHUTTING_DOWN.clear()
        self.addCleanup(cc._SHUTTING_DOWN.clear)

    def test_install_shutdown_handlers_wires_sigterm_and_sigint(self):
        prev_term = signal.getsignal(signal.SIGTERM)
        prev_int = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGTERM, prev_term)
        self.addCleanup(signal.signal, signal.SIGINT, prev_int)

        cc.install_shutdown_handlers()

        self.assertIs(signal.getsignal(signal.SIGTERM), cc._handle_shutdown_signal)
        self.assertIs(signal.getsignal(signal.SIGINT), cc._handle_shutdown_signal)

    def test_shutdown_stops_a_running_capture_before_the_process_would_exit(self):
        # This is _handle_shutdown_signal()'s body minus os._exit() (which
        # would kill the test runner) -- see _stop_for_shutdown()'s
        # docstring for why it is split out. If a future edit removed the
        # STATE.stop() call from the shutdown path, this is the test that
        # would catch it.
        fresh = CaptureState()
        fresh.process = _FakeProc(pid=4242, returncode=None)
        fresh.pgid = 4242
        fresh.request = {"mode": "multi"}
        fresh.started_at = 0.0

        with mock.patch.object(cc, "STATE", fresh), \
             mock.patch.object(cc.os, "killpg") as killpg, \
             mock.patch.object(cc, "_wait_for_group_exit", return_value=True):
            cc._stop_for_shutdown()

        killpg.assert_any_call(4242, signal.SIGINT)
        self.assertIsNone(fresh.pgid)

    def test_handle_shutdown_signal_stops_then_exits(self):
        # os._exit is mocked so this test process survives. A shared parent
        # Mock with both calls attached to it is what makes this an actual
        # ORDERING assertion rather than two independent "was it called"
        # checks -- a regression that swapped the two lines (exit before
        # stop, which would tear the process down mid-STATE.stop() and
        # abandon the cleanup ladder partway through) would still pass
        # "both called once" but would fail this.
        manager = mock.Mock()
        with mock.patch.object(cc, "_stop_for_shutdown") as stop_for_shutdown, \
             mock.patch.object(cc.os, "_exit") as os_exit:
            manager.attach_mock(stop_for_shutdown, "stop_for_shutdown")
            manager.attach_mock(os_exit, "os_exit")
            cc._handle_shutdown_signal(signal.SIGTERM, None)

        self.assertEqual(
            manager.mock_calls,
            [mock.call.stop_for_shutdown(), mock.call.os_exit(0)],
        )

    def test_a_second_signal_during_shutdown_does_not_stack_a_second_wait(self):
        # Restored after briefly being removed on the mistaken belief that
        # POST /stop's "concurrent calls are harmless" ruling covered this
        # too -- it does not. Two nested shutdown-signal invocations would
        # stack a second ~10s wait on top of the first (unlike two /stop
        # calls, which both wait out roughly the SAME window), risking
        # Docker's 15s stop_grace_period cutting the second wait short with
        # a SIGKILL -- see _handle_shutdown_signal's docstring. The second
        # signal here must be a complete no-op: no second STATE.stop(), no
        # second os._exit call.
        with mock.patch.object(cc, "_stop_for_shutdown") as stop_for_shutdown, \
             mock.patch.object(cc.os, "_exit") as os_exit:
            cc._handle_shutdown_signal(signal.SIGTERM, None)
            cc._handle_shutdown_signal(signal.SIGINT, None)
        stop_for_shutdown.assert_called_once()
        os_exit.assert_called_once_with(0)


class HttpLayerTest(unittest.TestCase):
    """Exercises the real HTTP layer for the parts of POST /start that live
    in header/body handling itself, not in build_args() or CaptureState: a
    malformed Content-Length, and a body larger than this endpoint should
    ever legitimately need to buffer or wait for.
    """

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cc.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)

    def _post_raw(self, path: str, body: bytes, content_length: object = None):
        """POST `body` to `path`, using `content_length` as the literal
        Content-Length header value when given (so a mismatched or
        non-numeric value can be sent deliberately) instead of len(body)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("POST", path)
            conn.putheader("Content-Type", "application/json")
            conn.putheader(
                "Content-Length",
                str(len(body)) if content_length is None else str(content_length),
            )
            conn.endheaders()
            if body:
                conn.send(body)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_malformed_content_length_is_a_clean_400(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("POST", "/start")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", "not-a-number")
            conn.endheaders()
            resp = conn.getresponse()
            status, body = resp.status, resp.read()
        finally:
            conn.close()
        self.assertEqual(status, 400)
        self.assertIn("Content-Length", body.decode())

    def test_oversized_body_is_rejected_before_being_read(self):
        status, body = self._post_raw(
            "/start", b"{}", content_length=cc.MAX_BODY_BYTES + 1
        )
        self.assertEqual(status, 400)
        self.assertIn("too large", body.decode())

    def test_a_parser_recursion_error_is_a_clean_400_not_a_crash(self):
        # Not reachable via an actual request body within MAX_BODY_BYTES --
        # nesting deep enough to blow json's recursion limit needs roughly
        # 100,000 bracket pairs (measured empirically), far past the 4 KiB
        # cap above. This is defense in depth for if that cap is ever
        # loosened elsewhere, verified here by forcing the failure mode
        # directly rather than by constructing an oversized body.
        with mock.patch.object(cc.json, "loads", side_effect=RecursionError("too deep")):
            status, body = self._post_raw("/start", b"{}")
        self.assertEqual(status, 400)
        self.assertIn("invalid JSON body", body.decode())


if __name__ == "__main__":
    unittest.main()
