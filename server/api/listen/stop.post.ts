import { stopListening, isRadioBusy } from '~/server/utils/processes'
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
    // No tracked session, but op25 may still hold the radio — bash can die
    // while the rx.py behind its pty survives. Answering 409 here would leave
    // the operator with a busy HackRF and no way to release it from the UI.
    if (isRadioBusy()) {
      await stopListening(0)          // no pid to signal; releases the radio
      return {
        success: true,
        data: { message: 'No tracked session; released the radio op25 was holding.' },
      }
    }
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
