import { join, resolve, basename } from 'node:path'

// `TG\d+` covers all 3,232 current recordings. `TGunknown` is also accepted:
// udp_audio_record.py:86 emits TGunknown_<stamp>.wav when no grant matched the
// audio, and server.py could still serve those. None exist today, but rejecting
// them here would make them silently unplayable.
const RECORDING_NAME = /^TG(?:\d+|unknown)_[A-Za-z0-9.-]+_\d{8}-\d{6}(?:_\d+)?\.(wav|txt)$/

export function sdrRoot(): string {
  return process.env.SDR_ROOT || '/home/besquivel/rtl'
}

export function recordingsDir(): string {
  return join(sdrRoot(), 'recordings')
}

export function referenceDir(): string {
  return join(sdrRoot(), 'reference')
}

export function scriptsDir(): string {
  return join(sdrRoot(), 'scripts')
}

// The Nitro server owns all four of these. Nothing in scripts/ writes them.
// All are already in .gitignore from the Python server era.
export function listenLogPath(): string {
  return join(sdrRoot(), 'web', 'listen.log')
}

// listen.pid / listen.config.json / listen.started are gone: session state is a
// row in sdr.db's `sessions` table, which also gives a history and a foreign
// key for calls.session_id. Only the log remains a file, because it is op25's
// stdout.

export function whitelistPath(): string {
  return join(sdrRoot(), 'lwin_active_whitelist.txt')
}

/**
 * Resolve a user-supplied recording filename to an absolute path,
 * or null if it is not a legal recording name or escapes the directory.
 */
export function safeRecordingPath(name: string): string | null {
  if (basename(name) !== name) return null
  if (!RECORDING_NAME.test(name)) return null

  const dir = recordingsDir()
  const full = resolve(dir, name)
  if (!full.startsWith(dir + '/')) return null
  return full
}
