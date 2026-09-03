import { describe, it, expect } from 'vitest'
import {
  receiverStatus, STALL_GRACE_MS, captureExpiry, canStartCapture, canStopCapture,
} from './captureStatus'

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

describe('captureExpiry', () => {
  it('computes the epoch-ms expiry and remaining time for a session mid-run', () => {
    // Opened at t=0 with a 3h (10800s) duration — the exact shape of
    // launch_184155/launch_230425, the two runs mistaken for crashes.
    expect(captureExpiry({
      sessionStartedAt: 0,
      sessionDurationSec: 10_800,
      nowMs: 60 * 60_000, // 1h in — 2h left
    })).toEqual({ expiresAtMs: 10_800_000, remainingMs: 2 * 60 * 60_000 })
  })

  it('boundary: exactly at expiry, remainingMs is zero, not null or negative-and-missed', () => {
    expect(captureExpiry({
      sessionStartedAt: 0,
      sessionDurationSec: 10_800,
      nowMs: 10_800_000,
    })).toEqual({ expiresAtMs: 10_800_000, remainingMs: 0 })
  })

  it('boundary: a capture past its end reports a negative remainingMs rather than clamping to zero', () => {
    // A silent, no-crash stop looks exactly like this from the bay's side —
    // the case this function exists to make visible before it happens, and
    // to still describe honestly if read a moment after.
    expect(captureExpiry({
      sessionStartedAt: 0,
      sessionDurationSec: 10_800,
      nowMs: 10_800_000 + 5_000,
    })).toEqual({ expiresAtMs: 10_800_000, remainingMs: -5_000 })
  })

  it('boundary: no duration recorded reports no expiry at all, not an expiry at session start', () => {
    // An unbounded run (no --pd) or a session whose config predates this
    // field. Must read exactly like "nothing to say" — not epoch-0.
    expect(captureExpiry({
      sessionStartedAt: 0,
      sessionDurationSec: null,
      nowMs: 60 * 60_000,
    })).toEqual({ expiresAtMs: null, remainingMs: null })
  })

  it('boundary: untracked (no sessionStartedAt) reports no expiry regardless of a stray duration', () => {
    expect(captureExpiry({
      sessionStartedAt: null,
      sessionDurationSec: 10_800,
      nowMs: 0,
    })).toEqual({ expiresAtMs: null, remainingMs: null })
  })
})

describe('canStopCapture / canStartCapture', () => {
  // One row per real state, including the fifth one `ReceiverStatus` itself
  // collapses into 'idle' (tracked, no radio yet, still inside grace) — the
  // whole reason these are derived from the raw signals and not looked up
  // by ReceiverStatus. See canStartCapture/canStopCapture's own docstring.
  it('onAirConsole (tracked, radioBusy): Stop only', () => {
    expect(canStopCapture({ tracked: true })).toBe(true)
    expect(canStartCapture({ tracked: true, radioBusy: true })).toBe(false)
  })

  it('onAirOutside (radioBusy, not tracked): neither — not ours to stop, and Start would only contend for the radio', () => {
    expect(canStopCapture({ tracked: false })).toBe(false)
    expect(canStartCapture({ tracked: false, radioBusy: true })).toBe(false)
  })

  it('stalled (tracked, no radio, past grace): Stop is the way out, Start still refuses', () => {
    expect(canStopCapture({ tracked: true })).toBe(true)
    expect(canStartCapture({ tracked: true, radioBusy: false })).toBe(false)
  })

  it('idle (untracked, no radio): Start only', () => {
    expect(canStopCapture({ tracked: false })).toBe(false)
    expect(canStartCapture({ tracked: false, radioBusy: false })).toBe(true)
  })

  it('the fifth state — tracked, no radio yet, still inside STALL_GRACE_MS — reads exactly like stalled for affordances: Stop reachable, Start refused', () => {
    // receiverStatus() itself reads this combination as 'idle' (see the
    // "does not trip on a healthy start" case above) precisely so the
    // Receiver line doesn't cry wolf during a routine startup — but a
    // lookup keyed on that display state would wrongly re-offer Start here
    // and withhold Stop for up to STALL_GRACE_MS. These two must not make
    // that mistake: they never look at session age at all.
    expect(receiverStatus({
      radioBusy: false, tracked: true, sessionStartedAt: 0, nowMs: 1000,
    })).toBe('idle') // sanity: this really is the display-idle case
    expect(canStopCapture({ tracked: true })).toBe(true)
    expect(canStartCapture({ tracked: true, radioBusy: false })).toBe(false)
  })
})
