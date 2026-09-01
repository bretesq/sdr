/**
 * Split a transcript into plain and code-bearing segments for rendering.
 *
 * Extracted from RecordingsList.vue so it can be tested: it is the consumer
 * half of a cross-runtime offset contract, and the producer half lives in
 * Python. A bug here silently annotates the wrong words rather than failing.
 */

export interface CodeMention {
  raw: string
  canonical: string
  kind: 'ten' | 'signal' | 'response'
  meaning: string | null
  confidence: 'high' | 'medium' | 'low'
  /** Code-point index into `transcriptNorm` — see the note on `segments`. */
  offStart: number
  /** Code-point index into `transcriptNorm`, exclusive. */
  offEnd: number
}

export interface Segment {
  text: string
  code?: CodeMention
}

/**
 * Build the render list from server-supplied offsets.
 *
 * Rendered with v-for rather than v-html: no injection surface, and no
 * re-running the extractor's regex in the browser.
 *
 * OFFSET UNITS — the reason this indexes a code-point array rather than
 * slicing the string directly. The offsets are produced by
 * scripts/tencodes.py, which indexes Python strings by CODE POINT.
 * JavaScript's String.slice indexes by UTF-16 CODE UNIT. The two agree only
 * while every character is in the BMP. One non-BMP character — an emoji
 * whisper decided to emit — anywhere before a code shifts that code and every
 * later one by at least one unit, and the bounds check still passes, so the UI
 * silently underlines the wrong span with the wrong tooltip. Iterating the
 * string yields code points, so `cp` puts both sides in the same index space.
 */
export function segments(text: string | null, codes: CodeMention[]): Segment[] {
  if (!text) return []
  if (codes.length === 0) return [{ text }]

  const cp = Array.from(text)
  const out: Segment[] = []
  let pos = 0

  for (const c of codes) {
    // Skip anything that cannot describe a forward span inside this text:
    // out of order, out of range, empty, or inverted. A malformed mention is
    // dropped rather than allowed to move the cursor backward, which would
    // duplicate text into the output.
    if (c.offStart < pos) continue
    if (c.offEnd <= c.offStart) continue
    if (c.offEnd > cp.length) continue

    if (c.offStart > pos) out.push({ text: cp.slice(pos, c.offStart).join('') })
    out.push({ text: cp.slice(c.offStart, c.offEnd).join(''), code: c })
    pos = c.offEnd
  }

  if (pos < cp.length) out.push({ text: cp.slice(pos).join('') })
  return out
}
