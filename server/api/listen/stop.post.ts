import { stopListening } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'

export default defineEventHandler(async (event) => {
  const session = sessionStore.get()
  if (!session) {
    setResponseStatus(event, 409)
    return { success: false, error: 'No listening session is running' }
  }

  try {
    await stopListening(session.pid)
    sessionStore.clear()
    return { success: true, data: { message: `Stopped session (pid ${session.pid})` } }
  } catch (err) {
    setResponseStatus(event, 500)
    return { success: false, error: err instanceof Error ? err.message : 'Failed to stop' }
  }
})
