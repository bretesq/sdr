# Phase 0 — Multi-channel LWIN capture on one HackRF (700 MHz leg) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record several simultaneous LWIN calls *and* a 100% grant census from a single HackRF, by replacing op25's single-channel `rx.py` with `multi_rx.py` running 1 pinned control-channel receiver plus 3 voice receivers inside one 8 Msps window over site 13's entire 700 MHz leg.

**Architecture:** `multi_rx.py` attaches N demodulator chains to one non-tunable SoapySDR device. Each chain retunes by rewriting frequency-translating filter taps, so a "retune" inside the device window costs no hardware tune. `tk_p25.py` gives a real voice-receiver pool — `talkgroups[tgid]['receiver']` makes each receiver claim a different call. One receiver is pinned to the control channel with a whitelist containing only a non-existent talkgroup, so it never leaves and the grant census stays complete. Each channel emits its own UDP audio stream, so one `udp_audio_record.py` instance runs per voice channel, each filtering op25's log by its own receiver id.

**Tech Stack:** Python 3.14, GNU Radio 3.10.12, op25 (boatbod, patched), SoapySDR 0.8.1, HackRF Pro, sqlite3, `unittest` for Python tests / `vitest` for TS.

## Global Constraints

- **Radio:** HackRF Pro r1.2, fw 2026.01.3, `soapy=0,driver=hackrf`. Gains `AMP:0,LNA:40,VGA:44` — measured working; `-a 1` and `-g 62` both break decoding (README gotcha #2).
- **op25 reaches the radio only through SoapySDR**, never gr-osmosdr (README gotcha #9).
- **Sample rate 8000000, `if_rate` 25000.** `get_decim(8e6)` resolves via 25000 → `decim 80/4`, `if1 100000`, `if2 25000`. A mismatched `if_rate` costs an `arb_resampler` per channel.
- **Device centre 771418500 Hz (771.4185 MHz), `usable_bw_pct 0.85`, `tunable: false`.**
- **Usable half-span is `(rate × usable_bw)/2 − if_rate/2` = 3.3875 MHz.** `multi_rx.py:62` imports **`p25_demodulator_dev`**, whose bound uses `if_rate` (25 kHz), *not* `if1` (100 kHz). Required half-span is 2.4313 MHz. Margin 0.956 MHz.
- **Channels covered:** 769.68125, 769.93125, 770.75625, 772.68125 (granted voice) + 773.05625, 774.54375 (both control). All within the window.
- **`crypt_behavior: 1`** — silence-but-record, matching today's `rx.py -n`. `2` makes `find_talkgroup` skip encrypted talkgroups outright and would break `--include-partial`.
- **`tunable: false` is mandatory.** `multi_rx.py:754` refuses to put a second channel on a `tunable: true` device.
- **UDP ports must be spaced ≥ 2 apart.** `op25_audio.cc:298` sends on `d_audio_port + slot_id`; `udp_audio_record.py` binds `PORT` and `PORT+1`.
- **Do not add an `if __name__ == '__main__'` guard to `scripts/udp_audio_record.py`.** `scripts/tests/test_static.py:123` pins its absence deliberately.
- **Tests run with:** `t` (= `vitest run && python3 -m unittest discover -s scripts/tests`). Python only: `python3 -m unittest discover -s scripts/tests -v`.
- **Conventional commits.** Never commit `sdr.db`, `recordings/`, `captures/`, or `results/`.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/op25_log.py` **(new)** | Pure parsing of op25 stderr text → per-call metadata. Extracted from `udp_audio_record.py` so it is importable and unit-testable; handles **both** op25 trunking log formats and optional per-receiver filtering. |
| `scripts/udp_audio_record.py` **(modify)** | Unchanged responsibility (UDP → WAV + DB). Loses its inline parser, gains `--rx-id`. |
| `scripts/make_multirx_cfg.py` **(new)** | Builds and *validates* the `multi_rx` JSON config. All the arithmetic that silently misbehaves (usable bandwidth, `if_rate`, DC clearance, port spacing) is checked here rather than discovered on the air. |
| `scripts/lwin_listen_700.sh` **(new)** | Launcher, sibling of `lwin_listen.sh`: whitelist → config → `multi_rx.py` + N recorders. |
| `scripts/tests/test_op25_log.py` **(new)** | Fixtures copied verbatim from real op25 output. |
| `scripts/tests/test_multirx_cfg.py` **(new)** | Config-generator arithmetic and rejection cases. |
| `scripts/tests/test_static.py` **(modify)** | Add both new modules to `IMPORTABLE`. |

### The format trap this plan exists to avoid

op25 has **two** trunking modules with **different** log formats, and `udp_audio_record.py`'s current regexes only match one:

```
rx.py       -> trunking.py:1874
  "voice update:  tg(17051), freq(852912500), slot(-), prio(3)"
   no receiver id | frequency in Hz (int) | no radio id

multi_rx.py -> tk_p25.py:2623
  "[2] voice update:  tg(6848), rid(2601234), freq(769.593750), slot(-), prio(3)"
   receiver id     | frequency in MHz (float) | radio id present
```

The existing `FREQPAT = r'voice update:\s*tg\((\d+)\),\s*freq\((\d+)\)'` fails on the
`multi_rx` line twice over: `rid(...)` sits between `tg` and `freq`, and `769.593750`
is not `\d+`. Left unfixed, every call recorded under `multi_rx` would silently lose
its frequency. The `rid` is a bonus — `sdr_db.upsert_call` already accepts
`src_addr` and nothing populates it today.

ESS lines are prefixed by the receiver id, but not adjacently — `log_ts.h`'s
`get(int id)` emits `MM/DD/YY HH:MM:SS.uuuuuu [id]`, and `p25p1_fdma.cc:327`
prints `[id] NAC 0x1bd LDU2: ` with no newline before `p25p1_fdma.cc:348` appends
`ESS: algid=...`. So the id is on the same line, ~20 characters earlier.

---

## Task 1: Extract the op25 log parser into an importable module

Behaviour-preserving refactor. No new features — this task exists so Task 2's
changes are testable at all.

**Files:**
- Create: `scripts/op25_log.py`
- Modify: `scripts/udp_audio_record.py:35-52` (delete regexes), `:54-123` (delete `LogTail`), add import
- Modify: `scripts/tests/test_static.py:33`
- Test: `scripts/tests/test_op25_log.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `op25_log.ANSI: re.Pattern` — ANSI escape stripper
  - `op25_log.LogTail(path: str, rx_id: int | None = None)` with
    `poll() -> None`, `current() -> int | None`, `metadata() -> dict`
  - `op25_log.TG_TTL: float = 12.0`
  - `metadata()` returns keys: `freq, algid, keyid, mi, sysid, rfss, site, nac`
    (all `int | str | None`)

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_op25_log.py`:

```python
#!/usr/bin/env python3
"""Tests for the op25 stderr log parser.

Every fixture is copied VERBATIM from a real op25 log in results/, or built
from the exact format string in op25's source when we have no capture yet
(tk_p25.py:2623). Inventing log lines is how the FREQPAT bug survived.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import op25_log


# Verbatim from results/op25_record.log (rx.py -> trunking.py:1874).
RX_PY_VOICE = (
    '08/31/26 13:38:35.183953 voice update:  '
    'tg(17051), freq(852912500), slot(-), prio(3)\n'
)
# Verbatim from results/op25_record.log (p25p1_fdma.cc:327 + :348 on one line).
ESS_RX0 = (
    '08/31/26 13:38:35.898304 [0] NAC 0x1bd LDU2: '
    'ESS: algid=80, keyid=0, mi=00 00 00 00 00 00 00 00 00, rs_errs=0\n'
)
# Verbatim from results/op25_record.log.
RFSS = ('08/31/26 13:38:30.000000 rfss_sts_bcst: '
        'syid: 1bd rfid: 1 stid: 13 ch1: 16e8(773.056250)\n')


def tail_over(text: str, rx_id=None) -> op25_log.LogTail:
    fh = tempfile.NamedTemporaryFile('w', suffix='.log', delete=False)
    fh.write(text); fh.close()
    t = op25_log.LogTail(fh.name, rx_id=rx_id)
    t.poll()
    return t


class TestRxPyFormat(unittest.TestCase):
    """The format lwin_listen.sh produces today must keep working."""

    def test_talkgroup_from_voice_update(self):
        self.assertEqual(tail_over(RX_PY_VOICE).current(), 17051)

    def test_frequency_in_hz_is_read_as_hz(self):
        self.assertEqual(tail_over(RX_PY_VOICE).metadata()['freq'], 852912500)

    def test_ess_is_read(self):
        md = tail_over(ESS_RX0).metadata()
        self.assertEqual((md['algid'], md['keyid']), (0x80, 0))
        self.assertEqual(md['mi'], '00' * 9)

    def test_site_identity_is_read(self):
        md = tail_over(RFSS).metadata()
        self.assertEqual((md['sysid'], md['rfss'], md['site']), (0x1bd, 1, 13))

    def test_stale_values_are_dropped_not_guessed(self):
        t = tail_over(RX_PY_VOICE)
        t.tg_t = time.time() - (op25_log.TG_TTL + 1)
        self.assertIsNone(t.current())


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_op25_log -v` from `/home/besquivel/rtl`
(or `cd scripts/tests && python3 -m unittest test_op25_log -v`)
Expected: FAIL — `ModuleNotFoundError: No module named 'op25_log'`

- [ ] **Step 3: Create `scripts/op25_log.py`**

Move the regexes and `LogTail` out of `udp_audio_record.py` **verbatim**, then
add the `rx_id` parameter as an unused-for-now argument so Task 2 has a seam.

```python
#!/usr/bin/env python3
"""Parse op25's stderr log into per-call metadata.

Extracted from udp_audio_record.py so it can be imported and tested:
scripts/tests/test_static.py pins that udp_audio_record.py executes at import
time and therefore cannot be imported.

op25 has TWO trunking modules with DIFFERENT log formats:

  rx.py       -> trunking.py:1874
    "voice update:  tg(17051), freq(852912500), slot(-), prio(3)"
     no receiver id | frequency in Hz | no radio id

  multi_rx.py -> tk_p25.py:2623
    "[2] voice update:  tg(6848), rid(2601234), freq(769.593750), slot(-), prio(3)"
     receiver id    | frequency in MHz | radio id present

Assuming either one alone silently loses metadata rather than erroring, so both
are matched here.
"""
from __future__ import annotations

import os
import re
import time

TG_TTL = 12.0

ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]')

# Per-call P25 metadata, all read from op25's own log output.
#
#   voice update:  tg(17169), freq(851287500), slot(-), prio(3)
#   ESS: algid=aa, keyid=8, mi=00 00 00 00 00 00 00 00 00
#   rfss_sts_bcst: syid: 1bd rfid: 1 stid: 13 ch1: 16e8(773.056250)
#
# ESS needs op25 at -v 10. Everything else appears at the default verbosity.
_TG_BODY = (r'(?:voice update:\s+tg\((\d+)\)'
            r'|hold active tg\((\d+)\)'
            r'|set tgid=(\d+))')
# rid() is present only under tk_p25; freq is Hz there and MHz under trunking.
_FREQ_BODY = (r'voice update:\s*tg\((\d+)\),\s*'
              r'(?:rid\((\d+)\),\s*)?'
              r'freq\(([0-9.]+)\)')
_ESS_BODY = (r'ESS:\s*algid=([0-9a-f]+),\s*keyid=([0-9a-f]+),'
             r'\s*mi=([0-9a-f ]{26})')

# System-wide, not per-receiver: with one trunked system every channel reports
# the same site, and only the control-channel receiver sees these at all. So
# they are never filtered by rx_id -- a voice receiver must be able to inherit
# the site identity the control receiver decoded.
SITEPAT = re.compile(r'rfss_sts_bcst:\s*syid:\s*([0-9a-f]+)\s*'
                     r'rfid:\s*(\d+)\s*stid:\s*(\d+)')
NACPAT = re.compile(r'NAC\s+0x([0-9a-f]{3})')


def _pat(body: str, rx_id: int | None, gap: str = r'\s*') -> re.Pattern:
    """Compile `body`, optionally requiring op25's `[rx_id]` prefix.

    rx_id None  -> accept the line with or without a receiver id (rx.py, and
                   any single-channel multi_rx run).
    rx_id N     -> require `[N]` before the body, so N recorders sharing one
                   log file each see only their own channel.
    """
    if rx_id is None:
        return re.compile(r'(?:\[\d+\]\s*)?' + body)
    return re.compile(r'\[' + str(int(rx_id)) + r'\]' + gap + body)


class LogTail:
    """Follow op25's log and expose the current call's metadata.

    Everything here is best-effort and time-bounded by TG_TTL: op25 emits these
    lines asynchronously from the audio stream, so a value older than the
    freshness window belongs to a previous call and must not be attached to
    this one.
    """

    def __init__(self, path: str, rx_id: int | None = None):
        self.path, self.fh, self.buf = path, None, ''
        self.rx_id = rx_id
        self.tg, self.tg_t = None, 0.0
        self.freq, self.freq_t = None, 0.0
        self.src_addr, self.src_t = None, 0.0
        self.ess, self.ess_t = None, 0.0          # (algid, keyid, mi)
        self.site = None                          # (sysid, rfss, stid)
        self.nac = None
        self.tgpat = _pat(_TG_BODY, rx_id)
        self.freqpat = _pat(_FREQ_BODY, rx_id)
        # The receiver id precedes ESS by ~20 chars ("NAC 0x1bd LDU2: "),
        # on the same line: p25p1_fdma.cc:327 prints no newline before :348.
        self.esspat = _pat(_ESS_BODY, rx_id, gap=r'[^\n]{0,40}?')

    def poll(self) -> None:
        if self.fh is None:
            if not os.path.exists(self.path):
                return
            self.fh = open(self.path, 'r', errors='ignore')
        chunk = self.fh.read()
        if not chunk:
            return
        self.buf += ANSI.sub('', chunk)
        now = time.time()

        # op25 rewrites the status line without newlines, so scan the buffer tail
        for m in self.tgpat.finditer(self.buf):
            tg = next(g for g in m.groups() if g)
            self.tg, self.tg_t = int(tg), now

        # tg+freq together: the grant's voice channel for this call
        for m in self.freqpat.finditer(self.buf):
            self.tg, self.tg_t = int(m.group(1)), now
            if m.group(2):
                self.src_addr, self.src_t = int(m.group(2)), now
            self.freq, self.freq_t = _to_hz(m.group(3)), now

        # ESS is the AUTHORITATIVE encryption signal for this specific call,
        # independent of the reference DB's static enc flag -- which is known to
        # disagree: TG 17086 is flagged 'full' in RadioReference but transmitted
        # algid 0x80 (clear) in all 23 observations here.
        for m in self.esspat.finditer(self.buf):
            self.ess = (int(m.group(1), 16), int(m.group(2), 16),
                        m.group(3).replace(' ', ''))
            self.ess_t = now

        for m in SITEPAT.finditer(self.buf):
            self.site = (int(m.group(1), 16), int(m.group(2)), int(m.group(3)))

        for m in NACPAT.finditer(self.buf):
            self.nac = int(m.group(1), 16)

        self.buf = self.buf[-8000:]

    def current(self) -> int | None:
        if self.tg is not None and time.time() - self.tg_t < TG_TTL:
            return self.tg
        return None

    def metadata(self) -> dict:
        """Fresh per-call metadata. Stale values are dropped, not guessed."""
        now = time.time()
        algid = keyid = mi = None
        if self.ess and now - self.ess_t < TG_TTL:
            algid, keyid, mi = self.ess
        return {
            'freq': self.freq if (self.freq and now - self.freq_t < TG_TTL) else None,
            'src_addr': (self.src_addr
                         if (self.src_addr and now - self.src_t < TG_TTL) else None),
            'algid': algid, 'keyid': keyid, 'mi': mi,
            'sysid': self.site[0] if self.site else None,
            'rfss': self.site[1] if self.site else None,
            'site': self.site[2] if self.site else None,
            'nac': self.nac,
        }


def _to_hz(raw: str) -> int:
    """trunking.py logs Hz as an int; tk_p25.py logs MHz as a float."""
    return int(round(float(raw) * 1e6)) if '.' in raw else int(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s scripts/tests -v -k op25_log`
Expected: 5 tests PASS

- [ ] **Step 5: Point `udp_audio_record.py` at the new module**

In `scripts/udp_audio_record.py`, delete lines 35–52 (`ANSI` through `NACPAT`)
and the whole `class LogTail` (lines 54–123), keeping `slug()`. Replace the
`GAP, MINDUR, TG_TTL` line and add the import. The file's import line becomes:

```python
import socket, sys, wave, time, os, select, json, re, datetime, signal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from op25_log import LogTail, TG_TTL          # noqa: E402  (path set above)
```

and the constants line drops `TG_TTL`:

```python
GAP, MINDUR = 2.0, 0.7
```

`slug()` stays (it uses `re`, so keep `re` in the import list).

- [ ] **Step 6: Add the new module to the static test's IMPORTABLE list**

In `scripts/tests/test_static.py:33`:

```python
IMPORTABLE = ['sdr_db', 'op25_log']
```

- [ ] **Step 7: Run the whole suite**

Run: `python3 -m unittest discover -s scripts/tests -v`
Expected: all PASS, including
`test_recorder_has_no_import_time_side_effects_we_forgot_about` (we added no
main guard) and `test_pure_modules_import_cleanly` (now covering `op25_log`).

- [ ] **Step 8: Verify the refactor changed no behaviour, against a real log**

Run:

```bash
cd /home/besquivel/rtl && python3 - <<'EOF'
import sys; sys.path.insert(0, 'scripts')
from op25_log import LogTail
t = LogTail('results/op25_record.log'); t.poll()
print('tg:', t.tg, 'freq:', t.freq, 'ess:', t.ess, 'site:', t.site, 'nac:', t.nac)
EOF
```

Expected: a real talkgroup id, `freq` in Hz around 8.5e8, `ess` = `(128, 0, '00...')`,
`site` = `(443, 1, 13)`, `nac` = `443`. If any is `None`, the refactor dropped a
pattern — fix before committing.

- [ ] **Step 9: Commit**

```bash
git add scripts/op25_log.py scripts/udp_audio_record.py \
        scripts/tests/test_op25_log.py scripts/tests/test_static.py
git commit -m "refactor: op25 log parsing moves to an importable, tested module"
```

---

## Task 2: Per-receiver log filtering and `--rx-id`

**Files:**
- Modify: `scripts/op25_log.py` (no code change needed — verify `rx_id` works)
- Modify: `scripts/udp_audio_record.py` (argument parsing, near line 25)
- Test: `scripts/tests/test_op25_log.py` (append)

**Interfaces:**
- Consumes: `op25_log.LogTail(path, rx_id=...)` from Task 1.
- Produces: `udp_audio_record.py` accepts `--rx-id N` anywhere in `argv`; the
  four positional arguments `[port] [seconds] [outdir] [op25_log]` keep their
  existing meaning and order.

- [ ] **Step 1: Write the failing test**

Append to `scripts/tests/test_op25_log.py`:

```python
# Built from the exact format string at tk_p25.py:2623 --
#   "%s [%d] voice update:  tg(%d), rid(%d), freq(%f), slot(%s), prio(%d)\n"
# with freq passed as freq/1e6 (MHz) and get_slot(None) == '-'.
MULTI_RX2 = (
    '08/31/26 14:15:04.747731 [2] voice update:  '
    'tg(6848), rid(2601234), freq(769.593750), slot(-), prio(3)\n'
)
MULTI_RX3 = (
    '08/31/26 14:15:05.100000 [3] voice update:  '
    'tg(17165), rid(9999999), freq(772.681250), slot(-), prio(2)\n'
)
ESS_RX3 = (
    '08/31/26 14:15:05.200000 [3] NAC 0x1bd LDU2: '
    'ESS: algid=aa, keyid=8, mi=11 22 33 44 55 66 77 88 99, rs_errs=0\n'
)


class TestMultiRxFormat(unittest.TestCase):
    """tk_p25.py adds rid(), logs freq in MHz, and prefixes the receiver id."""

    def test_mhz_float_frequency_is_converted_to_hz(self):
        self.assertEqual(tail_over(MULTI_RX2).metadata()['freq'], 769593750)

    def test_rid_becomes_src_addr(self):
        self.assertEqual(tail_over(MULTI_RX2).metadata()['src_addr'], 2601234)

    def test_talkgroup_is_read_despite_the_rid_field(self):
        self.assertEqual(tail_over(MULTI_RX2).current(), 6848)


class TestReceiverFiltering(unittest.TestCase):
    """N recorders share one log file; each must see only its own channel."""

    BOTH = MULTI_RX2 + MULTI_RX3

    def test_rx2_sees_only_its_own_call(self):
        t = tail_over(self.BOTH, rx_id=2)
        self.assertEqual(t.current(), 6848)
        self.assertEqual(t.metadata()['freq'], 769593750)

    def test_rx3_sees_only_its_own_call(self):
        t = tail_over(self.BOTH, rx_id=3)
        self.assertEqual(t.current(), 17165)
        self.assertEqual(t.metadata()['freq'], 772681250)

    def test_no_rx_id_sees_the_last_line_of_either(self):
        self.assertEqual(tail_over(self.BOTH).current(), 17165)

    def test_ess_is_attributed_by_receiver_across_the_ldu2_prefix(self):
        both = ESS_RX0 + ESS_RX3
        self.assertEqual(tail_over(both, rx_id=3).metadata()['algid'], 0xaa)
        self.assertEqual(tail_over(both, rx_id=0).metadata()['algid'], 0x80)

    def test_a_receiver_with_no_lines_reports_nothing_rather_than_guessing(self):
        t = tail_over(self.BOTH, rx_id=5)
        self.assertIsNone(t.current())
        self.assertIsNone(t.metadata()['freq'])

    def test_site_identity_is_shared_not_filtered(self):
        """Only the control receiver decodes rfss_sts_bcst; voice must inherit it."""
        t = tail_over(RFSS + self.BOTH, rx_id=3)
        self.assertEqual(t.metadata()['site'], 13)

    def test_two_digit_receiver_ids_do_not_collide(self):
        """[1] must not match a line belonging to [12]."""
        line12 = MULTI_RX2.replace('[2]', '[12]')
        self.assertIsNone(tail_over(line12, rx_id=1).current())
        self.assertEqual(tail_over(line12, rx_id=12).current(), 6848)
```

- [ ] **Step 2: Run test to verify which of these fail**

Run: `python3 -m unittest discover -s scripts/tests -v -k op25_log`
Expected: `TestMultiRxFormat` and most of `TestReceiverFiltering` PASS (Task 1
already built the patterns), but
`test_two_digit_receiver_ids_do_not_collide` FAILS —
`r'\[1\]\s*voice update'` does not anchor the closing bracket against `[12]`.

- [ ] **Step 3: Fix the receiver-id anchoring in `scripts/op25_log.py`**

The bug is real: `re.compile(r'\[1\]')` cannot match `[12]`, but the *TG* body is
reached through `finditer` over a buffer, and `[12] voice update` contains no
`[1]` followed by the body — so this specific test may already pass. Run it
first. If it fails, the cause is a partial-bracket match; make it explicit:

```python
def _pat(body: str, rx_id: int | None, gap: str = r'\s*') -> re.Pattern:
    if rx_id is None:
        return re.compile(r'(?:\[\d+\]\s*)?' + body)
    return re.compile(r'(?<!\d)\[' + str(int(rx_id)) + r'\]' + gap + body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s scripts/tests -v -k op25_log`
Expected: all PASS

- [ ] **Step 5: Add `--rx-id` to `udp_audio_record.py`**

Replace the argument block at `scripts/udp_audio_record.py:25-30`. Keep
module-level execution — **no `__main__` guard** (Global Constraints).

```python
# --rx-id is pulled out of argv before the positional parse so the four
# positional arguments keep their existing meaning for lwin_listen.sh,
# lwin_capture_audio.sh, lwin_capture_enc.sh and the web console, none of
# which pass it.
_argv = sys.argv[1:]
RX_ID = None
if '--rx-id' in _argv:
    _i = _argv.index('--rx-id')
    RX_ID = int(_argv[_i + 1])
    del _argv[_i:_i + 2]

PORT = int(_argv[0]) if len(_argv) > 0 else 23456
SECS = float(_argv[1]) if len(_argv) > 1 else 400
OUT  = _argv[2] if len(_argv) > 2 else '/home/besquivel/rtl/recordings'
LOG  = _argv[3] if len(_argv) > 3 else '/home/besquivel/rtl/results/op25_record.log'
```

Update the docstring usage line:

```
Usage: udp_audio_record.py [--rx-id N] [port] [seconds] [outdir] [op25_log]

  --rx-id N   under multi_rx.py, only attribute log lines tagged [N] to this
              recorder. N recorders share one op25 log; without this they all
              read every channel's talkgroup and mislabel calls.
```

and the construction near line 124:

```python
tail = LogTail(LOG, rx_id=RX_ID)
```

and the startup banner so a multi-channel run is legible:

```python
print(f"listening 127.0.0.1:{PORT}/{PORT+1} for {SECS:.0f}s -> {OUT}/"
      + (f"  [receiver {RX_ID}]" if RX_ID is not None else ""), flush=True)
```

- [ ] **Step 6: Pass `src_addr` through to the database**

`sdr_db.upsert_call` already accepts `src_addr=`, and `flush()` already splats
`**call.get('meta', {})`. Task 1 added `src_addr` to `metadata()`, so this
already works — **verify** rather than change:

Run:

```bash
cd /home/besquivel/rtl && python3 - <<'EOF'
import inspect, sys; sys.path.insert(0, 'scripts')
import sdr_db, op25_log
keys = set(op25_log.LogTail('/dev/null').metadata())
params = set(inspect.signature(sdr_db.upsert_call).parameters)
print('metadata keys not accepted by upsert_call:', sorted(keys - params))
EOF
```

Expected: `[]`. Anything listed would raise `TypeError` on the first recorded
call — add it to `upsert_call` before proceeding.

- [ ] **Step 7: Verify the existing callers still work unchanged**

Run: `python3 -m unittest discover -s scripts/tests -v && npx vitest run`
Expected: all PASS. Then confirm the positional path is untouched:

```bash
timeout 3 python3 scripts/udp_audio_record.py 23999 2 /tmp/rectest \
  results/op25_record.log; echo "exit=$?"
timeout 3 python3 scripts/udp_audio_record.py --rx-id 3 23998 2 /tmp/rectest \
  results/op25_record.log; echo "exit=$?"
```

Expected: both print a `listening 127.0.0.1:...` banner, the second with
`[receiver 3]`, and both exit 0.

- [ ] **Step 8: Commit**

```bash
git add scripts/op25_log.py scripts/udp_audio_record.py scripts/tests/test_op25_log.py
git commit -m "feat: udp_audio_record --rx-id, so N recorders can share one op25 log"
```

---

## Task 3: The `multi_rx` config generator

All the arithmetic that fails silently on the air is checked here instead.

**Files:**
- Create: `scripts/make_multirx_cfg.py`
- Modify: `scripts/tests/test_static.py:33`
- Test: `scripts/tests/test_multirx_cfg.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `get_decim(rate: int) -> tuple[int, int] | None`
  - `if_rate_for(rate: int) -> int`
  - `usable_half_span(rate: int, usable_bw: float, if_rate: int) -> float` (Hz)
  - `LEG_700: dict` and `LEG_800: dict`, each with keys
    `name, centre, rate, voice, control, dc_guard`
  - `build(leg: dict, whitelist: str, cc_whitelist: str, tgid_tags: str,
     n_voice: int, base_port: int = 23460, nac: str = '0x1bd',
     sysname: str = 'LWIN-BR', usable_bw: float = 0.85,
     crypt_behavior: int = 1) -> dict`
  - `validate(cfg: dict, leg: dict, usable_bw: float = 0.85) -> None`
    (raises `ValueError`)

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_multirx_cfg.py`:

```python
#!/usr/bin/env python3
"""Tests for the multi_rx config generator.

These assertions are the ones that would otherwise be discovered on the air as
"Unable to tune" or a channel that silently pays an arb_resampler. The numbers
come from docs/2026-08-31-wideband-multichannel.md and from op25's own
p25_demodulator_dev.get_decim / set_relative_frequency.
"""
from __future__ import annotations

import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import make_multirx_cfg as M


class TestDecimationAgreesWithOp25(unittest.TestCase):
    """A wrong if_rate is silent: every channel just gets an extra resampler."""

    def test_8_msps_resolves_via_25000(self):
        self.assertEqual(M.get_decim(8_000_000), (80, 4))
        self.assertEqual(M.if_rate_for(8_000_000), 25000)

    def test_12_msps_resolves_via_24000(self):
        self.assertEqual(M.get_decim(12_000_000), (125, 4))
        self.assertEqual(M.if_rate_for(12_000_000), 24000)

    def test_16_msps_resolves_via_25000(self):
        self.assertEqual(M.if_rate_for(16_000_000), 25000)

    def test_2_msps_matches_what_rx_py_uses_today(self):
        self.assertEqual(M.get_decim(2_000_000), (20, 4))

    def test_a_rate_op25_cannot_decimate_is_rejected(self):
        self.assertIsNone(M.get_decim(7_000_000))
        with self.assertRaises(ValueError):
            M.if_rate_for(7_000_000)


class TestUsableHalfSpan(unittest.TestCase):
    """p25_demodulator_dev bounds on if_rate (25 kHz), NOT if1 (100 kHz)."""

    def test_8_msps(self):
        self.assertAlmostEqual(
            M.usable_half_span(8_000_000, 0.85, 25000), 3_387_500.0, places=1)

    def test_12_msps(self):
        self.assertAlmostEqual(
            M.usable_half_span(12_000_000, 0.85, 24000), 5_088_000.0, places=1)

    def test_10_msps_cannot_reach_the_800_leg(self):
        need = max(abs(f - M.LEG_800['centre'])
                   for f in M.LEG_800['voice'] + M.LEG_800['control'])
        self.assertLess(M.usable_half_span(10_000_000, 0.85, 25000), need)


class TestSevenHundredLeg(unittest.TestCase):
    CFG = None

    @classmethod
    def setUpClass(cls):
        cls.CFG = M.build(M.LEG_700, whitelist='/tmp/wl.txt',
                          cc_whitelist='/tmp/cc.txt', tgid_tags='',
                          n_voice=3)

    def test_one_device_marked_not_tunable(self):
        devs = self.CFG['devices']
        self.assertEqual(len(devs), 1)
        self.assertFalse(devs[0]['tunable'])

    def test_rate_and_centre_match_the_plan(self):
        d = self.CFG['devices'][0]
        self.assertEqual(d['rate'], 8_000_000)
        self.assertEqual(d['frequency'], 771_418_500)

    def test_one_control_channel_plus_n_voice(self):
        self.assertEqual(len(self.CFG['channels']), 4)

    def test_every_channel_uses_the_matching_if_rate(self):
        for ch in self.CFG['channels']:
            self.assertEqual(ch['if_rate'], 25000)

    def test_every_channel_is_bound_to_the_one_device(self):
        for ch in self.CFG['channels']:
            self.assertEqual(ch['device'], self.CFG['devices'][0]['name'])

    def test_control_channel_is_pinned_by_its_own_whitelist(self):
        cc = self.CFG['channels'][0]
        self.assertEqual(cc['whitelist'], '/tmp/cc.txt')

    def test_voice_channels_get_the_real_whitelist(self):
        for ch in self.CFG['channels'][1:]:
            self.assertEqual(ch['whitelist'], '/tmp/wl.txt')

    def test_udp_ports_are_spaced_at_least_two_apart(self):
        ports = [int(ch['destination'].rsplit(':', 1)[1])
                 for ch in self.CFG['channels']]
        for a, b in zip(sorted(ports), sorted(ports)[1:]):
            self.assertGreaterEqual(b - a, 2)

    def test_destinations_are_loopback_not_0_0_0_0(self):
        for ch in self.CFG['channels']:
            self.assertTrue(ch['destination'].startswith('udp://127.0.0.1:'))

    def test_crypt_behavior_records_encrypted_calls_as_silence(self):
        for ch in self.CFG['channels']:
            self.assertEqual(ch['crypt_behavior'], 1)

    def test_both_control_channels_are_in_the_rotation_list(self):
        chans = self.CFG['trunking']['chans']
        self.assertEqual(len(chans), 1)
        self.assertIn('773.05625', chans[0]['control_channel_list'])
        self.assertIn('774.54375', chans[0]['control_channel_list'])

    def test_no_audio_section_this_host_has_no_sound_card(self):
        self.assertNotIn('audio', self.CFG)

    def test_it_validates(self):
        M.validate(self.CFG, M.LEG_700)


class TestValidationCatchesRealMistakes(unittest.TestCase):

    def test_a_frequency_outside_the_window_is_rejected(self):
        leg = dict(M.LEG_700, voice=M.LEG_700['voice'] + [860_237_500])
        cfg = M.build(leg, '/tmp/wl.txt', '/tmp/cc.txt', '', n_voice=3)
        with self.assertRaises(ValueError) as e:
            M.validate(cfg, leg)
        self.assertIn('outside', str(e.exception).lower())

    def test_a_centre_sitting_on_a_channel_is_rejected(self):
        leg = dict(M.LEG_700, centre=M.LEG_700['voice'][0])
        cfg = M.build(leg, '/tmp/wl.txt', '/tmp/cc.txt', '', n_voice=3)
        with self.assertRaises(ValueError) as e:
            M.validate(cfg, leg)
        self.assertIn('dc', str(e.exception).lower())

    def test_a_tunable_device_with_several_channels_is_rejected(self):
        cfg = M.build(M.LEG_700, '/tmp/wl.txt', '/tmp/cc.txt', '', n_voice=3)
        cfg['devices'][0]['tunable'] = True
        with self.assertRaises(ValueError) as e:
            M.validate(cfg, M.LEG_700)
        self.assertIn('tunable', str(e.exception).lower())

    def test_asking_for_more_voice_channels_than_ports_allow_is_rejected(self):
        with self.assertRaises(ValueError):
            M.build(M.LEG_700, '/tmp/wl.txt', '/tmp/cc.txt', '', n_voice=0)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -v -k multirx_cfg`
Expected: FAIL — `ModuleNotFoundError: No module named 'make_multirx_cfg'`

- [ ] **Step 3: Create `scripts/make_multirx_cfg.py`**

```python
#!/usr/bin/env python3
"""Build and validate an op25 multi_rx.py config for LWIN Baton Rouge.

Why a generator rather than a checked-in JSON file: every number here fails
SILENTLY when it is wrong.

  * A frequency outside the device window makes tk_p25.py:2300 tune_voice
    claim the talkgroup anyway -- change_freq returns False after the claim --
    so the receiver records nothing and stays occupied for the call.
  * An if_rate that does not match get_decim's second stage costs an
    arb_resampler per channel and never says so.
  * A device centre that lands on a real channel puts the DC spike in its
    passband. That is the trap of commit cf019d4.
  * UDP ports closer than 2 apart collide: op25_audio.cc:298 sends on
    d_audio_port + slot_id and udp_audio_record.py binds PORT and PORT+1.

validate() asserts all four before the radio is opened.

Usage:
    python3 scripts/make_multirx_cfg.py --leg 700 \
        --whitelist lwin_active_whitelist.txt \
        --cc-whitelist lwin_nofollow.txt \
        --n-voice 3 -o lwin_700.json
"""
from __future__ import annotations

import argparse
import json
import sys

# ---------------------------------------------------------------------------
# Copied from op25's p25_demodulator_dev.get_decim (the module multi_rx.py:62
# actually imports -- NOT p25_demodulator, whose bound differs). Duplicated
# rather than imported because op25 lives outside this package and importing
# it drags in GNU Radio.
# ---------------------------------------------------------------------------
def get_decim(speed: int) -> tuple[int, int] | None:
    s = int(speed)
    for i_f in (24000, 25000, 32000):
        if s % i_f != 0:
            continue
        q = s // i_f
        if q & 1:
            continue
        if q >= 40 and q & 3 == 0:
            return q // 4, 4
        return q // 2, 2
    return None


def if_rate_for(rate: int) -> int:
    """The if2 that get_decim lands on -- the only if_rate that avoids a resampler."""
    d = get_decim(rate)
    if d is None:
        raise ValueError(f'op25 cannot two-stage decimate {rate} Hz; '
                         f'pick a rate divisible by 24000, 25000 or 32000 '
                         f'with an even quotient')
    decim, decim2 = d
    return rate // decim // decim2


def usable_half_span(rate: int, usable_bw: float, if_rate: int) -> float:
    """p25_demodulator_dev.set_relative_frequency's bound, in Hz.

        abs(offset) > (input_rate * usable_bw)/2 - if_rate/2  ->  refuse to tune

    Note if_rate (24-25 kHz), not if1 (96-100 kHz): the _dev module differs
    from p25_demodulator.py here, and _dev is the one multi_rx imports.
    """
    return (rate * usable_bw) / 2 - if_rate / 2


# ---------------------------------------------------------------------------
# The two legs of LWIN RFSS 1 site 13 "Baton Rouge Simulcast".
# Frequencies: RadioReference, cross-checked against the grants table in
# sdr.db (3,765 grants, complete census -- see the feasibility doc section 2).
# ---------------------------------------------------------------------------
LEG_700 = {
    'name': '700',
    'centre': 771_418_500,          # 662 kHz clear of the nearest audible carrier
    'rate': 8_000_000,
    'voice': [769_681_250, 769_931_250, 770_756_250, 772_681_250],
    'control': [773_056_250, 774_543_750],
    'dc_guard': 100_000,
}

LEG_800 = {
    'name': '800',
    'centre': 855_725_000,
    'rate': 12_000_000,
    'voice': [851_287_500, 851_837_500, 852_037_500, 852_150_000, 852_350_000,
              852_562_500, 852_750_000, 852_912_500, 852_987_500,
              857_237_500, 858_237_500, 860_237_500],
    # Measured dead 2026-08-31 (+0.5 dB, 0% continuity). Kept for the record;
    # the 800 leg has no usable control channel, which is why it is Phase 1
    # and needs a second receiver on 773.05625.
    'control': [],
    'dc_guard': 100_000,
}

LEGS = {'700': LEG_700, '800': LEG_800}


def build(leg: dict, whitelist: str, cc_whitelist: str, tgid_tags: str,
          n_voice: int, base_port: int = 23460, nac: str = '0x1bd',
          sysname: str = 'LWIN-BR', usable_bw: float = 0.85,
          crypt_behavior: int = 1) -> dict:
    if n_voice < 1:
        raise ValueError('need at least one voice channel')
    rate = leg['rate']
    if_rate = if_rate_for(rate)
    dev_name = 'hrf0'

    def chan(name, freq, wl, port, ess_ok=True):
        return {
            'name': name,
            'device': dev_name,
            'trunking_sysname': sysname,
            'demod_type': 'cqpsk',
            'filter_type': 'rc',
            'excess_bw': 0.2,
            'frequency': freq,
            'if_rate': if_rate,
            'symbol_rate': 4800,          # P25 Phase 1 C4FM; this system is Phase I
            'destination': f'udp://127.0.0.1:{port}',
            'meta_stream_name': '',
            'plot': '',
            'enable_analog': 'off',
            'whitelist': wl,
            'blacklist': '',
            'crypt_keys': '',
            'crypt_behavior': crypt_behavior,
        }

    channels = []
    # Channel 0 is the pinned control receiver. Its whitelist holds only a
    # talkgroup that does not exist, so find_talkgroup never matches and it
    # never calls tune_voice -- the same trick lwin_cdr_run.sh uses. It keeps
    # the grant census at 100% while the others record audio, which
    # OBSERVATIONS.md section 3.3 records as impossible with one receiver.
    cc_freq = leg['control'][0] if leg['control'] else leg['centre']
    channels.append(chan('CC', cc_freq, cc_whitelist, base_port))
    for i in range(n_voice):
        port = base_port + 2 * (i + 1)
        start = leg['voice'][i % len(leg['voice'])]
        channels.append(chan(f'VC{i}', start, whitelist, port))

    cfg = {
        'channels': channels,
        'devices': [{
            'name': dev_name,
            'args': 'soapy=0,driver=hackrf',
            'gains': 'AMP:0,LNA:40,VGA:44',
            'frequency': leg['centre'],
            'rate': rate,
            'usable_bw_pct': usable_bw,
            'tunable': False,       # mandatory: multi_rx.py:754 refuses to share a tunable device
            'offset': 0,
            'ppm': 0.0,
        }],
        'trunking': {
            'module': 'tk_p25.py',
            'chans': [{
                'nac': nac,
                'sysname': sysname,
                'control_channel_list': ','.join(
                    f'{f/1e6:.5f}'.rstrip('0') for f in leg['control']
                ) or f'{leg["centre"]/1e6:.5f}',
                'whitelist': '',
                'blacklist': '',
                'tgid_tags_file': tgid_tags,
                'rid_tags_file': '',
                'tdma_cc': False,
                'crypt_behavior': crypt_behavior,
            }],
        },
        # No "audio" section: this host has no sound card and snd-aloop is
        # unavailable, so op25's -U/-O paths are unusable (OBSERVATIONS 3.4).
        # Audio leaves over UDP only. No "terminal" section either: multi_rx
        # treats both as optional (multi_rx.py:578-597), and dropping the
        # terminal stops op25 rewriting a curses status line into the log we
        # tail.
    }
    return cfg


def validate(cfg: dict, leg: dict, usable_bw: float | None = None) -> None:
    dev = cfg['devices'][0]
    rate, centre = dev['rate'], dev['frequency']
    usable_bw = dev['usable_bw_pct'] if usable_bw is None else usable_bw
    if_rate = if_rate_for(rate)

    if dev['tunable'] and len(cfg['channels']) > 1:
        raise ValueError(
            'device is marked tunable and carries more than one channel; '
            'multi_rx.py:754 will drop every channel after the first')

    for ch in cfg['channels']:
        if ch['if_rate'] != if_rate:
            raise ValueError(
                f"channel {ch['name']} has if_rate {ch['if_rate']} but "
                f"get_decim({rate}) lands on {if_rate}; every channel would "
                f"pay an arb_resampler")

    limit = usable_half_span(rate, usable_bw, if_rate)
    for f in leg['voice'] + leg['control']:
        if abs(f - centre) > limit:
            raise ValueError(
                f'{f/1e6:.5f} MHz is outside the device window: offset '
                f'{abs(f-centre)/1e6:.4f} MHz > limit {limit/1e6:.4f} MHz. '
                f'tune_voice would claim the call and record nothing')

    for f in leg['voice'] + leg['control']:
        if abs(f - centre) < leg['dc_guard']:
            raise ValueError(
                f'device centre {centre/1e6:.5f} is within '
                f'{leg["dc_guard"]/1e3:.0f} kHz of channel {f/1e6:.5f}; '
                f'the DC spike would land in its passband (see commit cf019d4)')

    ports = sorted(int(ch['destination'].rsplit(':', 1)[1])
                   for ch in cfg['channels'])
    for a, b in zip(ports, ports[1:]):
        if b - a < 2:
            raise ValueError(
                f'UDP ports {a} and {b} are less than 2 apart; op25 sends on '
                f'port+slot_id and the recorder binds port and port+1')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--leg', choices=sorted(LEGS), default='700')
    ap.add_argument('--whitelist', required=True)
    ap.add_argument('--cc-whitelist', required=True,
                    help='a file holding only a non-existent talkgroup, so the '
                         'control receiver never retunes')
    ap.add_argument('--tgid-tags', default='')
    ap.add_argument('--n-voice', type=int, default=3)
    ap.add_argument('--base-port', type=int, default=23460)
    ap.add_argument('--rate', type=int, help='override the leg default')
    ap.add_argument('--centre', type=int, help='override the leg default, Hz')
    ap.add_argument('-o', '--out', required=True)
    a = ap.parse_args()

    leg = dict(LEGS[a.leg])
    if a.rate:
        leg['rate'] = a.rate
    if a.centre:
        leg['centre'] = a.centre

    cfg = build(leg, a.whitelist, a.cc_whitelist, a.tgid_tags,
                a.n_voice, a.base_port)
    validate(cfg, leg)

    with open(a.out, 'w') as fh:
        json.dump(cfg, fh, indent=4)
        fh.write('\n')

    if_rate = if_rate_for(leg['rate'])
    limit = usable_half_span(leg['rate'], cfg['devices'][0]['usable_bw_pct'], if_rate)
    need = max(abs(f - leg['centre']) for f in leg['voice'] + leg['control'])
    print(f"{a.out}: leg {a.leg}, {leg['rate']/1e6:.0f} Msps @ "
          f"{leg['centre']/1e6:.4f} MHz, if_rate {if_rate}")
    print(f"  {len(cfg['channels'])} channels "
          f"(1 pinned control + {a.n_voice} voice), ports "
          f"{a.base_port}..{a.base_port + 2*a.n_voice}")
    print(f"  window +/-{limit/1e6:.4f} MHz, widest channel offset "
          f"+/-{need/1e6:.4f} MHz, margin {(limit-need)/1e6:.4f} MHz")
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s scripts/tests -v -k multirx_cfg`
Expected: all PASS

- [ ] **Step 5: Add to IMPORTABLE and re-run the whole suite**

`scripts/tests/test_static.py:33`:

```python
IMPORTABLE = ['sdr_db', 'op25_log', 'make_multirx_cfg']
```

Run: `python3 -m unittest discover -s scripts/tests -v`
Expected: all PASS.

Note `make_multirx_cfg.py` *does* have a `__main__` guard, which is correct —
the pin in `test_recorder_has_no_import_time_side_effects_we_forgot_about`
applies only to `udp_audio_record.py`.

- [ ] **Step 6: Generate the real config and eyeball it**

```bash
cd /home/besquivel/rtl
echo 999999 > lwin_nofollow.txt
python3 scripts/make_whitelist.py --preset pd-all --include-partial \
        -o lwin_active_whitelist.txt
python3 scripts/make_multirx_cfg.py --leg 700 \
        --whitelist "$PWD/lwin_active_whitelist.txt" \
        --cc-whitelist "$PWD/lwin_nofollow.txt" \
        --n-voice 3 -o lwin_700.json
python3 -m json.tool lwin_700.json | head -40
```

Expected output includes:

```
lwin_700.json: leg 700, 8 Msps @ 771.4185 MHz, if_rate 25000
  4 channels (1 pinned control + 3 voice), ports 23460..23466
  window +/-3.3875 MHz, widest channel offset +/-3.1253 MHz, margin 0.2623 MHz
```

- [ ] **Step 7: Commit**

```bash
git add scripts/make_multirx_cfg.py scripts/tests/test_multirx_cfg.py \
        scripts/tests/test_static.py lwin_700.json lwin_nofollow.txt
git commit -m "feat: make_multirx_cfg.py — validated multi_rx configs for both LWIN legs"
```

---

## Task 4: The launcher

**Files:**
- Create: `scripts/lwin_listen_700.sh` (chmod +x)
- Test: manual, per the steps below

**Interfaces:**
- Consumes: `make_multirx_cfg.py`, `make_whitelist.py`, `udp_audio_record.py --rx-id`
- Produces: `scripts/lwin_listen_700.sh [options] [seconds]`, same talkgroup-selection
  flags as `lwin_listen.sh`, plus `--n-voice N`.

- [ ] **Step 1: Create the launcher**

```bash
#!/usr/bin/env bash
# Listen to LWIN's 700 MHz leg with SEVERAL receivers at once, on one HackRF.
#
# Unlike lwin_listen.sh (op25 rx.py, one receiver that must leave the control
# channel to hear a call), this runs op25 multi_rx.py with four channels on a
# single 8 Msps window: one PINNED to the control channel plus three voice
# receivers. So a run captures audio AND a 100% grant census at the same time,
# which OBSERVATIONS.md section 3.3 records as impossible with one receiver.
#
# Coverage is site 13's 700 MHz leg only: 769.68125, 769.93125, 770.75625,
# 772.68125 voice plus 773.05625 / 774.54375 control. The 800 MHz leg carries
# 73% of calls but is 78-87 MHz away, which no sample rate spans -- that is
# Phase 1 and needs a second receiver. See
# docs/2026-08-31-wideband-multichannel.md.
#
# Usage: lwin_listen_700.sh [options] [seconds]
#   --n-voice N         voice receivers (default 3; 3 covers all four channels)
#   everything else     the same talkgroup-selection flags as lwin_listen.sh,
#                       passed through to make_whitelist.py
#   -h, --help          this help
set -u
R=/home/besquivel/rtl
A=$R/src/op25/op25/gr-op25_repeater/apps
BASE_PORT=23460
WL=$R/lwin_active_whitelist.txt
CCWL=$R/lwin_nofollow.txt
CFG=$R/lwin_700.json
LOG=$R/results/op25_700.log
SECS=0
NVOICE=3
GEN=()

usage() { sed -n '2,/^set -u/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --n-voice)           NVOICE="$2"; shift ;;
    --pd)                GEN+=(--preset pd) ;;
    --pd-all)            GEN+=(--preset pd-all) ;;
    --fire)              GEN+=(--preset fire) ;;
    --fire-all)          GEN+=(--preset fire-all) ;;
    --ems)               GEN+=(--preset ems) ;;
    --interop)           GEN+=(--preset interop) ;;
    --preset)            GEN+=(--preset "$2"); shift ;;
    --tag)               GEN+=(--tag "$2"); shift ;;
    --tg)                GEN+=(--tg "$2"); shift ;;
    --match)             GEN+=(--match "$2"); shift ;;
    --all-areas)         GEN+=(--all-areas) ;;
    --include-partial)   GEN+=(--include-partial) ;;
    --include-encrypted) GEN+=(--include-encrypted) ;;
    --list)              GEN+=(--list); LIST=1 ;;
    -h|--help)           usage ;;
    -*)                  echo "unknown option: $1" >&2; exit 1 ;;
    *)                   SECS="$1" ;;
  esac
  shift
done

python3 "$R/scripts/make_whitelist.py" "${GEN[@]+"${GEN[@]}"}" -o "$WL" || exit $?
[ -n "${LIST:-}" ] && exit 0

# The control receiver's whitelist holds one talkgroup that does not exist, so
# find_talkgroup never matches for it and it never leaves the control channel.
echo 999999 > "$CCWL"

python3 "$R/scripts/make_multirx_cfg.py" --leg 700 \
        --whitelist "$WL" --cc-whitelist "$CCWL" \
        --n-voice "$NVOICE" --base-port "$BASE_PORT" -o "$CFG" || exit $?

mkdir -p "$R/recordings" "$R/results"
[ "$SECS" -eq 0 ] 2>/dev/null && RUN=99999 || RUN=$SECS
: > "$LOG"

REC_PIDS=()
cleanup() {
  echo; echo "stopping..."
  [ -n "${OP25_PID:-}" ] && kill "$OP25_PID" 2>/dev/null
  pkill -f "python3 multi_rx\.py" 2>/dev/null
  for p in "${REC_PIDS[@]+"${REC_PIDS[@]}"}"; do kill -INT "$p" 2>/dev/null; done
  wait 2>/dev/null
  n=$(ls -1 "$R"/recordings/TG*.wav 2>/dev/null | wc -l)
  echo "-> $n call(s) in $R/recordings/"
  exit 0
}
trap cleanup INT TERM

# One recorder per VOICE channel. Channel 0 is the pinned control receiver and
# produces no audio, so receiver ids start at 1 and ports at BASE_PORT+2.
for i in $(seq 1 "$NVOICE"); do
  port=$((BASE_PORT + 2*i))
  python3 "$R/scripts/udp_audio_record.py" --rx-id "$i" \
          "$port" "$RUN" "$R/recordings" "$LOG" &
  REC_PIDS+=($!)
done
sleep 2

# Under a pty so the log is written in real time (python3 -u breaks op25 on 3.14).
OP25_CMD="cd $A && exec python3 multi_rx.py -c $CFG -v 2"
script -q -f -c "$OP25_CMD" "$LOG" >/dev/null 2>&1 &
OP25_PID=$!

echo "LWIN Baton Rouge Simulcast, 700 MHz leg — 8 Msps @ 771.4185 MHz"
echo "1 pinned control receiver + $NVOICE voice receivers"
echo "whitelist: $(wc -l < "$WL") talkgroups -> $R/recordings/"
[ "$SECS" -eq 0 ] 2>/dev/null && echo "Ctrl-C to stop." || echo "running ${SECS}s."

wait "${REC_PIDS[0]}"
cleanup
```

- [ ] **Step 2: Make it executable and check the help path takes no radio**

```bash
chmod +x scripts/lwin_listen_700.sh
./scripts/lwin_listen_700.sh --help
./scripts/lwin_listen_700.sh --pd-all --include-partial --list | tail -5
```

Expected: help text, then a talkgroup listing. Neither opens the HackRF.

- [ ] **Step 3: Commit**

```bash
git add scripts/lwin_listen_700.sh
git commit -m "feat: lwin_listen_700.sh — multi-receiver capture on one HackRF"
```

---

## Task 5: Live run and acceptance gates

No code. This is the task that decides whether Phase 0 shipped.

**Files:** none. Produces `results/op25_700.log` and rows in `sdr.db`.

- [ ] **Step 1: Confirm the radio is free**

```bash
ps aux | grep -E "rx\.py|multi_rx|udp_audio" | grep -v grep
hackrf_info | head -6
```

Expected: no op25 processes; `Found HackRF`, `Board ID Number: 5 (HackRF Pro)`.
If `hackrf_open() failed: Resource busy`, stop the other session first.

- [ ] **Step 2: Run for 5 minutes**

```bash
cd /home/besquivel/rtl
./scripts/lwin_listen_700.sh --pd-all --include-partial 300
```

- [ ] **Step 3: Gate 1 — four channels came up on one device**

```bash
sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' results/op25_700.log \
  | grep -aE "Using two-stage decimator|destination:|Channel .* ignored|not within spectrum"
```

Expected: four `Using two-stage decimator for speed=8000000, decim=80/4 if1=100000 if2=25000`
lines and four `op25_audio::op25_audio: destination: udp://127.0.0.1:2346[0246]` lines.
**Any** `cannot share a tunable device` or `not within spectrum band` line is a
config bug — go back to Task 3 and fix `validate()` so it catches it.

- [ ] **Step 4: Gate 2 — no channel was ever asked to tune outside its window**

```bash
grep -ac "Unable to tune" results/op25_700.log
```

Expected: **0**. A non-zero count means a grant landed outside the 8 Msps window
and that receiver claimed a call it could not hear.

- [ ] **Step 5: Gate 3 — the control receiver never left the control channel**

```bash
sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' results/op25_700.log \
  | grep -aoE "\[0\] (voice update|releasing control channel)" | sort | uniq -c
```

Expected: **no output at all**. Receiver 0 is pinned; if it ever logs
`voice update` or `releasing control channel`, the `999999` whitelist did not
take and the grant census is no longer complete.

- [ ] **Step 6: Gate 4 — several calls really were recorded concurrently**

```bash
python3 - <<'EOF'
import sqlite3
c = sqlite3.connect('file:sdr.db?mode=ro', uri=True)
rows = list(c.execute(
    "select file, tgid, start, dur from calls "
    "where start > strftime('%s','now') - 900 order by start"))
print(f"{len(rows)} calls in the last 15 min")
overlap = sum(
    1 for i, (f, t, s, d) in enumerate(rows)
    if any(s < s2 + d2 and s2 < s + d for _, _, s2, d2 in rows[:i]))
print(f"{overlap} of them overlap an earlier call in time")
EOF
```

Expected: `overlap` > 0. This is the whole point — with `rx.py` it is
structurally always 0.

- [ ] **Step 7: Gate 5 — per-channel metadata was not cross-attributed**

```bash
python3 - <<'EOF'
import sqlite3
c = sqlite3.connect('file:sdr.db?mode=ro', uri=True)
q = ("select tgid, freq, count(*) from calls "
     "where start > strftime('%s','now') - 900 and freq is not null "
     "group by tgid, freq order by 3 desc")
for tgid, freq, n in c.execute(q):
    print(f"  TG {tgid:>7}  {freq/1e6:10.5f} MHz  x{n}")
EOF
```

Expected: every `freq` is one of 769.68125 / 769.93125 / 770.75625 / 772.68125,
and each talkgroup maps to a plausible frequency. A talkgroup showing a
frequency another receiver was on means `--rx-id` filtering is wrong — go back
to Task 2.

Also confirm the MHz→Hz conversion landed: a `freq` of `769` or `770` (rather
than ~7.7e8) means `_to_hz` was skipped.

- [ ] **Step 8: Gate 6 — the grant census is complete**

```bash
python3 scripts/lwin_cdr.py 2>/dev/null || \
  sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' results/op25_700.log | grep -ac "voice update"
python3 -c "
import sqlite3
c=sqlite3.connect('file:sdr.db?mode=ro',uri=True)
print('grants in the last 15 min:',
      c.execute(\"select count(*) from grants where ts > strftime('%s','now')-900\").fetchone()[0])"
```

Expected: a healthy grant count — the 6-minute CDR reference run logged 3,765
grants in 359 s, so expect the same order of magnitude. Far fewer means the
pinned receiver is not holding the control channel.

- [ ] **Step 9: Compare against the baseline honestly**

Run `./scripts/lwin_listen.sh --pd-all --include-partial 300` for the same
duration at a comparable time of day, and record both call counts in
`OBSERVATIONS.md`. **Expect a comparable number of calls, not more** — Phase 0
covers only the 700 leg, which held 28 of 111 calls in the census, and today's
single receiver captured 28 of 111 across both legs. The win is that the census
is now complete and the plumbing is proven, not that the audio volume rose.
Note explicitly which talkgroups disappeared (anything granted on 851–860,
including TG 17165 BRPD Dispatch 1).

- [ ] **Step 10: Record the outcome and commit**

Append a Phase 0 result section to `docs/2026-08-31-wideband-multichannel.md`
with the six gate results, then:

```bash
git add docs/2026-08-31-wideband-multichannel.md OBSERVATIONS.md
git commit -m "docs: Phase 0 results — multi_rx on the 700 MHz leg"
```

---

## Self-Review

**Spec coverage** (against the four deliverables in the request and §8 of the feasibility doc):

| Requirement | Task |
|---|---|
| multi_rx JSON config, 8 Msps @ 771.4185, 1 pinned CC + 3 voice | Task 3 |
| `if_rate 25000` matching `get_decim` | Task 3, `if_rate_for` + test |
| `usable_bw_pct 0.85`, `tunable: false` | Task 3, `build` + `validate` |
| `crypt_behavior: 1` | Task 3, `build` + test |
| DC-spike clearance | Task 3, `validate` + test |
| `--rx-id` in `udp_audio_record.py` | Task 2 |
| One recorder per channel, ports ≥2 apart | Task 3 (`validate`), Task 4 (launcher) |
| Launcher alongside `lwin_listen.sh` | Task 4 |
| Tests | Tasks 1–3 |
| Cross-band trap avoided | Task 3 (single-leg legs), Task 5 Gate 2 |
| Phase 2 / TDMA check flagged | Not in scope — §5.2 of the feasibility doc; Gate 5 will surface it as an unexpected frequency |

**Placeholder scan:** no TBDs; every code step carries real code; the two
"verify rather than change" steps (Task 2 Step 6, Task 1 Step 8) carry runnable
commands and expected output.

**Type consistency:** `LogTail(path, rx_id=)` is used identically in Tasks 1, 2
and the tests. `metadata()` gains `src_addr` in Task 1 and it is checked against
`sdr_db.upsert_call`'s signature in Task 2 Step 6. `build()`/`validate()`
signatures match between the module, the tests and the CLI. `LEG_700['centre']`
is Hz everywhere (`771_418_500`), never MHz.

**Known gap, deliberately accepted:** Task 2's `--rx-id` filtering relies on
`tk_p25.py` prefixing `voice update` with `[N]`, which is verified from the
format string at `tk_p25.py:2623` but **not** from a real `multi_rx` capture —
we have never run `multi_rx` here. Task 5 Gate 5 is the check. If the prefix is
missing or differently placed, fix `_pat` and add the real line as a fixture.

---

## REVISION 2026-08-31 — a second HackRF was plugged in mid-plan

A **HackRF One** (serial `930c64dc275e54c3`) joined the Pro, and it **holds site
13's control channel**: `AMP:0,LNA:40,VGA:20`, +21.4 dB, 100% continuity, 1,459
TSBK updates / 26 talkgroups / 48 radio IDs / 1 startup timeout in 75 s. Both
radios also stream simultaneously with zero overruns (24.0 + 16.0 MB/s on one
480 Mbps hub). Full measurements in §10 of the feasibility doc.

Tasks 1 and 2 (the `op25_log` extraction and `--rx-id`) are **unaffected** —
they are prerequisites either way. The following changes apply from Task 3 on.

### R1. Device selection must be by serial — affects existing scripts today

The One enumerated as SoapySDR **index 0, ahead of the Pro**, so
`--args soapy=0,driver=hackrf` in `lwin_listen.sh:127`,
`lwin_capture_audio.sh:32`, `lwin_capture_enc.sh:37` and `lwin_cdr_run.sh:6`
now opens the **wrong radio** — the One, with the Pro's gain settings. Verified
fix, working through gr-osmosdr's Soapy backend:

```
soapy=0,driver=hackrf,serial=0000000000000000977c64de2d717413   # Pro
soapy=0,driver=hackrf,serial=0000000000000000930c64dc275e54c3   # One
```

Serial must be the full 32-character zero-padded form. **Do this before Task 3**,
as its own commit, so today's scripts keep working:

- [ ] Add `HRF_PRO=0000000000000000977c64de2d717413` and
      `HRF_ONE=0000000000000000930c64dc275e54c3` near the top of each of the
      four scripts and interpolate into `--args`.
- [ ] Verify each still opens the Pro: run with `-v 5` for 10 s and confirm the
      log says `Opening HackRF Pro #1 977c…`.
- [ ] `git commit -m "fix: address HackRFs by serial — the One enumerates first and stole soapy=0"`

### R2. `make_multirx_cfg.py` gains two-device support (replaces Task 3's single device)

`build()` grows a `devices` list rather than one device. Per-device fields:

| device | `args` | `gains` | `rate` | `frequency` |
|---|---|---|---|---|
| `one` | `soapy=0,driver=hackrf,serial=…930c64dc275e54c3` | `AMP:0,LNA:40,VGA:20` | 8000000 | 771418500 |
| `pro` | `soapy=1,driver=hackrf,serial=…977c64de2d717413` | `AMP:0,LNA:40,VGA:44` | 12000000 | 855725000 |

Both `tunable: false`, `usable_bw_pct 0.85`. `if_rate` is **per device**: 25000
for the One at 8 Msps, 24000 for the Pro at 12 Msps — `validate()` must check
each channel against *its own* device's rate, not a single global one. That is
the one real change to Task 3's tests.

Verify the `soapy=N` index does not have to match the device's enumeration order
(it is gr-osmosdr's own source counter): if two `soapy=0` sources conflict, use
`soapy=0` and `soapy=1` as above and confirm from the log which serial each
opened.

### R3. NEW REQUIRED TASK — patch `tk_p25.py tune_voice` to honour tuning failure

Previously a caveat avoided by staying single-leg; with receivers on both legs it
fires constantly. `multi_rx.py change_freq` returns `False` for an out-of-window
frequency, but `tk_p25.py:2300 tune_voice` ignores it *after* the talkgroup is
claimed, so the receiver records silence and stays occupied for the call.

- [ ] **Step 1:** Reproduce it. Build a deliberately-bad config — One only,
      3 voice channels, but leg `control` list pointing at 773.05625 while the
      whitelist admits 800-leg talkgroups. Run 120 s and confirm
      `grep -c "Unable to tune" results/op25_700.log` is > 0 while
      `sqlite3` shows calls with `dur` at the minimum and no audio.
- [ ] **Step 2:** In `tk_p25.py`, capture `tune_voice`'s outcome and release the
      claim on failure. `frequency_set` is `multi_rx.change_freq`, which already
      returns a bool:

```python
    def tune_voice(self, freq, tgid, slot):
        ...
        if (freq != self.tuned_frequency) or (slot != self.current_slot):
            ...
            if self.frequency_set(tune_params) is False:
                # This receiver's device window does not reach freq. Leave the
                # talkgroup unclaimed so a receiver on the other leg can take
                # it, instead of recording silence for the whole call.
                if self.debug > 0:
                    sys.stderr.write(
                        "%s [%d] cannot reach %f for tg(%d); releasing\n"
                        % (log_ts.get(), self.msgq_id, freq/1e6, tgid))
                with self.system.talkgroups_mutex:
                    if self.talkgroups[tgid]['receiver'] is self:
                        self.talkgroups[tgid]['receiver'] = None
                self.tuner_idle = True
                return
            self.tuned_frequency = freq
```

      **Check first** where `talkgroups[tgid]['receiver']` is actually assigned —
      it is set in `find_talkgroup`/`scan_for_talkgroups`, not in `tune_voice`,
      so the release must clear the same field the pool tests. Read
      `tk_p25.py:2576-2634` before writing this.
- [ ] **Step 3:** Re-run Step 1's bad config. Expected: `Unable to tune` still
      logged, but followed by a *different* receiver taking the same talkgroup,
      and no zero-audio calls in `sdr.db`.
- [ ] **Step 4:** Record the patch in `OBSERVATIONS.md` gotchas — op25 is a
      vendored dependency and an unrecorded local patch is lost on the next
      rebuild.
- [ ] **Step 5:** `git commit -m "fix(op25): release the talkgroup when a receiver cannot reach the granted frequency"`

### R4. Task 5's gates gain three checks

- Every device opened the serial it was configured for (`Opening HackRF …` lines).
- No `cannot reach … releasing` line appears for a frequency that *is* inside
  that receiver's window (would mean the window arithmetic is wrong, not the
  patch).
- Calls appear with frequencies from **both** legs — 769–772 *and* 851–860. This
  is the check that the whole exercise worked; with `rx.py` today, a run captures
  one call at a time from either.

### R5. Phase 0 is now optional

With both radios working, the single-radio 700-leg config is a bring-up step
rather than a destination. It is still the cheapest way to validate the
`multi_rx` plumbing (one device, one leg, no cross-band exposure, and the
cross-band patch in R3 not yet needed). Recommended order:

1. Tasks 1–2 (prerequisites, no radio needed).
2. R1 (serial addressing — fixes today's scripts, do it now regardless).
3. Task 3–5 as written, single device, One only — proves multi_rx end to end.
4. R2 + R3, then re-run Task 5 with both devices for ~92%.
