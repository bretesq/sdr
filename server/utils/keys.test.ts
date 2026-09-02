import { describe, it, expect, vi } from 'vitest'
import { readFileSync, writeFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { heldKeyIds, keysPath } from './keys'

/**
 * lwin_keys.json holds live ADP key BYTES for keys recovered by brute force.
 * The browser needs to know only whether a keyid is held, so it can tell a
 * call that will decode to speech from one that will decode to noise.
 * Nothing else in that file may leave the server.
 */
const FIXTURE = join(__dirname, '__fixtures__', 'keys.sample.json')

describe('parsing, against a versioned fixture', () => {
  /**
   * Exact-value assertions run against a CHECKED-IN fixture, never against the
   * live keyfile.
   *
   * Asserting exact ids against live, unversioned, operational data means that
   * whenever reality drifts from the literal, editing the data is a one-line
   * change that leaves no diff — the cheapest of the three ways to make a red
   * test green, and the only one that damages something irreplaceable. During
   * this task's first implementation an agent did exactly that to the live
   * keyfile. The fixture removes the incentive rather than forbidding the act:
   * a mismatch here is now a git diff.
   */
  it('parses hex key ids in every spelling the keyfile uses', () => {
    expect(heldKeyIds(FIXTURE)).toEqual([1, 11, 12040, 65535])
  })

  it('drops an unparseable id, keeps the rest, and says so', () => {
    // The quiet failure: one typo'd id among many valid ones returns
    // successfully with every other key intact, so the operator sees a single
    // talkgroup that will not decode and nothing points at the keyfile.
    const tmp = join(tmpdir(), `keys-malformed-${process.pid}.json`)
    writeFileSync(tmp, JSON.stringify({ '0x1': {}, '0xG1': {}, '0x8': {} }))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      expect(heldKeyIds(tmp)).toEqual([1, 8])
      expect(warn).toHaveBeenCalledOnce()
      expect(warn.mock.calls[0][0]).toContain('0xG1')
    } finally {
      warn.mockRestore()
      rmSync(tmp, { force: true })
    }
  })

  it('deduplicates ids spelled two ways', () => {
    const tmp = join(tmpdir(), `keys-dupe-${process.pid}.json`)
    writeFileSync(tmp, JSON.stringify({ '0x8': {}, '8': {} }))
    try {
      expect(heldKeyIds(tmp)).toEqual([8])
    } finally {
      rmSync(tmp, { force: true })
    }
  })
})

describe('held key ids', () => {
  it('reads the live keyfile without throwing', () => {
    // No literal: the live file's contents are operational state, not a fact
    // this suite gets to pin. Shape only.
    const ids = heldKeyIds()
    expect(Array.isArray(ids)).toBe(true)
    expect(ids.length).toBeGreaterThan(0)
  })

  it('returns numbers only, never key material', () => {
    const ids = heldKeyIds()
    expect(Array.isArray(ids)).toBe(true)
    for (const id of ids) expect(typeof id).toBe('number')

    const raw = JSON.parse(readFileSync(keysPath(), 'utf-8')) as
      Record<string, { key: string[] }>
    const allBytes = Object.values(raw).flatMap(e => e.key ?? [])

    // Preconditions, so this test cannot pass by seeing nothing.
    //
    // The assertion below is a NEGATIVE — "no key byte appears in the output"
    // — and a negative over an empty set is vacuously true. If the keyfile
    // failed to parse, held no entries, or held entries with empty byte
    // arrays, the loop would run zero times and this would report success
    // having verified nothing. For a test whose whole purpose is catching a
    // leak, passing blind is the worst available outcome, so prove the test
    // can see key bytes before trusting its silence about them.
    expect(Object.keys(raw).length).toBeGreaterThan(0)
    expect(allBytes.length).toBeGreaterThan(0)

    // Belt and braces: no byte of any key may appear anywhere in the
    // serialised result, so a future refactor cannot widen the return shape
    // into a leak without failing here.
    const serialised = JSON.stringify(ids)
    for (const byte of allBytes) {
      expect(serialised).not.toContain(byte)
    }
  })

  it('is sorted, so the output is stable across runs', () => {
    const ids = heldKeyIds()
    expect([...ids].sort((a, b) => a - b)).toEqual(ids)
  })
})
