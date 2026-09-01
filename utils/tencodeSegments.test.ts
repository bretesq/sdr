import { describe, it, expect } from 'vitest'
import { segments } from './tencodeSegments'
import type { CodeMention } from './tencodeSegments'

function mention(offStart: number, offEnd: number, canonical = '10-42'): CodeMention {
  return {
    raw: '1042',
    canonical,
    kind: 'ten',
    meaning: 'End of tour, off duty',
    confidence: 'medium',
    offStart,
    offEnd,
  }
}

/** Rejoin the segments — must always reproduce the input exactly. */
function rendered(text: string | null, codes: CodeMention[]): string {
  return segments(text, codes).map(s => s.text).join('')
}

describe('segments', () => {
  it('returns nothing for empty text', () => {
    expect(segments(null, [])).toEqual([])
    expect(segments('', [])).toEqual([])
  })

  it('returns one plain segment when there are no codes', () => {
    expect(segments('nothing here', [])).toEqual([{ text: 'nothing here' }])
  })

  it('splits a code out of the surrounding text', () => {
    // "Zachary, 43 is 10-42." — 10-42 at code points 15..20
    const out = segments('Zachary, 43 is 10-42.', [mention(15, 20)])
    expect(out.map(s => s.text)).toEqual(['Zachary, 43 is ', '10-42', '.'])
    expect(out[1]!.code?.canonical).toBe('10-42')
    expect(out[0]!.code).toBeUndefined()
  })

  it('handles a code at the very start and the very end', () => {
    expect(segments('10-42', [mention(0, 5)]).map(s => s.text)).toEqual(['10-42'])
    expect(segments('go 10-42', [mention(3, 8)]).map(s => s.text)).toEqual(['go ', '10-42'])
  })

  it('handles multiple codes in one transcript', () => {
    // "10-8, 10-42, out" — 10-8 at 0..4, 10-42 at 6..11
    const out = segments('10-8, 10-42, out', [mention(0, 4, '10-8'), mention(6, 11)])
    expect(out.map(s => s.text)).toEqual(['10-8', ', ', '10-42', ', out'])
  })

  /**
   * The regression this file exists for. Python indexes strings by code point;
   * JavaScript's String.slice indexes by UTF-16 code unit. An emoji is two
   * code units but one code point, so slicing the raw string shifts every
   * later offset — silently annotating the wrong words.
   */
  describe('non-BMP characters', () => {
    const text = '😀 call 10-42.'   // 10-42 spans code points 7..12

    it('annotates the correct span when an emoji precedes the code', () => {
      const out = segments(text, [mention(7, 12)])
      const coded = out.find(s => s.code)
      expect(coded?.text).toBe('10-42')
    })

    it('would be wrong under UTF-16 slicing — proving the test bites', () => {
      // Demonstrates the bug this guards against: the naive approach returns
      // a shifted span, not the code.
      expect(text.slice(7, 12)).not.toBe('10-42')
    })

    it('loses no text around the emoji', () => {
      expect(rendered(text, [mention(7, 12)])).toBe(text)
    })

    it('handles an emoji inside the trailing segment', () => {
      const t = 'call 10-42 😀 done'
      expect(rendered(t, [mention(5, 10)])).toBe(t)
      expect(segments(t, [mention(5, 10)]).find(s => s.code)?.text).toBe('10-42')
    })
  })

  describe('malformed mentions are dropped, never rendered wrong', () => {
    it('drops an inverted span rather than moving the cursor backward', () => {
      const out = segments('Zachary, 43 is 10-42.', [mention(20, 15)])
      expect(out.map(s => s.text)).toEqual(['Zachary, 43 is 10-42.'])
    })

    it('drops an empty span', () => {
      expect(segments('abc', [mention(1, 1)]).map(s => s.text)).toEqual(['abc'])
    })

    it('drops a span past the end of the text', () => {
      expect(segments('abc', [mention(1, 99)]).map(s => s.text)).toEqual(['abc'])
    })

    it('drops an out-of-order mention rather than duplicating text', () => {
      const out = segments('10-8, 10-42, out', [mention(6, 11), mention(0, 4, '10-8')])
      expect(rendered('10-8, 10-42, out', [mention(6, 11), mention(0, 4, '10-8')]))
        .toBe('10-8, 10-42, out')
      expect(out.filter(s => s.code)).toHaveLength(1)
    })
  })

  it('never loses or duplicates text, for every valid case', () => {
    const cases: [string, CodeMention[]][] = [
      ['Zachary, 43 is 10-42.', [mention(15, 20)]],
      ['10-8, 10-42, out', [mention(0, 4, '10-8'), mention(6, 11)]],
      ['😀 call 10-42.', [mention(7, 12)]],
      ['no codes at all', []],
      ['10-42', [mention(0, 5)]],
    ]
    for (const [text, codes] of cases) {
      expect(rendered(text, codes)).toBe(text)
    }
  })
})
