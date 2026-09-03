import { readFileSync } from 'node:fs'
import { getDb } from './db'
import type { CallRow, TalkgroupRow } from './db'
import { whitelistPath } from './paths'
import {
  talkgroupEncryption, EMPTY_TALKGROUP_TALLY,
  type TalkgroupEncryptionTally, type TalkgroupEncryptionVerdict,
} from '../../utils/talkgroupEncryption'
import { ADP_ALGID, CLEAR_ALGID } from '../../utils/callEncryption'

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
  /**
   * The scraped RadioReference label, verbatim. Hearsay: measured against the
   * air it is wrong in both directions (24-PPD DISP is listed clear and runs
   * 62 ADP calls out of 63). Kept because TalkgroupBrowser filters and colours
   * on it and because it is the only thing we have for the 4,044 talkgroups
   * nothing has ever been recorded on — never as the answer on its own. See
   * `encryption` below and utils/talkgroupEncryption.ts.
   */
  enc: 'clear' | 'partial' | 'full'
  mode: string
  /**
   * What this talkgroup's own recorded calls say about its encryption, with
   * the roster label as a clearly-marked fallback. Derived server-side into a
   * closed shape — the browser gets counts and a verdict, never call rows and
   * never a key.
   */
  encryption: TalkgroupEncryptionVerdict
  /**
   * Whether the RUNNING capture's whitelist covers this talkgroup. False means
   * op25 is not recording it, so arming it in the bay could never produce a
   * clip — the reason search results carry this at all. Always populated.
   */
  inWhitelist: boolean
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

/**
 * The talkgroup ids the RUNNING capture's whitelist covers.
 *
 * ONE parser, deliberately. This file, server/api/talkgroups/whitelist.get.ts
 * and the roster search all need the same answer, and until this existed they
 * each read the file their own way — `split(/[\s,]/)[0]` with `isFinite` in the
 * route, bare `parseInt` with `isInteger && n > 0` here. Divergent parsing on
 * this path is not cosmetic: a row marked in-whitelist by one endpoint and
 * out-of-whitelist by another is precisely the lie the search feature exists to
 * prevent, and it would appear only on a malformed line nobody tests.
 *
 * The permissive split wins because op25's whitelist format allows a trailing
 * comment or a comma; `> 0` wins because tgid 0 is not a talkgroup.
 *
 * The file is NOT proof anything is running — it persists unchanged after a
 * session dies. Callers pair it with isRadioBusy(); see the routes.
 */
export function whitelistTgids(): number[] {
  let text: string
  try {
    text = readFileSync(whitelistPath(), 'utf-8')
  } catch {
    return []          // no session has ever run on this checkout
  }
  const ids = text
    .split('\n')
    .map(l => Number.parseInt(l.trim().split(/[\s,]/)[0], 10))
    .filter(n => Number.isInteger(n) && n > 0)
  // Deduplicated: the file is generated, but a hand-edited one with a repeated
  // id would otherwise inflate every count taken off this list.
  return [...new Set(ids)]
}

interface EncTallyRow {
  tgid: number
  adp: number
  clear: number
  unhandled: number
  unknown: number
}

/**
 * Every talkgroup's calls bucketed by what their ESS said, in ONE aggregate.
 *
 * A per-row query was the obvious shape and is the one thing this must not be:
 * the bay polls /api/listen/followed every 20 seconds with 222 whitelisted
 * talkgroups, and roster search can match thousands of the 4,163. This is a
 * single grouped scan of `calls` — 11,886 rows in, 119 rows out, measured at
 * 3.2ms — so the cost is the same whether the caller wants one talkgroup's
 * verdict or all of them.
 *
 * Talkgroups with no calls are simply absent from the map rather than present
 * with zeros; `EMPTY_TALKGROUP_TALLY` is what a miss means, and
 * talkgroupEncryption() turns it into 'unknown' rather than 'clear'.
 *
 * The bucket CASEs mirror utils/talkgroupEncryption.ts's tallyBucket() — that
 * function is what the tests classify against, and the two must not drift.
 */
export function talkgroupEncryptionTallies(): Map<number, TalkgroupEncryptionTally> {
  const db = getDb()
  const rows = db.prepare(
    `SELECT tgid,
            SUM(CASE WHEN algid = ? THEN 1 ELSE 0 END) AS adp,
            SUM(CASE WHEN algid = ? THEN 1 ELSE 0 END) AS clear,
            SUM(CASE WHEN algid IS NOT NULL
                      AND algid <> ? AND algid <> ? THEN 1 ELSE 0 END) AS unhandled,
            SUM(CASE WHEN algid IS NULL THEN 1 ELSE 0 END) AS unknown
       FROM calls
      WHERE tgid IS NOT NULL
      GROUP BY tgid`,
  ).all(ADP_ALGID, CLEAR_ALGID, ADP_ALGID, CLEAR_ALGID) as unknown as EncTallyRow[]

  return new Map(rows.map(r => [r.tgid, {
    adp: Number(r.adp),
    clear: Number(r.clear),
    unhandled: Number(r.unhandled),
    unknown: Number(r.unknown),
  }]))
}

export interface TalkgroupQuery {
  area?: 'br' | 'all'
  category?: string
  enc?: string
  search?: string
  /**
   * Cap on returned rows, for the bay's roster search — a two-character query
   * can match thousands of the 4,163 and the standby panel can show a few
   * dozen. `matched` still reports the full count so the UI can say so.
   * Omitted by TalkgroupBrowser, which wants every row it asked for.
   */
  limit?: number
}

export function listTalkgroups(q: TalkgroupQuery = {}): {
  rows: Talkgroup[]
  total: number
  matched: number
} {
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

  // Both taken once for the whole result set rather than per row: the rollup
  // is one aggregate (see talkgroupEncryptionTallies) and the whitelist is one
  // small file read. Doing either inside the map below would turn a search
  // that matches 900 rows into 900 queries or 900 file reads.
  const tallies = talkgroupEncryptionTallies()
  const whitelisted = new Set(whitelistTgids())

  // Limited in JS rather than with SQL LIMIT so `matched` needs no second
  // COUNT(*) over a duplicated WHERE clause — the unlimited ceiling here is
  // 4,163 rows, which TalkgroupBrowser already asks for on every load.
  const matched = rows.length
  const page = q.limit !== undefined ? rows.slice(0, q.limit) : rows

  return {
    rows: page.map((r) => {
      const enc = (r.enc ?? 'clear') as 'clear' | 'partial' | 'full'
      return {
        tgid: r.tgid,
        alpha: r.alpha ?? '',
        desc: r.description ?? '',
        cat: r.cat ?? '',
        tag: r.tag ?? '',
        enc,
        mode: r.mode ?? '',
        encryption: talkgroupEncryption(
          tallies.get(r.tgid) ?? EMPTY_TALKGROUP_TALLY,
          enc,
        ),
        inWhitelist: whitelisted.has(r.tgid),
      }
    }),
    total: total.n,
    matched,
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

export interface TranscriptionHealth {
  /** Calls that ended before the grace cutoff and still have no transcript. */
  awaiting: number
  /** Age in seconds of the oldest such call, or null when there are none. */
  oldestAwaitingSec: number | null
  /** Calls that ended anywhere in the window — the denominator. */
  recentCalls: number
}

/**
 * Whether transcription is actually keeping up, measured from its output.
 *
 * Deliberately NOT a probe of whisper. A wedged server answers GET / in 0.4ms
 * while hanging every inference — that is how a 26-hour outage went unnoticed —
 * and a probe cannot see a dead watcher or a vanished GPU at all. Those three
 * failures are indistinguishable to the operator, and counting calls that ended
 * without gaining a transcript catches all of them.
 *
 * `graceSec` exists because a call with no transcript is the NORMAL state for a
 * few seconds after it ends: the recorder finalises the .wav, then the watcher
 * picks it up. Only a call still untranscribed past that grace period is
 * evidence of anything.
 *
 * `transcript IS NULL` (not falsy, not empty) is load-bearing: whisper hearing
 * silence stores an empty string, which is a completed attempt, not a pending
 * one — see recordingsSummary() above for the same distinction. A call whose
 * audio is silence must not inflate the backlog count just because the string
 * is empty.
 *
 * The WHERE clause bounds `ended_at` only from below (`> :since`), not above.
 * That is intentional, not an oversight: on real data `ended_at` can never
 * exceed the wall clock this function reads `now` from, so an upper bound
 * would be dead weight on every call. (It does mean a caller who passes a
 * `now` from BEFORE some fixture row's `ended_at` gets that row back anyway —
 * harmless in production, but worth knowing if this is ever exercised against
 * synthetic data with an arbitrary clock.)
 */
export function transcriptionHealth(
  windowSec = 900,
  graceSec = 300,
  now: number = Date.now() / 1000,
): TranscriptionHealth {
  const db = getDb()
  const row = db.prepare(
    `SELECT COUNT(*) AS recentCalls,
            SUM(CASE WHEN transcript IS NULL AND ended_at < :cutoff THEN 1 ELSE 0 END)
              AS awaiting,
            MIN(CASE WHEN transcript IS NULL AND ended_at < :cutoff THEN ended_at END)
              AS oldestAwaiting
       FROM calls
      WHERE ended_at IS NOT NULL AND ended_at > :since`,
  ).get({ since: now - windowSec, cutoff: now - graceSec }) as unknown as {
    recentCalls: number
    awaiting: number | null
    oldestAwaiting: number | null
  }
  return {
    recentCalls: Number(row.recentCalls),
    awaiting: Number(row.awaiting ?? 0),
    oldestAwaitingSec: row.oldestAwaiting === null ? null : now - Number(row.oldestAwaiting),
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

// ---------------------------------------------------------- followed feed

/** A talkgroup the running session follows, with its recent activity. */
export interface FollowedTalkgroup {
  tgid: number
  alpha: string | null
  desc: string | null
  cat: string | null
  /** Calls in the trailing window. Display ordering only. */
  recentCalls: number
  /**
   * What this talkgroup's recorded calls say about its encryption — the whole
   * corpus, NOT the `sinceSec` window, because "is this channel encrypted" is
   * a property of the channel rather than of the last six hours, and a window
   * narrow enough to be current is too narrow to be evidence. Derived
   * server-side into a closed shape; see utils/talkgroupEncryption.ts.
   */
  encryption: TalkgroupEncryptionVerdict
}

interface TalkgroupMetaRow {
  tgid: number
  alpha: string | null
  description: string | null
  cat: string | null
  enc: 'clear' | 'partial' | 'full' | null
}

interface TgidCountRow {
  tgid: number
  n: number
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
 * Ranking is load-bearing rather than cosmetic: only a fraction of the
 * followed talkgroups produce a call in a given window, so unranked the live
 * ones sit below a wall of silent rows.
 */
export function followedTalkgroups(sinceSec = 6 * 3600): FollowedTalkgroup[] {
  const ids = whitelistTgids()
  if (!ids.length) return []          // no session has ever run on this checkout

  const db = getDb()

  const metaParams: (string | number)[] = []
  const metaClause = tgidInClause('tgid', ids, metaParams)
  const meta = db.prepare(
    `SELECT tgid, alpha, description, cat, enc FROM talkgroups WHERE ${metaClause}`,
  ).all(...metaParams) as unknown as TalkgroupMetaRow[]
  const byTgid = new Map(meta.map(m => [m.tgid, m]))

  // One aggregate for all 222 whitelisted talkgroups, not one per row: this
  // runs on a 20-second poll, so a per-row query would be 222 statements every
  // 20s for a fact that changes on the timescale of a shift. 3.2ms measured.
  const tallies = talkgroupEncryptionTallies()

  // cutoff must be pushed onto countParams BEFORE tgidInClause builds its
  // clause below — the builder appends to the array it is given in clause
  // order, so the bound values and the placeholders they fill must line up.
  const cutoff = Math.floor(Date.now() / 1000) - sinceSec
  const countParams: (string | number)[] = [cutoff]
  const countClause = tgidInClause('tgid', ids, countParams)
  const counts = db.prepare(
    `SELECT tgid, COUNT(*) AS n FROM calls
      WHERE start > ? AND ${countClause} GROUP BY tgid`,
  ).all(...countParams) as unknown as TgidCountRow[]
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
        // `m?.enc ?? null` rather than a 'clear' default: a whitelisted id
        // with no roster row at all (the file can carry ids this DB has never
        // heard of) must not inherit a clear claim from a missing record.
        encryption: talkgroupEncryption(
          tallies.get(tgid) ?? EMPTY_TALKGROUP_TALLY,
          m?.enc ?? null,
        ),
      }
    })
    // Busiest first, then by id so the order is stable between calls.
    .sort((a, b) => b.recentCalls - a.recentCalls || a.tgid - b.tgid)
}
