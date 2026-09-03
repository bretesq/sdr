/**
 * The capture-control surface the Strip Bay is actually allowed to offer.
 *
 * `server/utils/processes.ts`'s `buildControlRequest()` is the real gate: the
 * web container cannot spawn op25 itself (see `captureCapabilityGap()` in
 * that file), so every Start from here delegates to the capture container's
 * control API, and that function refuses almost everything the pre-redesign
 * `ListenControl.vue` used to offer — talkgroup/tag/match selection, area,
 * partial-encryption, Whisper, per-band receiver counts, the census toggle,
 * and every capture mode except the two-radio multi-receiver one. What
 * survives is exactly what this module models: a fixed preset, a bounded
 * duration, and two independent booleans. Rendering a control for anything
 * outside that would let the operator configure a request that is refused
 * the instant it reaches the server — worse than not offering it at all,
 * per the brief this file was written against.
 *
 * `mode` and `preset` are not fields an operator picks here at all:
 *   - `mode` MUST be `'multi'` — `buildControlRequest()` refuses every other
 *     value outright ("the capture container only runs multi-receiver
 *     captures"). There is no other delegatable mode to choose between.
 *   - `preset` MUST be `'pd'` — the only value `buildControlRequest()` lets
 *     through, and also the only way `POST /api/listen/start`'s own
 *     "pick a preset, or enter talkgroup IDs, a tag, or a match regex" check
 *     (server/api/listen/start.post.ts) can be satisfied at all, since
 *     talkgroups/tag/match are each refused separately by the same
 *     function. Sending it is required, but it is not a choice — pinning it
 *     here rather than rendering a one-item dropdown is honest about that.
 *     (`preset` is in fact never forwarded past that gate: capture_control.py's
 *     `ALLOWED_FIELDS` has no `preset` key at all — it hardcodes `--pd` itself
 *     unconditionally, per that file's own build_args() comment. The value
 *     sent here only has to satisfy the two upstream checks that decide
 *     whether the request is delegatable in the first place.)
 */

/** capture_control.py's own MAX_DURATION_SEC — pinning two exclusive HackRFs
 *  for longer than this on what is almost certainly a typo is refused there
 *  rather than honored. Bounding the input here too means the operator learns
 *  that before a round trip, not after a 400. */
export const MAX_CAPTURE_DURATION_SEC = 24 * 60 * 60

/** capture_control.py's own MIN_DURATION_SEC: 0 or negative would make
 *  lwin_listen_multi.sh treat the run as UNBOUNDED, the opposite of what a
 *  caller who typed a duration at all very clearly asked for. */
export const MIN_CAPTURE_DURATION_SEC = 1

/**
 * Matches ListenControl.vue's own default (see that file's `duration` ref
 * docstring) and capture_control.py's MAX_DURATION_SEC: the longest a
 * delegated capture can run at all, so a blank/first-load field is a
 * deliberate choice rather than a number invented in the moment — the
 * failure mode that docstring documents two real sessions actually hitting.
 */
export const DEFAULT_CAPTURE_DURATION_SEC = MAX_CAPTURE_DURATION_SEC

/**
 * Is this a duration `POST /api/listen/start` and capture_control.py will
 * both accept, rather than a value that bounces back as a 400 after the
 * round trip?
 *
 * `string | null` alongside `number` because of exactly how Vue's
 * `v-model.number` behaves, not how it is often assumed to: it runs the raw
 * DOM string through `looseToNumber` (`parseFloat`, falling back to the
 * ORIGINAL string when that's `NaN`) — so an emptied `<input type="number">`
 * puts the empty STRING `''` in the ref, never `null` or `0`. `null` is kept
 * in the accepted type anyway as a defensive default for a ref no code path
 * here actually assigns, and any other non-numeric string reaching this
 * function (paste, autofill) is refused the same way `''` is: `typeof
 * seconds !== 'number'` is the one check that covers all of it, without
 * this function needing to know which string shape produced it.
 */
export function isValidCaptureDuration(seconds: number | string | null): boolean {
  if (typeof seconds !== 'number') return false
  return Number.isInteger(seconds)
    && seconds >= MIN_CAPTURE_DURATION_SEC
    && seconds <= MAX_CAPTURE_DURATION_SEC
}

/** The exact, and only, request body this console ever sends to `POST /api/listen/start`. */
export interface CaptureStartBody {
  mode: 'multi'
  preset: 'pd'
  duration: number
  ess: boolean
  includeEncrypted: boolean
}

/**
 * Build that body from the operator's two actual choices (duration, and the
 * two independent switches) plus the two fixed fields documented above.
 *
 * A single function that always emits this exact shape is what keeps a
 * future edit to this component from accidentally smuggling an extra field
 * (a stray `talkgroups` left over from a copy-paste, say) into a request
 * that `buildControlRequest()` would then refuse wholesale — every other
 * field it doesn't recognize turns the whole delegation down, per that
 * function's own docstring, so one typo here would silently break Start
 * entirely rather than just being ignored.
 */
export function buildCaptureStartBody(opts: {
  duration: number
  ess: boolean
  includeEncrypted: boolean
}): CaptureStartBody {
  return {
    mode: 'multi',
    preset: 'pd',
    duration: opts.duration,
    ess: opts.ess,
    includeEncrypted: opts.includeEncrypted,
  }
}

/**
 * ofetch (Nuxt's `$fetch`) throws a `FetchError` whose `.message` is just the
 * status line (`[POST] "/api/listen/start": 409 Conflict`) — the control
 * API's own human-readable text is on `.data.error`
 * (`server/api/listen/start.post.ts` and `stop.post.ts` both return a thrown
 * message verbatim in that field). Without unwrapping it here, the operator
 * sees a useless status line instead of e.g. "A listening session is already
 * running" or a capture-container validation error — exactly the "must not
 * lie or flatten a failure into something vague" ethos this project is built
 * on. Identical in behaviour to the same-named helper already duplicated in
 * `ListenControl.vue` and `RecordingsList.vue`; extracted here instead of
 * copied a third time, since it is a pure function this project's test
 * layout (`utils/**\/*.test.ts`) can actually cover.
 */
export function apiError(e: unknown, fallback: string): string {
  if (e && typeof e === 'object' && 'data' in e) {
    const d = (e as { data?: unknown }).data
    if (d && typeof d === 'object' && 'error' in d
        && typeof (d as { error?: unknown }).error === 'string') {
      return (d as { error: string }).error
    }
  }
  return e instanceof Error ? e.message : fallback
}
