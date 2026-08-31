<template>
  <section class="p-4 border-round surface-card">
    <div class="flex align-items-center justify-content-between mb-3">
      <h2 class="text-xl font-bold m-0">Recordings</h2>
      <Button icon="pi pi-refresh" text rounded :loading="loading" @click="load" />
    </div>

    <div class="flex gap-2 mb-3">
      <InputText v-model="search" placeholder="Search TG, alpha, description" class="flex-1" />
      <Select
        v-model="encFilter" :options="encOptions"
        option-label="label" option-value="value" class="w-10rem"
      />
    </div>

    <DataTable
      :value="filtered" :loading="loading" paginator :rows="10"
      data-key="file" size="small" striped-rows
    >
      <template #empty>No recordings yet.</template>

      <Column field="tgid" header="TG" style="width: 6rem" />
      <Column field="alpha" header="Talkgroup">
        <template #body="{ data }">
          {{ data.alpha ?? '—' }}
        </template>
      </Column>
      <Column header="Transcript">
        <template #body="{ data }">
          <span
            v-if="data.transcript"
            class="text-sm"
            :class="{ blank: isBlank(data.transcript) }"
          >{{ truncate(data.transcript) }}</span>
          <span v-else class="text-sm text-color-secondary">—</span>
        </template>
      </Column>
      <Column header="When" style="width: 11rem">
        <template #body="{ data }">{{ formatTime(data.start) }}</template>
      </Column>
      <Column header="Len" style="width: 5rem">
        <template #body="{ data }">{{ formatDuration(data.dur) }}</template>
      </Column>
      <Column header="Enc" style="width: 7rem">
        <template #body="{ data }">
          <Tag :value="data.enc ?? 'unknown'" :severity="encSeverity(data.enc)" />
        </template>
      </Column>
      <Column header="" style="width: 4rem">
        <template #body="{ data }">
          <Button icon="pi pi-play" text rounded @click="open(data)" />
        </template>
      </Column>
    </DataTable>

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
const loading = ref(false)
const search = ref('')
const encFilter = ref('all')

const dialogOpen = ref(false)
const selected = ref<Recording | null>(null)
const transcript = ref('')
const loadingTranscript = ref(false)

// Real vocabulary: 'full', never 'encrypted'. 'none' covers recordings whose
// talkgroup is not in the reference DB — 279 of 3,232 have no calls.json entry,
// and the old console had this option for exactly that reason.
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
    // Transcript search is the point of --stt: 3,231 transcripts on disk.
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
    if (res.success && res.data) recordings.value = res.data
  } catch {
    recordings.value = []
  } finally {
    loading.value = false
  }
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
 * many calls that transcribe to exactly [BLANK_AUDIO] — 528 of 3,231 today.
 * Without dimming they are indistinguishable from real content at a glance.
 */
function isBlank(t: string): boolean {
  return t.startsWith('[BLANK_AUDIO]')
}

function truncate(t: string, n = 60): string {
  return t.length > n ? `${t.slice(0, n)}…` : t
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
  opacity: 0.5;
  font-style: italic;
}
</style>
