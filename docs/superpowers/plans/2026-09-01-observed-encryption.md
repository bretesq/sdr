# Observed Encryption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the *observed* encryption state of each call a stored fact — harvested from op25 logs, bound to calls by receiver and grant interval — then reconcile it against RadioReference through a human-approved override file, and feed the already-logged ciphertext into ADP key recovery.

**Architecture:** One pure parsing/binding module (`scripts/enc_log.py`) with no I/O, driven by one CLI (`scripts/enc_harvest.py`) that writes to `sdr.db`. This mirrors the existing `tencodes.py` + `backfill_codes.py` and `enc_pair.py` + `extract_enc_pair.py` split, so the logic is unit-testable without hardware, a log file, or a database.

**Tech Stack:** Python 3 stdlib only (`re`, `datetime`, `sqlite3`, `dataclasses`), existing `sdr_db.py` and `enc_pair.py`, Vue 3 / PrimeVue for the UI touch, `unittest` for tests.

**Spec:** `docs/superpowers/specs/2026-09-01-observed-encryption-design.md`

## Global Constraints

- Python: stdlib only. `scripts/` has no third-party dependencies and must keep none.
- `reference/lwin_talkgroups.json` is upstream and MUST NOT be written by any task here.
- No task may raise op25 verbosity or change what is captured. `lwin_listen_multi.sh` already runs `-v 10`.
- Schema changes go through `_DERIVED_COLUMNS` + `_migrate()` in `scripts/sdr_db.py`. Do NOT bump `_USER_VERSION` (that rebuilds the FTS index; nothing here changes FTS).
- Log timestamps are **local time** (`%m/%d/%y %H:%M:%S.%f`), and `calls.start` is epoch seconds. Verified aligned to within ±1.6 s.
- The call interval is `start .. start + dur`. NOT `ended_at` — it is NULL on 3247 of 4606 rows.
- Frequencies in `voice update:` are MHz floats (`851.837500`); `calls.freq` is Hz. Convert with `int(round(mhz * 1e6))`.
- Gate that must stay green: `./node_modules/.bin/eslint .`, `./node_modules/.bin/nuxt typecheck`, `./node_modules/.bin/vitest run`, `python3 -m unittest discover -s scripts/tests`.
- `pnpm run <script>` currently fails on unresolved `allowBuilds` in `pnpm-workspace.yaml`. Call the binaries in `node_modules/.bin/` directly.

---

### Task 1: Schema columns for observed encryption

**Files:**
- Modify: `scripts/sdr_db.py` (the `_DERIVED_COLUMNS` tuple, ~line 255)
- Test: `scripts/tests/test_sdr_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: columns `calls.enc_observed TEXT`, `calls.enc_evidence TEXT`, `calls.enc_source TEXT`, present after any `sdr_db.connect()`.

- [ ] **Step 1: Write the failing test**

Add to `scripts/tests/test_sdr_db.py`:

```python
def test_observed_encryption_columns_exist(self):
    """The harvester's output columns are added by the standard migration."""
    db = sdr_db.connect(self.path)
    try:
        cols = {r[1] for r in db.execute('PRAGMA table_info(calls)')}
        self.assertIn('enc_observed', cols)
        self.assertIn('enc_evidence', cols)
        self.assertIn('enc_source', cols)
    finally:
        db.close()

def test_migration_is_idempotent_for_enc_columns(self):
    """connect() runs on every open; a second pass must not raise."""
    sdr_db.connect(self.path).close()
    db = sdr_db.connect(self.path)          # would raise "duplicate column" if unguarded
    try:
        cols = {r[1] for r in db.execute('PRAGMA table_info(calls)')}
        self.assertIn('enc_observed', cols)
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_sdr_db -v`
Expected: FAIL — `AssertionError: 'enc_observed' not found in {...}`

- [ ] **Step 3: Add the columns**

In `scripts/sdr_db.py`, extend `_DERIVED_COLUMNS`:

```python
_DERIVED_COLUMNS = (
    ('transcript_norm', 'TEXT'),
    ('codes_text', 'TEXT'),
    ('codes_set_id', 'TEXT'),
    ('codes_rev', 'TEXT'),
    # Observed encryption, written by enc_harvest.py. Distinct from
    # talkgroups.enc, which is a scraped RadioReference label describing the
    # talkgroup in general rather than this transmission.
    ('enc_observed', 'TEXT'),   # 'clear' | 'encrypted' | 'mixed' | NULL
    ('enc_evidence', 'TEXT'),   # 'ess' | 'speech' | 'both' | NULL
    ('enc_source', 'TEXT'),     # 'harvest' (authoritative) | 'live'
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_sdr_db -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/sdr_db.py scripts/tests/test_sdr_db.py
git commit -m "feat(db): add observed-encryption columns to calls"
```

---

### Task 2: Parse timestamped grants and ESS observations from op25 logs

**Files:**
- Create: `scripts/enc_log.py`
- Test: `scripts/tests/test_enc_log.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass Grant(ts: float, rx_id: int, tgid: int, freq: int)` — `freq` in Hz
  - `@dataclass EncObs(ts: float, rx_id: int, algid: int, keyid: int, mi: str, rs_errs: int)`
  - `parse_log(text: str) -> tuple[list[Grant], list[EncObs]]` — both lists sorted by `ts`

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_enc_log.py`:

```python
#!/usr/bin/env python3
"""Parsing and binding tests for the encryption-fact harvester.

Every log fragment below is copied verbatim from results/op25_multi.log. The
formats are load-bearing: two spaces after "voice update:", MHz floats for freq,
and a trailing rs_errs the ESS line does not always carry.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import enc_log  # noqa: E402

GRANT = ('09/01/26 12:00:41.896175 [9] voice update:  tg(17051), rid(0), '
         'freq(851.837500), slot(-), prio(3)\n')
ESS_ENC = ('09/01/26 12:00:43.585551 [10] NAC 0x1bd LDU2: ESS: algid=aa, '
           'keyid=22, mi=e0 99 ec a0 6b 7f 72 1a 00, rs_errs=4\n')
ESS_CLEAR = ('09/01/26 12:00:44.131919 [9] NAC 0x1bd LDU2: ESS: algid=80, '
             'keyid=0, mi=00 00 00 00 00 00 00 00 00, rs_errs=0\n')


class ParseLog(unittest.TestCase):
    def test_parses_a_grant_with_mhz_freq_as_hz(self):
        grants, _ = enc_log.parse_log(GRANT)
        self.assertEqual(len(grants), 1)
        g = grants[0]
        self.assertEqual(g.rx_id, 9)
        self.assertEqual(g.tgid, 17051)
        # calls.freq is Hz; the log is MHz.
        self.assertEqual(g.freq, 851837500)

    def test_parses_ess_fields(self):
        _, obs = enc_log.parse_log(ESS_ENC)
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual((o.rx_id, o.algid, o.keyid, o.rs_errs), (10, 0xAA, 0x22, 4))
        self.assertEqual(o.mi, 'e0 99 ec a0 6b 7f 72 1a 00')

    def test_timestamps_are_local_epoch_seconds_and_ordered(self):
        _, obs = enc_log.parse_log(ESS_ENC + ESS_CLEAR)
        self.assertEqual(len(obs), 2)
        self.assertLess(obs[0].ts, obs[1].ts)
        self.assertGreater(obs[0].ts, 1_700_000_000)   # a real epoch, not 1970

    def test_strips_ansi_so_a_coloured_log_still_parses(self):
        # op25 runs under `script`, which preserves terminal escapes.
        coloured = '\x1b[0m' + GRANT.replace('[9]', '\x1b[32m[9]\x1b[0m')
        grants, _ = enc_log.parse_log(coloured)
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].rx_id, 9)

    def test_ess_without_rs_errs_defaults_to_zero(self):
        line = ('09/01/26 12:00:44.131919 [9] NAC 0x1bd LDU2: ESS: algid=80, '
                'keyid=0, mi=00 00 00 00 00 00 00 00 00\n')
        _, obs = enc_log.parse_log(line)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0].rs_errs, 0)

    def test_lines_without_a_receiver_id_are_ignored(self):
        # Binding is per-receiver; an observation we cannot attribute to a
        # receiver cannot be bound to a call and must not be invented.
        _, obs = enc_log.parse_log(ESS_ENC.replace('[10] ', ''))
        self.assertEqual(obs, [])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_enc_log -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enc_log'`

- [ ] **Step 3: Write the parser**

Create `scripts/enc_log.py`:

```python
#!/usr/bin/env python3
"""Parse op25 logs into timestamped, per-receiver encryption facts.

Pure functions: text in, dataclasses out. No I/O, no database, no globals — so
the binding rules can be tested without a radio, a log file or a schema.

WHY THIS EXISTS
---------------
op25_log.py reads the same lines live, but keeps a single ESS slot for
TG_TTL = 12 seconds with no talkgroup binding, because the op25 ESS line carries
no tgid. An encrypted call therefore stamps its ALGID onto the next clear call
on the same receiver. Post-hoc we have something the live path does not: the
full timeline. Grants give exact per-receiver boundaries, so an observation is
attributed to the grant that was actually active, not to whatever was seen
recently.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]')

# op25 runs under `script`, so timestamps are the local clock, not UTC.
_TS = r'(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d+)'
_TS_FMT = '%m/%d/%y %H:%M:%S.%f'

# 09/01/26 12:00:41.896175 [9] voice update:  tg(17051), rid(0), freq(851.837500), ...
# Two spaces after the colon, and freq is MHz here (trunking.py logs Hz; tk_p25
# logs MHz) — hence the explicit '.' test rather than a bare int().
GRANT_RE = re.compile(
    _TS + r' \[(\d+)\] voice update:\s+tg\((\d+)\),\s*(?:rid\(\d+\),\s*)?'
    r'freq\(([0-9.]+)\)')

# 09/01/26 12:00:43.585551 [10] NAC 0x1bd LDU2: ESS: algid=aa, keyid=22, mi=..., rs_errs=4
# rs_errs is optional: not every build emits it, and its absence must not drop
# the observation.
ESS_RE = re.compile(
    _TS + r' \[(\d+)\][^\n]{0,40}?ESS:\s*algid=([0-9a-f]+),\s*keyid=([0-9a-f]+),'
    r'\s*mi=([0-9a-f ]{26})(?:,\s*rs_errs=(\d+))?')


@dataclass
class Grant:
    ts: float           # epoch seconds
    rx_id: int          # op25 receiver index, the [N] prefix
    tgid: int
    freq: int           # Hz


@dataclass
class EncObs:
    ts: float
    rx_id: int
    algid: int
    keyid: int
    mi: str             # 9 MI bytes as logged, space-separated lowercase hex
    rs_errs: int        # Reed-Solomon residual errors; 0 means trustworthy


def _epoch(ts: str) -> float:
    """Log timestamps are local time; calls.start is epoch seconds."""
    return datetime.datetime.strptime(ts, _TS_FMT).timestamp()


def _to_hz(raw: str) -> int:
    """trunking.py logs Hz as an int; tk_p25.py logs MHz as a float."""
    return int(round(float(raw) * 1e6)) if '.' in raw else int(raw)


def parse_log(text: str) -> tuple[list[Grant], list[EncObs]]:
    """Extract every grant and ESS observation, each sorted by timestamp.

    Lines with no `[N]` receiver prefix are skipped: binding is per-receiver, and
    an observation that cannot be attributed to a receiver cannot be attributed
    to a call either.
    """
    text = ANSI.sub('', text)
    grants = [
        Grant(_epoch(m.group(1)), int(m.group(2)), int(m.group(3)),
              _to_hz(m.group(4)))
        for m in GRANT_RE.finditer(text)
    ]
    obs = [
        EncObs(_epoch(m.group(1)), int(m.group(2)), int(m.group(3), 16),
               int(m.group(4), 16), m.group(5).strip(),
               int(m.group(6)) if m.group(6) else 0)
        for m in ESS_RE.finditer(text)
    ]
    grants.sort(key=lambda g: g.ts)
    obs.sort(key=lambda o: o.ts)
    return grants, obs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_enc_log -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/enc_log.py scripts/tests/test_enc_log.py
git commit -m "feat(enc): parse timestamped per-receiver grants and ESS from op25 logs"
```

---

### Task 2 verification against the real log

**Files:**
- Test: `scripts/tests/test_enc_log.py`

**Interfaces:**
- Consumes: `enc_log.parse_log`
- Produces: nothing new.

- [ ] **Step 1: Add a guarded real-log test**

Append to `scripts/tests/test_enc_log.py`:

```python
class RealLog(unittest.TestCase):
    """Guards against op25 changing its log format under us.

    Skipped when the log is absent so the suite still runs on a clean checkout.
    """

    LOG = '/home/besquivel/rtl/results/op25_multi.log'

    def setUp(self):
        if not os.path.exists(self.LOG):
            self.skipTest('results/op25_multi.log not present')

    def test_finds_grants_and_ess_in_the_real_log(self):
        with open(self.LOG, errors='ignore') as f:
            grants, obs = enc_log.parse_log(f.read())
        self.assertGreater(len(grants), 100)
        self.assertGreater(len(obs), 100)

    def test_real_observations_carry_plausible_algids(self):
        with open(self.LOG, errors='ignore') as f:
            _, obs = enc_log.parse_log(f.read())
        algids = {o.algid for o in obs}
        # 0x80 clear and 0xAA ADP are both known present in this corpus.
        self.assertIn(0x80, algids)
        self.assertIn(0xAA, algids)
```

- [ ] **Step 2: Run and verify it passes**

Run: `python3 -m unittest scripts.tests.test_enc_log -v`
Expected: PASS. If `op25_multi.log` is absent, the two RealLog tests report `skipped`.

- [ ] **Step 3: Commit**

```bash
git add scripts/tests/test_enc_log.py
git commit -m "test(enc): guard the op25 log format against upstream drift"
```

---

### Task 3: Attribute observations to the grant active on their receiver

**Files:**
- Modify: `scripts/enc_log.py`
- Test: `scripts/tests/test_enc_log.py`

**Interfaces:**
- Consumes: `Grant`, `EncObs` from Task 2.
- Produces: `attribute(grants: list[Grant], obs: list[EncObs], *, max_age: float = 30.0) -> list[tuple[EncObs, Grant | None]]` — the `Grant` is `None` when the observation cannot be attributed.

- [ ] **Step 1: Write the failing test**

Append to `scripts/tests/test_enc_log.py`:

```python
class Attribute(unittest.TestCase):
    """The core fix: an ESS belongs to its own receiver's active grant."""

    def obs(self, ts, rx, algid=0xAA):
        return enc_log.EncObs(ts=ts, rx_id=rx, algid=algid, keyid=8,
                              mi='00 ' * 8 + '00', rs_errs=0)

    def grant(self, ts, rx, tgid):
        return enc_log.Grant(ts=ts, rx_id=rx, tgid=tgid, freq=851837500)

    def test_binds_to_the_most_recent_grant_on_the_same_receiver(self):
        grants = [self.grant(100.0, 9, 17051)]
        out = enc_log.attribute(grants, [self.obs(101.0, 9)])
        self.assertEqual(out[0][1].tgid, 17051)

    def test_never_binds_across_receivers(self):
        # THE bug being fixed: rx 10 was encrypted while rx 9 was clear at the
        # same moment. Attributing across receivers invents encrypted calls.
        grants = [self.grant(100.0, 9, 17051)]
        out = enc_log.attribute(grants, [self.obs(101.0, 10)])
        self.assertIsNone(out[0][1])

    def test_a_later_grant_supersedes_an_earlier_one(self):
        grants = [self.grant(100.0, 9, 17051), self.grant(110.0, 9, 6848)]
        out = enc_log.attribute(grants, [self.obs(111.0, 9)])
        self.assertEqual(out[0][1].tgid, 6848)

    def test_observation_before_any_grant_is_unbound(self):
        grants = [self.grant(100.0, 9, 17051)]
        out = enc_log.attribute(grants, [self.obs(99.0, 9)])
        self.assertIsNone(out[0][1])

    def test_stale_grant_does_not_capture_a_much_later_observation(self):
        # Without an age bound, one grant would own the rest of the log.
        grants = [self.grant(100.0, 9, 17051)]
        out = enc_log.attribute(grants, [self.obs(1000.0, 9)], max_age=30.0)
        self.assertIsNone(out[0][1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_enc_log.Attribute -v`
Expected: FAIL — `AttributeError: module 'enc_log' has no attribute 'attribute'`

- [ ] **Step 3: Implement attribution**

Append to `scripts/enc_log.py`:

```python
def attribute(grants: list[Grant], obs: list[EncObs],
              *, max_age: float = 30.0) -> list[tuple[EncObs, Grant | None]]:
    """Pair each observation with the grant active on ITS receiver.

    Per receiver, the active grant is the most recent one at or before the
    observation, provided it is no older than `max_age`. Anything else yields
    None — deliberately. Attributing an observation to the nearest grant on
    another receiver is exactly the cross-attribution this replaces: two
    receivers routinely carry different talkgroups, one encrypted and one clear,
    at the same instant.

    `max_age` bounds a grant's reach so one grant cannot own the remainder of
    the log after its call ends. 30 s comfortably exceeds a normal
    transmission while staying far below the gap between unrelated calls.
    """
    per_rx: dict[int, list[Grant]] = {}
    for g in grants:
        per_rx.setdefault(g.rx_id, []).append(g)

    out: list[tuple[EncObs, Grant | None]] = []
    for o in obs:
        active = None
        for g in per_rx.get(o.rx_id, ()):
            if g.ts > o.ts:
                break                       # grants are sorted by ts
            active = g
        if active is not None and o.ts - active.ts > max_age:
            active = None
        out.append((o, active))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_enc_log -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/enc_log.py scripts/tests/test_enc_log.py
git commit -m "feat(enc): bind ESS observations to the active grant per receiver"
```

---

### Task 4: Classify a call from its ALGIDs, and from speech

**Files:**
- Modify: `scripts/enc_log.py`
- Test: `scripts/tests/test_enc_log.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `classify(algids: list[int]) -> str | None` — `'clear'` / `'encrypted'` / `'mixed'` / `None`
  - `is_speech(transcript: str) -> bool`
  - `KNOWN_ALGIDS: frozenset[int]`

- [ ] **Step 1: Write the failing test**

Append to `scripts/tests/test_enc_log.py`:

```python
class Classify(unittest.TestCase):
    def test_clear_and_encrypted(self):
        self.assertEqual(enc_log.classify([0x80]), 'clear')
        self.assertEqual(enc_log.classify([0xAA]), 'encrypted')

    def test_a_call_carrying_both_is_mixed_not_a_coin_flip(self):
        self.assertEqual(enc_log.classify([0x80, 0xAA]), 'mixed')

    def test_no_observations_is_unknown(self):
        self.assertIsNone(enc_log.classify([]))

    def test_bit_error_algids_are_discarded_not_treated_as_ciphers(self):
        # 0x0E/0x45/0xA8/0xB8 each appear exactly once in the corpus next to
        # non-zero rs_errs. Four exotic ciphers is the wrong reading.
        self.assertIsNone(enc_log.classify([0x0E]))
        self.assertEqual(enc_log.classify([0x80, 0x45]), 'clear')


class Speech(unittest.TestCase):
    """Intelligible speech proves the audio was not encrypted.

    This is the only evidence available for the BRPD TLK talkgroups, which have
    zero ESS observations across 21 calls.
    """

    def test_real_dispatch_speech_counts(self):
        self.assertTrue(enc_log.is_speech('10-4, we are en route to the scene.'))

    def test_whisper_silence_artifacts_do_not_count(self):
        # medium.en emits these on dead air (8/599 clips). Counting them would
        # "prove" an encrypted talkgroup is clear.
        for junk in ('Thank you.', 'Bye.', '[BLANK_AUDIO]',
                     'Thanks for watching!', 'you', ''):
            self.assertFalse(enc_log.is_speech(junk), junk)

    def test_single_word_is_not_enough(self):
        self.assertFalse(enc_log.is_speech('Anyway.'))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_enc_log.Classify -v`
Expected: FAIL — `AttributeError: module 'enc_log' has no attribute 'classify'`

- [ ] **Step 3: Implement both classifiers**

Append to `scripts/enc_log.py`:

```python
# ALGIDs this system is known to use. Anything else is treated as a bit error
# rather than an unknown cipher: 0x0E, 0x45, 0xA8 and 0xB8 each appear exactly
# once across 4,606 calls, alongside non-zero rs_errs on the same ESS lines.
KNOWN_ALGIDS = frozenset({0x80, 0xAA, 0x81, 0x83, 0x84, 0x85})
CLEAR_ALGID = 0x80

# Whole transcripts whisper produces from dead air. Not speech, whatever the
# word count. Compared case-insensitively after stripping.
_ARTIFACTS = frozenset({
    'thank you.', 'thank you', 'bye.', 'bye', 'you', 'you.', 'oh,', 'and', 'or',
    '.', "i don't know.", 'or...', 'thanks for watching!', '[blank_audio]',
    '(silence)', '[silence]',
})


def classify(algids: list[int]) -> str | None:
    """Reduce a call's observed ALGIDs to one state.

    Returns None when nothing usable was observed — never a guess. 'mixed' is a
    real state: a call carrying both clear and encrypted bursts, which a single
    algid column silently hides.
    """
    known = [a for a in algids if a in KNOWN_ALGIDS]
    if not known:
        return None
    clear = any(a == CLEAR_ALGID for a in known)
    enc = any(a != CLEAR_ALGID for a in known)
    if clear and enc:
        return 'mixed'
    return 'clear' if clear else 'encrypted'


def is_speech(transcript: str) -> bool:
    """True if a transcript is evidence the audio was NOT encrypted.

    Encrypted bursts are silenced by op25 -n, so intelligible speech cannot come
    from them. Two guards keep whisper's silence confabulations from becoming
    false evidence: an exact-match artifact list, and a two-word minimum.
    """
    t = (transcript or '').strip()
    if not t or t.lower() in _ARTIFACTS:
        return False
    return len(t.split()) >= 2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_enc_log -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/enc_log.py scripts/tests/test_enc_log.py
git commit -m "feat(enc): classify calls from ALGIDs and from speech evidence"
```

---

### Task 5: The harvester CLI — bind to calls and write the columns

**Files:**
- Create: `scripts/enc_harvest.py`
- Test: `scripts/tests/test_enc_harvest.py`

**Interfaces:**
- Consumes: `enc_log.parse_log`, `enc_log.attribute`, `enc_log.classify`, `enc_log.is_speech`; `sdr_db.connect`.
- Produces: `harvest(db, log_text: str) -> dict[str, int]` with keys `bound`, `unbound`, `updated`, `speech_only`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_enc_harvest.py`:

```python
#!/usr/bin/env python3
"""Harvester tests: log text plus a database, no radio and no files."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import enc_harvest  # noqa: E402
import sdr_db  # noqa: E402

# A grant on rx 9 for TG17051, then an ESS on rx 9, then an ESS on rx 10 that
# belongs to no grant at all.
LOG = (
    '09/01/26 12:00:41.896175 [9] voice update:  tg(17051), rid(0), '
    'freq(851.837500), slot(-), prio(3)\n'
    '09/01/26 12:00:42.100000 [9] NAC 0x1bd LDU2: ESS: algid=80, keyid=0, '
    'mi=00 00 00 00 00 00 00 00 00, rs_errs=0\n'
    '09/01/26 12:00:42.200000 [10] NAC 0x1bd LDU2: ESS: algid=aa, keyid=22, '
    'mi=e0 99 ec a0 6b 7f 72 1a 00, rs_errs=0\n'
)


def _epoch(ts: str) -> float:
    import datetime
    return datetime.datetime.strptime(ts, '%m/%d/%y %H:%M:%S.%f').timestamp()


class Harvest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), 'test.db')
        self.db = sdr_db.connect(self.path)
        # One call covering the grant window on rx 9's talkgroup.
        sdr_db.upsert_call(
            self.db, file='TG17051_A_20260901-120041.wav', tgid=17051,
            start=_epoch('09/01/26 12:00:41.900000'), dur=3.0,
            freq=851837500)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def row(self):
        return self.db.execute(
            'SELECT enc_observed, enc_evidence, enc_source, algid '
            'FROM calls WHERE tgid = 17051').fetchone()

    def test_binds_the_clear_ess_to_the_call(self):
        enc_harvest.harvest(self.db, LOG)
        r = self.row()
        self.assertEqual(r['enc_observed'], 'clear')
        self.assertEqual(r['enc_evidence'], 'ess')
        self.assertEqual(r['enc_source'], 'harvest')
        self.assertEqual(r['algid'], 0x80)

    def test_the_other_receivers_ess_is_unbound_not_borrowed(self):
        # The whole point: rx 10's 0xAA must not reach rx 9's clear call.
        stats = enc_harvest.harvest(self.db, LOG)
        self.assertEqual(stats['unbound'], 1)
        self.assertEqual(self.row()['enc_observed'], 'clear')

    def test_is_idempotent(self):
        enc_harvest.harvest(self.db, LOG)
        first = dict(self.row())
        enc_harvest.harvest(self.db, LOG)
        self.assertEqual(dict(self.row()), first)

    def test_speech_alone_marks_a_call_clear_when_no_ess_exists(self):
        sdr_db.upsert_call(
            self.db, file='TG17166_B_20260901-120100.wav', tgid=17166,
            start=_epoch('09/01/26 12:01:00.000000'), dur=2.0)
        sdr_db.set_transcript(self.db, 'TG17166_B_20260901-120100.wav',
                              '10-4, we are en route to the scene.')
        self.db.commit()
        enc_harvest.harvest(self.db, LOG)
        r = self.db.execute("SELECT enc_observed, enc_evidence FROM calls "
                            "WHERE tgid = 17166").fetchone()
        self.assertEqual(r['enc_observed'], 'clear')
        self.assertEqual(r['enc_evidence'], 'speech')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_enc_harvest -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enc_harvest'`

- [ ] **Step 3: Write the harvester**

Create `scripts/enc_harvest.py`:

```python
#!/usr/bin/env python3
"""Harvest observed encryption facts from op25 logs into sdr.db.

Reads logs, binds ESS observations to the calls they actually belong to, and
writes calls.enc_observed / enc_evidence / enc_source. Runs outside the
recording path, so it cannot disturb capture, and is re-runnable, so it
backfills history.

Usage:
  enc_harvest.py [LOG ...] [--db PATH] [--report]

With no LOG, reads results/op25_multi.log.
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import enc_log
import sdr_db

R = os.environ.get('SDR_ROOT', '/home/besquivel/rtl')
DEFAULT_LOG = f'{R}/results/op25_multi.log'

# How far outside a call's [start, start+dur] window an observation may fall and
# still belong to it. Log timestamps and calls.start agree to within ~1.6 s in
# practice; this absorbs that without letting one call claim its neighbour.
SLACK = 2.0


def _calls_by_tgid(db) -> dict:
    """Every call's (id, start, end) grouped by talkgroup.

    The interval is start .. start+dur, NOT start .. ended_at: ended_at is NULL
    on 3,247 of 4,606 rows, so binding on it would skip most of the corpus.
    """
    out = collections.defaultdict(list)
    for r in db.execute('SELECT id, tgid, start, dur FROM calls '
                        'WHERE tgid IS NOT NULL AND start IS NOT NULL'):
        out[r['tgid']].append((r['id'], r['start'], r['start'] + (r['dur'] or 0.0)))
    for v in out.values():
        v.sort(key=lambda t: t[1])
    return out


def harvest(db, log_text: str) -> dict:
    """Bind observations from `log_text` to calls and write the columns."""
    grants, obs = enc_log.parse_log(log_text)
    pairs = enc_log.attribute(grants, obs)
    by_tg = _calls_by_tgid(db)

    per_call = collections.defaultdict(list)      # call_id -> [(algid, keyid, mi)]
    stats = collections.Counter({'bound': 0, 'unbound': 0, 'updated': 0,
                                 'speech_only': 0})

    for o, g in pairs:
        if g is None:
            stats['unbound'] += 1
            continue
        hit = None
        for call_id, start, end in by_tg.get(g.tgid, ()):
            if start - SLACK <= o.ts <= end + SLACK:
                hit = call_id
                break
        if hit is None:
            # Attributable to a talkgroup but to no recorded call — reported,
            # never attached to the nearest one.
            stats['unbound'] += 1
            continue
        stats['bound'] += 1
        per_call[hit].append((o.algid, o.keyid, o.mi))

    for call_id, seen in per_call.items():
        state = enc_log.classify([a for a, _, _ in seen])
        if state is None:
            continue
        algid, keyid, mi = seen[0]
        db.execute(
            'UPDATE calls SET enc_observed=?, enc_evidence=?, enc_source=?, '
            'algid=?, keyid=?, mi=? WHERE id=?',
            (state, 'ess', 'harvest', algid, keyid, mi, call_id))
        stats['updated'] += 1

    # Speech evidence for calls the ESS never covered. This is the only evidence
    # available for talkgroups like 17166, which has 21 calls and no ESS at all.
    for r in db.execute(
            "SELECT id, transcript FROM calls "
            "WHERE enc_observed IS NULL AND trim(coalesce(transcript,'')) <> ''"):
        if enc_log.is_speech(r['transcript']):
            db.execute('UPDATE calls SET enc_observed=?, enc_evidence=?, '
                       'enc_source=? WHERE id=?',
                       ('clear', 'speech', 'harvest', r['id']))
            stats['speech_only'] += 1

    db.commit()
    return dict(stats)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('logs', nargs='*', default=[DEFAULT_LOG])
    p.add_argument('--db', default=sdr_db.DB_PATH)
    a = p.parse_args()

    db = sdr_db.connect(a.db)
    try:
        total = collections.Counter()
        for path in (a.logs or [DEFAULT_LOG]):
            if not os.path.exists(path):
                print(f'skip (missing): {path}')
                continue
            with open(path, errors='ignore') as f:
                s = harvest(db, f.read())
            print(f'{os.path.basename(path)}: {s["bound"]} bound, '
                  f'{s["unbound"]} unbound, {s["updated"]} calls updated, '
                  f'{s["speech_only"]} from speech')
            total.update(s)
        print(f'TOTAL: {dict(total)}')
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_enc_harvest -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run against the real corpus and sanity-check**

```bash
python3 scripts/enc_harvest.py
```

Expected: a non-zero `bound` count, and `unbound` reported rather than zero. Then confirm the previously-blank column is populated:

```bash
python3 -c "
import sqlite3; db=sqlite3.connect('sdr.db')
for r in db.execute('SELECT enc_observed, enc_evidence, count(*) FROM calls GROUP BY 1,2'):
    print(r)"
```

- [ ] **Step 6: Commit**

```bash
git add scripts/enc_harvest.py scripts/tests/test_enc_harvest.py
git commit -m "feat(enc): harvest observed encryption from op25 logs into sdr.db"
```

---

### Task 6: Reconciliation report and the override file

**Files:**
- Modify: `scripts/enc_harvest.py`
- Create: `reference/enc_overrides.json`
- Test: `scripts/tests/test_enc_harvest.py`

**Interfaces:**
- Consumes: `harvest` from Task 5.
- Produces:
  - `reconcile(db, ref: dict, *, min_obs: int = 5) -> list[dict]` — each dict has keys `tgid`, `rr`, `proposed`, `clear`, `encrypted`, `evidence`
  - `load_overrides(path: str) -> dict[int, str]`

- [ ] **Step 1: Write the failing test**

Append to `scripts/tests/test_enc_harvest.py`:

```python
class Reconcile(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), 'r.db')
        self.db = sdr_db.connect(self.path)

    def tearDown(self):
        self.db.close()

    def add(self, tgid, n, observed, start=1788282000.0):
        for i in range(n):
            f = f'TG{tgid}_X_{i}.wav'
            sdr_db.upsert_call(self.db, file=f, tgid=tgid, start=start + i, dur=1.0)
            self.db.execute('UPDATE calls SET enc_observed=?, enc_evidence=? '
                            'WHERE file=?', (observed, 'ess', f))
        self.db.commit()

    def test_proposes_clear_for_a_full_flagged_tg_observed_clear(self):
        self.add(17166, 21, 'clear')
        out = enc_harvest.reconcile(self.db, {'17166': {'enc': 'full'}}, min_obs=5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['proposed'], 'clear')

    def test_below_the_evidence_gate_nothing_is_proposed(self):
        self.add(17166, 2, 'clear')
        self.assertEqual(
            enc_harvest.reconcile(self.db, {'17166': {'enc': 'full'}}, min_obs=5), [])

    def test_a_tg_carrying_both_is_proposed_partial_not_clear(self):
        self.add(17086, 20, 'clear')
        self.add(17086, 4, 'encrypted', start=1788283000.0)
        out = enc_harvest.reconcile(self.db, {'17086': {'enc': 'full'}}, min_obs=5)
        self.assertEqual(out[0]['proposed'], 'partial')

    def test_agreement_is_not_reported(self):
        self.add(17053, 10, 'encrypted')
        self.assertEqual(
            enc_harvest.reconcile(self.db, {'17053': {'enc': 'full'}}, min_obs=5), [])


class Overrides(unittest.TestCase):
    def test_missing_file_is_empty_not_an_error(self):
        self.assertEqual(enc_harvest.load_overrides('/nonexistent.json'), {})

    def test_loads_int_keyed_enc_values(self):
        import json
        p = os.path.join(tempfile.mkdtemp(), 'o.json')
        with open(p, 'w') as f:
            json.dump({'17166': {'enc': 'clear', 'why': 'x', 'reviewed': 'y'}}, f)
        self.assertEqual(enc_harvest.load_overrides(p), {17166: 'clear'})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_enc_harvest.Reconcile -v`
Expected: FAIL — `AttributeError: module 'enc_harvest' has no attribute 'reconcile'`

- [ ] **Step 3: Implement reconcile and load_overrides**

Add to `scripts/enc_harvest.py` (imports `json` at the top):

```python
OVERRIDES = f'{R}/reference/enc_overrides.json'


def load_overrides(path: str = OVERRIDES) -> dict:
    """Reviewed reclassifications, keyed by tgid. Absent file means none."""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return {int(k): v['enc'] for k, v in json.load(f).items()}


def reconcile(db, ref: dict, *, min_obs: int = 5) -> list:
    """Talkgroups whose observed behaviour contradicts RadioReference.

    Only disagreements clear the report, and only above `min_obs` observations:
    ESS reaches 19% of calls, so small-N conclusions are not trustworthy. This
    proposes; it never writes. A human copies accepted rows into
    reference/enc_overrides.json.
    """
    rows = db.execute(
        "SELECT tgid, enc_observed, enc_evidence, count(*) AS n FROM calls "
        "WHERE enc_observed IS NOT NULL AND tgid IS NOT NULL "
        "GROUP BY tgid, enc_observed, enc_evidence").fetchall()

    agg = collections.defaultdict(collections.Counter)
    evid = collections.defaultdict(set)
    for r in rows:
        agg[r['tgid']][r['enc_observed']] += r['n']
        evid[r['tgid']].add(r['enc_evidence'])

    out = []
    for tgid, counts in sorted(agg.items()):
        total = sum(counts.values())
        if total < min_obs:
            continue
        clear = counts['clear']
        enc = counts['encrypted'] + counts['mixed']
        if enc == 0:
            proposed = 'clear'
        elif clear == 0:
            proposed = 'full'
        else:
            proposed = 'partial'
        rr = (ref.get(str(tgid)) or {}).get('enc')
        if rr == proposed:
            continue
        out.append({'tgid': tgid, 'rr': rr, 'proposed': proposed,
                    'clear': clear, 'encrypted': enc,
                    'evidence': ','.join(sorted(evid[tgid]))})
    return out
```

Add `--report` handling in `main()` after the harvest loop:

```python
    if a.report:
        ref = json.load(open(f'{R}/reference/lwin_talkgroups.json'))
        props = reconcile(db, ref, min_obs=a.min_obs)
        print(f'\n{len(props)} talkgroup(s) disagree with RadioReference '
              f'(>= {a.min_obs} observations)')
        print(f'{"TG":>7} {"RR":<8}{"proposed":<9}{"clear":>6}{"enc":>5}  evidence')
        for p in props:
            print(f'{p["tgid"]:>7} {str(p["rr"]):<8}{p["proposed"]:<9}'
                  f'{p["clear"]:>6}{p["encrypted"]:>5}  {p["evidence"]}')
        print('\nAccept by adding entries to reference/enc_overrides.json')
```

And the two new arguments:

```python
    p.add_argument('--report', action='store_true',
                   help='print talkgroups whose observed behaviour disagrees')
    p.add_argument('--min-obs', type=int, default=5,
                   help='minimum observations before proposing a change')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_enc_harvest -v`
Expected: PASS

- [ ] **Step 5: Create the empty override file**

```bash
cat > reference/enc_overrides.json <<'JSON'
{
  "_comment": "Reviewed reclassifications layered over the RadioReference scrape in lwin_talkgroups.json, which is upstream and never edited. Produced by: python3 scripts/enc_harvest.py --report. Each entry records why and when it was accepted.",
  "_example": { "enc": "clear", "why": "21/21 intelligible, no ESS observed", "reviewed": "2026-09-01" }
}
JSON
```

Note: `load_overrides` must skip keys beginning with `_`. Update it:

```python
        return {int(k): v['enc'] for k, v in json.load(f).items()
                if not k.startswith('_')}
```

- [ ] **Step 6: Run the real report**

```bash
cd /home/besquivel/rtl && python3 scripts/enc_harvest.py --report
```

Expected: TG17166 and the other BRPD TLK/MOTO talkgroups proposed `clear`; TG17086 proposed `partial`.

- [ ] **Step 7: Commit**

```bash
git add scripts/enc_harvest.py scripts/tests/test_enc_harvest.py reference/enc_overrides.json
git commit -m "feat(enc): reconcile observed behaviour against RadioReference"
```

---

### Task 7: Layer overrides into the whitelist builder

**Files:**
- Modify: `scripts/make_whitelist.py:41-44`
- Test: `scripts/tests/test_enc_harvest.py`

**Interfaces:**
- Consumes: `enc_harvest.load_overrides`.
- Produces: whitelist selection that honours reviewed overrides.

- [ ] **Step 1: Write the failing test**

Append to `scripts/tests/test_enc_harvest.py`:

```python
class ResolveEnc(unittest.TestCase):
    """The override layer, isolated from file and CLI concerns."""

    def test_override_wins_over_the_scraped_flag(self):
        ref = {'17166': {'enc': 'full'}}
        self.assertEqual(
            enc_harvest.resolve_enc(17166, ref, {17166: 'clear'}), 'clear')

    def test_without_an_override_the_scrape_stands(self):
        ref = {'17166': {'enc': 'full'}}
        self.assertEqual(enc_harvest.resolve_enc(17166, ref, {}), 'full')

    def test_unknown_talkgroup_is_none(self):
        self.assertIsNone(enc_harvest.resolve_enc(999, {}, {}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_enc_harvest.ResolveEnc -v`
Expected: FAIL — no attribute `resolve_enc`

- [ ] **Step 3: Add resolve_enc and use it**

Add to `scripts/enc_harvest.py`:

```python
def resolve_enc(tgid: int, ref: dict, overrides: dict) -> str | None:
    """The encryption class to act on: a reviewed override, else the scrape."""
    if tgid in overrides:
        return overrides[tgid]
    return (ref.get(str(tgid)) or {}).get('enc')
```

In `scripts/make_whitelist.py`, after the `db = json.load(...)` line:

```python
db = json.load(open(f'{R}/reference/lwin_talkgroups.json'))

# Reviewed reclassifications layered over the scrape. RadioReference's `enc`
# describes how a talkgroup is documented, not what it transmits: 367 of 377
# calls on talkgroups flagged 'full' carry real speech. Overrides are how a
# human records that, without editing the upstream scrape.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import enc_harvest                                            # noqa: E402
_overrides = enc_harvest.load_overrides()

allowed = {'clear'}
if a.include_partial:   allowed.add('partial')
if a.include_encrypted: allowed.add('full')
```

Then wherever the script tests `v['enc']` against `allowed`, replace with:

```python
enc_harvest.resolve_enc(int(k), db, _overrides) in allowed
```

Confirm every such site is updated:

```bash
grep -n "enc" scripts/make_whitelist.py
```

- [ ] **Step 4: Run tests and verify selection is unchanged with no overrides**

```bash
python3 -m unittest scripts.tests.test_enc_harvest -v
python3 scripts/make_whitelist.py --list | head -20
```

Expected: tests PASS; with an empty override file the selection is identical to before this task.

- [ ] **Step 5: Commit**

```bash
git add scripts/make_whitelist.py scripts/enc_harvest.py scripts/tests/test_enc_harvest.py
git commit -m "feat(enc): honour reviewed overrides when building whitelists"
```

---

### Task 8: Surface speech-backed evidence in the existing Observed column

**Files:**
- Modify: `server/utils/queries.ts:44-58,124-135` (add the field), `components/RecordingsList.vue:153-163,289-300`
- Test: `server/utils/queries.test.ts`

**Interfaces:**
- Consumes: `calls.enc_observed`, `calls.enc_evidence` from Task 1.
- Produces: `Recording.encObserved: string | null`, `Recording.encEvidence: string | null`.

**Context:** the UI already has an **Observed** column (`RecordingsList.vue:153`) and an ESS-vs-reference block in the dialog (`:199`). This task only fills the column when there is speech evidence but no ESS. Do not add columns or restate the dialog.

- [ ] **Step 1: Write the failing test**

Add to `server/utils/queries.test.ts`:

```ts
it('exposes observed encryption and its evidence', () => {
  const rows = listRecordings({ limit: 1 })
  expect(rows[0]).toHaveProperty('encObserved')
  expect(rows[0]).toHaveProperty('encEvidence')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./node_modules/.bin/vitest run server/utils/queries.test.ts`
Expected: FAIL — property does not exist.

- [ ] **Step 3: Add the fields**

In `server/utils/queries.ts`, add to the `Recording` interface near `algorithm` (line 44):

```ts
  encObserved: string | null
  encEvidence: string | null
```

Add to the SELECT (near line 54):

```sql
         c.enc_observed, c.enc_evidence,
```

Add to the row mapping (near line 132):

```ts
    encObserved: r.enc_observed,
    encEvidence: r.enc_evidence,
```

In `components/RecordingsList.vue`, extend the `Recording` type (line ~299) with the same two fields, then change the Observed column body (lines 153-163) to fall back to speech evidence:

```vue
      <Column field="algid" header="Observed" sortable style="width: 8rem">
        <template #body="{ data }">
          <!-- ESS is authoritative for this transmission. -->
          <Tag
            v-if="data.algid !== null"
            :value="data.algid === 128 ? 'clear' : (data.algorithm ?? 'enc')"
            :severity="essSeverity(data.algid)"
          />
          <!-- No ESS, but the audio was intelligible — which encrypted audio
               cannot be, since op25 -n silences it. Weaker evidence than an ESS
               header, so it is marked rather than shown as an equal. -->
          <Tag
            v-else-if="data.encObserved === 'clear' && data.encEvidence === 'speech'"
            value="clear (audio)" severity="secondary"
          />
          <span v-else class="text-color-secondary">—</span>
        </template>
      </Column>
```

- [ ] **Step 4: Mark a talkgroup whose class was reviewed**

Spec Phase 3 item 2: a reviewed reclassification should be visible as such,
rather than silently looking like the scrape. `reference/enc_overrides.json` is
read by Python; expose it to the server through the existing talkgroups query
rather than reading the file from Nitro.

In `scripts/enc_harvest.py`, add a writer so the override decision reaches the
database that the web layer already reads:

```python
def apply_overrides(db, path: str = OVERRIDES) -> int:
    """Copy reviewed overrides onto talkgroups.enc so the web layer sees them.

    reference/lwin_talkgroups.json stays untouched — it is the upstream scrape.
    The talkgroups TABLE is derived (import_to_sqlite.py rebuilds it), so it is
    the right place for a decision to land.
    """
    n = 0
    for tgid, enc in load_overrides(path).items():
        cur = db.execute(
            'UPDATE talkgroups SET enc = ?, enc_overridden = 1 WHERE tgid = ?',
            (enc, tgid))
        n += cur.rowcount
    db.commit()
    return n
```

Add `('enc_overridden', 'INTEGER')` handling for the `talkgroups` table. Note
`_DERIVED_COLUMNS` in `sdr_db.py` only targets `calls`; add a parallel tuple and
loop for `talkgroups` in `_migrate()`:

```python
_TALKGROUP_COLUMNS = (
    # Set by enc_harvest.apply_overrides when a human has reviewed a
    # reclassification, so the UI can show the label as decided rather than
    # scraped.
    ('enc_overridden', 'INTEGER'),
)
```

```python
    have_tg = {r[1] for r in db.execute('PRAGMA table_info(talkgroups)')}
    for name, decl in _TALKGROUP_COLUMNS:
        if name not in have_tg:
            db.execute(f'ALTER TABLE talkgroups ADD COLUMN {name} {decl}')
```

Surface it in `queries.ts` (add `t.enc_overridden` to the SELECT and
`encOverridden: Boolean(r.enc_overridden)` to the mapping, plus the interface
field), then mark the Enc column in `RecordingsList.vue`:

```vue
      <Column field="enc" header="Enc" sortable style="width: 7rem">
        <template #body="{ data }">
          <!-- The talkgroup label. A reviewed override is marked, because a
               decision someone made from observed traffic should not be
               indistinguishable from the RadioReference scrape. -->
          <Tag :value="data.enc ?? 'unknown'" :severity="encSeverity(data.enc)" />
          <span
            v-if="data.encOverridden"
            class="text-xs text-color-secondary ml-1"
            title="Reclassified from observed traffic; see reference/enc_overrides.json"
          >&#9998;</span>
        </template>
      </Column>
```

- [ ] **Step 5: Run the gate**

```bash
./node_modules/.bin/vitest run
./node_modules/.bin/nuxt typecheck
./node_modules/.bin/eslint .
python3 -m unittest discover -s scripts/tests
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/utils/queries.ts server/utils/queries.test.ts \
        components/RecordingsList.vue scripts/enc_harvest.py scripts/sdr_db.py
git commit -m "feat(web): show speech-backed clear evidence and mark reviewed overrides"
```

---

### Task 9: Stop the live path attributing one call's ESS to the next

**Files:**
- Modify: `scripts/op25_log.py` (the `poll` method, ~line 128-140)
- Test: `scripts/tests/test_op25_log.py`

**Interfaces:**
- Consumes: nothing.
- Produces: no API change; `metadata()` returns `algid=None` after a talkgroup change until a fresh ESS arrives.

- [ ] **Step 1: Write the failing test**

`poll()` runs a separate `finditer` pass per pattern, so it destroys line
ordering: all talkgroup matches are applied, *then* all ESS matches. Clearing the
ESS inside the talkgroup loop would simply be undone by the ESS loop later in the
same poll. The fix must compare buffer positions, and the test must prove
ordering is respected. The existing `tail_over()` helper does exactly one poll,
which is the case that matters.

Add to `scripts/tests/test_op25_log.py`:

```python
# An encrypted call, then a grant for a DIFFERENT talkgroup. The ESS precedes
# the new grant, so it belongs to the finished call.
ESS_THEN_NEW_GRANT = (
    '08/31/26 13:38:35.100000 [0] voice update:  '
    'tg(17086), freq(851837500), slot(-), prio(3)\n'
    '08/31/26 13:38:35.200000 [0] NAC 0x1bd LDU2: '
    'ESS: algid=aa, keyid=8, mi=00 00 00 00 00 00 00 00 00, rs_errs=0\n'
    '08/31/26 13:38:36.000000 [0] voice update:  '
    'tg(6848), freq(851837500), slot(-), prio(3)\n'
)
# Same lines, but the ESS arrives AFTER the new grant, so it is this call's.
NEW_GRANT_THEN_ESS = (
    '08/31/26 13:38:35.100000 [0] voice update:  '
    'tg(17086), freq(851837500), slot(-), prio(3)\n'
    '08/31/26 13:38:36.000000 [0] voice update:  '
    'tg(6848), freq(851837500), slot(-), prio(3)\n'
    '08/31/26 13:38:36.200000 [0] NAC 0x1bd LDU2: '
    'ESS: algid=aa, keyid=8, mi=00 00 00 00 00 00 00 00 00, rs_errs=0\n'
)


class TestEssDoesNotCrossCalls(unittest.TestCase):
    """The ESS line carries no tgid, so position is the only thing binding it.

    TG_TTL is 12 s, so without this an encrypted call's ALGID is still "fresh"
    when the next clear call starts, and gets recorded against it. Observed in
    the corpus: rows flagged 0xAA whose audio is plainly clear speech.
    """

    def test_ess_before_a_new_grant_is_dropped(self):
        t = tail_over(ESS_THEN_NEW_GRANT, rx_id=0)
        self.assertEqual(t.current(), 6848)
        self.assertIsNone(t.metadata()['algid'])

    def test_ess_after_a_new_grant_is_kept(self):
        t = tail_over(NEW_GRANT_THEN_ESS, rx_id=0)
        self.assertEqual(t.current(), 6848)
        self.assertEqual(t.metadata()['algid'], 0xAA)

    def test_an_ess_with_no_grant_change_is_untouched(self):
        t = tail_over(ESS_RX0, rx_id=0)
        self.assertEqual(t.metadata()['algid'], 0x80)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_op25_log.TestEssDoesNotCrossCalls -v`
Expected: `test_ess_before_a_new_grant_is_dropped` FAILS — `algid` is `0xAA`, because
the ESS survives the talkgroup change. The other two pass already.

- [ ] **Step 3: Bind the ESS by buffer position**

In `scripts/op25_log.py`, inside `poll()`, track where the last talkgroup change
and the last ESS occur, then drop the ESS if it predates the change. Replace the
three existing loops with:

```python
        # Buffer POSITIONS, not just values: poll() runs one finditer pass per
        # pattern, so the passes alone cannot tell whether an ESS arrived before
        # or after a grant. Offsets restore the ordering the passes discard.
        last_tg_change = -1
        last_ess = -1

        for m in self.tgpat.finditer(self.buf):
            tg = int(next(g for g in m.groups() if g))
            if tg != self.tg:
                last_tg_change = m.start()
            self.tg, self.tg_t = tg, now

        # tg+freq together: the grant's voice channel for this call
        for m in self.freqpat.finditer(self.buf):
            tg = int(m.group(1))
            if tg != self.tg:
                last_tg_change = max(last_tg_change, m.start())
            self.tg, self.tg_t = tg, now
            if m.group(2):
                self.src_addr, self.src_t = int(m.group(2)), now
            self.freq, self.freq_t = _to_hz(m.group(3)), now

        # ESS is the AUTHORITATIVE encryption signal for this specific call,
        # independent of the reference DB's static enc flag — which is known to
        # disagree: TG 17086 is flagged 'full' in RadioReference but transmitted
        # algid 0x80 (clear) in all 23 observations here.
        for m in self.esspat.finditer(self.buf):
            self.ess = (int(m.group(1), 16), int(m.group(2), 16),
                        m.group(3).replace(' ', ''))
            self.ess_t = now
            last_ess = m.start()

        # An ESS that precedes the newest grant described the call that just
        # ended. Keeping it would attribute one call's encryption to the next,
        # which TG_TTL (12 s) makes near-certain at normal call rates. Dropping
        # it yields "unknown", which enc_harvest.py then resolves properly from
        # the full timeline; enc_source distinguishes the two.
        if last_tg_change > last_ess:
            self.ess, self.ess_t = None, 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_op25_log -v`
Expected: PASS, and every pre-existing test in the file still green — especially
`test_ess_is_attributed_by_receiver_across_the_ldu2_prefix`.

- [ ] **Step 5: Commit**

```bash
git add scripts/op25_log.py scripts/tests/test_op25_log.py
git commit -m "fix(op25): drop the ESS at a talkgroup boundary so it cannot cross calls"
```

---

### Task 10: Harvest ADP pairs from ordinary logs, grouped by (algid, keyid)

**Files:**
- Modify: `scripts/extract_enc_pair.py`
- Test: `scripts/tests/test_enc_pair.py`

**Interfaces:**
- Consumes: `enc_pair.extract_pairs(log_text, *, algid, keyid) -> list[Pair]`; `enc_log.parse_log`.
- Produces: `enc_pair_keys(log_text: str, *, min_obs: int = 2) -> list[tuple[int, int]]` in `scripts/enc_harvest.py`.

- [ ] **Step 1: Write the failing test**

Append to `scripts/tests/test_enc_harvest.py`:

```python
class PairKeys(unittest.TestCase):
    """Which (algid, keyid) groups are worth a brute-force run.

    Five distinct ADP key ids appear in this corpus. Each is a different key, so
    pooling their pairs into one run searches for a key that does not exist.
    """

    def line(self, algid, keyid, rs, ts='12:00:42.100000'):
        return (f'09/01/26 {ts} [9] NAC 0x1bd LDU2: ESS: algid={algid}, '
                f'keyid={keyid}, mi=00 00 00 00 00 00 00 00 00, rs_errs={rs}\n')

    def test_groups_each_key_id_separately(self):
        text = (self.line('aa', '22', 0, '12:00:42.100000')
                + self.line('aa', '22', 0, '12:00:43.100000')
                + self.line('aa', '8', 0, '12:00:44.100000')
                + self.line('aa', '8', 0, '12:00:45.100000'))
        self.assertEqual(sorted(enc_harvest.enc_pair_keys(text, min_obs=2)),
                         [(0xAA, 0x8), (0xAA, 0x22)])

    def test_clear_is_never_a_brute_force_target(self):
        text = self.line('80', '0', 0) + self.line('80', '0', 0, '12:00:43.100000')
        self.assertEqual(enc_harvest.enc_pair_keys(text, min_obs=2), [])

    def test_a_lone_key_id_seen_with_bit_errors_is_excluded(self):
        # 0x2EF4 appears once, with rs_errs set. Grouping on a corrupted KID
        # both creates a bogus run and splits real pairs away from a good one.
        text = (self.line('aa', '22', 0, '12:00:42.100000')
                + self.line('aa', '22', 0, '12:00:43.100000')
                + self.line('aa', '2ef4', 3, '12:00:44.100000'))
        self.assertEqual(enc_harvest.enc_pair_keys(text, min_obs=2),
                         [(0xAA, 0x22)])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_enc_harvest.PairKeys -v`
Expected: FAIL — no attribute `enc_pair_keys`

- [ ] **Step 3: Implement the grouping**

Add to `scripts/enc_harvest.py`:

```python
def enc_pair_keys(log_text: str, *, min_obs: int = 2) -> list:
    """The (algid, keyid) groups in a log that are worth a brute-force run.

    Clear traffic is not a target. Observations with rs_errs > 0 are not counted
    towards a key id's evidence: the ESS carries Reed-Solomon residuals, and a
    corrupted KID would both invent a group and strand real pairs away from the
    run that could use them.
    """
    _, obs = enc_log.parse_log(log_text)
    counts = collections.Counter(
        (o.algid, o.keyid) for o in obs
        if o.algid in enc_log.KNOWN_ALGIDS
        and o.algid != enc_log.CLEAR_ALGID
        and o.rs_errs == 0)
    return sorted(k for k, n in counts.items() if n >= min_obs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts.tests.test_enc_harvest -v`
Expected: PASS

- [ ] **Step 5: Extract pairs per key group in the CLI**

In `scripts/extract_enc_pair.py`, replace the single fixed-target call with one run per discovered group. The existing default target was `algid=0xAA, keyid=8`, which silently ignored the other four key ids:

The current script hardcodes one target (`algid=0xAA, keyid=0x8`) and writes a
single `results/enc_pair.txt`, silently ignoring the other four key ids. Replace
the single-target body with one run per discovered group, each to its own file:

```python
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import enc_harvest  # noqa: E402

text = open(log, errors='ignore').read()

groups = enc_harvest.enc_pair_keys(text)
if not groups:
    print(f'no encrypted key groups with clean ESS in {log}')
    sys.exit(0)

for algid, keyid in groups:
    pairs = enc_pair.extract_pairs(text, algid=algid, keyid=keyid)
    # One file per key id. A single pooled file is worse than useless: each KID
    # is a different key, so a brute-force run over mixed pairs searches for a
    # key that does not exist.
    out = f'{R}/results/enc_pair_0x{algid:02X}_0x{keyid:X}.txt'
    with open(out, 'w') as f:
        f.write(f"# LWIN ADP/RC4 algid 0x{algid:02X}, keyid 0x{keyid:X} - "
                f"{len(pairs)} encrypted codeword pair(s)\n")
        f.write("# Each block: the MI that keyed this codeword (op25 chaining,\n"
                "# not the co-located ESS), the frame type, the codeword index,\n"
                "# and the --frame/--position flags to pass adp_brute.\n\n")
        for p in pairs:
            f.write(f"rx={p.rx_id} frame={p.frame} index={p.position} "
                    f"{brute_flags(p)}\n")
            f.write(f"MI  {' '.join(p.mi)}\n")
            f.write(f"CT  {' '.join(p.ct)}\n\n")
    print(f'algid=0x{algid:02X} keyid=0x{keyid:X}: {len(pairs)} pair(s) -> {out}')
    if pairs:
        print(f"  by frame: {dict(Counter(p.frame for p in pairs))}")
        print(f"  by receiver: {dict(Counter(p.rx_id for p in pairs))}")
```

Keep `SIL_PT` and the "supply the plaintext for THIS codeword" guidance exactly
as they are — that caveat is load-bearing and unaffected by the grouping.

- [ ] **Step 6: Run against the ordinary session log**

```bash
cd /home/besquivel/rtl && python3 scripts/extract_enc_pair.py results/op25_multi.log
```

Expected: one group reported per key id present, each with its own output file. Verify no file mixes key ids:

```bash
head -3 results/enc_pair_0x*.txt
```

- [ ] **Step 7: Commit**

```bash
git add scripts/extract_enc_pair.py scripts/enc_harvest.py scripts/tests/test_enc_harvest.py
git commit -m "feat(adp): harvest pairs from ordinary logs, grouped per key id"
```

---

## Final verification

- [ ] **Run the whole gate**

```bash
cd /home/besquivel/rtl
./node_modules/.bin/eslint .            ; echo "eslint:$?"
./node_modules/.bin/nuxt typecheck      ; echo "typecheck:$?"
./node_modules/.bin/vitest run
python3 -m unittest discover -s scripts/tests
```

Expected: eslint 0, typecheck 0, vitest all pass, python all pass.

- [ ] **Confirm the reported symptom is actually fixed**

```bash
python3 -c "
import sqlite3, json
db = sqlite3.connect('sdr.db')
ref = json.load(open('reference/lwin_talkgroups.json'))
full = [k for k, v in ref.items() if v.get('enc') == 'full']
q = ','.join(full)
print('calls on enc=full TGs now carrying an observed state:')
for r in db.execute(f'SELECT enc_observed, count(*) FROM calls WHERE tgid IN ({q}) GROUP BY 1'):
    print('  ', r)"
```

Expected: the majority now report `clear` rather than NULL. Before this work, 367 of 377 such calls contained speech while the UI showed only the static `full` label with `—` in the Observed column.
