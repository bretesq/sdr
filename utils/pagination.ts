/**
 * Window arithmetic for the bay's two scrolling rails.
 *
 * Both rails GROW a window from the top rather than fetching page N at an
 * offset, because both update live and new rows arrive at the TOP. With offset
 * paging, a row arriving between two page fetches is returned twice and
 * another is never returned at all — duplicates and silent holes, neither
 * visible in the UI. Refetching from the top cannot drift.
 *
 * Extracted here because the two composables would otherwise carry the same
 * arithmetic twice, and because the cap semantics below are the part with a
 * real wrong answer available.
 */

/** How many rows to ask for when the rail is `pages` deep. */
export function windowSize(pages: number, pageSize: number): number {
  return Math.max(1, pages) * pageSize
}

/**
 * Is there another page to show, and room to show it?
 *
 * BOTH conditions matter and for different reasons. `loaded < total` is the
 * obvious one. `pages < maxPages` is the honest one: each rail has a ceiling —
 * the archive's is self-imposed (no server-side limit cap exists, so an
 * unbounded scroll would request all 13,000 calls in one response), and the
 * data rail's is the server's own 500-row cap. Past the ceiling the next
 * request would return the SAME rows, so a rail that ignored it would show
 * "reading more" forever against a list that never grew.
 */
export function hasMorePages(
  loaded: number, total: number, pages: number, maxPages: number,
): boolean {
  return loaded < total && pages < maxPages
}
