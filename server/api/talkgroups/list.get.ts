import { listTalkgroups } from '~/server/utils/queries'

/**
 * The talkgroup roster, filtered.
 *
 * Two callers with different needs, one query. TalkgroupBrowser asks for a
 * whole area and paints a table; the bay's roster search (components/bay/
 * CommStack.vue) asks for `area=all&search=...&limit=n` because it is looking
 * up ONE talkgroup among 4,163 and has a panel a few dozen rows tall. Rather
 * than a second endpoint duplicating the filters, `limit` caps the rows and
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

  // `total` is the whole-DB count (4163), deliberately independent of `area`.
  // TalkgroupBrowser's footer uses the length of the array it holds, so BR
  // reads "601 of 601"; do not repoint it at this field. `matched` is the
  // filtered count before `limit`, which is the one a search result needs.
  return { success: true, data: rows, total, matched }
})
