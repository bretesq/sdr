import { describe, it, expect } from 'vitest'
import { parseNumberParam } from './query-params'

describe('parseNumberParam', () => {
  it('ignores an empty string, the shape a cleared UI filter sends', () => {
    // ?tgid= arrives as '', and Number('') is 0, not NaN — this is the bug
    // this function exists to close.
    expect(parseNumberParam('')).toBeUndefined()
  })

  it('ignores a missing param', () => {
    expect(parseNumberParam(undefined)).toBeUndefined()
  })

  it('ignores a non-numeric value', () => {
    expect(parseNumberParam('abc')).toBeUndefined()
  })

  it('honours a genuine zero as the number zero, not as absent', () => {
    expect(parseNumberParam('0')).toBe(0)
  })

  it('parses an ordinary positive integer', () => {
    expect(parseNumberParam('17330')).toBe(17330)
  })
})
