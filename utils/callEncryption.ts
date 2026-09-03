/**
 * What a recorded call's ESS actually tells us about its encryption, and which
 * stock a strip carrying it should be printed on.
 *
 * Extracted from components/bay/CallStrip.vue so it can be unit tested: vitest
 * collects only server/**\/*.test.ts and utils/**\/*.test.ts (see
 * vitest.config.ts), and this is exactly the kind of classification that rots
 * silently inside a .vue computed — the bug this file exists to fix lived there
 * for the life of the component with nothing able to fail on it.
 *
 * THE BUG THIS REPLACES
 * ----------------------
 * Both the strip and the scanner queue asked one question — `algid === 170` —
 * and treated every other value as clear:
 *
 *     const locked = call.algid === ADP_ALGID && !heldKeyIds.includes(keyid)
 *     if (locked) return 'locked'
 *     if (call.algid === ADP_ALGID) return 'keyed'
 *     return body ? 'clear' : 'void'
 *
 * That final `return` is the lie. It fires for algid 0x08, 0x0E, 0x45, 0x48,
 * 0x82, 0xA8, 0xAB, 0xB8 — all of them encrypted under something, none of them
 * ADP — and prints them on clear stock with whatever Whisper made of the
 * undecodable audio presented as a transcript. Eight calls in a corpus of
 * 11,743 is a small blast radius; a confident wrong answer where an honest
 * "cannot read this" was available is not a small class of error.
 *
 * WHY THE EIGHT ONE-OFF ALGIDS GET ONE SHARED CASE, NOT EIGHT
 * ------------------------------------------------------------
 * Measured over the whole corpus (11,743 calls) the distribution is:
 *
 *     algid  null   9078   no ESS captured at all
 *     algid  0x80   2349   genuinely clear
 *     algid  0xAA    308   ADP
 *     algid  0x08 / 0x0E / 0x45 / 0x48 / 0x82 / 0xA8 / 0xAB / 0xB8
 *                       1 each
 *
 * Exactly one call apiece is the signature of bit errors in the ESS, not of
 * eight distinct algorithms in use on this system — note that 0xA8 and 0xAB
 * are each one bit from 0xAA, and 0x82 is one bit from 0x80. Naming them
 * individually ("AES-256", "DES-OFB") would dress up corrupted bits as
 * intelligence. They collapse into a single `'unhandled'` state whose label
 * prints the raw byte, so an operator can see for themselves that a lone 0x08
 * next to 308 ADP calls is a flipped bit rather than a second algorithm.
 *
 * WHY `null` IS ITS OWN STATE AND STILL RENDERS AS IT DID
 * -------------------------------------------------------
 * 9,078 calls — 77% of the corpus — carry no algid at all, because no ESS was
 * captured for them. That is not evidence of clear traffic and it is not
 * evidence of encryption; it is the absence of evidence. Badging 77% of the
 * bay with a warning would be the cry-wolf failure this project has spent days
 * removing elsewhere (see utils/captureStatus.ts's STALL_GRACE_MS), so
 * `'unknown'` maps to the same clear/void stock it has always been printed on.
 * The change is in what the CODE asserts: `null` now resolves to an explicit
 * `'unknown'` rather than falling out of a "not ADP, therefore clear" branch,
 * so the next thing to ask this module a question gets told the truth.
 */

/** P25 ADP. The only encryption algorithm this console can key. */
export const ADP_ALGID = 170        // 0xAA

/**
 * P25's "no encryption" algorithm id. An ESS carrying 0x80 is a positive
 * assertion of clear traffic — the only value that earns clear stock.
 */
export const CLEAR_ALGID = 128      // 0x80

/** The encryption fields of a call. `FeedCall` satisfies this structurally. */
export interface CallEncryptionFields {
  algid: number | null
  keyid: number | null
}

export type Encryption =
  /** ESS said 0x80: unencrypted, and we know it. */
  | 'clear'
  /** ADP under a key id we hold. Decodes. */
  | 'keyed'
  /** ADP under a key id we do not hold. Recorded, not decoded. */
  | 'locked'
  /** Encrypted under an algorithm this console does not implement. */
  | 'unhandled'
  /** No ESS captured. Encryption state genuinely unknown. */
  | 'unknown'

/**
 * Classify one call's encryption.
 *
 * `heldKeyIds` is a list of key IDS, never key MATERIAL — ids are already on
 * screen and are not secret; the material never leaves the server.
 *
 * The `keyid ?? -1` is deliberate and is not a default: a call really can be
 * encrypted with no key id in its ESS, and -1 is a value no key can have, so
 * the lookup misses and the call reads as locked. Substituting 0 there would
 * assert a specific valid key that was never on the air.
 */
export function encryptionState(
  call: CallEncryptionFields,
  heldKeyIds: ReadonlySet<number>,
): Encryption {
  if (call.algid === null || call.algid === undefined) return 'unknown'
  if (call.algid === CLEAR_ALGID) return 'clear'
  if (call.algid !== ADP_ALGID) return 'unhandled'
  // A Set rather than an array-or-Set union: the scanner queue already holds
  // one, and the strip's `heldKeyIds` prop is a handful of ids it can wrap.
  // Accepting both would need either a type assertion or `in`-operator
  // narrowing, since `Array.isArray` does not narrow a `readonly number[]`
  // out of a union — neither is worth carrying to save one `new Set(...)`.
  return heldKeyIds.has(call.keyid ?? -1) ? 'keyed' : 'locked'
}

/**
 * The stock a strip is printed on: the call's CLASS, never its state.
 *
 * Four stocks, matching assets/css/bay.css's `.strip--*` — deliberately fewer
 * than there are encryption states. `'unhandled'` shares `'locked'`'s stock
 * because the operator-facing fact is identical (encrypted, cannot be played,
 * cocked out of the rail); only the printed label differs, and the component
 * takes that from `encryptionState()` rather than from the stock. A fifth
 * stock tint for eight calls in 11,743 would be a new colour in the design
 * system carrying no information the label does not already carry.
 */
export type StripStock = 'clear' | 'keyed' | 'locked' | 'void'

/**
 * `hasBody` is whether the strip has a real transcript to print — not whether
 * one exists in the database. A `[BLANK_AUDIO]` transcript is not speech.
 *
 * `'keyed'` is decided BEFORE the body check on purpose: an ADP call we hold
 * the key for is keyed stock whether or not Whisper has got to it yet, so the
 * green key label stays on the strip through the not-transcribed-yet window
 * instead of the strip dropping to void stock and losing it.
 */
export function stripStock(encryption: Encryption, hasBody: boolean): StripStock {
  if (encryption === 'locked' || encryption === 'unhandled') return 'locked'
  if (encryption === 'keyed') return 'keyed'
  // 'clear' and 'unknown': one we know is in the open, one we have no evidence
  // either way about. Same stock, and the difference is carried by the type,
  // not by a badge on 77% of the bay.
  return hasBody ? 'clear' : 'void'
}
