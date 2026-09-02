import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { sdrRoot } from './paths'

/**
 * Which ADP key ids we hold — and nothing else about them.
 *
 * lwin_keys.json is op25's keyfile: it maps a key id to the five key BYTES
 * recovered for it. That material must never reach the browser. The console
 * needs one bit per key id: held or not, which is what separates an encrypted
 * call that will decode to speech from one that will decode to noise.
 *
 * The file is gitignored and may be absent on a fresh checkout, so a missing
 * or malformed file yields an empty set rather than an error — the feed then
 * treats every encrypted call as unplayable, which is the safe reading.
 */

export function keysPath(): string {
  return join(sdrRoot(), 'lwin_keys.json')
}

export function heldKeyIds(): number[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(readFileSync(keysPath(), 'utf-8'))
  } catch {
    return []
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return []
  }
  return Object.keys(parsed)
    // Keys are written "0x1", "0x8", "0x2F08". parseInt with radix 16 accepts
    // the 0x prefix, so both spellings parse.
    .map(k => Number.parseInt(k, 16))
    .filter(n => Number.isInteger(n))
    .sort((a, b) => a - b)
}
