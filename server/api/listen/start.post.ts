import { startListening } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'
import type { ListenOptions } from '~/server/utils/processes'

// Verified against make_whitelist.py's PRESETS dict.
const PRESETS = new Set([
  'pd', 'pd-all', 'fire', 'fire-all', 'ems',
  'interop', 'schools', 'publicworks', 'all',
])
const TG_LIST = /^\d+(,\d+)*$/

/**
 * Is this a regex `scripts/make_whitelist.py` will accept?
 *
 * --match is compiled by Python's `re`, not JavaScript's engine, so a plain
 * `new RegExp(p)` rejects patterns that are perfectly valid downstream — a
 * Python named group `(?P<x>…)` being the common one. Normalize the
 * Python-only spellings to their JS equivalents before testing, so the check
 * still catches genuine typos (unbalanced parens, a dangling quantifier)
 * without 400-ing a pattern Python would have run happily.
 */
function looksLikeValidPythonRegex(pattern: string): boolean {
  const jsEquivalent = pattern
    .replace(/\(\?P</g, '(?<')       // (?P<name>…)  -> (?<name>…)
    .replace(/\(\?P=(\w+)\)/g, '\\k<$1>') // (?P=name)    -> \k<name>
    .replace(/\(\?#[^)]*\)/g, '')    // (?#comment)  -> (JS has no equivalent)
  try {
    new RegExp(jsEquivalent)
    return true
  } catch {
    return false
  }
}

/**
 * Reject patterns whose backtracking cost can blow up.
 *
 * `--match` is compiled by `make_whitelist.py:56` and `.search()`ed against
 * every one of 4,163 talkgroups. Python's `re` is a backtracking engine with no
 * time limit, so a nested quantifier is a denial of service: measured against a
 * real 97-character category string, `((.*)*)*!` ran over 25 seconds before
 * being killed, while `(a|a)+$` finished in 0.1 ms. The subprocess is spawned
 * from inside `lwin_listen.sh`, so nothing on the Node side can time it out —
 * it has to be refused up front.
 *
 * The signature is a quantifier applied to a group that itself contains one:
 * (...*)*, (...+)+, and so on. Legitimate selections here are simple substrings
 * and alternations ("BRPD", "DISP[0-9]", "Fire|EMS"), none of which nest.
 */
const NESTED_QUANTIFIER = /\([^)]*[*+][^)]*\)\s*[*+{]/

function isCheapRegex(pattern: string): boolean {
  if (pattern.length > 200) return false
  return !NESTED_QUANTIFIER.test(pattern)
}

export default defineEventHandler(async (event) => {
  // Must come first: this endpoint powers a radio and writes to disk, and
  // without it any page the operator visits can start an unbounded recording.
  const guard = assertJsonSameOrigin(event)
  if (!guard.ok) {
    setResponseStatus(event, guard.status)
    return { success: false, error: guard.error }
  }

  // readBody returns undefined for a POST with no body. Without this default,
  // the first `body.preset` below throws a TypeError outside the try/catch and
  // the caller gets an unhandled 500 instead of the 400 written for this case.
  const body = (await readBody<ListenOptions | undefined>(event)) ?? {}

  if (typeof body !== 'object' || Array.isArray(body)) {
    setResponseStatus(event, 400)
    return { success: false, error: 'Request body must be a JSON object' }
  }

  if (body.preset && !PRESETS.has(body.preset)) {
    setResponseStatus(event, 400)
    return { success: false, error: `Unknown preset: ${body.preset}` }
  }
  if (body.talkgroups && !TG_LIST.test(body.talkgroups)) {
    setResponseStatus(event, 400)
    return { success: false, error: 'Talkgroups must be a comma-separated list of numbers' }
  }
  if (body.match && !looksLikeValidPythonRegex(body.match)) {
    setResponseStatus(event, 400)
    return { success: false, error: `Not a valid regex: ${body.match}` }
  }
  if (body.match && !isCheapRegex(body.match)) {
    setResponseStatus(event, 400)
    return {
      success: false,
      error: 'Regex rejected: nested quantifiers can hang the whitelist builder. '
        + 'Use a simple substring or alternation, e.g. BRPD or Fire|EMS.',
    }
  }
  if (body.duration !== undefined && (!Number.isInteger(body.duration) || body.duration < 1)) {
    setResponseStatus(event, 400)
    return { success: false, error: 'Duration must be a positive integer' }
  }
  // tag and match are selection sources too — requiring preset-or-talkgroups
  // alone would reject a legitimate tag-only or match-only session.
  if (!body.preset && !body.talkgroups && !body.tag && !body.match) {
    setResponseStatus(event, 400)
    return { success: false, error: 'Pick a preset, or enter talkgroup IDs, a tag, or a match regex' }
  }

  // The "already running" check lives HERE, not at the top of the handler.
  //
  // Placed before `await readBody`, it is a TOCTOU: the await yields the event
  // loop, a second request passes the same check, and both spawn. Two op25
  // instances then contend for the one HackRF, web/listen.log is truncated
  // twice, and listen.pid ends up holding only the second pid — so the first
  // process group is invisible to /api/listen/stop forever and needs a
  // terminal to kill. A double-clicked Start button is enough to trigger it.
  //
  // Everything from here to sessionStore.set() is synchronous, so on Node's
  // single thread check -> spawn -> set is atomic and needs no lock.
  if (sessionStore.isRunning()) {
    setResponseStatus(event, 409)
    return { success: false, error: 'A listening session is already running' }
  }

  // The session row is opened BEFORE the spawn so its id can be passed in the
  // environment; the recorder reads SDR_SESSION_ID and stamps it on each call.
  // Opening it afterwards would race the recorder's first flush.
  const sessionId = sessionStore.open(body)
  try {
    const { pid, config } = startListening(body, sessionId)
    sessionStore.attach(sessionId, pid)
    const session = sessionStore.get()
    return {
      success: true,
      data: { pid, config, startTime: session?.startTime ?? Date.now() / 1000 },
    }
  } catch (err) {
    // Never leave an open row behind for a session that failed to start: it
    // would read as "already running" and block every later Start.
    sessionStore.close(sessionId)
    setResponseStatus(event, 500)
    return { success: false, error: err instanceof Error ? err.message : 'Failed to start' }
  }
})
