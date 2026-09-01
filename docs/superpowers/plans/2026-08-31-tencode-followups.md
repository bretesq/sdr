# 10-Code Annotation — Follow-Ups

Findings from the final whole-branch review of `feat/tencode-annotation` that were
judged **non-blocking** and deliberately deferred. Recorded here because the
review's scratch workspace is transient and this is the durable record.

Branch state at capture: 3,938 transcribed calls, 232 code mentions, 209 carrying
a meaning (90.1%).

## Worth fixing — ranked

### F1. Spelled-out codes are unsearchable by their own text
`scripts/tencodes.py` — `_spelled_to_digits()` runs *before* the candidate scan, so
by the time a `Mention` is built for a spelled form, `raw` already equals
`canonical`. `codes_text()` skips `raw` when it matches `canonical`, so the surface
text whisper actually produced — `"Ten-four"`, `"ten forty-two"` — ends up in
**neither** FTS column. About 25 calls cannot be found by searching the words they
literally contain.

Concatenated forms (`"1042"`) do not have this problem, and that asymmetry is what
makes it a bug rather than a design choice.

**Cost of fixing:** the fix changes extractor output, so it needs an
`EXTRACTOR_VERSION` bump and a full `backfill_codes.py` run. That is why it was
kept out of the pre-merge fix wave rather than because it is unimportant.

### F3. `MAX(cc.meaning)` collapses per-agency meanings
`server/utils/queries.ts` — `codeStats()` groups by `(canonical, kind)` and takes
`MAX(meaning)`. Today every canonical resolves to one meaning corpus-wide, so this
is invisible. It becomes a **wrong label** the moment two agencies define the same
code differently — which is exactly what populating the empty agency sets will do.
This is on the roadmap, so treat F3 as scheduled, not hypothetical.

Related: the UI code filter keys on `canonical` alone while `codeStats()` groups by
`(canonical, kind)`. If one canonical ever spans two kinds, the option-count /
row-count match established by commit `1e02e56` silently breaks. Pin both with one
test.

### F4. Spelled forms skip the address guard
`scripts/tencodes.py` — spelled forms take the separated-form branch, so they get
`high` confidence and **no** address-word guard, while concatenated forms get
`medium` plus the guard. A spoken time like `"ten fifteen"` therefore expands
confidently to `10-15` and counts in default statistics.

### F2. Raw-transcript fallbacks can misalign annotation
`components/RecordingsList.vue` — three fallback paths hand raw `transcript` to
`segments()` while still passing offsets that are only valid against
`transcript_norm`. The existing guard catches offsets that overrun the string but
not offsets that are merely *shifted*. Zero rows reach this today (every call with
a transcript also has `transcript_norm`, written atomically by `set_transcript`).

Fix alongside it: `segments()` has no guard for inverted offsets
(`offEnd < offStart`), which could move its cursor backward and duplicate a
character. Zero such rows exist.

## Known and accepted

- **F5** — `backfill_codes.py` runs one write transaction over the whole corpus, and
  `SCHEMA`'s `INSERT OR IGNORE INTO algorithms` means every `connect()` needs a write
  lock, so a concurrent `stt_watch` merge can fail after `busy_timeout`. Self-healing
  (the `.txt` files are durable and startup re-indexes), and the documented procedure
  stops the transcriber first.
- `tencode_sets.resolve()` returns entries aliasing an `lru_cache`; all consumers are
  read-only. In-place mutation of a returned entry would corrupt the cache
  process-wide.
- `tgid` is parsed two ways (`sdr_db.tgid_from_filename` vs `import_to_sqlite`'s own
  regex). Zero mismatches across 3,951 calls.
- `list.get.ts` drops a `tgid=0` via a truthiness check. Talkgroup 0 does not exist
  on this system.
- `loadCodeStats()` runs only on mount, so dropdown counts drift from row counts over
  a long live session as SSE delivers new mentions.
- `backfill_codes.py --report`'s per-agency table truncates long category names at a
  fixed column width.

## The actual next piece of work

`results/tencode_report.txt` is the sourcing worklist, and it points somewhere
specific: **`la-generic-ems` is the weak set** — 16 unresolved mentions against 35
resolved, a 40% unresolved rate where every other agency is near zero. Unresolved
there: `10-0`, `10-6`, `10-11`, `10-15`, `10-16`, `10-3`, `10-56`, `10-77`, `10-91`.

Secondary: `la-brpd-law` has `10-0`, `10-84`, `10-89` unresolved despite resolving
30 of 33 overall.

Source a set, add it to the agency's JSON with a real `src`, then run
`python3 scripts/backfill_codes.py --only-stale` — `codes_rev` makes the affected
rows detectably stale and history improves retroactively.
