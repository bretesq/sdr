<template>
  <div class="bay">
    <!-- Mobile only: the stack collapses to one bar; the rail keeps the screen -->
    <div class="bay__bar">
      <button
        class="arm"
        :class="{ 'arm--on': feed.armed.value }"
        :disabled="!feed.armed.value && feed.selected.value.length === 0"
        @click="toggleArm"
      >
        <span class="arm__lamp" />
        {{ feed.armed.value ? 'Stop' : 'Arm' }}
      </button>
      <span class="bay__barstate">{{ feed.selected.value.length }} armed · {{ receiverShort }}</span>
      <button class="bay__sheetbtn" type="button" @click="sheet = !sheet">
        {{ sheet ? 'Close' : 'Talkgroups' }}
      </button>
    </div>

    <BayCommStack
      class="bay__stack"
      :class="{ 'bay__stack--open': sheet }"
      :followed="feed.followed.value"
      :selected="feed.selected.value"
      :armed="feed.armed.value"
      :radio-busy="feed.radioBusy.value"
      :tracked="feed.tracked.value"
      @toggle="toggleArm"
      @toggle-tg="toggleTg"
    />

    <main class="bay__rails" :class="{ 'bay__rails--quiet': liveEmpty }">
      <!-- LIVE RAIL — the playing strip and whatever is queued behind it -->
      <section class="rail bay__live">
        <header class="rail__head">
          <span>Live</span>
          <span v-if="feed.armed.value && feed.streamOk.value" class="mark mark--live">on air</span>
          <span v-if="sttStalled" class="mark mark--stalled" aria-live="polite">{{ sttStalled }}</span>
          <span class="spacer" />
          <span v-if="feed.skipped.value" class="count">{{ feed.skipped.value }} aged out</span>
          <span v-if="feed.failed.value" class="count">{{ feed.failed.value }} failed</span>
        </header>

        <div class="bay__livebody">
          <TransitionGroup name="file" tag="div">
            <BayCallStrip
              v-if="feed.nowPlaying.value"
              :key="`now-${feed.nowPlaying.value.id}`"
              :call="feed.nowPlaying.value"
              :held-key-ids="feed.heldKeyIds.value"
              live
            />
            <BayCallStrip
              v-for="e in feed.entries.value"
              :key="e.call.id"
              :call="e.call"
              :held-key-ids="feed.heldKeyIds.value"
              @play="feed.review"
            />
          </TransitionGroup>

          <p v-if="!feed.nowPlaying.value && !feed.entries.value.length" class="idle">
            {{ feed.armed.value ? 'waiting for traffic' : 'bay not armed' }}
            <span class="idle__sub">{{ liveHint }}</span>
          </p>
        </div>

        <div v-if="feed.nowPlaying.value" class="transport">
          <span class="mark mark--live">playing</span>
          <span>{{ feed.nowPlaying.value.alpha ?? feed.nowPlaying.value.tgid }}</span>
          <div class="transport__bar"><div class="transport__fill" :style="{ transform: `scaleX(${progress})` }" /></div>
          <span>{{ feed.nowPlaying.value.dur.toFixed(1) }}s</span>
        </div>
      </section>

      <!-- FILED RAIL — the same strips, after they have been heard -->
      <section class="rail bay__filed">
        <header class="rail__head">
          <span>Filed</span>
          <span class="count">{{ archive.total.value.toLocaleString() }} calls</span>
          <span class="spacer" />
          <input
            v-model="archive.search.value"
            class="field bay__search"
            type="search"
            placeholder="search transcripts, talkgroups, ten-codes"
            aria-label="Search filed calls"
          >
        </header>

        <div class="bay__filedbody">
          <BayCallStrip
            v-for="c in filed"
            :key="c.id"
            :call="c"
            :held-key-ids="feed.heldKeyIds.value"
            @play="feed.review"
          />
          <p v-if="!filed.length" class="idle">
            {{ archive.loading.value ? 'reading' : 'nothing filed' }}
            <span class="idle__sub">{{ archive.search.value ? 'No filed call matches that.' : 'Calls file here once they have been recorded.' }}</span>
          </p>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'

useHead({ title: 'Strip Bay — LWIN P25' })

const feed = useScannerFeed()
const archive = useArchive()
const sheet = ref(false)

/** Strips the live rail is already holding must not also appear filed. */
const liveIds = computed(() => {
  const s = new Set<number>()
  if (feed.nowPlaying.value) s.add(feed.nowPlaying.value.id)
  for (const e of feed.entries.value) s.add(e.call.id)
  return s
})

const filed = computed(() => archive.rows.value.filter(r => !liveIds.value.has(r.id)))

/* An idle bay is the normal state, not a failure — but a live rail holding
   38% of the screen to say "nothing yet" is dead space. When it has no strips
   it collapses to its header and gives the room to the filed rail. */
const liveEmpty = computed(() => !feed.nowPlaying.value && feed.entries.value.length === 0)

const receiverShort = computed(() => (feed.radioBusy.value ? 'on air' : 'idle'))

interface ApiResponse<T> { success: boolean, data?: T, error?: string }

/** Mirrors the `data` shape of GET /api/transcribe/status. */
interface TranscribeStatus {
  running: boolean
  reachable: boolean
  state: 'idle' | 'healthy' | 'degraded'
  awaiting: number
  oldestAwaitingSec: number | null
}

const sttStatus = ref<TranscribeStatus | null>(null)

/**
 * Lit only for `state === 'degraded'` — the 26-hour-wedge signature this
 * indicator exists to surface. `healthy` and `idle` render nothing on
 * purpose: an indicator that is always on is furniture the operator learns
 * to ignore, and `idle` (quiet air, nobody keyed a mic) reads identically to
 * a stalled pipeline unless it stays silent. See transcriptionHealth() in
 * server/utils/queries.ts for what actually distinguishes the three states.
 */
const sttStalled = computed(() => {
  const s = sttStatus.value
  if (!s || s.state !== 'degraded') return null
  const oldest = s.oldestAwaitingSec === null ? '?' : `${Math.round(s.oldestAwaitingSec / 60)}m`
  return `transcription stalled · ${s.awaiting} waiting, oldest ${oldest}`
})

async function refreshSttStatus(): Promise<void> {
  try {
    const res = await $fetch<ApiResponse<TranscribeStatus>>('/api/transcribe/status')
    if (res.success && res.data) sttStatus.value = res.data
  } catch {
    // Leave the last known reading rather than clearing a real indicator
    // because of one dropped poll.
  }
}

const liveHint = computed(() => {
  if (!feed.armed.value) return 'Tick talkgroups, then arm the bay.'
  if (!feed.radioBusy.value) return 'No capture is running, so nothing new will land.'
  return 'Armed and listening. Strips appear as calls end.'
})

/* The transport fill is a clock, not a real seek position: the element is owned
   by the feed and a filed strip may be reviewed mid-queue. It reads as elapsed
   against the clip's known duration, which is what the operator needs. */
const elapsed = ref(0)
let ticker: ReturnType<typeof setInterval> | null = null
const progress = computed(() => {
  const c = feed.nowPlaying.value
  if (!c || !c.dur) return 0
  return Math.min(1, elapsed.value / c.dur)
})

function toggleArm(): void {
  if (feed.armed.value) feed.disarm()
  else void feed.arm()
}

function toggleTg(tgid: number): void {
  const i = feed.selected.value.indexOf(tgid)
  if (i === -1) feed.selected.value.push(tgid)
  else feed.selected.value.splice(i, 1)
}

// 10s, matching RecordingsList's transcriber poll — this is the same fact,
// read on a different page, and there is no reason for it to move faster.
let sttTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  void feed.load()
  void archive.load()
  archive.watchCorpus()
  ticker = setInterval(() => {
    elapsed.value = feed.nowPlaying.value ? elapsed.value + 0.25 : 0
  }, 250)
  void refreshSttStatus()
  sttTimer = setInterval(() => { void refreshSttStatus() }, 10_000)
})
onUnmounted(() => {
  if (ticker) clearInterval(ticker)
  if (sttTimer) clearInterval(sttTimer)
})
</script>

<style>
/* Layout is part of this world: the bay is a board with a stack bolted to its
   left edge and two rails filling the rest. Nothing floats. */
.bay {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  height: 100dvh;
}

.bay__bar { display: none; }

.bay__stack { grid-column: 1; min-height: 0; }

.bay__rails {
  grid-column: 2;
  display: grid;
  grid-template-rows: minmax(140px, 38%) minmax(0, 1fr);
  min-height: 0;
  transition: grid-template-rows 260ms var(--ease-file);
}

/* Nothing live: the rail keeps its head and gives the room to the archive. */
.bay__rails--quiet { grid-template-rows: auto minmax(0, 1fr); }
.bay__rails--quiet .bay__livebody { overflow: hidden; }
/* one line does not need a 28px cushion above and below it */
.bay__rails--quiet .idle { padding: 12px 16px 14px; }

.bay__live, .bay__filed {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  min-height: 0;
}

.bay__livebody, .bay__filedbody {
  position: relative;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.bay__search {
  width: min(360px, 42vw);
  padding: 3px 8px;
  font-size: 12px;
}

/* --- one dominant field on phones: the rail keeps the screen -------------- */
@media (max-width: 860px) {
  .bay {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: auto minmax(0, 1fr);
  }

  .bay__bar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 10px;
    background: var(--rail);
    border-bottom: 1px solid var(--rail-shadow);
    box-shadow: inset 0 1px 0 var(--rail-lip);
  }
  .bay__bar .arm { width: auto; flex: none; padding: 7px 14px; }

  .bay__barstate {
    flex: 1;
    font-family: var(--f-data);
    font-size: 12px;
    color: #b6bda3;
    font-variant-numeric: tabular-nums;
  }

  .bay__sheetbtn {
    padding: 7px 12px;
    border: 1px solid var(--board-edge);
    background: var(--board-deep);
    color: #cdd2bb;
    font-family: var(--f-label);
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    cursor: pointer;
  }

  /* the standby list is a sheet over the bay, not a squeezed column */
  .bay__stack {
    position: fixed;
    inset: 0;
    z-index: 20;
    display: none;
    border-right: 0;
  }
  .bay__stack--open { display: flex; }

  .bay__rails {
    grid-column: 1;
    /* the playing strip sits in the thumb zone: live rail anchored low */
    grid-template-rows: minmax(0, 1fr) auto;
    grid-template-areas: 'filed' 'live';
  }
  .bay__filed { grid-area: filed; }
  .bay__live {
    grid-area: live;
    max-height: 46dvh;
    border-top: 2px solid var(--rail-lip);
  }

  .bay__search { width: 100%; }
  .rail__head { flex-wrap: wrap; }
}
</style>
