import { describe, it, expect } from 'vitest'
import { parseRecordingFilename, loadJSON } from './files'

describe('parseRecordingFilename', () => {
  it('extracts tgid and a LOCAL-time timestamp', () => {
    const r = parseRecordingFilename('TG17165_17-BRPD-DSP1_20260830-170008.wav')
    expect(r.tgid).toBe(17165)
    // udp_audio_record.py stamps local wall-clock, and calls.json's `start` is
    // Python's local .timestamp(). Parsing as UTC is off by the UTC offset
    // (5 h here) for every recording not present in calls.json.
    expect(r.start).toBe(new Date(2026, 7, 30, 17, 0, 8).getTime() / 1000)
  })

  it('handles the duplicate-suffix form', () => {
    const r = parseRecordingFilename('TG5000_SP-A-DISP1_20260830-170051_2.wav')
    expect(r.tgid).toBe(5000)
  })

  it('returns nulls for an unparseable name', () => {
    const r = parseRecordingFilename('notarecording.wav')
    expect(r.tgid).toBeNull()
    expect(r.start).toBe(0)
  })
})

describe('loadJSON', () => {
  it('returns the fallback when the file is missing', () => {
    expect(loadJSON('/nonexistent/nope.json', { a: 1 })).toEqual({ a: 1 })
  })
})

import { mergeCalls } from './files'
import type { Recording } from './files'

const base: Recording = {
  file: 'TG17165_x_20260830-170008.wav',
  tgid: 17165, alpha: null, desc: null, cat: null, enc: null,
  start: 100, dur: 0, transcript: null,
}

describe('mergeCalls', () => {
  it('merges duration and transcript from an array-shaped calls.json', () => {
    const merged = mergeCalls([base], [
      { file: 'TG17165_x_20260830-170008.wav', dur: 12.1, transcript: 'hello' },
    ])
    expect(merged[0].dur).toBe(12.1)
    expect(merged[0].transcript).toBe('hello')
  })

  it('merges from an object-shaped calls.json', () => {
    const merged = mergeCalls([base], {
      'TG17165_x_20260830-170008.wav': { dur: 5, transcript: 'yo' },
    })
    expect(merged[0].dur).toBe(5)
  })

  it('never lets calls.json blank out a scanned field', () => {
    const merged = mergeCalls([{ ...base, alpha: 'FROM-DB' }], [
      { file: 'TG17165_x_20260830-170008.wav', alpha: null, dur: 3 },
    ])
    expect(merged[0].alpha).toBe('FROM-DB')
    expect(merged[0].dur).toBe(3)
  })

  it('leaves recordings absent from calls.json untouched', () => {
    const merged = mergeCalls([base], [])
    expect(merged[0].dur).toBe(0)
  })
})
