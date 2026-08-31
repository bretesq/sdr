import { getWritableDb } from './db'
import { isOurListenSession, processStartTime } from './processes'
import type { ListenOptions } from './processes'

/**
 * Listening sessions, stored as rows rather than three sidecar files.
 *
 * This used to be `web/listen.pid`, `web/listen.config.json` and
 * `web/listen.started`, written separately and read back on recovery. A row
 * gives the same recovery, plus a history of what was recorded when, plus a
 * foreign key for `calls.session_id` so a recording can be traced to the run
 * that produced it.
 *
 * IDENTITY, NOT JUST LIVENESS
 * ---------------------------
 * `proc_start` is /proc/<pid>/stat field 22. A pid alone is not an identity:
 * the kernel recycles pid numbers, a process group outlives its leader, and a
 * row left behind by a reboot would otherwise sit waiting for that number to be
 * reissued. Stop signals a process GROUP, and process.kill only refuses OTHER
 * users' processes, so a mistaken match puts everything the operator owns in
 * range of a SIGKILL. The kernel never reissues the same (pid, starttime) pair,
 * so together they are a real identity check.
 */

export interface Session {
  id: number
  pid: number
  config: ListenOptions
  startTime: number
  procStart: number | null
}

interface SessionRow {
  id: number
  pid: number | null
  proc_start: number | null
  config: string | null
  started_at: number
}

/** In-process cache. The row is the source of truth; this avoids a query per poll. */
let current: Session | null = null

function toSession(row: SessionRow): Session {
  let config: ListenOptions = {}
  try {
    config = row.config ? JSON.parse(row.config) as ListenOptions : {}
  } catch {
    // A malformed config is cosmetic — the pid is what Stop actually needs.
  }
  return {
    id: row.id,
    pid: row.pid ?? 0,
    config,
    startTime: row.started_at,
    procStart: row.proc_start,
  }
}

export const sessionStore = {
  /**
   * Record a session BEFORE the process is spawned, so the recorder can find
   * its own id. udp_audio_record.py reads SDR_SESSION_ID from the environment,
   * which Node sets on the spawn and bash passes through; opening the row after
   * the spawn would race the recorder's first flush.
   */
  open(config: ListenOptions): number {
    const db = getWritableDb()
    const startTime = Date.now() / 1000
    db.prepare(
      'INSERT INTO sessions (config, started_at) VALUES (?, ?)',
    ).run(JSON.stringify(config), startTime)
    const row = db.prepare('SELECT last_insert_rowid() AS id').get() as { id: number }
    current = { id: row.id, pid: 0, config, startTime, procStart: null }
    return row.id
  },

  /** Attach the pid once the spawn has returned one. */
  attach(id: number, pid: number): void {
    const procStart = processStartTime(pid)
    getWritableDb()
      .prepare('UPDATE sessions SET pid = ?, proc_start = ? WHERE id = ?')
      .run(pid, procStart, id)
    if (current?.id === id) {
      current.pid = pid
      current.procStart = procStart
    }
  },

  /**
   * The live session, or null. Closes any open row whose process is gone or is
   * not ours, so a stale row cannot keep claiming a session is running.
   */
  get(): Session | null {
    if (current && current.pid > 0
        && !isOurListenSession(current.pid, current.procStart)) {
      this.close(current.id)
      current = null
    }
    if (current) return current

    const db = getWritableDb()
    const row = db.prepare(
      `SELECT id, pid, proc_start, config, started_at
         FROM sessions WHERE ended_at IS NULL
        ORDER BY started_at DESC LIMIT 1`,
    ).get() as SessionRow | undefined
    if (!row) return null

    const candidate = toSession(row)

    // A row with no pid was opened moments ago and has not been attached yet;
    // treat it as live rather than closing a session that is mid-start.
    if (candidate.pid === 0) {
      current = candidate
      return candidate
    }

    if (!isOurListenSession(candidate.pid, candidate.procStart)) {
      this.close(candidate.id)      // left over from a crash or a reboot
      return null
    }

    current = candidate
    return candidate
  },

  /** Mark a session finished. Idempotent. */
  close(id: number): void {
    getWritableDb()
      .prepare('UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL')
      .run(Date.now() / 1000, id)
    if (current?.id === id) current = null
  },

  clear(): void {
    if (current) this.close(current.id)
    current = null
  },

  isRunning(): boolean {
    return this.get() !== null
  },
}
