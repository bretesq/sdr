/**
 * What a TALKGROUP's traffic tells us about its encryption, rolled up from the
 * calls actually recorded on it.
 *
 * utils/callEncryption.ts answers the same question for one call, and this file
 * is deliberately its neighbour rather than its rival: the algid constants and
 * the "absence of evidence is not evidence" discipline come from there, so a
 * talkgroup's badge and its calls' strips can never disagree about what ADP is.
 *
 * WHY A ROLLUP AT ALL — THE FAILURE THIS FIXES
 * ---------------------------------------------
 * A call strip has said "no key held" since 6b0e5e0, but the talkgroup LIST
 * said nothing, so the operator could not tell that 17-BRPD DSP1 is routinely
 * encrypted until they armed it, waited, played a clip and heard nothing. The
 * fact was in the database the whole time; only the roster was silent.
 *
 * WHY NOT `talkgroups.enc`
 * -------------------------
 * The roster carries a scraped RadioReference label — 'clear' (3,193 rows),
 * 'full' (856) and 'partial' (114). It is NOT empty, and this file does use it
 * (see `listed` below), but it cannot be the primary source, because measured
 * against what was actually on the air it is wrong in both directions:
 *
 *   19014  24-PPD DISP    listed CLEAR   observed 62 ADP,  1 clear
 *   17086  17 JAIL SEC1   listed FULL    observed  2 ADP, 110 clear
 *
 * The single most-encrypted talkgroup on this system is listed clear. Twenty-six
 * listed-clear talkgroups have carried ADP. So a listed-clear claim earns no
 * badge here at all, and a listed-encrypted claim earns only the weak "listed"
 * badge that says where it came from — while any talkgroup we have actually
 * heard is labelled from what we heard.
 *
 * WHY THE RATIO, AND WHY THESE THRESHOLDS
 * ----------------------------------------
 * Encryption on this system is not a per-talkgroup flag. The console that this
 * bay replaced had a checkbox reading "Include partially-encrypted TGs (BRPD /
 * EBR SO)", which is the shape of the real thing — measured over 11,886 calls
 * at time of writing (a capture is always running, so these counts grow; the
 * thresholds are set by the SHAPE of the distribution, not by these figures):
 *
 *   19014  24-PPD DISP     62 ADP /   1 clear   ratio 0.984
 *   17094  17-SO Court 2   21 ADP /   3 clear   ratio 0.875
 *   17165  17-BRPD DSP1    47 ADP /  79 clear   ratio 0.373
 *   17171  17-BRPD DSP4    26 ADP /  83 clear   ratio 0.239
 *   17051  17-SO DISP S     6 ADP / 212 clear   ratio 0.028
 *   17164  17-BRPD CIU      2 ADP /   0 clear   ratio 1.000
 *
 * ENCRYPTED_RATIO is 0.8 rather than 0.9 because 0.9 loses 17-SO Court 2, a
 * talkgroup whose three clear calls out of twenty-four are plainly the
 * exception; and it is not lower because BRPD's dispatch pair at 0.37 is
 * genuinely half-readable and must not be written off as dark. 17-SO K-9 sits
 * between them at 0.73 on fifteen calls and reads 'partial', which is the case
 * that pins this bound independently of the sample floor below.
 *
 * MIN_CONFIDENT_KNOWN_CALLS is the half of the rule that does the quiet work.
 * 17-BRPD CIU sits at ratio 1.000 on a sample of TWO calls; without a floor it
 * would outrank 24-PPD DISP's 63. "Every call we heard was encrypted" is not
 * "this talkgroup is encrypted" when we heard two, so a thin sample stays at
 * 'partial' — the state that tells the operator to expect some silence, which
 * is exactly what two ADP calls justify.
 *
 * WHY `algid IS NULL` IS EXCLUDED FROM BOTH SIDES
 * ------------------------------------------------
 * 9,183 of 11,886 calls (77%) carry no ESS at all, because ESS capture is an
 * opt-in that multiplies log volume tenfold. callEncryption.ts calls that
 * 'unknown' and refuses to read it as clear; the same refusal here is
 * load-bearing arithmetic rather than a nicety. Counted into the denominator,
 * 17-BRPD DSP1 would read 47/615 = 8% and print as a nearly-clear channel — a
 * confident wrong answer about the single talkgroup this whole feature exists
 * to warn about.
 *
 * WHY `unhandled` ALGIDS ARE EXCLUDED FROM BOTH SIDES TOO
 * --------------------------------------------------------
 * The eight one-off algids (0x08, 0x0E, 0x45, 0x48, 0x82, 0xA8, 0xAB, 0xB8, one
 * call each) are bit errors in the ESS, not eight algorithms — see
 * callEncryption.ts for that measurement. Counting them as encryption would
 * badge a talkgroup off a flipped bit. Verified safe: all seven talkgroups
 * carrying one also carry real ADP, so excluding them relabels nothing today —
 * it only stops the next flipped bit from inventing a warning.
 */

import { ADP_ALGID, CLEAR_ALGID } from './callEncryption'

/**
 * One talkgroup's calls, bucketed by what their ESS said. The buckets are the
 * `Encryption` states of callEncryption.ts, summed — `keyed` and `locked`
 * collapse into `adp` because holding a key is a property of a call's key id,
 * not of a talkgroup, and a talkgroup's future traffic can change key at any
 * time.
 */
export interface TalkgroupEncryptionTally {
  /** Calls whose ESS said ADP (0xAA). Encrypted, key held or not. */
  adp: number
  /** Calls whose ESS said 0x80 — a positive assertion of clear traffic. */
  clear: number
  /** Calls encrypted under an algid this console does not implement. */
  unhandled: number
  /** Calls with no ESS captured. Not evidence of anything. */
  unknown: number
}

/** A talkgroup with no calls at all. Not a default — the honest empty state. */
export const EMPTY_TALKGROUP_TALLY: TalkgroupEncryptionTally = {
  adp: 0, clear: 0, unhandled: 0, unknown: 0,
}

/**
 * The scraped roster label from `talkgroups.enc`, verbatim. 'full' rather than
 * 'encrypted' is the real vocabulary in this database — a previous bug came
 * from assuming otherwise, so the strings are not normalised here.
 */
export type ListedEncryption = 'clear' | 'partial' | 'full' | null

export type TalkgroupEncryptionState =
  /** We heard this talkgroup and every call with an ESS said clear. */
  | 'clear'
  /** Some of its traffic is encrypted. Expect silence on some calls. */
  | 'partial'
  /** Effectively all of its traffic is encrypted, on a sample big enough to say so. */
  | 'encrypted'
  /** No usable evidence either way. */
  | 'unknown'

export type TalkgroupEncryptionBasis =
  /** Derived from calls recorded on this talkgroup. */
  | 'observed'
  /** No call with an ESS; the state is the roster's claim, which is unreliable. */
  | 'listed'
  /** Nothing to go on: never heard, and the roster does not claim encryption. */
  | 'none'

export interface TalkgroupEncryptionVerdict {
  state: TalkgroupEncryptionState
  /** Where `state` came from. The UI prints observation and hearsay differently. */
  basis: TalkgroupEncryptionBasis
  /**
   * `encCalls / knownCalls`, or null when nothing was observed. Kept alongside
   * `state` so the badge can print the degree — "ENC 37%" is a different
   * instruction to an operator than "ENC".
   */
  encRatio: number | null
  /** ADP calls observed. Excludes the one-off bit-error algids. */
  encCalls: number
  /** Calls whose ESS said something: ADP or clear. The ratio's denominator. */
  knownCalls: number
}

/**
 * At or above this share of ESS-bearing calls, a talkgroup reads 'encrypted'.
 * 0.8 keeps 17-SO Court 2 (0.875) and excludes BRPD dispatch (0.37) — see the
 * measured table in this file's header for why those are the two cases that
 * set the bound.
 */
export const ENCRYPTED_RATIO = 0.8

/**
 * ESS-bearing calls needed before 'encrypted' can be claimed at all. Below it
 * a talkgroup stays 'partial' however lopsided its ratio: 17-BRPD CIU is 2 ADP
 * out of 2, and two calls do not describe a channel.
 */
export const MIN_CONFIDENT_KNOWN_CALLS = 10

/**
 * Roll one talkgroup's calls up into a verdict.
 *
 * `listed` is the roster's own claim, used ONLY when nothing was observed, and
 * only when it claims encryption — a listed-clear talkgroup we have never heard
 * comes back 'unknown'/'none' rather than 'clear', because this roster listed
 * 24-PPD DISP clear while it ran 62 ADP calls. Silence about 3,193 rows we have
 * no evidence for beats a green light we cannot stand behind.
 */
export function talkgroupEncryption(
  tally: TalkgroupEncryptionTally,
  listed: ListedEncryption = null,
): TalkgroupEncryptionVerdict {
  const knownCalls = tally.adp + tally.clear
  const encCalls = tally.adp

  if (knownCalls === 0) {
    // Never heard with an ESS. The roster is all there is, and it is hearsay.
    if (listed === 'full' || listed === 'partial') {
      return {
        state: listed === 'full' ? 'encrypted' : 'partial',
        basis: 'listed',
        encRatio: null,
        encCalls: 0,
        knownCalls: 0,
      }
    }
    return { state: 'unknown', basis: 'none', encRatio: null, encCalls: 0, knownCalls: 0 }
  }

  const encRatio = encCalls / knownCalls
  const state: TalkgroupEncryptionState
    = encCalls === 0
      ? 'clear'
      : encRatio >= ENCRYPTED_RATIO && knownCalls >= MIN_CONFIDENT_KNOWN_CALLS
        ? 'encrypted'
        : 'partial'

  return { state, basis: 'observed', encRatio, encCalls, knownCalls }
}

/**
 * Bucket one call's ESS into a tally field name, so a rollup built anywhere —
 * SQL aggregate on the server, a reduce over a fixture in a test — agrees with
 * callEncryption.ts's classification of the same call rather than re-deriving
 * it. `keyed`/`locked` are not distinguished; see TalkgroupEncryptionTally.
 */
export function tallyBucket(algid: number | null | undefined): keyof TalkgroupEncryptionTally {
  if (algid === null || algid === undefined) return 'unknown'
  if (algid === CLEAR_ALGID) return 'clear'
  if (algid === ADP_ALGID) return 'adp'
  return 'unhandled'
}

/** A pencil mark for a talkgroup row: the `.mark` classes of assets/css/bay.css. */
export interface TalkgroupMark {
  /** The modifier class, appended to `mark`. */
  cls: 'mark--locked' | 'mark--note'
  /** What prints on the row. Short: it shares a 12px line with the alpha tag. */
  label: string
  /** The evidence, for the row's title attribute. Never a claim without its count. */
  title: string
}

/**
 * The mark a talkgroup row carries, or null for no mark at all.
 *
 * Three marks from four states, on purpose:
 *
 *   'encrypted'/'partial' observed → `mark--locked`, the same blue pencil a
 *   call strip uses for "encrypted, cannot play". An operator should not have
 *   to learn a second colour for one concept; the DEGREE rides in the label,
 *   where "ENC 37%" says what a second tint could not.
 *
 *   'listed' basis → `mark--note`, the grey used for annotation rather than
 *   for encryption, plus a trailing "?" — because this claim comes from the
 *   roster that called 24-PPD DISP clear, and dressing it in the same blue as
 *   measured evidence would erase that difference.
 *
 *   'clear' and 'unknown' → nothing. A badge on 3,193 never-heard rows and on
 *   every quiet clear channel is the cry-wolf failure this project keeps
 *   removing (see utils/captureStatus.ts's STALL_GRACE_MS); absence of a lock
 *   already reads as "nothing known against this one".
 */
export function talkgroupMark(v: TalkgroupEncryptionVerdict): TalkgroupMark | null {
  if (v.basis === 'listed') {
    return {
      cls: 'mark--note',
      label: 'enc?',
      title: v.state === 'encrypted'
        ? 'Roster lists this talkgroup as fully encrypted. No call with encryption headers has ever been recorded on it, so this is the roster\'s claim, not an observation.'
        : 'Roster lists this talkgroup as partially encrypted. No call with encryption headers has ever been recorded on it, so this is the roster\'s claim, not an observation.',
    }
  }
  if (v.state === 'encrypted') {
    return {
      cls: 'mark--locked',
      label: 'enc',
      title: `${v.encCalls} of ${v.knownCalls} recorded calls with encryption headers were ADP. Expect silence.`,
    }
  }
  if (v.state === 'partial') {
    return {
      cls: 'mark--locked',
      label: `enc ${encPercentLabel(v)}`,
      title: `${v.encCalls} of ${v.knownCalls} recorded calls with encryption headers were ADP. Some traffic plays, some is silent.`,
    }
  }
  return null
}

/**
 * The ratio as a percentage for display, floored at 1% so a talkgroup with
 * real encrypted traffic never prints "ENC 0%" — 17-SO DISP S is 6 ADP calls
 * in 218, which rounds to 3%, but 1 in 180 (SP A - DISP1) rounds to 0 and the
 * whole point of the mark is that those six seconds of silence are coming.
 */
export function encPercentLabel(v: TalkgroupEncryptionVerdict): string {
  if (v.encRatio === null || v.encCalls === 0) return ''
  return `${Math.max(1, Math.round(v.encRatio * 100))}%`
}
