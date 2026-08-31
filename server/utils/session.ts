import { readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import { isProcessRunning } from './processes'
import { listenPidPath, listenConfigPath, listenStartedPath } from './paths'
import type { ListenOptions } from './processes'

export interface Session {
  pid: number
  config: ListenOptions
  startTime: number
}

let current: Session | null = null

function persist(s: Session): void {
  writeFileSync(listenPidPath(), String(s.pid))
  writeFileSync(listenConfigPath(), JSON.stringify(s.config))
  writeFileSync(listenStartedPath(), String(s.startTime))
}

/** Recover a session started before this server process existed. */
function recover(): Session | null {
  try {
    const pid = Number.parseInt(readFileSync(listenPidPath(), 'utf-8').trim(), 10)
    if (!Number.isFinite(pid) || !isProcessRunning(pid)) return null

    let config: ListenOptions = {}
    try {
      config = JSON.parse(readFileSync(listenConfigPath(), 'utf-8')) as ListenOptions
    } catch { /* config is a nicety; the pid is what matters for Stop */ }

    let startTime = Date.now() / 1000
    try {
      const t = Number.parseFloat(readFileSync(listenStartedPath(), 'utf-8').trim())
      if (Number.isFinite(t)) startTime = t
    } catch { /* fall back to now */ }

    return { pid, config, startTime }
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
    if (current && !isProcessRunning(current.pid)) {
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
