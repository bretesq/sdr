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
      <!--
        LAYOUT: how many radios and voice streams are actually in play.

        The old console had this and the redesign dropped it: ListenControl.vue
        carried a config summary plus `700 MHz voice receivers` / `800 MHz
        voice receivers` controls, and the bay replaced all of it with
        `{n} armed · {idle|on air|stalled}` — which says nothing about the
        receiver pool a missed call would have been missed by. It sits in the
        Receiver block rather than in Capture because it describes the
        machinery, not a control: the counts are not settable from this
        console (see `layoutNote`), so putting them beside the Capture fields
        would invite exactly the wrong reading.

        Reference information, so it is `idle__sub` at reading size rather
        than a `readout` — the receiver STATE above is what an operator scans
        for; this is what they check once and stop noticing. Three separate
        `<p>`s rather than one, because the leg split is suppressed for a
        single-leg capture (where it only repeats the totals) and every line
        disappears together when there is no layout to report.
      -->
      <p v-if="layoutLine" class="idle__sub" style="text-align: left; margin-top: 8px">
        Layout: {{ layoutLine }}
      </p>
      <p v-if="layoutLegLine" class="idle__sub" style="text-align: left">
        {{ layoutLegLine }}
      </p>
      <p v-if="layoutNote" class="idle__sub" style="text-align: left">
        {{ layoutNote }}
      </p>
    </div>

    <!--
      CAPTURE: starts and stops the RADIO. Separate from the Active block
      above on purpose — Arm/Stop up there only gates which clips this
      browser tab plays; it has never touched op25. This is the control the
      redesign dropped entirely (no page rendered ListenControl.vue after
      the bay replaced it), so an operator had no way to start or stop a
      capture from the console at all. See utils/listenControl.ts's module
      docstring for exactly why the surface below is this small: it is every
      field server/utils/processes.ts's buildControlRequest() will actually
      delegate to the capture container, and nothing it refuses.
    -->
    <div class="stack__block">
      <span class="stack__label">Capture</span>

      <p class="idle__sub" style="text-align: left; margin-bottom: 8px">
        Two radios, multi-receiver — the only capture shape this console can hand
        to the capture container. The preset picks which talkgroups it follows.
      </p>

      <label class="capture__row capture__row--wide">
        <span>Preset</span>
        <span class="capture__row-field">
          <select
            v-model="preset"
            class="field capture__preset"
            :disabled="!canStart || busy"
            aria-label="Talkgroup preset"
          >
            <option v-for="p in CAPTURE_PRESETS" :key="p" :value="p">
              {{ p }} — {{ CAPTURE_PRESET_LABELS[p] }}
            </option>
          </select>
        </span>
      </label>
      <!--
        What the selected preset actually follows: the TAG NAMES
        make_whitelist.py filters the roster by, which is the precise version
        of the option's human label. Always shown, for the same reason
        `durationHuman` is always shown beside the duration field — a value
        whose consequences are one indirection away should state them on
        screen rather than requiring the operator to remember what "interop"
        expands to.
      -->
      <p class="idle__sub" style="text-align: left; margin-top: -2px">
        Follows: {{ CAPTURE_PRESET_TAGS[preset] }}
      </p>

      <!--
        Two things the operator must not be misled about. They are INDEPENDENT
        conditions, so they are two independent `v-if`s: chained, whichever
        rendered first would suppress the other in states where both are true.

        1. The picker applies to the NEXT Start, never to a running capture.
           The primary defence is structural, not textual: like every other
           control in this block it is `:disabled` whenever Start is
           unavailable, so it cannot be changed under a running session and
           appear to have done something. This line says so in words — and it
           is gated on `tracked`, NOT on `!canStart`. canStartCapture() is
           `!tracked && !radioBusy`, so `!canStart` is also true when the
           radio is busy with a capture this console never started
           ('onAirOutside'), where there is no console session to "retune" and
           this sentence would be describing something that does not exist.

        2. A wider preset raises concurrent-call load. The 800 MHz leg's
           voice-receiver count was raised 5 -> 7 in 042cc3a on concurrency
           measured under `pd` ALONE (peak 5 of 5, ceiling touched 17 times in
           7,136 calls); every other preset here follows strictly more
           talkgroups than that measurement covered, so the headroom behind
           those counts is unverified for them. Deliberately qualitative — the
           honest number would be a fresh measurement, and inventing a ratio
           from the current roster would rot the moment the roster changes.
           Shown whenever a wide preset is SELECTED, regardless of whether
           Start happens to be available this instant: it is a fact about the
           selection, not about the button.
      -->
      <p v-if="tracked" class="idle__sub" style="text-align: left">
        Applies to the next Start — changing it does not retune the running capture.
      </p>
      <p
        v-if="preset !== DEFAULT_CAPTURE_PRESET"
        class="idle__sub capture__warn"
        style="text-align: left"
      >
        Wider than {{ DEFAULT_CAPTURE_PRESET }}. The voice-receiver counts were tuned
        on concurrency measured under {{ DEFAULT_CAPTURE_PRESET }} alone — more
        talkgroups means more overlapping calls, and calls past the receiver pool
        are missed, not queued.
      </p>

      <label class="capture__row">
        <span>Duration</span>
        <span class="capture__row-field">
          <input
            v-model.number="duration"
            class="field capture__duration"
            type="number"
            :min="MIN_CAPTURE_DURATION_SEC"
            :max="MAX_CAPTURE_DURATION_SEC"
            :disabled="!canStart || busy"
            aria-label="Capture duration in seconds"
          >
          <span class="capture__hint-inline">{{ durationHuman }}</span>
        </span>
      </label>
      <p v-if="!durationValid" class="idle__sub capture__warn" style="text-align: left">
        Must be a whole number of seconds from {{ MIN_CAPTURE_DURATION_SEC }} to
        {{ MAX_CAPTURE_DURATION_SEC }} (24h) — capture_control.py's own bound. Required:
        the delegated request has no "run until stopped".
      </p>

      <label class="capture__toggle">
        <input v-model="ess" type="checkbox" :disabled="!canStart || busy">
        Capture encryption headers (ESS, ~10&times; log volume)
      </label>

      <label class="capture__toggle">
        <input v-model="includeEncrypted" type="checkbox" :disabled="!canStart || busy">
        Include fully-encrypted talkgroups (records silence)
      </label>

      <button
        class="arm"
        :class="{ 'arm--on': canStop }"
        :disabled="busy || (!canStart && !canStop) || (canStart && !durationValid)"
        style="margin-top: 10px"
        @click="canStop ? stopCapture() : startCapture()"
      >
        <span class="arm__lamp" />
        {{ captureButtonLabel }}
      </button>

      <p class="idle__sub" style="text-align: left; margin-top: 6px">
        {{ captureHint }}
      </p>
      <p v-if="captureError" class="idle__sub capture__warn" style="text-align: left; margin-top: 6px">
        {{ captureError }}
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
import { receiverStatus, captureExpiry, canStartCapture, canStopCapture } from '~/utils/captureStatus'
import type { ReceiverStatus } from '~/utils/captureStatus'
import type { ReceiverLayout } from '~/utils/receiverLayout'
import {
  buildCaptureStartBody, isValidCaptureDuration, apiError,
  DEFAULT_CAPTURE_DURATION_SEC, MIN_CAPTURE_DURATION_SEC, MAX_CAPTURE_DURATION_SEC,
  CAPTURE_PRESETS, CAPTURE_PRESET_LABELS, CAPTURE_PRESET_TAGS, DEFAULT_CAPTURE_PRESET,
} from '~/utils/listenControl'
import type { CapturePreset } from '~/utils/listenControl'

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
  /**
   * How many radios and voice receivers the capture is built from, derived
   * server-side from the op25 config on disk — or null when that file is
   * missing or unreadable (a fresh checkout where no capture has ever run),
   * in which case the bay shows no layout at all rather than a zeroed one.
   * See utils/receiverLayout.ts for the derivation and
   * server/api/listen/followed.get.ts for why it rides on the same poll as
   * `tracked`/`radioBusy` — the layout can only be captioned honestly
   * alongside them.
   */
  receiverLayout: ReceiverLayout | null
}>()

const emit = defineEmits<{
  toggle: []
  toggleTg: [tgid: number]
  /**
   * Fired after a Start or Stop attempt settles, success or failure. This
   * component only owns the request/response and its own busy/error state —
   * `radioBusy`/`tracked`/`sessionStartedAt`/`sessionDurationSec` are props,
   * read from the SAME `/api/listen/followed` poll `useScannerFeed`
   * (composables/useScannerFeed.ts) already runs every
   * `FOLLOWED_POLL_MS` (20s) for the stall indicator. Without this emit the
   * operator would see their own Start/Stop take effect only on that next
   * poll — up to 20s of the button appearing to have done nothing. The
   * parent owns that composable, so refreshing it is the parent's call to
   * make; this only asks for it.
   */
  refreshCapture: []
}>()

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

/* ---------------------------------------------------------------------------
 * RECEIVER LAYOUT — how many radios and voice streams are in play
 *
 * The counts themselves are derived server-side (utils/receiverLayout.ts,
 * unit-tested); everything below is only phrasing, kept here for the same
 * reason RECEIVER_LINE/RECEIVER_NOTE and `expiryNote` are: the tested file
 * stays free of pluralisation and locale-shaped string work, and the strings
 * stay next to the markup that renders them.
 * ------------------------------------------------------------------------- */

/**
 * "2 radios", "1 radio", "no voice receivers" — never "0 voice receivers".
 * A zero-receiver layout is a real config (make_multirx_cfg.py will happily
 * build devices with no channels bound), and a bare `0` beside two other
 * counts reads as a rendering bug rather than as a statement.
 */
function countOf(n: number, noun: string): string {
  if (n === 0) return `no ${noun}s`
  return `${n} ${noun}${n === 1 ? '' : 's'}`
}

/**
 * "700 MHz" per leg — or the tuned centre to three decimals when two legs
 * land in the same 100 MHz band, where the band label would print the same
 * string twice ("700 MHz: 3 voice · 700 MHz: 7 voice") and distinguish
 * nothing. Two 700 MHz legs are not the shape this system runs today, but the
 * fallback costs one line and the failure it prevents is a display that looks
 * authoritative while being unreadable. `centreHz` is carried by
 * ReceiverLeg for exactly this.
 */
function legLabels(layout: ReceiverLayout): string[] {
  const bands = layout.legs.map(l => l.bandMhz)
  const distinct = new Set(bands).size === bands.length
  return layout.legs.map(l => distinct
    ? `${l.bandMhz} MHz`
    : `${Math.round(l.centreHz / 1e3) / 1000} MHz`)
}

/**
 * The totals: radios, the voice-receiver pool, and the control receiver(s).
 *
 * Voice and control are stated separately rather than as one channel count
 * because only the voice receivers can take a call — the `CC` receiver
 * decodes the trunking control stream and records nothing (see
 * utils/receiverLayout.ts's CONTROL_NAME). An operator reasoning about a
 * missed call needs the pool size, and 11 would be the wrong number.
 *
 * The control clause is dropped entirely when there is none: the generator
 * emits `CC` only for a leg that has a control channel, so "no control
 * channels" is a legal config, and saying so in a totals line nobody reads
 * for that would be noise.
 */
const layoutLine = computed(() => {
  const l = props.receiverLayout
  if (!l) return null
  const bits = [countOf(l.radios, 'radio'), countOf(l.voiceTotal, 'voice receiver')]
  if (l.controlTotal > 0) bits.push(countOf(l.controlTotal, 'control channel'))
  return bits.join(' · ')
})

/**
 * The per-leg split — "700 MHz: 3 voice + 1 control · 800 MHz: 7 voice" —
 * which is the half of the old console's display that the totals cannot
 * replace: the two legs have different receiver counts (3 and 7 today), so a
 * call missed on the 700 MHz leg is missed against a pool of three, not ten.
 *
 * Suppressed for a single-leg capture, where each figure would simply repeat
 * the totals line above it.
 */
const layoutLegLine = computed(() => {
  const l = props.receiverLayout
  if (!l || l.legs.length < 2) return null
  const labels = legLabels(l)
  return l.legs.map((leg, i) => {
    const rx = [`${leg.voice} voice`]
    if (leg.control > 0) rx.push(`${leg.control} control`)
    return `${labels[i]}: ${rx.join(' + ')}`
  }).join(' · ')
})

/**
 * WHERE THE LAYOUT COMES FROM, AND WHETHER IT IS THE RUNNING ONE
 * ---------------------------------------------------------------
 * lwin_both.json is rewritten by every capture start, so while a capture of
 * OURS is up the layout is that capture's by construction. When nothing is
 * running it describes the LAST one — still worth showing (it is what a Start
 * will build again), but presenting it then as if it were live would be
 * precisely the quietly-wrong readout the expiry work above exists to
 * remove. So the caption is keyed on the same `ReceiverStatus` the Receiver
 * line is, and each state says which of those it is.
 *
 * 'onAirOutside' gets the most guarded wording of the four: a capture this
 * console did not start MAY have been launched some other way (a hand-rolled
 * multi_rx invocation, or lwin_listen_multi.sh with a different `-o`), in
 * which case the file on disk is not describing what is on the air at all.
 * That is unlikely rather than impossible, and the honest thing is to name
 * the doubt rather than let the operator assume it away.
 */
const LAYOUT_SOURCE: Record<ReceiverStatus, string> = {
  onAirConsole: 'What this capture was launched with, read from the op25 config on disk.',
  onAirOutside: 'The last layout written to disk. This capture was started elsewhere, so it may be running a different one.',
  stalled: 'What this open session was launched with. op25 is gone, so nothing is receiving on any of it.',
  idle: 'What the last capture was launched with. Nothing is receiving now.',
}

/**
 * Appended to every one of the four, because the operator's next thought on
 * reading a receiver count is reliably "can I change it". They cannot, here:
 * the delegated request does carry `nVoice700`/`nVoice800`
 * (server/utils/processes.ts's buildControlRequest()), but the Capture block
 * below deliberately does not expose them — an operator-rare setting whose
 * safe values were measured, not chosen. Appended in one place rather than
 * written into all four strings so it cannot be lost from one of them.
 */
const NOT_ADJUSTABLE = 'The counts are not adjustable from the console.'

const layoutNote = computed(() => {
  if (!props.receiverLayout) return null
  return `${LAYOUT_SOURCE[status.value]} ${NOT_ADJUSTABLE}`
})

/* ===========================================================================
 * CAPTURE CONTROL — starts and stops the radio, not the audio feed
 *
 * `canStart`/`canStop` are NOT read off `status` above: `receiverStatus()`
 * intentionally collapses "just started, op25 not up yet" into the same
 * `'idle'` display state as genuine idle (see STALL_GRACE_MS), which is
 * right for the Receiver line but wrong for these two — see
 * canStartCapture()/canStopCapture()'s own docstring in
 * utils/captureStatus.ts for why they read `tracked`/`radioBusy` directly.
 * ========================================================================= */

interface ApiResponse<T> { success: boolean, data?: T, error?: string }

const canStart = computed(() => canStartCapture({ tracked: props.tracked, radioBusy: props.radioBusy }))
const canStop = computed(() => canStopCapture({ tracked: props.tracked }))

/**
 * Seconds, not the ISO-ish shape a `<input type=date>` would use — this is
 * exactly the `durationSec` field capture_control.py validates, sent
 * unconverted. `number | string | null` because `v-model.number` runs the
 * raw DOM string through Vue's `looseToNumber` (parseFloat, falling back to
 * the ORIGINAL string on `NaN`) — so clearing the field puts the empty
 * STRING `''` here, not `null` or `0`. See `isValidCaptureDuration`'s own
 * docstring in utils/listenControl.ts; that one function is the only place
 * this shape needs to be reasoned about.
 */
const duration = ref<number | string | null>(DEFAULT_CAPTURE_DURATION_SEC)

/**
 * Which talkgroups the NEXT capture follows.
 *
 * Defaults to `pd`, what every capture ran when this was not selectable —
 * so a fresh page offers the familiar profile and a wider one is always a
 * deliberate act. It is a `<select>` rather than free text because the value
 * is checked against an allowlist three times before it becomes argv (here,
 * `buildControlRequest()`, and `capture_control.py`'s `PRESET_ARGV` lookup);
 * offering a control that could produce a value any of those refuse would be
 * the same mistake this whole block was written to avoid.
 */
const preset = ref<CapturePreset>(DEFAULT_CAPTURE_PRESET)
const ess = ref(false)
const includeEncrypted = ref(false)
const busy = ref(false)
const captureError = ref('')

const durationValid = computed(() => isValidCaptureDuration(duration.value))

/**
 * "86400s · 24h" beside the field. A bare second count is the same trap
 * ListenControl.vue's own `duration` docstring documents two real sessions
 * falling into with 10800 (3h) typed in as "whatever number came to mind" —
 * showing the human-scale equivalent live is what makes the number in the
 * box a considered choice rather than an unchecked one.
 */
const durationHuman = computed(() => {
  const s = duration.value
  if (typeof s !== 'number' || !Number.isFinite(s)) return ''
  const h = s / 3600
  return `${Math.round(h * 100) / 100}h`
})

const captureButtonLabel = computed(() => {
  if (busy.value) return canStop.value ? 'Stopping…' : 'Starting…'
  return canStop.value ? 'Stop capture' : 'Start capture'
})

// Distinct from receiverNote above (which explains the RECEIVER, i.e. what
// is physically happening) — this explains what THIS BUTTON will do, which
// is not always the same sentence: 'onAirOutside' already has a full
// explanation on the Receiver block above, so this one stays short and
// points back at it rather than repeating it. Record, not a switch, for the
// same exhaustiveness reason as RECEIVER_LINE/RECEIVER_NOTE above.
const CAPTURE_HINT: Record<ReceiverStatus, string> = {
  onAirConsole: 'Stop ends the capture this console started, immediately.',
  onAirOutside: 'Already on air from elsewhere — see the receiver note above. This console can’t start or stop it.',
  stalled: 'Stop releases the recorders even though op25 is gone. Start stays refused until then.',
  idle: 'Runs for the duration above, or until Stop is pressed.',
}

/**
 * One override on top of CAPTURE_HINT, for the same reason canStart/canStop
 * are not looked up from `status` at all (see the block comment above): a
 * session that just opened but hasn't granted yet also reads as `'idle'`
 * here, and CAPTURE_HINT's idle line ("Runs for the duration above...")
 * would be actively wrong right when the operator is watching for it
 * hardest — Start is refused (canStart is false) while it claims Start is
 * what happens next, and it says nothing about the Stop button sitting
 * right there, lit. `canStop` is what actually tells this apart from
 * genuine idle (idle has `tracked` false, so canStop is false there too),
 * so branching on it instead of adding a fifth ReceiverStatus member is
 * what keeps this a display-only distinction rather than a new state
 * something else in the bay would also have to learn.
 */
const captureHint = computed(() => {
  if (status.value === 'idle' && canStop.value) {
    return 'Session just opened — op25 hasn’t granted yet. Stop is available if it doesn’t come up shortly.'
  }
  return CAPTURE_HINT[status.value]
})

async function startCapture(): Promise<void> {
  // Belt-and-braces: the button is already disabled for every one of these,
  // but a stray extra call (e.g. a fast double-click landing between one
  // Vue render and the next) must not re-enter a request already in flight.
  // The `typeof` check is what actually narrows `duration.value` to `number`
  // below — `durationValid.value` says the same thing but TS can't follow
  // that link through a separate computed.
  if (!canStart.value || !durationValid.value || busy.value || typeof duration.value !== 'number') return
  busy.value = true
  captureError.value = ''
  try {
    const res = await $fetch<ApiResponse<unknown>>('/api/listen/start', {
      method: 'POST',
      body: buildCaptureStartBody({
        duration: duration.value,
        ess: ess.value,
        includeEncrypted: includeEncrypted.value,
        preset: preset.value,
      }),
    })
    if (!res.success) captureError.value = res.error ?? 'Failed to start'
  } catch (e) {
    // Surfaced verbatim — see utils/listenControl.ts's apiError() for why a
    // bare FetchError.message would hide the control API's own 400/409/502
    // text (e.g. "A listening session is already running").
    captureError.value = apiError(e, 'Failed to start')
  } finally {
    busy.value = false
    emit('refreshCapture')
  }
}

async function stopCapture(): Promise<void> {
  if (!canStop.value || busy.value) return
  busy.value = true
  captureError.value = ''
  try {
    const res = await $fetch<ApiResponse<unknown>>('/api/listen/stop', { method: 'POST' })
    if (!res.success) captureError.value = res.error ?? 'Failed to stop'
  } catch (e) {
    captureError.value = apiError(e, 'Failed to stop')
  } finally {
    busy.value = false
    emit('refreshCapture')
  }
}
</script>
