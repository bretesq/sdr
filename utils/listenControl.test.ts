import { describe, it, expect } from 'vitest'
import {
  isValidCaptureDuration, buildCaptureStartBody, apiError,
  MIN_CAPTURE_DURATION_SEC, MAX_CAPTURE_DURATION_SEC, DEFAULT_CAPTURE_DURATION_SEC,
  CAPTURE_PRESETS, CAPTURE_PRESET_LABELS, CAPTURE_PRESET_TAGS,
  DEFAULT_CAPTURE_PRESET, isCapturePreset,
} from './listenControl'

describe('isValidCaptureDuration', () => {
  it('accepts the default (24h) — must always be valid, it is what a fresh page offers', () => {
    expect(isValidCaptureDuration(DEFAULT_CAPTURE_DURATION_SEC)).toBe(true)
  })

  it('rejects null (a defensive default, not something any code path here assigns)', () => {
    expect(isValidCaptureDuration(null)).toBe(false)
  })

  it('rejects an empty string — what v-model.number actually puts in the ref when the field is cleared', () => {
    expect(isValidCaptureDuration('')).toBe(false)
  })

  it('rejects a non-numeric string outright, regardless of what it contains', () => {
    expect(isValidCaptureDuration('86400')).toBe(false)
    expect(isValidCaptureDuration('abc')).toBe(false)
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

  it('mode is fixed regardless of the operator\'s inputs — it is the only delegatable one', () => {
    const body = buildCaptureStartBody({ duration: 60, ess: false, includeEncrypted: true })
    expect(body.mode).toBe('multi')
  })

  it('carries the operator\'s chosen preset — this is the field that was pinned to "pd"', () => {
    // The bug this replaced: the console could only ever run `pd`, ~44
    // talkgroups, which is exactly what the bay's Standby list could show.
    for (const preset of CAPTURE_PRESETS) {
      expect(buildCaptureStartBody({
        duration: 3600, ess: false, includeEncrypted: false, preset,
      })).toEqual({
        mode: 'multi', preset, duration: 3600, ess: false, includeEncrypted: false,
      })
    }
  })

  it('defaults to pd when no preset is given, so an untouched caller is unchanged', () => {
    expect(buildCaptureStartBody({ duration: 60, ess: false, includeEncrypted: true }).preset)
      .toBe(DEFAULT_CAPTURE_PRESET)
    expect(DEFAULT_CAPTURE_PRESET).toBe('pd')
  })
})

describe('CAPTURE_PRESETS', () => {
  it('is the nine presets make_whitelist.py and capture_control.py both know', () => {
    // Spelled out rather than derived from the constant: a test that reads
    // its expectation off the thing under test cannot notice a preset being
    // dropped. The far-side copies are cross-checked independently —
    // scripts/tests/test_capture_control.py parses make_whitelist.py's own
    // PRESETS dict, and server/utils/processes.test.ts checks this list
    // against the delegation gate.
    expect([...CAPTURE_PRESETS]).toEqual([
      'pd', 'pd-all', 'fire', 'fire-all', 'ems', 'interop', 'schools', 'publicworks', 'all',
    ])
  })

  it('labels every preset it offers, and offers every preset it labels', () => {
    // Both records, because both are rendered: the human label is the option
    // text and the tag list is the "Follows:" line under the picker. A preset
    // missing from either would render as a blank, and an entry in either with
    // no matching preset would be unreachable.
    expect(Object.keys(CAPTURE_PRESET_LABELS).sort()).toEqual([...CAPTURE_PRESETS].sort())
    expect(Object.keys(CAPTURE_PRESET_TAGS).sort()).toEqual([...CAPTURE_PRESETS].sort())
  })

  it('gives every preset a non-empty label and tag list', () => {
    // server/api/config/presets.get.ts maps straight over these, so a blank
    // would ship as a blank option in any client built against that endpoint
    // too, not just the bay's picker.
    for (const preset of CAPTURE_PRESETS) {
      expect(CAPTURE_PRESET_LABELS[preset].length).toBeGreaterThan(0)
      expect(CAPTURE_PRESET_TAGS[preset].length).toBeGreaterThan(0)
    }
  })

  it('accepts exactly the nine and nothing that merely looks like one', () => {
    for (const preset of CAPTURE_PRESETS) expect(isCapturePreset(preset)).toBe(true)
    for (const bad of ['PD', 'pd ', ' pd', 'police', '', 'pd,fire', '--pd']) {
      expect(isCapturePreset(bad)).toBe(false)
    }
  })

  it('rejects non-strings without throwing — it guards a JSON-shaped boundary', () => {
    for (const bad of [undefined, null, 7, true, ['pd'], { preset: 'pd' }]) {
      expect(isCapturePreset(bad)).toBe(false)
    }
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
