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

/** Shape the API returns for a recording. `desc` rather than `description`. */
export interface Recording {
  file: string
  tgid: number | null
  alpha: string | null
  desc: string | null
  cat: string | null
  enc: 'clear' | 'partial' | 'full' | null
  start: number
  dur: number
  transcript: string | null
  srcAddr: number | null
  algid: number | null
  algorithm: string | null
  keyid: number | null
  site: string | null
  freq: number | null
}

const CALL_SELECT = `
  SELECT c.file, c.tgid, c.start, c.dur, c.transcript,
         c.src_addr, c.algid, c.keyid, c.freq, c.rfss, c.site,
         t.alpha, t.description, t.cat, t.enc,
         a.name AS algorithm,
         s.name_county AS site_name
    FROM calls c
    LEFT JOIN talkgroups t ON t.tgid  = c.tgid
    LEFT JOIN algorithms  a ON a.algid = c.algid
    LEFT JOIN sites       s ON s.rfss  = c.rfss AND s.site_dec = c.site
`

function toRecording(r: CallRow): Recording {
  return {
    file: r.file,
    tgid: r.tgid,
    alpha: r.alpha,
    desc: r.description,
    cat: r.cat,
    enc: r.enc,
    start: r.start,
    dur: r.dur,
    transcript: r.transcript,
    srcAddr: r.src_addr,
    algid: r.algid,
    algorithm: r.algorithm,
    keyid: r.keyid,
    site: r.site_name,
    freq: r.freq,
  }
}

export interface RecordingQuery {
  search?: string
  enc?: string
  tgid?: number
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

export function listRecordings(q: RecordingQuery = {}): { rows: Recording[], total: number } {
  const db = getDb()
  const where: string[] = []
  const params: (string | number)[] = []

  if (q.tgid !== undefined) {
    where.push('c.tgid = ?')
    params.push(q.tgid)
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

  const clause = where.length ? `WHERE ${where.join(' AND ')}` : ''

  const total = db.prepare(
    `SELECT COUNT(*) AS n FROM calls c LEFT JOIN talkgroups t ON t.tgid = c.tgid ${clause}`,
  ).get(...params) as { n: number }

  const limit = q.limit ?? 5000
  const offset = q.offset ?? 0
  const rows = db.prepare(
    `${CALL_SELECT} ${clause} ORDER BY c.start DESC LIMIT ? OFFSET ?`,
  ).all(...params, limit, offset) as unknown as CallRow[]

  return { rows: rows.map(toRecording), total: total.n }
}

export function getRecording(file: string): Recording | null {
  const db = getDb()
  const row = db.prepare(`${CALL_SELECT} WHERE c.file = ?`).get(file) as unknown as CallRow | undefined
  return row ? toRecording(row) : null
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
