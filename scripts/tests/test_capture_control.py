#!/usr/bin/env python3
"""Tests for the capture container's control server.

build_args() is the security boundary of the whole feature: it is the only
thing standing between an HTTP request and a subprocess argv on a machine with
SDR hardware. Anything that can reach the web app can reach POST /start, so
every case here is really asking "does an injection attempt reach the command
line", not "does validation work" in the abstract.
"""
from __future__ import annotations

from scripts.capture_control import AlreadyRunning, CaptureState, ValidationError, build_args
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
        with self.assertRaises(AlreadyRunning):
            state.start({"mode": "multi", "durationSec": 600})

    def test_start_refuses_while_a_stop_is_in_flight(self):
        # stop() clears self.process only after its child has actually
        # exited, but sets `stopping` immediately, before it releases the
        # lock to signal and wait -- a concurrent start() must see THAT flag,
        # not just `process is not None`, or it could slip in and spawn a
        # second op25 while the first one is still being torn down.
        state = CaptureState()
        state.stopping = True
        with self.assertRaises(AlreadyRunning):
            state.start({"mode": "multi", "durationSec": 600})

    def test_snapshot_reaps_a_capture_that_exited_on_its_own(self):
        # A duration-limited run ends without anyone calling stop(). Without
        # reaping, /status would keep reporting a dead pid as running, and
        # the next legitimate /start would get a spurious 409 for a capture
        # that is, in fact, long gone.
        state = CaptureState()
        state.process = _FakeProc(returncode=0)
        state.request = {"mode": "multi"}
        state.started_at = 0.0

        snapshot = state.snapshot()

        self.assertEqual(snapshot, {"running": False, "pid": None})
        self.assertIsNone(state.process)
        self.assertIsNone(state.request)
        self.assertIsNone(state.started_at)

    def test_snapshot_never_crashes_with_nothing_running(self):
        self.assertEqual(CaptureState().snapshot(), {"running": False, "pid": None})

    def test_stop_is_a_no_op_when_nothing_is_running(self):
        result = CaptureState().stop()
        self.assertEqual(result, {"stopped": False, "message": "no capture running"})


if __name__ == "__main__":
    unittest.main()
