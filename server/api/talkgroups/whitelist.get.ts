import { join } from 'node:path'
import { loadTalkgroups, loadWhitelist } from '~/server/utils/talkgroups'
import { referenceDir, whitelistPath } from '~/server/utils/paths'

export default defineEventHandler(() => {
  const ids = loadWhitelist(whitelistPath())
  const all = loadTalkgroups(join(referenceDir(), 'lwin_talkgroups.json'))

  return {
    success: true,
    data: {
      tgids: [...ids],
      talkgroups: all.filter(tg => ids.has(tg.tgid)),
    },
  }
})
