<template>
  <section class="p-4 border-round surface-card">
    <h2 class="text-xl font-bold mt-0 mb-3">Listen &amp; Record</h2>

    <div v-if="running" class="p-3 mb-3 border-round surface-100">
      <div class="flex align-items-center gap-2">
        <!-- Green reads "healthy". If the poll is stale we cannot claim that. -->
        <Tag :value="stale ? 'RECORDING?' : 'RECORDING'" :severity="stale ? 'warn' : 'success'" />
        <span class="text-sm text-color-secondary">pid {{ pid }}</span>
      </div>
      <div class="text-2xl font-bold mt-2">{{ callCount }} calls</div>
      <div class="text-sm text-color-secondary">since {{ startedAt }}</div>
      <!-- What the session is actually following. server.py's status line showed
           this; without it you can see that something runs but not what. -->
      <div v-if="configSummary" class="text-sm mt-1">{{ configSummary }}</div>
    </div>

    <!--
      op25 holds the radio but we are not tracking a session: bash died while
      rx.py survived behind its pty. A fresh Start would contend for the HackRF,
      so say so rather than presenting an innocent-looking idle panel.
    -->
    <Message v-if="!running && radioBusy" severity="warn" :closable="false" class="mb-3">
      op25 is still using the radio although no session is being tracked.
      Press Stop to release it before starting a new session.
    </Message>

    <Message v-if="stale" severity="warn" :closable="false" class="mb-3">
      No response from the server since {{ new Date(lastOk).toLocaleTimeString() }} —
      the state shown may be out of date.
    </Message>

    <div class="flex flex-column gap-3">
      <!--
        Capture mode. 'single' is op25 rx.py on one HackRF: it must leave the
        control channel to hear a call, so it catches roughly a quarter of them
        and its grant census is incomplete. 'multi' is op25 multi_rx.py across
        both HackRFs, with one receiver pinned to the control channel and a
        voice pool per band — measured 64-70 calls per 300 s with 24-45 of them
        overlapping in time, plus a complete census in the same run.
      -->
      <div>
        <span id="mode-label" class="block mb-1 text-sm">Capture mode</span>
        <SelectButton
          v-model="mode" aria-labelledby="mode-label" :options="modes"
          option-label="label" option-value="value" :allow-empty="false"
          :disabled="running" class="w-full"
        />
        <small class="block mt-1 text-color-secondary">{{ modeHint }}</small>
      </div>

      <!-- Multi-only. Hidden in single mode because lwin_listen.sh exits 1 on
           an unknown option, and the API rejects these fields without mode. -->
      <template v-if="mode === 'multi'">
        <div class="grid">
          <div class="col-6">
            <label for="nv700" class="block mb-1 text-sm">
              700 MHz voice receivers
            </label>
            <InputNumber
              v-model="nVoice700" input-id="nv700" :min="1" :max="8"
              :disabled="running" class="w-full" show-buttons
            />
          </div>
          <div class="col-6">
            <label for="nv800" class="block mb-1 text-sm">
              800 MHz voice receivers
            </label>
            <InputNumber
              v-model="nVoice800" input-id="nv800" :min="1" :max="8"
              :disabled="running" class="w-full" show-buttons
            />
          </div>
        </div>
        <small class="block -mt-2 text-color-secondary">
          The control channel (773.05625) always gets its own pinned receiver on
          top of these. 3 and 5 match the measured traffic; the 800 MHz leg
          carries about three quarters of the calls.
        </small>

        <div class="flex align-items-center gap-2">
          <Checkbox v-model="census" input-id="census" binary :disabled="running" />
          <label for="census" class="text-sm">
            Import the grant census at exit (op25 -v 10, ~10x log volume)
          </label>
        </div>
      </template>

      <div>
        <span id="preset-label" class="block mb-1 text-sm">Preset</span>
        <Select
          v-model="preset" aria-labelledby="preset-label" :options="presets"
          option-label="label" option-value="value"
          placeholder="Select a preset" :disabled="running" class="w-full"
          show-clear
        />
      </div>

      <div>
        <label for="tgs" class="block mb-1 text-sm">Or explicit talkgroup IDs</label>
        <InputText
          id="tgs" v-model="talkgroups" placeholder="17165,17167,17169"
          :disabled="running" class="w-full"
        />
      </div>

      <div>
        <label for="tag" class="block mb-1 text-sm">Or by tag</label>
        <InputText
          id="tag" v-model="tag" placeholder="Law Dispatch,Law Talk"
          :disabled="running" class="w-full"
        />
      </div>

      <div>
        <label for="match" class="block mb-1 text-sm">Or by regex (alpha / desc / category)</label>
        <InputText
          id="match" v-model="match" placeholder="BRPD"
          :disabled="running" class="w-full"
        />
      </div>

      <!--
        Three INDEPENDENT checkboxes, not a radio group. make_whitelist.py adds
        'partial' only under --include-partial and 'full' only under
        --include-encrypted, so the flags compose. A radio group would make
        "partial AND full" unreachable and would silently drop partial when
        "encrypted" was chosen.
      -->
      <div class="flex align-items-center gap-2">
        <Checkbox v-model="includePartial" input-id="partial" binary :disabled="running" />
        <label for="partial" class="text-sm">Include partially-encrypted TGs (BRPD / EBR SO)</label>
      </div>

      <div class="flex align-items-center gap-2">
        <Checkbox v-model="includeEncrypted" input-id="encrypted" binary :disabled="running" />
        <label for="encrypted" class="text-sm">Include fully-encrypted TGs (records silence)</label>
      </div>

      <div class="flex align-items-center gap-2">
        <Checkbox v-model="allAreas" input-id="allareas" binary :disabled="running" />
        <label for="allareas" class="text-sm">All areas (statewide, not just Baton Rouge)</label>
      </div>

      <div class="flex align-items-center gap-2">
        <Checkbox v-model="stt" input-id="stt" binary :disabled="running" />
        <label for="stt" class="text-sm">Transcribe with Whisper</label>
      </div>

      <div class="flex align-items-center gap-2">
        <Checkbox v-model="ess" input-id="ess" binary :disabled="running" />
        <label for="ess" class="text-sm">
          Capture encryption headers (op25 -v 10, ~10&times; log volume)
        </label>
      </div>

      <div>
        <label for="dur" class="block mb-1 text-sm">Duration (seconds)</label>
        <InputNumber
          v-model="duration" input-id="dur" :disabled="running"
          :min="1" placeholder="blank = until stopped" class="w-full"
        />
      </div>

      <div class="flex gap-2">
        <Button
          v-if="!running" label="Start" icon="pi pi-play" severity="success"
          :loading="busy" :disabled="radioBusy" @click="start"
        />
        <Button
          v-if="running || radioBusy" label="Stop" icon="pi pi-stop" severity="danger"
          :loading="busy" @click="stop"
        />
      </div>

      <Message v-if="error" severity="error" :closable="true" @close="error = ''">
        {{ error }}
      </Message>
    </div>
  </section>
</template>

<script setup lang="ts">
interface PresetOption { value: string, label: string }

interface ListenConfig {
  mode?: 'single' | 'multi'
  legs?: '700' | '800' | '700,800'
  nVoice700?: number
  nVoice800?: number
  census?: boolean
  preset?: string
  talkgroups?: string
  tag?: string
  match?: string
  allAreas?: boolean
  includePartial?: boolean
  includeEncrypted?: boolean
  stt?: boolean
  ess?: boolean
  duration?: number
}

interface StatusPayload {
  running: boolean
  pid: number | null
  config: ListenConfig | null
  callCount: number
  startTime: number | null
  lastUpdate: number
  radioBusy: boolean
}

interface ApiResponse<T> { success: boolean, data?: T, error?: string }

// Defaults to 'single' so the console behaves exactly as before until the
// operator opts in. Multi mode needs BOTH HackRFs present.
const mode = ref<'single' | 'multi'>('single')
const modes = [
  { value: 'single', label: '1 radio' },
  { value: 'multi', label: '2 radios, many receivers' },
]
// 3 and 5 are the measured optima: 3 receivers took 28/28 of the 700 leg's
// calls in the census and 5 took 81/83 of the 800 leg's.
const nVoice700 = ref<number>(3)
const nVoice800 = ref<number>(5)
const census = ref(true)

const modeHint = computed(() =>
  mode.value === 'multi'
    ? 'Both HackRFs, one receiver pinned to the control channel. Records several '
      + 'simultaneous calls and a complete grant census in one run.'
    : 'One HackRF. Leaves the control channel to hear each call, so simultaneous '
      + 'calls are missed and the grant census is incomplete.',
)

// Defaults to 'all', matching make_whitelist.py's own --preset default and the
// old console. Starting at null meant a fresh page + Start returned a 400.
const preset = ref<string | null>('all')
const talkgroups = ref('')
const tag = ref('')
const match = ref('')
const allAreas = ref(false)
const includePartial = ref(false)
const includeEncrypted = ref(false)
const stt = ref(false)
const ess = ref(false)
const duration = ref<number | null>(null)

const running = ref(false)
const pid = ref<number | null>(null)
const callCount = ref(0)
const startTime = ref<number | null>(null)
const runningConfig = ref<ListenConfig | null>(null)
const busy = ref(false)
const error = ref('')

/**
 * When the last status poll SUCCEEDED. Without this a dead backend is
 * indistinguishable from a healthy idle one: the panel keeps showing RECORDING
 * and a frozen call count forever, and the first sign of trouble is Stop
 * failing for no visible reason.
 */
const radioBusy = ref(false)
const lastOk = ref<number>(Date.now())
const now = ref<number>(Date.now())
let clock: ReturnType<typeof setInterval> | null = null

// Two missed 5s polls. Long enough not to flicker on one slow response.
const stale = computed(() => now.value - lastOk.value > 13_000)

const presets = ref<PresetOption[]>([])

// Bumped on stop so RecordingsList knows to reload. calls.json is written only
// at session end, so this is exactly when new metadata becomes available.
const recordingsRefresh = useState<number>('recordings-refresh', () => 0)

const startedAt = computed(() =>
  startTime.value ? new Date(startTime.value * 1000).toLocaleTimeString() : '',
)

const configSummary = computed(() => {
  const c = runningConfig.value
  if (!c) return ''
  const bits: string[] = []
  if (c.preset) bits.push(c.preset)
  if (c.talkgroups) bits.push(`tg ${c.talkgroups}`)
  if (c.tag) bits.push(`tag "${c.tag}"`)
  if (c.match) bits.push(`match /${c.match}/`)
  if (c.allAreas) bits.push('statewide')
  if (c.includePartial) bits.push('+partial')
  if (c.includeEncrypted) bits.push('+encrypted')
  if (c.stt) bits.push('stt')
  if (c.ess) bits.push('ess')
  if (c.duration) bits.push(`${c.duration}s`)
  return bits.join(' · ')
})

/**
 * ofetch throws a FetchError whose .message is just the status line
 * (`[POST] "/api/listen/start": 409 Conflict`); the handler's JSON body is on
 * .data. Without this every server-side message is invisible to the user.
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

let timer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  try {
    const res = await $fetch<ApiResponse<{ presets: PresetOption[] }>>('/api/config/presets')
    if (res.success && res.data) presets.value = res.data.presets
  } catch {
    error.value = 'Could not load presets'
  }

  await refresh()
  timer = setInterval(refresh, 5000)
  clock = setInterval(() => { now.value = Date.now() }, 2000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (clock) clearInterval(clock)
})

async function refresh(): Promise<void> {
  try {
    const res = await $fetch<ApiResponse<StatusPayload>>('/api/listen/status')
    if (!res.success || !res.data) return
    running.value = res.data.running
    pid.value = res.data.pid
    callCount.value = res.data.callCount
    startTime.value = res.data.startTime
    runningConfig.value = res.data.config
    radioBusy.value = res.data.radioBusy
    lastOk.value = Date.now()
  } catch (err) {
    // A failed poll is usually transient (dev-server reload, brief network
    // blip), so the last known state stays on screen rather than flashing an
    // error every 5s. Logged rather than swallowed: a *persistent* failure here
    // means the status panel is silently frozen, which is worth being able to
    // see in the console.
    console.warn('[ListenControl] status poll failed:', err)
  }
}

async function start(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const res = await $fetch<ApiResponse<unknown>>('/api/listen/start', {
      method: 'POST',
      body: {
        // The multi-only fields must be ABSENT, not false/undefined-valued, in
        // single mode: the API rejects them outright rather than ignoring
        // them, so that a request never starts a differently-shaped session
        // than the one asked for.
        ...(mode.value === 'multi'
          ? {
              mode: 'multi' as const,
              legs: '700,800' as const,
              nVoice700: nVoice700.value,
              nVoice800: nVoice800.value,
              census: census.value,
            }
          : {}),
        preset: preset.value ?? undefined,
        talkgroups: talkgroups.value || undefined,
        tag: tag.value || undefined,
        match: match.value || undefined,
        allAreas: allAreas.value,
        includePartial: includePartial.value,
        includeEncrypted: includeEncrypted.value,
        stt: stt.value,
        ess: ess.value,
        duration: duration.value ?? undefined,
      },
    })
    if (!res.success) error.value = res.error ?? 'Failed to start'
  } catch (e) {
    error.value = apiError(e, 'Failed to start')
  } finally {
    busy.value = false
    await refresh()
  }
}

async function stop(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const res = await $fetch<ApiResponse<unknown>>('/api/listen/stop', { method: 'POST' })
    if (!res.success) error.value = res.error ?? 'Failed to stop'
  } catch (e) {
    error.value = apiError(e, 'Failed to stop')
  } finally {
    busy.value = false
    await refresh()
    // udp_audio_record.py writes calls.json in its finally block; give it a
    // moment to flush the last call, then tell RecordingsList to reload.
    // server.py's UI did exactly this (setTimeout(loadRecordings, 1500)).
    setTimeout(() => { recordingsRefresh.value++ }, 1500)
  }
}
</script>
