<template>
  <section class="p-4 border-round surface-card">
    <div class="flex align-items-center justify-content-between mb-3">
      <h2 class="text-xl font-bold m-0">Talkgroups</h2>
      <!--
        scripts/lwin_listen.sh:75 regenerates lwin_active_whitelist.txt on every
        Start, so the `active` marks go stale the moment a session begins and the
        footer below then asserts something false. Not reloaded automatically on
        Start: startListening() returns as soon as bash is spawned, before
        make_whitelist.py has necessarily written the file, so an immediate
        refetch would just cache the old contents.
      -->
      <Button
        icon="pi pi-refresh" text rounded
        aria-label="Reload talkgroups and whitelist"
        :loading="loading" @click="reload"
      />
    </div>

    <Message v-if="error" severity="error" :closable="false" class="mb-3">
      {{ error }}
      <Button label="Retry" text size="small" class="ml-2" @click="reload" />
    </Message>

    <div class="flex gap-2 mb-3 flex-wrap">
      <Select
        v-model="area" :options="areaOptions"
        option-label="label" option-value="value"
        class="w-12rem" aria-label="Area" @change="changeArea"
      />
      <Select
        v-model="category" :options="categoryOptions"
        option-label="label" option-value="value"
        placeholder="All categories" class="w-14rem" show-clear aria-label="Filter by category"
      />
      <Select
        v-model="encFilter" :options="encOptions"
        option-label="label" option-value="value" class="w-10rem"
        aria-label="Filter by encryption"
      />
      <InputText
        v-model="search" class="flex-1"
        aria-label="Search talkgroups"
        placeholder="Search TG, alpha, desc, category, tag"
      />
    </div>

    <!--
      Virtual scrolling over all 4,163 rows rather than a 12-row pager. Every
      column sorts; removable-sort lets a third click return to the natural
      tgid order. Rows are single-line here, so a 40px itemSize is safe.
    -->
    <DataTable
      :value="filtered" :loading="loading"
      data-key="tgid" size="small" striped-rows removable-sort
      :row-class="rowClass"
      scrollable scroll-height="60vh"
      :virtual-scroller-options="{ itemSize: 40 }"
    >
      <template #empty>
        <span v-if="loading">Loading talkgroups…</span>
        <span v-else-if="error">Could not load talkgroups.</span>
        <span v-else-if="talkgroups.length === 0">No talkgroups loaded.</span>
        <span v-else>No talkgroups match these filters.</span>
      </template>

      <Column field="tgid" header="TG" sortable style="width: 6rem" />
      <Column field="alpha" header="Alpha" sortable style="width: 12rem" />
      <Column field="desc" header="Description" sortable />
      <Column field="cat" header="Category" sortable style="width: 14rem" />
      <Column field="tag" header="Tag" sortable style="width: 9rem" />
      <Column field="enc" header="Enc" sortable style="width: 7rem">
        <template #body="{ data }">
          <Tag :value="data.enc" :severity="encSeverity(data.enc)" />
          <!-- mode is "D enc" for encrypted talkgroups, "D" otherwise -->
          <span class="text-color-secondary ml-1">{{ data.mode }}</span>
        </template>
      </Column>
      <Column field="inWhitelist" header="Whitelist" sortable style="width: 7rem">
        <template #body="{ data }">
          <Tag v-if="whitelist.has(data.tgid)" value="active" severity="info" />
        </template>
      </Column>
    </DataTable>

    <!--
      Counts, not just a legend sentence. This is how you notice at a glance that
      a filtering bug has silently emptied the table — the failure mode B1 caused.
    -->
    <p v-if="!loading" class="text-sm text-color-secondary mt-3 mb-0">
      showing {{ filtered.length }} of {{ total }} talkgroups
      ({{ area === 'br' ? 'Baton Rouge area' : 'statewide' }})
      · {{ whitelist.size }} in the current
      <code>lwin_active_whitelist.txt</code>, marked
      <Tag value="active" severity="info" />
    </p>
  </section>
</template>

<script setup lang="ts">
interface Talkgroup {
  tgid: number
  alpha: string
  desc: string
  cat: string
  enc: 'clear' | 'partial' | 'full'
  tag: string
  mode: string
  inWhitelist?: boolean
}

interface Option { value: string, label: string }
interface ApiResponse<T> { success: boolean, data?: T, error?: string }

const area = ref<'br' | 'all'>('br')
const category = ref<string | null>(null)
const encFilter = ref('all')
const search = ref('')

const talkgroups = ref<Talkgroup[]>([])
const whitelist = ref<Set<number>>(new Set())
const loading = ref(true)   // SSR paints loading, not a false 'empty'
const error = ref('')

const areaOptions: Option[] = [
  { value: 'br',  label: 'Baton Rouge Area' },
  { value: 'all', label: 'Statewide' },
]

// Fetched separately, not derived from the loaded rows: with server-side
// filtering the loaded set shrinks, so deriving would leave the dropdown
// offering only the category already selected.
const categories = ref<string[]>([])
const categoryOptions = computed<Option[]>(() =>
  categories.value.map(c => ({ value: c, label: c })))

const total = ref(0)

const encOptions: Option[] = [
  { value: 'all',     label: 'All encryption' },
  { value: 'clear',   label: 'Clear' },
  { value: 'partial', label: 'Partial' },
  { value: 'full',    label: 'Full' },
]

// Filtering is SQL now; `inWhitelist` is still derived here so the Whitelist
// column has a real field for PrimeVue to sort on.
const filtered = computed<Talkgroup[]>(() =>
  talkgroups.value.map(t => ({ ...t, inWhitelist: whitelist.value.has(t.tgid) })))

// Debounced: each keystroke is a request now. Client-side filtering measured
// 0.5-0.8ms and rightly had no debounce; a round trip is a different cost.
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch([search, category, encFilter], () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(load, 250)
})
onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

async function reload(): Promise<void> {
  await Promise.all([load(), loadWhitelist(), loadCategories()])
}

/** Area is a different corpus, so reset the filters that were scoped to it. */
async function changeArea(): Promise<void> {
  category.value = null
  await load()
}

onMounted(reload)

let requestSeq = 0

async function load(): Promise<void> {
  const seq = ++requestSeq
  loading.value = true
  try {
    const res = await $fetch<ApiResponse<Talkgroup[]> & { total?: number }>(
      '/api/talkgroups/list',
      { query: {
        area: area.value,
        category: category.value || undefined,
        enc: encFilter.value === 'all' ? undefined : encFilter.value,
        search: search.value.trim() || undefined,
      } },
    )
    if (seq !== requestSeq) return
    if (res.success && res.data) {
      talkgroups.value = res.data
      error.value = ''
    } else {
      error.value = res.error ?? 'The server returned no talkgroups.'
    }
  } catch (e) {
    // Not `talkgroups.value = []` — see RecordingsList. An empty table here
    // would read as "this area has no talkgroups", which is never true.
    error.value = e instanceof Error ? e.message : 'Could not reach the server.'
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

async function loadCategories(): Promise<void> {
  try {
    const res = await $fetch<ApiResponse<string[]>>('/api/talkgroups/categories')
    if (res.success && res.data) categories.value = res.data
  } catch {
    // Non-fatal: the dropdown is empty but every other filter still works.
  }
}

async function loadWhitelist(): Promise<void> {
  try {
    const res = await $fetch<ApiResponse<{ tgids: number[] }>>('/api/talkgroups/whitelist')
    if (res.success && res.data) whitelist.value = new Set(res.data.tgids)
  } catch {
    // Keep the last known whitelist rather than un-marking every row, which
    // would falsely imply nothing is being recorded.
  }
}

function rowClass(data: Talkgroup): string {
  return whitelist.value.has(data.tgid) ? 'surface-100' : ''
}

function encSeverity(enc: string): string {
  if (enc === 'clear') return 'success'
  if (enc === 'partial') return 'warn'   // PrimeVue 4 uses 'warn', not 'warning'
  if (enc === 'full') return 'danger'
  return 'secondary'
}
</script>
