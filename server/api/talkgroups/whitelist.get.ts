import { readFileSync } from 'node:fs'
import { whitelistPath } from '~/server/utils/paths'
import { listTalkgroups } from '~/server/utils/queries'

export default defineEventHandler(() => {
  // The whitelist stays a FILE: op25 reads it directly, and scripts/
  // lwin_listen.sh regenerates it via make_whitelist.py on every run. The
  // database is the source of truth for what talkgroups EXIST; this file is
  // the record of which ones the current session follows.
  let tgids: number[] = []
  try {
    tgids = readFileSync(whitelistPath(), 'utf-8')
      .split('\n')
      .map(l => Number.parseInt(l.trim().split(/[\s,]/)[0], 10))
      .filter(n => Number.isFinite(n))
  } catch {
    // Absent before the first run. An empty whitelist is the honest answer.
  }

  const ids = new Set(tgids)
  const { rows } = listTalkgroups({ area: 'all' })

  return {
    success: true,
    data: { tgids: [...ids], talkgroups: rows.filter(t => ids.has(t.tgid)) },
  }
})
