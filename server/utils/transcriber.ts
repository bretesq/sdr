import { spawn, execFileSync } from 'node:child_process'
import { openSync, closeSync } from 'node:fs'
import { join } from 'node:path'
import { scriptsDir, sdrRoot, recordingsDir } from './paths'
import { inContainer } from './processes'

/**
 * The speech-to-text watcher, as a process independent of any recording session.
 *
 * WHY THIS EXISTS
 * ---------------
 * stt_watch.py was only ever started by lwin_listen.sh / lwin_listen_multi.sh
 * when --stt was passed, and their cleanup killed it. So transcription lived
 * exactly as long as a radio session: stop recording and it stopped mid-backlog,
 * leaving calls permanently untranscribed until someone happened to run another
 * --stt session. Measured after a few test sessions: 42 calls never transcribed
 * and nothing running to pick them up.
 *
 * Transcription is CPU work over files already on disk. It has nothing to do
 * with holding a radio, so it should not be scoped to one.
 *
 * IDENTITY
 * --------
 * Matched by argv rather than a tracked pid, for the same reason isRadioBusy()
 * is: the contended resource is "one whisper working through recordings/", and a
 * watcher started from a shell, from a --stt session, or from here are all the
 * same thing. A second one would double CPU and race the first on .txt writes.
 */

/** Matches the watcher however it was started. `-f` matches the full argv. */
const STT_PATTERN = 'stt_watch\\.py'

/** Where the whisper-server listens. Mirrors STT_PORT in stt_server.sh. */
const STT_URL = process.env.STT_URL || 'http://127.0.0.1:8081'

/**
 * Bring up the persistent CUDA whisper-server the watcher POSTs to.
 *
 * Best-effort on purpose. stt_watch.py falls back to the CPU binary when the
 * server is unreachable, so a missing docker or a busy GPU degrades throughput
 * instead of breaking transcription. But the fallback is ~15x slower than the
 * server (2.5 s/clip vs 0.168 s) — slower even than the small.en pipeline this
 * replaced — so starting a watcher without trying the server first would hand
 * the operator a silent, large regression.
 *
 * FIRE AND FORGET, deliberately. `stt_server.sh start` may need to build the
 * image, which pulls a ~2 GB base layer; awaiting that would hang
 * POST /api/transcribe/start for as long as the pull takes. The watcher
 * transcribes on CPU until the server answers and then picks it up on the next
 * file, so nothing is lost by not waiting. Idempotent: the script returns
 * immediately when a container is already up.
 */
function ensureSttServer(): void {
  try {
    const child = spawn(join(scriptsDir(), 'stt_server.sh'), ['start'], {
      cwd: sdrRoot(),
      detached: true,
      stdio: 'ignore',
    })
    child.unref()
  } catch {
    // Reported through /api/transcribe/status, which surfaces server health
    // separately from watcher liveness.
  }
}

export function isTranscriberRunning(): boolean {
  try {
    const out = execFileSync('pgrep', ['-f', STT_PATTERN], {
      encoding: 'utf-8',
      timeout: 2000,
    })
    return out.trim().length > 0
  } catch {
    // pgrep exits 1 with no output when nothing matches: not running.
    return false
  }
}

/**
 * Start the watcher, detached, if one is not already running.
 *
 * @throws in a container, before anything below is attempted — see the guard.
 * @returns true if this call started one, false if one was already up.
 */
export function startTranscriber(): boolean {
  // This is the fourth case on this branch of containerization silently
  // breaking a host assumption (after procps/pgrep, the 127.0.0.1 loopback,
  // and PID-1 signal immunity) — and the worst of the four, because it fires
  // on the exact incident this whole effort exists to surface.
  //
  // `spawn('python3', ...)` below can NEVER succeed in the web image: it
  // carries no Python (verified — no python* on any PATH directory of
  // rtl-web). Node's spawn() reports a missing binary as an asynchronous
  // 'error' event on the ChildProcess, not as a thrown exception, so it
  // arrives one tick AFTER this function — and the try/catch in
  // start.post.ts around it — have already returned control to the caller.
  // The route would answer `{success: true, started: true}` — a false
  // success reported to the operator — and the unhandled 'error' event would
  // then crash the whole Nuxt process on the next tick. Nothing downstream of
  // the spawn call can defend against this; the only fix is to never reach
  // it. Refusing here, before ensureSttServer() or the spawn, is why this
  // guard is the FIRST statement in the function rather than a check wrapped
  // around the spawn: an asynchronous failure cannot be caught after the
  // fact, so it must be prevented before the fact.
  //
  // The watcher is compose-managed now (rtl-stt-watch, `restart:
  // unless-stopped`), so the correct recovery action is a compose command,
  // not a spawn from inside this container.
  if (inContainer()) {
    throw new Error(
      'The transcriber runs under compose now, not as a process this server '
      + 'can spawn — the web image carries no python3, and a missing binary '
      + "fails asynchronously in a way nothing here can catch. Restart it "
      + 'on the host instead: ./scripts/stack.sh restart stt-watch '
      + '(or: docker compose restart stt-watch)',
    )
  }

  if (isTranscriberRunning()) return false

  ensureSttServer()

  const script = join(scriptsDir(), 'stt_watch.py')
  // Appended, not truncated: this log is the only record of what whisper did
  // across restarts, and it is small (one line per call).
  const fd = openSync(join(sdrRoot(), 'results', 'stt_watch.log'), 'a')
  try {
    const child = spawn('python3', [script, '--dir', recordingsDir()], {
      cwd: sdrRoot(),
      // Its own process group, so a session's cleanup trap or a Stop that
      // signals a whole group cannot take the transcriber down with it. That
      // coupling is the bug this module exists to remove.
      detached: true,
      stdio: ['ignore', fd, fd],
    })
    child.unref()
    return true
  } finally {
    closeSync(fd)                 // the child holds its own dup
  }
}

/**
 * Stop the watcher.
 *
 * SIGINT, not SIGKILL: stt_watch.py traps it and sets a stop flag checked at the
 * top of its loop, so it finishes the file it is transcribing and exits cleanly
 * rather than orphaning a half-written .txt.
 *
 * @throws in a container, before anything below is attempted — see the guard.
 * @returns true if a signal was sent, false if nothing was running.
 */
export function stopTranscriber(): boolean {
  // Refused for coherence with startTranscriber()'s guard above, not because
  // this call is itself dangerous. Left unguarded, `pkill -INT -f
  // stt_watch.py` still works here — it reaches the containerized watcher
  // through `pid: host`, and both containers run as UID 1000, so the signal
  // is permitted. But `restart: unless-stopped` on rtl-stt-watch revives it
  // within about a second, so the Stop the operator just clicked silently
  // does not stick: `isTranscriberRunning()` goes false for a moment, the UI
  // flips its control to "Start the transcriber", and one click on THAT is
  // the direct path into the startTranscriber() guard above. Reporting a
  // signalled stop that does not actually stop anything is exactly the kind
  // of appears-to-work outcome this branch keeps finding — refusing honestly
  // here is strictly better than a Stop that quietly un-does itself.
  if (inContainer()) {
    throw new Error(
      'The transcriber runs under compose now — pkill here would signal the '
      + 'containerized watcher, but `restart: unless-stopped` revives it '
      + 'within about a second, so Stop would not actually stick. To take it '
      + 'down for real: docker compose stop stt-watch (it comes back at the '
      + 'next ./scripts/stack.sh up or restart)',
    )
  }

  if (!isTranscriberRunning()) return false
  try {
    execFileSync('pkill', ['-INT', '-f', STT_PATTERN], { timeout: 2000 })
    return true
  } catch {
    // pkill exits non-zero when it matched nothing, which is the goal state.
    return true
  }
}

/**
 * True if the whisper-server answers an HTTP request. That is ALL this proves.
 *
 * It is not a health check, and must not be read as one: this exact probe
 * answered `200` in 0.4ms for 26 straight hours while every transcription
 * hung forever, because a wedged whisper-server can still accept a TCP
 * connection and serve `GET /` while its inference path is dead. It also
 * cannot see a watcher that has died or a GPU that has vanished — both leave
 * this call returning `true` right up until the process itself goes away.
 *
 * For whether transcription is actually keeping up, see transcriptionHealth()
 * in queries.ts, which measures the one thing this cannot: whether transcripts
 * are appearing for calls that have ended. `GET /api/transcribe/status`
 * reports this function's result as `reachable` — deliberately not `healthy`
 * or `gpuServer` — precisely so nothing calling it implies more than it knows.
 *
 * A direct HTTP probe rather than `stt_server.sh status`, because this is on a
 * polled path (pages/index.vue polls `GET /api/transcribe/status` every 10s)
 * and that script shells
 * out to `docker ps` — which can block for seconds on this host, where ~50
 * containers are running. Same signal, no docker in the read path.
 */
export async function isSttServerRunning(): Promise<boolean> {
  try {
    const res = await fetch(`${STT_URL}/`, {
      signal: AbortSignal.timeout(1500),
    })
    return res.status >= 200 && res.status < 500
  } catch {
    return false
  }
}
