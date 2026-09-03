import { spawn, execFileSync, spawnSync } from 'node:child_process'
import { readFileSync, statSync, openSync, readSync, closeSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { scriptsDir, sdrRoot, listenLogPath } from './paths'

/**
 * Mirrors scripts/lwin_listen.sh's flags 1:1. Deliberately NOT typed with
 * `Encryption` — that is the DB's label vocabulary ('clear'|'partial'|'full').
 * These two booleans are independent listen-scope switches; conflating them
 * into one enum makes "partial AND full" unreachable.
 */
export type ListenMode = 'single' | 'multi'

/** Which LWIN band(s) the multi-receiver mode covers. */
export type ListenLegs = '700' | '800' | '700,800'

export interface ListenOptions {
  /**
   * 'single' runs scripts/lwin_listen.sh — op25 rx.py, ONE receiver that must
   * leave the control channel to hear a call. ~25% of calls, partial census.
   *
   * 'multi' runs scripts/lwin_listen_multi.sh — op25 multi_rx.py across BOTH
   * HackRFs: one receiver pinned to 773.05625 plus a voice pool on each band.
   * Measured 64-70 calls per 300 s with 24-45 of them overlapping in time,
   * plus a complete grant census in the same run.
   *
   * Defaults to 'single' so an existing client's request behaves exactly as
   * before.
   */
  mode?: ListenMode
  /** multi only. Default '700,800'. */
  legs?: ListenLegs
  /** multi only. Voice receivers on the HackRF One (700 MHz leg). */
  nVoice700?: number
  /** multi only. Voice receivers on the HackRF Pro (800 MHz leg). */
  nVoice800?: number
  /**
   * multi only. The grant census needs op25 at -v 10 and an import at exit;
   * both are on by default in the script. Set false to pass --no-census, which
   * drops to -v 2 and cuts log volume ~10x.
   */
  census?: boolean
  preset?: string
  talkgroups?: string
  tag?: string
  match?: string
  allAreas?: boolean
  includePartial?: boolean
  includeEncrypted?: boolean
  stt?: boolean
  /**
   * Raise op25 to -v 10 so it emits the ESS header (algid/keyid/mi) per voice
   * frame — the authoritative per-call encryption signal, which the reference
   * DB's static flag is known to contradict. Costs ~10x the log volume, so it
   * is opt-in.
   */
  ess?: boolean
  duration?: number
}

export function buildListenArgs(opts: ListenOptions): string[] {
  const args: string[] = []

  // Multi-only flags. lwin_listen.sh does not accept these and exits 1 on an
  // unknown option, so they are gated on the mode rather than emitted whenever
  // present.
  if (opts.mode === 'multi') {
    if (opts.legs)                       args.push('--legs', opts.legs)
    if (opts.nVoice700 !== undefined)    args.push('--n-voice-700', String(opts.nVoice700))
    if (opts.nVoice800 !== undefined)    args.push('--n-voice-800', String(opts.nVoice800))
    if (opts.census === false)           args.push('--no-census')
  }

  if (opts.preset)           args.push('--preset', opts.preset)
  if (opts.tag)              args.push('--tag', opts.tag)
  if (opts.talkgroups)       args.push('--tg', opts.talkgroups)
  if (opts.match)            args.push('--match', opts.match)
  if (opts.allAreas)         args.push('--all-areas')
  if (opts.includePartial)   args.push('--include-partial')
  if (opts.includeEncrypted) args.push('--include-encrypted')
  if (opts.stt)              args.push('--stt')
  if (opts.ess)              args.push('--ess')
  if (opts.duration)         args.push(String(opts.duration))  // positional — must stay last

  return args
}

const SAVED_WAV = /TG\d+_[^\s/]+\.wav/g

/** Count distinct saved .wav files mentioned in the log. */
export function countCalls(logText: string): number {
  const matches = logText.match(SAVED_WAV)
  if (!matches) return 0
  return new Set(matches).size
}

/**
 * Read the tail of a file without loading the whole thing.
 * Cap is 4 MB: a session log runs ~160 bytes/call (the saved-call line plus its
 * paired stt_watch line), so 4 MB covers ~25,000 calls. At 256 KB a long session
 * would silently drop half its calls and the displayed count would go DOWN.
 */
export function readTail(path: string, maxBytes = 4 * 1024 * 1024): string {
  try {
    const size = statSync(path).size
    const start = Math.max(0, size - maxBytes)
    const len = size - start
    const fd = openSync(path, 'r')
    try {
      const buf = Buffer.alloc(len)
      readSync(fd, buf, 0, len, start)
      return buf.toString('utf-8')
    } finally {
      closeSync(fd)
    }
  } catch {
    return ''
  }
}


/**
 * Identity of a live process: its start time, from /proc/<pid>/stat field 22.
 *
 * A pid alone is NOT an identity. Pids are recycled, a process group outlives
 * its leader, and a stale web/listen.pid survives reboots. Recording the start
 * time alongside the pid makes recovery verifiable: the kernel will never
 * reissue the same (pid, starttime) pair.
 *
 * Returns null if the process is gone or /proc is unreadable.
 */
export function processStartTime(pid: number): number | null {
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, 'utf-8')
    // Fields after the comm field, which is parenthesised and may contain spaces.
    const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ')
    // stat field 22 is the 20th entry after comm (fields are 1-indexed with
    // pid=1 and comm=2, so index 19 here).
    const started = Number(fields[19])
    return Number.isFinite(started) ? started : null
  } catch {
    return null
  }
}

/**
 * Does this pid actually belong to a listen session we started?
 *
 * Guards every signal. Without it, a stale pid whose number the kernel has
 * recycled onto an unrelated process means Stop sends SIGINT — then SIGKILL
 * eight seconds later — to that process's whole GROUP. process.kill only
 * refuses other users' processes, so everything the operator owns is in range,
 * including their editor and this very server.
 */
export function isOurListenSession(pid: number, expectedStartTime: number | null): boolean {
  if (!isProcessRunning(pid)) return false

  if (expectedStartTime !== null) {
    const actual = processStartTime(pid)
    if (actual === null || actual !== expectedStartTime) return false
  }

  try {
    // cmdline is NUL-separated; the launcher appears as a bash argument.
    // BOTH launchers must be recognised: a multi-mode session whose pid is not
    // matched here is invisible to Stop, which then answers 409 while two
    // HackRFs are still recording.
    const cmdline = readFileSync(`/proc/${pid}/cmdline`, 'utf-8')
    return LAUNCHERS.some(name => cmdline.includes(name))
  } catch {
    return false
  }
}

export function isProcessRunning(pid: number): boolean {
  try {
    process.kill(pid, 0)
  } catch {
    return false
  }
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, 'utf-8')
    const state = stat.slice(stat.lastIndexOf(')') + 1).trim().split(/\s+/)[0]
    return state !== 'Z'
  } catch {
    return false
  }
}

/**
 * `lwin_listen.sh` writes NO log of its own — web/listen.log existed only
 * because server.py redirected the child's stdout into it. This server now owns
 * that log, opened 'w' (truncating) so countCalls() measures exactly this
 * session. server.py used 'ab', which made the count cumulative across every
 * session ever (3,150 distinct .wav names in the current file).
 */
/**
 * The launcher script per mode. Exported so the API layer and the tests use the
 * same mapping rather than restating the filenames.
 */
export const LAUNCHERS = ['lwin_listen.sh', 'lwin_listen_multi.sh'] as const

export function scriptFor(mode: ListenMode | undefined): string {
  return mode === 'multi' ? 'lwin_listen_multi.sh' : 'lwin_listen.sh'
}

/**
 * Is this process the containerised web app rather than the host one?
 *
 * Set by docker-compose. Checked explicitly against '1' so that an empty or '0'
 * value cannot silently disable capture start on a host run.
 */
export function inContainer(): boolean {
  return process.env.SDR_IN_CONTAINER === '1'
}

/**
 * Path to the host's USB bus device tree. Present (bind-mounted) inside the
 * `capture` container per docker-compose.yml's `/dev/bus/usb:/dev/bus/usb`
 * volume; absent from `web`, which carries no such mount at all. A single
 * `existsSync` — the cheapest of the three checks below — so it runs first
 * and short-circuits the other two the instant it already answers "no".
 */
const USB_BUS_PATH = '/dev/bus/usb'

/** The binary op25's own launcher scripts assume is on PATH. */
const HACKRF_INFO_BIN = 'hackrf_info'

/**
 * Is `hackrf_info` on PATH? A PATH scan via `existsSync`, deliberately NOT an
 * execution of the binary: running it opens a HackRF, and the host (or the
 * capture container) may already hold both of them for a live session —
 * proving "can I list a HackRF" would itself contend for the very hardware
 * this probe exists to ask about. Presence on PATH is everything the launcher
 * scripts themselves ever check before invoking it.
 */
function hackrfInfoOnPath(): boolean {
  const dirs = (process.env.PATH ?? '').split(':').filter(Boolean)
  return dirs.some(dir => existsSync(join(dir, HACKRF_INFO_BIN)))
}

/**
 * Does python3 have op25's compiled GNU Radio block importable? The one check
 * here that has to run a subprocess — unavoidable, since "importable" is a
 * property of the interpreter's actual sys.path and compiled-extension ABI,
 * not something a file scan can answer. Unlike hackrf_info this is safe to
 * execute: importing a module never opens a HackRF (op25 only does that once
 * rx.py/multi_rx.py actually runs), so there is no hardware-contention reason
 * to avoid it — only cost, which is why it runs last, after the two cheaper
 * checks have already had a chance to answer "not capable" first.
 *
 * `web`'s image carries no python3 at all (transcriber.ts:96 already
 * documents this for the same reason) — that surfaces here as execFileSync
 * throwing ENOENT, treated identically to a real ImportError: either way,
 * op25 cannot run in this process.
 */
function op25Importable(): boolean {
  try {
    execFileSync('python3', ['-c', 'import gnuradio.op25_repeater'], {
      timeout: 5000,
      stdio: 'ignore',
    })
    return true
  } catch {
    return false
  }
}

/**
 * What, if anything, stops THIS process from running op25 itself.
 *
 * Replaces the old `inContainer()` guard in startListening() below, which was
 * always a proxy for "can this process reach the HackRFs" rather than the
 * real thing — a proxy that broke the day a SECOND container appeared:
 * `inContainer()` is true for both `web` and `capture`, but only `web`
 * actually lacks the hardware. `capture` has real USB passthrough, hackrf_info
 * and gnuradio (see docker-compose.yml's `capture` service and
 * docker/capture/Dockerfile) — asking the real question directly, instead of
 * inferring it from where the process happens to run, is what makes this
 * correct for both containers at once without special-casing either.
 *
 * @returns null if capable, otherwise a short human-readable description of
 * the first missing capability (checks are short-circuited in cheapest-first
 * order, so this is not necessarily an exhaustive list of everything that is
 * missing — just the first thing that is).
 */
export function captureCapabilityGap(): string | null {
  if (!existsSync(USB_BUS_PATH)) return `no USB access (${USB_BUS_PATH} does not exist)`
  if (!hackrfInfoOnPath()) return 'hackrf_info is not on PATH'
  if (!op25Importable()) return 'gnuradio.op25_repeater is not importable by python3'
  return null
}

export function isCaptureCapable(): boolean {
  return captureCapabilityGap() === null
}

/**
 * Where the capture container's control server listens — unpublished,
 * compose-network only (docker-compose.yml's `capture` service, port 8082;
 * see scripts/capture_control.py's module docstring for the full contract).
 * Overridable by env for tests and any non-standard layout, mirroring
 * transcriber.ts's STT_URL.
 */
const CAPTURE_URL = process.env.CAPTURE_URL || 'http://capture:8082'

/** The JSON shape scripts/capture_control.py's POST /start returns or rejects with. */
interface ControlStartResponse {
  started?: boolean
  pid?: number
  args?: string[]
  error?: string
}

/**
 * The JSON shape scripts/capture_control.py's GET /status returns. Never an
 * error response — see that file's snapshot().
 *
 * `running` already reflects op25 ITSELF, not merely its process group
 * (final-review.md's finding 5: `lwin_listen_multi.sh:243` waits on recorder
 * 0, not op25, so op25 can die while the launcher and all eight recorders
 * keep the group alive with no radio behind it). snapshot() used to answer
 * from group liveness alone and reported `running: true` for exactly that
 * dead-radio state — confirmed as the mechanism behind this project's
 * multi-hour unattended outages. Nothing on THIS side needs to special-case
 * that anymore: a `false` here already flows through
 * delegatedSessionLiveness() -> isSessionAlive() into the ordinary
 * session-closed path, the same as any other "stopped" answer.
 *
 * `degraded`/`message` are present only in that dead-radio-but-group-alive
 * case, for a caller talking to the control API directly (an operator, a
 * monitoring script) — server/utils/session.ts's liveness policy does not
 * need them, since `running: false` alone already says enough for THIS
 * app's purposes. Detection only, per the brief that added this: nothing
 * here or in capture_control.py auto-restarts anything.
 */
interface ControlStatusResponse {
  running?: boolean
  pid?: number | null
  startedAt?: string
  request?: unknown
  degraded?: boolean
  message?: string
}

/**
 * Read a control-API JSON response body, falling back to raw text when it
 * isn't valid JSON (M1 in task-3-review.md: a proxy/gateway error or a
 * response truncated under load could hand back an HTML or plain-text body
 * instead of the control server's own JSON contract). Used by every function
 * in this file that talks to the control API, so a parse failure surfaces
 * something more useful than a bare "HTTP <status>" everywhere, not just in
 * whichever one happened to hit it first.
 */
async function readControlResponse<T>(res: Response): Promise<{ json: T | null, rawText: string | null }> {
  const text = await res.text().catch(() => null)
  if (text === null) return { json: null, rawText: null }
  try {
    return { json: JSON.parse(text) as T, rawText: text }
  } catch {
    return { json: null, rawText: text }
  }
}

/** The control-API error text, falling back to a truncated raw body when the response wasn't JSON at all. */
function controlErrorMessage(status: number, json: { error?: string } | null, rawText: string | null): string {
  if (json?.error) return json.error
  if (rawText) return `capture control API returned HTTP ${status}: ${rawText.slice(0, 200)}`
  return `capture control API returned HTTP ${status}`
}

/**
 * Turn ListenOptions into exactly the request scripts/capture_control.py's
 * build_args() can express: `{ mode: 'multi', ess?, includeEncrypted?,
 * durationSec }`. That server hardcodes `--pd` itself (emitted only when
 * durationSec is present) and rejects every field outside that set
 * (scripts/capture_control.py:96,:126) — it deliberately exposes ONE
 * operational profile, a bounded PD multi-receiver capture, not the full
 * surface lwin_listen_multi.sh supports when run locally.
 *
 * Anything ListenOptions can ask for that this shape cannot — single-receiver
 * mode, a different preset, talkgroup/tag/match selection, per-band receiver
 * counts, --stt, an unbounded run — is refused HERE, before any network call,
 * rather than silently dropped or substituted. Coercing `mode` to "multi" or
 * dropping `legs` would start a capture shaped differently from the one the
 * operator asked for, with no way for them to tell from the response; the
 * only safe response to a request this API cannot honor faithfully is to
 * refuse it loudly.
 *
 * `sessionId` is threaded through separately from `opts` (it is not, and
 * should not become, a ListenOptions field — it identifies OUR OWN
 * server/utils/session.ts row, not anything the operator chose) and is never
 * itself validated here: it is always this server's own `sessionStore.open()`
 * result, an internal autoincrement id, not operator-supplied input. The
 * control server validates it again anyway (scripts/capture_control.py's
 * build_args()) as every field there does, regardless of how trustworthy its
 * usual caller is.
 */
function buildControlRequest(
  opts: ListenOptions,
  sessionId?: number,
): { mode: 'multi'; ess?: boolean; includeEncrypted?: boolean; durationSec: number; sessionId?: number } {
  const unsupported: string[] = []
  if (opts.mode !== 'multi') {
    unsupported.push(
      `mode ${JSON.stringify(opts.mode ?? 'single')} (the capture container only runs multi-receiver captures)`,
    )
  }
  if (opts.preset !== undefined && opts.preset !== 'pd') {
    unsupported.push(`preset "${opts.preset}" (only the "pd" preset can be delegated)`)
  }
  if (opts.talkgroups !== undefined) unsupported.push('talkgroups (no remote talkgroup selection)')
  if (opts.tag !== undefined) unsupported.push('tag (no remote tag selection)')
  if (opts.match !== undefined) unsupported.push('match (no remote regex selection)')
  if (opts.allAreas) unsupported.push('allAreas')
  if (opts.includePartial) unsupported.push('includePartial')
  if (opts.stt) unsupported.push('stt (the capture container does not run the transcription watcher)')
  if (opts.legs !== undefined) unsupported.push('legs (fixed to 700,800 remotely)')
  if (opts.nVoice700 !== undefined) unsupported.push('nVoice700 (receiver counts are fixed remotely)')
  if (opts.nVoice800 !== undefined) unsupported.push('nVoice800 (receiver counts are fixed remotely)')
  if (opts.census !== undefined) unsupported.push('census (fixed remotely)')
  if (opts.duration === undefined) {
    unsupported.push('duration (required — the remote PD preset is only emitted when a duration is given)')
  }

  if (unsupported.length > 0) {
    throw new Error(
      'This request cannot be delegated to the capture container — unsupported: '
      + unsupported.join('; ')
      + '. The control API only supports a bounded PD capture '
      + '({ mode: "multi", ess, includeEncrypted, duration }). Run the full '
      + 'request directly on a host with HackRF access instead, for example: '
      + './scripts/lwin_listen_multi.sh --ess --include-encrypted --pd 10800',
    )
  }

  const body: { mode: 'multi'; ess?: boolean; includeEncrypted?: boolean; durationSec: number; sessionId?: number } = {
    mode: 'multi',
    durationSec: opts.duration as number,
  }
  if (opts.ess !== undefined) body.ess = opts.ess
  if (opts.includeEncrypted !== undefined) body.includeEncrypted = opts.includeEncrypted
  if (sessionId !== undefined) body.sessionId = sessionId
  return body
}

/**
 * POST the structured request to the capture container's control API and
 * translate its response into startListening()'s existing return shape.
 *
 * Never builds or sends a command line — only the small, validated object
 * buildControlRequest() produces. Building argv is scripts/capture_control.py's
 * job and its stated security boundary (see that file's module docstring);
 * assembling one here instead would defeat the whole point of having a
 * separate control server do exactly that.
 *
 * The returned `pid` is a number in the CAPTURE CONTAINER's own PID
 * namespace — display-only. It must NEVER be passed to process.kill(),
 * processStartTime(), or isOurListenSession(): those read/signal against
 * THIS process's namespace (the host's, via `pid: host`), where that number
 * means something else entirely — usually a low-numbered, root-owned kernel
 * thread, since nothing else has run in a fresh capture container's
 * namespace yet. Session liveness/stop for a delegated session goes through
 * delegatedSessionLiveness()/stopDelegatedCapture() instead, both of which
 * ask the control API itself rather than resolving this pid locally. See
 * task-3-review.md's Critical C1 for the full trace of what went wrong the
 * first time this pid was treated as host-signalable.
 */
async function delegateStart(
  opts: ListenOptions,
  sessionId?: number,
): Promise<{ pid: number; config: ListenOptions; backend: 'delegated' }> {
  const body = buildControlRequest(opts, sessionId) // throws before any network call for a request this API cannot honor

  let res: Response
  try {
    res = await fetch(`${CAPTURE_URL}/start`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    })
  } catch (err) {
    // Same failure class as `docker compose ps` showing the capture container
    // down or still (re)building — name the two commands an operator would
    // actually run, the same way the old in-container refusal named
    // ./scripts/lwin_listen_multi.sh.
    //
    // CORRECTED (final-review.md I2): this used to recommend
    // `./scripts/stack.sh restart capture`, which is exactly wrong to hand an
    // operator here — this message only ever fires when delegation could NOT
    // reach the control API, i.e. it does not know whether a capture is
    // already live in that container. `restart` stops-then-starts, so
    // recommending it risks taking down a healthy, unreachable-only-by-network
    // capture the operator can't see from here. `docker compose up -d capture`
    // is the safe verb instead: it starts the container if it is down, but
    // (unlike `restart`) does not touch an already-running one with unchanged
    // config — see docker-compose.yml's `command:` for the config this would
    // and would not consider changed.
    throw new Error(
      `Cannot reach the capture container's control API at ${CAPTURE_URL} `
      + `(${err instanceof Error ? err.message : String(err)}). Check it with `
      + './scripts/stack.sh status, or bring it up with docker compose up -d capture',
      { cause: err },
    )
  }

  const { json: payload, rawText } = await readControlResponse<ControlStartResponse>(res)

  if (!res.ok) {
    // Surfaced verbatim, the same way server/api/listen/start.post.ts's catch
    // already returns a thrown Error's message to the operator — 400
    // (validation), 409 (already running) and 502 (launcher died) all carry a
    // human-readable `error` field per the control server's contract.
    throw new Error(controlErrorMessage(res.status, payload, rawText))
  }
  if (typeof payload?.pid !== 'number') {
    throw new Error('capture control API reported success but returned no pid')
  }
  return { pid: payload.pid, config: opts, backend: 'delegated' }
}

/**
 * What GET /status can tell us about a delegated session, WITHOUT collapsing
 * "the control API answered definitively" into "the control API didn't
 * answer at all" — task-3-review.md's "unreachable != stopped" finding
 * (fix round 2). The previous version of this function (isDelegatedSessionAlive(),
 * a boolean) returned `false` for THREE different situations: a genuine
 * `running: false`, a network error/timeout, and a non-2xx response. Its own
 * comment claimed a false "stopped" from the latter two "merely re-attaches
 * on the next successful poll" — that was WRONG: session.ts's get() reacted
 * to `false` by calling close(), which is one-way (see close()'s own
 * docstring, "idempotent" meaning "safe to call again," not "reversible").
 * A single dropped TCP connection or Docker-bridge hiccup — reachable on ANY
 * one of the ~17,000 polls a 24h capture makes, or on the very next Stop
 * click — permanently untracked a healthy, still-running session, reproducing
 * a probabilistic version of the exact "on air · outside session" symptom
 * this whole project exists to fix.
 *
 * 'alive'   — the control API affirmatively confirms THIS pid is running.
 * 'stopped' — the control API affirmatively confirms it is NOT: either
 *             `running: false`, or `running: true` for a DIFFERENT pid (some
 *             OTHER capture is live — e.g. started from a shell against the
 *             same control API after this one ended — which is just as
 *             definitive an answer that OUR session is not it).
 * 'unknown' — no information either way: unreachable, timed out, a non-2xx
 *             response (capture_control.py's do_GET always answers /status
 *             with 200 when healthy — see that file's own docstring on
 *             snapshot() never crashing — so a non-2xx here means something
 *             is wrong with REACHING it, not an authoritative answer about
 *             what's running), or a 200 body this function can't parse.
 *
 * This function's only job is to preserve that three-way distinction, not to
 * decide what to DO with 'unknown' — that policy (how many consecutive
 * 'unknown's to tolerate before giving up) lives in session.ts's
 * isSessionAlive(), the only caller, which is where the actual close()
 * decision gets made.
 */
export type DelegatedLiveness = 'alive' | 'stopped' | 'unknown'

export async function delegatedSessionLiveness(pid: number): Promise<DelegatedLiveness> {
  let res: Response
  try {
    res = await fetch(`${CAPTURE_URL}/status`, { signal: AbortSignal.timeout(5000) })
  } catch {
    return 'unknown' // network error, DNS blip, or the timeout above firing
  }
  if (!res.ok) return 'unknown'
  const { json: payload } = await readControlResponse<ControlStatusResponse>(res)
  if (payload?.running === true) return payload.pid === pid ? 'alive' : 'stopped'
  if (payload?.running === false) return 'stopped'
  return 'unknown' // a 200 whose body this function doesn't recognize is not a real answer either
}

/**
 * Stop the delegated capture via the control API's own POST /stop, instead
 * of stopListening()'s local pid/pkill ladder — which has no meaningful pid
 * to signal in THIS namespace for a delegated session (see delegateStart()'s
 * comment), and would otherwise fall straight through to its untargeted final
 * `pkill -f` step. The control server holds the real pgid and runs the
 * identical SIGINT-then-SIGKILL ladder itself
 * (scripts/capture_control.py's CaptureState.stop()) — targeted beats lucky.
 *
 * 15s timeout: the control server's own ladder is bounded at 8s (SIGINT) + 2s
 * (SIGKILL fallback) = 10s worst case; this leaves margin above that rather
 * than racing it.
 */
export async function stopDelegatedCapture(): Promise<void> {
  let res: Response
  try {
    res = await fetch(`${CAPTURE_URL}/stop`, { method: 'POST', signal: AbortSignal.timeout(15_000) })
  } catch (err) {
    throw new Error(
      `Cannot reach the capture container's control API at ${CAPTURE_URL} to stop it `
      + `(${err instanceof Error ? err.message : String(err)}). The capture may still be running — `
      + 'check with ./scripts/stack.sh status.',
      { cause: err },
    )
  }
  if (!res.ok) {
    const { json: payload, rawText } = await readControlResponse<{ error?: string }>(res)
    throw new Error(controlErrorMessage(res.status, payload, rawText))
  }
  // {stopped: bool, pid?, forced?, message?} — POST /stop is idempotent
  // (200 either way; "nothing was running" is success, not an error), so
  // nothing further needs branching on here.
}

/**
 * Which of the two ways a session was started. Determines which liveness/stop
 * mechanism server/utils/session.ts uses for it: 'local' sessions get a real,
 * host-signalable pid (isOurListenSession()/stopListening()); 'delegated'
 * sessions get a pid meaningful only inside the capture container's own PID
 * namespace, so they go through delegatedSessionLiveness()/
 * stopDelegatedCapture() (the control API) instead. Getting this wrong for a
 * delegated session resolves a foreign host pid instead — see
 * task-3-review.md's Critical C1.
 */
export type SessionBackend = 'local' | 'delegated'

export async function startListening(
  opts: ListenOptions,
  sessionId?: number,
): Promise<{ pid: number; config: ListenOptions; backend: SessionBackend }> {
  // Capability, not location. See captureCapabilityGap() above for why this
  // replaced the old inContainer() check. When this process genuinely can
  // reach the HackRFs itself (a bare-metal/dev host, not either container),
  // spawn exactly as before. Otherwise — the `web` container's normal case —
  // this process cannot run op25 itself, but the `capture` container can, so
  // delegate to it over the control API instead of refusing outright.
  if (captureCapabilityGap() === null) {
    const script = join(scriptsDir(), scriptFor(opts.mode))
    const fd = openSync(listenLogPath(), 'w')
    try {
      const child = spawn('bash', [script, ...buildListenArgs(opts)], {
        cwd: sdrRoot(),
        detached: true,               // setsid: child.pid becomes the process-group leader
        stdio: ['ignore', fd, fd],
        // Inherited by bash and then by udp_audio_record.py, so the recorder can
        // stamp session_id on each call without threading an argument through
        // the shell script.
        env: sessionId === undefined
          ? process.env
          : { ...process.env, SDR_SESSION_ID: String(sessionId) },
      })
      child.unref()

      if (!child.pid) throw new Error(`failed to spawn ${scriptFor(opts.mode)}`)
      return { pid: child.pid, config: opts, backend: 'local' }
    } finally {
      closeSync(fd)                 // the child holds its own dup; not closing leaks an fd per session
    }
  }

  return delegateStart(opts, sessionId)
}

/**
 * Signal `target`, treating "no such process" as success.
 *
 * The group can exit between an isProcessRunning() check and the signal —
 * routine for --stt and duration-limited sessions — and an unguarded ESRCH
 * would surface as a 500 for what was actually a clean stop. Anything else
 * (EPERM, EINVAL) is a real fault and must not be swallowed: silently
 * reporting a successful stop while op25 still holds the HackRF is the worst
 * possible outcome here.
 *
 * @returns true if the signal was delivered, false if the target was already gone.
 */
function signalOrAlreadyGone(target: number, signal: NodeJS.Signals): boolean {
  try {
    process.kill(target, signal)
    return true
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ESRCH') return false
    throw err
  }
}

/**
 * The pattern that actually matches op25's receiver.
 *
 * NOT 'gr-op25_repeater/apps/rx.py'. lwin_listen.sh runs it as
 * `cd <apps dir> && exec python3 rx.py ...`, so the receiver's argv is
 * `python3 rx.py --args soapy=...` with no path in it, and the `script` wrapper
 * holds `.../gr-op25_repeater/apps && exec python3 rx.py`. Neither contains
 * `/apps/rx.py` contiguously, so that pattern matches ZERO processes —
 * including in lwin_listen.sh's own cleanup trap, where it has silently never
 * worked. Verified against the real argv of a stranded receiver.
 */
const RX_PATTERN = 'python3 rx\\.py --args'

/**
 * The multi-receiver equivalent, and the reason this is a separate constant.
 *
 * `rx\.py` does NOT match `multi_rx.py --args`: lwin_listen_multi.sh runs op25
 * as `cd <apps dir> && exec python3 multi_rx.py -c <config> -v 10`, so there is
 * no `--args` in its argv at all. Left unmatched, a stranded multi_rx holding
 * BOTH HackRFs reads as "radio free": Stop returns early without pkill-ing it,
 * and the next Start contends for two radios instead of one.
 */
const MULTI_RX_PATTERN = 'python3 multi_rx\\.py'

/** Every op25 receiver pattern that means "a radio is in use". */
const RADIO_PATTERNS = [RX_PATTERN, MULTI_RX_PATTERN]

/**
 * The recorder. It does not hold the HackRF — it listens on a UDP port — but it
 * belongs to the same session and holds that port, so a stranded one blocks the
 * next Start just as surely.
 */
const RECORDER_PATTERN = 'udp_audio_record\\.py'

/**
 * Is op25 holding the radio right now, regardless of what we think we started?
 *
 * The contended resource is a HackRF, not a pid. `lwin_listen.sh` can die —
 * externally killed, or SIGKILLed after ignoring SIGINT — while the rx.py it
 * launched survives, because `script -c` puts that behind a pty in its own
 * session. In that state the pid-based view says "no session running" and Stop
 * answers 409 while the radio is still in use and a fresh Start would contend
 * for it.
 *
 * Matched on the full argv path so this cannot collide with, say, an editor
 * that happens to have rx.py open.
 */
export function isRadioBusy(): boolean {
  // -f matches the full argv. Both the receiver and the `script` wrapper
  // carrying it count as busy. Either launcher's receiver counts.
  return RADIO_PATTERNS.some((pattern) => {
    try {
      const out = execFileSync('pgrep', ['-f', pattern], {
        encoding: 'utf-8',
        timeout: 2000,
      })
      return out.trim().length > 0
    } catch {
      // pgrep exits 1 with no output when nothing matches — that is "not busy",
      // not a failure.
      return false
    }
  })
}

export async function stopListening(pid: number, procStart: number | null = null): Promise<void> {
  // pid 0 means "no tracked session, just release the radio". Guard it
  // explicitly: kill(0, sig) signals OUR OWN process group, and kill(-0) is
  // the same thing — which would take down this server.
  const havePid = pid > 0

  // Identity, not just liveness. Signalling -pid hits a whole process GROUP, and
  // process.kill only refuses OTHER users' processes — so a recycled pid would
  // put every process the operator owns in range of a SIGKILL.
  if (havePid && !isOurListenSession(pid, procStart)) return
  if (!havePid && !isRadioBusy()) return

  // Negative pid = whole process group. If the group is already gone, fall back
  // to the bare pid in case the child was never a group leader.
  if (havePid && !signalOrAlreadyGone(-pid, 'SIGINT')) {
    signalOrAlreadyGone(pid, 'SIGINT')
  }

  // Wait for the RADIO to be released, not merely for bash to exit. rx.py runs
  // behind a pty in its own session, so it can outlive the process group; the
  // pty hangup normally takes it down, but "normally" is not a guarantee worth
  // reporting success on.
  for (let waited = 0; waited < 8000; waited += 200) {
    if ((!havePid || !isProcessRunning(pid)) && !isRadioBusy()) break
    await new Promise(r => setTimeout(r, 200))
  }

  if (havePid && isProcessRunning(pid)) {
    signalOrAlreadyGone(-pid, 'SIGKILL')
  }

  // Last resort: the script's own cleanup trap does this too, but if the script
  // never ran its trap (SIGKILLed, or killed from outside) nothing else will.
  // SIGTERM first so udp_audio_record.py can flush the call in progress.
  const pkillErrors: string[] = []
  for (const pattern of [...RADIO_PATTERNS, RECORDER_PATTERN]) {
    const stderr = pkillPattern(pattern)
    if (stderr) pkillErrors.push(stderr)
  }

  // Verify against the RADIO, not against pkill's own exit code. procps pkill
  // exits 0 as soon as a pattern MATCHES, regardless of whether the kill()
  // syscall on any matched pid actually succeeded — so a signal refused with
  // EPERM for every single pid still looks like success to the old
  // `catch { /* matched nothing */ }` above pkillPattern() replaced.
  //
  // THE ACTUAL BOUNDARY (measured, not reasoned from the AppArmor template —
  // see final-review.md's I1, "the tenth instance"): it is HOST vs. CONTAINER,
  // not "did this process spawn the target". `docker-default`'s template
  // grants `signal (send,receive) peer=docker-default` — so this container
  // CAN signal a process in ANY other `docker-default` container (in
  // particular `capture`, the one place a delegated session's op25 actually
  // runs), and only fails EPERM against the HOST, which is unconfined and so
  // not a `docker-default` peer. `pid: host` giving read access to the host's
  // /proc (pgrep, and therefore isRadioBusy(), works fine) does not imply
  // signal access follows the same rule — reads and signals are governed by
  // different mechanisms entirely (namespace visibility vs. AppArmor peer
  // matching). A prior version of this comment claimed AppArmor blocks this
  // container from signalling ANY process "it did not start itself" and the
  // ledger recorded that as a correction of an earlier error; both were
  // wrong; see final-review.md and progress.md's correction entry.
  // CAUTION for anyone re-verifying this: `kill -0` is NOT mediated by
  // AppArmor and will falsely read as "permitted" against a host pid that a
  // real signal refuses — test with an actual signal (e.g. SIGCONT), never
  // `kill -0`.
  //
  // Practical consequence for THIS function: it is reachable only for a
  // 'local' (host-started) session (a delegated session is stopped via
  // stopDelegatedCapture() instead — see that function). Every pattern here
  // still targets host-visible process names, so in the ordinary case any
  // pid this pkill actually reaches IS a host process and DOES fail EPERM,
  // exactly as before — that part of the old reasoning happened to hold. But
  // if a capture container is ALSO running concurrently (stack.sh's own
  // "unexpected, check both" status line already treats that as reachable),
  // the same unscoped `pkill -f` ALSO matches the capture container's op25
  // and recorders by host-visible pid, and — unlike the host case — that
  // signal is DELIVERED. So this is not dead code and not merely a permission
  // check: it can silently kill a healthy, unrelated delegated capture as a
  // side effect of releasing a stranded HOST radio. Whether to change that
  // behavior (e.g. scope this pkill the same way lwin_listen_multi.sh's own
  // cleanup trap now is — see that file's cleanup()) is a decision for
  // whoever owns this fix, not implied by this comment; this comment only
  // states what the AppArmor boundary actually is.
  //
  // Reporting success on an EPERM-refused kill(), as before this fix, means
  // the console lies about having released a radio that is still on the air
  // — precisely the "on air · outside session" state this whole feature
  // exists to distinguish honestly. isRadioBusy() is the one signal that
  // cannot be spoofed by a refused kill(): it reads the host's /proc
  // directly, so it still reflects host reality even when signalling does
  // not — but it says nothing about the capture container's own state, which
  // this pkill can change without isRadioBusy() ever noticing.
  if (isRadioBusy()) {
    const detail = pkillErrors.length > 0 ? ` (${pkillErrors.join('; ')})` : ''
    // Built from the same patterns isRadioBusy()/the loop above just tried,
    // not hand-copied, so this can never drift from what actually needs
    // releasing (single-mode rx.py, multi-mode multi_rx.py, or the
    // recorders — whichever combination is actually stranded).
    const recovery = [...RADIO_PATTERNS, RECORDER_PATTERN]
      .map(pattern => `pkill -INT -f "${pattern}"`)
      .join('; ')
    throw new Error(
      `Could not release the radio${detail}. This container can see the `
      + "host's processes via /proc, but its AppArmor confinement (docker-default) "
      + 'blocks it from signalling the HOST specifically — not "any process it '
      + 'did not start" (it can signal a peer container fine; see '
      + 'server/utils/processes.ts\'s comment above this throw for the measured '
      + `boundary). Release it on the host instead: ${recovery}`,
    )
  }
}

/**
 * Send SIGTERM to every process matching `pattern` via pkill, and return
 * whatever it printed to stderr — or null if it printed nothing.
 *
 * Deliberately spawnSync(), not execFileSync(): execFileSync() only exposes
 * stderr when the child exits non-zero, and pkill exits 0 as soon as its
 * pattern matches at least one process, even if delivering the signal to
 * every matched pid failed. spawnSync() returns stdout/stderr unconditionally,
 * which is the only way to see a per-pid "killing pid NNNN failed:
 * Permission denied" line on an exit code that otherwise reads as success.
 */
function pkillPattern(pattern: string): string | null {
  const result = spawnSync('pkill', ['-f', pattern], { timeout: 2000, encoding: 'utf-8' })
  const stderr = (result.stderr ?? '').trim()
  return stderr.length > 0 ? stderr : null
}
