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

/**
 * `path` exists so tests can point at a fixture instead of the live keyfile.
 *
 * Not a general-purpose knob: production always takes the default. It is here
 * because the alternative — asserting exact key ids against the live,
 * unversioned keyfile — gives anyone facing a red test a one-line, no-diff way
 * to make it green by editing operational secret material instead of code.
 * That is not hypothetical; it happened during this task's first
 * implementation.
 */
export function heldKeyIds(path: string = keysPath()): number[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(readFileSync(path, 'utf-8'))
  } catch {
    return []
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return []
  }

  const ids: number[] = []
  const dropped: string[] = []
  for (const raw of Object.keys(parsed)) {
    // Keys are written "0x1", "0x8", "0x2F08". parseInt with radix 16 accepts
    // the 0x prefix, so both spellings parse.
    const n = Number.parseInt(raw, 16)
    if (Number.isInteger(n)) ids.push(n)
    else dropped.push(raw)
  }

  // A dropped id is the one silence here that loses information.
  //
  // Every other failure is all-or-nothing and announces itself: an absent or
  // corrupt keyfile yields an empty set, so nothing decodes and the operator
  // notices immediately. But ONE malformed id among many valid ones returns
  // successfully with every other key intact — and surfaces only as a single
  // talkgroup that will not decode, with nothing pointing at the keyfile.
  //
  // Key IDS are not secret: they travel in the clear in every P25 ESS field.
  // Key BYTES are. Log the id only, never the entry it maps to.
  if (dropped.length > 0) {
    console.warn(
      `heldKeyIds: ignoring ${dropped.length} unparseable key id(s) in ${path}: `
      + dropped.join(', '),
    )
  }

  // Deduped: "0x8" and "8" are different JSON keys that parse to the same id.
  return [...new Set(ids)].sort((a, b) => a - b)
}
