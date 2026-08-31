import { readFileSync, writeFileSync, unlinkSync, renameSync } from 'node:fs'
import { isOurListenSession } from './processes'
import { listenPidPath, listenConfigPath, listenStartedPath } from './paths'
import type { ListenOptions } from './processes'

export interface Session {
  pid: number
  config: ListenOptions
  startTime: number
  /**
   * /proc/<pid>/stat field 22. A pid is not an identity — the kernel recycles
   * pid numbers, and a stale sidecar survives reboots. Pairing the pid with the
   * process's start time makes recovery verifiable, because the kernel will
   * never reissue the same (pid, procStart) pair.
   */
  procStart: number | null
}

let current: Session | null = null

/**
 * Write via a temp file and rename, so a crash or a full disk cannot leave a
 * TRUNCATED pid behind. `12345` cut short to `12` is still a valid pid pointing
 * at something arbitrary, which is the same blast radius as recycling without
 * needing the kernel to wrap around.
 */
function writeAtomic(path: string, contents: string): void {
  const tmp = `${path}.tmp`
  writeFileSync(tmp, contents)
  renameSync(tmp, path)
}

function persist(s: Session): void {
  // pid and procStart travel together: a pid without its start time cannot be
  // verified on recovery.
  writeAtomic(listenPidPath(), `${s.pid} ${s.procStart ?? ''}`.trim())
  writeAtomic(listenConfigPath(), JSON.stringify(s.config))
  writeAtomic(listenStartedPath(), String(s.startTime))
}

/** Recover a session started before this server process existed. */
function recover(): Session | null {
  try {
    const raw = readFileSync(listenPidPath(), 'utf-8').trim().split(/\s+/)
    const pid = Number.parseInt(raw[0], 10)
    const procStart = raw[1] ? Number.parseInt(raw[1], 10) : null

    // Verify identity, not just liveness. Without this a recycled pid means
    // Stop SIGINTs — then SIGKILLs — an unrelated process GROUP.
    if (!Number.isFinite(pid) || !isOurListenSession(pid, procStart)) {
      // The recorded process is gone or is not ours. Clear the sidecars now:
      // leaving them means the stale window never closes and we sit waiting for
      // the kernel to recycle that pid number onto something innocent.
      removeSidecars()
      return null
    }

    let config: ListenOptions = {}
    try {
      config = JSON.parse(readFileSync(listenConfigPath(), 'utf-8')) as ListenOptions
    } catch { /* config is a nicety; the pid is what matters for Stop */ }

    let startTime = Date.now() / 1000
    try {
      const t = Number.parseFloat(readFileSync(listenStartedPath(), 'utf-8').trim())
      if (Number.isFinite(t)) startTime = t
    } catch { /* fall back to now */ }

    return { pid, config, startTime, procStart }
  } catch {
    return null
  }
}

function removeSidecars(): void {
  for (const p of [listenPidPath(), listenConfigPath(), listenStartedPath()]) {
    try {
      unlinkSync(p)
    } catch (err) {
      // ENOENT means the file is already gone, which is the desired end state.
      // Anything else (EACCES, EISDIR) is a real fault worth surfacing.
      if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err
    }
  }
}

export const sessionStore = {
  set(s: Session): void {
    current = s
    persist(s)
  },

  /**
   * The live session, from memory or recovered from the sidecar files.
   * Clears both when the process is gone.
   */
  get(): Session | null {
    if (current && !isOurListenSession(current.pid, current.procStart)) {
      current = null
      removeSidecars()
    }
    if (!current) {
      current = recover()
    }
    return current
  },

  clear(): void {
    current = null
    removeSidecars()
  },

  isRunning(): boolean {
    return this.get() !== null
  },
}
