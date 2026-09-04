import { ref, onMounted, onUnmounted } from 'vue'
import type { Packet, PacketSummary } from '~/server/utils/queries'

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

/** How many strips the rail holds. The endpoint caps at 500 regardless. */
const PACKET_PAGE = 40

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

  let timer: ReturnType<typeof setInterval> | null = null

  async function load() {
    try {
      const res = await $fetch<PacketResponse>('/api/packets/list', {
        query: { limit: PACKET_PAGE },
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
    }
  }

  onMounted(() => {
    void load()
    timer = setInterval(() => { void load() }, PACKET_POLL_MS)
  })

  onUnmounted(() => {
    if (timer) clearInterval(timer)
    timer = null
  })

  return { rows, summary, loaded, error, load }
}
