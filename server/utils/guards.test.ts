import { describe, it, expect, vi } from 'vitest'
import { assertSameOrigin, assertJsonSameOrigin } from './guards'

/**
 * guards.ts was the only file in server/utils without tests, which is backwards
 * from the risk: paths/processes/queries all had them, and this is the one
 * standing between a page the operator happens to visit and a radio that starts
 * recording to disk.
 *
 * A security review of this file reported the content-type check as unlanded.
 * It landed in 405867a and is wired to both routes; these tests pin that so the
 * question is settled by execution rather than by reading.
 */

/**
 * `getHeader` is a Nuxt auto-import, so it is a free global inside guards.ts
 * and undefined under plain vitest. Stub it rather than adding an explicit h3
 * import to production code: these tests exist to pin the guards' decision
 * logic, and that logic is the same either way.
 */
type FakeEvent = { headers: Record<string, string> }

vi.stubGlobal('getHeader', (event: FakeEvent, name: string) =>
  event.headers[name.toLowerCase()])

/** Minimal event stand-in: the guards only ever call getHeader. */
function ev(headers: Record<string, string>): never {
  const lower: Record<string, string> = {}
  for (const [k, v] of Object.entries(headers)) lower[k.toLowerCase()] = v
  return { headers: lower } as never
}

describe('assertJsonSameOrigin — /api/listen/start', () => {
  it('allows the UI: same-origin JSON', () => {
    const r = assertJsonSameOrigin(ev({
      'sec-fetch-site': 'same-origin',
      'content-type': 'application/json',
    }))
    expect(r.ok).toBe(true)
  })

  it('allows curl, which sends neither header', () => {
    expect(assertJsonSameOrigin(ev({})).ok).toBe(true)
  })

  it('rejects the cross-origin form POST — the vector that starts a recording', () => {
    // A plain <form> on any page the operator visits. urlencoded is
    // CORS-safelisted, so the browser sends it with no preflight.
    const r = assertJsonSameOrigin(ev({
      'sec-fetch-site': 'cross-site',
      'content-type': 'application/x-www-form-urlencoded',
    }))
    expect(r.ok).toBe(false)
  })

  it('rejects a safelisted content-type even when Sec-Fetch-Site is absent', () => {
    // The load-bearing case: an older browser that sends no Sec-Fetch-Site.
    // Check 2 cannot see this, so check 1 has to hold it alone.
    for (const ct of [
      'application/x-www-form-urlencoded',
      'multipart/form-data; boundary=x',
      'text/plain;charset=UTF-8',
    ]) {
      const r = assertJsonSameOrigin(ev({ 'content-type': ct }))
      expect(r.ok, ct).toBe(false)
      if (!r.ok) expect(r.status).toBe(415)
    }
  })

  it('accepts a charset parameter on the JSON type', () => {
    expect(assertJsonSameOrigin(ev({
      'content-type': 'application/json; charset=utf-8',
    })).ok).toBe(true)
  })

  it('is not case-sensitive about the content-type', () => {
    expect(assertJsonSameOrigin(ev({
      'content-type': 'APPLICATION/JSON',
    })).ok).toBe(true)
  })
})

describe('assertSameOrigin — /api/listen/stop', () => {
  it('allows the UI and curl', () => {
    expect(assertSameOrigin(ev({ 'sec-fetch-site': 'same-origin' })).ok).toBe(true)
    expect(assertSameOrigin(ev({ 'sec-fetch-site': 'none' })).ok).toBe(true)
    expect(assertSameOrigin(ev({})).ok).toBe(true)
  })

  it('rejects the bodyless cross-site POST that would kill a live session', () => {
    // fetch(url, {method:'POST', mode:'no-cors'}) sends no content-type, so
    // only the Sec-Fetch-Site check can catch it.
    expect(assertSameOrigin(ev({ 'sec-fetch-site': 'cross-site' })).ok).toBe(false)
    expect(assertSameOrigin(ev({ 'sec-fetch-site': 'same-site' })).ok).toBe(false)
  })

  /**
   * KNOWN RESIDUAL GAP, pinned deliberately so a future change has to
   * acknowledge it rather than discover it.
   *
   * /stop reads no body, so it has no content-type to check and rests entirely
   * on Sec-Fetch-Site. A browser that sends no Sec-Fetch-Site (pre-Chrome 76,
   * pre-Firefox 90, pre-Safari 16.4) can therefore stop a live recording
   * cross-origin. The header is absent for non-browser clients too, which is
   * why it cannot simply be required — that would break curl and the CLI.
   *
   * Impact is bounded: it stops a recording. It cannot start one (that is
   * /start, which has the content-type check), read anything, or write to disk.
   */
  it('documents that a legacy browser without Sec-Fetch-Site is not caught', () => {
    expect(assertSameOrigin(ev({ origin: 'https://evil.example' })).ok).toBe(true)
  })
})
