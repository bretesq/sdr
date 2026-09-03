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
