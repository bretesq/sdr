<template>
  <!--
    A fold, not a third full rail. The bay's vertical space is already
    contested between the live and filed rails, and closed this costs only its
    own head — so the layout the console already had is untouched until
    someone asks for this.

    The head IS the toggle. It carries the counts either way, so a closed rail
    still answers "is there data, and from how many radios" without opening.
  -->
  <section class="rail bay__data">
    <button
      class="rail__head data__crease"
      type="button"
      :aria-expanded="open"
      aria-controls="data-strips"
      @click="open = !open"
    >
      <span>Data</span>
      <!--
        Two counts, never one derived figure. `radios` beside `heard on voice`
        is the point of the rail: the data plane sees far more radios than the
        voice plane does. A single "data-only" number would read as a fleet of
        data-only devices, and it is not — source addresses are captured on a
        minority of voice grants, so part of the gap is our own blind spot.
      -->
      <span class="count">{{ totalLabel }} messages</span>
      <span class="count">{{ radiosLabel }} radios, {{ voiceLabel }} heard on voice</span>
      <span v-if="error" class="count data__stale">refresh failed</span>
      <span class="fold__box" :class="{ 'fold__box--open': open }" aria-hidden="true" />
    </button>

    <div v-show="open" id="data-strips" class="bay__databody">
      <p v-if="!loaded" class="data__note">Reading the data channel.</p>

      <!--
        An empty rail points at the reason rather than shrugging. The capture
        can be running perfectly and show nothing: data is bursty, and a data
        receiver holds a granted channel for three seconds at a time.
      -->
      <p v-else-if="!rows.length" class="data__note">
        No packet data yet. The receivers follow data grants as the system
        issues them, so this fills in as it talks to radios.
      </p>

      <article
        v-for="p in rows"
        v-else
        :key="p.id"
        class="strip"
        :class="`strip--${stockFor(p)}`"
      >
        <div class="strip__head">
          <span class="strip__tg">{{ radioLabel(p) }}</span>
          <span class="strip__alpha">{{ headline(p) }}</span>
          <span class="strip__time">{{ clock(p.ts) }}</span>
        </div>
        <div class="strip__rule" />
        <p class="strip__body">
          <!--
            Green is "heard, filed clean" everywhere else in the bay, so it
            means the same here: this radio has also been heard speaking. Its
            absence is the notable case and needs no mark of its own — marking
            both would make the common case shout.
          -->
          <span v-if="p.heardOnVoice" class="mark mark--keyed">also on voice</span>
          {{ sentence(p) }}
        </p>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { Packet, PacketSummary } from '~/server/utils/queries'
import {
  packetStock, radioLabel as radioLabelOf, packetHeadline, packetSentence,
  packetClock,
} from '~/utils/packetMessages'

const props = defineProps<{
  rows: Packet[]
  summary: PacketSummary | null
  loaded: boolean
  error: string | null
}>()

/** Closed by default: this is background traffic, not the operator's task. */
const open = ref(false)

const totalLabel = computed(() =>
  props.summary ? props.summary.total.toLocaleString() : '—')
const radiosLabel = computed(() =>
  props.summary ? props.summary.radios.toLocaleString() : '—')
const voiceLabel = computed(() =>
  props.summary ? props.summary.radiosAlsoOnVoice.toLocaleString() : '—')

/* Message wording and stock live in utils/packetMessages.ts so they can be
   tested: both carry claims that have to stay true. Re-exported here under the
   template's names. */
const stockFor = packetStock
const radioLabel = radioLabelOf
const headline = packetHeadline
const sentence = packetSentence
const clock = packetClock
</script>
