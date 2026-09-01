import { spawn, execFileSync } from 'node:child_process'
import { openSync, closeSync } from 'node:fs'
import { join } from 'node:path'
import { scriptsDir, sdrRoot, recordingsDir } from './paths'

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
 * @returns true if this call started one, false if one was already up.
 */
export function startTranscriber(): boolean {
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
 * @returns true if a signal was sent, false if nothing was running.
 */
export function stopTranscriber(): boolean {
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
 * True if the whisper-server answers. Distinct from the watcher being up: a
 * watcher can be running while the server is down, transcribing on CPU at
 * roughly 15x the cost per clip.
 *
 * A direct HTTP probe rather than `stt_server.sh status`, because this is on a
 * polled path (RecordingsList polls transcriber state) and that script shells
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
