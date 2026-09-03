import { describe, it, expect, vi, afterEach } from 'vitest'

/**
 * node:child_process is mocked here, in addition to — not instead of —
 * exercising the inContainer() guards below.
 *
 * WHY BOTH: these tests used to rely on the guard alone as their only safety
 * mechanism, which made the guard both the thing under test and the sole
 * thing preventing the test run from harming anything. That is fragile by
 * construction: if the guard ever regressed silently, stopTranscriber()'s
 * test would not just fail — it would run a REAL
 * `execFileSync('pkill', ['-INT', '-f', 'stt_watch\\.py'])`. On this box that
 * pattern also matches the live containerized transcription watcher, which is
 * visible to the host because the web service runs with `pid: host` — so a
 * red test would silently stop live transcription on a public-safety radio
 * monitoring system. startTranscriber()'s test has the mirror hazard: a real
 * detached `spawn('python3', ...)` watcher racing the compose-managed one
 * over the same recordings/ directory.
 *
 * Mocking node:child_process closes that hole independently of guard health:
 * spawn and execFileSync can never reach a real process from this file, full
 * stop, no matter what startTranscriber()/stopTranscriber() attempt to do
 * internally, guard intact or not. See the vi.mock() call below for how the
 * hoisting actually works given eslint's import-ordering rule.
 *
 * The guard assertions in each test below still genuinely pin the guard's
 * own behavior (the recovery-command text, not just "it threw") — the mock
 * is a second, independent backstop underneath that, not a replacement for
 * testing the guard.
 */
import { startTranscriber, stopTranscriber } from './transcriber'

const mockSpawn = vi.fn()
const mockExecFileSync = vi.fn()

// Vitest hoists this call above both imports above at runtime (regardless of
// its lexical position here), so node:child_process is already the mock by
// the time transcriber.ts's own top-level `import { spawn, execFileSync }`
// resolves. The `mock`-prefixed names are required by that same hoisting
// transform, which only allows referencing outer variables from inside a
// vi.mock() factory when they start with "mock".
vi.mock('node:child_process', () => ({
  spawn: (...args: unknown[]) => mockSpawn(...args),
  execFileSync: (...args: unknown[]) => mockExecFileSync(...args),
}))

afterEach(() => {
  vi.clearAllMocks()
})

describe('transcriber container guard', () => {
  it('startTranscriber refuses in-container, names the recovery command, and never reaches spawn/execFileSync', () => {
    process.env.SDR_IN_CONTAINER = '1'
    try {
      expect(() => startTranscriber()).toThrow(/compose/i)
      expect(() => startTranscriber()).toThrow(/stack\.sh restart stt-watch/)
      // The stronger assertion this mock buys: even though spawn/execFileSync
      // are harmless mocks in this file, a call into either one still means
      // the guard failed to refuse before reaching child_process — which is
      // exactly the regression this test exists to catch. Without the mock,
      // that same regression would reach a real pkill or a real spawn
      // instead of merely flipping this assertion.
      expect(mockSpawn).not.toHaveBeenCalled()
      expect(mockExecFileSync).not.toHaveBeenCalled()
    } finally {
      delete process.env.SDR_IN_CONTAINER
    }
  })

  it('stopTranscriber refuses in-container, names the recovery command, and never reaches execFileSync', () => {
    process.env.SDR_IN_CONTAINER = '1'
    try {
      // Explains the refusal is about non-stickiness, not danger, and points
      // at the compose command that actually holds the watcher down.
      expect(() => stopTranscriber()).toThrow(/restart: unless-stopped/)
      expect(() => stopTranscriber()).toThrow(/docker compose stop stt-watch/)
      expect(mockExecFileSync).not.toHaveBeenCalled()
      expect(mockSpawn).not.toHaveBeenCalled()
    } finally {
      delete process.env.SDR_IN_CONTAINER
    }
  })
})
