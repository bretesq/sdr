#!/usr/bin/env python3
"""Parse op25 logs into timestamped, per-receiver encryption facts.

Pure functions: text in, dataclasses out. No I/O, no database, no globals — so
the binding rules can be tested without a radio, a log file or a schema.

WHY THIS EXISTS
---------------
op25_log.py reads the same lines live, but keeps a single ESS slot for
TG_TTL = 12 seconds with no talkgroup binding, because the op25 ESS line carries
no tgid. An encrypted call therefore stamps its ALGID onto the next clear call
on the same receiver — observed in the corpus as rows flagged 0xAA whose audio
is plainly clear speech.

Post-hoc we have something the live path does not: the whole timeline. Grants
give exact per-receiver boundaries, so an observation is attributed to the grant
that was actually active, not to whatever happened to be seen recently.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]')

# op25 runs under `script`, so timestamps are the local clock, not UTC. Verified
# against calls.start: they agree to within ~1.6 s.
_TS = r'(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d+)'
_TS_FMT = '%m/%d/%y %H:%M:%S.%f'

# 09/01/26 12:00:41.896175 [9] voice update:  tg(17051), rid(0), freq(851.837500), ...
# Two spaces after the colon, and freq is MHz here — trunking.py logs Hz, tk_p25
# logs MHz, hence the explicit '.' test in _to_hz rather than a bare int().
GRANT_RE = re.compile(
    _TS + r' \[(\d+)\] voice update:\s+tg\((\d+)\),\s*(?:rid\(\d+\),\s*)?'
    r'freq\(([0-9.]+)\)')

# 09/01/26 12:00:43.585551 [10] NAC 0x1bd LDU2: ESS: algid=aa, keyid=22, mi=..., rs_errs=4
#
# p25p1_fdma.cc:327 prints "<ts> [id] NAC 0x1bd LDU2: " with no newline, then
# :348 appends the ESS text, so the receiver id sits ~20 characters earlier on
# the SAME line. [^\n] keeps the gap from reaching into the previous line and
# borrowing another receiver's id.
#
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
