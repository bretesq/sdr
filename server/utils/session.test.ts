import { describe, it, expect, vi, beforeAll, afterAll, afterEach } from 'vitest'
import { mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { createRequire } from 'node:module'
import { sessionStore } from './session'
import { closeDb, getWritableDb } from './db'

/**
 * server/utils/processes.ts is mocked, not exercised for real, for the same
 * reason transcriber.test.ts/processes.test.ts mock node:child_process: a
 * regression here can reach a REAL process or a REAL HTTP call to the
 * capture container. Specifically, this file's whole point is verifying that
 * sessionStore.get() calls the RIGHT ONE of isOurListenSession() (a local
 * /proc read) vs isDelegatedSessionAlive() (an HTTP round trip to
 * http://capture:8082/status) depending on a session's `backend` — if that
 * dispatch is ever wrong, the failure mode is exactly task-3-review.md's
 * Critical C1: a delegated session's pid gets resolved against the wrong PID
 * namespace. Mocking both means this file can assert "which one got called"
 * directly, deterministically, with neither one ever touching anything real.
 *
 * Vitest hoists this vi.mock() call above the imports above at runtime
 * (regardless of its lexical position here — see transcriber.test.ts's
 * identical comment for how the hoisting transform works), so
 * server/utils/session.ts's own `import ... from './processes'` already
 * resolves to this mock by the time it runs. The `mock`-prefixed names are
 * required by that same transform, which only allows referencing outer
 * variables from inside a vi.mock() factory when they start with "mock".
 */
const mockIsOurListenSession = vi.fn()
const mockProcessStartTime = vi.fn()
const mockIsDelegatedSessionAlive = vi.fn()

vi.mock('./processes', () => ({
  isOurListenSession: (...args: unknown[]) => mockIsOurListenSession(...args),
  processStartTime: (...args: unknown[]) => mockProcessStartTime(...args),
  isDelegatedSessionAlive: (...args: unknown[]) => mockIsDelegatedSessionAlive(...args),
}))

const nodeRequire = createRequire(import.meta.url)
const { DatabaseSync } = nodeRequire('node:sqlite') as typeof import('node:sqlite')

const fixtureDir = mkdtempSync(join(tmpdir(), 'session-store-'))
const originalSdrRoot = process.env.SDR_ROOT

beforeAll(() => {
  const dbFile = join(fixtureDir, 'sdr.db')
  const setup = new DatabaseSync(dbFile)
  // Mirrors the post-migration schema (scripts/sdr_db.py / db.ts's
  // migrateSessionsTable()) directly, rather than relying on the migration
  // path itself — that path has its own dedicated coverage in db.test.ts.
  setup.exec(`
    CREATE TABLE sessions (
      id         INTEGER PRIMARY KEY,
      pid        INTEGER,
      proc_start INTEGER,
      config     TEXT,
      started_at REAL NOT NULL,
      ended_at   REAL,
      backend    TEXT NOT NULL DEFAULT 'local' CHECK (backend IN ('local', 'delegated'))
    )
  `)
  setup.close()
  process.env.SDR_ROOT = fixtureDir
  closeDb()
})

afterAll(() => {
  closeDb()
  if (originalSdrRoot === undefined) delete process.env.SDR_ROOT
  else process.env.SDR_ROOT = originalSdrRoot
  rmSync(fixtureDir, { recursive: true, force: true })
})

afterEach(async () => {
  // sessionStore.clear() only closes an in-memory-tracked `current`; a
  // session that self-closed via get()'s own logic during a test leaves no
  // `current` behind, so a leftover OPEN row from a test that didn't reach
  // that path would otherwise leak into the next test's get() (`ORDER BY
  // started_at DESC LIMIT 1` picks the newest row regardless of which test
  // opened it). Force every row closed between tests instead of relying on
  // each test to clean up after itself correctly.
  sessionStore.clear()
  getWritableDb().exec('UPDATE sessions SET ended_at = 999999999 WHERE ended_at IS NULL')
  vi.resetAllMocks() // clearAllMocks() above resets call history but NOT a prior mockReturnValue/mockResolvedValue
})

describe('sessionStore backend dispatch (task-3-review.md Critical C1)', () => {
  it('attach() computes proc_start for a LOCAL session', () => {
    const id = sessionStore.open({ preset: 'pd' })
    mockProcessStartTime.mockReturnValue(555)

    sessionStore.attach(id, 1234, 'local')

    expect(mockProcessStartTime).toHaveBeenCalledWith(1234)
    const row = getWritableDb().prepare('SELECT proc_start, backend FROM sessions WHERE id = ?').get(id) as {
      proc_start: number | null
      backend: string
    }
    expect(row.proc_start).toBe(555)
    expect(row.backend).toBe('local')
  })

  it('attach() does NOT compute proc_start for a DELEGATED session', () => {
    // The regression this guards: processStartTime() reads /proc/<pid>/stat
    // in THIS process's (the host's) namespace. A delegated session's pid is
    // from the capture container's own namespace -- calling it at all here
    // would resolve an unrelated process and store a meaningless number.
    const id = sessionStore.open({ mode: 'multi', preset: 'pd', duration: 600 })

    sessionStore.attach(id, 8675309, 'delegated')

    expect(mockProcessStartTime).not.toHaveBeenCalled()
    const row = getWritableDb().prepare('SELECT proc_start, backend FROM sessions WHERE id = ?').get(id) as {
      proc_start: number | null
      backend: string
    }
    expect(row.proc_start).toBeNull()
    expect(row.backend).toBe('delegated')
  })

  it('get() on a LOCAL session checks isOurListenSession(), never isDelegatedSessionAlive()', async () => {
    const id = sessionStore.open({ preset: 'pd' })
    mockProcessStartTime.mockReturnValue(null) // real processStartTime() returns number|null, never undefined
    sessionStore.attach(id, 1234, 'local')
    mockIsOurListenSession.mockReturnValue(true)

    const session = await sessionStore.get()

    expect(session).not.toBeNull()
    expect(session?.pid).toBe(1234)
    expect(mockIsOurListenSession).toHaveBeenCalledWith(1234, null)
    expect(mockIsDelegatedSessionAlive).not.toHaveBeenCalled()
  })

  it('get() on a DELEGATED session checks isDelegatedSessionAlive(), never isOurListenSession() — the core C1 regression test', async () => {
    // If this dispatch is ever reverted to always calling
    // isOurListenSession() regardless of backend, THIS is the test that
    // fails: mockIsOurListenSession's default return (undefined, falsy)
    // would make sessionStore.get() self-close the row immediately and
    // return null instead of the live session asserted below — exactly
    // task-3-review.md's C1 trace, reproduced deterministically.
    const id = sessionStore.open({ mode: 'multi', preset: 'pd', duration: 600 })
    sessionStore.attach(id, 8675309, 'delegated')
    mockIsDelegatedSessionAlive.mockResolvedValue(true)

    const session = await sessionStore.get()

    expect(session).not.toBeNull()
    expect(session?.pid).toBe(8675309)
    expect(mockIsDelegatedSessionAlive).toHaveBeenCalledWith(8675309)
    expect(mockIsOurListenSession).not.toHaveBeenCalled()
  })

  it('a delegated session self-closes once the control API reports it no longer running', async () => {
    const id = sessionStore.open({ mode: 'multi', preset: 'pd', duration: 600 })
    sessionStore.attach(id, 42, 'delegated')
    mockIsDelegatedSessionAlive.mockResolvedValue(false)

    const session = await sessionStore.get()

    expect(session).toBeNull()
    const row = getWritableDb().prepare('SELECT ended_at FROM sessions WHERE id = ?').get(id) as {
      ended_at: number | null
    }
    expect(row.ended_at).not.toBeNull()
  })

  it('treats an unattached (mid-start) row as live for either backend, without calling either liveness check', async () => {
    sessionStore.open({ mode: 'multi', preset: 'pd', duration: 600 })
    // No attach() yet — pid is still 0.

    const session = await sessionStore.get()

    expect(session).not.toBeNull()
    expect(session?.pid).toBe(0)
    expect(mockIsOurListenSession).not.toHaveBeenCalled()
    expect(mockIsDelegatedSessionAlive).not.toHaveBeenCalled()
  })

  it('isRunning() reflects a delegated session\'s liveness via the control API', async () => {
    const id = sessionStore.open({ mode: 'multi', preset: 'pd', duration: 600 })
    sessionStore.attach(id, 42, 'delegated')

    mockIsDelegatedSessionAlive.mockResolvedValue(true)
    expect(await sessionStore.isRunning()).toBe(true)
  })
})
