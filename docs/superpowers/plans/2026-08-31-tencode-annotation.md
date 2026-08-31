# 10-Code Recognition and Annotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recognise radio codes (10-codes, signal codes, response codes) in existing whisper transcripts, expand them from per-agency code sets, and make them readable, searchable, filterable and countable — without ever rewriting `calls.transcript`.

**Architecture:** A derived layer. One pure Python extractor turns transcript text plus a resolved code set into normalized text and a list of code mentions. `sdr_db.set_transcript()` writes those into three new `calls` columns and a `call_codes` table in the same transaction as the transcript. FTS5 indexes the derived text instead of the raw text. TypeScript reads only; a backfill script re-derives everything from the durable `.txt` files.

**Tech Stack:** Python 3 (stdlib only — `sqlite3`, `re`, `json`, `hashlib`, `unittest`), Nuxt 3 / Nitro server routes, `node:sqlite`, Vue 3 `<script setup>`, PrimeVue 4, vitest.

## Global Constraints

- **`calls.transcript` is never written by this feature.** The `.txt` files stay the durable copy and `transcript` stays the raw whisper record.
- **No invented code meanings.** Every entry in a code set carries a `src` that resolves to an entry in that file's `sources` array. A data-integrity test enforces this.
- **Unknown codes render un-expanded**, never guessed.
- **Bare-number codes are out of scope.** Only `10-NN`, `10 NN`, `10NN`, `ten-<word>`, `signal NN`, `code N` are recognised.
- **No changes to whisper invocation.** `stt_watch.py` and `stt_transcribe.py` are not modified.
- Python is **stdlib only** — no new dependencies. Tests use `unittest`, not pytest.
- Test commands: `pnpm test` runs both. Python alone: `python3 -m unittest discover -s scripts/tests`. TypeScript alone: `pnpm exec vitest run`.
- vitest only collects `server/**/*.test.ts` (see `vitest.config.ts`), so TypeScript tests go there.
- Never suppress type errors with `as any`, `@ts-ignore` or `@ts-expect-error`.
- Commit messages use conventional commits.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `data/tencodes/index.json` | Resolver rules: (cat glob, tag glob) -> set id. First match wins. |
| `data/tencodes/sets/la-common.json` | Response codes (`code N`) shared by all disciplines. |
| `data/tencodes/sets/la-generic-law.json` | Louisiana law 10-codes and signal codes. Extends la-common. |
| `data/tencodes/sets/la-generic-fire.json` | Fire. Extends la-common. Ships with no fire-specific codes. |
| `data/tencodes/sets/la-generic-ems.json` | EMS. Extends la-common. Ships with no EMS-specific codes. |
| `data/tencodes/sets/la-brpd-law.json` | BRPD. Empty, extends `la-generic-law`. |
| `data/tencodes/sets/la-ebrso-law.json` | EBRSO. Empty, extends `la-generic-law`. |
| `data/tencodes/sets/la-lsp-law.json` | LSP Troop A. Empty, extends `la-generic-law`. |
| `data/tencodes/sets/la-lsupd-law.json` | LSU PD. Empty, extends `la-generic-law`. |
| `scripts/tencode_sets.py` | Loads sets, resolves (cat, tag) -> set id, flattens the `extends` chain, computes `codes_rev`. |
| `scripts/tencodes.py` | Pure extractor. No I/O, no DB, no globals. |
| `scripts/backfill_codes.py` | Re-derives `transcript_norm` / `codes_text` / `call_codes` from the `.txt` files. |
| `server/api/codes/stats.get.ts` | Aggregate code counts. |
| `scripts/tests/test_tencode_sets.py` | Data-integrity and resolution tests. |
| `scripts/tests/test_tencodes.py` | Golden and negative extractor tests. |

**Modified:**

| Path | Change |
|---|---|
| `scripts/sdr_db.py` | Add `call_codes` to `SCHEMA`; add `_migrate()`; extend `set_transcript()`. |
| `scripts/tests/test_sdr_db.py` | Add migration-idempotence and `set_transcript` code-writing tests. |
| `server/utils/db.ts` | Add `CallRow.transcript_norm`; add a migration-applied column check in `getDb()`. |
| `server/utils/queries.ts` | Add `transcriptNorm` + `codes` to `Recording`; add `code` filter; add `codeStats()`. |
| `server/utils/queries.test.ts` | Tests for the `code` filter and `codeStats()`. |
| `components/RecordingsList.vue` | Segment rendering, tooltips, detail Codes block, code filter. |

---

### Task 1: Code-set data and resolver

**Files:**
- Create: `data/tencodes/index.json`, `data/tencodes/sets/*.json` (8 files)
- Create: `scripts/tencode_sets.py`
- Test: `scripts/tests/test_tencode_sets.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `tencode_sets.resolve_set_id(cat: str | None, tag: str | None, root: str = DATA_ROOT) -> str`
  - `tencode_sets.resolve(set_id: str, root: str = DATA_ROOT) -> ResolvedSet`
  - `ResolvedSet` = `dict` with keys `id: str`, `name: str`, `ten: dict[str, Entry]`, `signal: dict[str, Entry]`, `response: dict[str, Entry]`
  - `Entry` = `dict` with keys `meaning: str`, `src: str`, optional `common: bool`
  - `tencode_sets.set_rev(resolved: ResolvedSet, extractor_version: str) -> str` (12 hex chars)
  - `tencode_sets.DATA_ROOT: str`

**Provenance of the shipped data.** Three independent sources, retrieved 2026-08-31:

- `s1` — https://police-codes.com/united-states/louisiana
- `s2` — https://forums.radioreference.com/threads/louisiana-10-codes.479431/
- `s3` — the corpus itself: observed usage in `sdr.db`, LWIN capture 2026-08-30/31

An entry ships only if two independent sources agree on it, **or** the corpus settles it. Codes where the sources conflict and the corpus is silent are recorded in a `conflicts` block and deliberately omitted, so nobody re-adds them from a list that looks authoritative.

- [ ] **Step 1: Write the failing data-integrity test**

Create `scripts/tests/test_tencode_sets.py`:

```python
#!/usr/bin/env python3
"""Integrity and resolution tests for the 10-code data in data/tencodes/.

These are what make "sourced properly" enforceable rather than aspirational:
an expansion with no traceable source, a resolver rule pointing at a set that
does not exist, or a cyclic `extends` chain all fail here rather than shipping
a confident wrong meaning into a transcript.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tencode_sets  # noqa: E402


class TestDataIntegrity(unittest.TestCase):
    def test_every_entry_has_a_resolvable_source(self):
        for set_id in tencode_sets.all_set_ids():
            raw = tencode_sets.load_set(set_id)
            refs = {s['ref'] for s in raw.get('sources', [])}
            for table in ('ten', 'signal', 'response'):
                for code, entry in raw.get(table, {}).items():
                    with self.subTest(set=set_id, table=table, code=code):
                        self.assertIn('meaning', entry)
                        self.assertIn('src', entry)
                        self.assertIn(entry['src'], refs)

    def test_every_index_rule_names_an_existing_set(self):
        known = set(tencode_sets.all_set_ids())
        for rule in tencode_sets.load_index():
            with self.subTest(rule=rule):
                self.assertIn(rule['set'], known)

    def test_extends_chains_terminate_and_do_not_cycle(self):
        for set_id in tencode_sets.all_set_ids():
            with self.subTest(set=set_id):
                chain = tencode_sets.chain_of(set_id)
                self.assertEqual(len(chain), len(set(chain)))

    def test_index_has_a_catch_all_rule(self):
        rules = tencode_sets.load_index()
        self.assertEqual(rules[-1]['cat'], '*')
        self.assertEqual(rules[-1]['tag'], '*')


class TestResolution(unittest.TestCase):
    def test_brpd_law_resolves_to_the_brpd_set(self):
        got = tencode_sets.resolve_set_id(
            'East Baton Rouge Parish (17) - Baton Rouge Police', 'Law Dispatch')
        self.assertEqual(got, 'la-brpd-law')

    def test_fire_resolves_to_fire_not_law(self):
        got = tencode_sets.resolve_set_id(
            'East Baton Rouge Parish (17) - Fire/EMS', 'Fire Dispatch')
        self.assertEqual(got, 'la-generic-fire')

    def test_unknown_parish_falls_back_to_generic_law(self):
        got = tencode_sets.resolve_set_id(
            'Pointe Coupee Parish (39) - Public Safety', 'Law Dispatch')
        self.assertEqual(got, 'la-generic-law')

    def test_missing_metadata_falls_back_to_generic_law(self):
        self.assertEqual(tencode_sets.resolve_set_id(None, None), 'la-generic-law')

    def test_empty_agency_set_inherits_the_generic_codes(self):
        resolved = tencode_sets.resolve('la-brpd-law')
        self.assertEqual(resolved['ten']['4']['meaning'], 'Acknowledged')

    def test_fire_does_not_inherit_police_ten_codes(self):
        resolved = tencode_sets.resolve('la-generic-fire')
        self.assertNotIn('15', resolved['ten'])

    def test_response_codes_are_shared_across_disciplines(self):
        for set_id in ('la-generic-law', 'la-generic-fire', 'la-generic-ems'):
            with self.subTest(set=set_id):
                resolved = tencode_sets.resolve(set_id)
                self.assertEqual(resolved['response']['4']['meaning'],
                                 'Scene secure, no further units needed')

    def test_fire_inherits_the_universal_ten_codes(self):
        """10-4 is universal. Leaving it out of the fire chain left 27 corpus
        occurrences unresolved, because 1,076 calls are Fire/EMS."""
        resolved = tencode_sets.resolve('la-generic-fire')
        for code in ('4', '7', '8', '9', '20', '97'):
            with self.subTest(code=code):
                self.assertIn(code, resolved['ten'])

    def test_law_only_codes_do_not_leak_into_fire(self):
        resolved = tencode_sets.resolve('la-generic-fire')
        for code in ('6', '15', '19', '42'):
            with self.subTest(code=code):
                self.assertNotIn(code, resolved['ten'])


class TestRev(unittest.TestCase):
    def test_rev_is_stable_for_the_same_input(self):
        a = tencode_sets.resolve('la-generic-law')
        b = tencode_sets.resolve('la-generic-law')
        self.assertEqual(tencode_sets.set_rev(a, 'v1'),
                         tencode_sets.set_rev(b, 'v1'))

    def test_rev_changes_when_a_meaning_changes(self):
        a = tencode_sets.resolve('la-generic-law')
        b = tencode_sets.resolve('la-generic-law')
        b['ten']['4'] = dict(b['ten']['4'], meaning='Something else')
        self.assertNotEqual(tencode_sets.set_rev(a, 'v1'),
                            tencode_sets.set_rev(b, 'v1'))

    def test_rev_changes_when_the_extractor_version_changes(self):
        a = tencode_sets.resolve('la-generic-law')
        self.assertNotEqual(tencode_sets.set_rev(a, 'v1'),
                            tencode_sets.set_rev(a, 'v2'))

    def test_rev_ignores_the_common_flag(self):
        a = tencode_sets.resolve('la-generic-law')
        b = tencode_sets.resolve('la-generic-law')
        b['ten']['4'] = dict(b['ten']['4'], common=False)
        self.assertEqual(tencode_sets.set_rev(a, 'v1'),
                         tencode_sets.set_rev(b, 'v1'))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest scripts.tests.test_tencode_sets -v` from the repo root, or
`python3 -m unittest discover -s scripts/tests -p 'test_tencode_sets.py' -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'tencode_sets'`

- [ ] **Step 3: Create the shared response-code set**

Create `data/tencodes/sets/la-common.json`:

```json
{
  "id": "la-common",
  "name": "Louisiana — codes shared across all disciplines",
  "discipline": "any",
  "sources": [
    { "ref": "s1", "url": "https://police-codes.com/united-states/louisiana",
      "retrieved": "2026-08-31", "note": "Louisiana ten-code and signal-code list" },
    { "ref": "s2",
      "url": "https://forums.radioreference.com/threads/louisiana-10-codes.479431/",
      "retrieved": "2026-08-31",
      "note": "red8 (Louisiana law), YardDart63 (north LA), N508SP1 (SE LA fire/EMS)" },
    { "ref": "s3", "url": "sdr.db — LWIN capture 2026-08-30/31",
      "retrieved": "2026-08-31", "note": "Observed usage in transcribed traffic on this system" }
  ],
  "ten": {
    "4":  { "meaning": "Acknowledged",        "src": "s2", "common": true },
    "7":  { "meaning": "Out of service",      "src": "s2" },
    "8":  { "meaning": "In service",          "src": "s2" },
    "9":  { "meaning": "Repeat transmission", "src": "s2" },
    "20": { "meaning": "Location",            "src": "s2" },
    "97": { "meaning": "Arrived on scene",    "src": "s2" }
  },
  "signal": {},
  "response": {
    "1": { "meaning": "Routine response, no lights or siren", "src": "s3" },
    "2": { "meaning": "Elevated priority response",           "src": "s3" },
    "4": { "meaning": "Scene secure, no further units needed", "src": "s2" }
  },
  "conflicts": {
    "response.3": "Not observed in the corpus and not attested by s1 or s2. Commonly 'emergency, lights and siren' elsewhere in the US, but unverified for this area — deliberately omitted."
  }
}
```

**Why these six ten-codes are shared rather than law-only.** They are the codes
where the Louisiana *law* lists (s1, s2/red8) and the SE Louisiana *fire/EMS*
list (s2/N508SP1) give the same meaning — agreement across disciplines, which
is a higher bar than agreement within one. Everything the two disciplines
disagree on (10-6, 10-19, 10-42) stays in the law set.

This matters more than it looks. Running the extractor over the corpus with an
empty fire/EMS `ten` table leaves **27 occurrences of `10-4` unresolved**,
because 1,076 calls are Fire/EMS and 10-4 is universal there too. Moving the
six shared codes here takes corpus resolution from 70% to 90%.

Corpus evidence for the response codes: `code 1` — *"Ladder 7, code 1 lift
assist"*, *"respond code 1, lift assist"* (routine). `code 2` — *"public assist
code 2"*, *"Copying alarm code 2"* (priority above routine). `code 4` —
*"go with 1418 until code 4"*, *"Is there a code 4 at this time?"*,
*"Negative code 4 right now"* (scene secure), matching s2 independently.

- [ ] **Step 4: Create the generic law set**

Create `data/tencodes/sets/la-generic-law.json`:

```json
{
  "id": "la-generic-law",
  "name": "Louisiana — law enforcement (generic)",
  "discipline": "law",
  "extends": "la-common",
  "sources": [
    { "ref": "s1", "url": "https://police-codes.com/united-states/louisiana",
      "retrieved": "2026-08-31", "note": "Louisiana ten-code and signal-code list" },
    { "ref": "s2",
      "url": "https://forums.radioreference.com/threads/louisiana-10-codes.479431/",
      "retrieved": "2026-08-31",
      "note": "Posters red8 ('CORRECT 10 DASH for Louisiana'), YardDart63 (north LA), N508SP1 (SE LA)" },
    { "ref": "s3", "url": "sdr.db — LWIN capture 2026-08-30/31",
      "retrieved": "2026-08-31", "note": "Observed usage in transcribed traffic on this system" }
  ],
  "ten": {
    "6":  { "meaning": "Busy, stand by",            "src": "s1" },
    "15": { "meaning": "Prisoner in custody",       "src": "s1" },
    "19": { "meaning": "Return to station",         "src": "s2" },
    "21": { "meaning": "Call office by telephone",  "src": "s2" },
    "22": { "meaning": "Disregard, take no further action", "src": "s2" },
    "28": { "meaning": "Registration check",        "src": "s1" },
    "42": { "meaning": "End of tour, off duty",     "src": "s3" }
  },
  "signal": {
    "18": { "meaning": "Disabled or stranded motorist", "src": "s2" },
    "20": { "meaning": "Vehicle crash",                 "src": "s2" }
  },
  "response": {},
  "conflicts": {
    "ten.26": "s1 'Call (personal)' vs s2 'Driver's license check'. Not settled by the corpus.",
    "ten.50": "s1 'No traffic for you' vs s2 north-LA 'No/negative' vs s2 SE-LA 'Not available'. Zero corpus occurrences.",
    "ten.10": "s1 'Available' vs s2 'Out of service, subject to call' — opposite meanings.",
    "ten.11": "s1 'Talking too fast' vs s2 SE-LA 'In service at other location'.",
    "ten.12": "s1 'Officials or visitors' vs s2 north-LA 'Civilians present'. Close but not equivalent; corpus silent.",
    "ten.14": "s1 'Send EMS unit' vs s2 'Convoy or escort' — unrelated meanings.",
    "ten.16": "s1 'Send rescue unit' vs s2 'Pickup prisoner at'.",
    "signal.31": "s1 'Drunk'. The single corpus occurrence ('His Signal 31 pregnant girlfriend is on scene and slapped him') does not support it and does not settle an alternative."
  }
}
```

The six shared codes (10-4, 10-7, 10-8, 10-9, 10-20, 10-97) are not repeated here — they arrive through `extends: la-common`.

Why `ten.42` carries `src: s3` rather than a published source: s1 says "Necessary Action" and s2 (SE Louisiana) says "Restroom break", and **both are wrong for this system**. All eight corpus occurrences pair the code with a sign-off — *"you can show me 10-8, 10-42, y'all have a good one"*, *"F-13 dispatch, 10-42. 10-42, 13"*, *"Have a good one. CPD2S1042"*. That is end of tour. This is the corpus-as-tie-breaker mechanism the spec describes, and it is why `src` matters: a later reader can see exactly why the published lists were rejected.

- [ ] **Step 5: Create the fire, EMS and empty agency sets**

Create `data/tencodes/sets/la-generic-fire.json`:

```json
{
  "id": "la-generic-fire",
  "name": "Louisiana — fire (generic)",
  "discipline": "fire",
  "extends": "la-common",
  "sources": [],
  "ten": {},
  "signal": {},
  "response": {},
  "conflicts": {
    "ten.*": "Fire ten-codes in Louisiana differ fundamentally from law ten-codes (s2, poster N508SP1: 10-26 'working fire', 10-29 'fire under control'), and that list is attributed to SE Louisiana rather than this system. Shipping it here would produce confidently wrong expansions on 318 fire calls. Left empty until confirmed against this system's traffic."
  }
}
```

Create `data/tencodes/sets/la-generic-ems.json`:

```json
{
  "id": "la-generic-ems",
  "name": "Louisiana — EMS (generic)",
  "discipline": "ems",
  "extends": "la-common",
  "sources": [],
  "ten": {},
  "signal": {},
  "response": {},
  "conflicts": {
    "ten.*": "No EMS-specific ten-code list attested for this system. EMS traffic here is predominantly plain language plus response codes, which come from la-common. Left empty rather than borrowing the law set."
  }
}
```

Create these four files, identical except for `id` and `name`:

`data/tencodes/sets/la-brpd-law.json`:

```json
{
  "id": "la-brpd-law",
  "name": "Baton Rouge Police Department — Law",
  "discipline": "law",
  "extends": "la-generic-law",
  "sources": [],
  "ten": {},
  "signal": {},
  "response": {},
  "conflicts": {
    "*": "No authoritative BRPD-specific code list is published. s2 states the single Louisiana list circulating online is 'completely incorrect'. This file exists as the destination for codes confirmed from this system's own traffic; until then the generic set applies through `extends`."
  }
}
```

`data/tencodes/sets/la-ebrso-law.json` — same, with
`"id": "la-ebrso-law"`, `"name": "East Baton Rouge Parish Sheriff's Office — Law"`.

`data/tencodes/sets/la-lsp-law.json` — same, with
`"id": "la-lsp-law"`, `"name": "Louisiana State Police Troop A — Law"`.

`data/tencodes/sets/la-lsupd-law.json` — same, with
`"id": "la-lsupd-law"`, `"name": "LSU Police Department — Law"`.

- [ ] **Step 6: Create the resolver index**

Create `data/tencodes/index.json`:

```json
[
  { "cat": "*Baton Rouge Police*",  "tag": "Law*",     "set": "la-brpd-law" },
  { "cat": "*(17) - Sheriff*",      "tag": "Law*",     "set": "la-ebrso-law" },
  { "cat": "*(17) - Sheriff*",      "tag": "Corrections", "set": "la-ebrso-law" },
  { "cat": "State Police*",         "tag": "Law*",     "set": "la-lsp-law" },
  { "cat": "LSU*",                  "tag": "Law*",     "set": "la-lsupd-law" },
  { "cat": "*",                     "tag": "Fire*",    "set": "la-generic-fire" },
  { "cat": "*",                     "tag": "EMS*",     "set": "la-generic-ems" },
  { "cat": "*",                     "tag": "Hospital", "set": "la-generic-ems" },
  { "cat": "*",                     "tag": "*",        "set": "la-generic-law" }
]
```

- [ ] **Step 7: Implement the loader**

Create `scripts/tencode_sets.py`:

```python
#!/usr/bin/env python3
"""Load and resolve the radio code sets in data/tencodes/.

A code set is keyed by (agency, discipline), not agency alone: EBR Sheriff and
EBR Fire share a parish but not a codebook. `talkgroups.cat` supplies the
agency and `talkgroups.tag` the discipline, and index.json maps that pair to a
set id.

Sets compose through `extends`, child overriding parent. An agency set that is
empty apart from its `extends` is a valid, working configuration — the chain
does the work, and the file is the destination for a code confirmed later.

A code absent from the whole chain resolves to nothing, and the caller renders
it un-expanded. Nothing here ever invents a meaning.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from functools import lru_cache

DATA_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'tencodes')

TABLES = ('ten', 'signal', 'response')


def _sets_dir(root: str) -> str:
    return os.path.join(root, 'sets')


def all_set_ids(root: str = DATA_ROOT) -> list[str]:
    d = _sets_dir(root)
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith('.json'))


@lru_cache(maxsize=None)
def load_set(set_id: str, root: str = DATA_ROOT) -> dict:
    """Read one set file verbatim. Raises if the id does not exist."""
    with open(os.path.join(_sets_dir(root), set_id + '.json')) as f:
        return json.load(f)


@lru_cache(maxsize=None)
def load_index(root: str = DATA_ROOT) -> tuple[dict, ...]:
    with open(os.path.join(root, 'index.json')) as f:
        return tuple(json.load(f))


def chain_of(set_id: str, root: str = DATA_ROOT) -> list[str]:
    """The `extends` chain, most specific first. Raises on a cycle."""
    chain: list[str] = []
    seen: set[str] = set()
    cur: str | None = set_id
    while cur:
        if cur in seen:
            raise ValueError(f'cyclic extends chain at {cur}: {chain}')
        seen.add(cur)
        chain.append(cur)
        cur = load_set(cur, root).get('extends')
    return chain


def resolve_set_id(cat: str | None, tag: str | None, root: str = DATA_ROOT) -> str:
    """Map a talkgroup's (cat, tag) to a set id. First matching rule wins.

    A missing cat or tag matches only the catch-all, which is why index.json is
    required to end with one — enforced by test_index_has_a_catch_all_rule.
    """
    c = cat or ''
    t = tag or ''
    for rule in load_index(root):
        if fnmatch.fnmatch(c, rule['cat']) and fnmatch.fnmatch(t, rule['tag']):
            return rule['set']
    raise ValueError('index.json has no catch-all rule')


def resolve(set_id: str, root: str = DATA_ROOT) -> dict:
    """Flatten the `extends` chain into one lookup table. Child wins."""
    chain = chain_of(set_id, root)
    out: dict = {'id': set_id,
                 'name': load_set(set_id, root).get('name', set_id)}
    for table in TABLES:
        merged: dict = {}
        for sid in reversed(chain):          # parent first, child overwrites
            merged.update(load_set(sid, root).get(table, {}))
        out[table] = merged
    return out


def set_rev(resolved: dict, extractor_version: str) -> str:
    """Short hash over everything that can change extraction output.

    `codes_set_id` alone is not enough to find stale rows: correcting a meaning
    inside an existing set leaves the id identical, so --only-stale would skip
    every affected row and the correction would never reach codes_text or
    call_codes. Since meanings get corrected repeatedly as sets are sourced,
    that is the common case.

    `common` is excluded deliberately — it affects rendering only, never stored
    output, so toggling it needs no backfill.
    """
    payload = {
        'v': extractor_version,
        'id': resolved['id'],
        'tables': {t: {k: resolved[t][k]['meaning'] for k in sorted(resolved[t])}
                   for t in TABLES},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(blob).hexdigest()[:12]
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_tencode_sets.py' -v`
Expected: PASS, 15 tests

- [ ] **Step 9: Commit**

```bash
git add data/tencodes scripts/tencode_sets.py scripts/tests/test_tencode_sets.py
git commit -m "feat(tencodes): code-set data and (agency, discipline) resolver

Ships only codes two independent sources agree on or the corpus settles.
Conflicting codes are recorded in a conflicts block and omitted rather than
guessed. Agency sets ship empty extending the generic set, because no
authoritative BRPD/EBRSO list is published."
```

---

### Task 2: The extractor

**Files:**
- Create: `scripts/tencodes.py`
- Test: `scripts/tests/test_tencodes.py`

**Interfaces:**
- Consumes: `ResolvedSet` from `tencode_sets.resolve()` — a dict with `ten`/`signal`/`response` tables mapping code string to `{meaning, src, common?}`.
- Produces:
  - `tencodes.EXTRACTOR_VERSION: str`
  - `tencodes.Mention` — frozen dataclass with fields `raw: str`, `canonical: str`, `kind: str`, `meaning: str | None`, `set_id: str | None`, `confidence: str`, `off_start: int`, `off_end: int`
  - `tencodes.extract(text: str, codes: dict) -> tuple[str, list[Mention]]`
  - `tencodes.codes_text(mentions: list[Mention]) -> str`

**Note on the signature.** The spec writes `extract(text, set_id)`. This plan passes the already-resolved set instead, so the function stays pure — no file I/O, no cache, testable with a literal dict. `set_id` still reaches the output through `Mention.set_id`.

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_tencodes.py`:

```python
#!/usr/bin/env python3
"""Extractor tests, grounded in the real corpus.

Every negative case below is a verbatim fragment from a transcribed LWIN call.
They are the point of the suite: `1003` (a dorm room) and `1042` (a real code)
are lexically identical, and only code-set membership separates them.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tencodes  # noqa: E402

CODES = {
    'id': 'test-set',
    'name': 'Test set',
    'ten': {
        '4':  {'meaning': 'Acknowledged', 'src': 's1', 'common': True},
        '8':  {'meaning': 'In service', 'src': 's1'},
        '15': {'meaning': 'Prisoner in custody', 'src': 's1'},
        '42': {'meaning': 'End of tour, off duty', 'src': 's3'},
    },
    'signal': {'20': {'meaning': 'Vehicle crash', 'src': 's2'}},
    'response': {'4': {'meaning': 'Scene secure, no further units needed', 'src': 's2'}},
}


def canon(text):
    norm, mentions = tencodes.extract(text, CODES)
    return norm, [(m.raw, m.canonical, m.kind, m.meaning, m.confidence)
                  for m in mentions]


class TestNegatives(unittest.TestCase):
    """Verbatim corpus fragments that must produce ZERO codes."""

    CASES = [
        'Can you be in route to Oxbow Hall, room 1003, for a welfare check',
        'back in the 1015 team and the mileage 46215 if there is',
        "I'm a 40-year-old male caller",
        'I got a suspicious 6627 Sullivan Road',
        'In 39, Kim Larkin, 39, occupied one time',
        'Transport 1010 15 from using a dramatic RCPD',
        '>> No more. >> ten more of that',
        'we down 28.5%',
        "That's my Bravo 8626 repeating, B862623",
    ]

    def test_no_codes_extracted(self):
        for text in self.CASES:
            with self.subTest(text=text):
                norm, mentions = canon(text)
                self.assertEqual(mentions, [], f'false positive in: {text}')

    def test_negative_text_is_returned_unchanged(self):
        for text in self.CASES:
            with self.subTest(text=text):
                norm, _ = canon(text)
                self.assertEqual(norm, text)


class TestSeparatedForms(unittest.TestCase):
    def test_hyphenated_code_resolves(self):
        _, m = canon('10-4, we are contracting.')
        self.assertEqual(m, [('10-4', '10-4', 'ten', 'Acknowledged', 'high')])

    def test_space_separated_code_normalizes_to_hyphen(self):
        norm, m = canon('One nine 10 4, same traffic')
        self.assertEqual(norm, 'One nine 10-4, same traffic')
        self.assertEqual(m[0][1], '10-4')

    def test_two_digit_wins_over_one_digit(self):
        _, m = canon('show me 10-42 for the night')
        self.assertEqual(m, [('10-42', '10-42', 'ten',
                              'End of tour, off duty', 'high')])

    def test_trailing_unit_number_is_not_absorbed(self):
        """'10-4-1-4-31' is 10-4 followed by unit 1-4-31, seen in the corpus."""
        _, m = canon('10-4-1-4-31')
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0][1], '10-4')

    def test_code_absent_from_the_set_is_recorded_unresolved(self):
        _, m = canon('send me a 10-84 out here')
        self.assertEqual(m, [('10-84', '10-84', 'ten', None, 'low')])

    def test_leading_zero_is_normalized(self):
        _, m = canon('show 10-04 please')
        self.assertEqual(m[0][1], '10-4')


class TestConcatenatedForms(unittest.TestCase):
    def test_known_code_is_split_and_marked_medium(self):
        norm, m = canon('Zachary, 43 is 1042.')
        self.assertEqual(norm, 'Zachary, 43 is 10-42.')
        self.assertEqual(m, [('1042', '10-42', 'ten',
                              'End of tour, off duty', 'medium')])

    def test_unknown_number_is_left_alone(self):
        norm, m = canon('It was 1003.')
        self.assertEqual(norm, 'It was 1003.')
        self.assertEqual(m, [])

    def test_address_word_suppresses_the_split(self):
        norm, m = canon('welfare check, room 1015, apartment D')
        self.assertEqual(norm, 'welfare check, room 1015, apartment D')
        self.assertEqual(m, [])

    def test_three_digits_after_ten_are_not_a_code(self):
        norm, m = canon('dispatching 10100 too rapidly')
        self.assertEqual(m, [])


class TestSpelledForms(unittest.TestCase):
    def test_ten_four(self):
        norm, m = canon('One nine ten four, same traffic')
        self.assertEqual(norm, 'One nine 10-4, same traffic')
        self.assertEqual(m[0][1], '10-4')

    def test_hyphenated_word_form(self):
        norm, _ = canon('the first ten-fifteen')
        self.assertEqual(norm, 'the first 10-15')

    def test_plural_form(self):
        norm, _ = canon('a couple of ten-fours')
        self.assertEqual(norm, 'a couple of 10-4')

    def test_oh_infix(self):
        norm, _ = canon('give me a ten oh four')
        self.assertEqual(norm, 'give me a 10-4')

    def test_compound_tens_and_units(self):
        norm, _ = canon('put me ten forty-two')
        self.assertEqual(norm, 'put me 10-42')

    def test_non_number_word_is_not_converted(self):
        for text in ('ten more', 'ten point five', 'ten minutes ago'):
            with self.subTest(text=text):
                norm, m = canon(text)
                self.assertEqual(norm, text)
                self.assertEqual(m, [])


class TestSignalAndResponse(unittest.TestCase):
    def test_signal_resolves(self):
        _, m = canon('possibly going to be a signal 20 if you could notify')
        self.assertEqual(m, [('signal 20', 'signal 20', 'signal',
                              'Vehicle crash', 'high')])

    def test_unknown_signal_is_recorded_unresolved(self):
        _, m = canon('His Signal 31 girlfriend is on scene')
        self.assertEqual(m, [('Signal 31', 'signal 31', 'signal', None, 'low')])

    def test_response_code_resolves(self):
        _, m = canon('backup 406, please, code 4, please.')
        self.assertEqual(m, [('code 4', 'code 4', 'response',
                              'Scene secure, no further units needed', 'high')])

    def test_unknown_response_code_is_recorded_unresolved(self):
        _, m = canon('respond code 1, lift assist')
        self.assertEqual(m, [('code 1', 'code 1', 'response', None, 'low')])


class TestOffsets(unittest.TestCase):
    def test_offsets_index_into_the_normalized_text(self):
        norm, mentions = tencodes.extract('Zachary, 43 is 1042.', CODES)
        m = mentions[0]
        self.assertEqual(norm[m.off_start:m.off_end], '10-42')

    def test_offsets_are_correct_after_a_spelled_form_shortens_the_text(self):
        norm, mentions = tencodes.extract('One nine ten four, same traffic', CODES)
        m = mentions[0]
        self.assertEqual(norm[m.off_start:m.off_end], '10-4')

    def test_multiple_mentions_have_distinct_increasing_offsets(self):
        norm, mentions = tencodes.extract('10-8, 10-42, have a good one', CODES)
        self.assertEqual(len(mentions), 2)
        self.assertLess(mentions[0].off_start, mentions[1].off_start)
        for m in mentions:
            self.assertEqual(norm[m.off_start:m.off_end], m.canonical)


class TestCodesText(unittest.TestCase):
    def test_includes_raw_canonical_and_meaning(self):
        _, mentions = tencodes.extract('Zachary, 43 is 1042.', CODES)
        blob = tencodes.codes_text(mentions)
        for token in ('1042', '10-42', 'End of tour'):
            self.assertIn(token, blob)

    def test_unresolved_code_still_contributes_raw_and_canonical(self):
        _, mentions = tencodes.extract('send me a 10-84', CODES)
        blob = tencodes.codes_text(mentions)
        self.assertIn('10-84', blob)

    def test_empty_for_no_mentions(self):
        self.assertEqual(tencodes.codes_text([]), '')


class TestPurity(unittest.TestCase):
    def test_normalized_text_is_always_returned_even_with_no_codes(self):
        norm, m = tencodes.extract('nothing to see here', CODES)
        self.assertEqual(norm, 'nothing to see here')
        self.assertEqual(m, [])

    def test_empty_input(self):
        self.assertEqual(tencodes.extract('', CODES), ('', []))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_tencodes.py' -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tencodes'`

- [ ] **Step 3: Implement the extractor**

Create `scripts/tencodes.py`:

```python
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
# code. Checked three tokens either side.
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_tencodes.py' -v`
Expected: PASS, 30 tests

If `test_three_digits_after_ten_are_not_a_code` fails on `'dispatching 10100 too rapidly'`, the cause is `\b10(\d{2})\b` matching `10100` — it cannot, because `\b` after two digits fails against a third digit. If it does fail, verify the regex has `\b` on both ends before changing anything else.

- [ ] **Step 5: Commit**

```bash
git add scripts/tencodes.py scripts/tests/test_tencodes.py
git commit -m "feat(tencodes): pure extractor with corpus-grounded tests

Recovers the three ways whisper mangles codes (concatenation, spelling,
run-together unit numbers). Every negative test case is a verbatim corpus
fragment: set membership is what separates room 1003 from code 1042."
```

---

### Task 3: Schema migration and the write path

**Files:**
- Modify: `scripts/sdr_db.py` (SCHEMA block ending at line 217; `set_transcript` at lines 272-279)
- Test: `scripts/tests/test_sdr_db.py`

**Interfaces:**
- Consumes: `tencodes.extract`, `tencodes.codes_text`, `tencodes.EXTRACTOR_VERSION`, `tencode_sets.resolve_set_id`, `tencode_sets.resolve`, `tencode_sets.set_rev`
- Produces:
  - `sdr_db.tgid_from_filename(file: str) -> int | None`
  - `sdr_db.code_context(db, tgid) -> tuple[str, dict, str]` returning `(set_id, resolved, rev)`
  - `sdr_db.apply_codes(db, file: str, transcript: str) -> None`
  - `calls.transcript_norm`, `calls.codes_text`, `calls.codes_set_id`, `calls.codes_rev`
  - `call_codes` table
  - `calls_fts` rebuilt over `(transcript_norm, codes_text)`

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_sdr_db.py` (keep the existing imports and classes):

```python
class TestCodeMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db = sdr_db.connect(self.tmp.name)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def _columns(self):
        return {r[1] for r in self.db.execute('PRAGMA table_info(calls)')}

    def test_derived_columns_exist(self):
        for col in ('transcript_norm', 'codes_text', 'codes_set_id', 'codes_rev'):
            self.assertIn(col, self._columns())

    def test_call_codes_table_exists(self):
        self.db.execute('SELECT count(*) FROM call_codes')

    def test_migration_is_idempotent(self):
        before = self._columns()
        self.db.close()
        self.db = sdr_db.connect(self.tmp.name)
        self.assertEqual(self._columns(), before)

    def test_fts_indexes_the_derived_columns(self):
        cols = [r[1] for r in self.db.execute('PRAGMA table_info(calls_fts)')]
        self.assertEqual(cols[:2], ['transcript_norm', 'codes_text'])


class TestTgidFromFilename(unittest.TestCase):
    def test_parses_the_tg_prefix(self):
        self.assertEqual(
            sdr_db.tgid_from_filename('TG16505_17-EBRP-FD1_20260830-210810.wav'),
            16505)

    def test_returns_none_for_an_unparseable_name(self):
        self.assertIsNone(sdr_db.tgid_from_filename('something-else.wav'))


class TestSetTranscriptWritesCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db = sdr_db.connect(self.tmp.name)
        self.db.execute(
            "INSERT INTO talkgroups (tgid, alpha, cat, tag) VALUES "
            "(17170, '17-BRPD TLK3', "
            "'East Baton Rouge Parish (17) - Baton Rouge Police', 'Law Talk')")
        self.db.commit()
        self.file = 'TG17170_17-BRPD-TLK3_20260830-210810.wav'

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_transcript_column_is_the_raw_text(self):
        sdr_db.set_transcript(self.db, self.file, 'Zachary, 43 is 1042.')
        row = self.db.execute(
            'SELECT transcript, transcript_norm FROM calls WHERE file = ?',
            (self.file,)).fetchone()
        self.assertEqual(row['transcript'], 'Zachary, 43 is 1042.')
        self.assertEqual(row['transcript_norm'], 'Zachary, 43 is 10-42.')

    def test_call_codes_row_is_written(self):
        sdr_db.set_transcript(self.db, self.file, 'Zachary, 43 is 1042.')
        rows = self.db.execute(
            'SELECT raw, canonical, kind, meaning, confidence FROM call_codes'
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['raw'], '1042')
        self.assertEqual(rows[0]['canonical'], '10-42')
        self.assertEqual(rows[0]['confidence'], 'medium')

    def test_reindexing_replaces_rather_than_duplicates(self):
        for _ in range(3):
            sdr_db.set_transcript(self.db, self.file, 'Zachary, 43 is 1042.')
        n = self.db.execute('SELECT count(*) AS n FROM call_codes').fetchone()['n']
        self.assertEqual(n, 1)

    def test_set_id_is_resolved_from_the_filename_not_the_row(self):
        """A transcript can land before the recorder's row, when tgid is NULL."""
        orphan = 'TG17170_17-BRPD-TLK3_20260830-999999.wav'
        sdr_db.set_transcript(self.db, orphan, '10-4')
        row = self.db.execute(
            'SELECT tgid, codes_set_id FROM calls WHERE file = ?',
            (orphan,)).fetchone()
        self.assertIsNone(row['tgid'])
        self.assertEqual(row['codes_set_id'], 'la-brpd-law')

    def test_fts_finds_a_call_by_code_meaning(self):
        sdr_db.set_transcript(self.db, self.file, 'signal 20 on Airline')
        hit = self.db.execute(
            "SELECT rowid FROM calls_fts WHERE calls_fts MATCH 'crash'"
        ).fetchone()
        self.assertIsNotNone(hit)

    def test_codes_rev_is_recorded(self):
        sdr_db.set_transcript(self.db, self.file, '10-4')
        rev = self.db.execute(
            'SELECT codes_rev FROM calls WHERE file = ?',
            (self.file,)).fetchone()['codes_rev']
        self.assertTrue(rev)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_sdr_db.py' -v`
Expected: FAIL — `sqlite3.OperationalError: no such column: transcript_norm`

- [ ] **Step 3: Update SCHEMA for freshly-created databases**

`SCHEMA` runs before `_migrate` on every connect, and `CREATE TABLE IF NOT
EXISTS` cannot add a column to an existing table. So the derived columns go in
**both** places: SCHEMA covers a database created from scratch, `_migrate`
covers `sdr.db` as it exists today. The existing `calls_fts` and its three
triggers already use `IF NOT EXISTS`, so once `_migrate` has replaced them,
SCHEMA leaves them alone.

In `scripts/sdr_db.py`, inside the `SCHEMA` string, add these four columns to
the `CREATE TABLE IF NOT EXISTS calls` block, after `session_id`:

```sql
  -- ---- derived code annotation ------------------------------------------
  -- All re-derivable from the .txt files by scripts/backfill_codes.py.
  -- `transcript` above is only ever raw whisper output and is never written
  -- by the annotation layer.
  transcript_norm TEXT,
  codes_text      TEXT,
  codes_set_id    TEXT,
  codes_rev       TEXT,
```

Replace the `calls_fts` virtual table and its three triggers with the
two-column form (see `_migrate` in Step 4 for the identical definitions):

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS calls_fts USING fts5(
  transcript_norm, codes_text,
  content = 'calls',
  content_rowid = 'id'
);

CREATE TRIGGER IF NOT EXISTS calls_ai AFTER INSERT ON calls BEGIN
  INSERT INTO calls_fts(rowid, transcript_norm, codes_text)
  VALUES (new.id, new.transcript_norm, new.codes_text);
END;
CREATE TRIGGER IF NOT EXISTS calls_ad AFTER DELETE ON calls BEGIN
  INSERT INTO calls_fts(calls_fts, rowid, transcript_norm, codes_text)
  VALUES ('delete', old.id, old.transcript_norm, old.codes_text);
END;
CREATE TRIGGER IF NOT EXISTS calls_au AFTER UPDATE ON calls BEGIN
  INSERT INTO calls_fts(calls_fts, rowid, transcript_norm, codes_text)
  VALUES ('delete', old.id, old.transcript_norm, old.codes_text);
  INSERT INTO calls_fts(rowid, transcript_norm, codes_text)
  VALUES (new.id, new.transcript_norm, new.codes_text);
END;
```

Then, immediately before the closing `"""` (after the `idx_sessions_started` line), add:

```sql
CREATE TABLE IF NOT EXISTS call_codes (
  id         INTEGER PRIMARY KEY,
  call_id    INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  raw        TEXT NOT NULL,
  canonical  TEXT NOT NULL,
  kind       TEXT NOT NULL CHECK (kind IN ('ten', 'signal', 'response')),
  meaning    TEXT,
  set_id     TEXT,
  confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
  off_start  INTEGER NOT NULL,
  off_end    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_call_codes_call  ON call_codes(call_id);
CREATE INDEX IF NOT EXISTS idx_call_codes_canon ON call_codes(canonical, call_id);
```

- [ ] **Step 4: Add the migration**

In `scripts/sdr_db.py`, add these imports at the top alongside the existing ones:

```python
import re
import tencodes
import tencode_sets
```

Add this function immediately above `def connect(`:

```python
# Bumped when the FTS layout changes. `CREATE TABLE IF NOT EXISTS` cannot
# express "add a column to an existing table", and ALTER TABLE is not
# idempotent, so this is the minimum migration mechanism the schema needs.
_USER_VERSION = 1

_DERIVED_COLUMNS = (
    ('transcript_norm', 'TEXT'),
    ('codes_text', 'TEXT'),
    ('codes_set_id', 'TEXT'),
    ('codes_rev', 'TEXT'),
)


def _migrate(db: sqlite3.Connection) -> None:
    """Add derived columns and move the FTS index onto them.

    Runs on every connect and must stay cheap and idempotent: after the first
    pass it is two PRAGMA reads.
    """
    have = {r[1] for r in db.execute('PRAGMA table_info(calls)')}
    for name, decl in _DERIVED_COLUMNS:
        if name not in have:
            db.execute(f'ALTER TABLE calls ADD COLUMN {name} {decl}')

    if db.execute('PRAGMA user_version').fetchone()[0] >= _USER_VERSION:
        return

    # calls_fts is an external-content table, so its columns must be columns of
    # `calls`. Indexing transcript_norm rather than transcript is deliberate:
    # normalization only ever rewrites code tokens, and codes_text carries the
    # raw forms too, so nothing becomes unsearchable.
    db.executescript("""
        DROP TRIGGER IF EXISTS calls_ai;
        DROP TRIGGER IF EXISTS calls_au;
        DROP TRIGGER IF EXISTS calls_ad;
        DROP TABLE IF EXISTS calls_fts;

        CREATE VIRTUAL TABLE calls_fts USING fts5(
          transcript_norm, codes_text,
          content = 'calls', content_rowid = 'id'
        );

        CREATE TRIGGER calls_ai AFTER INSERT ON calls BEGIN
          INSERT INTO calls_fts(rowid, transcript_norm, codes_text)
          VALUES (new.id, new.transcript_norm, new.codes_text);
        END;
        CREATE TRIGGER calls_ad AFTER DELETE ON calls BEGIN
          INSERT INTO calls_fts(calls_fts, rowid, transcript_norm, codes_text)
          VALUES ('delete', old.id, old.transcript_norm, old.codes_text);
        END;
        CREATE TRIGGER calls_au AFTER UPDATE ON calls BEGIN
          INSERT INTO calls_fts(calls_fts, rowid, transcript_norm, codes_text)
          VALUES ('delete', old.id, old.transcript_norm, old.codes_text);
          INSERT INTO calls_fts(rowid, transcript_norm, codes_text)
          VALUES (new.id, new.transcript_norm, new.codes_text);
        END;

        INSERT INTO calls_fts(calls_fts) VALUES('rebuild');
    """)
    db.execute(f'PRAGMA user_version = {_USER_VERSION}')
    db.commit()
```

Then change `connect()` to call it — replace:

```python
    db.executescript(SCHEMA)
    return db
```

with:

```python
    db.executescript(SCHEMA)
    _migrate(db)
    return db
```

- [ ] **Step 5: Add the code helpers and extend `set_transcript`**

In `scripts/sdr_db.py`, add above `def set_transcript(`:

```python
_TG_PREFIX = re.compile(r'^TG(\d+)_')


def tgid_from_filename(file: str) -> int | None:
    """Recordings are named TG16505_17-EBRP-FD1_20260830-210810.wav.

    Reading the talkgroup from the name rather than the row matters because
    set_transcript creates a stub row when the recorder's row has not landed
    yet, and at that moment calls.tgid is NULL.
    """
    m = _TG_PREFIX.match(os.path.basename(file))
    return int(m.group(1)) if m else None


def code_context(db: sqlite3.Connection, tgid: int | None) -> tuple[str, dict, str]:
    """(set_id, resolved set, rev) for a talkgroup."""
    cat = tag = None
    if tgid is not None:
        row = db.execute(
            'SELECT cat, tag FROM talkgroups WHERE tgid = ?', (tgid,)).fetchone()
        if row is not None:
            cat, tag = row['cat'], row['tag']
    set_id = tencode_sets.resolve_set_id(cat, tag)
    resolved = tencode_sets.resolve(set_id)
    return set_id, resolved, tencode_sets.set_rev(resolved, tencodes.EXTRACTOR_VERSION)
```

Then replace the body of `set_transcript` with:

```python
def set_transcript(db: sqlite3.Connection, file: str, transcript: str) -> None:
    """Attach a transcript and its derived codes, creating a stub row if needed.

    transcript, transcript_norm, codes_text, codes_set_id and codes_rev are
    written in ONE statement so the calls_au trigger fires once with every
    column populated. `transcript` itself is only ever the raw whisper output —
    the .txt file remains the durable copy and everything else is derived.
    """
    set_id, resolved, rev = code_context(db, tgid_from_filename(file))
    norm, mentions = tencodes.extract(transcript, resolved)
    blob = tencodes.codes_text(mentions)

    cur = db.execute(
        """UPDATE calls
              SET transcript = ?, transcript_norm = ?, codes_text = ?,
                  codes_set_id = ?, codes_rev = ?
            WHERE file = ?""",
        (transcript, norm, blob, set_id, rev, file),
    )
    if cur.rowcount == 0:
        db.execute(
            """INSERT OR IGNORE INTO calls
                 (file, start, dur, transcript, transcript_norm, codes_text,
                  codes_set_id, codes_rev)
               VALUES (?, 0, 0, ?, ?, ?, ?, ?)""",
            (file, transcript, norm, blob, set_id, rev),
        )

    row = db.execute('SELECT id FROM calls WHERE file = ?', (file,)).fetchone()
    if row is None:
        return
    call_id = row['id']
    db.execute('DELETE FROM call_codes WHERE call_id = ?', (call_id,))
    db.executemany(
        """INSERT INTO call_codes
             (call_id, raw, canonical, kind, meaning, set_id, confidence,
              off_start, off_end)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [(call_id, m.raw, m.canonical, m.kind, m.meaning, m.set_id,
          m.confidence, m.off_start, m.off_end) for m in mentions],
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -v`
Expected: PASS — all existing tests plus 13 new ones

- [ ] **Step 7: Commit**

```bash
git add scripts/sdr_db.py scripts/tests/test_sdr_db.py
git commit -m "feat(tencodes): derived columns, call_codes table and write path

Codes are written in the same transaction as the transcript, in one UPDATE so
the FTS trigger fires once with every column populated. calls.transcript is
still only ever raw whisper output. The code set resolves from the filename's
TG prefix, so a transcript arriving before the recorder's row still resolves
correctly."
```

---

### Task 4: Backfill

**Files:**
- Create: `scripts/backfill_codes.py`
- Test: `scripts/tests/test_backfill_codes.py`

**Interfaces:**
- Consumes: `sdr_db.connect`, `sdr_db.set_transcript`, `sdr_db.code_context`, `sdr_db.tgid_from_filename`
- Produces: `backfill_codes.backfill(db, only_stale: bool = False) -> dict[str, int]` returning counts keyed `scanned`, `updated`, `skipped`, `mentions`

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_backfill_codes.py`:

```python
#!/usr/bin/env python3
"""Backfill re-derivation tests.

The property under test is the one the whole design rests on: every derived
artifact can be recomputed from scratch, so a corrected code meaning
retroactively improves history.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backfill_codes  # noqa: E402
import sdr_db  # noqa: E402


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.db = sdr_db.connect(self.tmp.name)
        self.db.execute(
            "INSERT INTO talkgroups (tgid, alpha, cat, tag) VALUES "
            "(17170, '17-BRPD TLK3', "
            "'East Baton Rouge Parish (17) - Baton Rouge Police', 'Law Talk')")
        for n, text in enumerate(['Zachary, 43 is 1042.', '10-4, 4-25', 'nothing here']):
            self.db.execute(
                'INSERT INTO calls (file, start, dur, transcript) VALUES (?,?,?,?)',
                (f'TG17170_x_2026083{n}-000000.wav', 0, 0, text))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def _snapshot(self):
        return (
            self.db.execute(
                'SELECT file, transcript, transcript_norm, codes_text, '
                'codes_set_id, codes_rev FROM calls ORDER BY file').fetchall(),
            self.db.execute(
                'SELECT raw, canonical, kind, meaning, confidence, off_start, '
                'off_end FROM call_codes ORDER BY call_id, off_start').fetchall(),
        )

    def test_backfill_populates_derived_columns(self):
        backfill_codes.backfill(self.db)
        row = self.db.execute(
            "SELECT transcript_norm FROM calls WHERE transcript = 'Zachary, 43 is 1042.'"
        ).fetchone()
        self.assertEqual(row['transcript_norm'], 'Zachary, 43 is 10-42.')

    def test_backfill_never_alters_the_raw_transcript(self):
        before = self.db.execute(
            'SELECT file, transcript FROM calls ORDER BY file').fetchall()
        backfill_codes.backfill(self.db)
        after = self.db.execute(
            'SELECT file, transcript FROM calls ORDER BY file').fetchall()
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in after])

    def test_backfill_is_idempotent(self):
        backfill_codes.backfill(self.db)
        first = [[tuple(r) for r in part] for part in self._snapshot()]
        backfill_codes.backfill(self.db)
        second = [[tuple(r) for r in part] for part in self._snapshot()]
        self.assertEqual(first, second)

    def test_full_rederivation_from_a_wiped_state(self):
        backfill_codes.backfill(self.db)
        expected = [[tuple(r) for r in part] for part in self._snapshot()]

        self.db.execute('DELETE FROM call_codes')
        self.db.execute('UPDATE calls SET transcript_norm = NULL, '
                        'codes_text = NULL, codes_set_id = NULL, codes_rev = NULL')
        self.db.commit()

        backfill_codes.backfill(self.db)
        got = [[tuple(r) for r in part] for part in self._snapshot()]
        self.assertEqual(got, expected)

    def test_only_stale_skips_current_rows(self):
        backfill_codes.backfill(self.db)
        stats = backfill_codes.backfill(self.db, only_stale=True)
        self.assertEqual(stats['updated'], 0)
        self.assertEqual(stats['skipped'], 3)

    def test_only_stale_repairs_a_row_with_a_wrong_rev(self):
        backfill_codes.backfill(self.db)
        self.db.execute("UPDATE calls SET codes_rev = 'stale' "
                        "WHERE transcript = '10-4, 4-25'")
        self.db.commit()
        stats = backfill_codes.backfill(self.db, only_stale=True)
        self.assertEqual(stats['updated'], 1)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_backfill_codes.py' -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backfill_codes'`

- [ ] **Step 3: Implement the backfill script**

Create `scripts/backfill_codes.py`:

```python
#!/usr/bin/env python3
"""Re-derive transcript_norm, codes_text and call_codes for every call.

This is how a newly-sourced or corrected code set reaches history. Because
extraction is fully re-derivable, the unresolved-code report this prints is a
work queue rather than a defect list: source one more agency's codes, run
--only-stale, and past calls retroactively improve.

Usage:
  backfill_codes.py [--only-stale] [--report] [--db PATH]
"""
from __future__ import annotations

import argparse
import collections
import sys

import sdr_db


def backfill(db, only_stale: bool = False) -> dict[str, int]:
    # Initialised explicitly: Counter returns 0 for a missing key but does not
    # create it, so dict(stats) would omit 'updated' entirely on a run where
    # everything was skipped, and callers reading stats['updated'] would raise.
    stats = collections.Counter({'scanned': 0, 'updated': 0, 'skipped': 0})
    rows = db.execute(
        'SELECT file, transcript, codes_set_id, codes_rev FROM calls '
        "WHERE transcript IS NOT NULL AND trim(transcript) <> ''"
    ).fetchall()

    for row in rows:
        stats['scanned'] += 1
        if only_stale:
            set_id, _resolved, rev = sdr_db.code_context(
                db, sdr_db.tgid_from_filename(row['file']))
            if row['codes_set_id'] == set_id and row['codes_rev'] == rev:
                stats['skipped'] += 1
                continue
        sdr_db.set_transcript(db, row['file'], row['transcript'])
        stats['updated'] += 1

    db.commit()
    stats['mentions'] = db.execute(
        'SELECT count(*) AS n FROM call_codes').fetchone()['n']
    return dict(stats)


def report(db) -> str:
    """Resolved vs unresolved per agency, and the unresolved worklist."""
    lines = ['', 'Resolved / unresolved by agency', '-' * 58]
    for r in db.execute("""
        SELECT COALESCE(t.cat, '(unknown)') AS cat,
               SUM(cc.meaning IS NOT NULL) AS resolved,
               SUM(cc.meaning IS NULL)     AS unresolved
          FROM call_codes cc
          JOIN calls c      ON c.id = cc.call_id
          LEFT JOIN talkgroups t ON t.tgid = c.tgid
         GROUP BY 1 ORDER BY 2 DESC, 3 DESC"""):
        lines.append(f'{r["cat"][:44]:44} {r["resolved"]:>5} {r["unresolved"]:>5}')

    lines += ['', 'Unresolved codes — sourcing worklist', '-' * 58]
    for r in db.execute("""
        SELECT cc.canonical, cc.kind, COALESCE(c.codes_set_id, '?') AS set_id,
               count(*) AS n
          FROM call_codes cc
          JOIN calls c ON c.id = cc.call_id
         WHERE cc.meaning IS NULL
         GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 40"""):
        lines.append(f'{r["canonical"]:14} {r["kind"]:9} {r["set_id"]:20} {r["n"]:>4}')
    return '\n'.join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--db', default=sdr_db.DB_PATH)
    p.add_argument('--only-stale', action='store_true',
                   help='recompute only rows whose set id or rev has changed')
    p.add_argument('--report', action='store_true',
                   help='print resolved/unresolved counts and the worklist')
    a = p.parse_args()

    db = sdr_db.connect(a.db)
    try:
        stats = backfill(db, a.only_stale)
        print(f'{stats["scanned"]} scanned, {stats["updated"]} updated, '
              f'{stats.get("skipped", 0)} skipped, '
              f'{stats["mentions"]} code mentions stored')
        if a.report:
            print(report(db))
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -v`
Expected: PASS, all tests

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_codes.py scripts/tests/test_backfill_codes.py
git commit -m "feat(tencodes): backfill script with --only-stale and a sourcing report

Proves the layering: wiping every derived artifact and re-running reproduces
identical rows. --only-stale keys on codes_rev as well as codes_set_id, so a
corrected meaning inside an existing set is detected."
```

---

### Task 5: API — code filter and stats

**Files:**
- Modify: `server/utils/db.ts` (`CallRow` interface at lines 100-120; `getDb()` at lines 43-58)
- Modify: `server/utils/queries.ts` (`Recording` at :19, `CALL_SELECT` at :37, `toRecording` at :48, `RecordingQuery` at :69, `listRecordings` at :92)
- Create: `server/api/codes/stats.get.ts`
- Test: `server/utils/queries.test.ts`

**Interfaces:**
- Consumes: `call_codes` table and the derived `calls` columns from Task 3.
- Produces:
  - `Recording.transcriptNorm: string | null`
  - `Recording.codes: CodeMention[]`
  - `interface CodeMention { raw: string, canonical: string, kind: 'ten' | 'signal' | 'response', meaning: string | null, confidence: 'high' | 'medium' | 'low', offStart: number, offEnd: number }`
  - `RecordingQuery.code?: string`
  - `codeStats(q: CodeStatsQuery): CodeStat[]`
  - `interface CodeStatsQuery { since?: number, until?: number, tgid?: number, cat?: string, minConfidence?: 'high' | 'medium' | 'low' }`
  - `interface CodeStat { canonical: string, meaning: string | null, kind: string, calls: number, mentions: number }`

- [ ] **Step 1: Write the failing tests**

First extend the existing import at the top of `server/utils/queries.test.ts`
to bring in the new function:

```ts
import { listRecordings, codeStats } from './queries'
```

(keep whatever else that import already names). Then append, matching the
existing file's setup style:

```ts
describe('code filter and stats', () => {
  it('filters recordings by an exact code', () => {
    const { rows } = listRecordings({ code: '10-42' })
    expect(rows.length).toBeGreaterThan(0)
    for (const r of rows) {
      expect(r.codes.some(c => c.canonical === '10-42')).toBe(true)
    }
  })

  it('returns nothing for a code no call carries', () => {
    expect(listRecordings({ code: '10-999' }).rows).toHaveLength(0)
  })

  it('does not treat the hyphen in a code as an FTS operator', () => {
    // ftsQuery() strips '-', and FTS5 splits 10-50 into tokens '10' and '50',
    // so the code filter must not go through FTS at all.
    const viaCode = listRecordings({ code: '10-42' }).rows.length
    const viaSearch = listRecordings({ search: '10-42' }).rows.length
    expect(viaCode).toBeLessThanOrEqual(viaSearch)
  })

  it('combines the code filter with a talkgroup filter', () => {
    const { rows } = listRecordings({ code: '10-4', tgid: 17170 })
    for (const r of rows) expect(r.tgid).toBe(17170)
  })

  it('ships mentions with offsets into transcriptNorm', () => {
    const { rows } = listRecordings({ code: '10-42', limit: 1 })
    const rec = rows[0]!
    const m = rec.codes.find(c => c.canonical === '10-42')!
    expect(rec.transcriptNorm!.slice(m.offStart, m.offEnd)).toBe('10-42')
  })

  it('counts codes, most frequent first', () => {
    const stats = codeStats({})
    expect(stats.length).toBeGreaterThan(0)
    for (let i = 1; i < stats.length; i++) {
      expect(stats[i - 1]!.mentions).toBeGreaterThanOrEqual(stats[i]!.mentions)
    }
  })

  it('excludes medium-confidence mentions by default', () => {
    const dflt = codeStats({})
    const all = codeStats({ minConfidence: 'low' })
    const sum = (s: { mentions: number }[]) => s.reduce((a, b) => a + b.mentions, 0)
    expect(sum(dflt)).toBeLessThanOrEqual(sum(all))
  })

  it('existing transcript search is unchanged', () => {
    expect(listRecordings({ search: 'suspicious' }).rows.length).toBeGreaterThan(0)
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm exec vitest run server/utils/queries.test.ts`
Expected: FAIL — `codeStats is not defined` and `Object literal may only specify known properties, 'code'`

- [ ] **Step 3: Update `db.ts`**

In `server/utils/db.ts`, add to the `CallRow` interface after `transcript: string | null`:

```ts
  transcript_norm: string | null
```

And in `getDb()`, after the `PRAGMA busy_timeout` line, add:

```ts
  // The Python layer owns schema. If it has not run since these columns were
  // introduced, fail loudly with the command to run rather than throwing an
  // opaque "no such column" from deep inside a query — same policy as the
  // missing-database case above.
  const cols = db.prepare('PRAGMA table_info(calls)').all() as { name: string }[]
  if (!cols.some(c => c.name === 'transcript_norm')) {
    db.close()
    db = null
    throw createError({
      statusCode: 503,
      statusMessage: 'Database predates the 10-code migration. '
        + 'Run: python3 scripts/backfill_codes.py',
    })
  }
```

- [ ] **Step 4: Update `queries.ts`**

Add to the `Recording` interface, after `transcript: string | null`:

```ts
  transcriptNorm: string | null
  codes: CodeMention[]
```

Add above `interface Recording`:

```ts
/** One recognised radio code in a call's transcript. */
export interface CodeMention {
  raw: string
  canonical: string
  kind: 'ten' | 'signal' | 'response'
  meaning: string | null
  confidence: 'high' | 'medium' | 'low'
  offStart: number
  offEnd: number
}
```

Change `CALL_SELECT` to include the derived column — replace the first line
`SELECT c.file, c.tgid, c.start, c.dur, c.transcript,` with:

```sql
  SELECT c.file, c.tgid, c.start, c.dur, c.transcript, c.transcript_norm,
```

Add above `toRecording`:

```ts
interface CodeRow {
  file: string
  raw: string
  canonical: string
  kind: 'ten' | 'signal' | 'response'
  meaning: string | null
  confidence: 'high' | 'medium' | 'low'
  off_start: number
  off_end: number
}

/**
 * Mentions for a page of calls, in one query rather than one per row.
 *
 * Only ~250 mentions exist across 3,740 calls, so this is cheap; fetching them
 * per row would turn one query into 5,000.
 */
function codesFor(files: string[]): Map<string, CodeMention[]> {
  const byFile = new Map<string, CodeMention[]>()
  if (files.length === 0) return byFile

  const placeholders = files.map(() => '?').join(',')
  const rows = getDb().prepare(
    `SELECT c.file, cc.raw, cc.canonical, cc.kind, cc.meaning, cc.confidence,
            cc.off_start, cc.off_end
       FROM call_codes cc
       JOIN calls c ON c.id = cc.call_id
      WHERE c.file IN (${placeholders})
      ORDER BY c.file, cc.off_start`,
  ).all(...files) as unknown as CodeRow[]

  for (const r of rows) {
    const list = byFile.get(r.file) ?? []
    list.push({
      raw: r.raw,
      canonical: r.canonical,
      kind: r.kind,
      meaning: r.meaning,
      confidence: r.confidence,
      offStart: r.off_start,
      offEnd: r.off_end,
    })
    byFile.set(r.file, list)
  }
  return byFile
}
```

Change `toRecording` to take the mentions — replace its signature and the two
transcript-related lines:

```ts
function toRecording(r: CallRow, codes: CodeMention[] = []): Recording {
```

and inside the returned object, after `transcript: r.transcript,` add:

```ts
    transcriptNorm: r.transcript_norm,
    codes,
```

Add to `RecordingQuery`:

```ts
  /** Exact canonical code, e.g. "10-42". Goes through call_codes, not FTS. */
  code?: string
```

In `listRecordings`, add this predicate after the `enc` block:

```ts
  if (q.code) {
    // Exact match on an indexed column. FTS5 strips punctuation and splits
    // "10-50" into "10" and "50", so codes cannot be filtered through it.
    where.push('EXISTS (SELECT 1 FROM call_codes cc WHERE cc.call_id = c.id AND cc.canonical = ?)')
    params.push(q.code)
  }
```

And replace the final return with:

```ts
  const byFile = codesFor(rows.map(r => r.file))
  return {
    rows: rows.map(r => toRecording(r, byFile.get(r.file) ?? [])),
    total: total.n,
  }
```

In `getRecording`, replace the return with:

```ts
  if (!row) return null
  return toRecording(row, codesFor([row.file]).get(row.file) ?? [])
```

Add at the end of the file:

```ts
// ------------------------------------------------------------- radio codes

export interface CodeStatsQuery {
  since?: number
  until?: number
  tgid?: number
  cat?: string
  minConfidence?: 'high' | 'medium' | 'low'
}

export interface CodeStat {
  canonical: string
  meaning: string | null
  kind: string
  calls: number
  mentions: number
}

const CONFIDENCE_RANK = { high: 3, medium: 2, low: 1 } as const

/**
 * Code counts for a window. A GROUP BY over call_codes with no transcript text
 * touched, so it stays cheap enough to poll.
 *
 * `minConfidence` defaults to 'high', which excludes the concatenated-form
 * splits ("1042" -> 10-42) from counts unless deliberately requested.
 */
export function codeStats(q: CodeStatsQuery = {}): CodeStat[] {
  const where: string[] = []
  const params: (string | number)[] = []

  const rank = CONFIDENCE_RANK[q.minConfidence ?? 'high']
  where.push(`CASE cc.confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END >= ?`)
  params.push(rank)

  if (q.since !== undefined) { where.push('c.start >= ?'); params.push(q.since) }
  if (q.until !== undefined) { where.push('c.start <= ?'); params.push(q.until) }
  if (q.tgid !== undefined) { where.push('c.tgid = ?'); params.push(q.tgid) }
  if (q.cat) { where.push('t.cat = ?'); params.push(q.cat) }

  return getDb().prepare(
    `SELECT cc.canonical, cc.kind,
            MAX(cc.meaning)              AS meaning,
            COUNT(DISTINCT cc.call_id)   AS calls,
            COUNT(*)                     AS mentions
       FROM call_codes cc
       JOIN calls c           ON c.id   = cc.call_id
       LEFT JOIN talkgroups t ON t.tgid = c.tgid
      WHERE ${where.join(' AND ')}
      GROUP BY cc.canonical, cc.kind
      ORDER BY mentions DESC, cc.canonical`,
  ).all(...params) as unknown as CodeStat[]
}
```

- [ ] **Step 5: Create the stats route**

Create `server/api/codes/stats.get.ts`:

```ts
import { codeStats } from '../../utils/queries'
import type { CodeStatsQuery } from '../../utils/queries'

/**
 * Aggregate radio-code counts. Powers the code filter's option list and any
 * "which codes are busiest" view, without a separate analytics page.
 */
export default defineEventHandler((event) => {
  const q = getQuery(event)

  const num = (v: unknown): number | undefined => {
    const n = Number(v)
    return v === undefined || Number.isNaN(n) ? undefined : n
  }

  const conf = typeof q.minConfidence === 'string' ? q.minConfidence : undefined
  const query: CodeStatsQuery = {
    since: num(q.since),
    until: num(q.until),
    tgid: num(q.tgid),
    cat: typeof q.cat === 'string' ? q.cat : undefined,
    minConfidence: conf === 'high' || conf === 'medium' || conf === 'low'
      ? conf
      : undefined,
  }

  return codeStats(query)
})
```

- [ ] **Step 6: Wire `code` into the recordings route**

In `server/api/recordings/index.get.ts`, add `code` to the query object passed
to `listRecordings`, alongside the existing `search`/`enc`/`tgid` handling:

```ts
    code: typeof query.code === 'string' ? query.code : undefined,
```

- [ ] **Step 7: Run the tests and typecheck**

Run: `pnpm exec vitest run` — Expected: PASS
Run: `pnpm typecheck` — Expected: no errors
Run: `pnpm lint` — Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add server/utils/db.ts server/utils/queries.ts server/utils/queries.test.ts \
        server/api/codes/stats.get.ts server/api/recordings/index.get.ts
git commit -m "feat(tencodes): code filter and stats endpoint

Exact code filtering goes through call_codes and its index, never FTS: FTS5
strips punctuation and splits 10-50 into two tokens. Free-text search gains
meanings for free, because codes_text is indexed."
```

---

### Task 6: UI

**Files:**
- Modify: `components/RecordingsList.vue` — template lines 61 (search box), 103-112 (transcript column), 167-176 (filter row), 189-198 (detail transcript); script lines 213-226 (`Recording` interface), 236 (`transcript` ref), 478-497 (`open`)

**Interfaces:**
- Consumes: `Recording.transcriptNorm`, `Recording.codes` (`CodeMention`) from Task 5, `/api/codes/stats`.
- Produces: no exports; this is the leaf.

- [ ] **Step 1: Extend the client-side `Recording` interface**

In the `<script setup>` block, add to the local `Recording` interface after
`transcript: string | null`:

```ts
  transcriptNorm: string | null
  codes: CodeMention[]
```

And add above it:

```ts
interface CodeMention {
  raw: string
  canonical: string
  kind: 'ten' | 'signal' | 'response'
  meaning: string | null
  confidence: 'high' | 'medium' | 'low'
  offStart: number
  offEnd: number
}

interface Segment {
  text: string
  code?: CodeMention
}

/**
 * Split transcript text into plain and code-bearing segments using the
 * offsets the server supplies.
 *
 * Rendered with v-for rather than v-html: no injection surface, and no
 * re-running the extractor's regex in the browser.
 */
function segments(text: string | null, codes: CodeMention[]): Segment[] {
  if (!text) return []
  if (codes.length === 0) return [{ text }]

  const out: Segment[] = []
  let pos = 0
  for (const c of codes) {
    if (c.offStart < pos || c.offEnd > text.length) continue
    if (c.offStart > pos) out.push({ text: text.slice(pos, c.offStart) })
    out.push({ text: text.slice(c.offStart, c.offEnd), code: c })
    pos = c.offEnd
  }
  if (pos < text.length) out.push({ text: text.slice(pos) })
  return out
}

/**
 * 10-4 is 94 of ~250 mentions. Annotating the one code everyone knows would
 * turn the column into a field of underlines, so a resolved code is only
 * marked when its meaning adds something. Unresolved codes are never marked —
 * there is nothing to show.
 */
const COMMON_CODES = new Set(['10-4'])

function isAnnotated(c: CodeMention | undefined): boolean {
  return !!c && !!c.meaning && !COMMON_CODES.has(c.canonical)
}

function codeTitle(c: CodeMention): string {
  const base = `${c.canonical} — ${c.meaning}`
  return c.confidence === 'medium' ? `${base} (inferred from "${c.raw}")` : base
}
```

- [ ] **Step 2: Render segments in the table cell**

Replace the transcript `Column` body template (lines 103-112) with:

```vue
      <Column field="transcript" header="Transcript" sortable>
        <template #body="{ data }">
          <div
            v-if="data.transcript"
            class="transcript"
            :class="{ blank: isBlank(data.transcript) }"
          ><span
            v-for="(seg, i) in segments(data.transcriptNorm ?? data.transcript, data.codes)"
            :key="i"
            :class="{ tencode: isAnnotated(seg.code) }"
            :title="isAnnotated(seg.code) ? codeTitle(seg.code!) : undefined"
          >{{ seg.text }}</span></div>
          <span v-else class="text-color-secondary">—</span>
        </template>
      </Column>
```

The `<span>` elements are written without whitespace between them and the
surrounding `<div>` so Vue does not insert text nodes that would alter spacing.

- [ ] **Step 3: Add the underline style**

In the `<style>` block, after the `.transcript` rule, add:

```css
/*
  Dotted underline only — no padding, no background, no border box. The
  virtual scroller needs a constant row height, and .transcript above is
  already capped at 3.9em with internal scrolling, so inline marks are safe
  provided they do not change the line box. A padded chip would.
*/
.tencode {
  border-bottom: 1px dotted var(--p-primary-color);
  cursor: help;
}
```

- [ ] **Step 4: Add the code filter**

Replace the filter row (lines 167-176) with:

```vue
    <div class="flex gap-2 mb-3">
      <InputText
        v-model="search" class="flex-1"
        aria-label="Search recordings"
        placeholder="Search talkgroup, alpha, description, category, filename, transcript or code meaning"
      />
      <Select
        v-model="codeFilter" :options="codeOptions"
        option-label="label" option-value="value" class="w-12rem"
        aria-label="Filter by radio code"
      />
      <Select
        v-model="encFilter" :options="encOptions"
        option-label="label" option-value="value" class="w-10rem"
        aria-label="Filter by encryption"
      />
    </div>
```

And in the script, after `const encOptions = [...]`:

```ts
const codeFilter = ref('all')

interface CodeStat {
  canonical: string
  meaning: string | null
  kind: string
  calls: number
  mentions: number
}

const codeStats = ref<CodeStat[]>([])

// Only codes actually present in the corpus are offered, so the list never
// suggests a filter that returns nothing.
const codeOptions = computed(() => [
  { value: 'all', label: 'All codes' },
  ...codeStats.value.map(s => ({
    value: s.canonical,
    label: s.meaning
      ? `${s.canonical} · ${s.meaning} (${s.calls})`
      : `${s.canonical} (${s.calls})`,
  })),
])

async function loadCodeStats(): Promise<void> {
  try {
    codeStats.value = await $fetch<CodeStat[]>('/api/codes/stats')
  } catch {
    codeStats.value = []
  }
}
```

Call `loadCodeStats()` alongside the existing initial `load()` call in
`onMounted`, and add `codeFilter` to whatever watcher currently triggers a
reload on `search`/`encFilter`, passing it through to the request:

```ts
  if (codeFilter.value !== 'all') params.code = codeFilter.value
```

- [ ] **Step 5: Add the detail-dialog Codes block**

Replace the detail transcript block (lines 189-198) with:

```vue
        <div>
          <h3 class="text-base font-bold mb-2">Transcript</h3>
          <ProgressSpinner v-if="loadingTranscript" style="width: 2rem; height: 2rem" />
          <p
            v-else-if="transcript"
            class="m-0 text-sm line-height-3"
            :class="{ blank: isBlank(transcript) }"
          ><span
            v-for="(seg, i) in segments(transcriptNorm || transcript, selected?.codes ?? [])"
            :key="i"
            :class="{ tencode: isAnnotated(seg.code) }"
            :title="isAnnotated(seg.code) ? codeTitle(seg.code!) : undefined"
          >{{ seg.text }}</span></p>
          <p v-else class="m-0 text-sm text-color-secondary">No transcript for this call.</p>
        </div>

        <div v-if="selected?.codes.length">
          <h3 class="text-base font-bold mb-2">Codes</h3>
          <ul class="m-0 pl-3 text-sm">
            <li v-for="(c, i) in selected.codes" :key="i" class="mb-1">
              <strong>{{ c.canonical }}</strong>
              <template v-if="c.meaning"> — {{ c.meaning }}</template>
              <template v-else>
                <span class="text-color-secondary">
                  — no definition in this agency's code set
                </span>
              </template>
              <span v-if="c.confidence !== 'high'" class="text-color-secondary">
                (inferred from “{{ c.raw }}”)
              </span>
            </li>
          </ul>
        </div>
```

And in the script, add alongside `const transcript = ref('')`:

```ts
const transcriptNorm = ref('')
```

Then update `open()` — replace its body with:

```ts
async function open(rec: Recording): Promise<void> {
  selected.value = rec
  dialogOpen.value = true

  if (rec.transcript) {
    transcript.value = rec.transcript
    transcriptNorm.value = rec.transcriptNorm ?? rec.transcript
    return
  }

  transcript.value = ''
  transcriptNorm.value = ''
  loadingTranscript.value = true
  try {
    transcript.value = await $fetch<string>(
      `/api/recordings/${rec.file.replace(/\.wav$/, '.txt')}`,
    )
    // The .txt fallback is raw whisper output with no derived companion, so
    // there is nothing to annotate against and the raw text is shown as-is.
    transcriptNorm.value = ''
  } catch {
    transcript.value = ''
    transcriptNorm.value = ''
  } finally {
    loadingTranscript.value = false
  }
}
```

- [ ] **Step 6: Verify in the browser**

Run: `pnpm dev`
Then check, in order:
1. The recordings table renders with no console errors and rows are the same height as before.
2. A call whose transcript contains `10-42` shows it underlined; hovering gives "10-42 — End of tour, off duty".
3. A call containing `10-4` shows it plain, with no underline.
4. Selecting `10-42` in the code filter narrows the table, and the count in the option label matches the row count.
5. Opening a call with codes shows the Codes block; a call with an unresolved code shows "no definition in this agency's code set".
6. Typing `crash` in the search box returns calls containing `signal 20`.

- [ ] **Step 7: Run lint and typecheck**

Run: `pnpm lint` — Expected: no errors
Run: `pnpm typecheck` — Expected: no errors
Run: `pnpm test` — Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add components/RecordingsList.vue
git commit -m "feat(tencodes): annotate codes in the recordings table and detail view

Segments are built from server-supplied offsets and rendered with v-for, not
v-html. Marks are a dotted underline only: the transcript cell is already
height-capped for the virtual scroller, so inline marks are safe but a padded
chip would change the line box. 10-4 is left unmarked — it is 94 of ~250
mentions and needs no gloss."
```

---

### Task 7: Backfill the corpus and report

**Files:**
- No source changes. This runs the pipeline against the real database.

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: a populated `call_codes`, and `results/tencode_report.txt` — the sourcing worklist.

- [ ] **Step 1: Back up the database**

```bash
cp sdr.db sdr.db.pre-tencodes
```

The migration drops and rebuilds `calls_fts`. That is recoverable by re-running
the migration, but a copy costs 3 MB and removes the question.

- [ ] **Step 2: Stop the transcriber so nothing writes mid-migration**

```bash
pkill -f stt_watch.py || true
```

Confirm nothing remains: `pgrep -af stt_watch.py` should print nothing.

- [ ] **Step 3: Run the backfill with a report**

```bash
python3 scripts/backfill_codes.py --report | tee results/tencode_report.txt
```

Expected: roughly `~3,800 scanned, all updated, 0 skipped, ~230 code mentions stored`.
The exact count moves with the corpus, which grows while live capture runs; what
matters is that `scanned` equals the number of non-empty transcripts and
`updated` equals `scanned`.
followed by the per-agency table and the unresolved worklist.

- [ ] **Step 4: Verify the derived layer against the corpus**

```bash
python3 - <<'EOF'
import sqlite3
c = sqlite3.connect('file:sdr.db?mode=ro', uri=True)
q = c.execute

print('raw transcripts unchanged:',
      q("SELECT count(*) FROM calls WHERE transcript IS NOT NULL").fetchone()[0])
print('normalized populated:',
      q("SELECT count(*) FROM calls WHERE transcript_norm IS NOT NULL").fetchone()[0])
print('mentions:', q("SELECT count(*) FROM call_codes").fetchone()[0])
print()
print('by confidence:')
for r in q("SELECT confidence, count(*) FROM call_codes GROUP BY 1 ORDER BY 2 DESC"):
    print(' ', r)
print()
print('top resolved codes:')
for r in q("""SELECT canonical, meaning, count(*) n FROM call_codes
              WHERE meaning IS NOT NULL GROUP BY 1,2 ORDER BY n DESC LIMIT 12"""):
    print(' ', r)
print()
print('FTS finds a code by meaning:',
      q("""SELECT count(*) FROM calls_fts WHERE calls_fts MATCH 'custody'""").fetchone()[0])
EOF
```

Expected: `transcript_norm` populated for every transcribed call; ~230 mentions,
about 190 high / 13 medium / 22 low, roughly 90% carrying a meaning;
`10-4` the most frequent; a non-zero count for the meaning search.

- [ ] **Step 5: Verify idempotence on the real database**

```bash
python3 scripts/backfill_codes.py --only-stale
```

Expected: `N scanned, 0 updated, N skipped` — nothing is stale immediately
after a full backfill.

- [ ] **Step 6: Restart the transcriber and confirm live writes carry codes**

Start the transcriber through the UI's transcriber control, or:

```bash
python3 scripts/stt_watch.py --dir recordings &
```

Then, after the next call is transcribed:

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('file:sdr.db?mode=ro', uri=True)
for r in c.execute('''SELECT c.file, c.codes_set_id, cc.canonical, cc.meaning
                        FROM calls c JOIN call_codes cc ON cc.call_id = c.id
                       ORDER BY c.start DESC LIMIT 10'''):
    print(r)"
```

Expected: recent calls carry `codes_set_id` and any codes they contain.

- [ ] **Step 7: Commit the report and remove the backup**

```bash
git add results/tencode_report.txt
git commit -m "chore(tencodes): backfill the corpus and record the sourcing worklist

Unresolved codes ranked by frequency per agency. This is the input to the next
round of code-set sourcing: confirm a meaning, add it to the agency's set, and
run backfill_codes.py --only-stale to apply it to history."
rm sdr.db.pre-tencodes
```

Only remove the backup after Step 4 and Step 6 have both passed.
