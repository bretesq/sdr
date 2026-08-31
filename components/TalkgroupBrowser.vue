<template>
  <section class="p-4 border-round surface-card">
    <h2 class="text-xl font-bold mt-0 mb-3">Talkgroups</h2>

    <div class="flex gap-2 mb-3 flex-wrap">
      <Select
        v-model="area" :options="areaOptions"
        option-label="label" option-value="value"
        class="w-12rem" @change="load"
      />
      <Select
        v-model="category" :options="categoryOptions"
        option-label="label" option-value="value"
        placeholder="All categories" class="w-14rem" show-clear
      />
      <Select
        v-model="encFilter" :options="encOptions"
        option-label="label" option-value="value" class="w-10rem"
      />
      <InputText v-model="search" placeholder="Search TG, alpha, desc, category, tag" class="flex-1" />
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
        <span v-if="talkgroups.length === 0">No talkgroups loaded.</span>
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
    <p class="text-sm text-color-secondary mt-3 mb-0">
      showing {{ filtered.length }} of {{ talkgroups.length }} talkgroups
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
const loading = ref(false)

const areaOptions: Option[] = [
  { value: 'br',  label: 'Baton Rouge Area' },
  { value: 'all', label: 'Statewide' },
]

const categoryOptions = computed<Option[]>(() => {
  const cats = new Set(talkgroups.value.map(t => t.cat).filter(Boolean))
  return [...cats].sort().map(c => ({ value: c, label: c }))
})

const encOptions: Option[] = [
  { value: 'all',     label: 'All encryption' },
  { value: 'clear',   label: 'Clear' },
  { value: 'partial', label: 'Partial' },
  { value: 'full',    label: 'Full' },
]

// `inWhitelist` is derived onto each row rather than read from a Set in the
// template, so the Whitelist column has a real field for PrimeVue to sort on.
const filtered = computed<Talkgroup[]>(() => {
  const q = search.value.trim().toLowerCase()
  return talkgroups.value.filter((t) => {
    if (category.value && t.cat !== category.value) return false
    if (encFilter.value !== 'all' && t.enc !== encFilter.value) return false
    if (!q) return true
    // server.py searched [alpha, desc, cat, tag, tgid].
    return String(t.tgid).includes(q)
      || (t.alpha ?? '').toLowerCase().includes(q)
      || (t.desc ?? '').toLowerCase().includes(q)
      || (t.cat ?? '').toLowerCase().includes(q)
      || (t.tag ?? '').toLowerCase().includes(q)
  }).map(t => ({ ...t, inWhitelist: whitelist.value.has(t.tgid) }))
})

onMounted(async () => {
  await Promise.all([load(), loadWhitelist()])
})

async function load(): Promise<void> {
  loading.value = true
  category.value = null
  try {
    const res = await $fetch<ApiResponse<Talkgroup[]>>('/api/talkgroups/list', {
      query: { area: area.value },
    })
    if (res.success && res.data) talkgroups.value = res.data
  } catch {
    talkgroups.value = []
  } finally {
    loading.value = false
  }
}

async function loadWhitelist(): Promise<void> {
  try {
    const res = await $fetch<ApiResponse<{ tgids: number[] }>>('/api/talkgroups/whitelist')
    if (res.success && res.data) whitelist.value = new Set(res.data.tgids)
  } catch {
    whitelist.value = new Set()
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
