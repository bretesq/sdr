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
 * survives is exactly what this module models: a talkgroup preset, a bounded
 * duration, and two independent booleans. Rendering a control for anything
 * outside that would let the operator configure a request that is refused
 * the instant it reaches the server — worse than not offering it at all,
 * per the brief this file was written against.
 *
 * `mode` is still not a field an operator picks here: it MUST be `'multi'`,
 * since `buildControlRequest()` refuses every other value outright ("the
 * capture container only runs multi-receiver captures"). There is no other
 * delegatable mode to choose between, so it is pinned rather than rendered
 * as a one-item dropdown.
 *
 * `preset` USED TO BE pinned the same way, and no longer is. Until the
 * preset picker landed it was `'pd'` and nothing else: `buildControlRequest()`
 * refused every other value, and `capture_control.py`'s `ALLOWED_FIELDS` had
 * no `preset` key at all — it hardcoded `--pd` itself, so even the one
 * permitted value was never actually forwarded. Both of those are fixed now
 * (`PRESET_ARGV` in that file maps each allowlisted name to the fixed argv it
 * emits), and this is a real operator choice: `pd` alone is ~44 of the
 * roster's talkgroups, which is why the bay could only ever show that many.
 * It is still the only selection dimension offered — talkgroups, tag and
 * match remain refused by `buildControlRequest()`, so a preset is also the
 * only way `POST /api/listen/start`'s "pick a preset, or enter talkgroup IDs,
 * a tag, or a match regex" check can be satisfied from this console at all.
 */

/**
 * The talkgroup presets a console-started capture can run.
 *
 * The canonical TypeScript copy: `server/utils/processes.ts` imports this
 * list for `buildControlRequest()`'s gate and `server/api/listen/start.post.ts`
 * imports it for its own, so the browser control, the delegation gate and the
 * HTTP endpoint cannot disagree about what is selectable. The far side of the
 * wire has its own copy it must own (`scripts/capture_control.py`'s
 * `PRESET_ARGV` — argv construction is that file's job and its security
 * boundary, and it validates independently of whatever this console sends);
 * that copy is pinned to `scripts/make_whitelist.py`'s `PRESETS` by a test
 * there, and to this one by a test in `server/utils/processes.test.ts`.
 *
 * Ordered broadest-service-first rather than alphabetically, because that is
 * how the picker reads them out: each service's dispatch-only preset
 * immediately followed by its wider variant.
 */
export const CAPTURE_PRESETS = [
  'pd', 'pd-all', 'fire', 'fire-all', 'ems', 'interop', 'schools', 'publicworks', 'all',
] as const

export type CapturePreset = typeof CAPTURE_PRESETS[number]

/**
 * What every capture ran before the picker existed, and what a request that
 * names no preset still runs (`capture_control.py`'s `DEFAULT_PRESET`).
 * Police/sheriff dispatch.
 */
export const DEFAULT_CAPTURE_PRESET: CapturePreset = 'pd'

/**
 * Narrow an arbitrary string to a preset this console can actually send.
 *
 * `ListenOptions.preset` is typed `string` — it models the FULL local-spawn
 * surface, where `lwin_listen_multi.sh` also accepts tag/match/tg selection —
 * so the narrowing has to happen at the delegation gate rather than in the
 * type. This is that check, shared so the gate and the picker apply the same
 * one.
 */
export function isCapturePreset(value: unknown): value is CapturePreset {
  return typeof value === 'string' && (CAPTURE_PRESETS as readonly string[]).includes(value)
}

/**
 * The operator-facing name of each preset.
 *
 * Taken verbatim from `server/api/config/presets.get.ts`, which has served
 * exactly these nine labels since the pre-redesign `ListenControl.vue` used
 * them — this is the project's existing vocabulary for these presets, not a
 * new one invented here. That endpoint now builds its response from this
 * record instead of holding its own copy, so the console picker and the API
 * cannot describe the same preset differently.
 */
export const CAPTURE_PRESET_LABELS: Record<CapturePreset, string> = {
  'pd': 'Police / Sheriff Dispatch',
  'pd-all': 'Police — Dispatch + Talk + Tac',
  'fire': 'Fire Dispatch',
  'fire-all': 'Fire — Dispatch + Tac + Talk',
  'ems': 'EMS + Hospital',
  'interop': 'Interop / Emergency Ops',
  'schools': 'Schools',
  'publicworks': 'Public Works',
  'all': 'All Baton Rouge Area',
}

/**
 * What each preset actually selects — the precise version of the label above.
 *
 * These are the TAG NAMES from `scripts/make_whitelist.py`'s `PRESETS` dict,
 * verbatim — the literal thing that script filters the roster by. They are
 * deliberately NOT talkgroup counts: a count depends on the reference roster,
 * the Baton-Rouge-area category filter, the reviewed encryption overrides AND
 * the operator's own include-encrypted switch, so any number written here
 * would be a snapshot that silently rots as the roster changes. Naming the
 * tags tells the operator what a preset covers without inventing a figure
 * this console cannot honestly compute. (The live count for the RUNNING
 * session is already on screen, from the session whitelist — the Standby
 * block's "N followed".)
 */
export const CAPTURE_PRESET_TAGS: Record<CapturePreset, string> = {
  'pd': 'Law Dispatch',
  'pd-all': 'Law Dispatch + Law Talk + Law Tac',
  'fire': 'Fire Dispatch',
  'fire-all': 'Fire Dispatch + Fire-Tac + Fire-Talk',
  'ems': 'EMS Dispatch + EMS-Tac + EMS-Talk + Hospital',
  'interop': 'Interop + Emergency Ops + Multi-Tac + Multi-Dispatch',
  'schools': 'Schools',
  'publicworks': 'Public Works + Utilities + Transportation',
  'all': 'every Baton Rouge-area talkgroup, untagged ones included',
}

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
 * capture_control.py's MAX_DURATION_SEC: the longest a delegated capture can
 * run at all, so a blank/first-load field is a deliberate choice rather than a
 * number invented in the moment. Blank is not "until stopped" on this
 * deployment — buildControlRequest() refuses a delegated request with no
 * duration — so a blank field forced the operator to invent a number, which is
 * how two real sessions both got 10800 (3h) typed in as whatever came to mind,
 * with nothing to renew the capture at that mark. Pre-filling the longest sane
 * value turns that guess into a deliberate one.
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
  preset: CapturePreset
  duration: number
  ess: boolean
  includeEncrypted: boolean
}

/**
 * Build that body from the operator's actual choices (preset, duration, and
 * the two independent switches) plus the one fixed field documented above.
 *
 * `preset` is optional here and defaults to `DEFAULT_CAPTURE_PRESET`. That is
 * not laziness about the caller: it means a caller that has not been taught
 * about presets yet still emits exactly the body this console sent before the
 * picker existed, so "no preset chosen" can never silently become "some other
 * preset" — the one substitution that would start a capture the operator did
 * not ask for.
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
  preset?: CapturePreset
}): CaptureStartBody {
  return {
    mode: 'multi',
    preset: opts.preset ?? DEFAULT_CAPTURE_PRESET,
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
 * on. It was written to replace a same-named helper that the pre-redesign
 * `ListenControl.vue` and `RecordingsList.vue` each carried their own copy of
 * (both deleted since); extracted here rather than copied a third time,
 * because it is a pure function this project's test layout
 * (`utils/**\/*.test.ts`) can actually cover — and it is the single copy the
 * bay now uses.
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
