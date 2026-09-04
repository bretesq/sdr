import { ref, computed, onMounted, onUnmounted } from 'vue'
import type { Packet, PacketSummary } from '~/server/utils/queries'
import { windowSize, hasMorePages } from '~/utils/pagination'

/**
 * Poll interval for the data rail.
 *
 * Slower than the call feed on purpose: packet data arrives at roughly one
 * readable datagram a minute, against several calls a minute on voice, so a
 * faster poll would spend requests to re-render an unchanged list. 30 s also
 * keeps it out of step with the 20 s followed-talkgroup poll, so the two do not
 * land together on every third tick.
 */
const PACKET_POLL_MS = 30_000

/** How many strips one page holds. */
const PACKET_PAGE = 40

/**
 * Ceiling on how far the rail scrolls back, in pages.
 *
 * 500 is `listPackets`' own server-side cap, so asking for more would silently
 * return 500 and the rail would look like it had reached the end when it had
 * only reached the cap. Stopping at the same number keeps `hasMore` honest.
 */
const MAX_PAGES = Math.floor(500 / PACKET_PAGE)

interface PacketResponse {
  success: boolean
  data: { rows: Packet[], total: number, maxId: number, summary: PacketSummary }
}

/**
 * The packet-data feed: recent SNDCP messages plus the headline counts.
 *
 * Read-only. Unlike the call feed there is nothing to arm, select or play —
 * this is traffic the system sends to radios, and the console is only ever a
 * bystander to it.
 */
export function usePacketFeed() {
  const rows = ref<Packet[]>([])
  const summary = ref<PacketSummary | null>(null)
  /** Null until the first load resolves, so the rail can say "reading" once. */
  const loaded = ref(false)
  const error = ref<string | null>(null)

  /**
   * How many pages deep the rail is scrolled. The window GROWS -- every load
   * fetches `pages * PACKET_PAGE` from the top -- rather than fetching page N
   * at an offset.
   *
   * Correctness, not convenience. This feed polls every 30 s and new PDUs are
   * inserted at the TOP, so with offset paging a message arriving between two
   * page fetches pushes one row from page 1 into page 2 (returned twice) and
   * pushes one row off the end of page 2 (never returned). Duplicates and
   * silent holes, neither visible in the UI. Refetching from the top cannot
   * drift.
   */
  const pages = ref(1)
  const loading = ref(false)

  /** More to show, and room within the server's own cap to show it. */
  const hasMore = computed(() =>
    summary.value !== null
    && hasMorePages(rows.value.length, summary.value.total, pages.value, MAX_PAGES))

  let timer: ReturnType<typeof setInterval> | null = null

  async function load() {
    loading.value = true
    try {
      const res = await $fetch<PacketResponse>('/api/packets/list', {
        query: { limit: windowSize(pages.value, PACKET_PAGE) },
      })
      rows.value = res.data.rows
      summary.value = res.data.summary
      error.value = null
    }
    catch (e) {
      // Keep the last good rows on screen and say so. Blanking the rail on a
      // transient fetch failure would read as "the data stopped", which is a
      // different and much more alarming fact than "we could not refresh".
      error.value = e instanceof Error ? e.message : 'could not reach the console'
    }
    finally {
      loaded.value = true
      loading.value = false
    }
  }

  /**
   * Show one more page.
   *
   * `loading` guards re-entry: a scroll sentinel fires repeatedly while it
   * remains on screen, so without this one flick queues several identical
   * growing requests.
   */
  async function loadMore(): Promise<void> {
    if (loading.value || !hasMore.value) return
    pages.value += 1
    await load()
  }

  onMounted(() => {
    void load()
    timer = setInterval(() => { void load() }, PACKET_POLL_MS)
  })

  onUnmounted(() => {
    if (timer) clearInterval(timer)
    timer = null
  })

  return { rows, summary, loaded, error, load, loadMore, hasMore, loading }
}
