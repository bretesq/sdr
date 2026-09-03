import { describe, it, expect } from 'vitest'
import {
  talkgroupEncryption, talkgroupMark, tallyBucket, encPercentLabel,
  EMPTY_TALKGROUP_TALLY, ENCRYPTED_RATIO, MIN_CONFIDENT_KNOWN_CALLS,
  type TalkgroupEncryptionTally,
} from './talkgroupEncryption'
import { ADP_ALGID, CLEAR_ALGID } from './callEncryption'

/**
 * The numbers in these cases are measured off this system's own sdr.db
 * (11,886 calls), not invented — see the table in talkgroupEncryption.ts's
 * header. A fixture built from assumptions would have agreed with the
 * assumptions; the whole reason this rollup exists is that the assumption
 * (talkgroups.enc) disagrees with the air.
 */
function tally(t: Partial<TalkgroupEncryptionTally>): TalkgroupEncryptionTally {
  return { ...EMPTY_TALKGROUP_TALLY, ...t }
}

describe('talkgroupEncryption', () => {
  it('reads a talkgroup with no encrypted calls as clear', () => {
    // 17-SO DISP A: 41 clear, 127 with no ESS, no ADP ever.
    const v = talkgroupEncryption(tally({ clear: 41, unknown: 127 }))
    expect(v.state).toBe('clear')
    expect(v.basis).toBe('observed')
    expect(v.encRatio).toBe(0)
    expect(v.knownCalls).toBe(41)
  })

  it('reads 24-PPD DISP as encrypted: 62 ADP against 1 clear', () => {
    const v = talkgroupEncryption(tally({ adp: 62, clear: 1, unknown: 139 }))
    expect(v.state).toBe('encrypted')
    expect(v.basis).toBe('observed')
    expect(v.encCalls).toBe(62)
    expect(v.knownCalls).toBe(63)
  })

  it('reads 17-SO Court 2 as encrypted at 0.875 — the case that sets the 0.8 bound', () => {
    // A 0.9 threshold would call this partial. Three clear calls in
    // twenty-four are the exception on this talkgroup, not the rule.
    const v = talkgroupEncryption(tally({ adp: 21, clear: 3, unknown: 53 }))
    expect(v.state).toBe('encrypted')
    expect(v.encRatio).toBeGreaterThan(ENCRYPTED_RATIO)
  })

  it('reads BRPD dispatch as partial, not encrypted', () => {
    // 17-BRPD DSP1: half-readable. Calling it encrypted would tell the
    // operator not to bother with 79 clear calls.
    const v = talkgroupEncryption(tally({ adp: 47, clear: 79, unknown: 489 }))
    expect(v.state).toBe('partial')
    expect(v.encRatio).toBeCloseTo(0.373, 3)
  })

  it('reads EBR SO dispatch as partial off 6 ADP calls in 218', () => {
    // 17-SO DISP S. The console this bay replaced had a checkbox reading
    // "Include partially-encrypted TGs (BRPD / EBR SO)" — this is the EBR SO
    // half of it, and any ADP at all must be enough to earn the warning.
    const v = talkgroupEncryption(tally({ adp: 6, clear: 212, unknown: 618 }))
    expect(v.state).toBe('partial')
    expect(talkgroupMark(v)?.label).toBe('enc 3%')
  })

  it('will not call a talkgroup encrypted off a thin sample, however lopsided', () => {
    // 17-BRPD CIU: 2 ADP, 0 clear — ratio 1.000 on two calls. Without
    // MIN_CONFIDENT_KNOWN_CALLS this outranks 24-PPD DISP's 63-call sample.
    const v = talkgroupEncryption(tally({ adp: 2, clear: 0, unknown: 23 }))
    expect(v.encRatio).toBe(1)
    expect(v.knownCalls).toBeLessThan(MIN_CONFIDENT_KNOWN_CALLS)
    expect(v.state).toBe('partial')
  })

  it('holds a mid ratio at partial even on an ample sample', () => {
    // 17-SO K-9: 11 ADP of 15 with an ESS — 0.733, past the sample floor but
    // under ENCRYPTED_RATIO. This pins the ratio threshold INDEPENDENTLY of
    // the floor; the two cases above vary n at a fixed ratio, this varies the
    // ratio at a sufficient n, so neither bound can be loosened unnoticed.
    const v = talkgroupEncryption(tally({ adp: 11, clear: 4, unknown: 5 }))
    expect(v.knownCalls).toBeGreaterThanOrEqual(MIN_CONFIDENT_KNOWN_CALLS)
    expect(v.encRatio).toBeLessThan(ENCRYPTED_RATIO)
    expect(v.state).toBe('partial')
  })

  it('pins ENCRYPTED_RATIO exactly, not merely to a range', () => {
    // MIN_CONFIDENT_KNOWN_CALLS is pinned to the value (9 -> partial, 10 ->
    // encrypted, below). ENCRYPTED_RATIO was not: the two cases bracketing it
    // were 11/15 = 0.733 and 21/24 = 0.875, so ANY threshold in (0.733, 0.875]
    // passed — 0.75 and 0.85 included. The value is well argued in this
    // module's header (0.8 keeps 17-SO Court 2 at 0.875 and excludes BRPD
    // dispatch at 0.37); this is about the pin, not the value.
    //
    // Both cases sit at or beside 0.8 exactly, on a sample past the floor so
    // the ratio is the only bound in play, and the counts are derived from
    // ENCRYPTED_RATIO rather than written out — so moving the constant moves
    // the boundary these two probe and the equality assertions below fail.
    expect(ENCRYPTED_RATIO).toBe(0.8)
    const n = 20
    const atThreshold = Math.round(ENCRYPTED_RATIO * n)          // 16 of 20
    const justUnder = atThreshold - 1                            // 15 of 20

    const on = talkgroupEncryption(
      tally({ adp: atThreshold, clear: n - atThreshold }))
    expect(on.encRatio).toBe(ENCRYPTED_RATIO)
    expect(on.state).toBe('encrypted')       // the bound is inclusive

    const under = talkgroupEncryption(
      tally({ adp: justUnder, clear: n - justUnder }))
    expect(under.encRatio).toBeLessThan(ENCRYPTED_RATIO)
    expect(under.knownCalls).toBeGreaterThanOrEqual(MIN_CONFIDENT_KNOWN_CALLS)
    expect(under.state).toBe('partial')
  })

  it('promotes the same ratio once the sample reaches the floor', () => {
    const thin = talkgroupEncryption(tally({ adp: 9, clear: 0 }))
    const enough = talkgroupEncryption(tally({ adp: 10, clear: 0 }))
    expect(thin.state).toBe('partial')
    expect(enough.state).toBe('encrypted')
  })

  it('never reads a talkgroup with no calls at all as clear', () => {
    const v = talkgroupEncryption(EMPTY_TALKGROUP_TALLY)
    expect(v.state).toBe('unknown')
    expect(v.basis).toBe('none')
    expect(v.encRatio).toBeNull()
    expect(talkgroupMark(v)).toBeNull()
  })

  it('never reads calls with no ESS as clear', () => {
    // 63 calls recorded, not one with an ESS. This is the absence of evidence,
    // and it must not come back 'clear' — the same refusal callEncryption.ts
    // makes for one call (17-BRPD MOTO1 is exactly this shape).
    const v = talkgroupEncryption(tally({ unknown: 63 }))
    expect(v.state).toBe('unknown')
    expect(v.basis).toBe('none')
  })

  it('excludes no-ESS calls from the ratio rather than diluting it', () => {
    // The bug this guards: counting 489 no-ESS calls into the denominator
    // turns BRPD DSP1's 0.373 into 0.076 and prints a dark channel as clear.
    const v = talkgroupEncryption(tally({ adp: 47, clear: 79, unknown: 489 }))
    expect(v.knownCalls).toBe(126)
    expect(v.encRatio).not.toBeCloseTo(47 / 615, 3)
  })

  it('excludes bit-error algids from both sides of the ratio', () => {
    // 17-SO Court 2 carries one such call. Counted as encryption it would
    // shift the ratio; counted as clear it would too. It is neither.
    const withBitError = talkgroupEncryption(tally({ adp: 21, clear: 3, unhandled: 1 }))
    const without = talkgroupEncryption(tally({ adp: 21, clear: 3 }))
    expect(withBitError).toEqual(without)
  })

  it('will not badge a talkgroup off a bit error alone', () => {
    const v = talkgroupEncryption(tally({ clear: 40, unhandled: 1 }))
    expect(v.state).toBe('clear')
    expect(talkgroupMark(v)).toBeNull()
  })
})

describe('talkgroupEncryption falling back to the roster', () => {
  it('carries a listed-full claim as encrypted, marked as hearsay', () => {
    const v = talkgroupEncryption(EMPTY_TALKGROUP_TALLY, 'full')
    expect(v.state).toBe('encrypted')
    expect(v.basis).toBe('listed')
    expect(v.encRatio).toBeNull()
    expect(talkgroupMark(v)?.cls).toBe('mark--note')
    expect(talkgroupMark(v)?.label).toBe('enc?')
  })

  it('carries a listed-partial claim as partial, marked as hearsay', () => {
    const v = talkgroupEncryption(EMPTY_TALKGROUP_TALLY, 'partial')
    expect(v.state).toBe('partial')
    expect(v.basis).toBe('listed')
    expect(talkgroupMark(v)?.cls).toBe('mark--note')
  })

  it('does not accept a listed-clear claim as evidence of clear traffic', () => {
    // The roster lists 24-PPD DISP clear while it runs 62 ADP calls out of 63.
    // A listed-clear talkgroup we have never heard is unknown, not clear.
    const v = talkgroupEncryption(EMPTY_TALKGROUP_TALLY, 'clear')
    expect(v.state).toBe('unknown')
    expect(v.basis).toBe('none')
  })

  it('lets observation override the roster in both directions', () => {
    // Listed clear, heard encrypted (24-PPD DISP)...
    const heard = talkgroupEncryption(tally({ adp: 62, clear: 1 }), 'clear')
    expect(heard.state).toBe('encrypted')
    expect(heard.basis).toBe('observed')
    // ...and listed full, heard almost entirely clear (17 JAIL SEC1).
    const quiet = talkgroupEncryption(tally({ adp: 2, clear: 110 }), 'full')
    expect(quiet.state).toBe('partial')
    expect(quiet.basis).toBe('observed')
    expect(talkgroupMark(quiet)?.cls).toBe('mark--locked')
  })
})

describe('tallyBucket', () => {
  it('buckets by the same algids callEncryption.ts classifies on', () => {
    expect(tallyBucket(ADP_ALGID)).toBe('adp')
    expect(tallyBucket(CLEAR_ALGID)).toBe('clear')
    expect(tallyBucket(null)).toBe('unknown')
    expect(tallyBucket(undefined)).toBe('unknown')
    // The eight one-off algids measured in callEncryption.ts's header.
    for (const algid of [0x08, 0x0e, 0x45, 0x48, 0x82, 0xa8, 0xab, 0xb8]) {
      expect(tallyBucket(algid)).toBe('unhandled')
    }
  })
})

describe('encPercentLabel', () => {
  it('floors a nonzero ratio at 1% so real encryption never prints as none', () => {
    // SP A - DISP1: 1 ADP in 180. Rounds to 0%, which would read as clear.
    expect(encPercentLabel(talkgroupEncryption(tally({ adp: 1, clear: 179 })))).toBe('1%')
  })

  it('is empty when there is nothing to report', () => {
    expect(encPercentLabel(talkgroupEncryption(tally({ clear: 10 })))).toBe('')
    expect(encPercentLabel(talkgroupEncryption(EMPTY_TALKGROUP_TALLY))).toBe('')
  })
})
