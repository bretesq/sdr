import { spawn, execFileSync } from 'node:child_process'
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
 */
function buildControlRequest(
  opts: ListenOptions,
): { mode: 'multi'; ess?: boolean; includeEncrypted?: boolean; durationSec: number } {
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

  const body: { mode: 'multi'; ess?: boolean; includeEncrypted?: boolean; durationSec: number } = {
    mode: 'multi',
    durationSec: opts.duration as number,
  }
  if (opts.ess !== undefined) body.ess = opts.ess
  if (opts.includeEncrypted !== undefined) body.includeEncrypted = opts.includeEncrypted
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
 * NOTE: unlike the local-spawn path below, this cannot pass `sessionId`
 * through — scripts/capture_control.py's request shape has no field for it
 * (see ALLOWED_FIELDS there), so op25/udp_audio_record.py run inside the
 * capture container with no SDR_SESSION_ID in their environment and every
 * call they record lands with a NULL session_id. This is a real gap between
 * Task 2's contract and session-linked recordings; flagged in this task's
 * report rather than silently worked around, since closing it means changing
 * scripts/capture_control.py's request shape, which is Task 2's file.
 */
async function delegateStart(opts: ListenOptions): Promise<{ pid: number; config: ListenOptions }> {
  const body = buildControlRequest(opts) // throws before any network call for a request this API cannot honor

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
    throw new Error(
      `Cannot reach the capture container's control API at ${CAPTURE_URL} `
      + `(${err instanceof Error ? err.message : String(err)}). Check it with `
      + './scripts/stack.sh status, or bring it up with ./scripts/stack.sh restart capture',
      { cause: err },
    )
  }

  const payload = await res.json().catch(() => null) as ControlStartResponse | null

  if (!res.ok) {
    // Surfaced verbatim, the same way server/api/listen/start.post.ts's catch
    // already returns a thrown Error's message to the operator — 400
    // (validation), 409 (already running) and 502 (launcher died) all carry a
    // human-readable `error` field per the control server's contract.
    throw new Error(payload?.error ?? `capture control API returned HTTP ${res.status}`)
  }
  if (typeof payload?.pid !== 'number') {
    throw new Error('capture control API reported success but returned no pid')
  }
  return { pid: payload.pid, config: opts }
}

export async function startListening(
  opts: ListenOptions,
  sessionId?: number,
): Promise<{ pid: number; config: ListenOptions }> {
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
      return { pid: child.pid, config: opts }
    } finally {
      closeSync(fd)                 // the child holds its own dup; not closing leaks an fd per session
    }
  }

  return delegateStart(opts)
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
  for (const pattern of [...RADIO_PATTERNS, RECORDER_PATTERN]) {
    try {
      execFileSync('pkill', ['-f', pattern], { timeout: 2000 })
    } catch {
      // pkill exits non-zero when it matched nothing, which is the goal state.
    }
  }
}
