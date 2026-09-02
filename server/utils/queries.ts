import { getDb } from './db'
import type { CallRow, TalkgroupRow } from './db'

/**
 * All database reads live here, so the API routes stay thin and every query is
 * in one place to review.
 *
 * Two things moved from the client to the database in this layer:
 *
 *   Transcript search. The list endpoint used to ship all 3,220 transcripts so
 *   the browser could run String.includes over them per keystroke. It is now an
 *   FTS5 MATCH.
 *
 *   Talkgroup joins. `alpha`/`desc`/`cat`/`enc` used to be resolved per call in
 *   JavaScript against a JSON blob; they are now a join.
 */

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

/** Shape the API returns for a recording. `desc` rather than `description`. */
export interface Recording {
  /** Assigned at commit; monotonic across the recorder processes. The live
   *  feed cursors on this rather than on a timestamp — see toRecording below. */
  id: number
  file: string
  tgid: number | null
  alpha: string | null
  desc: string | null
  cat: string | null
  enc: 'clear' | 'partial' | 'full' | null
  start: number
  dur: number
  /** Unix seconds at which the recorder closed this call's WAV. Staleness in
   *  the live feed is measured from here, never from `start`. */
  endedAt: number | null
  transcript: string | null
  transcriptNorm: string | null
  codes: CodeMention[]
  srcAddr: number | null
  algid: number | null
  algorithm: string | null
  keyid: number | null
  // What THIS call carried, harvested from op25's ESS or proven by intelligible
  // speech. `enc` above is the scraped RadioReference label for the talkgroup,
  // which describes how it is documented rather than what it transmitted.
  encObserved: string | null
  encEvidence: string | null
  encSource: string | null
  // True when a human reviewed this talkgroup's class from observed traffic.
  encOverridden: boolean
  site: string | null
  freq: number | null
}

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

  // Chunked: listRecordings defaults to limit 5000, and a 5,000-placeholder
  // IN clause is at the mercy of SQLITE_MAX_VARIABLE_NUMBER, which is a
  // build-time setting. 500 is comfortably under every default.
  const CHUNK = 500
  const rows: CodeRow[] = []
  for (let i = 0; i < files.length; i += CHUNK) {
    const batch = files.slice(i, i + CHUNK)
    const placeholders = batch.map(() => '?').join(',')
    rows.push(...getDb().prepare(
      `SELECT c.file, cc.raw, cc.canonical, cc.kind, cc.meaning, cc.confidence,
              cc.off_start, cc.off_end
         FROM call_codes cc
         JOIN calls c ON c.id = cc.call_id
        WHERE c.file IN (${placeholders})
        ORDER BY c.file, cc.off_start`,
    ).all(...batch) as unknown as CodeRow[])
  }

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
    transcriptNorm: r.transcript_norm,
    codes,
    srcAddr: r.src_addr,
    algid: r.algid,
    algorithm: r.algorithm,
    keyid: r.keyid,
    encObserved: r.enc_observed,
    encEvidence: r.enc_evidence,
    encSource: r.enc_source,
    encOverridden: Boolean(r.enc_overridden),
    site: r.site_name,
    freq: r.freq,
  }
}

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

/**
 * FTS5 needs its input sanitised: a bare apostrophe or an unbalanced quote is a
 * syntax error, not a no-match. Each word becomes its own prefix term so
 * "brpd disp" behaves like the substring search the UI replaced.
 */
function ftsQuery(search: string): string {
  const terms = search
    .toLowerCase()
    .replace(/["*():^-]/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .map(t => `"${t}"*`)
  return terms.join(' AND ')
}

/**
 * Render a list of talkgroup ids as a SQL literal list.
 *
 * Inlined rather than bound as placeholders because the whitelist can hold
 * every talkgroup in the system (preset "all" is 4,163), which overruns the
 * 999-parameter limit on older SQLite builds. Safe because every element is
 * proven to be a finite integer here and the function throws otherwise —
 * nothing string-shaped can reach the query.
 */
function intList(values: number[]): string {
  for (const v of values) {
    if (!Number.isInteger(v)) throw new TypeError(`Not an integer id: ${String(v)}`)
  }
  return values.join(',')
}

export function listRecordings(q: RecordingQuery = {}): { rows: Recording[], total: number, maxId: number } {
  const db = getDb()
  const where: string[] = []
  const params: (string | number)[] = []

  if (q.tgid !== undefined) {
    where.push('c.tgid = ?')
    params.push(q.tgid)
  }

  if (q.tgids !== undefined) {
    // An empty selection matches nothing. `1 = 0` rather than an early return
    // so `total` and `maxId` below are still computed the same way.
    where.push(q.tgids.length ? `c.tgid IN (${intList(q.tgids)})` : '1 = 0')
  }

  if (q.afterId !== undefined) {
    where.push('c.id > ?')
    params.push(q.afterId)
  }

  if (q.enc && q.enc !== 'all') {
    if (q.enc === 'none') {
      where.push('t.enc IS NULL')
    } else {
      where.push('t.enc = ?')
      params.push(q.enc)
    }
  }

  if (q.search?.trim()) {
    const raw = q.search.trim()
    const fts = ftsQuery(raw)
    // Transcript hits come from the FTS index; the other five fields are a
    // plain LIKE, which is what the old client-side search did across
    // tgid/alpha/desc/cat/file.
    const like = `%${raw.toLowerCase()}%`
    if (fts) {
      where.push(`(
        c.id IN (SELECT rowid FROM calls_fts WHERE calls_fts MATCH ?)
        OR CAST(c.tgid AS TEXT) LIKE ?
        OR LOWER(t.alpha) LIKE ?
        OR LOWER(t.description) LIKE ?
        OR LOWER(t.cat) LIKE ?
        OR LOWER(c.file) LIKE ?
      )`)
      params.push(fts, like, like, like, like, like)
    }
  }

  if (q.code) {
    // Exact match on an indexed column. FTS5 strips punctuation and splits
    // "10-50" into "10" and "50", so codes cannot be filtered through it.
    where.push('EXISTS (SELECT 1 FROM call_codes cc WHERE cc.call_id = c.id AND cc.canonical = ?)')
    params.push(q.code)
  }

  const clause = where.length ? `WHERE ${where.join(' AND ')}` : ''

  const total = db.prepare(
    `SELECT COUNT(*) AS n FROM calls c LEFT JOIN talkgroups t ON t.tgid = c.tgid ${clause}`,
  ).get(...params) as { n: number }

  const limit = q.limit ?? 5000
  const offset = q.offset ?? 0

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

  const maxRow = db.prepare(
    'SELECT COALESCE(MAX(id), 0) AS n FROM calls',
  ).get() as { n: number }

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
}

export function getRecording(file: string): Recording | null {
  const db = getDb()
  const row = db.prepare(`${CALL_SELECT} WHERE c.file = ?`).get(file) as unknown as CallRow | undefined
  if (!row) return null
  return toRecording(row, codesFor([row.file]).get(row.file) ?? [])
}

// ------------------------------------------------------------- talkgroups

export interface Talkgroup {
  tgid: number
  alpha: string
  desc: string
  cat: string
  tag: string
  enc: 'clear' | 'partial' | 'full'
  mode: string
  inWhitelist?: boolean
}

/**
 * Categories in this DB are full strings like
 * "East Baton Rouge Parish (17) - Baton Rouge Police", so area selection is a
 * substring match, mirroring scripts/make_whitelist.py. Selects 601 of 4,163.
 */
const BR_AREA_KEYWORDS = [
  'East Baton Rouge', 'Baton Rouge', 'LSU', 'Southern University',
  'State Police - Troop A', 'West Baton Rouge', 'Livingston', 'Ascension',
  'Iberville', 'Feliciana', 'Pointe Coupee', 'EMS Agencies',
  'Wildlife and Fisheries',
]

export interface TalkgroupQuery {
  area?: 'br' | 'all'
  category?: string
  enc?: string
  search?: string
}

export function listTalkgroups(q: TalkgroupQuery = {}): { rows: Talkgroup[], total: number } {
  const db = getDb()
  const where: string[] = []
  const params: string[] = []

  if ((q.area ?? 'br') === 'br') {
    where.push(`(${BR_AREA_KEYWORDS.map(() => 'cat LIKE ?').join(' OR ')})`)
    params.push(...BR_AREA_KEYWORDS.map(k => `%${k}%`))
  }
  if (q.category) {
    where.push('cat = ?')
    params.push(q.category)
  }
  if (q.enc && q.enc !== 'all') {
    where.push('enc = ?')
    params.push(q.enc)
  }
  if (q.search?.trim()) {
    const like = `%${q.search.trim().toLowerCase()}%`
    where.push(`(CAST(tgid AS TEXT) LIKE ? OR LOWER(alpha) LIKE ?
                 OR LOWER(description) LIKE ? OR LOWER(cat) LIKE ? OR LOWER(tag) LIKE ?)`)
    params.push(like, like, like, like, like)
  }

  const clause = where.length ? `WHERE ${where.join(' AND ')}` : ''
  const rows = db.prepare(
    `SELECT tgid, alpha, description, cat, tag, enc, mode
       FROM talkgroups ${clause} ORDER BY tgid`,
  ).all(...params) as unknown as TalkgroupRow[]

  const total = db.prepare('SELECT COUNT(*) AS n FROM talkgroups').get() as { n: number }

  return {
    rows: rows.map(r => ({
      tgid: r.tgid,
      alpha: r.alpha ?? '',
      desc: r.description ?? '',
      cat: r.cat ?? '',
      tag: r.tag ?? '',
      enc: (r.enc ?? 'clear') as 'clear' | 'partial' | 'full',
      mode: r.mode ?? '',
    })),
    total: total.n,
  }
}

export function listCategories(): string[] {
  const db = getDb()
  const rows = db.prepare(
    'SELECT DISTINCT cat FROM talkgroups WHERE cat IS NOT NULL ORDER BY cat',
  ).all() as unknown as { cat: string }[]
  return rows.map(r => r.cat)
}

/**
 * A cheap fingerprint of the recordings corpus, for the live SSE stream.
 *
 * Three aggregates over `calls`, no join and no scan of transcript text, so it
 * is cheap enough to evaluate on every change tick.
 *
 * `transcripts` counts non-NULL, and NULL now means exactly one thing: not yet
 * attempted. It used to also cover "attempted, but whisper heard silence",
 * because stt_watch.py skipped empty text — so `calls - transcripts` could never
 * reach zero and a caught-up corpus still reported a dozen outstanding. Empty
 * text is now stored as an empty string (it is what the .txt on disk says), so
 * the difference is a true pending backlog and does reach zero.
 *
 * `latest` lets a client tell "a new call arrived" from "an existing call
 * gained a transcript" without shipping any rows.
 */
export interface RecordingsSummary {
  calls: number
  transcripts: number
  latest: number | null
}

export function recordingsSummary(): RecordingsSummary {
  const db = getDb()
  const row = db.prepare(
    `SELECT COUNT(*)          AS calls,
            COUNT(transcript) AS transcripts,
            MAX(start)        AS latest
       FROM calls`,
  ).get() as unknown as { calls: number, transcripts: number, latest: number | null }
  return {
    calls: Number(row.calls),
    transcripts: Number(row.transcripts),
    latest: row.latest === null ? null : Number(row.latest),
  }
}

/**
 * SQLite's own cross-connection change counter.
 *
 * `PRAGMA data_version` is unchanged for commits made on the SAME connection
 * and changes when ANY OTHER connection commits — which is exactly our case:
 * udp_audio_record.py and stt_watch.py write from separate processes while this
 * one reads. Verified on this database: stable across re-reads on our
 * connection, 2 -> 3 after a python process committed.
 *
 * This is the whole reason the SSE stream needs no polling of the corpus
 * itself. SQLite has no cross-process change notification (sqlite3_update_hook
 * fires only for the connection that made the change), so the alternative was
 * re-running an aggregate every tick.
 */
export function dataVersion(): number {
  const db = getDb()
  const row = db.prepare('PRAGMA data_version').get() as unknown as { data_version: number }
  return Number(row.data_version)
}

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

interface CodeStatRow {
  canonical: string
  meaning: string | null
  kind: string
  calls: number
  mentions: number
}

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

  const rows = getDb().prepare(
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
  ).all(...params) as unknown as CodeStatRow[]

  // COUNT(*) and COUNT(DISTINCT ...) come back as bigint-safe numbers from
  // node:sqlite in practice, but recordingsSummary() above already treats
  // SQLite aggregates as needing an explicit Number() coercion; matching that
  // here keeps callers like the sum-of-mentions check in queries.test.ts safe
  // regardless of how node:sqlite happens to box an aggregate today.
  return rows.map(r => ({
    canonical: r.canonical,
    meaning: r.meaning,
    kind: r.kind,
    calls: Number(r.calls),
    mentions: Number(r.mentions),
  }))
}
