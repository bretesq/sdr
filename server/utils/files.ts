import { readFileSync, readdirSync } from 'node:fs'

/**
 * Encryption label as it appears in the reference DB and calls.json.
 * Verified vocabulary — 'encrypted' is NOT a value that ever occurs.
 * Do not conflate this with the listen-scope flags (see ListenOptions).
 */
export type Encryption = 'clear' | 'partial' | 'full'

export interface TalkgroupEntry {
  tgid: number      // synthesized from the JSON key — absent from the value object
  alpha: string
  desc: string
  cat: string
  enc: Encryption
  tag: string       // e.g. "Law Dispatch" — searched, and what --tag selects on
  mode: string      // e.g. "D" / "De" — shown so "D enc" is visible
}

export interface Recording {
  file: string
  tgid: number | null
  alpha: string | null
  desc: string | null
  cat: string | null
  enc: Encryption | null
  start: number
  dur: number
  transcript: string | null
}

const NAME = /^TG(\d+)_.+_(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(?:_\d+)?\.wav$/

export function loadJSON<T>(path: string, fallback: T): T {
  try {
    return JSON.parse(readFileSync(path, 'utf-8')) as T
  } catch {
    return fallback
  }
}

export function parseRecordingFilename(name: string): { tgid: number | null; start: number } {
  const m = NAME.exec(name)
  if (!m) return { tgid: null, start: 0 }

  const [, tg, y, mo, d, h, mi, s] = m
  // LOCAL time — see the note above. Do not change to Date.UTC.
  const start = new Date(
    Number(y), Number(mo) - 1, Number(d),
    Number(h), Number(mi), Number(s),
  ).getTime() / 1000

  return { tgid: Number(tg), start }
}

export function scanRecordings(
  dir: string,
  tgdb: Record<string, TalkgroupEntry>,
): Recording[] {
  let files: string[]
  try {
    files = readdirSync(dir)
  } catch {
    return []
  }

  const out: Recording[] = []
  for (const file of files) {
    if (!file.endsWith('.wav')) continue
    const { tgid, start } = parseRecordingFilename(file)
    const entry = tgid === null ? undefined : tgdb[String(tgid)]

    out.push({
      file,
      tgid,
      alpha: entry?.alpha ?? null,
      desc: entry?.desc ?? null,
      cat: entry?.cat ?? null,
      enc: entry?.enc ?? null,
      start,
      dur: 0,
      transcript: null,
    })
  }

  return out.sort((a, b) => b.start - a.start)
}

type CallRecord = Partial<Recording>

/**
 * Merge metadata from calls.json into scanned recordings.
 * Accepts either an array of call records or an object keyed by filename.
 * Only non-null values from calls.json override scanned values.
 */
export function mergeCalls(recordings: Recording[], calls: unknown): Recording[] {
  const byFile = new Map<string, CallRecord>()

  if (Array.isArray(calls)) {
    for (const c of calls as CallRecord[]) {
      if (c && typeof c === 'object' && typeof c.file === 'string') byFile.set(c.file, c)
    }
  } else if (calls && typeof calls === 'object') {
    for (const [key, val] of Object.entries(calls as Record<string, CallRecord>)) {
      if (!val || typeof val !== 'object') continue
      byFile.set(typeof val.file === 'string' ? val.file : key, val)
    }
  }

  // Fields enumerated explicitly: the generic key-walk needed an `as any`,
  // which the Global Constraints forbid. calls.json's key set is known and
  // fixed (Task 0 Step 2), so there is nothing to be generic about.
  return recordings.map((rec) => {
    const c = byFile.get(rec.file)
    if (!c) return rec

    return {
      ...rec,
      tgid:       c.tgid       ?? rec.tgid,
      alpha:      c.alpha      ?? rec.alpha,
      desc:       c.desc       ?? rec.desc,
      cat:        c.cat        ?? rec.cat,
      enc:        c.enc        ?? rec.enc,
      start:      c.start      ?? rec.start,
      dur:        c.dur        ?? rec.dur,
      transcript: c.transcript ?? rec.transcript,
      file:       rec.file,     // never let calls.json rename the file
    }
  })
}
