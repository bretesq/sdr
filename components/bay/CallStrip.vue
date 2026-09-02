<template>
  <!--
    Two explicit branches rather than <component :is="locked ? 'div' : 'button'">.
    Vue resolves a dynamic `is` string against registered components before
    falling back to a native element, and it matches case-insensitively — so
    "button" silently resolved to a globally registered `Button` component and
    every strip rendered as that component instead of a native one.
  -->
  <!-- A locked strip is not playable, so it is not a button. It sits cocked
       out of the rail and stays there. -->
  <div v-if="locked" class="strip strip--locked">
    <div class="strip__head">
      <span class="strip__tg">{{ call.tgid ?? '—' }}</span>
      <span class="strip__alpha">{{ call.alpha ?? 'unlisted talkgroup' }}</span>
      <span class="strip__time">{{ clock }}</span>
      <span class="strip__dur">{{ dur }}</span>
    </div>
    <div class="strip__rule" />
    <p class="strip__body">
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
    <p v-if="body" class="strip__body">
      <span v-if="stock === 'keyed'" class="mark mark--keyed">{{ keyLabel }}</span>
      {{ body }}
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
export type StripCall = FeedCall & { transcript?: string | null }

const props = defineProps<{
  call: StripCall
  heldKeyIds: number[]
  live?: boolean
}>()

defineEmits<{ play: [call: FeedCall] }>()

/** P25 ADP. Anything else in algid is not an encryption algorithm we gate on. */
const ADP_ALGID = 170

const locked = computed(
  () => props.call.algid === ADP_ALGID && !props.heldKeyIds.includes(props.call.keyid ?? -1),
)

const stock = computed(() => {
  if (locked.value) return 'locked'
  if (props.call.algid === ADP_ALGID) return 'keyed'
  return body.value ? 'clear' : 'void'
})

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

/** op25 writes this literal when it silenced a burst; it is not speech. */
const body = computed(() => {
  const t = (props.call.transcript ?? '').trim()
  return t && t !== '[BLANK_AUDIO]' ? t : ''
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
