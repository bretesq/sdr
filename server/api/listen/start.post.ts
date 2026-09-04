import { startListening } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'
import type { ListenOptions } from '~/server/utils/processes'
import { CAPTURE_PRESETS } from '~/utils/listenControl'

// The nine presets, from the one shared list — no longer a second copy
// hand-verified against make_whitelist.py. It matters more than it used to:
// this endpoint's check and the delegation gate's check used to disagree on
// purpose (this one accepted all nine, buildControlRequest() accepted only
// "pd"), so a preset could pass here and be refused one layer down. Now that
// every preset is genuinely selectable, the two must agree exactly, and
// sharing the list is the only way that stays true without a third place to
// forget.
const PRESETS = new Set<string>(CAPTURE_PRESETS)
const TG_LIST = /^\d+(,\d+)*$/

const MODES = new Set(['single', 'multi'])
// Verified against make_multirx_cfg.py's LEGS dict.
const LEGS = new Set(['700', '800', '700,800'])
/**
 * Upper bound on voice receivers per band.
 *
 * Not arbitrary: each channel adds a decimating FIR running at the device's
 * full sample rate, and each needs its own udp_audio_record.py process and a
 * UDP port two above the last. 8 per band keeps the port block inside
 * 23460-23496 (widened from 23492 as SNDCP data receivers were added, one
 * per leg, each an always-present extra channel -- see make_multirx_cfg.py's
 * PORT_BLOCK_SPAN) and stays above the 800 leg's default of 7 (itself derived from
 * concurrency measurement -- see scripts/make_multirx_cfg.py's LEG_800
 * comment: peak concurrency 5 of 5 receivers, 17 at-capacity events in 7,136
 * calls) with headroom for an operator override, not a number this endpoint
 * expects most requests to reach.
 */
const MAX_VOICE = 8

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
  if (body.mode !== undefined && !MODES.has(body.mode)) {
    setResponseStatus(event, 400)
    return { success: false, error: `Unknown mode: ${body.mode}` }
  }

  // The multi-only knobs are rejected outright in single mode rather than
  // ignored: buildListenArgs drops them, so honouring the request silently
  // would start a session shaped differently from the one asked for.
  const multiOnly = ['legs', 'nVoice700', 'nVoice800', 'census'] as const
  if (body.mode !== 'multi') {
    const used = multiOnly.filter(k => body[k] !== undefined)
    if (used.length) {
      setResponseStatus(event, 400)
      return {
        success: false,
        error: `${used.join(', ')} require mode "multi" (two-radio, multi-receiver capture)`,
      }
    }
  }

  if (body.legs !== undefined && !LEGS.has(body.legs)) {
    setResponseStatus(event, 400)
    return { success: false, error: `Unknown legs: ${body.legs}. Use 700, 800, or 700,800` }
  }
  // The 800 MHz leg has no live control channel — 851.0375 and 851.4875 both
  // measured +0.5 dB with 0% continuity — so a session confined to it would
  // never see a grant and never record anything.
  if (body.legs === '800') {
    setResponseStatus(event, 400)
    return {
      success: false,
      error: 'The 800 MHz leg has no live control channel; it must be paired '
        + 'with 700. Use legs "700,800".',
    }
  }
  for (const key of ['nVoice700', 'nVoice800'] as const) {
    const v = body[key]
    if (v !== undefined && (!Number.isInteger(v) || v < 1 || v > MAX_VOICE)) {
      setResponseStatus(event, 400)
      return { success: false, error: `${key} must be an integer from 1 to ${MAX_VOICE}` }
    }
  }
  if (body.census !== undefined && typeof body.census !== 'boolean') {
    setResponseStatus(event, 400)
    return { success: false, error: 'census must be a boolean' }
  }
  // Asking for 700-only receivers while covering only 800, or vice versa, is a
  // config the generator would reject after the radio was already claimed.
  if (body.mode === 'multi' && body.legs === '700' && body.nVoice800 !== undefined) {
    setResponseStatus(event, 400)
    return { success: false, error: 'nVoice800 has no effect with legs "700"' }
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
  // check -> spawn -> set is NO LONGER atomic on its own, in EITHER of two
  // ways now. startListening() is async: when this process cannot reach the
  // HackRFs itself, it awaits a network round trip to the capture
  // container's control API (server/utils/processes.ts's
  // captureCapabilityGap()/delegateStart()) — a real yield point a
  // double-click can race through, unlike the old fully-synchronous spawn.
  // And sessionStore.isRunning() itself is now ALSO a network round trip
  // whenever a delegated session is already tracked (it calls get(), which
  // asks the control API's GET /status for a delegated session's liveness —
  // see session.ts's isSessionAlive()), so this guard is no longer the fast,
  // local-only check it used to be either. The actual backstop against two
  // captures both starting lives one layer down instead:
  // scripts/capture_control.py's POST /start refuses with 409 the instant a
  // capture is already tracked, and the HackRFs themselves cannot be opened
  // twice — so the worst a raced double-click can do is have the second
  // request's control-API call come back 409, surfaced here like any other
  // thrown Error. sessionStore.isRunning() below still matters as the common
  // case that avoids ever reaching startListening() twice, but it is not a
  // synchronous, race-proof guard the way it was before either path in this
  // feature involved a network call.
  if (await sessionStore.isRunning()) {
    setResponseStatus(event, 409)
    return { success: false, error: 'A listening session is already running' }
  }

  // The session row is opened BEFORE the spawn so its id can be passed in the
  // environment; the recorder reads SDR_SESSION_ID and stamps it on each call.
  // Opening it afterwards would race the recorder's first flush.
  const sessionId = sessionStore.open(body)
  try {
    const { pid, config, backend } = await startListening(body, sessionId)
    sessionStore.attach(sessionId, pid, backend)
    const session = await sessionStore.get()
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
