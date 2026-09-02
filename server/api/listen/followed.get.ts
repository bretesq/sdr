import { statSync } from 'node:fs'
import { followedTalkgroups } from '~/server/utils/queries'
import { heldKeyIds } from '~/server/utils/keys'
import { isRadioBusy } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'
import { whitelistPath } from '~/server/utils/paths'

/**
 * Everything the Scanner Feed panel needs to arm itself, in one request.
 *
 * Read-only, so no CSRF guard: unlike /api/listen/start and /stop this drives
 * no radio and writes nothing. It reveals talkgroup ids the recordings list
 * already returns, plus which key IDs are held — never key material.
 *
 * `tracked` and `radioBusy` are reported separately on purpose. A session
 * started from a shell rather than from the console shows tracked:false with
 * radioBusy:true, and the feed works fine in that state because it depends on
 * the whitelist file and sdr.db rather than on sessionStore. Reporting only
 * `tracked` would make a working feed look dead.
 */
export default defineEventHandler(() => {
  let whitelistMtime: number | null
  try {
    whitelistMtime = statSync(whitelistPath()).mtimeMs / 1000
  } catch {
    whitelistMtime = null       // no session has ever run on this checkout
  }

  return {
    success: true,
    data: {
      talkgroups: followedTalkgroups(),
      heldKeyIds: heldKeyIds(),
      radioBusy: isRadioBusy(),
      tracked: sessionStore.get() !== null,
      whitelistMtime,
    },
  }
})
