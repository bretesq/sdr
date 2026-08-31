import { stopListening } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'

export default defineEventHandler(async (event) => {
  // This route reads no body, so a bodyless cross-site
  // `fetch(url, {method:'POST', mode:'no-cors'})` from any page the operator
  // visits would kill a live recording. Sec-Fetch-Site is the only signal
  // available here.
  const guard = assertSameOrigin(event)
  if (!guard.ok) {
    setResponseStatus(event, 403)
    return { success: false, error: guard.error }
  }

  const session = sessionStore.get()
  if (!session) {
    setResponseStatus(event, 409)
    return { success: false, error: 'No listening session is running' }
  }

  try {
    await stopListening(session.pid, session.procStart)
    sessionStore.clear()
    return { success: true, data: { message: `Stopped session (pid ${session.pid})` } }
  } catch (err) {
    setResponseStatus(event, 500)
    return { success: false, error: err instanceof Error ? err.message : 'Failed to stop' }
  }
})
