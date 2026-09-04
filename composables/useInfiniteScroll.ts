import { ref, onMounted, onUnmounted } from 'vue'

/**
 * Call `onLoadMore` when a sentinel element scrolls into view.
 *
 * Put `sentinel` on an empty element at the END of a scrolling list, and
 * `root` on the element that actually scrolls.
 *
 * ROOT IS NOT OPTIONAL IN PRACTICE. Both rails in this console scroll inside a
 * div with `overflow-y: auto`, not the page. An observer left on the default
 * root (the viewport) still works -- intersection is clipped by the scrolling
 * ancestor -- but it fires against the viewport's geometry, so `rootMargin`
 * measures from the wrong edge and the prefetch distance is whatever the rail
 * happens to sit at on screen. Passing the scroller makes the margin mean what
 * it says.
 */
export function useInfiniteScroll(onLoadMore: () => void | Promise<void>) {
  const sentinel = ref<HTMLElement | null>(null)
  const root = ref<HTMLElement | null>(null)
  let io: IntersectionObserver | null = null

  onMounted(() => {
    // Guarded because this composable runs during SSR hydration too, where
    // there is no IntersectionObserver and no layout to observe.
    if (!sentinel.value || typeof IntersectionObserver === 'undefined') return
    io = new IntersectionObserver(
      (entries) => {
        if (entries.some(e => e.isIntersecting)) void onLoadMore()
      },
      {
        root: root.value ?? null,
        // Start fetching before the sentinel is actually visible, so the next
        // page is usually there by the time the reader reaches it.
        rootMargin: '300px',
      },
    )
    io.observe(sentinel.value)
  })

  onUnmounted(() => {
    io?.disconnect()
    io = null
  })

  return { sentinel, root }
}
