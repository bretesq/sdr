#!/usr/bin/env python3
"""Whitelist selection tests, driven through a fixture tree via SDR_ROOT.

make_whitelist.py does its work at import time (argparse and the write both run
at module level), so it is exercised as a subprocess rather than imported. That
is also the honest test: the live callers -- lwin_listen.sh and
lwin_listen_multi.sh -- invoke it exactly this way.

Never reads the real reference DB or the real sdr.db.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SCRIPTS, 'make_whitelist.py')

# One talkgroup per (tag, enc) combination the selection logic distinguishes.
# `cat` has to contain a BR_AREA string or the area filter drops the row before
# the encryption filter is ever consulted.
CAT = 'East Baton Rouge'
DB = {
    '100': {'alpha': 'PD CLEAR',   'desc': '', 'cat': CAT, 'tag': 'Law Dispatch', 'enc': 'clear'},
    '101': {'alpha': 'PD PARTIAL', 'desc': '', 'cat': CAT, 'tag': 'Law Dispatch', 'enc': 'partial'},
    '102': {'alpha': 'PD FULL',    'desc': '', 'cat': CAT, 'tag': 'Law Dispatch', 'enc': 'full'},
    '103': {'alpha': 'TAC PART',   'desc': '', 'cat': CAT, 'tag': 'Law Tac',      'enc': 'partial'},
    '200': {'alpha': 'FD CLEAR',   'desc': '', 'cat': CAT, 'tag': 'Fire Dispatch', 'enc': 'clear'},
    '201': {'alpha': 'FD PARTIAL', 'desc': '', 'cat': CAT, 'tag': 'Fire Dispatch', 'enc': 'partial'},
}


class WhitelistCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.root, 'reference'))
        with open(os.path.join(self.root, 'reference/lwin_talkgroups.json'), 'w') as f:
            json.dump(DB, f)
        self.out = os.path.join(self.root, 'wl.txt')

    def select(self, *args: str, overrides: dict | None = None) -> list[int]:
        """Run the script and return the talkgroups it wrote."""
        if overrides is not None:
            with open(os.path.join(self.root, 'reference/enc_overrides.json'), 'w') as f:
                json.dump(overrides, f)
        env = dict(os.environ, SDR_ROOT=self.root)
        r = subprocess.run([sys.executable, SCRIPT, *args, '-o', self.out],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.out) as f:
            return sorted(int(line) for line in f if line.strip())


class TestPresetPartial(WhitelistCase):
    """pd and pd-all carry `partial` without being asked.

    The regression this pins: from 2026-09-02 to 2026-09-04 a `pd-all` capture
    silently excluded BRPD Dispatch 1-4 and the Sheriff dispatch channels,
    because the encryption filter treated 'documented as partially encrypted'
    as 'not worth recording'. Those talkgroups were 96% clear speech.
    """

    def test_pd_all_includes_partial_law_talkgroups(self) -> None:
        self.assertEqual(self.select('-p', 'pd-all'), [100, 101, 103])

    def test_pd_includes_partial_dispatch(self) -> None:
        self.assertEqual(self.select('-p', 'pd'), [100, 101])

    def test_preset_does_not_pull_in_full(self) -> None:
        """The implication is about `partial` only.

        `full` records silence, which is the thing the filter exists to
        prevent, so it stays behind --include-encrypted.
        """
        self.assertNotIn(102, self.select('-p', 'pd-all'))
        self.assertIn(102, self.select('-p', 'pd-all', '--include-encrypted'))

    def test_other_presets_keep_the_plain_filter(self) -> None:
        """Scoped to law, because 96% clear was measured on law talkgroups.

        Nothing has been measured about partial-flagged fire traffic; the
        preset must not assert otherwise.
        """
        self.assertEqual(self.select('-p', 'fire'), [200])
        self.assertEqual(self.select('-p', 'fire', '--include-partial'), [200, 201])

    def test_tag_selection_is_the_escape_hatch(self) -> None:
        """`-t 'Law Dispatch'` takes the same rows through the plain filter.

        Without this there is no way to ask for clear-only law dispatch, and
        the preset's implication would be unconditional rather than a default.
        """
        self.assertEqual(self.select('-t', 'Law Dispatch'), [100])
        self.assertEqual(self.select('-t', 'Law Dispatch', '--include-partial'), [100, 101])

    def test_match_selection_keeps_the_plain_filter(self) -> None:
        self.assertEqual(self.select('-p', 'pd-all', '-m', 'PD '), [100])


class TestEncOverrides(WhitelistCase):
    """A reviewed override decides the class, ahead of the scrape."""

    def test_override_promotes_a_partial_talkgroup(self) -> None:
        self.assertEqual(
            self.select('-t', 'Fire Dispatch',
                        overrides={'201': {'enc': 'clear', 'why': 'observed', 'reviewed': 'x'}}),
            [200, 201])

    def test_override_can_demote_too(self) -> None:
        """TG17282 was the real case: flagged clear, carrying encrypted traffic.

        Selected with --include-partial so the demotion is visible as a
        dropped row. Demoting the only survivor instead empties the selection,
        and the script refuses to write an empty whitelist -- covered in
        TestRefusals rather than conflated with the override behaviour here.
        """
        self.assertEqual(
            self.select('-t', 'Law Dispatch', '--include-partial',
                        overrides={'100': {'enc': 'full', 'why': 'observed', 'reviewed': 'x'}}),
            [101])

    def test_underscore_keys_are_documentation(self) -> None:
        self.assertEqual(
            self.select('-t', 'Law Dispatch',
                        overrides={'_comment': 'not a talkgroup',
                                   '_example': {'enc': 'clear', 'why': '', 'reviewed': ''}}),
            [100])


class TestAddTg(WhitelistCase):
    """--add-tg unions onto the selection and bypasses the encryption filter."""

    def test_add_tg_keeps_the_preset_selection(self) -> None:
        self.assertEqual(self.select('-p', 'pd', '--add-tg', '200'), [100, 101, 200])

    def test_add_tg_honours_an_id_absent_from_the_reference_db(self) -> None:
        """TG20000 is the live case: second-busiest here, unknown to RadioReference."""
        self.assertIn(20000, self.select('-p', 'pd', '--add-tg', '20000'))

    def test_add_tg_overrides_the_encryption_filter(self) -> None:
        """An explicitly named talkgroup is an instruction, not a suggestion."""
        self.assertIn(102, self.select('-p', 'pd', '--add-tg', '102'))

    def test_tg_replaces_rather_than_adds(self) -> None:
        """The distinction --add-tg exists for; pinned so it cannot blur."""
        self.assertEqual(self.select('-p', 'pd', '-g', '200'), [200])


class TestRefusals(WhitelistCase):
    def test_empty_selection_is_not_written(self) -> None:
        """A capture reading an empty whitelist records nothing, silently."""
        env = dict(os.environ, SDR_ROOT=self.root)
        r = subprocess.run([sys.executable, SCRIPT, '-m', 'nothing matches this',
                            '-o', self.out], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(self.out))


if __name__ == '__main__':
    unittest.main()
