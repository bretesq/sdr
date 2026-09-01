import { describe, it, expect, beforeAll } from 'vitest'
import { existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { sdrRoot } from './paths'
import { listRecordings, getRecording, listTalkgroups, listCategories, codeStats } from './queries'
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
    const { rows, total } = listRecordings()
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
