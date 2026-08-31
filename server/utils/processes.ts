import { spawn } from 'node:child_process'
import { readFileSync, statSync, openSync, readSync, closeSync } from 'node:fs'
import { join } from 'node:path'
import { scriptsDir, sdrRoot, listenLogPath } from './paths'

/**
 * Mirrors scripts/lwin_listen.sh's flags 1:1. Deliberately NOT typed with
 * `Encryption` — that is the DB's label vocabulary ('clear'|'partial'|'full').
 * These two booleans are independent listen-scope switches; conflating them
 * into one enum makes "partial AND full" unreachable.
 */
export interface ListenOptions {
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
    // cmdline is NUL-separated; lwin_listen.sh appears as a bash argument.
    const cmdline = readFileSync(`/proc/${pid}/cmdline`, 'utf-8')
    return cmdline.includes('lwin_listen.sh')
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
export function startListening(opts: ListenOptions): { pid: number; config: ListenOptions } {
  const script = join(scriptsDir(), 'lwin_listen.sh')
  const fd = openSync(listenLogPath(), 'w')
  try {
    const child = spawn('bash', [script, ...buildListenArgs(opts)], {
      cwd: sdrRoot(),
      detached: true,               // setsid: child.pid becomes the process-group leader
      stdio: ['ignore', fd, fd],
    })
    child.unref()

    if (!child.pid) throw new Error('failed to spawn lwin_listen.sh')
    return { pid: child.pid, config: opts }
  } finally {
    closeSync(fd)                   // the child holds its own dup; not closing leaks an fd per session
  }
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

export async function stopListening(pid: number, procStart: number | null = null): Promise<void> {
  // Identity, not just liveness. Signalling -pid hits a whole process GROUP, and
  // process.kill only refuses OTHER users' processes — so a recycled pid would
  // put every process the operator owns in range of a SIGKILL.
  if (!isOurListenSession(pid, procStart)) return

  // Negative pid = whole process group. If the group is already gone, fall back
  // to the bare pid in case the child was never a group leader.
  if (!signalOrAlreadyGone(-pid, 'SIGINT')) {
    signalOrAlreadyGone(pid, 'SIGINT')
  }

  for (let waited = 0; waited < 8000 && isProcessRunning(pid); waited += 200) {
    await new Promise(r => setTimeout(r, 200))
  }

  if (isProcessRunning(pid)) {
    // Group-scoped, so rx.py cannot be stranded holding the HackRF.
    signalOrAlreadyGone(-pid, 'SIGKILL')
  }
}
