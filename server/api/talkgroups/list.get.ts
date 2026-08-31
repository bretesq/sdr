import { join } from 'node:path'
import { loadTalkgroups, filterByArea, filterTalkgroups } from '~/server/utils/talkgroups'
import { referenceDir } from '~/server/utils/paths'

export default defineEventHandler((event) => {
  const q = getQuery(event)
  const area = q.area === 'all' ? 'all' : 'br'
  const category = q.category ? String(q.category) : undefined
  const text = q.text ? String(q.text) : undefined
  const enc = q.enc ? String(q.enc) : undefined

  const all = loadTalkgroups(join(referenceDir(), 'lwin_talkgroups.json'))
  const data = filterTalkgroups(filterByArea(all, area), { category, text, enc })

  // Entries carry tag and mode as well as tgid/alpha/desc/cat/enc — return them
  // whole. server.py returned both; tag is searched and mode shows "D enc".
  return { success: true, data, total: all.length }
})
