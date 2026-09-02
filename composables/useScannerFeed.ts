import { ref, computed, onUnmounted } from 'vue'
import {
  createQueue, admit, prune, takeNext,
  type FeedCall, type ScannerQueue,
} from '~/utils/scannerQueue'

interface FollowedTalkgroup {
  tgid: number
  alpha: string | null
  desc: string | null
  cat: string | null
  recentCalls: number
}

interface FollowedResponse {
  success: boolean
  data: {
    talkgroups: FollowedTalkgroup[]
    heldKeyIds: number[]
    radioBusy: boolean
    tracked: boolean
    whitelistMtime: number | null
  }
}

interface ListResponse {
  success: boolean
  data: FeedCall[]
  total: number
  maxId: number
}

/**
 * How many calls to pull per SSE tick.
 *
 * At roughly 4 calls a minute a page is never close to full. If one ever is —
 * after a long disconnect — the query returns the oldest pending rows, this
 * drains them a page per tick, and `prune` discards whatever is already older
 * than the staleness bound. No special case needed; see pump().
 */
const PAGE = 500

export function useScannerFeed() {
  const followed = ref<FollowedTalkgroup[]>([])
  const heldKeyIds = ref<number[]>([])
  const selected = ref<number[]>([])
  const armed = ref(false)
  const stalenessSec = ref(30)
  /** Task 7 gives this meaning; declared here so the return shape is stable. */
  const settingPersists = ref(true)
  const skipped = ref(0)
  const nowPlaying = ref<FeedCall | null>(null)
  const streamOk = ref(false)
  const radioBusy = ref(false)
  const tracked = ref(false)
  const error = ref('')

  const queue: ScannerQueue = createQueue()
  const entries = ref(queue.entries)

  let lastSeenId = 0
  let es: EventSource | null = null
  // ONE element, created on the first arm and reused for every clip.
  //
  // Browsers gate autoplay on a user gesture and the unlock attaches to the
  // element that gesture reached. A fresh `new Audio()` per call loses it, and
  // the feed goes silent after the first clip on Safari and iOS.
  let audio: HTMLAudioElement | null = null

  const selectedSet = computed(() => new Set(selected.value))
  const heldSet = computed(() => new Set(heldKeyIds.value))

  /** Mirror the plain queue object into the ref the template renders. */
  function sync(): void {
    entries.value = [...queue.entries]
    skipped.value = queue.skipped
  }

  async function load(): Promise<void> {
    try {
      const res = await $fetch<FollowedResponse>('/api/listen/followed')
      followed.value = res.data.talkgroups
      heldKeyIds.value = res.data.heldKeyIds
      radioBusy.value = res.data.radioBusy
      tracked.value = res.data.tracked
      error.value = ''
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Could not load talkgroups'
    }
  }

  async function pump(): Promise<void> {
    // An armed feed with nothing selected must be silent. The query layer also
    // refuses an empty selection; this avoids the round trip.
    if (!armed.value || selected.value.length === 0) return
    let res: ListResponse
    try {
      res = await $fetch<ListResponse>('/api/recordings/list', {
        query: {
          afterId: lastSeenId,
          tgids: selected.value.join(','),
          limit: PAGE,
        },
      })
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Feed fetch failed'
      return
    }

    // Rows arrive oldest-first because the query orders by rowid whenever
    // `afterId` is set, so they are admitted in the order the calls finished
    // and no reordering is needed here.
    //
    // A truncated page needs no special handling either: it is a prefix of the
    // pending set, so advancing to the last row received and letting the next
    // tick continue drains the backlog without losing a row. Anything in that
    // backlog older than the staleness bound is dropped by `prune` on arrival,
    // which is what stops a long absence from replaying hours of audio.
    for (const row of res.data) {
      lastSeenId = Math.max(lastSeenId, row.id)
      admit(queue, row, selectedSet.value, heldSet.value)
    }
    prune(queue, Date.now(), stalenessSec.value * 1000)
    sync()
    playIfIdle()
  }

  function playIfIdle(): void {
    if (!audio || !audio.paused) return
    const call = takeNext(queue, Date.now(), stalenessSec.value * 1000)
    sync()
    if (!call) {
      nowPlaying.value = null
      return
    }
    nowPlaying.value = call
    audio.src = `/api/recordings/${encodeURIComponent(call.file)}`
    audio.play().catch((e: unknown) => {
      // Autoplay refused, or the file vanished. Drop this clip and move on
      // rather than wedging the queue on it.
      error.value = e instanceof Error ? e.message : 'Playback failed'
      nowPlaying.value = null
    })
  }

  async function arm(): Promise<void> {
    // Created inside the click handler so the user gesture unlocks THIS element.
    if (!audio) {
      audio = new Audio()
      audio.addEventListener('ended', playIfIdle)
    }

    // Seed the cursor at arm time, not at mount: starting from MAX(id) means
    // arming the feed starts from now instead of replaying the whole corpus,
    // and taking it here closes the window where calls land while the panel
    // sits open but unarmed.
    try {
      const res = await $fetch<ListResponse>('/api/recordings/list', {
        query: { limit: 1 },
      })
      lastSeenId = res.maxId
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Could not seed the feed cursor'
      return
    }

    queue.entries = []
    queue.skipped = 0
    sync()
    armed.value = true

    // EventSource reconnects on its own after a drop, which is most of why
    // this is SSE and not a hand-rolled fetch loop. The id cursor means a drop
    // costs latency, not calls.
    es = new EventSource('/api/recordings/stream')
    es.onopen = () => { streamOk.value = true }
    es.onerror = () => { streamOk.value = false }
    es.onmessage = () => {
      streamOk.value = true
      void pump()
    }
  }

  function disarm(): void {
    armed.value = false
    streamOk.value = false
    es?.close()
    es = null
    if (audio) {
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
    }
    nowPlaying.value = null
    queue.entries = []
    sync()
  }

  onUnmounted(disarm)

  return {
    followed, heldKeyIds, selected, armed, stalenessSec, settingPersists,
    entries, skipped, nowPlaying, streamOk, radioBusy, tracked, error,
    load, arm, disarm,
  }
}
