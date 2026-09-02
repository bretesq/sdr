import { describe, it, expect, beforeAll } from 'vitest'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { sdrRoot, whitelistPath } from './paths'
import {
  listRecordings, getRecording, listTalkgroups, listCategories, codeStats,
  followedTalkgroups,
} from './queries'
import { dbPath } from './db'

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
    const { rows } = listRecordings({ tgid: 17165, limit: 1 })
    expect(rows[0].alpha).toBe('17-BRPD DSP1')
    expect(rows[0].enc).toBe('partial')
    expect(rows[0].desc).not.toBeNull()
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
