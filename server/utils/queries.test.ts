import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import { existsSync, readdirSync, readFileSync, mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { createRequire } from 'node:module'
import { sdrRoot, whitelistPath } from './paths'
import {
  listRecordings, getRecording, listTalkgroups, listCategories, codeStats,
  followedTalkgroups, transcriptionHealth, whitelistTgids, talkgroupEncryptionTallies,
} from './queries'
import { dbPath, closeDb, getDb } from './db'
import { CLEAR_ALGID } from '../../utils/callEncryption'

/**
 * Count the SQL statements a call actually EXECUTES.
 *
 * Two tests in this file are named for a property their bodies never checked:
 * `it('rolls the whole corpus up in ONE query, not one per talkgroup')`
 * asserted only that the returned map had a plausible size, and
 * `it('joins talkgroup metadata rather than resolving it per row in JS')`
 * asserted only that the joined columns were populated. A per-talkgroup or
 * per-row refactor passes both unchanged — which makes them tests of the name,
 * not of the code. The cost this file's comments care about is real: the bay
 * polls /api/listen/followed every 20 s across 222 whitelisted talkgroups.
 *
 * `getDb()` is a module-level singleton shared by every test in this file, so
 * the patch is installed on the live handle and MUST come off again — hence
 * the `finally`. A leaked patch would count another test's queries and, worse,
 * outlive this describe block.
 *
 * Execution is counted rather than preparation because that is what the claim
 * is about: a loop preparing one statement and running it 222 times is exactly
 * the shape being ruled out. A Proxy is used rather than assigning over
 * `st.all` because `all`/`get`/`run` are overloaded (positional and named
 * parameters), and a single-signature replacement is not assignable to them.
 */
function countQueries<T>(fn: () => T): { result: T, runs: number, sql: string[] } {
  const db = getDb()
  const realPrepare = db.prepare.bind(db)
  const sql: string[] = []
  let runs = 0
  const EXECUTORS = new Set(['all', 'get', 'run', 'iterate'])

  db.prepare = (source: string) => new Proxy(realPrepare(source), {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver)
      if (typeof value !== 'function' || !EXECUTORS.has(String(prop))) return value
      return (...args: unknown[]) => {
        runs += 1
        sql.push(source)
        return Reflect.apply(value, target, args)
      }
    },
  })

  try {
    return { result: fn(), runs, sql }
  }
  finally {
    db.prepare = realPrepare
  }
}

/**
 * These run against the REAL sdr.db, not a fixture.
 *
 * That is deliberate. The bugs this project actually hit were all disagreements
 * between assumed and real data — talkgroup entries with no `tgid` field, an
 * `enc` value of 'full' rather than 'encrypted', 149 sites collapsing to 67 on
 * a non-unique key. A fixture would have reproduced the assumption, not the
 * data. The counts below are measured facts about this system.
 */
beforeAll(() => {
  if (!existsSync(dbPath())) {
    throw new Error(
      `No database at ${dbPath()}. Run: python3 scripts/import_to_sqlite.py`,
    )
  }
})

describe('observed encryption', () => {
  /**
   * The reported symptom: a recording said its talkgroup was "fully encrypted"
   * while playing clear voice. enc is the scraped RadioReference label for the
   * talkgroup; encObserved is what this call actually carried.
   */
  it('exposes the observed state and the evidence behind it', () => {
    const rows = listRecordings({ limit: 1 }).rows
    expect(rows[0]).toHaveProperty('encObserved')
    expect(rows[0]).toHaveProperty('encEvidence')
  })

  it('has observed states populated by the harvester', () => {
    // Scoped to the whole corpus, not the newest page. enc_observed is written
    // by a later reconciliation pass (scripts/enc_harvest.py), so calls from a
    // capture still in progress carry NULL. Taking the newest 500 rows during
    // a live session found zero harvested rows and went red — the newest 500
    // were all from that session.
    const { total } = listRecordings({ limit: 1 })
    const rows = listRecordings({ limit: total }).rows
    const observed = rows.filter(r => r.encObserved !== null)
    expect(observed.length).toBeGreaterThan(0)
    // Only the four states the classifier can produce.
    for (const r of observed) {
      expect(['clear', 'encrypted', 'mixed']).toContain(r.encObserved)
    }
  })
})

describe('listTalkgroups', () => {
  it('returns the Baton Rouge area subset by default', () => {
    // 601 is also the size make_whitelist.py selects for the BR area.
    const { rows } = listTalkgroups()
    expect(rows).toHaveLength(601)
  })

  it('returns the whole system for area=all', () => {
    const { rows, total } = listTalkgroups({ area: 'all' })
    expect(rows).toHaveLength(4163)
    expect(total).toBe(4163)
  })

  it('reports the real encryption vocabulary, which has no "encrypted"', () => {
    const all = listTalkgroups({ area: 'all' }).rows
    expect(new Set(all.map(t => t.enc))).toEqual(new Set(['clear', 'partial', 'full']))
    expect(listTalkgroups({ area: 'all', enc: 'clear' }).rows).toHaveLength(3193)
    expect(listTalkgroups({ area: 'all', enc: 'partial' }).rows).toHaveLength(114)
    expect(listTalkgroups({ area: 'all', enc: 'full' }).rows).toHaveLength(856)
  })

  it('synthesizes tgid, which the source JSON does not carry as a field', () => {
    const brpd = listTalkgroups({ area: 'all', search: '17165' }).rows[0]
    expect(brpd.tgid).toBe(17165)
    expect(brpd.alpha).toBe('17-BRPD DSP1')
    expect(brpd.enc).toBe('partial')
    expect(brpd.tag).toBe('Law Dispatch')
    expect(brpd.mode).toBe('D enc')   // "enc" suffix marks an encrypted talkgroup
  })

  it('searches tgid, alpha, description, category and tag', () => {
    expect(listTalkgroups({ area: 'all', search: 'brpd' }).rows.length).toBeGreaterThan(0)
    expect(listTalkgroups({ area: 'all', search: 'law dispatch' }).rows.length).toBeGreaterThan(0)
    expect(listTalkgroups({ area: 'all', search: 'zzzznotathing' }).rows).toHaveLength(0)
  })

  it('returns rows ordered by tgid', () => {
    const rows = listTalkgroups({ area: 'all' }).rows
    for (let i = 1; i < rows.length; i++) {
      expect(rows[i].tgid).toBeGreaterThan(rows[i - 1].tgid)
    }
  })
})

describe('listCategories', () => {
  it('returns the distinct category strings', () => {
    const cats = listCategories()
    expect(cats.length).toBeGreaterThan(200)
    expect(cats).toContain('East Baton Rouge Parish (17) - Baton Rouge Police')
  })
})

describe('listRecordings', () => {
  it('returns every recording, newest first', () => {
    // NOT an exact count. Every recording session adds rows, so a hard number
    // here goes red the moment the radio runs — the same mistake an earlier
    // whitelist test made. Assert the invariants: there is a substantial
    // corpus, and it is ordered newest-first.
    //
    // `rows` is a PAGE of `total`, never all of it: listRecordings applies a
    // default limit of 5000. Comparing rows.length against the unfiltered
    // total was the same class of bug this comment warns about, one line
    // lower — it held only while the corpus stayed under the cap and went red
    // when the 5,000th call landed. Ask for a page big enough to hold it.
    const { total } = listRecordings({ limit: 1 })
    const { rows } = listRecordings({ limit: total })
    expect(total).toBeGreaterThanOrEqual(3240)
    expect(rows).toHaveLength(total)
    for (let i = 1; i < rows.length; i++) {
      expect(rows[i].start).toBeLessThanOrEqual(rows[i - 1].start)
    }
  })

  it('agrees with the .wav files on disk', () => {
    // The real invariant: every recording on disk is indexed, and the index
    // invents nothing. This is what the destructive calls.json write broke.
    const wavs = readdirSync(join(sdrRoot(), 'recordings')).filter(f => f.endsWith('.wav'))
    expect(listRecordings().total).toBe(wavs.length)
  })

  it('joins talkgroup metadata rather than resolving it per row in JS', () => {
    const one = countQueries(() => listRecordings({ tgid: 17165, limit: 1 }))
    expect(one.result.rows[0].alpha).toBe('17-BRPD DSP1')
    expect(one.result.rows[0].enc).toBe('partial')
    expect(one.result.rows[0].desc).not.toBeNull()

    // THE PROPERTY IN THE NAME, which the three assertions above do not test:
    // they pass just as well against an implementation that looks each row's
    // talkgroup up with its own query. Asserted as INVARIANCE rather than as
    // a magic number, because the count itself (a COUNT(*) plus the page) is
    // an implementation detail while "does not grow with the page size" is
    // exactly the claim.
    const many = countQueries(() => listRecordings({ tgid: 17165, limit: 50 }))
    expect(many.result.rows.length).toBeGreaterThan(one.result.rows.length)
    expect(many.runs).toBe(one.runs)
  })

  it('filters by encryption using the real vocabulary', () => {
    expect(listRecordings({ enc: 'clear' }).total).toBeGreaterThan(0)
    expect(listRecordings({ enc: 'full' }).total).toBeGreaterThan(0)
    // 'encrypted' is not a value in this data; it must match nothing rather
    // than throwing or silently matching everything.
    expect(listRecordings({ enc: 'encrypted' }).total).toBe(0)
  })

  it('finds transcripts through the FTS index', () => {
    // Substring search over 3,220 transcripts used to run in the browser on
    // every keystroke; this is an index lookup.
    const { total } = listRecordings({ search: 'looking' })
    expect(total).toBeGreaterThan(0)
  })

  it('survives FTS-hostile input instead of erroring', () => {
    // A bare quote or operator is an FTS5 syntax error, not a no-match, so the
    // query builder has to neutralise them.
    for (const s of ['"', "it's", 'a AND', '*', '(', '^foo', 'a-b']) {
      expect(() => listRecordings({ search: s })).not.toThrow()
    }
  })

  it('matches on alpha as well as transcript text', () => {
    expect(listRecordings({ search: 'BRPD' }).total).toBeGreaterThan(0)
  })

  it('paginates', () => {
    const first = listRecordings({ limit: 10 })
    const second = listRecordings({ limit: 10, offset: 10 })
    expect(first.rows).toHaveLength(10)
    expect(second.rows).toHaveLength(10)
    expect(first.rows[0].file).not.toBe(second.rows[0].file)
    expect(first.total).toBe(second.total)   // total ignores the window
  })
})

describe('getRecording', () => {
  it('returns one row by filename', () => {
    const sample = listRecordings({ limit: 1 }).rows[0]
    const one = getRecording(sample.file)
    expect(one?.file).toBe(sample.file)
  })

  it('returns null for an unknown file rather than throwing', () => {
    expect(getRecording('TG1_nope_20260101-000000.wav')).toBeNull()
  })
})

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
    expect(viaCode).toBeGreaterThan(0)
    expect(viaCode).toBeLessThanOrEqual(viaSearch)
  })

  it('combines the code filter with a talkgroup filter', () => {
    // tgid 17170 has ZERO 10-4 mentions (measured against the live corpus),
    // which would let this test pass vacuously against an empty array. 17330
    // has 18, so the loop body is actually exercised.
    const { rows } = listRecordings({ code: '10-4', tgid: 17330 })
    expect(rows.length).toBeGreaterThan(0)
    for (const r of rows) expect(r.tgid).toBe(17330)
  })

  it('ships mentions with offsets into transcriptNorm', () => {
    const { rows } = listRecordings({ code: '10-42', limit: 1 })
    const rec = rows[0]
    const m = rec.codes.find(c => c.canonical === '10-42')
    expect(m).toBeDefined()
    // scripts/tencodes.py's extract() rewrites transcript_norm by substituting
    // the CANONICAL form in place of whatever raw text matched (e.g. "1042"
    // in the input becomes "10-42" in the output), and off_start/off_end are
    // computed against that rewritten output, not the input. So the offsets
    // bound `canonical`, not `raw` — asserting m.raw here would fail for any
    // mention whose raw surface form differs from its canonical one (as the
    // newest 10-42 mention in this corpus, "1042", currently does).
    expect(rec.transcriptNorm).not.toBeNull()
    expect(rec.transcriptNorm!.slice(m!.offStart, m!.offEnd)).toBe(m!.canonical)
  })

  it('counts codes, most frequent first', () => {
    const stats = codeStats({})
    expect(stats.length).toBeGreaterThan(0)
    for (let i = 1; i < stats.length; i++) {
      expect(stats[i - 1].mentions).toBeGreaterThanOrEqual(stats[i].mentions)
    }
  })

  it('excludes medium-confidence mentions by default', () => {
    const dflt = codeStats({})
    const all = codeStats({ minConfidence: 'low' })
    const sum = (s: { mentions: number }[]) => s.reduce((a, b) => a + b.mentions, 0)
    // Strict: the live corpus has non-high-confidence mentions today, so a
    // default that actually excludes them must yield a strictly smaller sum,
    // not merely a smaller-or-equal one that could pass with nothing excluded.
    expect(sum(dflt)).toBeLessThan(sum(all))
  })

  it('existing transcript search is unchanged', () => {
    expect(listRecordings({ search: 'suspicious' }).rows.length).toBeGreaterThan(0)
  })
})

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
    // The bay's archive search (composables/useArchive.ts) depends on this and
    // passes no cursor.
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

  it('carries an encryption verdict on every followed talkgroup', () => {
    // The gap this closes: the strip for a CALL has labelled encryption since
    // 6b0e5e0, but the talkgroup list said nothing, so the operator learnt
    // that BRPD Dispatch is encrypted by arming it and hearing silence.
    const rows = followedTalkgroups()
    for (const r of rows) {
      expect(['clear', 'partial', 'encrypted', 'unknown']).toContain(r.encryption.state)
      expect(['observed', 'listed', 'none']).toContain(r.encryption.basis)
    }
  })

  it('labels BRPD dispatch as partially encrypted from its own traffic', () => {
    // 17165 17-BRPD DSP1: 47 ADP against 79 clear at time of writing. Both
    // halves matter — 'clear' would be the bug, 'encrypted' would write off 79
    // playable calls. Skipped rather than failed if the running capture's
    // preset does not follow it: the whitelist is what this function reads.
    const row = followedTalkgroups().find(r => r.tgid === 17165)
    if (!row) return
    expect(row.encryption.basis).toBe('observed')
    expect(row.encryption.state).toBe('partial')
    expect(row.encryption.encCalls).toBeGreaterThan(0)
    expect(row.encryption.knownCalls).toBeGreaterThan(row.encryption.encCalls)
  })

  it('does not dilute the ratio with the 77% of calls that carry no ESS', () => {
    // The measured trap: counted into the denominator, BRPD DSP1's 0.373 reads
    // 0.076 and prints as a nearly-clear channel.
    const row = followedTalkgroups().find(r => r.tgid === 17165)
    if (!row) return
    const totalCalls = listRecordings({ tgid: 17165, limit: 1 }).total
    expect(row.encryption.knownCalls).toBeLessThan(totalCalls)
  })
})

describe('whitelistTgids', () => {
  it('is the one parser for the whitelist file', () => {
    // Three call sites used to read this file three different ways. A row
    // marked in-whitelist by one and out by another is exactly the lie the
    // roster search exists to prevent.
    const ids = whitelistTgids()
    expect(ids.length).toBeGreaterThan(0)
    for (const id of ids) {
      expect(Number.isInteger(id)).toBe(true)
      expect(id).toBeGreaterThan(0)
    }
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('agrees with followedTalkgroups, which is built from it', () => {
    expect(followedTalkgroups().map(r => r.tgid).sort((a, b) => a - b))
      .toEqual([...whitelistTgids()].sort((a, b) => a - b))
  })
})

describe('talkgroupEncryptionTallies', () => {
  it('rolls the whole corpus up in ONE query, not one per talkgroup', () => {
    // Not a style preference: /api/listen/followed polls every 20s over 222
    // whitelisted talkgroups, and roster search can match thousands of 4,163.
    const { result: tallies, runs, sql } = countQueries(
      () => talkgroupEncryptionTallies(),
    )
    expect(tallies.size).toBeGreaterThan(0)
    // THE PROPERTY IN THE NAME. One statement executed, however many
    // talkgroups came back — so the cost does not scale with the roster.
    expect(runs).toBe(1)
    expect(sql[0]).toContain('GROUP BY tgid')
    // And the one query really did roll up many talkgroups, so `runs === 1`
    // cannot be satisfied by a query that returns one row.
    expect(tallies.size).toBeGreaterThan(50)
    // Every talkgroup that has ever been recorded, and no more.
    expect(tallies.size).toBeLessThan(listTalkgroups({ area: 'all' }).total)
  })

  it('buckets each call the way callEncryption.ts classifies it', () => {
    const tallies = talkgroupEncryptionTallies()
    let adp = 0
    let unhandled = 0
    for (const t of tallies.values()) {
      adp += t.adp
      unhandled += t.unhandled
      expect(t.adp + t.clear + t.unhandled + t.unknown).toBeGreaterThan(0)
    }
    // Measured: 0xAA is in real use here, and the one-off algids are a handful
    // of bit errors rather than a second algorithm.
    expect(adp).toBeGreaterThan(100)
    // A RATIO, not the absolute `toBeLessThan(20)` this used to be. A capture
    // is always running, so bit errors accumulate; an absolute ceiling on them
    // was a test that would go red on corpus growth alone, with no code change
    // anywhere — and the obvious "fix" for a red bound is to raise it, which
    // would eventually relax the check past the point of noticing a genuine
    // second algorithm. The SHAPE is what the claim rests on: the one-off
    // algids are noise against ADP, not a rival.
    expect(unhandled).toBeLessThan(adp * 0.1)
  })

  it('never badges a talkgroup off a corrupted ESS alone', () => {
    // The claim excluding `unhandled` from the ratio rests on: every talkgroup
    // carrying a bit-error algid also carries real ADP, so excluding them
    // relabels nothing today and only stops the next flipped bit from
    // inventing a warning. Asserted rather than assumed.
    for (const [tgid, t] of talkgroupEncryptionTallies()) {
      if (t.unhandled === 0) continue
      // The failure message says WHAT WENT RED, because this one asserts a
      // property of the DATA rather than of the code. If it fires, nothing in
      // this repo is broken: a talkgroup is carrying non-ADP algids and no ADP
      // at all, which is what a genuine second algorithm on this system would
      // look like. The response is to go and classify it in
      // utils/callEncryption.ts — NOT to relax this bound.
      expect(
        t.adp,
        `talkgroup ${tgid} has ${t.unhandled} call(s) with a non-ADP algid and `
        + `NO ADP calls. utils/talkgroupEncryption.ts excludes 'unhandled' from `
        + `both sides of its ratio on the argument that every such talkgroup `
        + `also carries real ADP, so those algids are bit errors. That argument `
        + `no longer holds for this talkgroup. This is a fact about the corpus, `
        + `not a code regression: look at the algids on tgid ${tgid} before `
        + `touching either file.`,
      ).toBeGreaterThan(0)
    }
  })

  it('leaves never-recorded talkgroups absent rather than present with zeros', () => {
    // A miss must mean 'unknown', not 'clear' — the distinction the whole
    // rollup exists to preserve.
    const tallies = talkgroupEncryptionTallies()
    const all = listTalkgroups({ area: 'all', limit: 4163 }).rows
    const unheard = all.find(t => !tallies.has(t.tgid))
    expect(unheard).toBeDefined()
    expect(unheard?.encryption.basis).not.toBe('observed')
  })
})

describe('roster search', () => {
  it('finds a talkgroup by id across the whole roster', () => {
    // 24-PPD DISP is outside the BR area keywords, so the default view cannot
    // reach it — the gap this feature closes.
    const { rows } = listTalkgroups({ area: 'all', search: '19014' })
    expect(rows.map(r => r.tgid)).toContain(19014)
  })

  it('finds talkgroups by name', () => {
    const { rows } = listTalkgroups({ area: 'all', search: 'brpd dsp' })
    expect(rows.length).toBeGreaterThan(1)
    for (const r of rows) {
      expect(`${r.tgid} ${r.alpha} ${r.desc} ${r.cat} ${r.tag}`.toLowerCase())
        .toContain('brpd dsp')
    }
  })

  it('marks every result in or out of the running capture', () => {
    // A result outside the whitelist can never produce a clip. Arming it would
    // be selecting into permanent silence with no explanation anywhere.
    const whitelisted = new Set(whitelistTgids())
    const { rows } = listTalkgroups({ area: 'all', search: 'disp' })
    expect(rows.length).toBeGreaterThan(0)
    for (const r of rows) {
      expect(r.inWhitelist).toBe(whitelisted.has(r.tgid))
    }
    // Both cases actually occur in this result set, so the flag is load-bearing.
    expect(rows.some(r => r.inWhitelist)).toBe(true)
    expect(rows.some(r => !r.inWhitelist)).toBe(true)
  })

  it('caps returned rows but still reports how many matched', () => {
    // "Showing 5 of 900" versus implying the roster holds five.
    const all = listTalkgroups({ area: 'all', search: 'disp' })
    const capped = listTalkgroups({ area: 'all', search: 'disp', limit: 5 })
    expect(all.matched).toBeGreaterThan(5)
    expect(capped.rows).toHaveLength(5)
    expect(capped.matched).toBe(all.matched)
  })

  it('labels the most-encrypted talkgroup on the system as encrypted', () => {
    // 19014 24-PPD DISP: 62 ADP calls against 1 clear. The roster lists it
    // CLEAR, which is why observation has to win.
    const row = listTalkgroups({ area: 'all', search: '19014' }).rows
      .find(r => r.tgid === 19014)
    expect(row).toBeDefined()
    expect(row?.enc).toBe('clear')                     // the roster's claim
    expect(row?.encryption.state).toBe('encrypted')    // what was on the air
    expect(row?.encryption.basis).toBe('observed')
  })

  it('falls back to the roster label, marked as hearsay, when nothing was heard', () => {
    // 4,044 of 4,163 talkgroups have never been recorded. A search that
    // returned no encryption hint for a listed-encrypted tac channel would be
    // the same silent failure this feature exists to fix.
    const listedEnc = listTalkgroups({ area: 'all', enc: 'full', limit: 500 }).rows
      .filter(r => r.encryption.basis === 'listed')
    expect(listedEnc.length).toBeGreaterThan(0)
    for (const r of listedEnc) {
      expect(r.encryption.state).toBe('encrypted')
      expect(r.encryption.encRatio).toBeNull()
    }
  })

  it('does not let a listed-clear label stand in for evidence', () => {
    const unheardClear = listTalkgroups({ area: 'all', enc: 'clear', limit: 500 }).rows
      .filter(r => r.encryption.basis !== 'observed')
    expect(unheardClear.length).toBeGreaterThan(0)
    for (const r of unheardClear) {
      expect(r.encryption.state).toBe('unknown')
      expect(r.encryption.basis).toBe('none')
    }
  })
})

describe('transcriptionHealth', () => {
  /**
   * Every describe block above this one runs against the REAL sdr.db, on
   * purpose (see the file header). That approach does not work here: a live
   * capture and its transcriber are running against this same corpus right
   * now, so "a call that ended 10s ago" or "a call stuck past its grace
   * cutoff" cannot be measured against live rows without racing real time and
   * flaking by construction — and this task's own constraints forbid pointing
   * a test at the live sdr.db regardless.
   *
   * transcriptionHealth() has no seam for a different database — it goes
   * through the same getDb() singleton as every other query in this file — so
   * the seam used here is the one getDb() already reads: SDR_ROOT. A tiny,
   * throwaway sdr.db is built in an OS temp directory, SDR_ROOT is pointed at
   * it only for the lifetime of this describe block, and closeDb() drops the
   * cached connection so the next getDb() call reopens against the new path.
   * Every test above and below this block is unaffected once it is restored.
   *
   * The fixture file is built with a WRITABLE connection and getDb() (which
   * opens read-only) is only ever pointed at it once that file already exists
   * on disk. Opening a read-only connection to a path that does not exist is
   * exactly the silent-file-creation trap this task's constraints call out —
   * so schema and rows are written and the writer closed *before* SDR_ROOT
   * is touched.
   */
  const nodeRequire = createRequire(import.meta.url)
  const { DatabaseSync } = nodeRequire('node:sqlite') as typeof import('node:sqlite')

  const fixtureDir = mkdtempSync(join(tmpdir(), 'transcription-health-'))
  const originalSdrRoot = process.env.SDR_ROOT

  // One fixed corpus, several `now` values, rather than one shared window.
  // transcriptionHealth()'s WHERE clause bounds `ended_at` only from BELOW
  // (`> :since`) — correct in production, where a call can never end after
  // the clock `now` is read from, so there is no reason to also bound it
  // above. But it means "the window" here is [now - windowSec, +inf), not a
  // closed interval: a `now` chosen too early would let a stale row inside
  // its cutoff sneak back into view instead of aging out of it. So each test
  // picks a `now` late enough, relative to this fixed corpus, that only the
  // rows it means to exercise land on the counted side of `since`.
  const BASE = 1_800_000_000
  const FAR_PAST = BASE + 1_000 // outside every window below; never counted
  const OLD_AWAITING = BASE + 3_000 // no transcript — the real backlog row
  const SILENT = BASE + 3_001 // transcript '' (whisper heard silence)
  const TRANSCRIBED = BASE + 3_002 // transcript present
  const GRACE = BASE + 5_000 // no transcript, but newest — inside its grace period

  beforeAll(() => {
    const dbFile = join(fixtureDir, 'sdr.db')
    const setup = new DatabaseSync(dbFile)
    // Only the columns transcriptionHealth() reads, plus transcript_norm:
    // getDb()'s migration guard checks for that column on every database it
    // opens (see db.ts), real corpus or not.
    setup.exec(`
      CREATE TABLE calls (
        id INTEGER PRIMARY KEY,
        ended_at INTEGER,
        transcript TEXT,
        transcript_norm TEXT
      )
    `)
    const insert = setup.prepare(
      'INSERT INTO calls (ended_at, transcript) VALUES (:ended_at, :transcript)',
    )
    insert.run({ ended_at: FAR_PAST, transcript: null })
    insert.run({ ended_at: OLD_AWAITING, transcript: null })
    insert.run({ ended_at: SILENT, transcript: '' })
    insert.run({ ended_at: TRANSCRIBED, transcript: 'ten four' })
    insert.run({ ended_at: GRACE, transcript: null })
    setup.close()

    process.env.SDR_ROOT = fixtureDir
    closeDb()
  })

  afterAll(() => {
    closeDb()
    // Assigning the string "undefined" back onto an unset var would point
    // every later test in this file at /undefined/sdr.db.
    if (originalSdrRoot === undefined) delete process.env.SDR_ROOT
    else process.env.SDR_ROOT = originalSdrRoot
    rmSync(fixtureDir, { recursive: true, force: true })
  })

  it('reports idle when no calls ended in the window', () => {
    // `now` is later than every fixture row by more than windowSec, so
    // `since` clears all five of them and the window is genuinely empty.
    const h = transcriptionHealth(600, 300, GRACE + 5_000)
    expect(h.recentCalls).toBe(0)
    expect(h.awaiting).toBe(0)
  })

  it('does not count a call still inside its grace period', () => {
    // GRACE ended 10s before `now`: inside the 600s window, nowhere near the
    // 300s grace cutoff. A call with no transcript yet is the NORMAL state
    // this soon after it ends, not evidence of a backlog.
    const h = transcriptionHealth(600, 300, GRACE + 10)
    expect(h.awaiting).toBe(0)
  })

  it('counts a call that ended before the grace cutoff and has no transcript', () => {
    // `now` is 400s after OLD_AWAITING ended — past the 300s cutoff — while
    // FAR_PAST stays outside the 600s window entirely and GRACE has not
    // happened yet relative to this `now`.
    const h = transcriptionHealth(600, 300, OLD_AWAITING + 400)
    expect(h.awaiting).toBeGreaterThan(0)
    expect(h.oldestAwaitingSec).toBeGreaterThan(300)
  })

  it('does not count a silent or already-transcribed call as awaiting', () => {
    // SILENT and TRANSCRIBED are exactly as stale as OLD_AWAITING and land in
    // the same window and past the same cutoff, but transcript IS NOT NULL
    // for either — an empty string is a completed attempt (whisper heard
    // nothing), not a pending one, matching recordingsSummary()'s comment
    // above. Without that IS NULL guard this would read 3, not 1.
    const h = transcriptionHealth(600, 300, OLD_AWAITING + 400)
    expect(h.awaiting).toBe(1)
  })
})

describe('listRecordings encState', () => {
  // These run against the REAL corpus, which grows whenever the radio runs, so
  // every assertion here is an INVARIANT rather than a count — the same rule
  // the listRecordings block above spells out.
  //
  // CLEAR_ALGID is 128. A call carries a non-clear algid, exactly 128, or no
  // algid at all (no ESS captured, which is ~77% of this corpus).

  it('partitions the corpus: open + encrypted === all', () => {
    // The property that makes the filter trustworthy. If a future definition
    // drops the NULL bucket from 'open' — the tempting simplification, since
    // "open" sounds like "algid = 128" — this goes red immediately, because
    // those ~10,000 calls would belong to neither half.
    const all = listRecordings({ limit: 1 }).total
    const open = listRecordings({ limit: 1, encState: 'open' }).total
    const enc = listRecordings({ limit: 1, encState: 'encrypted' }).total
    expect(open + enc).toBe(all)
    expect(open).toBeGreaterThan(0)
    expect(enc).toBeGreaterThan(0)
  })

  it("'all' is the same as no filter at all", () => {
    expect(listRecordings({ limit: 1, encState: 'all' }).total)
      .toBe(listRecordings({ limit: 1 }).total)
  })

  it("'encrypted' returns only calls the radio marked encrypted", () => {
    const rows = listRecordings({ limit: 200, encState: 'encrypted' }).rows
    expect(rows.length).toBeGreaterThan(0)
    for (const r of rows) {
      expect(r.algid).not.toBeNull()
      expect(r.algid).not.toBe(CLEAR_ALGID)
    }
  })

  it("'open' returns only calls NOT known to be encrypted, NULLs included", () => {
    const rows = listRecordings({ limit: 400, encState: 'open' }).rows
    expect(rows.length).toBeGreaterThan(0)
    for (const r of rows) {
      expect(r.algid === null || r.algid === CLEAR_ALGID).toBe(true)
    }
    // The NULL half must actually be present, not merely permitted: 'open'
    // meaning `algid = 128` alone would still pass every assertion above while
    // hiding most of the audible traffic.
    expect(rows.some(r => r.algid === null)).toBe(true)
  })
})
