import { dataVersion, recordingsSummary, type RecordingsSummary } from '~/server/utils/queries'

// createEventStream is NOT imported from 'h3'. Nitro auto-imports h3's utilities
// (as every other route here does for defineEventHandler/getQuery), and there
// are two h3 copies in node_modules — 1.15.11 and a 2.0.0-rc. An explicit
// `import { createEventStream } from 'h3'` resolved to the copy Nitro is not
// using, so the stream was never wired to the response: the route registered
// fine and then hung without ever flushing headers.

/**
 * Server-sent events telling clients when the recordings corpus changed.
 *
 * WHY SSE AND NOT A CLIENT POLL
 * -----------------------------
 * Both writers are already live — udp_audio_record.py commits each call to
 * sdr.db as it flushes, and stt_watch.py commits each transcript as Whisper
 * finishes one — but nothing told the browser. RecordingsList only reloaded on
 * mount, on a filter change, on the refresh button, and 1.5 s after Stop. In
 * multi-receiver mode nine receivers land calls concurrently, so a frozen table
 * hides a lot.
 *
 * WHAT THE CHANGE SOURCE IS
 * -------------------------
 * SQLite has no cross-process change notification: sqlite3_update_hook fires
 * only for the connection that made the change. So something has to poll. This
 * moves that poll to the server, where it is ONE tick shared by every connected
 * client, and where the tick is `PRAGMA data_version` — a counter SQLite bumps
 * when another connection commits, costing no scan of the corpus. The
 * aggregate query only runs on the ticks where that counter actually moved.
 *
 * Net effect: N browsers cost one pragma per second, not N aggregate queries.
 *
 * WHAT IS PUSHED
 * --------------
 * A summary, never rows: `{ calls, transcripts, latest }`. The client re-runs
 * its own filtered/sorted query, so this route does not need to know about
 * search terms, encryption filters, or sort order — and there is exactly one
 * place that builds a recordings query.
 *
 * `data_version` also moves for writes we do not care about — the sessions
 * table, and a grant census import writing ~2,000 rows. The summary is
 * therefore compared against the last one broadcast and identical summaries are
 * dropped, so those writes are invisible to clients.
 */

const TICK_MS = 1000

const clients = new Set<{ push: (msg: string) => Promise<void> }>()
let timer: ReturnType<typeof setInterval> | null = null
let lastVersion: number | null = null
let lastSummary: RecordingsSummary | null = null

function serialise(summary: RecordingsSummary): string {
  return JSON.stringify(summary)
}

function broadcast(payload: string): void {
  for (const c of clients) {
    // A push to a client that has gone away rejects; that client's own
    // onClosed removes it from the set, so this only has to avoid becoming an
    // unhandled rejection in the interval callback.
    c.push(payload).catch(() => {})
  }
}

function tick(): void {
  let version: number
  try {
    version = dataVersion()
  } catch {
    // getDb() throws a 503 when sdr.db is absent. Nothing to report and
    // nothing to fix from in here; the next tick will retry. Swallowing this
    // is deliberate — an exception escaping a setInterval callback takes the
    // whole Nitro process down.
    return
  }

  if (version === lastVersion) return
  lastVersion = version

  let summary: RecordingsSummary
  try {
    summary = recordingsSummary()
  } catch {
    return
  }

  const payload = serialise(summary)
  // Drop writes that changed the file but not the corpus: session bookkeeping,
  // and grant imports.
  if (lastSummary !== null && serialise(lastSummary) === payload) return
  lastSummary = summary
  broadcast(payload)
}

function startPolling(): void {
  if (timer) return
  // Prime both baselines BEFORE the first tick. Otherwise lastVersion is null,
  // the first tick reads a "changed" version and broadcasts a summary the
  // connecting client has just been sent directly — a duplicate event on every
  // connect. Observed as two identical frames arriving back to back.
  try {
    lastVersion = dataVersion()
    lastSummary = recordingsSummary()
  } catch {
    // No database. Leave them null; the first successful tick primes them, and
    // it broadcasts to whoever is listening, which is correct at that point.
  }
  timer = setInterval(tick, TICK_MS)
  // Do not hold the event loop open on this: a dev-server reload or a shutdown
  // should not wait a tick, and Nitro has no hook here that is guaranteed to run.
  timer.unref?.()
}

function stopPolling(): void {
  if (!timer) return
  clearInterval(timer)
  timer = null
  // Forget the version so the next subscriber's first tick re-reads it rather
  // than comparing against a counter from minutes ago. lastSummary is kept:
  // it is what suppresses a spurious "changed" event to a fresh subscriber.
  lastVersion = null
}

export default defineEventHandler((event) => {
  // Read-only and same-origin-safe: no body, no state change. The CSRF guards
  // on start/stop exist because those drive a radio; this only reveals counts
  // the recordings list already returns.
  const stream = createEventStream(event)

  clients.add(stream)
  startPolling()

  // NEVER await a push before returning send().
  //
  // In h3 1.15.11 `push()` on a stream that has not been handled yet buffers
  // the data but returns a promise that does not settle, so `await
  // stream.push(...)` before `send()` deadlocks the handler: send() is never
  // reached, no headers are ever written, and the client sees a connection that
  // hangs rather than an error. Verified in isolation against this exact h3
  // build — await-before-send times out with no status; push-after-send returns
  // 200 with `data: tick`.
  //
  // So the baseline goes out on the next tick of the event loop, after this
  // handler has returned the stream. A client connecting mid-session still gets
  // its starting counts immediately rather than waiting for the next change.
  setImmediate(() => {
    let payload: string
    try {
      payload = serialise(recordingsSummary())
    } catch {
      return          // no database yet; the first real change will carry it
    }
    // Deliberately does NOT touch lastSummary: that is the shared broadcast
    // dedupe baseline, and resetting it here would let one client connecting
    // swallow a genuine change for every other client already listening.
    stream.push(payload).catch(() => {})
  })

  stream.onClosed(async () => {
    clients.delete(stream)
    if (clients.size === 0) stopPolling()
    await stream.close()
  })

  return stream.send()
})
