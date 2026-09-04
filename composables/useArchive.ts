import { ref, computed, watch, onUnmounted } from 'vue'
import type { CodeMention } from '~/utils/tencodeSegments'
import { windowSize, hasMorePages } from '~/utils/pagination'

/**
 * The filed half of the bay.
 *
 * The live feed and the archive are one surface in this design — a strip that
 * finishes playing files downward and becomes history — so this composable
 * exists only to keep the rail supplied below the fold. It holds no playback
 * state and owns no audio.
 *
 * It rides the same SSE change-feed the live path uses, but reacts to it
 * differently: the live path fetches only what is newer than its cursor, while
 * this re-runs the operator's current query, because a new call can land
 * anywhere in a filtered result, not only at the top.
 */

export interface ArchiveCall {
  id: number
  file: string
  tgid: number | null
  alpha: string | null
  desc: string | null
  cat: string | null
  start: number
  dur: number
  endedAt: number | null
  transcript: string | null
  /**
   * The normalised transcript and the codes found in it. Both already ride the
   * /api/recordings/list response -- `Recording` has carried them since the
   * ten-code work -- but this type stopped at `keyid`, so the strip could not
   * see them. Declaring them is the whole change on this side.
   */
  transcriptNorm: string | null
  codes: CodeMention[]
  algid: number | null
  keyid: number | null
}

interface ListResponse {
  success: boolean
  data: ArchiveCall[]
  total: number
  maxId: number
}

const PAGE = 120

/**
 * Ceiling on how far the rail will scroll back, in pages.
 *
 * Not a performance guess: `listRecordings` has NO server-side limit cap
 * (`q.limit ?? 5000`), so without a bound here a long scroll session would ask
 * for the whole 13,000-call corpus in one response and render it all.
 */
const MAX_PAGES = 20

export function useArchive() {
  const rows = ref<ArchiveCall[]>([])
  const total = ref(0)
  const search = ref('')
  /**
   * Per-call encryption filter, applied in SQL over the whole corpus.
   *
   * Server-side on purpose: the rail pages, so filtering `rows` here would
   * filter the loaded window and report it as the corpus — the archive is
   * 13,000+ calls and a page is `PAGE`.
   */
  const encState = ref<'all' | 'open' | 'encrypted'>('all')
  const loading = ref(false)
  const error = ref('')

  /**
   * How many pages deep the rail is scrolled. The window GROWS -- `load()`
   * always fetches `pages * PAGE` from the top -- rather than fetching page N
   * at an offset.
   *
   * That is deliberate and it is about correctness, not simplicity. This list
   * updates live: `watchCorpus` reloads on every new call, and a new call is
   * inserted at the TOP. With offset paging, a row arriving between "fetch
   * rows 0-119" and "fetch rows 120-239" pushes row 119 down into the second
   * page, so it is returned twice and the row that should have been at 239 is
   * never returned at all. Duplicates and silent holes, both invisible.
   * Refetching from the top cannot drift.
   */
  const pages = ref(1)

  /** More to show, and room to show it. */
  const hasMore = computed(() =>
    hasMorePages(rows.value.length, total.value, pages.value, MAX_PAGES))

  let debounce: ReturnType<typeof setTimeout> | null = null
  let es: EventSource | null = null
  /** Rejects a stale response that resolves after a newer query was issued. */
  let generation = 0

  async function load(): Promise<void> {
    const mine = ++generation
    loading.value = true
    try {
      const res = await $fetch<ListResponse>('/api/recordings/list', {
        query: {
          search: search.value.trim() || undefined,
          // Omitted when 'all' so the common case sends no filter at all.
          encState: encState.value === 'all' ? undefined : encState.value,
          limit: windowSize(pages.value, PAGE),
        },
      })
      if (mine !== generation) return
      rows.value = res.data
      total.value = res.total
      error.value = ''
    } catch (e) {
      if (mine !== generation) return
      error.value = e instanceof Error ? e.message : 'Could not read the archive'
    } finally {
      if (mine === generation) loading.value = false
    }
  }

  /**
   * Show one more page.
   *
   * `loading` guards re-entry: a scroll sentinel fires repeatedly while it
   * stays on screen, and without this a single flick would queue several
   * identical growing requests.
   */
  async function loadMore(): Promise<void> {
    if (loading.value || !hasMore.value) return
    pages.value += 1
    await load()
  }

  // A new query is a new corpus: keep the deep window and the rail opens
  // scrolled into results the user has not seen and cannot scroll above.
  watch(search, () => {
    pages.value = 1
    if (debounce) clearTimeout(debounce)
    debounce = setTimeout(() => { void load() }, 220)
  })

  // Undebounced: a filter is a discrete click, not typing, and `generation`
  // already discards a stale response that resolves after a newer one — so a
  // fast switch cannot render the previous filter's rows.
  watch(encState, () => { pages.value = 1; void load() })

  function watchCorpus(): void {
    if (es) return
    es = new EventSource('/api/recordings/stream')
    es.onmessage = () => { void load() }
  }

  function stop(): void {
    if (debounce) { clearTimeout(debounce); debounce = null }
    es?.close()
    es = null
  }

  onUnmounted(stop)

  /** Filed strips, minus anything the live rail is already holding. */
  function filed(excludeIds: ReadonlySet<number>) {
    return computed(() => rows.value.filter(r => !excludeIds.has(r.id)))
  }

  return {
    rows, total, search, encState, loading, error, load, loadMore,
    hasMore, watchCorpus, stop, filed,
  }
}
