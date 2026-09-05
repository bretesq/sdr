<template>
  <div class="bay">
    <!-- Mobile only: the stack collapses to one bar; the rail keeps the screen -->
    <div class="bay__bar">
      <button
        class="arm"
        :class="{ 'arm--on': feed.armed.value }"
        :disabled="!feed.armed.value && !feed.listenAll.value && feed.selected.value.length === 0"
        @click="toggleArm"
      >
        <span class="arm__lamp" />
        {{ feed.armed.value ? 'Stop' : 'Listen' }}
      </button>
      <span class="bay__barstate">{{ selectionLabel }} · {{ receiverShort }}</span>
      <button class="bay__sheetbtn" type="button" @click="sheet = !sheet">
        {{ sheet ? 'Close' : 'Talkgroups' }}
      </button>
    </div>

    <BayCommStack
      class="bay__stack"
      :class="{ 'bay__stack--open': sheet }"
      :followed="feed.followed.value"
      :selected="feed.selected.value"
      :listen-all="feed.listenAll.value"
      :armed="feed.armed.value"
      :radio-busy="feed.radioBusy.value"
      :tracked="feed.tracked.value"
      :session-started-at="feed.sessionStartedAt.value"
      :session-duration-sec="feed.sessionDurationSec.value"
      :receiver-layout="feed.receiverLayout.value"
      @toggle="toggleArm"
      @toggle-tg="toggleTg"
      @hear-everything="hearEverything"
      @refresh-capture="refreshCapture"
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
            <!--
              Only a clip the FEED pulled off the queue gets a strip here. A
              reviewed one is already on screen in the filed rail, so printing
              it again above would duplicate the strip the operator just
              clicked — and the rail growing to hold it is what shoved the page
              down under their cursor.
            -->
            <BayCallStrip
              v-if="feed.nowPlaying.value && !feed.reviewing.value"
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

          <!--
            The idle notice stays put while a reviewed clip plays. The transport
            below it carries the waveform and the clock, which is the whole of
            what reviewing needs to show — the rail has no arrivals to report,
            and saying "bay not armed" is still true.
          -->
          <p
            v-if="(!feed.nowPlaying.value || feed.reviewing.value) && !feed.entries.value.length"
            class="idle"
          >
            {{ feed.armed.value ? 'waiting for traffic' : 'bay not armed' }}
            <span class="idle__sub">{{ liveHint }}</span>
          </p>
        </div>

        <div v-if="feed.nowPlaying.value" class="transport">
          <span class="mark mark--live">playing</span>
          <span>{{ feed.nowPlaying.value.alpha ?? feed.nowPlaying.value.tgid }}</span>
          <!--
            The clip's own shape, in place of the flat fill that was here.

            Keyed by file so switching clips remounts rather than animating one
            envelope into another — the strip being reviewed changes wholesale,
            and a canvas holding the previous clip's peaks for a frame would
            show the wrong audio under the playhead.
          -->
          <BayWaveform
            :key="feed.nowPlaying.value.file"
            class="transport__wave"
            :file="feed.nowPlaying.value.file"
            :progress="progress"
          />
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
          <!--
            Filed by what the RADIO sent (c.algid), never by the talkgroup
            roster's `enc` label — that label is scraped and wrong often enough
            to mislead: 24-PPD DISP is listed clear and ran 62 ADP calls of 63.

            "Open" reads as NOT KNOWN TO BE ENCRYPTED, which is why it is
            labelled that way in the title rather than "clear". 77% of this
            corpus carries no algid because no ESS was captured, and 93% of
            those transcribed to real speech — so counting them as anything but
            open would hide most of the audible traffic behind a filter.
          -->
          <select
            v-model="archive.encState.value"
            class="field bay__encfilter"
            aria-label="Filter filed calls by encryption"
            title="Open = not known to be encrypted. Most calls carry no encryption field at all; nearly all of those were audible."
          >
            <option value="all">all calls</option>
            <option value="open">open</option>
            <option value="encrypted">encrypted</option>
          </select>
        </header>

        <div ref="filedScroller" class="bay__filedbody">
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
          <!--
            Sentinel: scrolling it into view asks the archive for another page.
            The rail held exactly one page of 120 until now, so the archive
            simply ended there with no indication that 13,000 more existed.
          -->
          <div v-if="filed.length" ref="filedSentinel" class="filed__more">
            <span v-if="archive.loading.value">reading more</span>
            <span v-else-if="!archive.hasMore.value">end of the archive</span>
          </div>
        </div>
      </section>

      <!--
        Third rail, folded shut. `auto` in the grid means a closed fold costs
        only its own head, so the live and filed rails keep the space they had
        before this existed.
      -->
      <BayDataRail
        :rows="packets.rows.value"
        :summary="packets.summary.value"
        :loaded="packets.loaded.value"
        :error="packets.error.value"
        :loading="packets.loading.value"
        :has-more="packets.hasMore.value"
        @load-more="packets.loadMore"
      />
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { receiverStatus } from '~/utils/captureStatus'
import { usePacketFeed } from '~/composables/usePacketFeed'
import type { ReceiverStatus } from '~/utils/captureStatus'

useHead({ title: 'Strip Bay — LWIN P25' })

const feed = useScannerFeed()
const archive = useArchive()
const sheet = ref(false)

/**
 * Strips the live rail is already holding must not also appear filed.
 *
 * A REVIEWED clip is the exception, and it matters: it is playing *because* the
 * operator clicked it in the filed rail. Hiding it there would delete the strip
 * out from under the cursor that just pressed it and shuffle every strip below
 * up one — the same page-moves-while-you-look-at-it problem as the rail
 * expanding, arriving from the other direction.
 */
const liveIds = computed(() => {
  const s = new Set<number>()
  if (feed.nowPlaying.value && !feed.reviewing.value) s.add(feed.nowPlaying.value.id)
  for (const e of feed.entries.value) s.add(e.call.id)
  return s
})

const filed = computed(() => archive.rows.value.filter(r => !liveIds.value.has(r.id)))

/* An idle bay is the normal state, not a failure — but a live rail holding
   38% of the screen to say "nothing yet" is dead space. When it has no strips
   it collapses to its header and gives the room to the filed rail.

   A REVIEWED clip does not count as a strip. Playing one used to flip this
   false, so the rail expanded from its collapsed height to 38% — the whole
   page shifting under the operator's cursor at the exact moment they clicked
   something, which is the worst possible time to move it. Reviewing shows in
   the transport instead: the shape, the clock, the talkgroup. Nothing arrived,
   so nothing needs room. */
const liveEmpty = computed(() =>
  (!feed.nowPlaying.value || feed.reviewing.value) && feed.entries.value.length === 0,
)

/**
 * The mobile bar's compact status. Shares receiverStatus() with BayCommStack
 * so the same "session open, no radio" reading does not silently say "idle"
 * here just because this line is a separate, shorter render of the same fact.
 * Record, not a switch, for the same exhaustiveness reason as CommStack.vue's
 * RECEIVER_LINE.
 */
const RECEIVER_SHORT: Record<ReceiverStatus, string> = {
  onAirConsole: 'on air',
  onAirOutside: 'on air',
  stalled: 'stalled',
  idle: 'idle',
}

// Read-only background feed; its poll starts and stops with the page.
const packets = usePacketFeed()

// The filed rail's own infinite scroll. `root` is the scrolling div, not the
// viewport -- see useInfiniteScroll.
const { sentinel: filedSentinel, root: filedScroller }
  = useInfiniteScroll(() => archive.loadMore())

const receiverShort = computed(() => RECEIVER_SHORT[receiverStatus({
  radioBusy: feed.radioBusy.value,
  tracked: feed.tracked.value,
  sessionStartedAt: feed.sessionStartedAt.value,
  nowMs: Date.now(),
})])

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

/**
 * What the bay is listening to, in words rather than a count.
 *
 * "0 selected" was the old reading for the default state, which described the
 * tick boxes accurately and the behaviour exactly backwards -- nothing ticked
 * is when the bay plays the MOST.
 */
const selectionLabel = computed(() =>
  feed.listenAll.value
    ? 'all talkgroups'
    : `${feed.selected.value.length} selected`)

const liveHint = computed(() => {
  if (!feed.armed.value) {
    return feed.listenAll.value
      ? 'Press Listen live to hear every call as it lands.'
      : 'Tick talkgroups, then press Listen live.'
  }
  if (feed.radioBusy.value) return 'Listening. Strips appear as calls end.'
  if (feed.tracked.value) return 'A session is open but nothing is receiving. Nothing new will land until it does.'
  return 'No capture is running, so nothing new will land.'
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
  // Ticking a talkgroup is how the filter gets turned ON. Without this the
  // bay stays unfiltered and the tick boxes do nothing at all.
  //
  // Only in this direction. Unticking back down to zero leaves the filter on
  // and the bay silent, which is a state the operator can see and undo with
  // "hear everything" -- whereas silently reopening to all 224 would put the
  // whole system on the speaker because they cleared their last selection.
  if (feed.selected.value.length > 0) feed.listenAll.value = false
}

/** Drop the talkgroup filter and go back to playing the system. */
function hearEverything(): void {
  feed.selected.value.splice(0, feed.selected.value.length)
  feed.listenAll.value = true
}

/**
 * BayCommStack's capture Start/Stop settled (either way) and asked for a
 * fresh read rather than waiting for useScannerFeed's own 20s poll
 * (FOLLOWED_POLL_MS) — otherwise the operator's own click could sit for up
 * to 20s before the Receiver line or the Capture control's Start/Stop
 * availability caught up with what they just did.
 */
function refreshCapture(): void {
  void feed.load()
}

// 10s. Whether the STT server answers changes on the scale of a container
// restart, and this is the only poller of it left, so there is no reason for
// it to move faster — see server/utils/transcriber.ts's isSttServerRunning()
// for why that probe is an HTTP GET rather than `docker ps`.
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
  grid-template-rows: minmax(140px, 38%) minmax(0, 1fr) auto;
  min-height: 0;
  transition: grid-template-rows 260ms var(--ease-file);
}

/* Nothing live: the rail keeps its head and gives the room to the archive. */
.bay__rails--quiet { grid-template-rows: auto minmax(0, 1fr) auto; }
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

/* Sits beside the search field and reads as its sibling, not as a control of
   its own — same .field stock, same 12px, just narrower. */
.bay__encfilter {
  margin-left: 8px;
  padding: 3px 6px;
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
