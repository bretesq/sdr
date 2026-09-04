import { describe, it, expect } from 'vitest'
import { windowSize, hasMorePages } from './pagination'

describe('windowSize', () => {
  it('grows the window from the top rather than offsetting', () => {
    expect(windowSize(1, 120)).toBe(120)
    expect(windowSize(2, 120)).toBe(240)
    expect(windowSize(3, 40)).toBe(120)
  })

  it('never asks for zero rows', () => {
    // A rail that requested 0 would render empty and look like "no data"
    // rather than like a bug.
    expect(windowSize(0, 120)).toBe(120)
    expect(windowSize(-1, 40)).toBe(40)
  })
})

describe('hasMorePages', () => {
  it('is true while rows remain and the ceiling is not reached', () => {
    expect(hasMorePages(120, 13000, 1, 20)).toBe(true)
  })

  it('is false once everything is loaded', () => {
    expect(hasMorePages(42, 42, 1, 20)).toBe(false)
  })

  it('is false AT the ceiling even with rows remaining', () => {
    // The honest case. Past the cap the next request returns the same rows,
    // so a rail ignoring this would show "reading more" forever against a
    // list that never grows.
    expect(hasMorePages(2400, 13000, 20, 20)).toBe(false)
    expect(hasMorePages(500, 1449, 12, 12)).toBe(false)
  })

  it('does not go true again past the ceiling', () => {
    expect(hasMorePages(2400, 13000, 21, 20)).toBe(false)
  })

  it('handles an empty corpus without claiming more', () => {
    expect(hasMorePages(0, 0, 1, 20)).toBe(false)
  })
})
