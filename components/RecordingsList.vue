<template>
  <section class="p-4 border-round surface-card">
    <div class="flex align-items-center justify-content-between mb-3">
      <h2 class="text-xl font-bold m-0">Recordings</h2>
      <div class="flex align-items-center gap-2">
        <!--
          Frozen rather than paused: the SSE connection stays open either way,
          so unfreezing shows the current state immediately. This exists because
          the table is a VIRTUAL SCROLLER, not a paginator — a reload replaces
          the rows and jumps to the top, which yanks the operator away from
          whatever they were reading. Live is the default because watching calls
          land is the point.
        -->
        <Button
          v-if="pending > 0 && !live"
          :label="`${pending} new`" icon="pi pi-arrow-down" size="small" text
          :aria-label="`Load ${pending} new recordings`" @click="load"
        />
        <span v-if="live" class="text-xs text-color-secondary" aria-live="polite">
          {{ streamOk ? 'live' : 'live (reconnecting)' }}
        </span>
        <Button
          :icon="live ? 'pi pi-pause' : 'pi pi-play'" text rounded
          :aria-label="live ? 'Freeze the table' : 'Resume live updates'"
          @click="toggleLive"
        />
        <Button icon="pi pi-refresh" text rounded aria-label="Reload recordings" :loading="loading" @click="load" />
      </div>
    </div>

    <Message v-if="error" severity="error" :closable="false" class="mb-3">
      {{ error }}
      <Button label="Retry" text size="small" class="ml-2" @click="load" />
    </Message>

    <div class="flex gap-2 mb-3">
      <InputText
        v-model="search" class="flex-1"
        aria-label="Search recordings"
        placeholder="Search talkgroup, alpha, description, category, filename or transcript text"
      />
      <Select
        v-model="encFilter" :options="encOptions"
        option-label="label" option-value="value" class="w-10rem"
        aria-label="Filter by encryption"
      />
    </div>

    <!--
      Virtual scrolling, not a paginator. 3,240 rows: the old console showed
      every row in one scroll, and a fixed 10-row pager was a regression on that.
      itemSize must be a constant for the virtualiser, so the transcript cell is
      height-capped and scrolls internally rather than growing the row — see the
      .transcript rule below. Median transcript is 25 chars and p90 is 103, so
      three lines shows the great majority in full.
    -->
    <DataTable
      :value="filtered" :loading="loading"
      data-key="file" size="small" striped-rows removable-sort
      scrollable scroll-height="60vh"
      :virtual-scroller-options="{ itemSize: 62 }"
      sort-field="start" :sort-order="-1"
    >
      <template #empty>
        <span v-if="loading">Loading recordings…</span>
        <span v-else-if="error">Could not load recordings.</span>
        <span v-else-if="recordings.length === 0">No recordings yet.</span>
        <span v-else>No recordings match this search or filter.</span>
      </template>

      <Column field="tgid" header="TG" sortable style="width: 6rem" />
      <Column field="alpha" header="Talkgroup" sortable style="width: 10rem">
        <template #body="{ data }">
          {{ data.alpha ?? '—' }}
        </template>
      </Column>
      <Column field="desc" header="Description" sortable style="width: 14rem">
        <template #body="{ data }">
          <span class="text-color-secondary">{{ data.desc ?? '—' }}</span>
        </template>
      </Column>
      <Column field="transcript" header="Transcript" sortable>
        <template #body="{ data }">
          <div
            v-if="data.transcript"
            class="transcript"
            :class="{ blank: isBlank(data.transcript) }"
          >{{ data.transcript }}</div>
          <span v-else class="text-color-secondary">—</span>
        </template>
      </Column>
      <Column field="start" header="When" sortable style="width: 11rem">
        <template #body="{ data }">{{ formatTime(data.start) }}</template>
      </Column>
      <Column field="dur" header="Len" sortable style="width: 5rem">
        <template #body="{ data }">{{ formatDuration(data.dur) }}</template>
      </Column>
      <Column field="enc" header="Enc" sortable style="width: 7rem">
        <template #body="{ data }">
          <!-- The reference DB's static per-talkgroup label. -->
          <Tag :value="data.enc ?? 'unknown'" :severity="encSeverity(data.enc)" />
        </template>
      </Column>
      <Column field="algid" header="Observed" sortable style="width: 8rem">
        <template #body="{ data }">
          <!-- What the ESS header said for THIS call. Blank when op25 was not
               run with --ess, which is the default. -->
          <Tag
            v-if="data.algid !== null"
            :value="data.algid === 128 ? 'clear' : (data.algorithm ?? 'enc')"
            :severity="essSeverity(data.algid)"
          />
          <span v-else class="text-color-secondary">—</span>
        </template>
      </Column>
      <Column header="" style="width: 4rem">
        <template #body="{ data }">
          <Button icon="pi pi-play" text rounded :aria-label="`Play ${data.alpha ?? data.file}`" @click="open(data)" />
        </template>
      </Column>
    </DataTable>

    <p v-if="!loading" class="text-sm text-color-secondary mt-2 mb-0">
      showing {{ filtered.length }} of {{ total }} recordings
      <span v-if="filtered.length !== total">(filtered)</span>
    </p>

    <Dialog
      v-model:visible="dialogOpen" modal
      :header="selected?.alpha ?? selected?.file ?? 'Recording'"
      :style="{ width: '40rem', maxWidth: '95vw' }"
    >
      <div v-if="selected" class="flex flex-column gap-3">
        <audio :src="`/api/recordings/${selected.file}`" controls class="w-full" />

        <div class="text-sm">
          <div><strong>TG:</strong> {{ selected.tgid ?? '—' }}</div>
          <div><strong>Description:</strong> {{ selected.desc ?? '—' }}</div>
          <div><strong>Category:</strong> {{ selected.cat ?? '—' }}</div>
          <div><strong>Encryption:</strong> {{ selected.enc ?? 'unknown' }}</div>
          <div><strong>Recorded:</strong> {{ formatTime(selected.start) }}</div>
          <div><strong>Voice channel:</strong> {{ formatFreq(selected.freq) }}</div>
          <div v-if="selected.site"><strong>Site:</strong> {{ selected.site }}</div>
          <div v-if="selected.srcAddr">
            <strong>Transmitting unit:</strong> {{ selected.srcAddr }}
          </div>
        </div>

        <!--
          The per-call encryption header, shown against the reference DB's
          static flag. When they disagree the ESS is the one to believe: it is
          read in the clear from this call's own LDU2 frame, whereas the DB
          label describes the talkgroup in general.
        -->
        <div v-if="essLabel(selected)" class="p-2 border-round surface-100 text-sm">
          <div>
            <strong>Encryption observed on this call:</strong>
            {{ essLabel(selected) }}
          </div>
          <div class="text-color-secondary mt-1">
            Reference DB labels this talkgroup
            <em>{{ selected.enc ?? 'unknown' }}</em>.
            <span v-if="selected.algid === 128 && selected.enc !== 'clear'">
              This call transmitted in the clear despite that label.
            </span>
          </div>
        </div>

        <div>
          <h3 class="text-base font-bold mb-2">Transcript</h3>
          <ProgressSpinner v-if="loadingTranscript" style="width: 2rem; height: 2rem" />
          <p
            v-else-if="transcript"
            class="m-0 text-sm line-height-3"
            :class="{ blank: isBlank(transcript) }"
          >{{ transcript }}</p>
          <p v-else class="m-0 text-sm text-color-secondary">No transcript for this call.</p>
        </div>
      </div>
    </Dialog>
  </section>
</template>

<script setup lang="ts">
interface Recording {
  file: string
  tgid: number | null
  alpha: string | null
  desc: string | null
  cat: string | null
  enc: 'clear' | 'partial' | 'full' | null
  start: number
  dur: number
  transcript: string | null
  // P25 metadata read from op25's own output, per call. All optional: a null
  // means "not observed for this call", never zero or unknown-as-a-value.
  srcAddr: number | null
  algid: number | null
  algorithm: string | null
  keyid: number | null
  site: string | null
  freq: number | null
}

interface ApiResponse<T> { success: boolean, data?: T, error?: string }

const recordings = ref<Recording[]>([])
const loading = ref(true)   // SSR paints a loading state, not a false 'empty'
const error = ref('')
const search = ref('')
const encFilter = ref('all')

const dialogOpen = ref(false)
const selected = ref<Recording | null>(null)
const transcript = ref('')
const loadingTranscript = ref(false)

// Real vocabulary: 'full', never 'encrypted'. 'none' covers a recording whose
// talkgroup is absent from the reference DB. Today that is 0 of 3,232 — a
// missing calls.json entry (279 of them) is NOT an unresolved enc, because
// scanRecordings resolves enc from the reference DB regardless. The option is
// kept because the old console had it and it would catch a genuinely unknown
// talkgroup; an empty table when it is selected is correct, not a bug.
const encOptions = [
  { value: 'all',     label: 'All' },
  { value: 'clear',   label: 'Clear' },
  { value: 'partial', label: 'Partial' },
  { value: 'full',    label: 'Full' },
  { value: 'none',    label: 'Unlabelled' },
]

// Filtering happens in SQL now, so `filtered` is simply what the server
// returned. Transcript matching is an FTS5 index lookup rather than
// String.includes across every transcript in the browser.
const filtered = computed(() => recordings.value)

// `total` is the unfiltered corpus size, so the footer can say "N of M".
const total = ref(0)

/**
 * Debounced because each keystroke is now a request.
 *
 * The frontend review measured client-side filtering at 0.33-0.81ms and
 * explicitly said NOT to debounce it — correct then, because the work was
 * local. A network round trip is a different cost, so 250ms applies here and
 * would have been pure added latency before.
 */
let searchTimer: ReturnType<typeof setTimeout> | null = null

watch([search, encFilter], () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(load, 250)
})

onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

// Still bumped by ListenControl after Stop. Kept even though the SSE stream
// now covers the same ground: Stop is the one moment the operator definitely
// wants the final state, and it costs one reload.
const recordingsRefresh = useState<number>('recordings-refresh', () => 0)
watch(recordingsRefresh, load)

/**
 * Live updates, over /api/recordings/stream.
 *
 * The server pushes a summary — `{ calls, transcripts, latest }` — whenever the
 * corpus changes, and never rows: this component re-runs its own query so the
 * search box, encryption filter and sort keep working, and there is one place
 * that knows how to build a recordings query.
 *
 * Transcripts arrive well after their call (Whisper on CPU), so a row appears
 * first and gains its transcript seconds to minutes later. That is why the
 * summary carries a transcript count and not just a call count — otherwise
 * transcripts would only ever show up on a manual refresh.
 */
const live = ref(true)
const streamOk = ref(false)
const pending = ref(0)
let seen: { calls: number, transcripts: number } | null = null
let es: EventSource | null = null

function onSummary(next: { calls: number, transcripts: number }): void {
  if (seen === null) {                       // first frame: just a baseline
    seen = next
    return
  }
  const changed = next.calls !== seen.calls || next.transcripts !== seen.transcripts
  if (!changed) return
  pending.value += Math.max(0, next.calls - seen.calls)
  seen = next
  if (live.value) load()
}

function toggleLive(): void {
  live.value = !live.value
  if (live.value) load()
}

onMounted(() => {
  load()
  // EventSource reconnects on its own after a drop, which is most of why this
  // is SSE and not a hand-rolled fetch loop.
  es = new EventSource('/api/recordings/stream')
  es.onopen = () => { streamOk.value = true }
  es.onerror = () => { streamOk.value = false }
  es.onmessage = (ev) => {
    streamOk.value = true
    try {
      onSummary(JSON.parse(ev.data) as { calls: number, transcripts: number })
    } catch {
      // A frame we cannot parse is not worth breaking the table over; the next
      // change will carry the same counts.
    }
  }
})

onUnmounted(() => {
  es?.close()
  es = null
})

/**
 * Guards against a slow earlier request landing after a faster later one and
 * overwriting it with stale rows — visible as the table briefly showing results
 * for a prefix of what you typed.
 */
let requestSeq = 0

async function load(): Promise<void> {
  const seq = ++requestSeq
  loading.value = true
  try {
    const res = await $fetch<ApiResponse<Recording[]> & { total?: number }>(
      '/api/recordings/list',
      { query: {
        search: search.value.trim() || undefined,
        enc: encFilter.value === 'all' ? undefined : encFilter.value,
      } },
    )
    if (seq !== requestSeq) return          // superseded; drop it
    if (res.success && res.data) {
      recordings.value = res.data
      pending.value = 0
      if (typeof res.total === 'number' && !search.value.trim() && encFilter.value === 'all') {
        total.value = res.total
      }
      error.value = ''
    } else {
      error.value = res.error ?? 'The server returned no recordings.'
    }
  } catch (e) {
    if (seq !== requestSeq) return
    // Deliberately NOT `recordings.value = []`. Blanking the table turns
    // "the backend is down" into "you have no recordings", which on a
    // monitoring console is an active lie — and it also wipes a populated
    // table on a transient blip. Keep the last known rows and say so.
    error.value = apiError(e, 'Could not reach the server.')
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

/**
 * ofetch throws a FetchError whose .message is only the status line; the
 * handler's JSON body is on .data.
 */
function apiError(e: unknown, fallback: string): string {
  if (e && typeof e === 'object' && 'data' in e) {
    const d = (e as { data?: unknown }).data
    if (d && typeof d === 'object' && 'error' in d
        && typeof (d as { error?: unknown }).error === 'string') {
      return (d as { error: string }).error
    }
  }
  return e instanceof Error ? e.message : fallback
}

async function open(rec: Recording): Promise<void> {
  selected.value = rec
  dialogOpen.value = true

  if (rec.transcript) {
    transcript.value = rec.transcript
    return
  }

  transcript.value = ''
  loadingTranscript.value = true
  try {
    transcript.value = await $fetch<string>(
      `/api/recordings/${rec.file.replace(/\.wav$/, '.txt')}`,
    )
  } catch {
    transcript.value = ''
  } finally {
    loadingTranscript.value = false
  }
}

function formatTime(ts: number): string {
  return ts ? new Date(ts * 1000).toLocaleString() : '—'
}

function formatDuration(sec: number): string {
  if (!sec) return '—'
  return sec < 60 ? `${sec.toFixed(1)}s` : `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`
}

/**
 * op25's -n silences encrypted bursts, so partial-encryption talkgroups produce
 * many calls that transcribe to exactly [BLANK_AUDIO] — 518 of 3,232 today.
 * Without dimming they are indistinguishable from real content at a glance.
 */
function isBlank(t: string): boolean {
  return t.startsWith('[BLANK_AUDIO]')
}

function formatFreq(hz: number | null): string {
  return hz ? `${(hz / 1e6).toFixed(4)} MHz` : '—'
}

/**
 * What the ESS header said for THIS call, which is the authoritative signal.
 * The `enc` column beside it is the reference DB's static per-talkgroup label,
 * and the two genuinely disagree: TG 17086 is flagged 'full' upstream but
 * transmitted algid 0x80 (clear) in all 23 observations, and TG 17165 is
 * flagged 'partial' while most of its calls transmit clear.
 */
function essLabel(r: Recording): string | null {
  if (r.algid === null) return null
  const hex = `0x${r.algid.toString(16).padStart(2, '0')}`
  return r.keyid ? `${r.algorithm ?? hex} (key 0x${r.keyid.toString(16)})` : (r.algorithm ?? hex)
}

function essSeverity(algid: number | null): string {
  if (algid === null) return 'secondary'
  return algid === 0x80 ? 'success' : 'danger'   // 0x80 is the only clear value
}

function encSeverity(enc: string | null): string {
  if (enc === 'clear') return 'success'
  if (enc === 'partial') return 'warn'   // PrimeVue 4 uses 'warn', not 'warning'
  if (enc === 'full') return 'danger'
  return 'secondary'
}
</script>

<style scoped>
/* [BLANK_AUDIO] — a silenced encrypted burst, not speech. */
.blank {
  /* NOT opacity. Composited against white, opacity:.5 on #334155 measures
     2.64:1 — below WCAG AA's 4.5 — across 518 rows. The muted token measures
     4.76:1 and italic already carries the signal non-chromatically. */
  color: var(--p-text-muted-color);
  font-style: italic;
}

/*
  Full transcript, not a 60-char truncation — the old console showed it whole
  and it is the payoff of --stt. But the virtual scroller needs a CONSTANT row
  height, so the cell is capped and scrolls internally instead of growing the
  row. Three lines covers p90 (103 chars); the 2.8% longer than 200 chars
  scroll in place, and the Dialog always shows the whole thing.
*/
.transcript {
  max-height: 3.9em;
  overflow-y: auto;
  white-space: pre-wrap;
  line-height: 1.3;
  scrollbar-width: thin;
}
</style>
