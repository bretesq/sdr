<template>
  <aside class="stack">
    <!-- ACTIVE: what the bay is doing right now -->
    <div class="stack__block">
      <span class="stack__label">Active</span>
      <div class="readout" :class="{ 'readout--dim': !armed }">
        {{ armed ? selected.length : '—' }}<span class="readout__unit"> tg armed</span>
      </div>
      <button
        class="arm"
        :class="{ 'arm--on': armed }"
        :disabled="!armed && selected.length === 0"
        style="margin-top: 10px"
        @click="$emit('toggle')"
      >
        <span class="arm__lamp" />
        {{ armed ? 'Stop' : 'Arm bay' }}
      </button>
      <p v-if="!armed && selected.length === 0" class="idle__sub" style="text-align: left; margin-top: 8px">
        Tick a talkgroup below to arm.
      </p>
    </div>

    <!-- RECEIVER: the machinery, reported honestly -->
    <div class="stack__block">
      <span class="stack__label">Receiver</span>
      <div class="readout readout--dim" style="font-size: 15px">
        {{ receiverLine }}
      </div>
      <p class="idle__sub" style="text-align: left; margin-top: 6px">
        {{ receiverNote }}
      </p>
    </div>

    <!-- STANDBY: the talkgroups this session can actually produce -->
    <div class="stack__block" style="padding-bottom: 8px">
      <span class="stack__label">Standby — {{ followed.length }} followed, {{ activeCount }} active</span>
      <input
        v-model="query"
        class="field"
        type="search"
        placeholder="filter talkgroups"
        aria-label="Filter talkgroups"
      >
    </div>

    <div class="standby">
      <button
        v-for="t in shown"
        :key="t.tgid"
        class="standby__row"
        :class="{ 'standby__row--on': selected.includes(t.tgid) }"
        type="button"
        :aria-pressed="selected.includes(t.tgid)"
        @click="$emit('toggleTg', t.tgid)"
      >
        <span class="standby__tick" />
        <span>{{ t.tgid }}</span>
        <span class="standby__name">{{ t.alpha ?? 'unlisted' }}</span>
        <span class="standby__n">{{ t.recentCalls || '' }}</span>
      </button>
      <p v-if="!shown.length" class="idle">
        no match
        <span class="idle__sub">{{ followed.length ? 'Nothing in the session whitelist matches that.' : 'No session has written a whitelist yet.' }}</span>
      </p>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { receiverStatus, captureExpiry } from '~/utils/captureStatus'
import type { ReceiverStatus } from '~/utils/captureStatus'

/**
 * The comm stack: active above, standby below, exactly as a radio stack reads.
 *
 * The standby list is the session whitelist rather than the full 4,163-row
 * reference, because op25 only emits audio for talkgroups the running session
 * follows — a row offered here that cannot produce sound would be a lie the
 * operator could not see through.
 */

const props = defineProps<{
  followed: { tgid: number, alpha: string | null, recentCalls: number }[]
  selected: number[]
  armed: boolean
  radioBusy: boolean
  tracked: boolean
  /** Epoch seconds the tracked session opened, or null when untracked. */
  sessionStartedAt: number | null
  /**
   * Seconds the tracked session was started with, or null when untracked or
   * the session has no recorded duration (an unbounded run, no `--pd`). See
   * server/api/listen/followed.get.ts's own docstring for where this comes
   * from — it is the operator's own Start request, never a guess made here.
   */
  sessionDurationSec: number | null
}>()

defineEmits<{ toggle: [], toggleTg: [tgid: number] }>()

const query = ref('')

const activeCount = computed(() => props.followed.filter(t => t.recentCalls > 0).length)

const shown = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return props.followed
  return props.followed.filter(
    t => String(t.tgid).includes(q) || (t.alpha ?? '').toLowerCase().includes(q),
  )
})

/**
 * radioBusy and tracked are reported separately on purpose. A session started
 * from a shell rather than through this console reads busy-but-untracked, and
 * the feed works perfectly in that state — calling it "no session" would make a
 * working bay look dead.
 *
 * The fourth state — tracked with no radio, past a grace period — is the one
 * this component used to have no way to say at all: RADIO_PATTERNS matches
 * op25 itself, not its recorders, so op25 dying while its recorders survive
 * used to fall through to "idle" here, the calmest possible reading of a
 * session that is open with nothing receiving. receiverStatus() (utils/
 * captureStatus.ts) is what tells "just started" apart from "actually
 * stalled" — see that file for the grace period and its measurement.
 *
 * No ticking clock drives this: Date.now() is read fresh each time this
 * computed re-evaluates, which is only when a prop changes (a fresh
 * /api/listen/followed read). A timer that just re-ran the same stale
 * radioBusy/tracked values through Date.now() would buy nothing — the radio
 * state itself only changes on the next server read, ticker or not.
 */
const status = computed(() => receiverStatus({
  radioBusy: props.radioBusy,
  tracked: props.tracked,
  sessionStartedAt: props.sessionStartedAt,
  nowMs: Date.now(),
}))

/**
 * THE SILENT-EXPIRY PROBLEM — see utils/captureStatus.ts's captureExpiry()
 * for the full incident writeup. Short version: `on air · console session`
 * used to read identically one second before a bounded session's `--pd`
 * duration ran out and one hour into a healthy run, because nothing here
 * knew the session HAD a deadline. `expiry` is the pure timing math
 * (captureExpiry(), unit-tested for its own boundaries); the wall-clock
 * string below is assembled here rather than in that file so the tested
 * logic stays free of `toLocaleTimeString`'s locale/timezone dependence —
 * exactly the same split this component already keeps between
 * receiverStatus() (state) and RECEIVER_LINE/RECEIVER_NOTE (strings).
 *
 * Same "no ticking clock" note as `status` above applies to `expiresAtMs`
 * itself — it is a fixed point in time, so a stale computed (this only
 * re-evaluates when a prop changes, i.e. on the next /api/listen/followed
 * poll) still reads correctly. It would NOT be safe to derive a live
 * countdown ("ends in 12m") from `remainingMs` the same way: that number
 * only updates on the next poll too, so a countdown would freeze at
 * whatever value it had when this last recomputed and go stale the moment
 * time keeps moving without a fresh poll — decaying into exactly the kind
 * of quietly-wrong readout this feature exists to remove. An absolute clock
 * time stays true regardless of when it was last rendered, which is why
 * that is the only form used below.
 */
const expiry = computed(() => captureExpiry({
  sessionStartedAt: props.sessionStartedAt,
  sessionDurationSec: props.sessionDurationSec,
  nowMs: Date.now(),
}))

/**
 * "It ends at 14:32", appended to the on-air note — or null when there is
 * nothing to say (no duration recorded, i.e. an unbounded session), which
 * leaves RECEIVER_NOTE.onAirConsole completely unchanged, the no-regression
 * case utils/captureStatus.test.ts covers.
 *
 * `remainingMs <= 0` (the session has already run past its requested
 * duration but the next poll hasn't yet caught the resulting stop) gets its
 * own honest phrasing rather than a clock time that would read as "ends in
 * the past" — the boundary utils/captureStatus.test.ts's captureExpiry
 * suite covers numerically.
 */
const expiryNote = computed(() => {
  const { expiresAtMs, remainingMs } = expiry.value
  if (expiresAtMs === null || remainingMs === null) return null
  if (remainingMs <= 0) {
    return 'It has run past its requested duration and can stop at any moment.'
  }
  const clock = new Date(expiresAtMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return `It ends at ${clock} unless stopped first — nothing renews it automatically.`
})

// Record, not a switch: TS's exhaustiveness check on a Record's key type
// catches a future ReceiverStatus member left unhandled at compile time,
// which a switch with no default cannot (and vue/return-in-computed-property
// rejects a switch with no default anyway, since it cannot see the switch is
// exhaustive over a closed union).
const RECEIVER_LINE: Record<ReceiverStatus, string> = {
  onAirConsole: 'on air · console session',
  onAirOutside: 'on air · outside session',
  stalled: 'stalled · console session open',
  idle: 'idle',
}

const RECEIVER_NOTE: Record<ReceiverStatus, string> = {
  onAirConsole: 'This console started the capture and can stop it.',
  onAirOutside: 'Something else started this capture. The bay still fills; stopping it is not this console’s to do.',
  stalled: 'A session is still open but nothing is receiving. Start will refuse while it stays open — Stop reaches the recorders even though op25 is gone.',
  idle: 'No capture running. Filed strips still read; nothing new will land.',
}

const receiverLine = computed(() => RECEIVER_LINE[status.value])

// Expiry is appended only to the on-air-console note, per the brief: this
// adds expiry information to the existing on-air case, not a fifth state —
// 'onAirOutside' has no session row to read a duration from, 'stalled' and
// 'idle' are not describing a running capture at all.
const receiverNote = computed(() => {
  const base = RECEIVER_NOTE[status.value]
  if (status.value === 'onAirConsole' && expiryNote.value) {
    return `${base} ${expiryNote.value}`
  }
  return base
})
</script>
