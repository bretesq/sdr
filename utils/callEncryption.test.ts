import { describe, it, expect } from 'vitest'
import {
  ADP_ALGID, CLEAR_ALGID, encryptionState, stripStock,
  type CallEncryptionFields,
} from './callEncryption'

const HELD = new Set([1, 8, 12040])

function call(over: Partial<CallEncryptionFields> = {}): CallEncryptionFields {
  return { algid: null, keyid: null, ...over }
}

describe('encryptionState', () => {
  it('reads algid 0x80 as clear', () => {
    expect(encryptionState(call({ algid: CLEAR_ALGID, keyid: 0 }), HELD)).toBe('clear')
  })

  it('reads ADP under a key we hold as keyed', () => {
    expect(encryptionState(call({ algid: ADP_ALGID, keyid: 8 }), HELD)).toBe('keyed')
  })

  it('reads ADP under a key we do not hold as locked', () => {
    // keyid 0x1320 — observed on air, not in lwin_keys.json.
    expect(encryptionState(call({ algid: ADP_ALGID, keyid: 4896 }), HELD)).toBe('locked')
  })

  it('reads ADP with no key id at all as locked, not as key 0', () => {
    // The `?? -1` sentinel. If it were `?? 0` and 0 were ever held, a call
    // whose ESS carried no key id would decode-by-accident into 'keyed'.
    expect(encryptionState(call({ algid: ADP_ALGID, keyid: null }), new Set([0, ...HELD]))).toBe('locked')
  })

  /**
   * THE BUG. Each of these appears exactly once in a corpus of 11,743 calls,
   * and every one of them rendered as CLEAR before this module existed —
   * Whisper's transcription of undecodable audio printed as if it were speech.
   * Revert stripStock/encryptionState to `algid === ADP_ALGID` and this dies.
   */
  it.each([8, 14, 69, 72, 130, 168, 171, 184])(
    'reads algid %i as encrypted under an unhandled algorithm, never as clear',
    (algid) => {
      const enc = encryptionState(call({ algid, keyid: null }), HELD)
      expect(enc).toBe('unhandled')
      expect(enc).not.toBe('clear')
    },
  )

  it('does not let a held key id unlock an unhandled algorithm', () => {
    // There is no key to hold for an algorithm we cannot run: holding 0x8 says
    // nothing about a call whose algid is not ADP.
    expect(encryptionState(call({ algid: 8, keyid: 8 }), HELD)).toBe('unhandled')
  })

  it('reads a missing algid as unknown, not as clear', () => {
    // 9,078 of 11,743 calls. No ESS was captured; that is the absence of
    // evidence, and the old code called it clear.
    const enc = encryptionState(call({ algid: null, keyid: null }), HELD)
    expect(enc).toBe('unknown')
    expect(enc).not.toBe('clear')
  })

})

describe('stripStock', () => {
  it('prints a transcribed clear call on clear stock', () => {
    expect(stripStock('clear', true)).toBe('clear')
  })

  it('prints a clear call with nothing to show on void stock', () => {
    expect(stripStock('clear', false)).toBe('void')
  })

  it('prints an unhandled algorithm on locked stock, never clear or void', () => {
    // Both bodies: an unhandled call with a Whisper transcript is exactly the
    // case that used to print as clear with noise on its face.
    expect(stripStock('unhandled', true)).toBe('locked')
    expect(stripStock('unhandled', false)).toBe('locked')
  })

  it('prints ADP with no key held on locked stock', () => {
    expect(stripStock('locked', true)).toBe('locked')
    expect(stripStock('locked', false)).toBe('locked')
  })

  it('keeps keyed stock when the transcript has not arrived yet', () => {
    // Decided before the body check on purpose: an ADP call we CAN decode
    // keeps its green key label through the not-transcribed-yet window rather
    // than dropping to void stock and losing it.
    expect(stripStock('keyed', true)).toBe('keyed')
    expect(stripStock('keyed', false)).toBe('keyed')
  })

  it('leaves an unknown-encryption call on exactly the stock it always had', () => {
    // 77% of the corpus. Rendering unchanged is the point: a warning badge on
    // three calls in four teaches the operator to ignore all of them.
    expect(stripStock('unknown', true)).toBe('clear')
    expect(stripStock('unknown', false)).toBe('void')
  })
})
