import type { H3Event } from 'h3'

/**
 * Reject cross-site requests to state-changing endpoints.
 *
 * WHY THIS EXISTS
 * ---------------
 * This console has no authentication by design — it is a LAN tool. That makes
 * "an attacker who can reach port 3000 can drive it" an accepted risk. It does
 * NOT make "any web page the operator happens to visit can drive it" acceptable,
 * and without this guard that is exactly the situation:
 *
 *   <form action="http://10.56.1.77:3000/api/listen/start" method="POST">
 *     <input name="preset" value="pd">
 *   </form>
 *   <script>document.forms[0].submit()</script>
 *
 * `application/x-www-form-urlencoded` is CORS-safelisted, so a plain HTML form
 * needs no preflight, and h3's readBody happily parses it into a real object
 * that passes every validation gate. Omitting `duration` is the nasty part: the
 * string "600" would fail Number.isInteger, so the attacker simply leaves it
 * out, buildListenArgs emits no positional argument, lwin_listen.sh falls to
 * SECS=0 / RUN=99999, and the HackRF records indefinitely.
 *
 * /api/listen/stop is worse still — it reads no body at all, so
 * `fetch(url, {method:'POST', mode:'no-cors'})` from any page kills a live
 * session. Stop-then-start hands an attacker control of what the radio captures.
 *
 * Vite's allowedHosts does not help: it returns true unconditionally for any
 * IPv4 literal, and the attacker's page addresses the box by IP.
 *
 * TWO INDEPENDENT CHECKS
 * ----------------------
 * 1. Require a JSON content-type. JSON is not CORS-safelisted, so a browser
 *    must preflight it; the preflight fails because we send no CORS headers.
 *    This is the primary defence and works regardless of Sec-Fetch-Site support.
 * 2. Reject a present-and-cross-site `Sec-Fetch-Site`. Defence in depth, and it
 *    catches the bodyless POST that check 1 cannot see. Absent header is allowed
 *    so curl and other non-browser clients still work.
 */
export function assertSameOrigin(event: H3Event): { ok: true } | { ok: false, error: string } {
  const site = getHeader(event, 'sec-fetch-site')
  if (site && site !== 'same-origin' && site !== 'none') {
    return { ok: false, error: 'Cross-site requests are not allowed' }
  }
  return { ok: true }
}

/**
 * As above, plus a JSON content-type requirement. For routes that read a body.
 * Kept separate because /api/listen/stop reads none and must not 415 on an
 * empty request from the UI.
 */
export function assertJsonSameOrigin(event: H3Event): { ok: true } | { ok: false, error: string, status: number } {
  const site = getHeader(event, 'sec-fetch-site')
  if (site && site !== 'same-origin' && site !== 'none') {
    return { ok: false, error: 'Cross-site requests are not allowed', status: 403 }
  }

  const ct = (getHeader(event, 'content-type') ?? '').toLowerCase()
  // No body at all is fine — validation downstream produces a useful 400.
  // A body in a CORS-safelisted type is not: that is the form-POST vector.
  if (ct && !ct.startsWith('application/json')) {
    return {
      ok: false,
      status: 415,
      error: 'Content-Type must be application/json',
    }
  }

  return { ok: true }
}
