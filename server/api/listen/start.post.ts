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

export default defineEventHandler(async (event) => {
  if (sessionStore.isRunning()) {
    setResponseStatus(event, 409)
    return { success: false, error: 'A listening session is already running' }
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

  try {
    const { pid, config } = startListening(body)
    const startTime = Date.now() / 1000
    sessionStore.set({ pid, config, startTime })
    return { success: true, data: { pid, config, startTime } }
  } catch (err) {
    setResponseStatus(event, 500)
    return { success: false, error: err instanceof Error ? err.message : 'Failed to start' }
  }
})
