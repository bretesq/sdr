import { describe, it, expect } from 'vitest'
import {
  isValidCaptureDuration, buildCaptureStartBody, apiError,
  MIN_CAPTURE_DURATION_SEC, MAX_CAPTURE_DURATION_SEC, DEFAULT_CAPTURE_DURATION_SEC,
} from './listenControl'

describe('isValidCaptureDuration', () => {
  it('accepts the default (24h) — must always be valid, it is what a fresh page offers', () => {
    expect(isValidCaptureDuration(DEFAULT_CAPTURE_DURATION_SEC)).toBe(true)
  })

  it('rejects null — an emptied number input, not zero or NaN', () => {
    expect(isValidCaptureDuration(null)).toBe(false)
  })

  it('rejects 0 and negative values (capture_control.py\'s MIN_DURATION_SEC=1)', () => {
    expect(isValidCaptureDuration(0)).toBe(false)
    expect(isValidCaptureDuration(-5)).toBe(false)
  })

  it('boundary: MIN_CAPTURE_DURATION_SEC itself is valid', () => {
    expect(isValidCaptureDuration(MIN_CAPTURE_DURATION_SEC)).toBe(true)
  })

  it('boundary: MAX_CAPTURE_DURATION_SEC itself is valid, one past it is not', () => {
    expect(isValidCaptureDuration(MAX_CAPTURE_DURATION_SEC)).toBe(true)
    expect(isValidCaptureDuration(MAX_CAPTURE_DURATION_SEC + 1)).toBe(false)
  })

  it('rejects a non-integer — capture_control.py\'s isinstance(duration, int) check has no fractional seconds', () => {
    expect(isValidCaptureDuration(10.5)).toBe(false)
  })
})

describe('buildCaptureStartBody', () => {
  it('emits exactly the fields buildControlRequest() (server/utils/processes.ts) will accept, nothing else', () => {
    expect(buildCaptureStartBody({ duration: 3600, ess: true, includeEncrypted: false })).toEqual({
      mode: 'multi',
      preset: 'pd',
      duration: 3600,
      ess: true,
      includeEncrypted: false,
    })
  })

  it('mode and preset are fixed regardless of the operator\'s inputs', () => {
    const body = buildCaptureStartBody({ duration: 60, ess: false, includeEncrypted: true })
    expect(body.mode).toBe('multi')
    expect(body.preset).toBe('pd')
  })
})

describe('apiError', () => {
  it('unwraps a FetchError\'s .data.error, the control API\'s own message', () => {
    const err = { data: { error: 'A listening session is already running' } }
    expect(apiError(err, 'fallback')).toBe('A listening session is already running')
  })

  it('falls back to a real Error\'s own message when there is no .data.error', () => {
    expect(apiError(new Error('network down'), 'fallback')).toBe('network down')
  })

  it('falls back to the supplied fallback for something unrecognizable', () => {
    expect(apiError('a bare string', 'fallback')).toBe('fallback')
    expect(apiError(undefined, 'fallback')).toBe('fallback')
  })

  it('ignores a .data with no string .error field', () => {
    expect(apiError({ data: { error: 42 } }, 'fallback')).toBe('fallback')
    expect(apiError({ data: {} }, 'fallback')).toBe('fallback')
  })
})
