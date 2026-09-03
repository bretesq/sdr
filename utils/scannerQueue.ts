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

import { encryptionState } from './callEncryption'

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

/**
 * What the queue decided to DO with a call — never what the call IS.
 *
 * `'locked'` is deliberately coarser than the encryption vocabulary: it means
 * "do not push this at the speakers", and it covers BOTH `'locked'` (ADP under
 * a key we do not hold) and `'unhandled'` (encrypted under an algorithm this
 * console does not implement). Those are genuinely different facts about the
 * call, and collapsing them is correct HERE because the playback decision is
 * identical for both.
 *
 * It is not correct anywhere a sentence about the call gets rendered. A
 * renderer that reads `kind === 'locked'` and prints "no key held · crack
 * target" is wrong for every non-ADP algid: there is no ADP key involved, "no
 * key held" implies one exists that we could hold, and "crack target"
 * nominates it for ADP keystream recovery. components/ScannerFeed.vue did
 * exactly that and was deleted for it. Anything that needs to SAY what a call
 * is must call `encryptionState()` on `entry.call` — which is what
 * components/bay/CallStrip.vue does — rather than re-deriving a claim from
 * this three-valued verdict.
 */
export type Admission = 'playable' | 'locked' | 'rejected'

export interface QueueEntry {
  call: FeedCall
  /** See `Admission`: a playback verdict, not a description of the call. */
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
 *
 * It is also decided by utils/callEncryption.ts rather than by an `algid ===
 * ADP_ALGID` test written out here. This function used to carry that test
 * itself, which meant a call encrypted under any algorithm OTHER than ADP was
 * admitted as `'playable'` and its undecodable audio actually pushed through
 * the speakers — the docstring above warns about playing noise, and the
 * predicate underneath it did exactly that for every non-ADP algid. Sharing
 * one classifier with the strip also guarantees the two can never disagree:
 * a strip reading "recorded, not decoded" while that same call plays is a
 * worse failure than either half alone.
 */
export function classify(
  call: FeedCall,
  selectedTgids: ReadonlySet<number>,
  heldKeyIds: ReadonlySet<number>,
): Admission {
  if (call.tgid === null || !selectedTgids.has(call.tgid)) return 'rejected'
  const enc = encryptionState(call, heldKeyIds)
  // 'unknown' (no ESS captured) stays playable: 77% of the corpus has no
  // algid, and refusing to play all of it on the chance some is encrypted
  // would silence the scanner. Encrypted-but-undecodable is what we refuse.
  return enc === 'locked' || enc === 'unhandled' ? 'locked' : 'playable'
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
  // Mutates `entries` IN PLACE rather than reassigning it.
  //
  // Rebuilding into a new array and assigning `queue.entries = kept` would be
  // simpler to read, and would quietly break any consumer holding a reference
  // to the array — which a Vue composable does the moment it aliases it into a
  // ref. `takeNext` already splices in place, so doing the same here keeps one
  // uniform contract: the array identity a caller obtains stays valid for the
  // life of the queue.
  //
  // Iterated backwards because splicing during a forward walk skips the
  // element after each removal.
  let dropped = 0
  for (let i = queue.entries.length - 1; i >= 0; i--) {
    const e = queue.entries[i]
    if (nowMs - endedAtMs(e.call) > stalenessMs) {
      if (e.kind === 'playable') {
        queue.skipped += 1
        dropped += 1
      }
      queue.entries.splice(i, 1)
    }
  }
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
