import { ref, computed, watch, onUnmounted } from 'vue'
import type { CodeMention } from '~/utils/tencodeSegments'

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
          limit: PAGE,
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

  watch(search, () => {
    if (debounce) clearTimeout(debounce)
    debounce = setTimeout(() => { void load() }, 220)
  })

  // Undebounced: a filter is a discrete click, not typing, and `generation`
  // already discards a stale response that resolves after a newer one — so a
  // fast switch cannot render the previous filter's rows.
  watch(encState, () => { void load() })

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

  return { rows, total, search, encState, loading, error, load, watchCorpus, stop, filed }
}
