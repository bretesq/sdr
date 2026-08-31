# 10-Code Recognition and Annotation for Call Transcripts

**Date:** 2026-08-31
**Author:** bretesq
**Status:** Design (Ready for Implementation)

## Executive Summary

Whisper transcribes LWIN radio traffic well enough to read, but it mangles the
one vocabulary that carries the most operational meaning: radio codes. This adds
a **derived annotation layer** over existing transcripts that recognises 10-codes,
signal codes and response codes, expands them from per-agency code sets, and makes
them searchable, filterable and countable.

Four capabilities, one pipeline:

1. **Read** codes expanded in the UI (tooltip on hover).
2. **Search** by code or by meaning — typing `accident` finds 10-50 calls.
3. **Filter and count** — "every 10-50 on BRPD tonight", top codes per talkgroup.
4. **Corrected text** — `1042` reads as `10-42`, `ten four` as `10-4`.

`calls.transcript` is **never rewritten**. The `.txt` files stay the durable copy,
`transcript` stays the raw whisper record, and every artifact this feature produces
is re-derivable from scratch with one command.

---

## Measured Baseline

Dumped every code-shaped hit across all 3,728 non-empty transcripts (of 3,740 calls) in `sdr.db`:

| Form | Hits | Top values |
|---|---|---|
| `10-NN` (separated) | 168 | 10-4 x94, 10-8 x32, 10-15 x10, 10-9 x6 |
| `10NN` (concatenated) | 37 | 1015, 1042, 1064, 1040, 1097, 1098 |
| `ten-<word>` (spelled) | 30 | ten four x14, ten eight x4 |
| `signal NN` | 4 | signal 20, 31, 18 |
| `code N` | 14 | code 4 x6, code 1 x5, code 2 x3 |

~250 explicit mentions, ~6.7% of calls. `10-4` alone is 94 of them.

Whisper mangles codes in exactly three mechanical ways, all recoverable:
concatenation (`10-42` -> `1042`), spelling (`ten four`), and run-together
trailing unit numbers (`10-4-1-4-31`).

### Corpus composition

21 distinct agencies across four service disciplines:

| Discipline (`talkgroups.tag`) | Calls |
|---|---|
| Law (Dispatch/Talk/Tac) | 2,389 |
| EMS (Dispatch/Tac/Talk/Hospital) | 657 |
| Fire (Dispatch/Tac/Talk) | 318 |
| Corrections | 259 |

Top agencies (`talkgroups.cat`): BRPD (734), EBRSO (615), EBR Fire/EMS (463),
West Baton Rouge (359), EMS Agencies (299), East Feliciana (208), LSU PD (152),
BR Fire (149), LSP Troop A (147), Livingston (106), plus Pointe Coupee,
West Feliciana, Iberville, Southern University, Baker, Central and LDWF.

---

## Scope

### In scope

- Explicit code forms only: `10-NN`, `10 NN`, `10NN`, `ten-<word>`, `signal NN`, `code N`.
- Per-agency, per-discipline code sets with an explicit resolution chain.
- Derived storage (`transcript_norm`, `codes_text`, `call_codes`), FTS over the
  derived text, exact code filtering, aggregate stats endpoint.
- UI annotation in the recordings table and detail dialog; code filter.
- Backfill of the 3,740 existing calls.

### Explicitly out of scope

**Bare-number codes.** The corpus proves this is unwinnable without context
modelling. `"28 is going to be displayed on the white leaf on Altima"` genuinely
is a 10-28 registration check — but so are `mileage 46215`, `6627 Sullivan Road`,
`40-year-old male`, `39, Kim Larkin, 39` and `B862623`. Recorded here so it is
not relitigated during implementation.

**Whisper prompt biasing.** `whisper-cli` supports `--prompt`, but clips are very
short (median transcript 25 chars, p90 103) and 12 are silent. Priming code
vocabulary into a model decoding 2-second clips is a known recipe for
hallucinating `10-4` onto noise. Post-processing is risk-free, works on all
existing transcripts immediately, and costs no re-transcription. `--grammar` is
rejected outright: GBNF constrains the entire output, so it would require a
grammar accepting arbitrary English.

---

## Section 1 — Code-Set Data

`data/tencodes/` holds one JSON file per (agency, discipline) plus a resolver index.

```json
// data/tencodes/sets/la-ebrso-law.json
{
  "id": "la-ebrso-law",
  "name": "East Baton Rouge Parish Sheriff's Office — Law",
  "discipline": "law",
  "extends": "la-generic-law",
  "sources": [
    { "ref": "s1", "url": "https://www.radioreference.com/...",
      "retrieved": "2026-08-31", "note": "EBRSO 10-code list" }
  ],
  "ten":      { "42": { "meaning": "End of shift", "src": "s1" },
                "4":  { "meaning": "Acknowledged", "src": "s1", "common": true } },
  "signal":   { "20": { "meaning": "...", "src": "s1" } },
  "response": { "2":  { "meaning": "Urgent, no lights/siren", "src": "s1" } }
}
```

**Field semantics**

- `extends` builds the resolution chain, e.g. `la-brpd-law` -> `la-generic-law` -> end.
  A code absent from the entire chain resolves to *unknown* and renders
  un-expanded. No entry is ever invented.
- `src` references an entry in this file's `sources`, so every expansion is
  traceable to where it came from. An entry without a resolvable `src` fails a
  data-integrity test — this is the mechanism that keeps "sourced properly"
  enforceable rather than aspirational.
- `common: true` marks codes that need no gloss (10-4). Still extracted, indexed
  and counted; not visually annotated. Without this, 40% of annotations would be
  pure noise.

**Resolver index** — first match wins, glob on `cat` and `tag`:

```json
// data/tencodes/index.json
[
  { "cat": "*Baton Rouge Police*",   "tag": "Law*",     "set": "la-brpd-law" },
  { "cat": "*(17) - Sheriff*",       "tag": "Law*",     "set": "la-ebrso-law" },
  { "cat": "State Police*",          "tag": "Law*",     "set": "la-lsp-law" },
  { "cat": "LSU*",                   "tag": "Law*",     "set": "la-lsupd-law" },
  { "cat": "*",                      "tag": "Fire*",    "set": "la-generic-fire" },
  { "cat": "*",                      "tag": "EMS*",     "set": "la-generic-ems" },
  { "cat": "*",                      "tag": "Hospital", "set": "la-generic-ems" },
  { "cat": "*",                      "tag": "*",        "set": "la-generic-law" }
]
```

Resolution is keyed on **(agency, discipline)**, not agency alone. EBR Sheriff and
EBR Fire share a parish but not a codebook: Fire/EMS largely use plain language
plus response codes, Law uses 10-codes. That is 975 Fire/EMS calls where applying
a police 10-code table would produce confidently wrong expansions.

### Sourcing findings (verified 2026-08-31)

A search pass before committing this design changed the Phase 1 plan. Recorded
here so it is not re-discovered during implementation:

- **Louisiana agencies do not follow APCO.** LSP and local agencies use a code
  set distinct from APCO and from neighbouring states, and they mix 10-codes,
  *signal* codes, plain language and statute shorthand.
- **Statewide/LSP-level codes are citable, and the corpus corroborates them.**
  Published LSP signal codes include `signal 20` (vehicle crash), `signal 18`
  (stranded motorist), `signal 98` (DUI), `signal 100` (hit-and-run),
  `signal 103` (disturbance). The corpus independently contains `signal 20` (x2),
  `signal 18` and `signal 31` — agreement between an external source and observed
  traffic, which is the strongest validation available without agency documents.
- **Agency-specific BRPD and EBRSO lists are NOT reliably published.** A
  RadioReference discussion of Louisiana 10-codes states that the single list
  circulating online is "completely incorrect". Treating any such list as
  authoritative would poison the membership test that the whole extractor depends
  on.

**Consequence for Phase 1.** Build only what is defensible:

| Set | Source | Status |
|---|---|---|
| `la-generic-law` | Published LSP / Louisiana signal + 10-code lists, cross-checked against corpus occurrences | Buildable now |
| `la-generic-fire`, `la-generic-ems` | Response codes (`code 1/2/3`) — small, well-attested | Buildable now |
| `la-brpd-law`, `la-ebrso-law`, `la-lsp-law`, `la-lsupd-law` | Not reliably published | **Start empty**, `extends` the generic set |
| Small parishes | Not published | No file; resolve to `la-generic-law` |

An empty agency set that only `extends` the generic one is a valid, working
configuration — the chain does the work, and the file exists as the place a
verified agency-specific code goes once one is confirmed.

Two paths fill the agency sets over time, and the design supports both without
change: the operator supplies a list they trust, or a code is confirmed from the
corpus itself. Phase 6's unresolved-code report is what drives this — it names
codes actually spoken on a given agency's talkgroups, ranked by frequency, which
is a far better sourcing worklist than a generic list of 100 codes most of which
never air here.

**A missing list always degrades to "no expansion", never to a wrong one.** That
is the property that makes shipping with mostly-empty agency sets acceptable.

---

## Section 2 — Normalizer and Extractor

`scripts/tencodes.py` exposes one pure function:

```python
def extract(text: str, set_id: str) -> tuple[str, list[Mention]]

@dataclass(frozen=True)
class Mention:
    raw: str            # "1042", exactly as it appeared in the input
    canonical: str      # "10-42"
    kind: str           # 'ten' | 'signal' | 'response'
    meaning: str | None # None when unresolved
    set_id: str | None  # set in the chain that supplied `meaning`
    confidence: str     # 'high' | 'medium' | 'low'
    off_start: int      # offsets into the RETURNED normalized text,
    off_end: int        # not into the input
```

The returned normalized text is **always** populated, even when no code is found:
it equals the input in that case. `transcript_norm` is therefore never NULL for a
call that has a transcript, which is what lets FTS index it unconditionally.

No I/O, no database, no globals. That is what makes it testable against the 250
real corpus hits.

### Pass 1 — spelled numbers to digits, in code position only

`ten four` -> `10-4`, `ten-fifteen` -> `10-15`, `ten oh four` -> `10-4`.

Guarded, because the corpus contains `ten more`, `ten point`, `ten oh` and
`ten-fours`. Rule: convert only when the following token is a number word.
`ten-fours` (plural) maps to `10-4`.

### Pass 2 — candidate detection with a membership test

| Pattern | Rule | Confidence |
|---|---|---|
| `10-NN`, `10 NN` | `NN` present in chain -> resolved | high |
| `10-NN` | `NN` absent from chain -> recorded, unexpanded | low |
| `10NN` | split **only if** `NN` in chain **and** no address/room word within 3 tokens | medium |
| `signal NN` | `NN` in chain's `signal` table | high |
| `code N` | `N` in chain's `response` table | high |

The `10NN` guard is what stops `room 1003` and `Transport 1010 15` becoming
codes. Address/room stop-words: room, apartment, apt, suite, unit, block,
mileage, milepost, plus street suffixes. Splits are marked *medium* so analytics
can exclude them and the UI can render them tentatively.

### Pass 3 — longest valid match, then stop

`10-4-1-4-31` matches `10-4`; trailing digits are a unit number and are left
alone. Two-digit candidates are tested before one-digit, so `10-42` never
mis-reads as `10-4` followed by `2`.

**Why the data must exist before the extractor:** `1003` (a dorm room) and `1042`
(a real code) are lexically identical. Only set membership separates them.

---

## Section 3 — Schema and Pipeline Integration

Two facts about the existing codebase shape this:

- `sdr_db.connect()` re-runs an idempotent `CREATE TABLE IF NOT EXISTS` script on
  every open. There is **no migration mechanism**, and `ALTER TABLE ADD COLUMN`
  is not idempotent.
- `calls_fts` is an **external-content** FTS5 table (`content='calls'`), so any
  indexed column must be a real column on `calls`.

### New columns and table

```sql
-- added to calls by the migration guard
transcript_norm TEXT,   -- code-normalized text; FTS indexes this, not `transcript`
codes_text      TEXT,   -- "1042 10-42 end of shift" — raw + canonical + meaning,
                        -- space-joined over every mention. Unresolved codes
                        -- contribute raw + canonical only, so they stay
                        -- searchable before their set is sourced.
codes_set_id    TEXT,   -- which set resolved this call
codes_rev       TEXT,   -- short hash of (extractor version + resolved chain
                        -- content). This, not codes_set_id, is what makes a
                        -- row findable as stale.

CREATE TABLE IF NOT EXISTS call_codes (
  id         INTEGER PRIMARY KEY,
  call_id    INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  raw        TEXT NOT NULL,   -- "1042", exactly as whisper wrote it
  canonical  TEXT NOT NULL,   -- "10-42"
  kind       TEXT NOT NULL CHECK (kind IN ('ten','signal','response')),
  meaning    TEXT,            -- NULL when unresolved. Never a guess.
  set_id     TEXT,            -- the set in the chain that supplied `meaning`
  confidence TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
  off_start  INTEGER NOT NULL,  -- offsets into transcript_norm
  off_end    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_call_codes_call  ON call_codes(call_id);
CREATE INDEX IF NOT EXISTS idx_call_codes_canon ON call_codes(canonical, call_id);
```

**Layering acceptance test:**

```sql
DELETE FROM call_codes;
UPDATE calls SET transcript_norm = NULL, codes_text = NULL, codes_set_id = NULL;
```

then `python3 scripts/backfill_codes.py` must reproduce byte-identical results.

### Migration

A `_migrate(db)` in `sdr_db.py`, called from `connect()` immediately after
`executescript(SCHEMA)`:

- reads `PRAGMA table_info(calls)` and adds only missing columns;
- gates the one-off FTS rebuild on `PRAGMA user_version`.

Python owns schema. `server/utils/db.ts` opens read-only, so `getDb()` gains a
one-time column check that throws the same loud "run this command" error the file
already uses for a missing database — no silent fallback, consistent with the
existing design philosophy stated in that file.

### FTS rebuild

```sql
DROP TABLE calls_fts;
CREATE VIRTUAL TABLE calls_fts USING fts5(
  transcript_norm, codes_text, content='calls', content_rowid='id');
-- calls_ai / calls_au / calls_ad triggers rewritten to carry both columns
INSERT INTO calls_fts(calls_fts) VALUES('rebuild');   -- 3,740 rows, one-off
```

Indexing `transcript_norm` rather than `transcript` is deliberate: normalization
only ever rewrites code tokens, and `codes_text` carries the raw forms too, so
nothing becomes unsearchable and the index does not double in size.

### Write path

`set_transcript()` gains the extraction, resolving the code set from the
filename's `TG<tgid>` prefix rather than the row's `tgid`:

```python
def set_transcript(db, file, transcript):
    set_id = resolve_set(db, tgid_from_filename(file))
    norm, mentions = tencodes.extract(transcript, set_id)
    # ONE update statement, so the FTS trigger fires once with all columns populated
    cur = db.execute("""UPDATE calls SET transcript = ?, transcript_norm = ?,
                        codes_text = ?, codes_set_id = ? WHERE file = ?""", ...)
    ...  # then replace call_codes rows for this call, in the same transaction
```

Filename-derived tgid matters because `set_transcript()` creates a stub row when
the recorder's row has not landed yet (`sdr_db.py:275`), at which point `tgid` is
NULL. Recordings are named `TG16505_17-EBRP-FD1_20260830-210810.wav`, so the
talkgroup is always available and the race is moot.

`stt_watch.py` and `stt_transcribe.py` need no changes — both already call
`set_transcript`. Codes land in the same commit as the transcript, so the
existing SSE broadcast carries them with no new plumbing.

### Backfill

`scripts/backfill_codes.py` re-derives everything from the `.txt` files. This is
how a newly-sourced code set gets applied to history.

- default: recompute all rows;
- `--only-stale`: recompute rows whose `codes_set_id` **or** `codes_rev` differs
  from what the resolver and extractor produce today. Also cleans up calls whose
  transcript arrived before their `tgid` did.

**Why `codes_rev` and not `codes_set_id` alone.** `codes_set_id` only changes when
a call resolves to a *different* set. Correcting a `meaning` inside an existing
set, adding a code to it, or changing the `extends` chain leaves `codes_set_id`
identical — so `--only-stale` would silently skip every affected row, and the
corrected meaning would never reach `codes_text` (which is FTS-indexed) or
`call_codes.meaning`. Since meanings will be corrected repeatedly as sets are
sourced and validated, that is the common case, not an edge case.

`codes_rev` is a short hash over the extractor version plus the fully-resolved
chain content for that set. It changes on any edit that could alter output,
including a change to the extractor itself, so re-derivation stays correct
without anyone having to remember which kind of edit needs a full backfill.

One exception by design: `common` affects rendering only, never `codes_text` or
`call_codes`. It is excluded from the `codes_rev` hash, so toggling it takes
effect immediately with no backfill.

---

## Section 4 — Search and Analytics

### Exact code filtering does not go through FTS

`ftsQuery()` (`server/utils/queries.ts:81`) strips `-` from the search string, and
FTS5's default tokenizer splits `10-50` into tokens `10` and `50` regardless. So
searching "10-50" today matches any call containing "10" and "50" separately.

Exact filtering uses `call_codes` and `idx_call_codes_canon`, an index seek:

```
GET /api/recordings?code=10-50
GET /api/recordings?code=10-50&tgid=17170&since=...
```

`listRecordings()` gains one optional `code` predicate alongside the existing
`tgid`/`enc`/`search`. `ftsQuery()` is left untouched — no hyphen hack, no
tokenizer workaround.

FTS5 is a fuzzy relevance index: it strips punctuation and prefix-matches, which
is right for "find calls about an accident" and wrong for "count every 10-50".
Codes are a closed, enumerable vocabulary and belong in a normal indexed table
where `=` means equals.

### Search by meaning comes free

`codes_text` carries meanings, so typing `accident` finds 10-50 calls whose
transcript never contains the word. No extra query path.

### Stats endpoint

```
GET /api/codes/stats?since=&until=&tgid=&cat=&min_confidence=high
-> [{ canonical: "10-50", meaning: "Accident", kind: "ten",
      calls: 23, mentions: 27 }, ...]
```

A `GROUP BY canonical` over `call_codes` joined to `calls` for the time window. No
transcript text touched, so it stays cheap enough to poll. `min_confidence`
defaults to `high`, excluding `10NN`-split mentions unless deliberately requested.

---

## Section 5 — UI

### Rendering without `v-html`

The API ships `transcriptNorm` plus a `codes` array carrying offsets. The
component turns that into a segment list and renders it with `v-for` — no
`v-html`, no injection surface, no regex re-run in the browser:

```ts
// [{ text: "Show me " }, { text: "10-42", code: {...} }, { text: " at Sherwood" }]
function segments(text: string, codes: CodeMention[]): Segment[]
```

Payload cost is negligible: ~250 mentions across 3,740 rows.

### Four surfaces

1. **Table transcript cell** (`RecordingsList.vue:103`) — renders `transcriptNorm`
   in segments. Resolved codes get a dotted underline plus a native `title`
   tooltip. Codes flagged `common: true` render as plain text.

   The cell is already `max-height: 3.9em; overflow-y: auto` (`:565`), so it
   scrolls internally and row height is fixed regardless of content. Inline
   markup is therefore safe, provided it does not change the line box: dotted
   underline yes, padded chips no. The virtualiser's constant `itemSize: 62`
   (`:88`) is unaffected.

2. **Detail dialog** (`:191`) — same segments, plus a **Codes** block listing each
   mention with meaning, resolving set, and confidence. Unresolved mentions read
   `10-84 — no definition in EBRSO (Law)`. That honest fallback doubles as the
   sourcing to-do list.

3. **Code filter** — a `MultiSelect` beside the existing enc filter, populated
   from `/api/codes/stats` so it only offers codes actually present. Selecting one
   adds `?code=`.

4. **Search placeholder** (`:61`) updated to say meanings are searchable.

**Not building** a separate analytics page. The stats endpoint plus the filter's
counts cover "which codes are busiest" without a new route to maintain.

---

## Section 6 — Testing

### The corpus is the fixture

All 250 real mentions are extracted into
`scripts/tests/fixtures/tencode_cases.tsv` with expected results — a golden-file
suite grounded in actual radio traffic rather than invented strings.

### Negative cases (verbatim from the corpus; each must yield zero codes)

`room 1003` · `mileage 46215` · `40-year-old male` · `6627 Sullivan Road` ·
`39, Kim Larkin, 39` · `Transport 1010 15` · `ten more` · `ten point` ·
`we down 28.5%` · `B862623`

### Positive cases

`1042` -> `10-42` (medium) · `ten four` -> `10-4` (high) ·
`10-4-1-4-31` -> exactly one `10-4` · `10-84` -> recorded, unresolved, no meaning

### Data-integrity tests

- every code entry has a `src` resolving to a real entry in its file's `sources`;
- every `index.json` rule names a set that exists;
- `extends` chains terminate and contain no cycles.

### Structural tests

- backfill run twice produces identical `call_codes` (idempotence);
- `connect()` called repeatedly leaves the schema unchanged (migration idempotence);
- the new `code` filter returns expected rows, and existing search results are
  unchanged (regression, in `server/utils/queries.test.ts`).

Python tests go in `scripts/tests/` (pytest, matching `test_sdr_db.py`);
TypeScript tests use the existing vitest setup.

---

## Build Order

Data before extractor, because membership is the disambiguator.

| Phase | Deliverable |
|---|---|
| 1 | Build `la-generic-law` from citable LSP/Louisiana lists, cross-checked against corpus occurrences; `la-generic-fire`/`-ems` response codes; agency sets created empty with `extends`. Data-integrity tests pass. |
| 2 | `scripts/tencodes.py` + golden and negative tests. Pure function, no DB. |
| 3 | Migration, `call_codes`, FTS rebuild, `set_transcript` hook, `backfill_codes.py`. |
| 4 | `code` filter in `listRecordings()` + `/api/codes/stats`. |
| 5 | UI: segments, tooltips, detail Codes block, code filter. |
| 6 | Backfill the 3,740 existing calls; report resolved/unresolved per agency. |

Phase 6's report is the honest measure of success, and its unresolved list feeds
straight back into phase 1 for the parishes without published code sets. Because
extraction is fully re-derivable, that report is a work queue rather than a defect
list: source one more agency's codes, run `backfill_codes.py --only-stale`, and
history retroactively improves.

That property is exactly what would be lost by normalizing `transcript` in place —
an original mangling would become indistinguishable from a previous pass's
correction, and the corpus would rot with every code-table fix.

---

## Risks

| Risk | Mitigation |
|---|---|
| Agency-specific lists (BRPD, EBRSO) are unpublished, and the one list circulating online is reported incorrect | Agency sets ship empty and `extends` the generic set; unknown codes render un-expanded, never guessed. Phase 6's report drives verified additions |
| A sourced meaning is later corrected | `codes_rev` makes every affected row stale, so `--only-stale` re-derives it; `src` provenance makes each expansion auditable |
| `10NN` split produces false positives | Membership test + address/room stop-words + `medium` confidence, excluded from stats by default |
| FTS rebuild during live capture | One-off, 3,740 rows; WAL means readers are not blocked; run between sessions |
| TS reads columns before migration runs | `getDb()` column check throws the existing loud "run this command" error |

