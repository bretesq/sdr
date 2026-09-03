import { listRecordings } from '~/server/utils/queries'
import type { CallEncryptionFilter } from '~/server/utils/queries'

/**
 * The only values `encState` may take. A Set, not a cast: the point is to
 * REJECT anything else rather than trust the query string.
 */
const ENC_STATES = new Set<string>(['all', 'open', 'encrypted'])

/**
 * Validate the filter, and THROW on an unrecognised value rather than ignoring
 * it.
 *
 * Ignoring is what `afterId` above does, and it is right there: a missing
 * cursor legitimately means "from the beginning". A filter is different. Drop a
 * misspelled `?encState=encrytped` and the route answers with all 13,390 rows
 * and a `total` that looks authoritative — the caller cannot tell the filter
 * was discarded, and reads the whole corpus as the filtered subset. That is the
 * same class of failure as a health check that reports success because the
 * thing it asked never ran. Better to refuse and say which values exist.
 */
function encState(raw: unknown): CallEncryptionFilter | undefined {
  if (raw === undefined) return undefined
  const v = String(raw)
  if (!ENC_STATES.has(v)) {
    throw createError({
      statusCode: 400,
      statusMessage: `Unknown encState "${v}". Use one of: ${[...ENC_STATES].join(', ')}`,
    })
  }
  return v as CallEncryptionFilter
}

/** "17094,17095" -> [17094, 17095]. Non-numeric entries are dropped. */
function parseTgids(raw: string): number[] {
  return raw
    .split(',')
    .map(s => Number.parseInt(s.trim(), 10))
    .filter(n => Number.isInteger(n))
}

export default defineEventHandler((event) => {
  const q = getQuery(event)

  // The cursor is validated on SHAPE — a run of digits — not on whether it
  // coerces to a number, and this is the one parameter here that is guarded.
  //
  // `?afterId=abc` parses to NaN, and NaN is not undefined, so it would be
  // bound: node:sqlite binds NaN as NULL, `id > NULL` is NULL, and the feed
  // returns zero rows forever with no exception and no log entry.
  //
  // Testing `Number.isInteger(Number(...))` instead looks equivalent and is
  // not. `Number('')` is 0, so `?afterId=` would become a real cursor of 0 —
  // and because the ordering below keys off the parameter being PRESENT, that
  // does not merely add a no-op `id > 0` predicate, it silently flips the page
  // into `c.id ASC` across the whole corpus. The same guard would also accept
  // `1e3` as cursor 1000 and coerce a repeated `?afterId=1&afterId=2` into
  // `"1,2"` → 12. A digit run cannot impersonate a number the way an empty
  // string can.
  //
  // `afterId=0` stays legal and means "cursor at the very beginning": absence
  // of the key is what means "no cursor", never the value being zero.
  const rawAfterId = q.afterId === undefined ? '' : String(q.afterId).trim()

  // Search, encryption and talkgroup filtering all happen in SQL now. The old
  // route shipped every row plus all 3,220 transcripts so the browser could
  // filter with String.includes; transcript matching is an FTS5 index lookup.
  const { rows, total, maxId } = listRecordings({
    search: q.search ? String(q.search) : undefined,
    enc: q.enc ? String(q.enc) : undefined,
    encState: encState(q.encState),
    tgid: q.tgid ? Number.parseInt(String(q.tgid), 10) : undefined,
    // Present-but-empty is meaningful: it matches nothing. So this checks for
    // the parameter's presence, not its truthiness — `tgids=` must not read as
    // "no filter".
    tgids: q.tgids !== undefined ? parseTgids(String(q.tgids)) : undefined,
    afterId: /^\d+$/.test(rawAfterId) ? Number(rawAfterId) : undefined,
    code: q.code ? String(q.code) : undefined,
    limit: q.limit ? Number.parseInt(String(q.limit), 10) : undefined,
    offset: q.offset ? Number.parseInt(String(q.offset), 10) : undefined,
  })

  // `total` counts rows matching the filters actually supplied, so on a feed
  // poll it means "calls committed since the cursor", not "calls in the
  // corpus". Do not render it as a corpus count without checking for afterId.
  return { success: true, data: rows, total, maxId }
})
