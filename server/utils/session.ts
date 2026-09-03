import { getWritableDb } from './db'
import { delegatedSessionLiveness, isOurListenSession, processStartTime } from './processes'
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
 * (delegatedSessionLiveness()), never resolved locally.
 *
 * UNREACHABLE IS NOT THE SAME ANSWER AS STOPPED
 * ----------------------------------------------
 * delegatedSessionLiveness() returns three states, not two: 'alive',
 * 'stopped', and 'unknown' (network error, timeout, or an unparseable
 * response — see that function's own docstring for the full reasoning, and
 * task-3-review.md's fix-round-2 finding for why this distinction exists at
 * all). Fix round 1 collapsed 'unknown' into the same `false` as 'stopped',
 * and get() reacted by calling close() — which is one-way (see close()'s own
 * docstring: "idempotent" means safe to call again, not reversible) — so a
 * SINGLE transient control-API blip permanently untracked a healthy,
 * still-running session for the rest of its life. isSessionAlive() below
 * tolerates a bounded STRETCH of wall-clock time spent answering 'unknown'
 * (UNKNOWN_TOLERANCE_MS) before finally giving up and treating the session
 * as stopped — long enough to absorb one blip, short enough that a
 * genuinely, permanently dead control API doesn't wedge a session as
 * "running" forever with nothing left to ever close it. A single
 * 'alive'/'stopped' answer resets the streak: only a SUSTAINED run of
 * 'unknown' spanning the whole tolerance window counts against the budget.
 * This is deliberately a TIME bound, not a call-count bound — see
 * unknownSince's own comment below for why that distinction was corrected.
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
 * Epoch ms of the FIRST 'unknown' liveness answer in the current unbroken
 * streak, keyed by session id — the retry budget behind
 * UNKNOWN_TOLERANCE_MS below. In-memory only, deliberately not persisted: a
 * server restart already resets `current` to null, and starting fresh on
 * the next check is the same conservative "tolerate a blip, within the same
 * bound" default a genuinely fresh process should apply anyway. Keyed by id
 * rather than kept only on the `Session` object itself so it survives
 * get()'s own `current = null` / reload churn — the streak is about the
 * SESSION's run of bad luck, not about any one in-memory object.
 *
 * WHY A TIMESTAMP, NOT A COUNT (this replaces a call-count budget that used
 * to live here)
 * -------------------------------------------------------------------------
 * The previous version of this tolerance was `consecutiveUnknown: Map<number,
 * number>` counting up to MAX_CONSECUTIVE_UNKNOWN = 3 calls, and its own
 * comment already flagged the problem this caused (final-review.md M3): its
 * real-world duration depended entirely on how often something happened to
 * call get(). At the polling rate that existed when that bound was written
 * (nothing — `/api/listen/followed` was fetched exactly once per page load,
 * never on an interval; see docs/../stall-indicator-report.md for how that
 * was verified), 3 consecutive checks could span anything from seconds to
 * hours depending on how long the operator's tab happened to sit unpolled.
 * Once `composables/useScannerFeed.ts` gained a periodic poll of its own
 * (added alongside this change, to make a stalled capture visible in an
 * already-open tab — see utils/captureStatus.ts), that same call-count
 * budget would have become load-bearing in the wrong way: a brief
 * control-API network hiccup during a fast poll could burn all three
 * tolerances in seconds and permanently untrack a healthy session — close()
 * is one-way. A wall-clock budget makes the tolerance mean the same thing
 * regardless of how often, or how rarely, anything happens to call get().
 */
const unknownSince = new Map<number, number>()

/**
 * How long a delegated session's liveness check may keep answering 'unknown'
 * (see this file's module comment, "UNREACHABLE IS NOT THE SAME ANSWER AS
 * STOPPED") before this file gives up and treats it as stopped anyway,
 * measured in wall-clock milliseconds from the FIRST 'unknown' in the
 * current streak — not a call count (see unknownSince's own comment for why
 * that distinction matters now that a poll actually exists).
 *
 * 60s: several times longer than a transient network blip or a brief
 * control-API restart, so an operator's newly-added periodic poll (see
 * composables/useScannerFeed.ts) surviving a couple of bad reads in a row
 * does not cost a healthy session its tracked state. Still short enough
 * that a control API that is genuinely, permanently gone stops holding a
 * dead session open forever — this file has no other mechanism that will
 * ever close it once that happens. A single 'alive' or 'stopped' answer
 * resets the streak entirely (see isSessionAlive() below): only a SUSTAINED
 * run of 'unknown' spanning this whole window counts against the budget.
 */
export const UNKNOWN_TOLERANCE_MS = 60_000

/**
 * Is `session` still actually running? Dispatches on `backend` — see this
 * file's module comment ("TWO BACKENDS, TWO IDENTITY CHECKS") for why a
 * single check cannot serve both: a delegated session's `pid` is not
 * resolvable against this process's own /proc at all.
 *
 * For a delegated session, this is also where 'unknown' gets turned into a
 * decision (see "UNREACHABLE IS NOT THE SAME ANSWER AS STOPPED" above):
 * delegatedSessionLiveness() itself stays a pure, policy-free reporter of
 * what the control API said, and every bit of "how long is too long"
 * judgment lives HERE, the one place session lifecycle decisions are
 * actually made.
 */
async function isSessionAlive(session: Session): Promise<boolean> {
  if (session.backend !== 'delegated') {
    return isOurListenSession(session.pid, session.procStart)
  }

  const liveness = await delegatedSessionLiveness(session.pid)
  if (liveness === 'alive') {
    unknownSince.delete(session.id)
    return true
  }
  if (liveness === 'stopped') {
    unknownSince.delete(session.id)
    return false
  }

  // 'unknown': tolerate it, up to the wall-clock bound, rather than treating
  // a single control-API hiccup as indistinguishable from a real stop.
  const now = Date.now()
  const since = unknownSince.get(session.id)
  if (since === undefined) {
    // First 'unknown' in a fresh streak — always tolerated, and the streak
    // starts timing from right now.
    unknownSince.set(session.id, now)
    return true
  }
  if (now - since >= UNKNOWN_TOLERANCE_MS) {
    unknownSince.delete(session.id)
    return false
  }
  return true
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
   * (isSessionAlive() -> delegatedSessionLiveness()) is a real HTTP round trip
   * to the capture container's control API, not a local /proc read. Every
   * caller of get()/isRunning() had to become async alongside this — see
   * server/api/listen/{start,stop}.post.ts and {status,followed}.get.ts.
   *
   * WHAT AN 'unknown' ANSWER LOOKS LIKE TO THE OPERATOR, decided deliberately
   * (per task-3-review.md's fix-round-2 ask): NOTHING changes. `current` (or
   * the freshly-loaded `candidate`) is returned exactly as it would be for a
   * confirmed-alive session — same pid, same config, same startTime — because
   * isSessionAlive() only returns `false` for 'unknown' once
   * UNKNOWN_TOLERANCE_MS worth of wall-clock time has elapsed since the
   * first 'unknown' in the streak, and until then it returns `true`. So GET
   * /api/listen/status keeps reporting `running: true` with the session's
   * real data through a tolerated blip, not a degraded or "uncertain"
   * variant of it — a transient network hiccup one layer down should not
   * rewrite what the operator sees. This does mean a genuinely dead capture
   * during that tolerance window would ALSO still read as "running" for up
   * to UNKNOWN_TOLERANCE_MS — the accepted cost of not making a single blip
   * indistinguishable from a real stop.
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
    // A session can be closed while it still owes a live unknownSince streak
    // (e.g. clear()/an explicit Stop hits it mid-tolerance-window rather than
    // the streak ever reaching UNKNOWN_TOLERANCE_MS on its own). The id is
    // dead either way, so drop the entry rather than leave it keyed to a
    // session nothing will ever look up again — harmless if left (bounded by
    // sessions started per process lifetime, and get() never age-checks the
    // map), but free to remove and answers "was that streak ever cleaned up"
    // cleanly.
    unknownSince.delete(id)
  },

  clear(): void {
    if (current) this.close(current.id)
    current = null
  },

  async isRunning(): Promise<boolean> {
    return (await this.get()) !== null
  },
}
