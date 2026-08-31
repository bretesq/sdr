import { listTalkgroups } from '~/server/utils/queries'

export default defineEventHandler((event) => {
  const q = getQuery(event)

  const { rows, total } = listTalkgroups({
    area: q.area === 'all' ? 'all' : 'br',
    category: q.category ? String(q.category) : undefined,
    enc: q.enc ? String(q.enc) : undefined,
    search: q.search ? String(q.search) : undefined,
  })

  // `total` is the whole-DB count (4163), deliberately independent of `area`.
  // TalkgroupBrowser's footer uses the length of the array it holds, so BR
  // reads "601 of 601"; do not repoint it at this field.
  return { success: true, data: rows, total }
})
