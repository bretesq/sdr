import { listTalkgroups, whitelistTgids } from '~/server/utils/queries'

export default defineEventHandler(() => {
  // The whitelist stays a FILE: op25 reads it directly, and scripts/
  // lwin_listen.sh regenerates it via make_whitelist.py on every run. The
  // database is the source of truth for what talkgroups EXIST; this file is
  // the record of which ones the current session follows.
  //
  // Parsed by queries.ts's whitelistTgids() rather than here, so this route,
  // followedTalkgroups() and every roster search agree about what the file
  // says — they used to each parse it their own way. See that function.
  const ids = new Set(whitelistTgids())
  const { rows } = listTalkgroups({ area: 'all' })

  return {
    success: true,
    data: { tgids: [...ids], talkgroups: rows.filter(t => ids.has(t.tgid)) },
  }
})
