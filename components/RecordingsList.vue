<template>
  <section class="p-4 border-round surface-card">
    <div class="flex align-items-center justify-content-between mb-3">
      <h2 class="text-xl font-bold m-0">Recordings</h2>
      <Button icon="pi pi-refresh" text rounded aria-label="Reload recordings" :loading="loading" @click="load" />
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
          <Tag :value="data.enc ?? 'unknown'" :severity="encSeverity(data.enc)" />
        </template>
      </Column>
      <Column header="" style="width: 4rem">
        <template #body="{ data }">
          <Button icon="pi pi-play" text rounded :aria-label="`Play ${data.alpha ?? data.file}`" @click="open(data)" />
        </template>
      </Column>
    </DataTable>

    <p v-if="!loading" class="text-sm text-color-secondary mt-2 mb-0">
      showing {{ filtered.length }} of {{ recordings.length }} recordings
      <span v-if="filtered.length !== recordings.length">(filtered)</span>
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

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return recordings.value.filter((r) => {
    if (encFilter.value === 'none') {
      if (r.enc) return false
    } else if (encFilter.value !== 'all' && r.enc !== encFilter.value) {
      return false
    }
    if (!q) return true
    // Six fields, matching server.py's [alpha, desc, cat, transcript, file, tgid].
    // Transcript search is the point of --stt: 3,220 non-empty transcripts.
    return String(r.tgid ?? '').includes(q)
      || (r.alpha ?? '').toLowerCase().includes(q)
      || (r.desc ?? '').toLowerCase().includes(q)
      || (r.cat ?? '').toLowerCase().includes(q)
      || (r.transcript ?? '').toLowerCase().includes(q)
      || r.file.toLowerCase().includes(q)
  })
})

// Bumped by ListenControl 1.5 s after Stop, when calls.json has been flushed.
const recordingsRefresh = useState<number>('recordings-refresh', () => 0)
watch(recordingsRefresh, load)

onMounted(load)

async function load(): Promise<void> {
  loading.value = true
  try {
    const res = await $fetch<ApiResponse<Recording[]>>('/api/recordings/list')
    if (res.success && res.data) {
      recordings.value = res.data
      error.value = ''
    } else {
      error.value = res.error ?? 'The server returned no recordings.'
    }
  } catch (e) {
    // Deliberately NOT `recordings.value = []`. Blanking the table turns
    // "the backend is down" into "you have no recordings", which on a
    // monitoring console is an active lie — and it also wipes a populated
    // table on a transient blip. Keep the last known rows and say so.
    error.value = apiError(e, 'Could not reach the server.')
  } finally {
    loading.value = false
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
