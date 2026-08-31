import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { createRequire } from 'node:module'
import { sdrRoot } from './paths'

/**
 * `node:sqlite` is loaded through createRequire rather than a static import.
 *
 * It landed in Node 22 and is not in Vite 5's list of Node builtins, so a
 * static `import ... from 'node:sqlite'` makes Vite strip the prefix and try to
 * resolve a package called "sqlite", failing with "Does the file exist?".
 * Externalising it in vitest.config.ts does not help because the failure is in
 * Vite's transform, before externalisation applies. Going through createRequire
 * keeps the specifier out of Vite's static analysis and hands it to Node.
 */
const nodeRequire = createRequire(import.meta.url)
const { DatabaseSync } = nodeRequire('node:sqlite') as typeof import('node:sqlite')
type DatabaseSync = InstanceType<typeof DatabaseSync>

/**
 * The SQLite layer. `node:sqlite` is built into Node 22+, so this costs no
 * dependency and no native build.
 *
 * The database is the source of truth for talkgroups, sites, categories, calls
 * and transcripts. It is NOT a cache over the JSON files — those are import
 * sources, and the previous arrangement (read three JSON files, scan a
 * directory, then read 3,231 .txt files per request) is exactly what this
 * replaces.
 *
 * Deliberately no fallback to the JSON files when the database is absent. A
 * silent fallback would hide a missing import and quietly restore the
 * two-sources-of-truth problem this exists to remove, so a missing database is
 * a loud error telling the operator which command to run.
 */

let db: DatabaseSync | null = null

export function dbPath(): string {
  return join(sdrRoot(), 'sdr.db')
}

export function getDb(): DatabaseSync {
  if (db) return db

  const path = dbPath()
  if (!existsSync(path)) {
    throw createError({
      statusCode: 503,
      statusMessage: `No database at ${path}. Run: python3 scripts/import_to_sqlite.py`,
    })
  }

  db = new DatabaseSync(path, { readOnly: true })
  // WAL lets this read while udp_audio_record.py writes. busy_timeout covers
  // the brief exclusive lock SQLite takes when the writer checkpoints.
  db.exec('PRAGMA busy_timeout = 5000')
  return db
}

/** Drop the handle so the next call reopens. Used after an import. */
export function closeDb(): void {
  db?.close()
  db = null
}

// ---------------------------------------------------------------- row types
// These mirror the table columns. The API maps `description` -> `desc` at the
// edge, because `desc` is a SQL keyword but the existing client contract and
// the reference JSON both use it.

export interface TalkgroupRow {
  tgid: number
  alpha: string | null
  description: string | null
  cat: string | null
  tag: string | null
  enc: 'clear' | 'partial' | 'full' | null
  mode: string | null
  hex: string | null
  tgcat: string | null
}

export interface CallRow {
  file: string
  tgid: number | null
  start: number
  dur: number
  ended_at: number | null
  transcript: string | null
  alpha: string | null
  description: string | null
  cat: string | null
  enc: 'clear' | 'partial' | 'full' | null
  src_addr: number | null
  algid: number | null
  algorithm: string | null
  keyid: number | null
  mi: string | null
  rfss: number | null
  site: number | null
  site_name: string | null
  freq: number | null
}
