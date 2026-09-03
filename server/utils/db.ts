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

  // The Python layer owns schema. If it has not run since these columns were
  // introduced, fail loudly with the command to run rather than throwing an
  // opaque "no such column" from deep inside a query — same policy as the
  // missing-database case above.
  const cols = db.prepare('PRAGMA table_info(calls)').all() as { name: string }[]
  if (!cols.some(c => c.name === 'transcript_norm')) {
    db.close()
    db = null
    throw createError({
      statusCode: 503,
      statusMessage: 'Database predates the 10-code migration. '
        + 'Run: python3 scripts/backfill_codes.py',
    })
  }

  return db
}

let writable: DatabaseSync | null = null

/**
 * A writable handle, for the sessions table.
 *
 * Reads go through getDb(), which opens read-only so a bug in a query route
 * cannot mutate the corpus. Session bookkeeping genuinely writes, so it gets
 * its own connection rather than making everything writable.
 *
 * WAL means this does not block udp_audio_record.py writing calls at the same
 * time; busy_timeout covers the brief exclusive lock during a checkpoint.
 */
export function getWritableDb(): DatabaseSync {
  if (writable) return writable

  const path = dbPath()
  if (!existsSync(path)) {
    throw createError({
      statusCode: 503,
      statusMessage: `No database at ${path}. Run: python3 scripts/import_to_sqlite.py`,
    })
  }

  writable = new DatabaseSync(path)
  writable.exec('PRAGMA busy_timeout = 5000')
  migrateSessionsTable(writable)
  return writable
}

/**
 * Idempotently add `sessions.backend`, for a live `sdr.db` that predates it.
 *
 * `sessions` is written ONLY from here (Python never touches it -- see
 * scripts/sdr_db.py's comment on the same table), so unlike `calls`/
 * `talkgroups`, its schema evolution has to live in THIS file rather than in
 * sdr_db.py's own `_migrate()`: nothing else ever opens this table writable,
 * and a live database created before this column existed would otherwise
 * have `server/utils/session.ts`'s `UPDATE sessions SET ... backend = ?`
 * fail with "no such column" the first time a delegated session tried to
 * record one. `PRAGMA table_info` + a guarded `ALTER TABLE ADD COLUMN`
 * mirrors the exact idiom scripts/sdr_db.py's own `_migrate()` already uses
 * for `calls`/`talkgroups`, kept cheap by running only once per process
 * (getWritableDb()'s `writable` singleton means this body runs at most once
 * per server lifetime, not per request).
 *
 * `cols.length === 0` means the table itself does not exist yet (a fresh,
 * never-imported database) -- skipped rather than attempting `ALTER TABLE`
 * against a table that isn't there; the INSERT/UPDATE statements that
 * actually use this table will fail with SQLite's own clear error in that
 * case, exactly as they already would have before this migration existed.
 */
function migrateSessionsTable(handle: DatabaseSync): void {
  const cols = handle.prepare('PRAGMA table_info(sessions)').all() as { name: string }[]
  if (cols.length === 0) return
  if (!cols.some(c => c.name === 'backend')) {
    handle.exec(
      "ALTER TABLE sessions ADD COLUMN backend TEXT NOT NULL DEFAULT 'local' "
      + "CHECK (backend IN ('local', 'delegated'))",
    )
  }
}

/** Drop the handle so the next call reopens. Used after an import. */
export function closeDb(): void {
  db?.close()
  db = null
  writable?.close()
  writable = null
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
  id: number
  file: string
  tgid: number | null
  start: number
  dur: number
  ended_at: number | null
  transcript: string | null
  transcript_norm: string | null
  alpha: string | null
  description: string | null
  cat: string | null
  enc: 'clear' | 'partial' | 'full' | null
  enc_overridden: number | null
  src_addr: number | null
  algid: number | null
  algorithm: string | null
  keyid: number | null
  mi: string | null
  // Written by scripts/enc_harvest.py. Distinct from `enc` above, which is the
  // scraped RadioReference label for the talkgroup rather than a fact about
  // this transmission.
  enc_observed: string | null
  enc_evidence: string | null
  enc_source: string | null
  rfss: number | null
  site: number | null
  site_name: string | null
  freq: number | null
}
