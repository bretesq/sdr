import { listTalkgroups } from '~/server/utils/queries'

/**
 * The talkgroup roster, filtered.
 *
 * Two shapes of request, one query. An uncapped request asks for a whole area
 * and gets every row; the bay's roster search (components/bay/CommStack.vue)
 * asks for `area=all&search=...&limit=n` because it is looking up ONE
 * talkgroup among 4,163 and has a panel a few dozen rows tall. Rather than a
 * second endpoint duplicating the filters, `limit` caps the rows and
 * `matched` reports how many the filters actually hit, so the bay can say
 * "showing 40 of 900" instead of implying it found forty.
 */
export default defineEventHandler((event) => {
  const q = getQuery(event)

  // Bounded and integral before it reaches the query: an unparseable or
  // negative `limit` must fall back to "no cap" rather than silently slicing
  // to zero rows, which would look exactly like "no matches".
  const rawLimit = q.limit === undefined ? Number.NaN : Number(q.limit)
  const limit = Number.isInteger(rawLimit) && rawLimit > 0
    ? Math.min(rawLimit, 500)
    : undefined

  const { rows, total, matched } = listTalkgroups({
    area: q.area === 'all' ? 'all' : 'br',
    category: q.category ? String(q.category) : undefined,
    enc: q.enc ? String(q.enc) : undefined,
    search: q.search ? String(q.search) : undefined,
    limit,
  })

  // `total` is the whole-DB count (4163), deliberately independent of `area`
  // and of the filters — no caller renders it today. `matched` is the filtered
  // count before `limit`, and that is the one a search result needs: the bay's
  // roster footer pairs it with the length of the array it holds to say
  // "12 shown · 384 matched" (CommStack.vue's `rosterNote`). A footer must not
  // be repointed at `total`, which would report the corpus rather than the
  // search.
  return { success: true, data: rows, total, matched }
})
