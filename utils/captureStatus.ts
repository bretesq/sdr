/**
 * What the "Receiver" line in the bay actually means, and when a tracked
 * session with no radio reads as a stall rather than a normal startup.
 *
 * Extracted from components/bay/CommStack.vue so this can be unit tested:
 * vitest only collects server/**\/*.test.ts and utils/**\/*.test.ts (see
 * vitest.config.ts), and the grace-period arithmetic below is exactly the
 * kind of boundary logic that silently rots inside a .vue computed with no
 * test ever failing to catch it.
 *
 * THE FOURTH STATE
 * ----------------
 * `radioBusy` comes from RADIO_PATTERNS matching op25 itself (rx.py /
 * multi_rx.py), never the udp_audio_record recorders. So when op25 dies
 * while its recorders survive — the incident this file exists to catch,
 * observed four times in two days, once for 82 minutes and once for over
 * five hours, with the console giving no sign — `radioBusy` goes false while
 * `tracked` stays true: the session row is still open, and the recorders
 * hold the process group alive. Read naively as three states (on air with a
 * console session / on air from outside / idle), that combination falls
 * through to "idle" — the calmest possible reading of the worst state the
 * bay can be in. `receiverStatus()` below adds the fourth: `tracked &&
 * !radioBusy` past a grace period is `'stalled'`, not `'idle'`.
 *
 * WHY THE GRACE PERIOD IS MEASURED FROM SESSION AGE, NOT CONDITION DURATION
 * --------------------------------------------------------------------------
 * `tracked && !radioBusy` is also true, briefly, on every healthy start: the
 * session row opens before op25 has had a chance to come up. Alarming on
 * that transient would cry wolf on every normal Start, teaching the operator
 * to ignore the indicator — exactly the failure mode this project has spent
 * two days removing elsewhere (see session.ts's MAX_CONSECUTIVE_UNKNOWN
 * history). The fix is to measure the grace window from the SESSION's start
 * time, not from how long the `!radioBusy` condition has been observed to
 * hold: a count-of-polls approach was already shipped once for a different
 * signal and had to be corrected, because an unattended session with nobody
 * polling can span far more wall-clock time than a poll count implies. Grace
 * measured from `sessionStartedAt` suppresses the startup transient exactly
 * once, at the start, and then never again — a death at hour two alarms on
 * the very next read.
 */

export type ReceiverStatus = 'onAirConsole' | 'onAirOutside' | 'stalled' | 'idle'

export interface ReceiverStatusArgs {
  radioBusy: boolean
  tracked: boolean
  /**
   * Epoch seconds the open session began (Session.startTime from
   * server/utils/session.ts), or null when `tracked` is false. Ignored
   * whenever `tracked` is false.
   */
  sessionStartedAt: number | null
  /**
   * Epoch milliseconds to treat as "now". Passed in rather than read from
   * Date.now() internally so this stays pure and the grace boundary can be
   * tested on both sides without a fake timer.
   */
  nowMs: number
}

/**
 * How long a tracked session is allowed to show no radio before it reads as
 * stalled rather than starting up.
 *
 * MEASURED, not guessed — from this morning's successful cutover (session
 * 30, 2026-09-03):
 *
 *   session opened        07:38:53
 *   op25 first grant       07:38:56   (+3s)
 *   first call stamped     07:39:32   (+39s)
 *
 * op25 itself was up 3 seconds after the session opened. 45s is roughly 15x
 * that, which comfortably absorbs a slower make_whitelist.py build on a
 * larger talkgroup set than that run's 47 (the step between session-open and
 * op25-grant, and the only one whose duration scales with the whitelist
 * size) — and it still clears the +39s first-call mark from the same run,
 * so a healthy start never spends any of its life inside "stalled" even at
 * the tail end of a normal cutover. Past 45s with no radio, something that
 * should have granted by now has not, and the operator is told so on the
 * very next read rather than after 82 minutes or 5+ hours unattended — the
 * incidents that motivated this indicator.
 */
export const STALL_GRACE_MS = 45_000

/**
 * Classify the bay's receiver into exactly one of four states.
 *
 * `radioBusy` and `tracked` are reported by the server independently on
 * purpose (see server/api/listen/followed.get.ts's own docstring): a session
 * started from a shell rather than from this console reads
 * tracked:false/radioBusy:true, and that is a legitimately different state
 * from either console-started capture or a stall, not a variant of "idle".
 */
export function receiverStatus(args: ReceiverStatusArgs): ReceiverStatus {
  const { radioBusy, tracked, sessionStartedAt, nowMs } = args

  if (radioBusy && tracked) return 'onAirConsole'
  if (radioBusy) return 'onAirOutside'
  if (!tracked) return 'idle'

  // tracked && !radioBusy: either a session that just opened (op25 has not
  // granted yet) or one whose op25 died while its recorders kept the process
  // group alive. `sessionStartedAt` is always set for a tracked session (see
  // sessionStore.open() in server/utils/session.ts) — the `null` fallback to
  // Infinity is a fail-toward-alarming default for a shape that should be
  // unreachable in practice, not a state this function expects to see.
  const ageMs = sessionStartedAt === null ? Number.POSITIVE_INFINITY : nowMs - sessionStartedAt * 1000
  return ageMs > STALL_GRACE_MS ? 'stalled' : 'idle'
}

/**
 * THE SILENT-EXPIRY PROBLEM
 * --------------------------
 * `receiverStatus()` above answers "is the radio actually receiving right
 * now" — it says nothing about a running capture's future. A session started
 * with `--pd <seconds>` (see server/utils/processes.ts's buildListenArgs())
 * stops on its own the moment that many seconds elapse, and the ONLY
 * observable difference between that clean stop and an op25 crash is the
 * process's own exit code, which nothing in this UI reads. Two runs
 * (launch_184155, launch_230425) each ran for exactly the three hours their
 * own `--pd 10800` asked for and were treated as unexplained outages for two
 * days, because `on air · console session` reads identically one second
 * before expiry and one hour into a healthy run. This — not another dead-op25
 * detector like `receiverStatus()`'s "stalled" state — is the actual gap: an
 * operator watching the bay has no way to tell a bounded session is
 * *going* to end, only that it already has.
 *
 * `captureExpiry()` is the pure, testable half of the fix: given when a
 * tracked session opened and how long it was asked to run, when does it end.
 * It does not decide whether that is "soon" or format a clock string — see
 * components/bay/CommStack.vue, which owns both, so this file stays free of
 * the locale/timezone formatting that would make expiry math hard to test
 * deterministically.
 */
export interface CaptureExpiryArgs {
  /** Epoch seconds the tracked session opened, or null when untracked. */
  sessionStartedAt: number | null
  /**
   * Seconds the session was started with (Session.config.duration from
   * server/utils/session.ts, surfaced by GET /api/listen/followed as
   * `sessionDurationSec`), or null when the session has no recorded
   * duration — an unbounded run (no `--pd`), or one whose config predates
   * this field. Null means "no expiry to report", not "expires at epoch 0".
   */
  sessionDurationSec: number | null
  /** Epoch milliseconds to treat as "now". See ReceiverStatusArgs.nowMs. */
  nowMs: number
}

export interface CaptureExpiry {
  /**
   * Epoch ms the session's requested duration elapses, or null when there is
   * no basis to compute one (untracked, or no duration recorded).
   */
  expiresAtMs: number | null
  /**
   * `expiresAtMs - nowMs`. Negative once the session has run past its
   * requested duration. Mirrors `expiresAtMs`: null exactly when it is null.
   */
  remainingMs: number | null
}

export function captureExpiry(args: CaptureExpiryArgs): CaptureExpiry {
  const { sessionStartedAt, sessionDurationSec, nowMs } = args
  if (sessionStartedAt === null || sessionDurationSec === null) {
    return { expiresAtMs: null, remainingMs: null }
  }
  const expiresAtMs = sessionStartedAt * 1000 + sessionDurationSec * 1000
  return { expiresAtMs, remainingMs: expiresAtMs - nowMs }
}

/**
 * WHY THESE ARE NOT DERIVED FROM `ReceiverStatus`
 * ------------------------------------------------
 * The obvious way to answer "can the bay's capture control offer Start /
 * Stop right now" is a lookup table keyed on `ReceiverStatus` — and it is
 * wrong, in a way that only shows up in the first ~45 seconds after a real
 * Start. `ReceiverStatus` collapses `tracked && !radioBusy` into a single
 * `'idle'` bucket whether the session is untracked-and-truly-idle or
 * tracked-and-still-warming-up (op25 measured up in +3s on a healthy start,
 * see STALL_GRACE_MS's own docstring) — that collapse is exactly the point
 * of `'idle'` as a DISPLAY state, so the Receiver line doesn't flicker
 * "stalled" during a routine startup. But it means a lookup table on
 * `ReceiverStatus` would read a session mid-startup as plain `'idle'`,
 * which offers Start again (a double-Start race: the second call either
 * races the still-opening session row or bounces off the control API's own
 * 409) and offers no Stop at all for up to 45 seconds — the operator's
 * only way out of a startup gone wrong would be to wait.
 *
 * These two read the two RAW signals `receiverStatus()` itself starts from
 * (`tracked`, `radioBusy`) instead, which is the only way to keep the
 * fifth, unnamed state — "our session row is open but op25 hasn't granted
 * yet" — distinct from genuine idle. `sessionStartedAt`/grace timing simply
 * do not matter for either of these: whether a tracked session is 2 seconds
 * or 2 hours old, Stop must always be able to reach it, and Start must
 * always refuse while it is open.
 */

export interface CaptureAffordanceArgs {
  /** See ReceiverStatusArgs.tracked — an open session row, ours. */
  tracked: boolean
  /** See ReceiverStatusArgs.radioBusy — op25 holding a HackRF, ours or not. */
  radioBusy: boolean
}

/**
 * Stop is reachable whenever OUR session row is open — on air, still
 * starting, or stalled with op25 gone. It is deliberately NOT offered for
 * `onAirOutside` (radioBusy with no tracked row of ours): per the brief,
 * stopping a capture this console did not start is not this console's to
 * do, and `stopDelegatedCapture()`/`stopListening()` have no session of
 * ours to target there anyway — see server/api/listen/stop.post.ts's own
 * untracked-radioBusy branch, which takes a different, pid-less recovery
 * path rather than a normal session stop.
 */
export function canStopCapture(args: Pick<CaptureAffordanceArgs, 'tracked'>): boolean {
  return args.tracked
}

/**
 * Start is offered only when there is neither a session row of ours open
 * NOR a foreign op25 already holding a HackRF. The latter matters even
 * though nothing here is "our" session: attempting Start while some other
 * capture holds the radio would only contend for it — `POST
 * /api/listen/start` has no way to know that in advance and would spawn (or
 * delegate) anyway, so refusing it here is the one honest thing the control
 * can do before that round trip.
 */
export function canStartCapture(args: CaptureAffordanceArgs): boolean {
  return !args.tracked && !args.radioBusy
}
