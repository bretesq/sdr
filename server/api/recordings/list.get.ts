import { listRecordings } from '~/server/utils/queries'

export default defineEventHandler((event) => {
  const q = getQuery(event)

  // Search, encryption and talkgroup filtering all happen in SQL now. The old
  // route shipped every row plus all 3,220 transcripts so the browser could
  // filter with String.includes; transcript matching is an FTS5 index lookup.
  const { rows, total } = listRecordings({
    search: q.search ? String(q.search) : undefined,
    enc: q.enc ? String(q.enc) : undefined,
    tgid: q.tgid ? Number.parseInt(String(q.tgid), 10) : undefined,
    limit: q.limit ? Number.parseInt(String(q.limit), 10) : undefined,
    offset: q.offset ? Number.parseInt(String(q.offset), 10) : undefined,
  })

  return { success: true, data: rows, total }
})
