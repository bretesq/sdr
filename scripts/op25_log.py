#!/usr/bin/env python3
"""Parse op25's stderr log into per-call metadata.

Extracted from udp_audio_record.py so it can be imported and tested:
scripts/tests/test_static.py pins that udp_audio_record.py executes at import
time and therefore cannot be imported.

op25 has TWO trunking modules with DIFFERENT log formats, and rx.py and
multi_rx.py use different ones (rx.py:80 imports `trunking`, multi_rx.py:686
loads `tk_p25.py`):

  rx.py       -> trunking.py:1874
    "voice update:  tg(17051), freq(852912500), slot(-), prio(3)"
     no receiver id | frequency in Hz (int) | no radio id

  multi_rx.py -> tk_p25.py:2623
    "[2] voice update:  tg(6848), rid(2601234), freq(769.593750), slot(-), prio(3)"
     receiver id    | frequency in MHz (float) | radio id present

Assuming either format alone loses metadata SILENTLY rather than erroring, so
both are matched here. The MHz/Hz difference is the nastiest of the three: a
frequency of 769.59375 parses fine as a number and is simply wrong by six
orders of magnitude.

The receiver id is what lets N recorders share one log file under multi_rx:
pass rx_id and each sees only its own channel's calls.
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
# rid() exists only under tk_p25; freq is Hz there and MHz under trunking.
_FREQ_BODY = (r'voice update:\s*tg\((\d+)\),\s*'
              r'(?:rid\((\d+)\),\s*)?'
              r'freq\(([0-9.]+)\)')
_ESS_BODY = (r'ESS:\s*algid=([0-9a-f]+),\s*keyid=([0-9a-f]+),'
             r'\s*mi=([0-9a-f ]{26})')

# System-wide, not per-receiver: with one trunked system every channel reports
# the same site, and only the control-channel receiver sees these lines at all.
# So they are never filtered by rx_id — a voice receiver must be able to inherit
# the site identity the control receiver decoded, or every call it records loses
# its rfss/site/nac.
SITEPAT = re.compile(r'rfss_sts_bcst:\s*syid:\s*([0-9a-f]+)\s*'
                     r'rfid:\s*(\d+)\s*stid:\s*(\d+)')
NACPAT = re.compile(r'NAC\s+0x([0-9a-f]{3})')


def _pat(body: str, rx_id: int | None, gap: str = r'\s*') -> re.Pattern:
    """Compile `body`, optionally requiring op25's `[rx_id]` prefix.

    rx_id None -> accept the line with or without a receiver id (rx.py, and any
                  single-channel multi_rx run).
    rx_id N    -> require `[N]` before the body, so N recorders sharing one log
                  file each see only their own channel.

    The bracket is anchored on both sides: `(?<!\\d)\\[1\\]` will not match the
    `[1` of `[12]`, and the closing `\\]` will not match the `2]` of `[12]`.
    `gap` must never allow a newline, or a line belonging to one receiver could
    be attached to the previous line's receiver.
    """
    if rx_id is None:
        return re.compile(r'(?:\[\d+\]\s*)?' + body)
    return re.compile(r'(?<!\d)\[' + str(int(rx_id)) + r'\]' + gap + body)


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
        self.site = None                          # (sysid, rfss, stid) — static per site
        self.nac = None
        self.tgpat = _pat(_TG_BODY, rx_id)
        self.freqpat = _pat(_FREQ_BODY, rx_id)
        # The receiver id precedes ESS by about 20 characters ("NAC 0x1bd
        # LDU2: ") on the SAME line, because p25p1_fdma.cc:327 prints no
        # newline before :348 appends the ESS text. [^\n] keeps it from
        # reaching back into the previous line.
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

        # op25 rewrites the status line without newlines, so scan the whole
        # buffer tail rather than trying to split into lines.
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
        # independent of the reference DB's static enc flag — which is known to
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
