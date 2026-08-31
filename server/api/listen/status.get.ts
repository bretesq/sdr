import { countCalls, readTail, isRadioBusy } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'
import { listenLogPath } from '~/server/utils/paths'

export default defineEventHandler(() => {
  const session = sessionStore.get()

  return {
    success: true,
    data: {
      running: session !== null,
      pid: session?.pid ?? null,
      config: session?.config ?? null,
      callCount: session ? countCalls(readTail(listenLogPath())) : 0,
      // True whenever op25 holds the HackRF, INDEPENDENTLY of whether we are
      // tracking a session. running:false + radioBusy:true is the stranded
      // case: bash died but rx.py survived, so a fresh Start would contend for
      // the radio. Reporting it beats silently claiming nothing is running.
      radioBusy: isRadioBusy(),
      startTime: session?.startTime ?? null,
      lastUpdate: Date.now() / 1000,
    },
  }
})
