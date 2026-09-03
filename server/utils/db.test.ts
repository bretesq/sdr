import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { createRequire } from 'node:module'
import { getWritableDb, closeDb } from './db'

/**
 * getWritableDb()'s lazy `sessions.backend` migration (task-3-review.md's
 * Critical C1 fix): a live `sdr.db` created before this column existed must
 * get it automatically the next time the web server opens it, not require a
 * manual migration step — see db.ts's migrateSessionsTable() for the full
 * reasoning on why this lives here rather than in scripts/sdr_db.py's own
 * _migrate().
 *
 * Each test builds its own throwaway sqlite file and points SDR_ROOT at it,
 * mirroring queries.test.ts's transcription-health fixture pattern.
 */
const nodeRequire = createRequire(import.meta.url)
const { DatabaseSync } = nodeRequire('node:sqlite') as typeof import('node:sqlite')

let fixtureDir: string
let originalSdrRoot: string | undefined

beforeEach(() => {
  fixtureDir = mkdtempSync(join(tmpdir(), 'db-migrate-'))
  originalSdrRoot = process.env.SDR_ROOT
  process.env.SDR_ROOT = fixtureDir
})

afterEach(() => {
  closeDb()
  if (originalSdrRoot === undefined) delete process.env.SDR_ROOT
  else process.env.SDR_ROOT = originalSdrRoot
  rmSync(fixtureDir, { recursive: true, force: true })
})

describe('sessions.backend migration', () => {
  it('adds backend to a sessions table that predates the column, defaulting existing rows to local', () => {
    const dbFile = join(fixtureDir, 'sdr.db')
    const setup = new DatabaseSync(dbFile)
    // The pre-Task-3 schema: no `backend` column at all.
    setup.exec(`
      CREATE TABLE sessions (
        id INTEGER PRIMARY KEY,
        pid INTEGER,
        proc_start INTEGER,
        config TEXT,
        started_at REAL NOT NULL,
        ended_at REAL
      )
    `)
    setup.prepare('INSERT INTO sessions (id, pid, started_at) VALUES (1, 555, 100.0)').run()
    setup.close()

    const handle = getWritableDb()
    const cols = handle.prepare('PRAGMA table_info(sessions)').all() as { name: string }[]
    expect(cols.some(c => c.name === 'backend')).toBe(true)

    // SQLite backfills a NOT NULL DEFAULT for pre-existing rows -- the row
    // inserted before the column existed reads back as 'local', not NULL.
    const row = handle.prepare('SELECT backend FROM sessions WHERE id = 1').get() as { backend: string }
    expect(row.backend).toBe('local')
  })

  it('accepts a new insert with an explicit delegated backend after migrating', () => {
    const dbFile = join(fixtureDir, 'sdr.db')
    const setup = new DatabaseSync(dbFile)
    setup.exec(`
      CREATE TABLE sessions (
        id INTEGER PRIMARY KEY, pid INTEGER, proc_start INTEGER,
        config TEXT, started_at REAL NOT NULL, ended_at REAL
      )
    `)
    setup.close()

    const handle = getWritableDb()
    handle.prepare(
      "INSERT INTO sessions (pid, started_at, backend) VALUES (?, ?, 'delegated')",
    ).run(4242, 200.0)
    const row = handle.prepare('SELECT backend FROM sessions WHERE pid = 4242').get() as { backend: string }
    expect(row.backend).toBe('delegated')
  })

  it('is idempotent: reopening a database that already has the column does not error', () => {
    const dbFile = join(fixtureDir, 'sdr.db')
    const setup = new DatabaseSync(dbFile)
    setup.exec(`
      CREATE TABLE sessions (
        id INTEGER PRIMARY KEY, pid INTEGER, proc_start INTEGER, config TEXT,
        started_at REAL NOT NULL, ended_at REAL,
        backend TEXT NOT NULL DEFAULT 'local'
      )
    `)
    setup.close()

    // getWritableDb() is a singleton per process, so calling it twice here
    // doesn't re-run the migration by itself -- close and reopen to actually
    // exercise "a second process opens an already-migrated database", which
    // is the real-world case (every server restart).
    expect(() => getWritableDb()).not.toThrow()
    closeDb()
    expect(() => getWritableDb()).not.toThrow()
  })

  it('does nothing when the sessions table does not exist yet (a never-imported database)', () => {
    const dbFile = join(fixtureDir, 'sdr.db')
    // An empty database -- no sessions table at all. Must not throw trying
    // to ALTER a table that isn't there.
    new DatabaseSync(dbFile).close()

    expect(() => getWritableDb()).not.toThrow()
  })
})
