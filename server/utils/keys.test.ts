import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { heldKeyIds, keysPath } from './keys'

/**
 * lwin_keys.json holds live ADP key BYTES for keys recovered by brute force.
 * The browser needs to know only whether a keyid is held, so it can tell a
 * call that will decode to speech from one that will decode to noise.
 * Nothing else in that file may leave the server.
 */
describe('held key ids', () => {
  it('returns the recovered key ids as numbers', () => {
    // 0x1, 0x8, 0x2F08 as of 2026-09-01.
    expect(heldKeyIds()).toEqual([1, 8, 12040])
  })

  it('returns numbers only, never key material', () => {
    const ids = heldKeyIds()
    expect(Array.isArray(ids)).toBe(true)
    for (const id of ids) expect(typeof id).toBe('number')

    // Belt and braces: assert no byte of any key appears anywhere in the
    // serialised result, so a future refactor cannot widen the return shape
    // into a leak without failing here.
    const serialised = JSON.stringify(ids)
    const raw = JSON.parse(readFileSync(keysPath(), 'utf-8')) as
      Record<string, { key: string[] }>
    for (const entry of Object.values(raw)) {
      for (const byte of entry.key) {
        expect(serialised).not.toContain(byte)
      }
    }
  })

  it('is sorted, so the output is stable across runs', () => {
    const ids = heldKeyIds()
    expect([...ids].sort((a, b) => a - b)).toEqual(ids)
  })
})
