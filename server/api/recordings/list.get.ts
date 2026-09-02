import { listRecordings } from '~/server/utils/queries'

/** "17094,17095" -> [17094, 17095]. Non-numeric entries are dropped. */
function parseTgids(raw: string): number[] {
  return raw
    .split(',')
    .map(s => Number.parseInt(s.trim(), 10))
    .filter(n => Number.isInteger(n))
}

export default defineEventHandler((event) => {
  const q = getQuery(event)

  // Search, encryption and talkgroup filtering all happen in SQL now. The old
  // route shipped every row plus all 3,220 transcripts so the browser could
  // filter with String.includes; transcript matching is an FTS5 index lookup.
  const { rows, total, maxId } = listRecordings({
    search: q.search ? String(q.search) : undefined,
    enc: q.enc ? String(q.enc) : undefined,
    tgid: q.tgid ? Number.parseInt(String(q.tgid), 10) : undefined,
    // Present-but-empty is meaningful: it matches nothing. So this checks for
    // the parameter's presence, not its truthiness — `tgids=` must not read as
    // "no filter".
    tgids: q.tgids !== undefined ? parseTgids(String(q.tgids)) : undefined,
    afterId: q.afterId ? Number.parseInt(String(q.afterId), 10) : undefined,
    code: q.code ? String(q.code) : undefined,
    limit: q.limit ? Number.parseInt(String(q.limit), 10) : undefined,
    offset: q.offset ? Number.parseInt(String(q.offset), 10) : undefined,
  })

  return { success: true, data: rows, total, maxId }
})
