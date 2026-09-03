<template>
  <!--
    Two explicit branches rather than <component :is="locked ? 'div' : 'button'">.
    Vue resolves a dynamic `is` string against registered components before
    falling back to a native element, and it matches case-insensitively — so
    "button" silently resolved to a globally registered `Button` component and
    every strip rendered as that component instead of a native one.
  -->
  <!-- A locked strip is not playable, so it is not a button. It sits cocked
       out of the rail and stays there.

       Both encrypted-and-undecodable states print here: ADP under a key we do
       not hold, and a call encrypted under an algorithm this console does not
       implement at all. They share one stock because the operator-facing fact
       is identical — recorded, cannot be played — and differ only in the mark
       and the sentence, which say which of the two it is rather than letting
       the unhandled case borrow ADP's wording. -->
  <div v-if="stock === 'locked'" class="strip strip--locked">
    <div class="strip__head">
      <span class="strip__tg">{{ call.tgid ?? '—' }}</span>
      <span class="strip__alpha">{{ call.alpha ?? 'unlisted talkgroup' }}</span>
      <span class="strip__time">{{ clock }}</span>
      <span class="strip__dur">{{ dur }}</span>
    </div>
    <div class="strip__rule" />
    <p v-if="encryption === 'unhandled'" class="strip__body">
      <span class="mark mark--locked">algorithm not handled</span>
      — encrypted under algid {{ algLabel }}, which this console cannot decode.
      Recorded, not decoded.
    </p>
    <p v-else class="strip__body">
      <span class="mark mark--locked">no key held</span>
      — encrypted under key {{ keyLabel }}. Recorded, not decoded.
    </p>
  </div>

  <button
    v-else
    type="button"
    class="strip"
    :class="[`strip--${stock}`, { 'strip--live': live }]"
    :aria-current="live ? 'true' : undefined"
    :aria-label="`Play ${call.alpha ?? 'talkgroup ' + call.tgid}, ${dur}`"
    @click="$emit('play', call)"
  >
    <div class="strip__head">
      <span class="strip__tg">{{ call.tgid ?? '—' }}</span>
      <span class="strip__alpha">{{ call.alpha ?? 'unlisted talkgroup' }}</span>
      <span class="strip__time">{{ clock }}</span>
      <span class="strip__dur">{{ dur }}</span>
    </div>
    <div class="strip__rule" />
    <!--
      The transcript, with any ten-codes the extractor found marked in place.

      `codeSegments` falls back to one plain segment whenever there are no
      codes, so the common strip renders exactly as it always has — v-for over a
      single span, not a second template branch to keep in step with this one.

      v-for, never v-html: the segments carry operator speech off the air, and
      the offsets come from a Python extractor. Rendering them as markup would
      be an injection surface for whatever whisper transcribed.
    -->
    <p v-if="body" class="strip__body">
      <span v-if="stock === 'keyed'" class="mark mark--keyed">{{ keyLabel }}</span><!--
      --><template v-for="(seg, i) in codeSegments" :key="i"><span
        v-if="seg.code"
        class="strip__code"
        :title="seg.code.meaning ?? seg.code.canonical"
      >{{ seg.text }}</span><template v-else>{{ seg.text }}</template></template>
    </p>
    <p v-else class="strip__body strip__body--empty">
      <span v-if="stock === 'keyed'" class="mark mark--keyed">{{ keyLabel }}</span>
      {{ voidReason }}
    </p>
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { FeedCall } from '~/utils/scannerQueue'
import { encryptionState, stripStock } from '~/utils/callEncryption'
import { segments } from '~/utils/tencodeSegments'
import type { CodeMention } from '~/utils/tencodeSegments'

/**
 * One call, as one printed strip.
 *
 * Every strip carries the identical grid — talkgroup, name, clock, duration,
 * then the transcript printed on the strip itself. That sameness is the point:
 * a rail of sixty strips is meant to be readable down its left edge without
 * reading any single one of them.
 *
 * Stock tint carries the call's CLASS, never its state. Where a strip sits in
 * the bay carries its state. This is how a real strip bay works and it is why
 * nothing here needs a status badge.
 */

/**
 * A strip renders anything the bay can hold, live or filed. Both rails carry
 * the same object: a call the feed could play, optionally carrying the
 * transcript printed on its face.
 */
export type StripCall = FeedCall & {
  transcript?: string | null
  /**
   * The text the code offsets index, and therefore the text a marked strip
   * renders.
   *
   * `transcriptNorm` rather than `transcript` because scripts/tencodes.py
   * produced the offsets against the normalised form; see tencodeSegments.ts's
   * own note on why that indexing has to line up. Measured on this corpus the
   * two are byte-identical for 99.10% of transcripts, and the 124 that differ
   * differ only in the case of the code itself ("Code 4" -> "code 4") — so
   * rendering the normalised form costs nothing a reader would notice, while
   * slicing the raw form with these offsets would eventually mark the wrong
   * span.
   */
  transcriptNorm?: string | null
  codes?: CodeMention[]
}

const props = defineProps<{
  call: StripCall
  heldKeyIds: number[]
  live?: boolean
}>()

defineEmits<{ play: [call: FeedCall] }>()

/**
 * Which of the five encryption states this call is in, and which of the four
 * stocks that prints on. Both live in utils/callEncryption.ts — see that file
 * for why a non-ADP algid is no longer allowed to fall through to clear stock,
 * why the eight one-off algids share a single 'unhandled' case, and why a null
 * algid stays on the stock it has always had instead of gaining a badge.
 *
 * The scanner queue's classify() reads the SAME function, so a strip can never
 * say "recorded, not decoded" about a call the feed is busy playing.
 */
const encryption = computed(() =>
  encryptionState(props.call, new Set(props.heldKeyIds)),
)

const stock = computed(() => stripStock(encryption.value, Boolean(body.value)))

/**
 * `?? 0` would be a lie rather than a default: a call really can be encrypted
 * with no key id in its ESS, and printing 0x0 asserts a specific valid key that
 * was never on the air.
 */
const keyLabel = computed(() =>
  props.call.keyid === null || props.call.keyid === undefined
    ? 'unknown key'
    : `0x${props.call.keyid.toString(16).toUpperCase()}`,
)

/**
 * The raw algid byte, printed rather than translated into an algorithm name.
 *
 * Every value that reaches this label occurs exactly once in a corpus of
 * 11,743 calls — 0x08, 0x0E, 0x45, 0x48, 0x82, 0xA8, 0xAB, 0xB8 — which is
 * what an ESS bit error looks like, not what eight algorithms in daily use
 * look like (0xA8 and 0xAB are each one bit off ADP's 0xAA). Printing the byte
 * lets an operator recognise that; printing "AES-256" would invent a fact.
 */
const algLabel = computed(() =>
  props.call.algid === null || props.call.algid === undefined
    ? 'unknown'
    : `0x${props.call.algid.toString(16).toUpperCase().padStart(2, '0')}`,
)

/** op25 writes this literal when it silenced a burst; it is not speech. */
const body = computed(() => {
  const t = (props.call.transcript ?? '').trim()
  return t && t !== '[BLANK_AUDIO]' ? t : ''
})

/**
 * The transcript split into plain runs and ten-code runs.
 *
 * Only 8% of calls carry a code, so this collapses to a single plain segment
 * for the great majority and the strip reads exactly as before. That is the
 * point: a mark that appeared on every strip would be furniture, and this rail
 * is meant to be scannable down its left edge.
 *
 * The offsets index the NORMALISED transcript, so that is what gets sliced. If
 * a call arrives without it — the live feed's FeedCall does not carry codes —
 * `segments()` receives no codes and returns the body unmarked, which is the
 * honest degradation: no claim is made rather than a wrong span highlighted.
 */
const codeSegments = computed(() => {
  const codes = props.call.codes ?? []
  const text = codes.length ? (props.call.transcriptNorm ?? body.value) : body.value
  return segments(text, codes)
})

const voidReason = computed(() =>
  props.call.transcript?.trim() === '[BLANK_AUDIO]'
    ? 'silence — nothing was said'
    : 'not transcribed yet',
)

const clock = computed(() => {
  const d = new Date(props.call.start * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
})

const dur = computed(() => `${props.call.dur.toFixed(1)}s`)
</script>
