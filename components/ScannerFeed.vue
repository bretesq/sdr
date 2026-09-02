<template>
  <div class="card p-3 border-round surface-card">
    <div class="flex align-items-center justify-content-between mb-3">
      <h2 class="text-xl font-semibold m-0">Scanner Feed</h2>
      <div class="flex align-items-center gap-2 text-sm">
        <Tag v-if="armed && streamOk" severity="success" value="live" />
        <Tag v-else-if="armed" severity="warn" value="reconnecting" />
        <span class="text-color-secondary">{{ sessionLabel }}</span>
      </div>
    </div>

    <Message v-if="error" severity="error" :closable="false" class="mb-2">
      {{ error }}
    </Message>

    <div class="flex gap-2 align-items-end mb-3">
      <div class="flex-1">
        <label for="feed-tgs" class="block mb-1 text-sm text-color-secondary">
          Talkgroups ({{ followed.length }} followed, {{ activeCount }} active)
        </label>
        <MultiSelect
          id="feed-tgs"
          v-model="selected"
          :options="followed"
          option-label="label"
          option-value="tgid"
          filter
          display="chip"
          placeholder="Select talkgroups"
          class="w-full"
        />
      </div>
      <div style="width: 9rem">
        <label for="feed-stale" class="block mb-1 text-sm text-color-secondary">
          Drop after
        </label>
        <InputNumber
          id="feed-stale"
          v-model="stalenessSec"
          :min="10"
          :max="300"
          suffix=" s"
          show-buttons
          class="w-full"
        />
        <small v-if="!settingPersists" class="text-color-secondary">
          won't persist in this browser
        </small>
      </div>
    </div>

    <div class="flex align-items-center gap-3 mb-3">
      <Button
        :label="armed ? 'Stop' : 'Play'"
        :icon="armed ? 'pi pi-stop' : 'pi pi-play'"
        :disabled="!armed && selected.length === 0"
        @click="armed ? disarm() : arm()"
      />
      <div v-if="nowPlaying" class="flex-1">
        <div class="font-medium">
          {{ nowPlaying.alpha ?? `TG ${nowPlaying.tgid}` }}
        </div>
        <div class="text-sm text-color-secondary">
          {{ nowPlaying.dur.toFixed(1) }}s · {{ behindLive }}s behind live
        </div>
      </div>
      <div v-else-if="armed" class="flex-1 text-color-secondary">
        waiting for traffic…
      </div>
      <Tag v-if="skipped > 0" severity="secondary" :value="`${skipped} skipped`" />
      <!-- Distinct from `skipped` on purpose. `skipped` means the staleness
           bound dropped a call before it could play, so a rising count says
           the bound is too tight. `failed` means playback itself broke on a
           clip the feed had already chosen. Folding them together would hide
           which of the two is actually happening. -->
      <Tag v-if="failed > 0" severity="warn" :value="`${failed} failed`" />
    </div>

    <div v-if="armed" class="border-top-1 surface-border pt-2">
      <div v-if="entries.length === 0" class="text-sm text-color-secondary">
        Queue empty.
      </div>
      <div
        v-for="e in entries"
        :key="e.call.id"
        class="flex align-items-center gap-2 py-1 text-sm"
        :class="e.kind === 'locked' ? 'text-color-secondary' : ''"
      >
        <i :class="e.kind === 'locked' ? 'pi pi-lock' : 'pi pi-volume-up'" />
        <span class="font-medium">
          {{ e.call.alpha ?? `TG ${e.call.tgid}` }}
        </span>
        <span>{{ e.call.dur.toFixed(1) }}s</span>
        <span v-if="e.kind === 'locked'">
          <!-- `?? 0` here would be a lie, not a default. classify() locks a
               call whenever algid is ADP and the keyid is not one we hold —
               and a NULL keyid satisfies that, so this row really can render
               with no key id at all. Printing 0x0 would assert a specific,
               valid key id that was never observed, and this row feeds crack
               targeting directly. Say unknown when it is unknown. -->
          keyid {{ e.call.keyid === null ? 'unknown'
                 : '0x' + e.call.keyid.toString(16).toUpperCase() }} ·
          no key held · crack target
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'

const feed = useScannerFeed()
const {
  selected, armed, stalenessSec, settingPersists, entries, skipped, failed,
  nowPlaying, streamOk, error, arm, disarm,
} = feed

// MultiSelect needs a flat label; keeping the activity count in it makes the
// server's ranking legible in the dropdown. Only about 15 of the 100 followed
// talkgroups are ever active, so the count is what makes the list usable.
const followed = computed(() =>
  feed.followed.value.map(t => ({
    ...t,
    label: `${t.tgid} · ${t.alpha ?? 'unknown'} (${t.recentCalls})`,
  })),
)

const activeCount = computed(
  () => feed.followed.value.filter(t => t.recentCalls > 0).length,
)

/**
 * Honest reporting of what the radio is doing.
 *
 * A session started from a shell rather than from the console reads
 * tracked:false with radioBusy:true, and the feed works fine in that state —
 * it depends on the whitelist file and sdr.db, not on sessionStore. Showing
 * only "no session" would make a working feed look dead.
 */
const sessionLabel = computed(() => {
  if (feed.tracked.value) return 'console session'
  if (feed.radioBusy.value) return 'radio busy · untracked session'
  return 'radio idle'
})

/**
 * A ticking clock, because Date.now() is not reactive.
 *
 * Reading Date.now() straight from a computed recomputes only when its other
 * dependencies change — so "behind live" froze at whatever it was when the
 * clip started and sat there, which is most visibly wrong on a long
 * transmission. This drives it instead, and only while armed, so an idle panel
 * costs nothing.
 */
const nowMs = ref(Date.now())
let tick: ReturnType<typeof setInterval> | null = null
watch(armed, (on) => {
  if (tick) { clearInterval(tick); tick = null }
  if (on) tick = setInterval(() => { nowMs.value = Date.now() }, 1000)
}, { immediate: true })
onUnmounted(() => { if (tick) clearInterval(tick) })

const behindLive = computed(() => {
  const c = nowPlaying.value
  if (!c) return '0'
  const ended = (c.endedAt ?? c.start + c.dur) * 1000
  return Math.max(0, (nowMs.value - ended) / 1000).toFixed(0)
})

onMounted(feed.load)
</script>
