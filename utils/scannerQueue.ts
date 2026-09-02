/**
 * The Scanner Feed's queue, as a pure module.
 *
 * No DOM, no fetch, no Vue — everything that decides WHICH call plays and WHEN
 * one is too old to bother with lives here, so it can be tested directly. The
 * composable around it does only wiring: SSE in, <audio> out.
 *
 * Scanner semantics: one output, calls play in the order they happened, and a
 * call that has been waiting longer than the staleness bound is dropped rather
 * than played late. With up to four simultaneous calls across the receivers, a
 * serial player that never dropped anything would fall progressively further
 * behind live during a burst and never catch up.
 */

/** P25 ADP. Anything else in `algid` is not an encryption algorithm we gate on. */
export const ADP_ALGID = 170        // 0xAA

/** The fields of a `Recording` the queue actually needs. */
export interface FeedCall {
  id: number
  file: string
  tgid: number | null
  alpha: string | null
  start: number
  dur: number
  endedAt: number | null
  algid: number | null
  keyid: number | null
}

export type Admission = 'playable' | 'locked' | 'rejected'

export interface QueueEntry {
  call: FeedCall
  kind: 'playable' | 'locked'
}

export interface ScannerQueue {
  entries: QueueEntry[]
  /** Playable calls dropped for age. Locked ones are not counted. */
  skipped: number
}

export function createQueue(): ScannerQueue {
  return { entries: [], skipped: 0 }
}

/**
 * When this call ENDED, in ms.
 *
 * Staleness is measured from the end of the transmission, never from its
 * start: the longest measured call is 32.4 s against a default 30 s bound, so
 * ageing from `start` would make every long transmission born stale and drop
 * it before it was ever played.
 *
 * `endedAt` is populated on every row the recorder writes; the `start + dur`
 * fallback covers a row written by some other path.
 */
export function endedAtMs(call: FeedCall): number {
  const sec = call.endedAt ?? call.start + call.dur
  return sec * 1000
}

/**
 * Should this call play, appear silently, or be ignored?
 *
 * Encryption is decided from `algid`/`keyid`, NOT from `encObserved` /
 * `encEvidence`. Those two are filled by a later reconciliation pass and are
 * null on every live row, so a filter keyed off them classifies everything as
 * clear and plays noise.
 */
export function classify(
  call: FeedCall,
  selectedTgids: ReadonlySet<number>,
  heldKeyIds: ReadonlySet<number>,
): Admission {
  if (call.tgid === null || !selectedTgids.has(call.tgid)) return 'rejected'
  if (call.algid === ADP_ALGID && !heldKeyIds.has(call.keyid ?? -1)) return 'locked'
  return 'playable'
}

/**
 * Classify and enqueue. Returns what was decided.
 *
 * A call already in the queue is refused: the id cursor should make a duplicate
 * impossible, but a retried fetch must not double-play.
 */
export function admit(
  queue: ScannerQueue,
  call: FeedCall,
  selectedTgids: ReadonlySet<number>,
  heldKeyIds: ReadonlySet<number>,
): Admission {
  if (queue.entries.some(e => e.call.id === call.id)) return 'rejected'
  const kind = classify(call, selectedTgids, heldKeyIds)
  if (kind === 'rejected') return 'rejected'
  queue.entries.push({ call, kind })
  return kind
}

/**
 * Drop everything that ended longer than `stalenessMs` ago.
 *
 * Locked entries age out on the same bound so the panel does not accumulate
 * them, but they do not count toward `skipped` — reporting noise you were never
 * going to hear as a loss would be misleading.
 */
export function prune(queue: ScannerQueue, nowMs: number, stalenessMs: number): number {
  const kept: QueueEntry[] = []
  let dropped = 0
  for (const e of queue.entries) {
    if (nowMs - endedAtMs(e.call) > stalenessMs) {
      if (e.kind === 'playable') {
        queue.skipped += 1
        dropped += 1
      }
      continue
    }
    kept.push(e)
  }
  queue.entries = kept
  return dropped
}

/**
 * Prune, then remove and return the oldest playable call.
 *
 * Pruning happens here rather than on a timer so a backgrounded tab — where
 * browsers throttle timers hard — cannot leave the queue in a stale state.
 * Locked entries are stepped over and left in place; they are display-only.
 */
export function takeNext(
  queue: ScannerQueue,
  nowMs: number,
  stalenessMs: number,
): FeedCall | null {
  prune(queue, nowMs, stalenessMs)
  const i = queue.entries.findIndex(e => e.kind === 'playable')
  if (i === -1) return null
  const [entry] = queue.entries.splice(i, 1)
  return entry.call
}
