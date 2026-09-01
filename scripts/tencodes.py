#!/usr/bin/env python3
"""Recognise radio codes in a transcript. Pure: no I/O, no DB, no globals.

Whisper mangles codes three mechanical ways, all recoverable:
  concatenation   10-42 -> "1042"
  spelling        10-4  -> "ten four"
  run-together    10-4 followed by unit 1-4-31 -> "10-4-1-4-31"

Bare numbers are deliberately NOT recognised. In this corpus "28 is going to be
displayed on the white leaf on Altima" genuinely is a 10-28 registration check,
but so are "mileage 46215", "6627 Sullivan Road" and "40-year-old male". There
is no way to separate them without context modelling, so v1 requires an
explicit 10-/signal/code prefix.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Bump whenever a change alters extraction output. It feeds codes_rev, so a
# bump makes every stored row detectably stale.
EXTRACTOR_VERSION = 'v1'

_NUMBER_WORDS = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
    'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12,
    'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16,
    'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20,
    'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70,
    'eighty': 80, 'ninety': 90,
}
_TENS = {20, 30, 40, 50, 60, 70, 80, 90}

# The alternation is spelled out rather than using \w+ so that "ten more",
# "ten point" and "ten minutes" cannot match at all.
_TEN_WORD = re.compile(
    r'\bten[\s\-]+(?:oh[\s\-]+)?'
    r'(' + '|'.join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r')s?'
    r'(?:[\s\-]+(one|two|three|four|five|six|seven|eight|nine)s?)?\b',
    re.IGNORECASE)

# Alternative 1 REQUIRES a separator, so "1042" falls through to alternative 2
# and gets the membership test rather than being read as 10 + 42.
_CANDIDATE = re.compile(
    r'\b10[\s\-–.](\d{1,2})\b'
    r'|\b10(\d{2})\b'
    r'|\b(signal)[\s\-]+(\d{1,3})\b'
    r'|\b(code)[\s\-]+(\d{1,2})\b',
    re.IGNORECASE)

# A "10NN" next to any of these is a room, address or odometer reading, not a
# code. Checked five tokens either side.
_ADDRESS_WORDS = frozenset({
    'room', 'rooms', 'apartment', 'apartments', 'apt', 'suite', 'ste', 'unit',
    'block', 'mileage', 'milepost', 'marker', 'box', 'lot', 'building',
    'bldg', 'hall', 'dorm', 'floor', 'road', 'street', 'avenue', 'drive',
    'boulevard', 'lane', 'highway', 'court', 'place', 'trail', 'parkway',
})
# Five, not three. The corpus case "back in the 1015 team and the mileage
# 46215" puts 'mileage' four tokens after the candidate, and a three-token
# window lets that false positive through.
_GUARD_TOKENS = 5


@dataclass(frozen=True)
class Mention:
    raw: str             # exactly as it appeared in the input
    canonical: str       # "10-42" / "signal 20" / "code 4"
    kind: str            # 'ten' | 'signal' | 'response'
    meaning: str | None  # None when the chain does not define it
    set_id: str | None
    confidence: str      # 'high' | 'medium' | 'low'
    off_start: int       # offsets into the RETURNED normalized text
    off_end: int


def _spelled_to_digits(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        n = _NUMBER_WORDS[m.group(1).lower()]
        unit = m.group(2)
        if unit and n in _TENS:
            return f'10-{n + _NUMBER_WORDS[unit.lower()]}'
        if unit:
            return f'10-{n} {unit}'
        return f'10-{n}'
    return _TEN_WORD.sub(repl, text)


def _near_address_word(text: str, start: int, end: int) -> bool:
    before = re.findall(r'[A-Za-z]+', text[:start])[-_GUARD_TOKENS:]
    after = re.findall(r'[A-Za-z]+', text[end:])[:_GUARD_TOKENS]
    return any(w.lower() in _ADDRESS_WORDS for w in before + after)


def extract(text: str, codes: dict) -> tuple[str, list[Mention]]:
    """Return (normalized text, mentions).

    The normalized text is ALWAYS returned, equal to the input when nothing
    matched, so transcript_norm is never NULL for a call that has a transcript
    and FTS can index it unconditionally.
    """
    if not text:
        return text, []

    text = _spelled_to_digits(text)
    set_id = codes.get('id')

    out: list[str] = []
    mentions: list[Mention] = []
    pos = 0        # cursor in the input
    grown = 0      # length of output emitted so far

    for m in _CANDIDATE.finditer(text):
        sep, cat, sig_kw, sig_n, resp_kw, resp_n = m.groups()

        if sep is not None:
            kind, key, conf = 'ten', str(int(sep)), None
            canonical = f'10-{key}'
        elif cat is not None:
            kind, key = 'ten', str(int(cat))
            canonical = f'10-{key}'
            if key not in codes['ten'] or _near_address_word(text, m.start(), m.end()):
                continue                      # a room number, not a code
            conf = 'medium'
        elif sig_kw is not None:
            kind, key, conf = 'signal', str(int(sig_n)), None
            canonical = f'signal {key}'
        else:
            kind, key, conf = 'response', str(int(resp_n)), None
            canonical = f'code {key}'

        entry = codes[kind].get(key)
        if conf is None:
            conf = 'high' if entry else 'low'

        out.append(text[pos:m.start()])
        grown += m.start() - pos
        start = grown
        out.append(canonical)
        grown += len(canonical)
        pos = m.end()

        mentions.append(Mention(
            raw=m.group(0),
            canonical=canonical,
            kind=kind,
            meaning=entry['meaning'] if entry else None,
            set_id=set_id if entry else None,
            confidence=conf,
            off_start=start,
            off_end=grown,
        ))

    out.append(text[pos:])
    return ''.join(out), mentions


def codes_text(mentions: list[Mention]) -> str:
    """Space-joined blob for FTS: raw + canonical + meaning per mention.

    Unresolved codes contribute raw and canonical only, so they stay searchable
    before their set is sourced.
    """
    parts: list[str] = []
    for m in mentions:
        parts.append(m.raw)
        if m.canonical != m.raw:
            parts.append(m.canonical)
        if m.meaning:
            parts.append(m.meaning)
    return ' '.join(parts)
