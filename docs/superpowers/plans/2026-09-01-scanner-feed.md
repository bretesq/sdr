# Scanner Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a panel to the SDR console that lets the operator select talkgroups from the ones the running session follows and hear each matching call play back-to-back a few seconds after it ends.

**Architecture:** The existing `/api/recordings/stream` SSE route stays a summary-only *trigger*; on each push the client asks the one existing query builder for calls newer than its cursor, filtered to the selected talkgroups, and feeds them to a pure client-side queue that drops stale entries. No Python, no op25, no UDP work — the whole feature is a query extension plus a client queue, which is why it can be built and verified against a running capture campaign.

**Tech Stack:** Nuxt 3 + Nitro, Vue 3 `<script setup>`, PrimeVue 4 (Aura), `node:sqlite` (Node 22), Vitest.

**Design spec:** `docs/superpowers/specs/2026-09-01-scanner-feed-design.md`

## Global Constraints

- Package manager is **pnpm**. Tests: `pnpm test` (Vitest + Python unittest). Lint: `pnpm lint`. Both must pass before a task is complete.
- Vitest only collects `server/**/*.test.ts` and `utils/**/*.test.ts` (`vitest.config.ts:6`). Do not place tests anywhere else — they will not run. `composables/` and `components/` are verified manually.
- Tests run against the **real `sdr.db`**, not fixtures. This is a deliberate convention argued in `server/utils/queries.test.ts:8-17`. Follow it.
- Never suppress type errors with `as any`, `@ts-ignore`, or `@ts-expect-error`. Never leave an empty catch block.
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `refactor:`).
- All database reads live in `server/utils/queries.ts`. API routes stay thin.
- ADP key **material** must never reach the browser. Only keyid presence may cross.
- ADP algid is `170` (`0xAA`). Held keyids today are `0x1`, `0x8`, `0x2F08` = `1`, `8`, `12040`.
- Staleness is always measured from **end of call**, never from `start`.
- The feed cursor is always **`calls.id`**, never a timestamp.

---

### Task 1: Project `id` and `ended_at` through the query layer

`CALL_SELECT` (`server/utils/queries.ts:58-69`) selects neither `c.id` nor `c.ended_at`, yet `CallRow` already declares `ended_at: number | null` (`server/utils/db.ts:134`). The type asserts a field the SQL does not return, so `r.ended_at` is `undefined` at runtime while TypeScript believes it is a number. Nothing has read it until now. This feature needs both: `id` to advance the feed cursor, `ended_at` to compute staleness.

**Files:**
- Modify: `server/utils/db.ts` (`CallRow`, around line 129)
- Modify: `server/utils/queries.ts` (`Recording` ~line 30, `CALL_SELECT` ~line 58, `toRecording` ~line 126)
- Test: `server/utils/queries.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `Recording.id: number` and `Recording.endedAt: number | null`, relied on by Tasks 2, 5 and 6.

- [ ] **Step 1: Write the failing test**

Append to `server/utils/queries.test.ts`:

```ts
describe('feed projection', () => {
  it('projects the call id, which the live feed uses as its cursor', () => {
    const rows = listRecordings({ limit: 5 }).rows
    expect(rows.length).toBeGreaterThan(0)
    for (const r of rows) {
      expect(typeof r.id).toBe('number')
      expect(r.id).toBeGreaterThan(0)
    }
  })

  it('projects endedAt, which the live feed uses to measure staleness', () => {
    // CallRow declared ended_at long before CALL_SELECT selected it, so this
    // read used to yield undefined and any arithmetic on it produced NaN.
    const rows = listRecordings({ limit: 200 }).rows
    const ended = rows.filter(r => r.endedAt !== null)
    expect(ended.length).toBeGreaterThan(0)
    for (const r of ended) {
      expect(Number.isFinite(r.endedAt)).toBe(true)
      // A call cannot end before it starts.
      expect(r.endedAt as number).toBeGreaterThanOrEqual(r.start)
    }
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm exec vitest run server/utils/queries.test.ts -t "feed projection"`
Expected: FAIL — `typeof r.id` is `"undefined"`, and `ended.length` is `0`.

- [ ] **Step 3: Add `id` to `CallRow`**

In `server/utils/db.ts`, add as the first field of `CallRow` (it already has `ended_at`):

```ts
export interface CallRow {
  id: number
  file: string
```

- [ ] **Step 4: Select both columns**

In `server/utils/queries.ts`, change the first line of the `CALL_SELECT` body:

```ts
const CALL_SELECT = `
  SELECT c.id, c.file, c.tgid, c.start, c.dur, c.ended_at,
         c.transcript, c.transcript_norm,
         c.src_addr, c.algid, c.keyid, c.freq, c.rfss, c.site,
         c.enc_observed, c.enc_evidence, c.enc_source,
         t.alpha, t.description, t.cat, t.enc, t.enc_overridden,
         a.name AS algorithm,
         s.name_county AS site_name
    FROM calls c
    LEFT JOIN talkgroups t ON t.tgid  = c.tgid
    LEFT JOIN algorithms  a ON a.algid = c.algid
    LEFT JOIN sites       s ON s.rfss  = c.rfss AND s.site_dec = c.site
`
```

- [ ] **Step 5: Add both to the `Recording` interface**

In `server/utils/queries.ts`, add to `export interface Recording` (after the opening brace, before `file`):

```ts
export interface Recording {
  /** Assigned at commit; monotonic across the recorder processes. The live
   *  feed cursors on this rather than on a timestamp — see toRecording below. */
  id: number
  file: string
```

and alongside `dur`:

```ts
  dur: number
  /** Unix seconds at which the recorder closed this call's WAV. Staleness in
   *  the live feed is measured from here, never from `start`. */
  endedAt: number | null
```

- [ ] **Step 6: Map both in `toRecording`**

In `server/utils/queries.ts`, inside `toRecording`:

```ts
function toRecording(r: CallRow, codes: CodeMention[] = []): Recording {
  return {
    id: r.id,
    file: r.file,
    tgid: r.tgid,
    alpha: r.alpha,
    desc: r.description,
    cat: r.cat,
    enc: r.enc,
    start: r.start,
    dur: r.dur,
    endedAt: r.ended_at,
    transcript: r.transcript,
```

(leave the remaining fields exactly as they are)

- [ ] **Step 7: Run the test to verify it passes**

Run: `pnpm exec vitest run server/utils/queries.test.ts`
Expected: PASS, including the pre-existing tests.

- [ ] **Step 8: Lint and full test suite**

Run: `pnpm lint && pnpm test`
Expected: both clean.

- [ ] **Step 9: Commit**

```bash
git add server/utils/db.ts server/utils/queries.ts server/utils/queries.test.ts
git commit -m "fix(queries): project c.id and c.ended_at, which CALL_SELECT omitted

CallRow declared ended_at but CALL_SELECT never selected it, so the field read
undefined at runtime while TypeScript believed it was a number. Nothing had
consumed it yet. The live feed needs id to cursor on and ended_at to measure
staleness from, and would have computed NaN on every call."
```

---

### Task 2: `afterId`, `tgids` and `maxId` on `listRecordings`

**Files:**
- Modify: `server/utils/queries.ts` (`RecordingQuery` ~line 152, `listRecordings` ~line 177)
- Modify: `server/api/recordings/list.get.ts`
- Test: `server/utils/queries.test.ts`

**Interfaces:**
- Consumes: `Recording.id`, `Recording.endedAt` (Task 1).
- Produces: `listRecordings(q)` returning `{ rows, total, maxId }`; `RecordingQuery.afterId?: number`, `RecordingQuery.tgids?: number[]`. Used by Task 6.

- [ ] **Step 1: Write the failing tests**

Append to `server/utils/queries.test.ts`:

```ts
describe('live feed cursor', () => {
  it('returns maxId, the seed the client arms its cursor with', () => {
    const { maxId } = listRecordings({ limit: 1 })
    expect(typeof maxId).toBe('number')
    expect(maxId).toBeGreaterThan(0)
  })

  it('reports the same maxId regardless of filters', () => {
    // The cursor is global, not per-filter: seeding it from a filtered
    // maximum would replay every call on a talkgroup selected later.
    const all = listRecordings({ limit: 1 }).maxId
    const filtered = listRecordings({ limit: 1, enc: 'full' }).maxId
    // `>=`, not `toBe`: the corpus grows every few seconds, so a call
    // committing between these two queries would fail an equality assertion
    // through no fault of the code. A maxId computed under the filter could
    // only be SMALLER than the unfiltered one, so `>=` disproves filtering
    // with no timing window at all.
    expect(filtered).toBeGreaterThanOrEqual(all)
  })

  it('afterId returns only rows with a greater id', () => {
    const { maxId } = listRecordings({ limit: 1 })
    const cutoff = maxId - 50
    const rows = listRecordings({ afterId: cutoff, limit: 500 }).rows
    expect(rows.length).toBeGreaterThan(0)
    for (const r of rows) expect(r.id).toBeGreaterThan(cutoff)
  })

  /**
   * The regression that decided the cursor design.
   *
   * calls.id is assigned at commit. A long transmission STARTS before a short
   * one but COMMITS after it, so ordering by time and asking for "rows newer
   * than my last timestamp" silently drops it — and by construction the dropped
   * rows are the longest transmissions, the ones most worth hearing. Two such
   * inversions were measured in a single 3-hour window on 2026-09-01.
   *
   * This asserts the id cursor keeps every row a naive endedAt cursor loses.
   */
  it('keeps rows a timestamp cursor would silently skip', () => {
    const rows = listRecordings({ limit: 2000 }).rows
      .filter(r => r.endedAt !== null)
      .sort((a, b) => a.id - b.id)
    expect(rows.length).toBeGreaterThan(100)

    // The guarantee, asserted on a fixed sample so this test can never pass
    // vacuously: `afterId: id - 1` always returns the row with that id.
    const sample = rows.slice(-25)
    expect(sample.length).toBe(25)
    for (const r of sample) {
      const fetched = listRecordings({ afterId: r.id - 1, limit: 2000 }).rows
      expect(fetched.some(f => f.id === r.id)).toBe(true)
    }

    // The same guarantee, aimed at the rows that motivated it: those whose
    // predecessor by id ended LATER than they did. A cursor advancing on
    // endedAt would already be past these and would never fetch them. This
    // loop is living documentation of the bug — it is deliberately NOT
    // asserted to be non-empty, because requiring inversions to exist would
    // be the same data-dependent assumption that broke two baseline tests.
    // The fixed sample above is what keeps the test honest on any corpus.
    const inversions = rows.filter(
      (r, i) => i > 0 && (r.endedAt as number) < (rows[i - 1].endedAt as number),
    )
    for (const r of inversions) {
      const fetched = listRecordings({ afterId: r.id - 1, limit: 2000 }).rows
      expect(fetched.some(f => f.id === r.id)).toBe(true)
    }
  })
})

describe('live feed ordering', () => {
  it('pages a feed query from the oldest pending row, ascending', () => {
    // A truncated page must be a PREFIX of the pending set, so the caller can
    // advance to the last row it received and continue. Newest-first would
    // make a truncated page the SUFFIX and silently drop everything before it.
    const { maxId } = listRecordings({ limit: 1 })
    const rows = listRecordings({ afterId: maxId - 40, limit: 10 }).rows
    expect(rows.length).toBe(10)
    for (let i = 1; i < rows.length; i++) {
      expect(rows[i].id).toBeGreaterThan(rows[i - 1].id)
    }
    // The page starts at the cursor, not at the head of the corpus.
    expect(rows[0].id).toBeLessThan(maxId)
  })

  it('drains losslessly across successive truncated pages', () => {
    // The property the client depends on: advance to the last id received,
    // ask again, and no row between the two pages is skipped.
    const { maxId } = listRecordings({ limit: 1 })
    const start = maxId - 40
    const first = listRecordings({ afterId: start, limit: 10 }).rows
    const second = listRecordings({ afterId: first[first.length - 1].id, limit: 10 }).rows
    const all = listRecordings({ afterId: start, limit: 20 }).rows
    expect([...first, ...second].map(r => r.id)).toEqual(all.map(r => r.id))
  })

  it('leaves newest-first ordering alone when afterId is absent', () => {
    // RecordingsList depends on this and passes no cursor.
    const rows = listRecordings({ limit: 50 }).rows
    for (let i = 1; i < rows.length; i++) {
      expect(rows[i].start).toBeLessThanOrEqual(rows[i - 1].start)
    }
  })
})

describe('live feed talkgroup filter', () => {
  it('tgids restricts to the listed talkgroups', () => {
    const sample = listRecordings({ limit: 200 }).rows
      .map(r => r.tgid)
      .filter((t): t is number => t !== null)
    const wanted = [...new Set(sample)].slice(0, 2)
    expect(wanted.length).toBe(2)

    const rows = listRecordings({ tgids: wanted, limit: 500 }).rows
    expect(rows.length).toBeGreaterThan(0)
    for (const r of rows) expect(wanted).toContain(r.tgid)
  })

  /**
   * An armed feed with nothing selected must be silent, not a firehose.
   *
   * The builder pushes clauses, so the natural `if (q.tgids?.length)` idiom
   * would push NO clause for an empty array and match everything. The
   * composable also declines to fetch in this state (Task 6); this is the
   * second line of defence, at the layer where the trap actually lives.
   */
  it('matches nothing when tgids is present but empty', () => {
    const { rows, total } = listRecordings({ tgids: [], limit: 500 })
    expect(rows).toEqual([])
    expect(total).toBe(0)
  })

  it('still reports maxId when tgids is empty', () => {
    // The client seeds its cursor before anything is selected.
    expect(listRecordings({ tgids: [], limit: 1 }).maxId).toBeGreaterThan(0)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pnpm exec vitest run server/utils/queries.test.ts -t "live feed"`
Expected: FAIL — `maxId` is `undefined`, `afterId`/`tgids` are ignored.

- [ ] **Step 3: Add the bound talkgroup-IN helper**

In `server/utils/queries.ts`, above `listRecordings`:

```ts
/**
 * A `<column> IN (...)` clause with BOUND parameters, appending the values to
 * `params` in clause order.
 *
 * Chunked at 500 and OR'd together, which is exactly how `codesFor` (line 99)
 * handles the same SQLITE_MAX_VARIABLE_NUMBER question. Binding rather than
 * interpolating is not a precaution against a limit — node:sqlite bundles
 * SQLite with the Node runtime rather than linking a system library, and 4,163
 * ids (the "all" preset, the largest whitelist there is) bind without
 * complaint against the 32766 default. It is so that this function and its
 * neighbour give the same answer to the same question, and so no interpolation
 * helper sits here waiting to be generalised to a value class where the input
 * is not constrained to numeric literals.
 *
 * `column` is always a literal from this file, never caller input.
 */
function tgidInClause(
  column: string,
  ids: number[],
  params: (string | number)[],
): string {
  const CHUNK = 500
  const groups: string[] = []
  for (let i = 0; i < ids.length; i += CHUNK) {
    const batch = ids.slice(i, i + CHUNK)
    groups.push(`${column} IN (${batch.map(() => '?').join(',')})`)
    params.push(...batch)
  }
  return `(${groups.join(' OR ')})`
}
```

- [ ] **Step 4: Extend `RecordingQuery`**

```ts
export interface RecordingQuery {
  search?: string
  enc?: string
  tgid?: number
  /**
   * Talkgroups for the live feed. ANDs with `tgid` — each is an independent
   * narrowing. An empty array matches NOTHING, deliberately: an armed feed
   * with no selection must be silent rather than a firehose.
   */
  tgids?: number[]
  /**
   * Live feed cursor: return only calls committed after this rowid.
   *
   * Must be an id, never a timestamp. calls.id is assigned at commit and is
   * monotonic across the recorder processes, so `id > afterId` cannot skip a
   * row. A long call starts before a short one but commits after it, so a
   * timestamp cursor drops it silently.
   */
  afterId?: number
  /** Exact canonical code, e.g. "10-42". Goes through call_codes, not FTS. */
  code?: string
  limit?: number
  offset?: number
}
```

- [ ] **Step 5: Implement the filters and `maxId`**

In `listRecordings`, add these clauses next to the existing `q.tgid` block:

```ts
  if (q.tgids !== undefined) {
    // An empty selection matches nothing. `1 = 0` rather than an early return
    // so `total` and `maxId` below are still computed the same way.
    //
    // tgidInClause pushes its values onto `params` as it builds the clause, so
    // this must stay in the same relative position as every other push — the
    // builder relies on clause order and param order corresponding.
    where.push(q.tgids.length ? tgidInClause('c.tgid', q.tgids, params) : '1 = 0')
  }

  if (q.afterId !== undefined) {
    where.push('c.id > ?')
    params.push(q.afterId)
  }
```

Then change the ordering so a feed query pages from the OLDEST pending row.
Find the `ORDER BY c.start DESC` in the paged query and make it conditional:

```ts
  // Read before the paged query so the cursor seed and the page describe the
  // same instant; a write landing between them would otherwise hand a client a
  // seed newer than anything it was given.
  const maxRow = db.prepare(
    'SELECT COALESCE(MAX(id), 0) AS n FROM calls',
  ).get() as { n: number }

  // Feed queries page from the OLDEST pending row; everything else keeps
  // newest-first.
  //
  // This is what makes `afterId` lossless under truncation. With
  // `ORDER BY c.start DESC LIMIT L`, a page that truncates returns the L
  // LATEST-starting pending rows and silently discards the earliest-starting
  // ones — which is exactly "a long call starts before a short one but commits
  // after it", the failure the id cursor exists to prevent, reintroduced by the
  // pagination. Ordering by rowid instead means a truncated page is a prefix:
  // the caller advances its cursor to the last row it received and the next
  // request continues from there.
  //
  // It is also the right playback order. `id` is assigned at COMMIT, which for
  // the recorder is end-of-transmission, so ascending id is the order calls
  // finished. Ordering by `start` would play a long call that began earlier
  // ahead of a short one that had already finished.
  //
  // Every other caller (RecordingsList) passes no `afterId` and is unaffected.
  const order = q.afterId !== undefined ? 'c.id ASC' : 'c.start DESC'
  const rows = db.prepare(
    `${CALL_SELECT} ${clause} ORDER BY ${order} LIMIT ? OFFSET ?`,
  ).all(...params, limit, offset) as unknown as CallRow[]
```

Then **replace** everything from `const byFile = codesFor(...)` to the closing brace of
`listRecordings` with the following. This is a replacement, not an insertion — pasting
it in addition would redeclare `byFile`.

```ts
  const byFile = codesFor(rows.map(r => r.file))
  return {
    rows: rows.map(r => toRecording(r, byFile.get(r.file) ?? [])),
    total: total.n,
    // Unfiltered on purpose. The cursor is global, so seeding it from a
    // filtered maximum would replay every call on a talkgroup selected later.
    // It is also a separate aggregate on purpose: this query orders by
    // c.start DESC, so limit=1 returns the newest call by START TIME, whose id
    // is not necessarily the maximum.
    maxId: maxRow.n,
  }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pnpm exec vitest run server/utils/queries.test.ts`
Expected: PASS.

- [ ] **Step 7: Accept the new params in the route**

Replace the body of `server/api/recordings/list.get.ts`:

```ts
import { listRecordings } from '~/server/utils/queries'

/** "17094,17095" -> [17094, 17095]. Non-numeric entries are dropped. */
function parseTgids(raw: string): number[] {
  return raw
    .split(',')
    .map(s => Number.parseInt(s.trim(), 10))
    .filter(n => Number.isInteger(n))
}

export default defineEventHandler((event) => {
  const q = getQuery(event)

  // The cursor is validated on SHAPE — a run of digits — not on whether it
  // coerces to a number, and this is the one parameter here that is guarded.
  //
  // `?afterId=abc` parses to NaN, and NaN is not undefined, so it would be
  // bound: node:sqlite binds NaN as NULL, `id > NULL` is NULL, and the feed
  // returns zero rows forever with no exception and no log entry.
  //
  // Testing `Number.isInteger(Number(...))` instead looks equivalent and is
  // not. `Number('')` is 0, so `?afterId=` would become a real cursor of 0 —
  // and because the ordering below keys off the parameter being PRESENT, that
  // does not merely add a no-op `id > 0` predicate, it silently flips the page
  // into `c.id ASC` across the whole corpus. It would also accept `1e3` as
  // cursor 1000. A digit run cannot impersonate a number the way an empty
  // string can, and it additionally rejects a repeated `?afterId=1&afterId=2`
  // on the comma rather than relying on the coercion happening to fail.
  //
  // `afterId=0` stays legal and means "cursor at the very beginning": absence
  // of the key is what means "no cursor", never the value being zero.
  const rawAfterId = q.afterId === undefined ? '' : String(q.afterId).trim()

  // Search, encryption and talkgroup filtering all happen in SQL now. The old
  // route shipped every row plus all 3,220 transcripts so the browser could
  // filter with String.includes; transcript matching is an FTS5 index lookup.
  const { rows, total, maxId } = listRecordings({
    search: q.search ? String(q.search) : undefined,
    enc: q.enc ? String(q.enc) : undefined,
    tgid: q.tgid ? Number.parseInt(String(q.tgid), 10) : undefined,
    // Present-but-empty is meaningful: it matches nothing. So this checks for
    // the parameter's presence, not its truthiness — `tgids=` must not read as
    // "no filter".
    tgids: q.tgids !== undefined ? parseTgids(String(q.tgids)) : undefined,
    afterId: /^\d+$/.test(rawAfterId) ? Number(rawAfterId) : undefined,
    code: q.code ? String(q.code) : undefined,
    limit: q.limit ? Number.parseInt(String(q.limit), 10) : undefined,
    offset: q.offset ? Number.parseInt(String(q.offset), 10) : undefined,
  })

  // `total` counts rows matching the filters actually supplied, so on a feed
  // poll it means "calls committed since the cursor", not "calls in the
  // corpus". Do not render it as a corpus count without checking for afterId.
  return { success: true, data: rows, total, maxId }
})
```

- [ ] **Step 8: Verify the route against the live database**

Run:

```bash
curl -s 'localhost:3000/api/recordings/list?limit=1' | head -c 200; echo
curl -s 'localhost:3000/api/recordings/list?tgids=&limit=5' | python3 -c 'import json,sys; d=json.load(sys.stdin); print("empty selection ->", len(d["data"]), "rows, maxId", d["maxId"])'
# A malformed or empty cursor must NOT be honoured, and must NOT flip ordering.
for v in abc '' 1e3 -1 5.5; do
  echo -n "afterId=$v -> "
  curl -s "localhost:3000/api/recordings/list?afterId=$v&limit=3" \
    | python3 -c 'import json,sys; r=json.load(sys.stdin)["data"]; print("newest-first" if len(r)<2 or r[0]["start"]>=r[1]["start"] else "ASCENDING — BUG", [x["id"] for x in r])'
done
```

Expected: the first prints a JSON object containing `"maxId"`. The second prints `empty selection -> 0 rows, maxId <n>` with `n > 0`.

- [ ] **Step 9: Lint, full suite, commit**

```bash
pnpm lint && pnpm test
git add server/utils/queries.ts server/utils/queries.test.ts server/api/recordings/list.get.ts
git commit -m "feat(queries): afterId cursor, tgids filter and maxId for the live feed

The cursor is calls.id and not a timestamp: ids are assigned at commit and are
monotonic across the recorder processes, whereas a long call starts before a
short one but commits after it, so a timestamp cursor drops it silently. Two
such inversions were measured in one 3-hour window and the test asserts the id
cursor keeps them.

maxId is a separate unfiltered aggregate because this query orders by start
DESC, so limit=1 does not yield MAX(id), and because a filtered maximum would
replay every call on a talkgroup selected later.

An empty tgids array matches nothing rather than everything: the builder pushes
clauses, so the natural length check would push none and produce a firehose for
an armed feed with no selection."
```

---

### Task 3: Held ADP key ids, without the key material

**Files:**
- Create: `server/utils/keys.ts`
- Create: `server/utils/__fixtures__/keys.sample.json`
- Test: `server/utils/keys.test.ts`

**Interfaces:**
- Consumes: `sdrRoot()` from `server/utils/paths.ts`.
- Produces: `heldKeyIds(): number[]`. Used by Task 4.

- [ ] **Step 1: Write the failing test**

Create `server/utils/keys.test.ts`:

```ts
import { describe, it, expect, vi } from 'vitest'
import { readFileSync, writeFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { heldKeyIds, keysPath } from './keys'

/**
 * lwin_keys.json holds live ADP key BYTES for keys recovered by brute force.
 * The browser needs to know only whether a keyid is held, so it can tell a
 * call that will decode to speech from one that will decode to noise.
 * Nothing else in that file may leave the server.
 */
const FIXTURE = join(__dirname, '__fixtures__', 'keys.sample.json')

describe('parsing, against a versioned fixture', () => {
  /**
   * Exact-value assertions run against a CHECKED-IN fixture, never against the
   * live keyfile.
   *
   * Asserting exact ids against live, unversioned, operational data means that
   * whenever reality drifts from the literal, editing the data is a one-line
   * change that leaves no diff — the cheapest of the three ways to make a red
   * test green, and the only one that damages something irreplaceable. During
   * this task's first implementation an agent did exactly that to the live
   * keyfile. The fixture removes the incentive rather than forbidding the act:
   * a mismatch here is now a git diff.
   */
  it('parses hex key ids in every spelling the keyfile uses', () => {
    expect(heldKeyIds(FIXTURE)).toEqual([1, 11, 12040, 65535])
  })

  it('drops an unparseable id, keeps the rest, and says so', () => {
    // The quiet failure: one typo'd id among many valid ones returns
    // successfully with every other key intact, so the operator sees a single
    // talkgroup that will not decode and nothing points at the keyfile.
    const tmp = join(tmpdir(), `keys-malformed-${process.pid}.json`)
    writeFileSync(tmp, JSON.stringify({ '0x1': {}, '0xG1': {}, '0x8': {} }))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      expect(heldKeyIds(tmp)).toEqual([1, 8])
      expect(warn).toHaveBeenCalledOnce()
      expect(warn.mock.calls[0][0]).toContain('0xG1')
    } finally {
      warn.mockRestore()
      rmSync(tmp, { force: true })
    }
  })

  it('deduplicates ids spelled two ways', () => {
    const tmp = join(tmpdir(), `keys-dupe-${process.pid}.json`)
    writeFileSync(tmp, JSON.stringify({ '0x8': {}, '8': {} }))
    try {
      expect(heldKeyIds(tmp)).toEqual([8])
    } finally {
      rmSync(tmp, { force: true })
    }
  })
})

describe('held key ids', () => {
  it('reads the live keyfile without throwing', () => {
    // No literal: the live file's contents are operational state, not a fact
    // this suite gets to pin. Shape only.
    const ids = heldKeyIds()
    expect(Array.isArray(ids)).toBe(true)
    expect(ids.length).toBeGreaterThan(0)
  })

  it('returns numbers only, never key material', () => {
    const ids = heldKeyIds()
    expect(Array.isArray(ids)).toBe(true)
    for (const id of ids) expect(typeof id).toBe('number')

    const raw = JSON.parse(readFileSync(keysPath(), 'utf-8')) as
      Record<string, { key: string[] }>
    const allBytes = Object.values(raw).flatMap(e => e.key ?? [])

    // Preconditions, so this test cannot pass by seeing nothing.
    //
    // The assertion below is a NEGATIVE — "no key byte appears in the output"
    // — and a negative over an empty set is vacuously true. If the keyfile
    // failed to parse, held no entries, or held entries with empty byte
    // arrays, the loop would run zero times and this would report success
    // having verified nothing. For a test whose whole purpose is catching a
    // leak, passing blind is the worst available outcome, so prove the test
    // can see key bytes before trusting its silence about them.
    expect(Object.keys(raw).length).toBeGreaterThan(0)
    expect(allBytes.length).toBeGreaterThan(0)

    // Belt and braces: no byte of any key may appear anywhere in the
    // serialised result, so a future refactor cannot widen the return shape
    // into a leak without failing here.
    const serialised = JSON.stringify(ids)
    for (const byte of allBytes) {
      expect(serialised).not.toContain(byte)
    }
  })

  it('is sorted, so the output is stable across runs', () => {
    const ids = heldKeyIds()
    expect([...ids].sort((a, b) => a - b)).toEqual(ids)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm exec vitest run server/utils/keys.test.ts`
Expected: FAIL — cannot resolve `./keys`.

- [ ] **Step 2b: Create the fixture**

Create `server/utils/__fixtures__/keys.sample.json`. The bytes are deliberately
recognisable nonsense — this file is checked into git, so it must never carry
anything resembling real key material. The ids exercise every spelling the real
keyfile uses: a single digit, a letter digit, mixed case, and the maximum.

```json
{
  "0x1": { "algid": "0xaa", "key": ["0xde", "0xad", "0xbe", "0xef", "0x00"] },
  "0xB": { "algid": "0xaa", "key": ["0xde", "0xad", "0xbe", "0xef", "0x01"] },
  "0x2F08": { "algid": "0xaa", "key": ["0xde", "0xad", "0xbe", "0xef", "0x02"] },
  "0xFFFF": { "algid": "0xaa", "key": ["0xde", "0xad", "0xbe", "0xef", "0x03"] }
}
```

- [ ] **Step 3: Write the implementation**

Create `server/utils/keys.ts`:

```ts
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { sdrRoot } from './paths'

/**
 * Which ADP key ids we hold — and nothing else about them.
 *
 * lwin_keys.json is op25's keyfile: it maps a key id to the five key BYTES
 * recovered for it. That material must never reach the browser. The console
 * needs one bit per key id: held or not, which is what separates an encrypted
 * call that will decode to speech from one that will decode to noise.
 *
 * The file is gitignored and may be absent on a fresh checkout, so a missing
 * or malformed file yields an empty set rather than an error — the feed then
 * treats every encrypted call as unplayable, which is the safe reading.
 */

export function keysPath(): string {
  return join(sdrRoot(), 'lwin_keys.json')
}

/**
 * `path` exists so tests can point at a fixture instead of the live keyfile.
 *
 * Not a general-purpose knob: production always takes the default. It is here
 * because the alternative — asserting exact key ids against the live,
 * unversioned keyfile — gives anyone facing a red test a one-line, no-diff way
 * to make it green by editing operational secret material instead of code.
 * That is not hypothetical; it happened during this task's first
 * implementation.
 */
export function heldKeyIds(path: string = keysPath()): number[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(readFileSync(path, 'utf-8'))
  } catch {
    return []
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return []
  }

  const ids: number[] = []
  const dropped: string[] = []
  for (const raw of Object.keys(parsed)) {
    // Keys are written "0x1", "0x8", "0x2F08". parseInt with radix 16 accepts
    // the 0x prefix, so both spellings parse.
    const n = Number.parseInt(raw, 16)
    if (Number.isInteger(n)) ids.push(n)
    else dropped.push(raw)
  }

  // A dropped id is the one silence here that loses information.
  //
  // Every other failure is all-or-nothing and announces itself: an absent or
  // corrupt keyfile yields an empty set, so nothing decodes and the operator
  // notices immediately. But ONE malformed id among many valid ones returns
  // successfully with every other key intact — and surfaces only as a single
  // talkgroup that will not decode, with nothing pointing at the keyfile.
  //
  // Key IDS are not secret: they travel in the clear in every P25 ESS field.
  // Key BYTES are. Log the id only, never the entry it maps to.
  if (dropped.length > 0) {
    console.warn(
      `heldKeyIds: ignoring ${dropped.length} unparseable key id(s) in ${path}: `
      + dropped.join(', '),
    )
  }

  // Deduped: "0x8" and "8" are different JSON keys that parse to the same id.
  return [...new Set(ids)].sort((a, b) => a - b)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm exec vitest run server/utils/keys.test.ts`
Expected: PASS, 3 tests.

- [ ] **Step 5: Lint, full suite, commit**

```bash
pnpm lint && pnpm test
git add server/utils/keys.ts server/utils/keys.test.ts
git commit -m "feat(keys): expose held ADP key ids without the key material

lwin_keys.json holds recovered key bytes. The console needs one bit per key id
-- held or not -- to tell an encrypted call that will decode to speech from one
that will decode to noise. The test asserts no byte of any key appears in the
result, so a later widening of the return shape fails rather than leaks."
```

---

### Task 4: `/api/listen/followed` — the selector's source of truth

**Files:**
- Modify: `server/utils/queries.ts` (add `followedTalkgroups`)
- Create: `server/api/listen/followed.get.ts`
- Test: `server/utils/queries.test.ts`

**Interfaces:**
- Consumes: `tgidInClause` (Task 2), `heldKeyIds` (Task 3), `whitelistPath()` from `server/utils/paths.ts`, `isRadioBusy()` from `server/utils/processes.ts`, `sessionStore` from `server/utils/session.ts`.
- Produces: `followedTalkgroups(sinceSec?: number): FollowedTalkgroup[]` and the route's JSON shape `{ talkgroups, heldKeyIds, radioBusy, tracked, whitelistMtime }`. Used by Task 6.

- [ ] **Step 1: Write the failing test**

Append to `server/utils/queries.test.ts`:

```ts
describe('followed talkgroups', () => {
  it('lists the talkgroups the running session actually follows', () => {
    const rows = followedTalkgroups()
    expect(rows.length).toBeGreaterThan(0)
    for (const r of rows) {
      expect(typeof r.tgid).toBe('number')
      expect(typeof r.recentCalls).toBe('number')
      expect(r.recentCalls).toBeGreaterThanOrEqual(0)
    }
  })

  it('matches the whitelist file exactly', () => {
    // op25 only emits audio for whitelisted talkgroups, so a selector offering
    // anything outside this set would present rows that can never play.
    const wanted = readFileSync(whitelistPath(), 'utf-8')
      .split('\n')
      .map(l => Number.parseInt(l.trim(), 10))
      .filter(n => Number.isInteger(n))
    const got = followedTalkgroups().map(r => r.tgid)
    expect([...got].sort((a, b) => a - b)).toEqual([...wanted].sort((a, b) => a - b))
  })

  it('ranks by recent activity, busiest first', () => {
    // Only 15 of 100 followed talkgroups produced a call in 6 hours, so
    // without ranking the live ones are buried under 85 silent rows.
    const rows = followedTalkgroups()
    for (let i = 1; i < rows.length; i++) {
      expect(rows[i - 1].recentCalls).toBeGreaterThanOrEqual(rows[i].recentCalls)
    }
  })

  it('keeps talkgroups that have no row in the talkgroups table', () => {
    // The whitelist is authoritative for what op25 follows; a talkgroup absent
    // from the scraped reference data still produces audio and must still be
    // selectable, with null metadata.
    const rows = followedTalkgroups()
    for (const r of rows) {
      expect(r).toHaveProperty('alpha')
      expect(r).toHaveProperty('desc')
      expect(r).toHaveProperty('cat')
    }
  })
})
```

Add `readFileSync` and `whitelistPath` to that file's imports, and `followedTalkgroups` to the import from `./queries`:

```ts
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { sdrRoot, whitelistPath } from './paths'
import {
  listRecordings, getRecording, listTalkgroups, listCategories, codeStats,
  followedTalkgroups,
} from './queries'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm exec vitest run server/utils/queries.test.ts -t "followed talkgroups"`
Expected: FAIL — `followedTalkgroups` is not exported.

- [ ] **Step 3: Implement `followedTalkgroups`**

Add to `server/utils/queries.ts` (import `readFileSync` from `node:fs` and `whitelistPath` from `./paths` at the top of the file):

```ts
/** A talkgroup the running session follows, with its recent activity. */
export interface FollowedTalkgroup {
  tgid: number
  alpha: string | null
  desc: string | null
  cat: string | null
  /** Calls in the trailing window. Display ordering only. */
  recentCalls: number
}

/**
 * The talkgroups op25 is currently following, busiest first.
 *
 * Sourced from lwin_active_whitelist.txt rather than from the talkgroups table
 * or from sessionStore, for two reasons:
 *
 *   op25 emits audio ONLY for whitelisted talkgroups, so this is the exact set
 *   that can produce sound. A selector built from the reference table would
 *   offer rows that are silent forever with no error anywhere.
 *
 *   lwin_listen_multi.sh:117 writes the file at session start regardless of
 *   who launched the session, so this works for a session started from a shell
 *   as well as one started from the console.
 *
 * The file is NOT proof that anything is running — it persists unchanged after
 * a session dies. Callers pair it with isRadioBusy(); see the route.
 *
 * Ranking is load-bearing rather than cosmetic: only 15 of the 100 followed
 * talkgroups produced a call in a 6-hour window, so unranked the live ones sit
 * below 85 silent rows.
 */
export function followedTalkgroups(sinceSec = 6 * 3600): FollowedTalkgroup[] {
  let ids: number[]
  try {
    ids = readFileSync(whitelistPath(), 'utf-8')
      .split('\n')
      .map(l => Number.parseInt(l.trim(), 10))
      .filter(n => Number.isInteger(n) && n > 0)
  } catch {
    return []          // no session has ever run on this checkout
  }
  if (!ids.length) return []

  const db = getDb()

  const metaParams: (string | number)[] = []
  const metaWhere = tgidInClause('tgid', ids, metaParams)
  const meta = db.prepare(
    `SELECT tgid, alpha, description, cat FROM talkgroups WHERE ${metaWhere}`,
  ).all(...metaParams) as unknown as Array<{
    tgid: number
    alpha: string | null
    description: string | null
    cat: string | null
  }>
  const byTgid = new Map(meta.map(m => [m.tgid, m]))

  const cutoff = Math.floor(Date.now() / 1000) - sinceSec
  // cutoff is pushed first so the bound order matches the clause order below.
  const countParams: (string | number)[] = [cutoff]
  const countWhere = tgidInClause('tgid', ids, countParams)
  const counts = db.prepare(
    `SELECT tgid, COUNT(*) AS n FROM calls
      WHERE start > ? AND ${countWhere} GROUP BY tgid`,
  ).all(...countParams) as unknown as Array<{ tgid: number, n: number }>
  const countByTgid = new Map(counts.map(c => [c.tgid, c.n]))

  return ids
    .map((tgid) => {
      const m = byTgid.get(tgid)
      return {
        tgid,
        alpha: m?.alpha ?? null,
        desc: m?.description ?? null,
        cat: m?.cat ?? null,
        recentCalls: countByTgid.get(tgid) ?? 0,
      }
    })
    // Busiest first, then by id so the order is stable between calls.
    .sort((a, b) => b.recentCalls - a.recentCalls || a.tgid - b.tgid)
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pnpm exec vitest run server/utils/queries.test.ts`
Expected: PASS.

- [ ] **Step 5: Write the route**

Create `server/api/listen/followed.get.ts`:

```ts
import { statSync } from 'node:fs'
import { followedTalkgroups } from '~/server/utils/queries'
import { heldKeyIds } from '~/server/utils/keys'
import { isRadioBusy } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'
import { whitelistPath } from '~/server/utils/paths'

/**
 * Everything the Scanner Feed panel needs to arm itself, in one request.
 *
 * Read-only, so no CSRF guard: unlike /api/listen/start and /stop this drives
 * no radio and writes nothing. It reveals talkgroup ids the recordings list
 * already returns, plus which key IDS are held — never key material.
 *
 * `tracked` and `radioBusy` are reported separately on purpose. A session
 * started from a shell rather than from the console shows tracked:false with
 * radioBusy:true, and the feed works fine in that state because it depends on
 * the whitelist file and sdr.db rather than on sessionStore. Reporting only
 * `tracked` would make a working feed look dead.
 */
export default defineEventHandler(() => {
  let whitelistMtime: number | null = null
  try {
    whitelistMtime = statSync(whitelistPath()).mtimeMs / 1000
  } catch {
    whitelistMtime = null       // no session has ever run on this checkout
  }

  return {
    success: true,
    data: {
      talkgroups: followedTalkgroups(),
      heldKeyIds: heldKeyIds(),
      radioBusy: isRadioBusy(),
      tracked: sessionStore.get() !== null,
      whitelistMtime,
    },
  }
})
```

- [ ] **Step 6: Verify the route against the live system**

Run:

```bash
curl -s localhost:3000/api/listen/followed | python3 -c '
import json,sys
d=json.load(sys.stdin)["data"]
print("talkgroups:", len(d["talkgroups"]), "| heldKeyIds:", d["heldKeyIds"])
print("radioBusy:", d["radioBusy"], "| tracked:", d["tracked"])
print("top 5 by activity:", [(t["tgid"], t["recentCalls"]) for t in d["talkgroups"][:5]])
'
```

Expected: `talkgroups: 100`, `heldKeyIds: [1, 8, 12040]`, and the top rows carrying non-zero `recentCalls` in descending order.

- [ ] **Step 7: Confirm no key material is served**

Run: `curl -s localhost:3000/api/listen/followed | grep -c '"key"'`
Expected: `0`.

- [ ] **Step 8: Lint, full suite, commit**

```bash
pnpm lint && pnpm test
git add server/utils/queries.ts server/utils/queries.test.ts server/api/listen/followed.get.ts
git commit -m "feat(api): /api/listen/followed, the scanner feed selector source

Sourced from lwin_active_whitelist.txt rather than the talkgroups table,
because op25 emits audio only for whitelisted talkgroups -- a selector built
from the reference data would offer rows that stay silent forever with no error
anywhere. The file is written at session start regardless of who launched the
session, so this also works for a session started from a shell.

Activity ranking is load-bearing: only 15 of 100 followed talkgroups produced a
call in 6 hours, so unranked the live ones sit below 85 silent rows.

radioBusy and tracked are reported separately so an untracked shell-launched
session reads as working rather than dead."
```

---

### Task 5: The pure queue engine

**Files:**
- Create: `utils/scannerQueue.ts`
- Test: `utils/scannerQueue.test.ts`

**Interfaces:**
- Consumes: nothing. No DOM, no fetch, no Vue — this is why it is testable.
- Produces: `FeedCall`, `QueueEntry`, `ScannerQueue`, `ADP_ALGID`, `createQueue()`, `endedAtMs()`, `classify()`, `admit()`, `prune()`, `takeNext()`. Used by Task 6.

- [ ] **Step 1: Write the failing tests**

Create `utils/scannerQueue.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import {
  ADP_ALGID, createQueue, endedAtMs, classify, admit, prune, takeNext,
  type FeedCall,
} from './scannerQueue'

const HELD = new Set([1, 8, 12040])
const SELECTED = new Set([17094, 17095])
const NOW = 1_788_300_000_000        // fixed clock, ms

/** A clear call on a selected talkgroup, ending `agoSec` before NOW. */
function call(over: Partial<FeedCall> = {}): FeedCall {
  const agoSec = over.endedAt === undefined ? 5 : 0
  const endedAt = over.endedAt ?? NOW / 1000 - agoSec
  return {
    id: 1,
    file: 'TG17094_x_20260901-200000.wav',
    tgid: 17094,
    alpha: 'BRPD DISP 1',
    start: endedAt - 4,
    dur: 4,
    endedAt,
    algid: null,
    keyid: null,
    ...over,
  }
}

describe('endedAtMs', () => {
  it('uses endedAt when present', () => {
    expect(endedAtMs(call({ endedAt: 1000, start: 990, dur: 10 }))).toBe(1_000_000)
  })

  it('falls back to start + dur when endedAt is null', () => {
    expect(endedAtMs(call({ endedAt: null, start: 990, dur: 10 }))).toBe(1_000_000)
  })
})

describe('classify', () => {
  it('accepts a clear call on a selected talkgroup', () => {
    expect(classify(call(), SELECTED, HELD)).toBe('playable')
  })

  it('rejects a talkgroup that is not selected', () => {
    expect(classify(call({ tgid: 19999 }), SELECTED, HELD)).toBe('rejected')
  })

  it('accepts ADP under a key we hold', () => {
    expect(classify(call({ algid: ADP_ALGID, keyid: 8 }), SELECTED, HELD)).toBe('playable')
  })

  it('locks ADP under a key we do not hold', () => {
    // keyid 0x1320. Playing it would emit noise and read as a broken feature.
    expect(classify(call({ algid: ADP_ALGID, keyid: 4896 }), SELECTED, HELD)).toBe('locked')
  })

  it('treats algid 128 as clear', () => {
    // 0x80 is P25's "unencrypted" algorithm id, not an encryption algorithm.
    expect(classify(call({ algid: 128, keyid: 0 }), SELECTED, HELD)).toBe('playable')
  })
})

describe('prune', () => {
  /**
   * The regression that fixed the staleness field.
   *
   * The longest measured transmission is 32.4 s against a 30 s bound. Measuring
   * age from `start` makes every call longer than the bound born stale and
   * dropped before it is ever played — silently discarding exactly the long
   * transmissions most worth hearing.
   */
  it('keeps a 32.4s call that ended 5s ago under a 30s bound', () => {
    const q = createQueue()
    const ended = NOW / 1000 - 5
    admit(q, call({ endedAt: ended, start: ended - 32.4, dur: 32.4 }), SELECTED, HELD)
    prune(q, NOW, 30_000)
    expect(q.entries.length).toBe(1)
    expect(q.skipped).toBe(0)
  })

  it('drops a call that ended longer ago than the bound', () => {
    const q = createQueue()
    admit(q, call({ endedAt: NOW / 1000 - 45 }), SELECTED, HELD)
    prune(q, NOW, 30_000)
    expect(q.entries.length).toBe(0)
    expect(q.skipped).toBe(1)
  })

  it('ages locked entries out without counting them as skipped', () => {
    // Skipping noise that was never going to play is not a loss to report.
    const q = createQueue()
    admit(q, call({ algid: ADP_ALGID, keyid: 4896, endedAt: NOW / 1000 - 45 }), SELECTED, HELD)
    // Assert it was actually ENQUEUED first. Without this the end state is
    // indistinguishable from a bug where admit rejected the locked call
    // outright and prune trivially found an empty queue.
    expect(q.entries.length).toBe(1)
    expect(q.entries[0].kind).toBe('locked')
    prune(q, NOW, 30_000)
    expect(q.entries.length).toBe(0)
    expect(q.skipped).toBe(0)
  })

  it('keeps the same entries array, so a held reference stays valid', () => {
    // takeNext splices in place; prune must too. A consumer that aliases
    // queue.entries — which a Vue ref does — would otherwise be left holding a
    // detached array after the first prune, showing a queue frozen in time.
    const q = createQueue()
    const alias = q.entries
    admit(q, call({ id: 1, endedAt: NOW / 1000 - 45 }), SELECTED, HELD)
    admit(q, call({ id: 2, endedAt: NOW / 1000 - 2 }), SELECTED, HELD)
    prune(q, NOW, 30_000)
    expect(q.entries).toBe(alias)
    expect(alias.map(e => e.call.id)).toEqual([2])
  })
})

describe('admit', () => {
  it('does not enqueue a rejected call', () => {
    const q = createQueue()
    expect(admit(q, call({ tgid: 19999 }), SELECTED, HELD)).toBe('rejected')
    expect(q.entries.length).toBe(0)
  })

  it('ignores a call it has already queued', () => {
    // The id cursor makes this unlikely, but a retried fetch must not
    // double-play a call.
    const q = createQueue()
    admit(q, call({ id: 7 }), SELECTED, HELD)
    expect(admit(q, call({ id: 7 }), SELECTED, HELD)).toBe('rejected')
    expect(q.entries.length).toBe(1)
  })
})

describe('takeNext', () => {
  it('returns calls in the order they were admitted', () => {
    const q = createQueue()
    admit(q, call({ id: 1 }), SELECTED, HELD)
    admit(q, call({ id: 2, tgid: 17095 }), SELECTED, HELD)
    expect(takeNext(q, NOW, 30_000)?.id).toBe(1)
    expect(takeNext(q, NOW, 30_000)?.id).toBe(2)
    expect(takeNext(q, NOW, 30_000)).toBe(null)
  })

  it('skips over locked entries without removing them', () => {
    const q = createQueue()
    admit(q, call({ id: 1, algid: ADP_ALGID, keyid: 4896 }), SELECTED, HELD)
    admit(q, call({ id: 2 }), SELECTED, HELD)
    expect(takeNext(q, NOW, 30_000)?.id).toBe(2)
    // The locked row stays visible in the panel until it ages out.
    expect(q.entries.map(e => e.call.id)).toEqual([1])
  })

  it('returns null when only locked entries remain', () => {
    const q = createQueue()
    admit(q, call({ id: 1, algid: ADP_ALGID, keyid: 4896 }), SELECTED, HELD)
    expect(takeNext(q, NOW, 30_000)).toBe(null)
  })

  it('prunes before choosing, so a stale head is never played', () => {
    const q = createQueue()
    admit(q, call({ id: 1, endedAt: NOW / 1000 - 45 }), SELECTED, HELD)
    admit(q, call({ id: 2, endedAt: NOW / 1000 - 2 }), SELECTED, HELD)
    expect(takeNext(q, NOW, 30_000)?.id).toBe(2)
    expect(q.skipped).toBe(1)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pnpm exec vitest run utils/scannerQueue.test.ts`
Expected: FAIL — cannot resolve `./scannerQueue`.

- [ ] **Step 3: Write the implementation**

Create `utils/scannerQueue.ts`:

```ts
/**
 * The Scanner Feed's queue, as a pure module.
 *
 * No DOM, no fetch, no Vue — everything that decides WHICH call plays and WHEN
 * one is too old to bother with lives here, so it can be tested directly. The
 * composable around it does only wiring: SSE in, <audio> out.
 *
 * Scanner semantics: one output, calls play in the order they happened, and a
 * call that has been waiting longer than the staleness bound is dropped rather
 * than played late. With up to four simultaneous calls across the receivers, a
 * serial player that never dropped anything would fall progressively further
 * behind live during a burst and never catch up.
 */

/** P25 ADP. Anything else in `algid` is not an encryption algorithm we gate on. */
export const ADP_ALGID = 170        // 0xAA

/** The fields of a `Recording` the queue actually needs. */
export interface FeedCall {
  id: number
  file: string
  tgid: number | null
  alpha: string | null
  start: number
  dur: number
  endedAt: number | null
  algid: number | null
  keyid: number | null
}

export type Admission = 'playable' | 'locked' | 'rejected'

export interface QueueEntry {
  call: FeedCall
  kind: 'playable' | 'locked'
}

export interface ScannerQueue {
  entries: QueueEntry[]
  /** Playable calls dropped for age. Locked ones are not counted. */
  skipped: number
}

export function createQueue(): ScannerQueue {
  return { entries: [], skipped: 0 }
}

/**
 * When this call ENDED, in ms.
 *
 * Staleness is measured from the end of the transmission, never from its
 * start: the longest measured call is 32.4 s against a default 30 s bound, so
 * ageing from `start` would make every long transmission born stale and drop
 * it before it was ever played.
 *
 * `endedAt` is populated on every row the recorder writes; the `start + dur`
 * fallback covers a row written by some other path.
 */
export function endedAtMs(call: FeedCall): number {
  const sec = call.endedAt ?? call.start + call.dur
  return sec * 1000
}

/**
 * Should this call play, appear silently, or be ignored?
 *
 * Encryption is decided from `algid`/`keyid`, NOT from `encObserved` /
 * `encEvidence`. Those two are filled by a later reconciliation pass and are
 * null on every live row, so a filter keyed off them classifies everything as
 * clear and plays noise.
 */
export function classify(
  call: FeedCall,
  selectedTgids: ReadonlySet<number>,
  heldKeyIds: ReadonlySet<number>,
): Admission {
  if (call.tgid === null || !selectedTgids.has(call.tgid)) return 'rejected'
  if (call.algid === ADP_ALGID && !heldKeyIds.has(call.keyid ?? -1)) return 'locked'
  return 'playable'
}

/**
 * Classify and enqueue. Returns what was decided.
 *
 * A call already in the queue is refused: the id cursor should make a duplicate
 * impossible, but a retried fetch must not double-play.
 */
export function admit(
  queue: ScannerQueue,
  call: FeedCall,
  selectedTgids: ReadonlySet<number>,
  heldKeyIds: ReadonlySet<number>,
): Admission {
  if (queue.entries.some(e => e.call.id === call.id)) return 'rejected'
  const kind = classify(call, selectedTgids, heldKeyIds)
  if (kind === 'rejected') return 'rejected'
  queue.entries.push({ call, kind })
  return kind
}

/**
 * Drop everything that ended longer than `stalenessMs` ago.
 *
 * Locked entries age out on the same bound so the panel does not accumulate
 * them, but they do not count toward `skipped` — reporting noise you were never
 * going to hear as a loss would be misleading.
 */
export function prune(queue: ScannerQueue, nowMs: number, stalenessMs: number): number {
  // Mutates `entries` IN PLACE rather than reassigning it.
  //
  // Rebuilding into a new array and assigning `queue.entries = kept` would be
  // simpler to read, and would quietly break any consumer holding a reference
  // to the array — which a Vue composable does the moment it aliases it into a
  // ref. `takeNext` already splices in place, so doing the same here keeps one
  // uniform contract: the array identity a caller obtains stays valid for the
  // life of the queue.
  //
  // Iterated backwards because splicing during a forward walk skips the
  // element after each removal.
  let dropped = 0
  for (let i = queue.entries.length - 1; i >= 0; i--) {
    const e = queue.entries[i]
    if (nowMs - endedAtMs(e.call) > stalenessMs) {
      if (e.kind === 'playable') {
        queue.skipped += 1
        dropped += 1
      }
      queue.entries.splice(i, 1)
    }
  }
  return dropped
}

/**
 * Prune, then remove and return the oldest playable call.
 *
 * Pruning happens here rather than on a timer so a backgrounded tab — where
 * browsers throttle timers hard — cannot leave the queue in a stale state.
 * Locked entries are stepped over and left in place; they are display-only.
 */
export function takeNext(
  queue: ScannerQueue,
  nowMs: number,
  stalenessMs: number,
): FeedCall | null {
  prune(queue, nowMs, stalenessMs)
  const i = queue.entries.findIndex(e => e.kind === 'playable')
  if (i === -1) return null
  const [entry] = queue.entries.splice(i, 1)
  return entry.call
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pnpm exec vitest run utils/scannerQueue.test.ts`
Expected: PASS, 16 tests.

- [ ] **Step 5: Lint, full suite, commit**

```bash
pnpm lint && pnpm test
git add utils/scannerQueue.ts utils/scannerQueue.test.ts
git commit -m "feat(scanner): pure queue engine for the live feed

Everything deciding which call plays and when one is too old lives here with no
DOM, fetch or Vue, so it is testable directly.

Two regressions are pinned. Staleness measures from end of call, not start: the
longest measured transmission is 32.4s against a 30s bound, so ageing from
start would make every long call born stale and drop it unplayed. And
encryption is classified from algid/keyid rather than encObserved/encEvidence,
which are filled by a later reconciliation pass and are null on every live row
-- a filter keyed off them would call everything clear and play noise."
```

---

### Task 6: Wire it up — audio actually plays

Deliverable: pressing Play produces audio from the selected talkgroups. The panel is deliberately minimal here; Task 7 makes it usable.

**Files:**
- Create: `composables/useScannerFeed.ts`
- Create: `components/ScannerFeed.vue` (minimal)
- Modify: `pages/index.vue`

**Interfaces:**
- Consumes: `/api/listen/followed` (Task 4), `/api/recordings/list` with `afterId`/`tgids` returning `maxId` (Task 2), `utils/scannerQueue.ts` (Task 5).
- Produces: `useScannerFeed()` returning `{ followed, heldKeyIds, selected, armed, stalenessSec, settingPersists, entries, skipped, failed, nowPlaying, streamOk, radioBusy, tracked, error, load, arm, disarm }`. Used by Task 7. In Task 6 `settingPersists` is `ref(true)` and unused; Task 7 gives it meaning.

- [ ] **Step 1: Write the composable**

Create `composables/useScannerFeed.ts`:

```ts
import { ref, computed, onUnmounted } from 'vue'
import {
  createQueue, admit, prune, takeNext,
  type FeedCall, type QueueEntry, type ScannerQueue,
} from '~/utils/scannerQueue'

interface FollowedTalkgroup {
  tgid: number
  alpha: string | null
  desc: string | null
  cat: string | null
  recentCalls: number
}

interface FollowedResponse {
  success: boolean
  data: {
    talkgroups: FollowedTalkgroup[]
    heldKeyIds: number[]
    radioBusy: boolean
    tracked: boolean
    whitelistMtime: number | null
  }
}

interface ListResponse {
  success: boolean
  data: FeedCall[]
  total: number
  maxId: number
}

/**
 * How many calls to pull per SSE tick.
 *
 * At roughly 4 calls a minute a page is never close to full. If one ever is —
 * after a long disconnect — the query returns the oldest pending rows, this
 * drains them a page per tick, and `prune` discards whatever is already older
 * than the staleness bound. No special case needed; see pump().
 */
const PAGE = 500

/**
 * A clip must BEGIN within this long, or it is abandoned.
 *
 * Separate from the total deadline below because the two failures have very
 * different costs. A 32-second clip that never starts would burn 35 seconds
 * against the total deadline alone — longer than the default 30-second
 * staleness bound, so the entire queued backlog would prune while the feed sat
 * on one dead clip. Two seconds is far beyond what a ~70 KB file over a LAN
 * needs, and `playing` clears it the moment audio actually starts.
 */
const START_DEADLINE_MS = 2000

/**
 * Slack added to a clip's own duration for its total deadline.
 *
 * The asymmetry matters: a deadline that fires late costs dead air, one that
 * fires early truncates real audio — so this is deliberately generous.
 * Background-tab throttling only delays timers, which fails in the safe
 * direction.
 */
const CLIP_SLACK_MS = 3000

/** `dur` comes from the database and is not validated there. */
const MAX_CLIP_SEC = 60

export function useScannerFeed() {
  const followed = ref<FollowedTalkgroup[]>([])
  const heldKeyIds = ref<number[]>([])
  const selected = ref<number[]>([])
  const armed = ref(false)
  const stalenessSec = ref(30)
  /** Task 7 gives this meaning; declared here so the return shape is stable. */
  const settingPersists = ref(true)
  const skipped = ref(0)
  const nowPlaying = ref<FeedCall | null>(null)
  const streamOk = ref(false)
  const radioBusy = ref(false)
  const tracked = ref(false)
  const error = ref('')

  const queue: ScannerQueue = createQueue()
  // NOT `ref(queue.entries)`: aliasing the live array here would leave this ref
  // pointing at queue state that mutates underneath Vue without notifying it.
  // `sync()` assigns a fresh array on every change instead.
  const entries = ref<QueueEntry[]>([])

  /**
   * Clips abandoned because playback failed, deliberately NOT folded into
   * `skipped`.
   *
   * `skipped` means "playable calls dropped for age" and Task 7 surfaces it as
   * the signal for whether the staleness bound is too tight. A clip that
   * stalled after playing most of its audio is a different event, and
   * `skipped` is the pure module's state — the transport does not own it.
   */
  const failed = ref(0)

  let lastSeenId = 0
  // Guards `arm` across its await, and serialises pumps. Without the first, a
  // double-click runs `arm` twice and orphans the first EventSource — never
  // closed, doubling the poll rate for the life of the page. Without the
  // second, two change-frames inside one fetch round-trip both query the same
  // `afterId`; a call the first pump already handed to `takeNext` is no longer
  // in `entries`, so `admit`'s dedupe misses it and it plays twice.
  let armInFlight = false
  let pumpInFlight = false
  // Bumped by `disarm`, so an `arm` still awaiting its seed fetch can tell it
  // was cancelled and must not resurrect the feed.
  let armGeneration = 0
  let startTimer: ReturnType<typeof setTimeout> | null = null
  let clipTimer: ReturnType<typeof setTimeout> | null = null
  let es: EventSource | null = null
  // ONE element, created on the first arm and reused for every clip.
  //
  // Browsers gate autoplay on a user gesture and the unlock attaches to the
  // element that gesture reached. A fresh `new Audio()` per call loses it, and
  // the feed goes silent after the first clip on Safari and iOS.
  let audio: HTMLAudioElement | null = null

  const selectedSet = computed(() => new Set(selected.value))
  const heldSet = computed(() => new Set(heldKeyIds.value))

  /** Mirror the plain queue object into the ref the template renders. */
  function sync(): void {
    entries.value = [...queue.entries]
    skipped.value = queue.skipped
  }

  async function load(): Promise<void> {
    try {
      const res = await $fetch<FollowedResponse>('/api/listen/followed')
      followed.value = res.data.talkgroups
      heldKeyIds.value = res.data.heldKeyIds
      radioBusy.value = res.data.radioBusy
      tracked.value = res.data.tracked
      error.value = ''
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Could not load talkgroups'
    }
  }

  async function pump(): Promise<void> {
    // An armed feed with nothing selected must be silent. The query layer also
    // refuses an empty selection; this avoids the round trip.
    if (!armed.value || selected.value.length === 0) return
    if (pumpInFlight) return
    pumpInFlight = true
    try {
      await pumpOnce()
    } finally {
      pumpInFlight = false
    }
  }

  async function pumpOnce(): Promise<void> {
    let res: ListResponse
    try {
      res = await $fetch<ListResponse>('/api/recordings/list', {
        query: {
          afterId: lastSeenId,
          tgids: selected.value.join(','),
          limit: PAGE,
        },
      })
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Feed fetch failed'
      return
    }
    // Clear on success, or a single transient failure stays on screen forever.
    error.value = ''

    // Rows arrive oldest-first because the query orders by rowid whenever
    // `afterId` is set, so they are admitted in the order the calls finished
    // and no reordering is needed here.
    //
    // A truncated page needs no special handling either: it is a prefix of the
    // pending set, so advancing to the last row received and letting the next
    // tick continue drains the backlog without losing a row. Anything in that
    // backlog older than the staleness bound is dropped by `prune` on arrival,
    // which is what stops a long absence from replaying hours of audio.
    for (const row of res.data) {
      lastSeenId = Math.max(lastSeenId, row.id)
      admit(queue, row, selectedSet.value, heldSet.value)
    }
    prune(queue, Date.now(), stalenessSec.value * 1000)
    sync()
    playIfIdle()
  }

  function clearTimers(): void {
    if (startTimer) { clearTimeout(startTimer); startTimer = null }
    if (clipTimer) { clearTimeout(clipTimer); clipTimer = null }
  }

  /**
   * The ONE way a clip ends. Clears the interlock, then advances.
   *
   * Every advance path must go through here. `nowPlaying` is the interlock
   * `playIfIdle` tests, so clearing it inline and calling `playIfIdle`
   * separately — or in the wrong order — makes the handler early-return and
   * reintroduces the wedge this whole mechanism exists to prevent.
   */
  function finishClip(didFail: boolean): void {
    clearTimers()
    if (didFail) failed.value += 1
    nowPlaying.value = null
    playIfIdle()
  }

  function playIfIdle(): void {
    if (!armed.value) return
    if (!audio) return
    // The interlock is OUR state, never the element's.
    //
    // `audio.paused` cannot serve here: `play()` resolves only when playback
    // begins, so a clip that never starts leaves `paused` false forever,
    // `ended` never fires, and every later tick early-returns — the feed goes
    // permanently off the air with no error anywhere. That failure was
    // observed during this task's implementation and misread as environmental.
    if (nowPlaying.value !== null) return

    const call = takeNext(queue, Date.now(), stalenessSec.value * 1000)
    sync()
    if (!call) return

    nowPlaying.value = call
    audio.src = `/api/recordings/${encodeURIComponent(call.file)}`

    // Both timers verify they still own the current clip before acting, so a
    // stale timer surviving any exit path is inert rather than cutting off the
    // clip that just started.
    startTimer = setTimeout(() => {
      if (nowPlaying.value?.id !== call.id) return
      finishClip(true)
    }, START_DEADLINE_MS)

    const dur = Number.isFinite(call.dur)
      ? Math.max(0, Math.min(call.dur, MAX_CLIP_SEC))
      : 0
    clipTimer = setTimeout(() => {
      if (nowPlaying.value?.id !== call.id) return
      finishClip(true)
    }, dur * 1000 + CLIP_SLACK_MS)

    audio.play().catch((e: unknown) => {
      if (nowPlaying.value?.id !== call.id) return
      // `disarm`'s pause() rejects a pending play() with AbortError. That is
      // us stopping deliberately, not a clip failure — surfacing it puts a red
      // banner on screen every time the operator presses Stop.
      if (e instanceof DOMException && e.name === 'AbortError') return
      error.value = e instanceof Error ? e.message : 'Playback failed'
      finishClip(true)
    })
  }

  async function arm(): Promise<void> {
    // Re-entry guard. Two rapid Play clicks would otherwise run this twice and
    // orphan the first EventSource — unreachable, never closed, doubling the
    // poll rate and holding the server's 1s data_version timer open until the
    // page unloads.
    if (armInFlight || armed.value) return
    armInFlight = true
    const generation = ++armGeneration

    try {
    // Created inside the click handler so the user gesture unlocks THIS
    // element — and ACTIVATED here too, which construction alone does not do.
    //
    // WebKit blesses a media element only when play() or load() is invoked
    // during the gesture. Without this load(), the first play() happens on a
    // later SSE tick, outside any gesture, on an element iOS/Safari has never
    // seen a gesture-driven call on — every clip then throws NotAllowedError
    // and the feed never plays at all. Desktop Chrome and Firefox grant
    // document-level sticky activation from the click, which is exactly why
    // this hides during desktop testing.
    if (!audio) {
      audio = new Audio()
      audio.load()

      audio.addEventListener('playing', () => {
        // Playback actually began; only the total deadline still applies.
        if (startTimer) { clearTimeout(startTimer); startTimer = null }
      })
      audio.addEventListener('ended', () => finishClip(false))
      audio.addEventListener('error', () => {
        // disarm's removeAttribute('src') + load() fires an empty-src error on
        // Chrome. That is teardown, not a clip failure, and advancing on it
        // would make Stop pull the next clip on its way out.
        if (nowPlaying.value === null) return
        finishClip(true)
      })
    }

    // Seed the cursor at arm time, not at mount: starting from MAX(id) means
    // arming the feed starts from now instead of replaying the whole corpus,
    // and taking it here closes the window where calls land while the panel
    // sits open but unarmed.
    try {
      const res = await $fetch<ListResponse>('/api/recordings/list', {
        query: { limit: 1 },
      })
      lastSeenId = res.maxId
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Could not seed the feed cursor'
      return
    }

    // Cancelled while awaiting the seed. Without this, Play -> Stop during the
    // fetch resumes here and leaves the feed armed with a live EventSource
    // after the operator asked it to stop.
    if (generation !== armGeneration) return

    // length = 0 rather than a fresh array: prune and takeNext both mutate in
    // place, and the queue's identity contract says the array a caller holds
    // stays valid.
    queue.entries.length = 0
    queue.skipped = 0
    failed.value = 0
    sync()
    armed.value = true

    // EventSource reconnects on its own after a drop, which is most of why
    // this is SSE and not a hand-rolled fetch loop. The id cursor means a drop
    // costs latency, not calls.
    es = new EventSource('/api/recordings/stream')
    es.onopen = () => { streamOk.value = true }
    es.onerror = () => { streamOk.value = false }
    es.onmessage = () => {
      streamOk.value = true
      void pump()
    }
    } finally {
      armInFlight = false
    }
  }

  function disarm(): void {
    // Invalidates any arm() still awaiting its seed fetch.
    armGeneration++
    armed.value = false
    streamOk.value = false
    clearTimers()
    es?.close()
    es = null
    // Cleared BEFORE the teardown below, so the error listener sees a null
    // nowPlaying and treats the empty-src error as teardown rather than a
    // failed clip.
    nowPlaying.value = null
    if (audio) {
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
    }
    queue.entries.length = 0
    sync()
  }

  onUnmounted(disarm)

  return {
    followed, heldKeyIds, selected, armed, stalenessSec, settingPersists,
    entries, skipped, failed, nowPlaying, streamOk, radioBusy, tracked, error,
    load, arm, disarm,
  }
}
```

- [ ] **Step 2: Write the minimal component**

Create `components/ScannerFeed.vue`:

```vue
<template>
  <div class="card p-3 border-round surface-card">
    <h2 class="text-xl font-semibold mt-0 mb-3">Scanner Feed</h2>

    <Message v-if="error" severity="error" :closable="false" class="mb-2">
      {{ error }}
    </Message>

    <div class="mb-3">
      <label for="feed-tgs" class="block mb-1 text-sm text-color-secondary">
        Talkgroups ({{ followed.length }} followed)
      </label>
      <MultiSelect
        id="feed-tgs"
        v-model="selected"
        :options="followed"
        option-label="label"
        option-value="tgid"
        filter
        display="chip"
        placeholder="Select talkgroups"
        class="w-full"
      />
    </div>

    <div class="flex align-items-center gap-2">
      <Button
        :label="armed ? 'Stop' : 'Play'"
        :icon="armed ? 'pi pi-stop' : 'pi pi-play'"
        :disabled="!armed && selected.length === 0"
        @click="armed ? disarm() : arm()"
      />
      <span v-if="nowPlaying" class="font-medium">
        {{ nowPlaying.alpha ?? nowPlaying.tgid }}
      </span>
      <span v-else-if="armed" class="text-color-secondary">waiting…</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'

const feed = useScannerFeed()
const {
  selected, armed, nowPlaying, error, arm, disarm,
} = feed

// MultiSelect needs a flat label; keep the activity count visible in it so the
// ranking from the server is legible in the dropdown.
const followed = computed(() =>
  feed.followed.value.map(t => ({
    ...t,
    label: `${t.tgid} · ${t.alpha ?? 'unknown'} (${t.recentCalls})`,
  })),
)

onMounted(feed.load)
</script>
```

- [ ] **Step 3: Register MultiSelect**

In `plugins/primevue.ts`, add the import alongside the others (alphabetical, after `Message`):

```ts
import MultiSelect from 'primevue/multiselect'
```

and the registration alongside the others:

```ts
  nuxtApp.vueApp.component('MultiSelect', MultiSelect)
```

- [ ] **Step 4: Mount the panel**

In `pages/index.vue`, put `ScannerFeed` above `RecordingsList` — the live feed is what is being watched, the recordings table is the archive being searched:

```vue
    <div class="grid">
      <div class="col-12 lg:col-4">
        <ListenControl />
      </div>
      <div class="col-12 lg:col-8">
        <ScannerFeed />
        <div class="mt-3">
          <RecordingsList />
        </div>
      </div>
      <div class="col-12">
        <TalkgroupBrowser />
      </div>
    </div>
```

- [ ] **Step 5: Verify it plays**

Start the dev server if it is not already running (`pnpm dev`), open `http://localhost:3000`, then:

1. Confirm the Talkgroups dropdown lists ~100 entries, busiest first, each showing a call count.
2. Select the top two or three (highest counts).
3. Press **Play**. The button becomes Stop.
4. Wait for traffic. Within a few seconds of a call ending on a selected talkgroup you should hear it, and the talkgroup name should appear beside the button.

Confirm the request shape in DevTools → Network: each SSE tick should be followed by a `list?afterId=<n>&tgids=...` request whose `afterId` increases and never resets.

If nothing plays within a few minutes, check that the talkgroups you picked have non-zero counts — only about 15 of the 100 are ever active.

- [ ] **Step 6: Lint, typecheck, full suite**

Run: `pnpm lint && pnpm typecheck && pnpm test`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add composables/useScannerFeed.ts components/ScannerFeed.vue plugins/primevue.ts pages/index.vue
git commit -m "feat(scanner): live feed plays selected talkgroups in the browser

Rides the existing recordings SSE as a trigger only and re-queries through the
one query builder, so the server keeps no per-client filter state.

One <audio> element is created inside the click handler and reused for every
clip: browsers gate autoplay on a user gesture and the unlock attaches to that
element, so a fresh Audio() per call goes silent after the first clip.

The cursor is seeded at arm time rather than at mount, so arming starts from
now instead of replaying the corpus, and no calls slip through while the panel
sits open unarmed."
```

---

### Task 7: Make the panel usable

Deliverable: the queue is visible, locked calls are legible as crack targets, session state is reported honestly, and the staleness bound is adjustable and remembered.

**Files:**
- Modify: `components/ScannerFeed.vue`
- Modify: `composables/useScannerFeed.ts` (persist `stalenessSec`)

**Interfaces:**
- Consumes: everything `useScannerFeed()` returns (Task 6).
- Produces: nothing downstream.

- [ ] **Step 1: Persist the staleness setting**

In `composables/useScannerFeed.ts`, replace the `stalenessSec` declaration:

```ts
  /**
   * How long a call may wait before it is dropped rather than played late.
   *
   * Client state; the server has no opinion about it. Read defensively because
   * localStorage throws in some contexts rather than returning null.
   */
  const stalenessSec = ref(30)
  /**
   * False once a write has been refused — a private window, or a browser
   * blocking site data. Surfaced in the panel so the operator is told the
   * setting will not survive a reload, rather than discovering it later.
   *
   * Handled visibly rather than swallowed: a comment-only catch would leave
   * the failure invisible, and a console.warn would fire on every change of
   * the control.
   */
  const settingPersists = ref(true)

  const DEFAULT_STALENESS = 30
  try {
    const saved = Number.parseInt(localStorage.getItem('scanner-staleness') ?? '', 10)
    stalenessSec.value = Number.isInteger(saved) && saved >= 10 && saved <= 300
      ? saved
      : DEFAULT_STALENESS
  } catch {
    // Reading was refused, so nothing was stored for us to honour. Fall back
    // explicitly and record that writes will fail too.
    stalenessSec.value = DEFAULT_STALENESS
    settingPersists.value = false
  }
  watch(stalenessSec, (v) => {
    try {
      localStorage.setItem('scanner-staleness', String(v))
      settingPersists.value = true
    } catch {
      settingPersists.value = false
    }
  })
```

and add `watch` to the `vue` import at the top of the file:

```ts
import { ref, computed, watch, onUnmounted } from 'vue'
```

- [ ] **Step 2: Build out the template**

Replace the `<template>` block of `components/ScannerFeed.vue`:

```vue
<template>
  <div class="card p-3 border-round surface-card">
    <div class="flex align-items-center justify-content-between mb-3">
      <h2 class="text-xl font-semibold m-0">Scanner Feed</h2>
      <div class="flex align-items-center gap-2 text-sm">
        <Tag v-if="armed && streamOk" severity="success" value="live" />
        <Tag v-else-if="armed" severity="warn" value="reconnecting" />
        <span class="text-color-secondary">{{ sessionLabel }}</span>
      </div>
    </div>

    <Message v-if="error" severity="error" :closable="false" class="mb-2">
      {{ error }}
    </Message>

    <div class="flex gap-2 align-items-end mb-3">
      <div class="flex-1">
        <label for="feed-tgs" class="block mb-1 text-sm text-color-secondary">
          Talkgroups ({{ followed.length }} followed, {{ activeCount }} active)
        </label>
        <MultiSelect
          id="feed-tgs"
          v-model="selected"
          :options="followed"
          option-label="label"
          option-value="tgid"
          filter
          display="chip"
          placeholder="Select talkgroups"
          class="w-full"
        />
      </div>
      <div style="width: 9rem">
        <label for="feed-stale" class="block mb-1 text-sm text-color-secondary">
          Drop after
        </label>
        <InputNumber
          id="feed-stale"
          v-model="stalenessSec"
          :min="10"
          :max="300"
          suffix=" s"
          show-buttons
          class="w-full"
        />
        <small v-if="!settingPersists" class="text-color-secondary">
          won't persist in this browser
        </small>
      </div>
    </div>

    <div class="flex align-items-center gap-3 mb-3">
      <Button
        :label="armed ? 'Stop' : 'Play'"
        :icon="armed ? 'pi pi-stop' : 'pi pi-play'"
        :disabled="!armed && selected.length === 0"
        @click="armed ? disarm() : arm()"
      />
      <div v-if="nowPlaying" class="flex-1">
        <div class="font-medium">
          {{ nowPlaying.alpha ?? `TG ${nowPlaying.tgid}` }}
        </div>
        <div class="text-sm text-color-secondary">
          {{ nowPlaying.dur.toFixed(1) }}s · {{ behindLive }}s behind live
        </div>
      </div>
      <div v-else-if="armed" class="flex-1 text-color-secondary">
        waiting for traffic…
      </div>
      <Tag v-if="skipped > 0" severity="secondary" :value="`${skipped} skipped`" />
    </div>

    <div v-if="armed" class="border-top-1 surface-border pt-2">
      <div v-if="entries.length === 0" class="text-sm text-color-secondary">
        Queue empty.
      </div>
      <div
        v-for="e in entries"
        :key="e.call.id"
        class="flex align-items-center gap-2 py-1 text-sm"
        :class="e.kind === 'locked' ? 'text-color-secondary' : ''"
      >
        <i :class="e.kind === 'locked' ? 'pi pi-lock' : 'pi pi-volume-up'" />
        <span class="font-medium">
          {{ e.call.alpha ?? `TG ${e.call.tgid}` }}
        </span>
        <span>{{ e.call.dur.toFixed(1) }}s</span>
        <span v-if="e.kind === 'locked'">
          keyid 0x{{ (e.call.keyid ?? 0).toString(16).toUpperCase() }} · no key held ·
          crack target
        </span>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 3: Extend the script block**

Replace the `<script setup lang="ts">` block of `components/ScannerFeed.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted } from 'vue'

const feed = useScannerFeed()
const {
  selected, armed, stalenessSec, settingPersists, entries, skipped, nowPlaying,
  streamOk, error, arm, disarm,
} = feed

// MultiSelect needs a flat label; keeping the activity count in it makes the
// server's ranking legible in the dropdown. Only about 15 of the 100 followed
// talkgroups are ever active, so the count is what makes the list usable.
const followed = computed(() =>
  feed.followed.value.map(t => ({
    ...t,
    label: `${t.tgid} · ${t.alpha ?? 'unknown'} (${t.recentCalls})`,
  })),
)

const activeCount = computed(
  () => feed.followed.value.filter(t => t.recentCalls > 0).length,
)

/**
 * Honest reporting of what the radio is doing.
 *
 * A session started from a shell rather than from the console reads
 * tracked:false with radioBusy:true, and the feed works fine in that state —
 * it depends on the whitelist file and sdr.db, not on sessionStore. Showing
 * only "no session" would make a working feed look dead.
 */
const sessionLabel = computed(() => {
  if (feed.tracked.value) return 'console session'
  if (feed.radioBusy.value) return 'radio busy · untracked session'
  return 'radio idle'
})

const behindLive = computed(() => {
  const c = nowPlaying.value
  if (!c) return '0'
  const ended = (c.endedAt ?? c.start + c.dur) * 1000
  return Math.max(0, (Date.now() - ended) / 1000).toFixed(0)
})

onMounted(feed.load)
</script>
```

- [ ] **Step 4: Verify the finished panel**

With `pnpm dev` running and a capture session live, open `http://localhost:3000` and confirm each of these:

1. The header shows `radio busy · untracked session` (or `console session` if started from the console), and `live` once armed.
2. The talkgroup label reads `N followed, M active`, with M much smaller than N.
3. Selecting talkgroups and pressing Play produces audio; the queue list below shows pending calls.
4. `Drop after` changes persist across a page reload. In a private window it should instead show "won't persist in this browser" beneath the control.
5. Set `Drop after` to its 10 s minimum during a busy period and confirm the `skipped` tag appears and increments.
6. If an unkeyed encrypted call arrives, it appears greyed with a lock icon, its keyid in hex, and `crack target` — and is not played. To confirm the rendering without waiting, temporarily select a talkgroup known to carry keyid `0x1320`.

- [ ] **Step 5: Lint, typecheck, full suite**

Run: `pnpm lint && pnpm typecheck && pnpm test`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add components/ScannerFeed.vue composables/useScannerFeed.ts
git commit -m "feat(scanner): queue view, locked-call badges and staleness control

Unkeyed encrypted calls render greyed with their keyid and 'crack target'
rather than being hidden: an unheld keyid appearing live is exactly the signal
the ADP recovery work needs, and playing it would emit noise that reads as a
broken feature.

The header reports tracked and radioBusy separately so a shell-launched session
shows as working rather than dead."
```

---

## Verification against the spec

| Spec requirement | Task |
|---|---|
| `afterId` cursor on `calls.id`, not a timestamp | 2 |
| `tgids` filter; empty selection matches nothing | 2 (SQL) + 6 (composable) |
| `maxId` as a separate unfiltered aggregate | 2 |
| Project `c.id` and `c.ended_at` through `Recording` | 1 |
| `algid`/`keyid` already projected — no change needed | verified in 1 |
| `keys.ts` exposing keyid presence only | 3 |
| `heldKeyIds` delivered on the followed response | 4 |
| `/api/listen/followed`, whitelist-sourced, activity-ranked | 4 |
| `radioBusy` + `tracked` reported separately | 4 (route) + 7 (label) |
| Pure queue engine: admit / classify / prune / takeNext | 5 |
| Staleness from `ended_at`, never `start` | 5 (impl + regression test) |
| Locked entries visible, silent, not counted as skipped | 5 (logic) + 7 (rendering) |
| Cursor seeded from `maxId` at Play, not at mount | 6 |
| One reused `<audio>` element for the autoplay unlock | 6 |
| Rows reversed to play in the order they happened | 6 |
| Panel above `RecordingsList` in `pages/index.vue` | 6 |
| Staleness adjustable 10–300 s, persisted in `localStorage` | 7 |
| SSE drop costs latency, not calls | 6 (cursor) + 7 (`reconnecting` tag) |
| No key material reachable from the browser | 3 (test) + 4 (curl check) |
