import { getWritableDb } from './db'
import { isDelegatedSessionAlive, isOurListenSession, processStartTime } from './processes'
import type { ListenOptions, SessionBackend } from './processes'

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
 *
 * TWO BACKENDS, TWO IDENTITY CHECKS
 * ---------------------------------
 * The above is true only for a `backend: 'local'` session, whose `pid` is a
 * real, host-signalable pid this process spawned directly. A `backend:
 * 'delegated'` session's `pid` comes from server/utils/processes.ts's
 * delegateStart() — a number in the CAPTURE CONTAINER's own PID namespace,
 * meaningless against THIS process's /proc (the host's, via `pid: host`).
 * Resolving it with processStartTime()/isOurListenSession() here would check
 * an unrelated process in a different namespace entirely — usually a
 * low-numbered, root-owned kernel thread — and self-close a healthy
 * session's row within the same request that started it. See
 * task-3-review.md's Critical C1 for the full trace. isSessionAlive() below
 * dispatches on `backend` specifically so this cannot happen: a delegated
 * session's liveness is asked of the control API's own GET /status
 * (isDelegatedSessionAlive()), never resolved locally.
 */

export interface Session {
  id: number
  pid: number
  config: ListenOptions
  startTime: number
  procStart: number | null
  backend: SessionBackend
}

interface SessionRow {
  id: number
  pid: number | null
  proc_start: number | null
  config: string | null
  started_at: number
  backend: string | null
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
    // Anything other than the literal string 'delegated' is treated as
    // 'local' — the SAFE default direction: it applies the STRICTER identity
    // check (isOurListenSession(), which already fails closed on anything
    // ambiguous) rather than the network-only one, for a row this server
    // cannot otherwise account for (in practice: db.ts's migration has
    // already backfilled every existing row to 'local' by the time this
    // ever reads a live database, so this branch is a defensive fallback,
    // not an expected path).
    backend: row.backend === 'delegated' ? 'delegated' : 'local',
  }
}

/**
 * Is `session` still actually running? Dispatches on `backend` — see this
 * file's module comment ("TWO BACKENDS, TWO IDENTITY CHECKS") for why a
 * single check cannot serve both: a delegated session's `pid` is not
 * resolvable against this process's own /proc at all.
 */
async function isSessionAlive(session: Session): Promise<boolean> {
  if (session.backend === 'delegated') return isDelegatedSessionAlive(session.pid)
  return isOurListenSession(session.pid, session.procStart)
}

export const sessionStore = {
  /**
   * Record a session BEFORE the process is spawned, so the recorder can find
   * its own id. udp_audio_record.py reads SDR_SESSION_ID from the environment,
   * which Node sets on the spawn and bash passes through; opening the row after
   * the spawn would race the recorder's first flush.
   *
   * `backend` is not yet known here — whether this session ends up local or
   * delegated is decided inside startListening(), which runs AFTER this —
   * so the row (and the in-memory placeholder below) starts as 'local' and
   * attach() corrects it once the real answer is known. The mid-start window
   * this leaves is harmless: get()'s `candidate.pid === 0` branch treats an
   * unattached row as live regardless of backend, never reaching
   * isSessionAlive() at all until attach() has run.
   */
  open(config: ListenOptions): number {
    const db = getWritableDb()
    const startTime = Date.now() / 1000
    db.prepare(
      'INSERT INTO sessions (config, started_at) VALUES (?, ?)',
    ).run(JSON.stringify(config), startTime)
    const row = db.prepare('SELECT last_insert_rowid() AS id').get() as { id: number }
    current = { id: row.id, pid: 0, config, startTime, procStart: null, backend: 'local' }
    return row.id
  },

  /**
   * Attach the pid (and which backend produced it) once startListening() has
   * returned. `procStart` is computed ONLY for a local session:
   * processStartTime() reads /proc/<pid>/stat in THIS process's namespace
   * (the host's, via `pid: host`), which is the right question for a real
   * host pid and a meaningless one for a delegated session's capture-
   * namespace pid — storing a number there anyway would invite some future
   * caller to misread it as a real proc_start. See this file's module
   * comment for the full reasoning.
   */
  attach(id: number, pid: number, backend: SessionBackend): void {
    const procStart = backend === 'local' ? processStartTime(pid) : null
    getWritableDb()
      .prepare('UPDATE sessions SET pid = ?, proc_start = ?, backend = ? WHERE id = ?')
      .run(pid, procStart, backend, id)
    if (current?.id === id) {
      current.pid = pid
      current.procStart = procStart
      current.backend = backend
    }
  },

  /**
   * The live session, or null. Closes any open row whose process is gone or is
   * not ours, so a stale row cannot keep claiming a session is running.
   *
   * ASYNC, unlike before: a delegated session's liveness check
   * (isSessionAlive() -> isDelegatedSessionAlive()) is a real HTTP round trip
   * to the capture container's control API, not a local /proc read. Every
   * caller of get()/isRunning() had to become async alongside this — see
   * server/api/listen/{start,stop}.post.ts and {status,followed}.get.ts.
   */
  async get(): Promise<Session | null> {
    if (current && current.pid > 0 && !(await isSessionAlive(current))) {
      this.close(current.id)
      current = null
    }
    if (current) return current

    const db = getWritableDb()
    const row = db.prepare(
      `SELECT id, pid, proc_start, config, started_at, backend
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

    if (!(await isSessionAlive(candidate))) {
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

  async isRunning(): Promise<boolean> {
    return (await this.get()) !== null
  },
}
