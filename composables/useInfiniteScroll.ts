import { ref, watch, onUnmounted } from 'vue'

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
 *
 * ATTACHES ON A WATCHER, NOT ON MOUNT. Both call sites guard the sentinel with
 * `v-if="rows.length"`, because a "loading more" marker under an empty list is
 * nonsense. So at mount the sentinel does not exist yet -- the rows arrive from
 * a fetch a moment later. An onMounted-only version read `sentinel.value`,
 * found null, returned, and never looked again: observe() was called zero times
 * in the lifetime of the page, on either rail, and neither list ever paged.
 *
 * That failure is invisible from the outside. There is no error, the sentinel
 * is in the DOM when you inspect it, and the list still scrolls -- it just
 * stops at the first window and looks like the end of the data. Unit tests do
 * not catch it either: they exercise the window arithmetic, which was correct.
 * The wiring was the broken part, and only a real browser shows it.
 */
export function useInfiniteScroll(onLoadMore: () => void | Promise<void>) {
  const sentinel = ref<HTMLElement | null>(null)
  const root = ref<HTMLElement | null>(null)
  let io: IntersectionObserver | null = null

  function attach() {
    io?.disconnect()
    io = null
    // Guarded because this composable runs during SSR too, where there is no
    // IntersectionObserver and no layout to observe.
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
  }

  // Both refs, because `root` is fixed at construction: an observer built
  // while the scroller was still null would measure rootMargin against the
  // viewport for the rest of the page's life.
  watch([sentinel, root], attach, { flush: 'post', immediate: true })

  onUnmounted(() => {
    io?.disconnect()
    io = null
  })

  return { sentinel, root }
}
