import { describe, it, expect } from 'vitest'
import {
  ADP_ALGID, createQueue, endedAtMs, classify, admit, prune, takeNext,
  type FeedCall,
} from './scannerQueue'

const HELD = new Set([1, 8, 12040])
const SELECTED = new Set([17094, 17095])
const NOW = 1_788_300_000_000        // fixed clock, ms

/** A clear call on a selected talkgroup, ending `agoSec` before NOW. */
function call(over: Partial<FeedCall> = {}): FeedCall {
  const agoSec = over.endedAt === undefined ? 5 : 0
  const endedAt = over.endedAt ?? NOW / 1000 - agoSec
  return {
    id: 1,
    file: 'TG17094_x_20260901-200000.wav',
    tgid: 17094,
    alpha: 'BRPD DISP 1',
    start: endedAt - 4,
    dur: 4,
    endedAt,
    algid: null,
    keyid: null,
    ...over,
  }
}

describe('endedAtMs', () => {
  it('uses endedAt when present', () => {
    expect(endedAtMs(call({ endedAt: 1000, start: 990, dur: 10 }))).toBe(1_000_000)
  })

  it('falls back to start + dur when endedAt is null', () => {
    expect(endedAtMs(call({ endedAt: null, start: 990, dur: 10 }))).toBe(1_000_000)
  })
})

describe('classify', () => {
  it('accepts a clear call on a selected talkgroup', () => {
    expect(classify(call(), SELECTED, HELD)).toBe('playable')
  })

  it('rejects a talkgroup that is not selected', () => {
    expect(classify(call({ tgid: 19999 }), SELECTED, HELD)).toBe('rejected')
  })

  it('accepts ADP under a key we hold', () => {
    expect(classify(call({ algid: ADP_ALGID, keyid: 8 }), SELECTED, HELD)).toBe('playable')
  })

  it('locks ADP under a key we do not hold', () => {
    // keyid 0x1320. Playing it would emit noise and read as a broken feature.
    expect(classify(call({ algid: ADP_ALGID, keyid: 4896 }), SELECTED, HELD)).toBe('locked')
  })

  it('treats algid 128 as clear', () => {
    // 0x80 is P25's "unencrypted" algorithm id, not an encryption algorithm.
    expect(classify(call({ algid: 128, keyid: 0 }), SELECTED, HELD)).toBe('playable')
  })
})

describe('prune', () => {
  /**
   * The regression that fixed the staleness field.
   *
   * The longest measured transmission is 32.4 s against a 30 s bound. Measuring
   * age from `start` makes every call longer than the bound born stale and
   * dropped before it is ever played — silently discarding exactly the long
   * transmissions most worth hearing.
   */
  it('keeps a 32.4s call that ended 5s ago under a 30s bound', () => {
    const q = createQueue()
    const ended = NOW / 1000 - 5
    admit(q, call({ endedAt: ended, start: ended - 32.4, dur: 32.4 }), SELECTED, HELD)
    prune(q, NOW, 30_000)
    expect(q.entries.length).toBe(1)
    expect(q.skipped).toBe(0)
  })

  it('drops a call that ended longer ago than the bound', () => {
    const q = createQueue()
    admit(q, call({ endedAt: NOW / 1000 - 45 }), SELECTED, HELD)
    prune(q, NOW, 30_000)
    expect(q.entries.length).toBe(0)
    expect(q.skipped).toBe(1)
  })

  it('ages locked entries out without counting them as skipped', () => {
    // Skipping noise that was never going to play is not a loss to report.
    const q = createQueue()
    admit(q, call({ algid: ADP_ALGID, keyid: 4896, endedAt: NOW / 1000 - 45 }), SELECTED, HELD)
    prune(q, NOW, 30_000)
    expect(q.entries.length).toBe(0)
    expect(q.skipped).toBe(0)
  })
})

describe('admit', () => {
  it('does not enqueue a rejected call', () => {
    const q = createQueue()
    expect(admit(q, call({ tgid: 19999 }), SELECTED, HELD)).toBe('rejected')
    expect(q.entries.length).toBe(0)
  })

  it('ignores a call it has already queued', () => {
    // The id cursor makes this unlikely, but a retried fetch must not
    // double-play a call.
    const q = createQueue()
    admit(q, call({ id: 7 }), SELECTED, HELD)
    expect(admit(q, call({ id: 7 }), SELECTED, HELD)).toBe('rejected')
    expect(q.entries.length).toBe(1)
  })
})

describe('takeNext', () => {
  it('returns calls in the order they were admitted', () => {
    const q = createQueue()
    admit(q, call({ id: 1 }), SELECTED, HELD)
    admit(q, call({ id: 2, tgid: 17095 }), SELECTED, HELD)
    expect(takeNext(q, NOW, 30_000)?.id).toBe(1)
    expect(takeNext(q, NOW, 30_000)?.id).toBe(2)
    expect(takeNext(q, NOW, 30_000)).toBe(null)
  })

  it('skips over locked entries without removing them', () => {
    const q = createQueue()
    admit(q, call({ id: 1, algid: ADP_ALGID, keyid: 4896 }), SELECTED, HELD)
    admit(q, call({ id: 2 }), SELECTED, HELD)
    expect(takeNext(q, NOW, 30_000)?.id).toBe(2)
    // The locked row stays visible in the panel until it ages out.
    expect(q.entries.map(e => e.call.id)).toEqual([1])
  })

  it('returns null when only locked entries remain', () => {
    const q = createQueue()
    admit(q, call({ id: 1, algid: ADP_ALGID, keyid: 4896 }), SELECTED, HELD)
    expect(takeNext(q, NOW, 30_000)).toBe(null)
  })

  it('prunes before choosing, so a stale head is never played', () => {
    const q = createQueue()
    admit(q, call({ id: 1, endedAt: NOW / 1000 - 45 }), SELECTED, HELD)
    admit(q, call({ id: 2, endedAt: NOW / 1000 - 2 }), SELECTED, HELD)
    expect(takeNext(q, NOW, 30_000)?.id).toBe(2)
    expect(q.skipped).toBe(1)
  })
})
