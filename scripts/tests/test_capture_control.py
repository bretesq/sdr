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
from pathlib import Path
from scripts.capture_control import AlreadyRunning, CaptureState, ValidationError, build_args
from unittest import mock
import ast
import http.client
import re
import scripts.capture_control as cc
import scripts.make_multirx_cfg as mrx
import signal
import threading
import unittest

# The repo's scripts/ directory, resolved from THIS file rather than from the
# working directory, so the cross-file checks in PresetTest read the same
# make_whitelist.py and lwin_listen_multi.sh regardless of where the suite is
# run from (`python3 -m unittest discover -s scripts/tests` from the repo root
# is only one of the ways this runs).
SCRIPTS_DIR = Path(__file__).resolve().parent.parent

# The repo root, for the same reason: StopLadderTimingTest's nesting checks
# read docker-compose.yml and server/utils/processes.ts, neither of which is
# under scripts/, so SCRIPTS_DIR.parent is the anchor they need.
REPO_ROOT = SCRIPTS_DIR.parent


def _compose_service_block(compose: str, service: str) -> str:
    """The lines of docker-compose.yml belonging to ONE service.

    Deliberately a text slice rather than a YAML parse: this repo carries no
    yaml dependency for the test suite, and the property being checked is a
    property of a two-space-indented block under `services:`, which the
    compose file's own fixed formatting makes unambiguous.

    Raises rather than returning '' when the service is absent. An empty slice
    would make every assertion downstream vacuous, which is the exact failure
    mode -- a check that quietly stops checking -- this helper was added to
    remove.
    """
    lines = compose.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line == f"  {service}:"), None)
    if start is None:
        raise AssertionError(
            f"docker-compose.yml has no `  {service}:` service. If the file "
            f"was restructured, re-anchor this helper -- do not let the "
            f"checks that use it read an empty slice.")
    # Ends at the next service (same two-space indent), or at EOF. `capture`
    # is currently last, so the EOF case is the live one and must work.
    end = next((i for i in range(start + 1, len(lines))
                if lines[i][:3].strip() and not lines[i].startswith("   ")),
               len(lines))
    return "\n".join(lines[start:end])


class AddTalkgroupsTest(unittest.TestCase):
    """`addTalkgroups` is the only field whose value reaches argv as a
    caller-supplied string, so it gets the strictest checks in this file."""

    BASE = {"mode": "multi", "preset": "pd-all", "durationSec": 3600}

    def test_it_reaches_argv_as_its_own_token(self):
        args, _ = build_args({**self.BASE, "addTalkgroups": "20000,5080"})
        self.assertIn("--add-tg", args)
        self.assertEqual(args[args.index("--add-tg") + 1], "20000,5080")

    def test_the_value_is_rebuilt_from_parsed_integers(self):
        # Re-emitted from ints, not echoed from the caller's string, so a
        # pattern that somehow satisfied the regex still cannot survive as
        # written.
        args, _ = build_args({**self.BASE, "addTalkgroups": "007,20000"})
        self.assertEqual(args[args.index("--add-tg") + 1], "7,20000")

    def test_injection_attempts_are_refused(self):
        for bad in ("20000; rm -rf /", "20000 && id", "$(id)", "`id`",
                    "20000|cat /etc/passwd", "--tg", "20000 --n-voice-800 99"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    build_args({**self.BASE, "addTalkgroups": bad})

    def test_non_numeric_and_empty_are_refused(self):
        for bad in ("", "abc", "20000,", ",20000", "20000,,5080", "20000, 5080"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    build_args({**self.BASE, "addTalkgroups": bad})

    def test_a_non_string_is_refused_rather_than_coerced(self):
        for bad in (20000, ["20000"], {"tg": 1}, True, None.__class__):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    build_args({**self.BASE, "addTalkgroups": bad})

    def test_an_absurd_id_is_refused(self):
        # Talkgroup ids are at most 7 digits on this system; a longer run is a
        # typo or a probe, not a talkgroup.
        with self.assertRaises(ValidationError):
            build_args({**self.BASE, "addTalkgroups": "12345678"})

    def test_too_many_ids_are_refused(self):
        many = ",".join(str(i) for i in range(1, cc.MAX_ADD_TALKGROUPS + 2))
        with self.assertRaises(ValidationError):
            build_args({**self.BASE, "addTalkgroups": many})

    def test_omitting_it_changes_nothing(self):
        args, _ = build_args(self.BASE)
        self.assertNotIn("--add-tg", args)


class BuildArgsTest(unittest.TestCase):
    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "; rm -rf /"})

    def test_rejects_non_integer_duration(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "durationSec": "10800; id"})

    def test_builds_the_documented_invocation(self):
        args, session_id = build_args({
            "mode": "multi",
            "ess": True,
            "includeEncrypted": True,
            "durationSec": 10800,
        })
        self.assertEqual(
            args,
            ["--ess", "--include-encrypted", "--pd", "10800"],
        )
        self.assertIsNone(session_id)

    def test_include_partial_reaches_the_whitelist_builder(self):
        """The flag that decides whether dispatch gets recorded at all.

        It was absent from ALLOWED_FIELDS while the web layer listed it as
        unsupported, so a caller asking for partial talkgroups over the
        delegated path got a capture that silently skipped them -- including
        BRPD Dispatch 1-4 and the Sheriff dispatch channels.
        """
        args, _ = build_args({"mode": "multi", "preset": "pd-all",
                              "includePartial": True, "durationSec": 86400})
        self.assertIn("--include-partial", args)

    def test_include_partial_and_encrypted_are_independent(self):
        args, _ = build_args({"mode": "multi", "includePartial": True})
        self.assertIn("--include-partial", args)
        self.assertNotIn("--include-encrypted", args)

    def test_omits_flags_that_were_not_requested(self):
        args, session_id = build_args({"mode": "multi"})
        self.assertEqual(args, [])
        self.assertIsNone(session_id)

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
        # `preset` used to be this test's first example of an unknown field.
        # It is an ACCEPTED field now (see PresetTest below), so the example
        # was swapped for two that are still genuinely outside ALLOWED_FIELDS
        # -- one plausible-looking (the launcher really does have a --tag
        # selector; this endpoint deliberately does not expose it) and one
        # obviously hostile.
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "tag": "Law Dispatch"})
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

    def test_rejects_non_boolean_include_partial(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "includePartial": "yes"})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "includePartial": 0})

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
        args, _ = build_args({"mode": "multi", "ess": True})
        self.assertEqual(args, ["--ess"])

    # --- sessionId ---------------------------------------------------------
    # Never becomes an argv token (see build_args()'s own docstring); these
    # tests cover its validation, which goes through this same function.

    def test_rejects_non_integer_session_id(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "sessionId": "42"})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "sessionId": 4.2})

    def test_rejects_boolean_session_id(self):
        # isinstance(True, int) is True in Python -- without an explicit bool
        # exclusion, {"sessionId": true} would sail through as session_id=1.
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "sessionId": True})

    def test_rejects_non_positive_session_id(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "sessionId": 0})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "sessionId": -5})

    def test_rejects_out_of_range_session_id(self):
        # final-review.md I4: sessionId had a floor (MIN_SESSION_ID) but no
        # ceiling. An absurdly large value reached SDR_SESSION_ID in the
        # child environment and then sdr_db.upsert_call()'s 64-bit SQLite
        # column, raising OverflowError there instead of being rejected here
        # -- a silent corpus outage for the whole session. This asserts the
        # boundary itself, not the downstream OverflowError, because the
        # fix's whole point is that build_args() must refuse it before any
        # of that happens.
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "sessionId": cc.MAX_SESSION_ID + 1})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "sessionId": 10 ** 30})

    def test_accepts_session_id_at_the_max_boundary(self):
        args, session_id = build_args({"mode": "multi", "sessionId": cc.MAX_SESSION_ID})
        self.assertEqual(session_id, cc.MAX_SESSION_ID)
        self.assertEqual(args, [])

    def test_accepts_a_valid_session_id(self):
        args, session_id = build_args({"mode": "multi", "sessionId": 42})
        self.assertEqual(session_id, 42)
        self.assertEqual(args, [])  # sessionId never contributes an argv token

    def test_session_id_defaults_to_none_when_omitted(self):
        _, session_id = build_args({"mode": "multi"})
        self.assertIsNone(session_id)

    # --- nVoice700 / nVoice800 ----------------------------------------------
    # Receiver-count overrides for lwin_listen_multi.sh's own
    # --n-voice-700/--n-voice-800 flags. Bounds mirror
    # server/api/listen/start.post.ts's MAX_VOICE exactly (see this module's
    # own MIN_VOICE/MAX_VOICE comment for why).

    def test_rejects_non_integer_n_voice(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "nVoice700": "3; rm -rf /"})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "nVoice800": 4.2})

    def test_rejects_boolean_n_voice(self):
        # isinstance(True, int) is True in Python -- without an explicit bool
        # exclusion, {"nVoice700": true} would otherwise sail through as
        # n_voice=1.
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "nVoice700": True})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "nVoice800": True})

    def test_rejects_out_of_range_n_voice(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "nVoice700": 0})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "nVoice700": -1})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "nVoice800": cc.MAX_VOICE + 1})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "nVoice800": 10 ** 9})

    def test_accepts_n_voice_at_the_boundaries_and_emits_the_flags(self):
        args, _ = build_args({
            "mode": "multi", "nVoice700": cc.MIN_VOICE, "nVoice800": cc.MAX_VOICE,
        })
        self.assertEqual(
            args,
            ["--n-voice-700", str(cc.MIN_VOICE), "--n-voice-800", str(cc.MAX_VOICE)],
        )

    def test_n_voice_flags_omitted_when_not_requested(self):
        args, _ = build_args({"mode": "multi"})
        self.assertNotIn("--n-voice-700", args)
        self.assertNotIn("--n-voice-800", args)

    def test_n_voice_flags_land_before_the_positional_duration(self):
        # --pd/duration must stay LAST in argv (build_args()'s own docstring
        # and the comment above the durationSec block explain why: the bare
        # number after --pd has no flag of its own and falls through to
        # lwin_listen_multi.sh's `*)` catch-all). Assert that directly rather
        # than trusting field declaration order to keep it true.
        args, _ = build_args({
            "mode": "multi", "nVoice700": 3, "nVoice800": 7, "durationSec": 600,
        })
        self.assertEqual(args[-2:], ["--pd", "600"])

    def test_no_string_ever_reaches_the_argument_list_unvalidated(self):
        # Every element build_args can possibly emit is drawn from a fixed
        # set of literals it owns (--ess, --include-encrypted, and
        # PRESET_ARGV's values) or is str(int) of a range-checked integer.
        # Assert that directly: nothing in the request's own string values can
        # appear verbatim in the output.
        malicious = "$(rm -rf /)"
        with self.assertRaises(ValidationError):
            build_args({"mode": malicious})
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "preset": malicious, "durationSec": 600})


class PresetTest(unittest.TestCase):
    """`preset` is the only accepted field whose value is a NAME.

    Every other field this endpoint takes is a boolean, an integer, or a mode
    that never reaches argv at all, so `preset` is the single place where a
    caller-supplied string sits closest to a command line on a machine with
    SDR hardware. These tests exist to pin the property that keeps that safe:
    the string is a LOOKUP KEY into capture_control.PRESET_ARGV and the emitted
    tokens come from that table's fixed values, so a name outside the table
    produces a refusal rather than a token.
    """

    # The nine presets, spelled out here rather than read from PRESET_ARGV, so
    # this test fails if a preset is silently dropped from the table as well as
    # if one is silently added. Deriving the expectation from the thing under
    # test would make both changes invisible.
    EXPECTED_ARGV = {
        "pd":          ["--pd"],
        "pd-all":      ["--pd-all"],
        "fire":        ["--fire"],
        "fire-all":    ["--fire-all"],
        "ems":         ["--ems"],
        "interop":     ["--interop"],
        "schools":     ["--preset", "schools"],
        "publicworks": ["--preset", "publicworks"],
        "all":         ["--preset", "all"],
    }

    def test_every_preset_maps_to_its_expected_argv(self):
        for preset, expected in self.EXPECTED_ARGV.items():
            with self.subTest(preset=preset):
                args, _ = build_args({
                    "mode": "multi", "preset": preset, "durationSec": 600,
                })
                # The preset tokens, then the bare duration LAST -- see
                # build_args()'s comment on why the number's position is
                # load-bearing for lwin_listen_multi.sh's argument loop.
                self.assertEqual(args, expected + ["600"])

    def test_the_table_covers_exactly_the_nine_presets(self):
        self.assertEqual(sorted(cc.PRESET_ARGV), sorted(self.EXPECTED_ARGV))

    def test_defaults_to_pd_when_no_preset_is_given(self):
        # The whole point of the default: a caller written before this field
        # existed must keep getting the identical capture it always got.
        self.assertEqual(cc.DEFAULT_PRESET, "pd")
        args, _ = build_args({"mode": "multi", "durationSec": 10800})
        self.assertEqual(args, ["--pd", "10800"])

    def test_rejects_a_preset_outside_the_allowlist(self):
        for bad in ("PD", "pd ", "police", "", "pd-all-all"):
            with self.subTest(preset=bad), self.assertRaises(ValidationError):
                build_args({"mode": "multi", "preset": bad, "durationSec": 600})

    def test_rejects_an_injection_attempt_instead_of_forwarding_it(self):
        # The failure this whole design prevents: a shell metacharacter, an
        # extra flag, or an argument smuggled in as the "preset" reaching argv.
        for bad in (
            "pd; rm -rf /",
            "pd --tg 1,2,3",
            "$(id)",
            "--all-areas",
            "../../etc/passwd",
        ):
            with self.subTest(preset=bad), self.assertRaises(ValidationError):
                build_args({"mode": "multi", "preset": bad, "durationSec": 600})

    def test_rejects_a_non_string_preset_without_crashing(self):
        # Same unhashable-value trap `mode` has: `x in a_dict` hashes x, so a
        # list or dict here would be a TypeError (a crash) rather than a
        # ValidationError (a rejection) without the isinstance check first.
        for bad in (["pd"], {"$ne": None}, 7, True, 4.2):
            with self.subTest(preset=bad), self.assertRaises(ValidationError):
                build_args({"mode": "multi", "preset": bad, "durationSec": 600})

    def test_preset_without_a_duration_is_refused_not_ignored(self):
        # Emitting it would start an UNBOUNDED capture on a wider whitelist --
        # strictly more than this endpoint could ever do before. Dropping it
        # would run `pd` for a caller who asked for something else, invisibly.
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "preset": "fire-all"})

    def test_preset_composes_with_the_other_flags_and_stays_before_the_duration(self):
        args, _ = build_args({
            "mode": "multi", "ess": True, "includeEncrypted": True,
            "nVoice700": 3, "nVoice800": 7,
            "preset": "schools", "durationSec": 10800,
        })
        self.assertEqual(args, [
            "--ess", "--include-encrypted",
            "--n-voice-700", "3", "--n-voice-800", "7",
            "--preset", "schools", "10800",
        ])

    def test_emitted_tokens_are_never_the_callers_object(self):
        # Belt-and-braces on the lookup itself: `PRESET_ARGV[preset]` must not
        # be `("--preset", preset)`. Passing a str SUBCLASS that remembers it
        # was the caller's shows the difference -- an implementation that
        # forwarded the key would put this exact object in argv, while the
        # lookup puts capture_control's own plain str there instead.
        class CallerString(str):
            pass

        args, _ = build_args({
            "mode": "multi", "preset": CallerString("schools"), "durationSec": 600,
        })
        self.assertEqual(args, ["--preset", "schools", "600"])
        for token in args:
            self.assertNotIsInstance(token, CallerString)

    def test_preset_names_match_make_whitelist_pys_own_presets(self):
        """PRESET_ARGV's keys must be names make_whitelist.py understands.

        make_whitelist.py is what actually turns a preset name into a
        whitelist, and it passes its own `--preset` through argparse's
        `choices=`, so a name this table emits that that dict does not have is
        not a subtle bug -- it is a capture that dies at whitelist-build time
        with the radios already claimed. Parsed out of the source with `ast`
        rather than imported because that script builds its ArgumentParser and
        reads the reference DB at module scope: importing it here would run it.
        """
        source = (SCRIPTS_DIR / "make_whitelist.py").read_text()
        presets_dict = None
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "PRESETS" for t in node.targets
            ):
                presets_dict = node.value
                break
        self.assertIsInstance(
            presets_dict, ast.Dict,
            "make_whitelist.py no longer has a module-level PRESETS dict literal; "
            "this cross-check needs updating, not deleting",
        )
        names = {k.value for k in presets_dict.keys}
        self.assertEqual(sorted(cc.PRESET_ARGV), sorted(names))

    def test_every_emitted_flag_is_one_the_launcher_accepts(self):
        """The flags in PRESET_ARGV must exist in lwin_listen_multi.sh's own
        argument loop.

        That script ends its `case` with `-*) exit 1`, so an unknown flag is a
        capture that refuses to start rather than one that ignores an option.
        Nothing else in this repo checks the two files against each other.
        """
        launcher = (SCRIPTS_DIR / "lwin_listen_multi.sh").read_text()
        for preset, argv in cc.PRESET_ARGV.items():
            flag = argv[0]
            with self.subTest(preset=preset, flag=flag):
                self.assertRegex(
                    launcher,
                    rf"(?m)^\s*{re.escape(flag)}\)",
                    f"lwin_listen_multi.sh has no `{flag})` case",
                )


class VoiceChannelBudgetTest(unittest.TestCase):
    """MAX_VOICE is a THREE-file agreement, and two of the three were prose.

    This module's MIN_VOICE/MAX_VOICE comment says its bound "mirror[s]
    server/api/listen/start.post.ts's MAX_VOICE exactly (both must agree: this
    is the same physical launcher, lwin_listen_multi.sh, reached by two
    different front doors)", and both comments justify the value 8 by claiming
    it keeps the UDP port block inside 23460-23492.

    Neither claim was checked anywhere. `grep MAX_VOICE scripts/tests/` found
    only uses of cc.MAX_VOICE -- unlike the two nesting-invariant tests in
    StopLadderTimingTest, which genuinely parse the other file. So the console
    could accept an nVoice the container refused (or worse, the reverse), and
    raising either bound could push the port block past its window with
    nothing failing. Both are asserted here, in the pattern
    test_the_client_stop_timeout_nests_outside_the_ladder established.
    """

    def test_the_two_front_doors_agree_on_max_voice(self):
        ts = (REPO_ROOT / "server" / "api" / "listen" / "start.post.ts").read_text()
        # Anchored to a whole line and asserted to be unique, so this cannot
        # start reading some other constant that happens to contain the name
        # -- the exact defect the compose grace-period check below was fixed
        # for.
        matches = re.findall(r"^const MAX_VOICE = (\d+)$", ts, re.M)
        self.assertEqual(
            len(matches), 1,
            "expected exactly one top-level `const MAX_VOICE = N` in "
            "start.post.ts; if it moved or gained a sibling, re-anchor this "
            "check rather than relaxing it",
        )
        self.assertEqual(
            int(matches[0]), cc.MAX_VOICE,
            f"start.post.ts caps nVoice at {matches[0]} and this module caps "
            f"it at {cc.MAX_VOICE}. They reach the SAME launcher, so a "
            f"disagreement means a request the console accepts is refused by "
            f"the container (or the reverse), with the 400 quoting a bound "
            f"the other half does not hold.",
        )

    def test_max_voice_keeps_the_udp_block_inside_its_window(self):
        # The reason the bound is 8 and not 12, checked against the generator
        # that actually lays the ports out rather than against the sentence in
        # this module's comment. One control channel plus MAX_VOICE per leg
        # plus the pinned SNDCP data receivers, two ports apart:
        #
        # The data count is read off the generator's own leg definitions rather
        # than written as a literal here. That receiver is unconditional -- an
        # operator cannot dial it down the way they can nVoice -- so if a leg
        # ever declares a second data frequency, this derivation follows it and
        # this test fails until the block is widened, which is the whole point
        # of the exactness assertion below.
        n_data = mrx.LEG_700.get('n_data', 0) + mrx.LEG_800.get('n_data', 0)
        channels = 1 + 2 * cc.MAX_VOICE + n_data
        span = 2 * (channels - 1)
        self.assertLessEqual(
            span, mrx.PORT_BLOCK_SPAN,
            f"MAX_VOICE={cc.MAX_VOICE} needs {channels} channels spanning "
            f"{span} ports, but make_multirx_cfg.py reserves only "
            f"{mrx.PORT_BLOCK_SPAN} ({mrx.BASE_PORT}-{mrx.LAST_PORT}). "
            f"make_multirx_cfg.validate() would now refuse to build it -- "
            f"raise the block there, and in BOTH MAX_VOICE comments, before "
            f"raising this bound.",
        )
        # And it is at the ceiling, not comfortably under it. Stated so the
        # zero headroom is a recorded fact rather than a surprise: the next
        # person to add a channel of any kind has to widen the block first.
        self.assertEqual(
            span, mrx.PORT_BLOCK_SPAN,
            "the port budget has always been EXACT at MAX_VOICE. If that is "
            "no longer true the comments describing it need updating too.",
        )


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
        # reparented once the launcher exited. _op25_alive() is mocked
        # separately (True) because this test is specifically about op25
        # surviving -- see the sibling degraded test below for op25 NOT
        # surviving the same group-alive condition.
        with mock.patch.object(cc.os, "killpg", return_value=None) as killpg, \
             mock.patch.object(cc, "_op25_alive", return_value=True) as op25_alive:
            snapshot = state.snapshot()

        killpg.assert_called_once_with(555, 0)
        op25_alive.assert_called_once_with(555)
        self.assertEqual(snapshot["running"], True)
        self.assertEqual(snapshot["pid"], 555)
        self.assertNotIn("degraded", snapshot)
        self.assertIsNotNone(state.pgid)  # NOT cleared -- the group is still alive

    def test_status_reports_degraded_when_op25_died_but_recorders_survive(self):
        # final-review.md finding 5, reproduced directly: the process GROUP
        # is still alive (recorders survive op25), but op25 itself -- the
        # thing that actually holds the HackRFs -- is gone.
        # `lwin_listen_multi.sh:243` waits on recorder 0, not op25, so this
        # is exactly the state a dead op25 leaves behind. Before finding 5's
        # fix, snapshot() only asked whether ANY group member was alive, so
        # it kept reporting `running: true` for a session with no radio at
        # all -- confirmed as the mechanism behind this project's unattended
        # multi-hour outages.
        #
        # CORRECTED (final-review.md section 8, round-2 re-review): `running`
        # must stay `true` here, NOT flip to `false` -- a prior version of
        # this fix flipped it, which made sessionStore.get() auto-close the
        # tracked session the instant this fired, taking away the console's
        # own Stop button for a state the operator still needs to act on
        # (Task 4 had relied on exactly that button to recover an equivalent
        # stale session before). `degraded` carries the honest signal
        # instead, additively, without touching session lifecycle.
        state = CaptureState()
        state.process = _FakeProc(pid=555, returncode=0)
        state.pgid = 555
        state.request = {"mode": "multi", "durationSec": 600}
        state.started_at = 0.0

        with mock.patch.object(cc.os, "killpg", return_value=None), \
             mock.patch.object(cc, "_op25_alive", return_value=False) as op25_alive:
            snapshot = state.snapshot()

        op25_alive.assert_called_once_with(555)
        self.assertEqual(snapshot["running"], True)
        self.assertEqual(snapshot["degraded"], True)
        self.assertIn("op25", snapshot["message"])
        # self.pgid is untouched either way -- POST /start still correctly
        # 409s (the orphaned recorders still hold their UDP ports) and POST
        # /stop still correctly reaps them regardless of op25's state.
        self.assertEqual(state.pgid, 555)

    def test_status_is_not_degraded_when_the_op25_check_is_inconclusive(self):
        # final-review.md section 8, round-2 re-review, Important #1: a
        # `pgrep` spawn hiccup (timeout, missing binary, its own error exit)
        # is genuinely NO INFORMATION about op25, not a lean toward "dead".
        # Before this fix, _op25_alive() collapsed that into the same
        # `False` as a CONFIRMED death, which snapshot() then reported as
        # `running: false` indistinguishably from the real thing --
        # `delegatedSessionLiveness()` mapped that straight to 'stopped'
        # with NO retry tolerance (unlike the adjacent 'unknown' path this
        # same codebase already built MAX_CONSECUTIVE_UNKNOWN for), so a
        # single transient pgrep failure could permanently close a live,
        # healthy session's DB row. Reproduced directly here: an
        # inconclusive check (_op25_alive() returns None) must leave
        # `running` untouched and must NOT set `degraded` -- no claim in
        # either direction.
        state = CaptureState()
        state.process = _FakeProc(pid=555, returncode=0)
        state.pgid = 555
        state.request = {"mode": "multi", "durationSec": 600}
        state.started_at = 0.0

        with mock.patch.object(cc.os, "killpg", return_value=None), \
             mock.patch.object(cc, "_op25_alive", return_value=None) as op25_alive:
            snapshot = state.snapshot()

        op25_alive.assert_called_once_with(555)
        self.assertEqual(snapshot["running"], True)
        self.assertNotIn("degraded", snapshot)
        self.assertEqual(state.pgid, 555)

    def test_snapshot_never_crashes_with_nothing_running(self):
        self.assertEqual(CaptureState().snapshot(), {"running": False, "pid": None})

    def test_stop_is_a_no_op_when_nothing_is_running(self):
        result = CaptureState().stop()
        self.assertEqual(result, {"stopped": False, "message": "no capture running"})

    def test_start_passes_session_id_through_the_child_environment(self):
        # The only path this can reach a real subprocess.Popen call at all --
        # AlreadyRunning/stopping tests above refuse before ever getting
        # here, deliberately, since a well-formed request would start a real
        # capture. Popen itself is mocked, so nothing is actually spawned.
        state = CaptureState()
        fake_proc = _FakeProc(pid=999, returncode=None)
        with mock.patch.object(cc.subprocess, "Popen", return_value=fake_proc) as popen, \
             mock.patch.object(cc.os, "killpg", return_value=None), \
             mock.patch.object(cc.time, "sleep"):
            result = state.start({"mode": "multi", "durationSec": 600, "sessionId": 42})

        self.assertEqual(result["pid"], 999)
        _, kwargs = popen.call_args
        self.assertEqual(kwargs.get("env", {}).get("SDR_SESSION_ID"), "42")

    def test_start_omits_session_id_from_env_when_not_given(self):
        # env=None tells subprocess.Popen to inherit this process's own
        # environment unchanged -- the same behaviour as before sessionId
        # existed. A regression that always builds an env dict (even an
        # empty-looking one) would silently stop inheriting PATH/LD_LIBRARY_
        # PATH/etc, which op25 and hackrf_info both need.
        state = CaptureState()
        fake_proc = _FakeProc(pid=1000, returncode=None)
        with mock.patch.object(cc.subprocess, "Popen", return_value=fake_proc) as popen, \
             mock.patch.object(cc.os, "killpg", return_value=None), \
             mock.patch.object(cc.time, "sleep"):
            state.start({"mode": "multi", "durationSec": 600})

        _, kwargs = popen.call_args
        self.assertIsNone(kwargs.get("env"))

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
        # `waitedSec` is asserted separately (StopLadderTimingTest) rather than
        # pinned to a value here: this test mocks _wait_for_group_exit
        # wholesale, so no simulated time passes and the measured wait is
        # whatever the real clock did in microseconds. What THIS test is about
        # is that the STORED pgid got signalled, so the wait is checked only
        # for shape.
        self.assertEqual(
            {k: v for k, v in result.items() if k != "waitedSec"},
            {"stopped": True, "pid": 777, "forced": False},
        )
        self.assertIsInstance(result["waitedSec"], float)
        self.assertIsNone(state.pgid)


class _FakeClock:
    """A monotonic clock that only advances when something sleeps.

    Substituted for time.monotonic/time.sleep so the stop ladder's REAL
    timeouts can be exercised at their real values without the suite paying
    65 seconds of wall clock for it. _wait_for_group_exit()'s loop is
    `while monotonic() < deadline: ...; sleep(0.2)`, so advancing `now` by
    exactly the sleep duration reproduces real timing semantics precisely --
    including the deadline arithmetic -- at zero cost.

    This matters for revert-detection: a test that mocked
    _wait_for_group_exit() wholesale (as the older stop tests do, deliberately,
    for their own purposes) would pass with ANY timeout value and so could
    never catch a regression to the old 8.0s. Driving the real loop over a fake
    clock is what makes the timing behaviour genuinely under test.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.start = start

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class StopLadderTimingTest(unittest.TestCase):
    """The SIGINT-then-SIGKILL stop ladder's timing, and the invariant that
    two other files' timeouts stay nested outside it.

    WHY THIS CLASS EXISTS. The ladder waited 8s after SIGINT before escalating
    to SIGKILL -- calibrated for the `pd` preset (47 talkgroups, 8 recorders).
    The console moved to `pd-all` (222 talkgroups, 10 recorders) and every stop
    began logging `forced=True`, i.e. escalating. That escalation SIGKILLs
    lwin_listen_multi.sh's process group partway through its `trap cleanup INT
    TERM`, which is the one thing that SIGINTs the udp_audio_record recorders so
    they finalise the .wav each is mid-write on -- exactly what stop()'s
    "SIGINT, never SIGKILL first" comment exists to prevent. See
    STOP_SIGINT_TIMEOUT_SEC in scripts/capture_control.py for the measurements
    behind the current values.
    """

    def _stopping_state(self, pgid: int = 4242) -> CaptureState:
        state = CaptureState()
        state.process = _FakeProc(pid=pgid, returncode=None)
        state.pgid = pgid
        state.request = {"mode": "multi"}
        state.started_at = 0.0
        return state

    def _run_stop(self, state: CaptureState, exits_after_sec: float | None):
        """Drive state.stop() over a fake clock, with the process group
        modelled as emptying `exits_after_sec` simulated seconds in (or never,
        for None).

        _pgid_alive is patched rather than os.killpg's ESRCH behaviour because
        _pgid_alive is the single primitive both the pre-stop reap and
        _wait_for_group_exit consult -- so patching it here leaves os.killpg
        used ONLY for the real signals stop() sends, which is what lets the
        assertions below say exactly which signals were delivered.
        """
        clock = _FakeClock()

        def alive(_pgid: int) -> bool:
            if exits_after_sec is None:
                return True
            return clock.now - clock.start < exits_after_sec

        with mock.patch.object(cc, "_pgid_alive", side_effect=alive), \
             mock.patch.object(cc.time, "monotonic", clock.monotonic), \
             mock.patch.object(cc.time, "sleep", clock.sleep), \
             mock.patch.object(cc.os, "killpg") as killpg:
            result = state.stop()
        return result, killpg, clock

    def test_a_group_that_exits_inside_the_window_is_never_escalated(self):
        # 30 simulated seconds: comfortably longer than the OLD 8s cap and
        # comfortably inside the new 60s one. This is the case the fix is FOR
        # -- a `pd-all` cleanup measured at ~20-25s on a 3-hour session -- so
        # a revert of STOP_SIGINT_TIMEOUT_SEC to 8.0 makes this test fail,
        # which is the point of choosing 30 rather than something tiny.
        state = self._stopping_state(pgid=4242)
        result, killpg, _clock = self._run_stop(state, exits_after_sec=30.0)

        killpg.assert_called_once_with(4242, signal.SIGINT)
        self.assertNotIn(
            mock.call(4242, signal.SIGKILL), killpg.call_args_list,
            "a group that exited on its own inside the window must never be "
            "SIGKILLed -- that bypasses lwin_listen_multi.sh's cleanup trap",
        )
        self.assertIs(result["forced"], False)
        # Polled, not slept: the wait returns when the group is empty, so the
        # reported wait is ~30s, NOT the 60s cap. This is what makes a
        # generous cap acceptable to an operator standing at the Stop button.
        self.assertAlmostEqual(result["waitedSec"], 30.0, delta=0.5)
        self.assertIsNone(state.pgid)

    def test_a_group_that_outlives_the_window_is_escalated_to_sigkill(self):
        # The cap still has to exist: a cleanup that is genuinely wedged (or a
        # session so long the import cannot finish -- see
        # STOP_SIGINT_TIMEOUT_SEC's honesty note about ~27-hour sessions) must
        # not hold the operator forever. Raising the cap must not have turned
        # the ladder into an unbounded wait.
        state = self._stopping_state(pgid=555)
        result, killpg, _clock = self._run_stop(state, exits_after_sec=None)

        self.assertEqual(
            killpg.call_args_list,
            [mock.call(555, signal.SIGINT), mock.call(555, signal.SIGKILL)],
            "SIGINT must come first and SIGKILL only as the escalation",
        )
        self.assertIs(result["forced"], True)
        # The full ladder, both legs: the SIGINT window plus the SIGKILL reap.
        self.assertAlmostEqual(
            result["waitedSec"], cc.STOP_LADDER_BUDGET_SEC, delta=0.5,
        )
        # State is cleared even in the forced case -- otherwise /status would
        # keep reporting a stale pid and /start would refuse the next capture.
        self.assertIsNone(state.pgid)

    def test_the_ladder_budget_is_the_sum_of_its_two_legs(self):
        # Derived, never hand-written, so it cannot drift from its terms --
        # and the two cross-file checks below are stated against it.
        self.assertEqual(
            cc.STOP_LADDER_BUDGET_SEC,
            cc.STOP_SIGINT_TIMEOUT_SEC + cc.STOP_SIGKILL_TIMEOUT_SEC,
        )

    def test_the_sigint_window_covers_a_measured_pd_all_cleanup(self):
        # THE REVERT DETECTOR for the timing values themselves. The behaviour
        # tests above prove the ladder escalates correctly around whatever the
        # constants say; this one pins what they must SAY, with the evidence.
        #
        # MEASURED 2026-09-03 against results/op25_multi.log.20260902-182333
        # (176 MB, 1,964,517 lines, 156,794 grants -- about a 3-hour pd-all
        # session at the observed 10,558 lines/min). lwin_listen_multi.sh's
        # cleanup() runs `setsid python3 scripts/import_grants.py "$LOG"`
        # SYNCHRONOUSLY (setsid, but no `&`), so bash blocks in the trap until
        # it returns, and CENSUS defaults to 1:
        #
        #   parse phase (import_grants.py --dry-run):                  4.6 s
        #   link phase (one indexed SELECT per grant; 55.4 us measured
        #     read-only over sdr.db x 156,794 grants):                ~8.7 s
        #   plus 156,794 INSERTs + commit, and the recorders'/op25's own
        #     exit and .wav finalisation.
        #
        # ~20-25 s total. The old 8.0s could not cover it, which is why every
        # pd-all stop escalated. 60s is ~2.5-3x measured, so a stop is not
        # escalated merely for being a large preset.
        self.assertGreaterEqual(
            cc.STOP_SIGINT_TIMEOUT_SEC, 45.0,
            "a measured pd-all cleanup is ~20-25s; anything near the old 8.0s "
            "escalates to SIGKILL on every ordinary Stop and bypasses "
            "lwin_listen_multi.sh's recorder-finalising cleanup trap",
        )
        # And it must stay bounded: an operator is waiting on Stop.
        self.assertLessEqual(
            cc.STOP_SIGINT_TIMEOUT_SEC, 120.0,
            "the wait is polled but capped on purpose -- a wedged cleanup must "
            "not hold the Stop button indefinitely",
        )
        # The reap leg is short by design but not vanishing: 2s was thin for
        # reaping op25 plus ten recorders, and this leg is only ever paid on a
        # path that has already gone wrong.
        self.assertGreaterEqual(cc.STOP_SIGKILL_TIMEOUT_SEC, 5.0)

    def test_compose_stop_grace_period_nests_outside_the_ladder(self):
        # NESTING INVARIANT 1, checked in code rather than in a comment.
        # `docker compose stop/restart capture` SIGTERMs, waits
        # stop_grace_period, then SIGKILLs the whole cgroup -- which tears
        # down op25's process group mid-trap and skips cleanup entirely. If
        # the ladder is raised without raising this, the bug is not fixed but
        # MOVED, and to a worse place: Docker's SIGKILL lands mid-ladder.
        compose = (REPO_ROOT / "docker-compose.yml").read_text()
        # SCOPED TO THE `capture` SERVICE, not to the file.
        #
        # This used to be a file-wide `re.findall` defended by "exactly one
        # match" -- which is not the same property at all. Delete capture's
        # stop_grace_period and add one to `whisper`, and the file-wide search
        # still finds exactly one 90 and this test still passes, while
        # `capture` silently falls back to Docker's 10s default and Docker
        # SIGKILLs the cgroup 55 s into a 65 s ladder. The test's own comment
        # anticipated that ("if another service gained one, scope this check to
        # the capture service") and scoped nothing. This is that scoping.
        capture_block = _compose_service_block(compose, "capture")
        # `capture` is the LAST service in the file, so its slice runs to EOF.
        # Asserted because an off-by-one there would yield an empty string and
        # every check below it would pass vacuously.
        self.assertGreater(len(capture_block.splitlines()), 10, capture_block)
        matches = re.findall(
            r"^\s*stop_grace_period:\s*(\d+)s\s*$", capture_block, re.M)
        self.assertEqual(
            len(matches), 1,
            "expected exactly one stop_grace_period inside docker-compose.yml's "
            "`capture:` service. Zero means capture is on Docker's 10s default "
            "-- a SIGKILL 55s into a 65s ladder -- however many other services "
            "declare one.",
        )
        grace = float(matches[0])
        self.assertGreater(
            grace, cc.STOP_LADDER_BUDGET_SEC,
            f"capture's stop_grace_period ({grace:.0f}s) must exceed the stop "
            f"ladder ({cc.STOP_LADDER_BUDGET_SEC:.0f}s), or Docker SIGKILLs "
            f"the cgroup partway through the graceful shutdown",
        )
        # Margin, not just strict inequality: SIGTERM delivery, tini's
        # forwarding, and the handler reaching STATE.stop() all happen inside
        # the grace period before the ladder's own clock even starts.
        self.assertGreaterEqual(grace, cc.STOP_LADDER_BUDGET_SEC + 10.0)

    def test_a_grace_period_on_another_service_does_not_satisfy_the_check(self):
        # The defect the scoping above fixes, demonstrated. A file-wide
        # `re.findall` guarded by "exactly one match" passes on this input --
        # one match, value 90 -- while `capture` carries no grace period at
        # all and gets Docker's 10s default. This is the assertion that stops
        # the scoping from being quietly reverted to a file-wide search.
        forged = (
            "services:\n"
            "  whisper:\n"
            "    image: rtl-whisper-cuda\n"
            "    stop_grace_period: 90s\n"
            "  capture:\n"
            "    image: rtl-capture\n"
            "    user: \"1000:1000\"\n"
        )
        self.assertEqual(
            re.findall(r"^\s*stop_grace_period:\s*(\d+)s\s*$", forged, re.M),
            ["90"],
            "sanity: the OLD file-wide search finds exactly one 90 here",
        )
        block = _compose_service_block(forged, "capture")
        self.assertNotIn(
            "stop_grace_period", block,
            "the scoped slice must not see whisper's grace period; if it "
            "does, capture can lose its own with nothing failing",
        )

    def test_the_client_stop_timeout_nests_outside_the_ladder(self):
        # NESTING INVARIANT 2. server/utils/processes.ts's
        # stopDelegatedCapture() POSTs /stop behind an AbortSignal.timeout. If
        # the CLIENT gives up before the ladder finishes, the console reports a
        # failed Stop for a stop that is proceeding correctly -- and the
        # operator's likely response (Stop again, or restart the container) is
        # what actually breaks the cleanup. This coupling is easy to miss
        # because it lives in a different language in a different directory,
        # which is precisely why it is asserted here.
        ts = (REPO_ROOT / "server" / "utils" / "processes.ts").read_text()
        # Scoped to the /stop fetch specifically. processes.ts has three
        # AbortSignal.timeout calls -- /start (10s), GET /status (5s), and
        # this one -- and only THIS one is coupled to the stop ladder. The
        # other two bound requests that do not wait on a cleanup at all, so
        # matching them would make this assertion meaningless. Anchoring on
        # the "/stop`" path literal on the same line is what keeps the check
        # pointed at the right timeout.
        matches = re.findall(r"/stop`[^\n]*AbortSignal\.timeout\(([\d_]+)\)", ts)
        self.assertEqual(
            len(matches), 1,
            "expected exactly one POST /stop fetch with an AbortSignal.timeout "
            "in processes.ts; if stopDelegatedCapture() was restructured, "
            "re-point this check rather than relaxing it",
        )
        client_ms = float(matches[0].replace("_", ""))
        self.assertGreater(
            client_ms / 1000.0, cc.STOP_LADDER_BUDGET_SEC,
            f"the console's POST /stop timeout ({client_ms / 1000.0:.0f}s) must "
            f"exceed the control server's ladder "
            f"({cc.STOP_LADDER_BUDGET_SEC:.0f}s), or a correct stop is "
            f"reported to the operator as a failure",
        )
        self.assertGreaterEqual(client_ms / 1000.0, cc.STOP_LADDER_BUDGET_SEC + 5.0)


class Op25AliveTest(unittest.TestCase):
    """_op25_alive() in isolation, mocking subprocess.run rather than
    CaptureState, so the pgrep-specific exit-code/exception handling is
    covered directly rather than only through snapshot()'s two tests above."""

    def test_true_when_pgrep_matches(self):
        with mock.patch.object(
            cc.subprocess, "run",
            return_value=mock.Mock(returncode=0, stderr=b""),
        ) as run:
            self.assertTrue(cc._op25_alive(555))
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["pgrep", "-g"])
        self.assertIn("555", args)

    def test_false_when_pgrep_finds_nothing(self):
        # exit 1 is pgrep's own documented "no process matched" -- a real
        # negative answer, not an error.
        with mock.patch.object(
            cc.subprocess, "run",
            return_value=mock.Mock(returncode=1, stderr=b""),
        ):
            self.assertFalse(cc._op25_alive(555))

    def test_none_when_pgrep_itself_errors(self):
        # A non-0/1 exit is pgrep reporting ITS OWN failure (bad argument,
        # internal error) -- not an authoritative "no match" and not an
        # authoritative "alive" either. CORRECTED (final-review.md section 8,
        # round-2 re-review): a prior version of this function collapsed
        # this into `False`, "not confirmed alive", on the reasoning that an
        # inability to prove op25 is there must not silently read as
        # healthy. That reasoning was right for THIS function taken alone,
        # but wrong for the whole system: the caller (snapshot()) could not
        # tell this apart from a CONFIRMED death, so a transient pgrep
        # hiccup and a real op25 death produced the identical `running:
        # false` -- and downstream, delegatedSessionLiveness() maps that
        # straight to 'stopped' with no retry tolerance, so one blip could
        # permanently close a live session's DB row. `None` lets the caller
        # keep the two apart -- see snapshot()'s own docstring for how.
        with mock.patch.object(
            cc.subprocess, "run",
            return_value=mock.Mock(returncode=2, stderr=b"pgrep: error"),
        ):
            self.assertIsNone(cc._op25_alive(555))

    def test_none_when_pgrep_binary_is_missing(self):
        with mock.patch.object(cc.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(cc._op25_alive(555))

    def test_none_when_pgrep_times_out(self):
        with mock.patch.object(
            cc.subprocess, "run",
            side_effect=cc.subprocess.TimeoutExpired(cmd="pgrep", timeout=2),
        ):
            self.assertIsNone(cc._op25_alive(555))


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
        # Docker's stop_grace_period cutting the second wait short with
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
