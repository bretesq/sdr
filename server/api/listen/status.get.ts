import { countCalls, readTail } from '~/server/utils/processes'
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
      startTime: session?.startTime ?? null,
      lastUpdate: Date.now() / 1000,
    },
  }
})
