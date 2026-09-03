import { describe, it, expect } from 'vitest'
import { receiverStatus, STALL_GRACE_MS } from './captureStatus'

/** Session opened at t=0; `nowMs` is expressed as an offset from that. */
function statusAt(offsetMs: number, overrides: Partial<{ radioBusy: boolean, tracked: boolean }> = {}) {
  return receiverStatus({
    radioBusy: overrides.radioBusy ?? false,
    tracked: overrides.tracked ?? true,
    sessionStartedAt: 0,
    nowMs: offsetMs,
  })
}

describe('receiverStatus', () => {
  it('reads on-air with a console session when both signals are up', () => {
    expect(receiverStatus({
      radioBusy: true,
      tracked: true,
      sessionStartedAt: 0,
      nowMs: 10 * 60_000, // long past the grace window — must not matter here
    })).toBe('onAirConsole')
  })

  it('reads on-air-outside for a shell-started capture regardless of session age', () => {
    expect(receiverStatus({
      radioBusy: true,
      tracked: false,
      sessionStartedAt: null,
      nowMs: 0,
    })).toBe('onAirOutside')
  })

  it('reads idle when nothing is tracked and no radio is busy', () => {
    expect(receiverStatus({
      radioBusy: false,
      tracked: false,
      sessionStartedAt: null,
      nowMs: 0,
    })).toBe('idle')
  })

  it('does not trip on a healthy start: tracked with no radio yet, well inside grace', () => {
    // Measured op25-up time on a healthy cutover was +3s; this checks a point
    // an order of magnitude past that and still inside the 45s grace window.
    expect(statusAt(30_000)).toBe('idle')
  })

  it('grace boundary: one ms inside the window still reads idle', () => {
    expect(statusAt(STALL_GRACE_MS - 1)).toBe('idle')
  })

  it('grace boundary: exactly at the window still reads idle (boundary is exclusive on the stall side)', () => {
    expect(statusAt(STALL_GRACE_MS)).toBe('idle')
  })

  it('grace boundary: one ms past the window reads stalled', () => {
    expect(statusAt(STALL_GRACE_MS + 1)).toBe('stalled')
  })

  it('reads stalled long after the grace window with a session still open and no radio', () => {
    // The 82-minute and 5+-hour incidents that motivated this indicator.
    expect(statusAt(82 * 60_000)).toBe('stalled')
    expect(statusAt(5 * 60 * 60_000)).toBe('stalled')
  })

  it('radioBusy coming back up during the stall window clears it, even past grace', () => {
    expect(statusAt(60 * 60_000, { radioBusy: true })).toBe('onAirConsole')
  })

  it('treats a missing sessionStartedAt on a tracked session as stalled rather than guessing idle', () => {
    expect(receiverStatus({
      radioBusy: false,
      tracked: true,
      sessionStartedAt: null,
      nowMs: 0,
    })).toBe('stalled')
  })
})
