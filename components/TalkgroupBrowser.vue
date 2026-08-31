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

    <DataTable
      :value="filtered" :loading="loading" paginator :rows="12"
      data-key="tgid" size="small" striped-rows :row-class="rowClass"
    >
      <template #empty>No talkgroups match.</template>

      <Column field="tgid" header="TG" style="width: 6rem" />
      <Column field="alpha" header="Alpha" style="width: 12rem" />
      <Column field="desc" header="Description" />
      <Column field="cat" header="Category" style="width: 14rem" />
      <Column field="tag" header="Tag" style="width: 9rem" />
      <Column header="Enc" style="width: 7rem">
        <template #body="{ data }">
          <Tag :value="data.enc" :severity="encSeverity(data.enc)" />
          <!-- mode is "D enc" for encrypted talkgroups, "D" otherwise -->
          <div class="text-sm text-color-secondary">{{ data.mode }}</div>
        </template>
      </Column>
      <Column header="Whitelist" style="width: 7rem">
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

const filtered = computed(() => {
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
  })
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
