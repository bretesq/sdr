import { describe, it, expect } from 'vitest'
import { safeRecordingPath } from './paths'

describe('safeRecordingPath', () => {
  it('accepts a well-formed recording name', () => {
    const p = safeRecordingPath('TG17165_17-BRPD-DSP1_20260830-170008.wav')
    expect(p).toContain('/recordings/TG17165_17-BRPD-DSP1_20260830-170008.wav')
  })

  it('accepts the matching transcript name', () => {
    const p = safeRecordingPath('TG17165_17-BRPD-DSP1_20260830-170008.txt')
    expect(p).toContain('.txt')
  })

  it('rejects path traversal', () => {
    expect(safeRecordingPath('../../etc/passwd')).toBeNull()
    expect(safeRecordingPath('TG1_x_20260830-170008.wav/../../../etc/passwd')).toBeNull()
  })

  it('rejects names that do not match the recording pattern', () => {
    expect(safeRecordingPath('calls.json')).toBeNull()
    expect(safeRecordingPath('server.py')).toBeNull()
  })
})
