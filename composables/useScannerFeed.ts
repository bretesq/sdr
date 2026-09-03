import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import {
  createQueue, admit, prune, takeNext,
  type FeedCall, type QueueEntry, type ScannerQueue,
} from '~/utils/scannerQueue'
import type { ReceiverLayout } from '~/utils/receiverLayout'
import type { TalkgroupEncryptionVerdict } from '~/utils/talkgroupEncryption'

interface FollowedTalkgroup {
  tgid: number
  alpha: string | null
  desc: string | null
  cat: string | null
  recentCalls: number
  /**
   * What this talkgroup's recorded calls say about its encryption, derived
   * server-side (server/utils/queries.ts's followedTalkgroups, via
   * utils/talkgroupEncryption.ts). A closed verdict plus its counts — never
   * call rows, never a key.
   */
  encryption: TalkgroupEncryptionVerdict
}

interface FollowedResponse {
  success: boolean
  data: {
    talkgroups: FollowedTalkgroup[]
    heldKeyIds: number[]
    radioBusy: boolean
    tracked: boolean
    /** Epoch seconds the tracked session opened, or null when untracked. */
    sessionStartedAt: number | null
    /**
     * Seconds the tracked session was started with, or null when untracked
     * or the session has no recorded duration (an unbounded run). See
     * server/api/listen/followed.get.ts's own docstring.
     */
    sessionDurationSec: number | null
    /**
     * How many radios and voice receivers the capture is built from, or null
     * when the op25 config on disk is missing or unreadable. See
     * utils/receiverLayout.ts and server/api/listen/followed.get.ts.
     */
    receiverLayout: ReceiverLayout | null
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

/**
 * A clip must BEGIN within this long, or it is abandoned.
 *
 * Separate from the total deadline below because the two failures have very
 * different costs. A 32-second clip that never starts would burn 35 seconds
 * against the total deadline alone — longer than the default 30-second
 * staleness bound, so the entire queued backlog would prune while the feed sat
 * on one dead clip. Two seconds is far beyond what a ~70 KB file over a LAN
 * needs, and `playing` clears it the moment audio actually starts.
 */
const START_DEADLINE_MS = 2000

/**
 * Slack added to a clip's own duration for its total deadline.
 *
 * The asymmetry matters: a deadline that fires late costs dead air, one that
 * fires early truncates real audio — so this is deliberately generous.
 * Background-tab throttling only delays timers, which fails in the safe
 * direction.
 */
const CLIP_SLACK_MS = 3000

/** `dur` comes from the database and is not validated there. */
const MAX_CLIP_SEC = 60

/**
 * How often to re-fetch /api/listen/followed while a component is mounted.
 *
 * Before this, `load()` ran exactly once, in the consumer's onMounted — a
 * console left open across a stall would never learn radioBusy had gone
 * false; the operator would have to reload the page to see it (see
 * utils/captureStatus.ts for the stall this exists to surface). This is what
 * makes that live rather than reload-only.
 *
 * 20s, chosen against server/utils/session.ts's UNKNOWN_TOLERANCE_MS (60s):
 * comfortably coarse — a delegated session's liveness check is a real
 * control-API HTTP call, so this poll is exactly the "something polls" case
 * UNKNOWN_TOLERANCE_MS's wall-clock (not call-count) budget was corrected to
 * stay safe under. At 20s, roughly three polls fit inside one tolerance
 * window, so a session only closes on a control-API problem that outlasts
 * several consecutive checks, not a single bad one — while still being fast
 * enough that a stall reads within one or two polls of crossing
 * STALL_GRACE_MS (45s in captureStatus.ts), not many minutes later.
 */
const FOLLOWED_POLL_MS = 20_000

export function useScannerFeed() {
  const followed = ref<FollowedTalkgroup[]>([])
  const heldKeyIds = ref<number[]>([])
  const selected = ref<number[]>([])
  const armed = ref(false)
  /**
   * How long a call may wait before it is dropped rather than played late.
   *
   * Client state; the server has no opinion about it. Read defensively because
   * localStorage throws in some contexts rather than returning null.
   */
  const stalenessSec = ref(30)
  /**
   * False once a write has been refused — a private window, or a browser
   * blocking site data. Surfaced in the panel so the operator is told the
   * setting will not survive a reload, rather than discovering it later.
   *
   * Handled visibly rather than swallowed: a comment-only catch would leave
   * the failure invisible, and a console.warn would fire on every change of
   * the control.
   */
  const settingPersists = ref(true)

  const DEFAULT_STALENESS = 30

  /**
   * Read on the CLIENT only.
   *
   * Nuxt runs composable setup during server render too, where localStorage
   * does not exist. Reading it unguarded there throws, the catch sets
   * settingPersists false, and the server emits the "won't persist" warning —
   * which the client then contradicts after hydration. That is not a cosmetic
   * flash: server and client produce different DOM, which Vue reports as a
   * hydration mismatch. Guarding on import.meta.client means the server
   * renders the defaults and the client fills in the stored value.
   */
  function loadStaleness(): void {
    try {
      const saved = Number.parseInt(localStorage.getItem('scanner-staleness') ?? '', 10)
      stalenessSec.value = Number.isInteger(saved) && saved >= 10 && saved <= 300
        ? saved
        : DEFAULT_STALENESS
    } catch {
      // Reading was refused — a private window, or site data blocked. Nothing
      // was stored for us to honour, and writes will fail the same way.
      stalenessSec.value = DEFAULT_STALENESS
      settingPersists.value = false
    }
  }
  if (import.meta.client) loadStaleness()
  watch(stalenessSec, (v) => {
    try {
      localStorage.setItem('scanner-staleness', String(v))
      settingPersists.value = true
    } catch {
      settingPersists.value = false
    }
  })
  const skipped = ref(0)
  const nowPlaying = ref<FeedCall | null>(null)
  const streamOk = ref(false)
  const radioBusy = ref(false)
  const tracked = ref(false)
  /** Epoch seconds the tracked session opened, or null when untracked. */
  const sessionStartedAt = ref<number | null>(null)
  /** Seconds the tracked session was started with, or null. See FollowedResponse above. */
  const sessionDurationSec = ref<number | null>(null)
  /**
   * The receiver layout the capture was launched with, or null when no
   * capture has ever run on this checkout. See FollowedResponse above.
   *
   * Held here rather than fetched by the component for the same reason
   * `radioBusy`/`tracked` are: it arrives on this composable's existing
   * 20-second /api/listen/followed poll, from the same read as the liveness
   * signals it has to be captioned with.
   */
  const receiverLayout = ref<ReceiverLayout | null>(null)
  const error = ref('')

  const queue: ScannerQueue = createQueue()
  // NOT `ref(queue.entries)`: aliasing the live array here would leave this ref
  // pointing at queue state that mutates underneath Vue without notifying it.
  // `sync()` assigns a fresh array on every change instead.
  const entries = ref<QueueEntry[]>([])

  /**
   * Clips abandoned because playback failed, deliberately NOT folded into
   * `skipped`.
   *
   * `skipped` means "playable calls dropped for age" and Task 7 surfaces it as
   * the signal for whether the staleness bound is too tight. A clip that
   * stalled after playing most of its audio is a different event, and
   * `skipped` is the pure module's state — the transport does not own it.
   */
  const failed = ref(0)

  let lastSeenId = 0
  // Guards `arm` across its await, and serialises pumps. Without the first, a
  // double-click runs `arm` twice and orphans the first EventSource — never
  // closed, doubling the poll rate for the life of the page. Without the
  // second, two change-frames inside one fetch round-trip both query the same
  // `afterId`; a call the first pump already handed to `takeNext` is no longer
  // in `entries`, so `admit`'s dedupe misses it and it plays twice.
  let armInFlight = false
  let pumpInFlight = false
  // Bumped by `disarm`, so an `arm` still awaiting its seed fetch can tell it
  // was cancelled and must not resurrect the feed.
  let armGeneration = 0
  let startTimer: ReturnType<typeof setTimeout> | null = null
  let clipTimer: ReturnType<typeof setTimeout> | null = null
  let es: EventSource | null = null
  // Set once in onMounted below and cleared once in onUnmounted — never
  // reassigned anywhere else, so this can't become the same orphaned-timer
  // bug `arm`'s armInFlight guard exists to prevent (a double EventSource
  // that's never closed, doubling the poll rate for the life of the page).
  // The guarantee is structural, not "only one caller happens to exist
  // today": `followedPollTimer` is a variable local to THIS invocation of
  // useScannerFeed() (a fresh closure per call), and Vue's onMounted fires
  // at most once per component INSTANCE. So even a second component that
  // called useScannerFeed() (components/ScannerFeed.vue does exist in this
  // tree, though nothing currently renders it — see pages/index.vue, the
  // only page) would get its own independent closure and its own single
  // onMounted firing once, not a second interval racing this one. What
  // would actually double a timer — calling useScannerFeed() a second time
  // for the SAME already-mounted instance, or this file's onMounted running
  // twice for one instance — is not something Vue's lifecycle permits.
  let followedPollTimer: ReturnType<typeof setInterval> | null = null
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
      sessionStartedAt.value = res.data.sessionStartedAt
      sessionDurationSec.value = res.data.sessionDurationSec
      receiverLayout.value = res.data.receiverLayout
      error.value = ''
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Could not load talkgroups'
    }
  }

  async function pump(): Promise<void> {
    // An armed feed with nothing selected must be silent. The query layer also
    // refuses an empty selection; this avoids the round trip.
    if (!armed.value || selected.value.length === 0) return
    if (pumpInFlight) return
    pumpInFlight = true
    try {
      await pumpOnce()
    } finally {
      pumpInFlight = false
    }
  }

  async function pumpOnce(): Promise<void> {
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
    // Clear on success, or a single transient failure stays on screen forever.
    error.value = ''

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

  function clearTimers(): void {
    if (startTimer) { clearTimeout(startTimer); startTimer = null }
    if (clipTimer) { clearTimeout(clipTimer); clipTimer = null }
  }

  /**
   * The ONE way a clip ends. Clears the interlock, then advances.
   *
   * Every advance path must go through here. `nowPlaying` is the interlock
   * `playIfIdle` tests, so clearing it inline and calling `playIfIdle`
   * separately — or in the wrong order — makes the handler early-return and
   * reintroduces the wedge this whole mechanism exists to prevent.
   */
  function finishClip(didFail: boolean): void {
    clearTimers()
    if (didFail) failed.value += 1
    nowPlaying.value = null
    playIfIdle()
  }

  function playIfIdle(): void {
    if (!armed.value) return
    if (!audio) return
    // The interlock is OUR state, never the element's.
    //
    // `audio.paused` cannot serve here: `play()` resolves only when playback
    // begins, so a clip that never starts leaves `paused` false forever,
    // `ended` never fires, and every later tick early-returns — the feed goes
    // permanently off the air with no error anywhere. That failure was
    // observed during this task's implementation and misread as environmental.
    if (nowPlaying.value !== null) return

    const call = takeNext(queue, Date.now(), stalenessSec.value * 1000)
    sync()
    if (!call) return

    nowPlaying.value = call
    audio.src = `/api/recordings/${encodeURIComponent(call.file)}`

    // Both timers verify they still own the current clip before acting, so a
    // stale timer surviving any exit path is inert rather than cutting off the
    // clip that just started.
    startTimer = setTimeout(() => {
      if (nowPlaying.value?.id !== call.id) return
      finishClip(true)
    }, START_DEADLINE_MS)

    const dur = Number.isFinite(call.dur)
      ? Math.max(0, Math.min(call.dur, MAX_CLIP_SEC))
      : 0
    clipTimer = setTimeout(() => {
      if (nowPlaying.value?.id !== call.id) return
      finishClip(true)
    }, dur * 1000 + CLIP_SLACK_MS)

    audio.play().catch((e: unknown) => {
      if (nowPlaying.value?.id !== call.id) return
      // `disarm`'s pause() rejects a pending play() with AbortError. That is
      // us stopping deliberately, not a clip failure — surfacing it puts a red
      // banner on screen every time the operator presses Stop.
      if (e instanceof DOMException && e.name === 'AbortError') return
      error.value = e instanceof Error ? e.message : 'Playback failed'
      finishClip(true)
    })
  }

  /**
   * One `<audio>` element for the life of the composable, created and ACTIVATED
   * inside whichever click reaches it first — arming the feed, or reviewing a
   * filed strip.
   *
   * Construction alone does not unlock an element on WebKit: iOS and Safari
   * bless a media element only when play() or load() is invoked *during* a user
   * gesture. Without the load() here the first play() happens on a later SSE
   * tick, outside any gesture, and every clip throws NotAllowedError. Desktop
   * Chrome and Firefox grant document-level sticky activation from the click,
   * which is exactly why this failure hides during desktop testing.
   */
  function ensureAudio(): void {
    if (audio) return
    audio = new Audio()
    audio.load()

    audio.addEventListener('playing', () => {
      // Playback actually began; only the total deadline still applies.
      if (startTimer) { clearTimeout(startTimer); startTimer = null }
    })
    audio.addEventListener('ended', () => finishClip(false))
    audio.addEventListener('error', () => {
      // disarm's removeAttribute('src') + load() fires an empty-src error on
      // Chrome. That is teardown, not a clip failure, and advancing on it
      // would make Stop pull the next clip on its way out.
      if (nowPlaying.value === null) return
      finishClip(true)
    })
  }

  async function arm(): Promise<void> {
    // Re-entry guard. Two rapid Play clicks would otherwise run this twice and
    // orphan the first EventSource — unreachable, never closed, doubling the
    // poll rate and holding the server's 1s data_version timer open until the
    // page unloads.
    if (armInFlight || armed.value) return
    armInFlight = true
    const generation = ++armGeneration

    try {
    // Created inside the click handler so the user gesture unlocks THIS
    // element — and ACTIVATED here too, which construction alone does not do.
    //
    // WebKit blesses a media element only when play() or load() is invoked
    // during the gesture. Without this load(), the first play() happens on a
    // later SSE tick, outside any gesture, on an element iOS/Safari has never
    // seen a gesture-driven call on — every clip then throws NotAllowedError
    // and the feed never plays at all. Desktop Chrome and Firefox grant
    // document-level sticky activation from the click, which is exactly why
    // this hides during desktop testing.
    ensureAudio()

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

    // Cancelled while awaiting the seed. Without this, Play -> Stop during the
    // fetch resumes here and leaves the feed armed with a live EventSource
    // after the operator asked it to stop.
    if (generation !== armGeneration) return

    // length = 0 rather than a fresh array: prune and takeNext both mutate in
    // place, and the queue's identity contract says the array a caller holds
    // stays valid.
    queue.entries.length = 0
    queue.skipped = 0
    failed.value = 0
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
    } finally {
      armInFlight = false
    }
  }

  function disarm(): void {
    // Invalidates any arm() still awaiting its seed fetch.
    armGeneration++
    armed.value = false
    streamOk.value = false
    clearTimers()
    es?.close()
    es = null
    // Cleared BEFORE the teardown below, so the error listener sees a null
    // nowPlaying and treats the empty-src error as teardown rather than a
    // failed clip.
    nowPlaying.value = null
    if (audio) {
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
    }
    queue.entries.length = 0
    sync()
  }

  /**
   * Play one already-filed call on demand, outside the live queue.
   *
   * Reviewing a filed strip and listening to the live feed are the same
   * activity through the same speaker, so they share one element rather than
   * competing: a second `<audio>` would let the operator hear two calls at
   * once and would need its own gesture unlock. Review therefore takes the
   * element over — the live queue keeps filling behind it and resumes at the
   * next tick once this clip ends.
   *
   * Called from a click, so the gesture unlocks the element on first use even
   * when the feed was never armed.
   */
  function review(call: FeedCall): void {
    ensureAudio()
    if (!audio) return
    clearTimers()
    nowPlaying.value = call
    audio.src = `/api/recordings/${encodeURIComponent(call.file)}`

    const dur = Number.isFinite(call.dur)
      ? Math.max(0, Math.min(call.dur, MAX_CLIP_SEC))
      : 0
    clipTimer = setTimeout(() => {
      if (nowPlaying.value?.id !== call.id) return
      finishClip(true)
    }, dur * 1000 + CLIP_SLACK_MS)

    audio.play().catch((e: unknown) => {
      if (nowPlaying.value?.id !== call.id) return
      if (e instanceof DOMException && e.name === 'AbortError') return
      error.value = e instanceof Error ? e.message : 'Playback failed'
      finishClip(true)
    })
  }

  // Keeps radioBusy/tracked/sessionStartedAt (and the talkgroup list) live
  // for as long as this component stays mounted, independent of whether the
  // feed is armed — the operator needs to see a stall even when nobody has
  // pressed Play. onMounted runs once per component instance (never during
  // SSR, so no server-side timer leaks), which is what makes the "created
  // twice" hazard structurally impossible rather than merely unlikely — see
  // followedPollTimer's own comment above.
  onMounted(() => {
    followedPollTimer = setInterval(() => { void load() }, FOLLOWED_POLL_MS)
  })
  onUnmounted(() => {
    if (followedPollTimer) { clearInterval(followedPollTimer); followedPollTimer = null }
  })
  onUnmounted(disarm)

  return {
    followed, heldKeyIds, selected, armed, stalenessSec, settingPersists,
    entries, skipped, failed, nowPlaying, streamOk, radioBusy, tracked, sessionStartedAt, sessionDurationSec,
    receiverLayout, error,
    load, arm, disarm, review,
  }
}
