import { stopTranscriber, isTranscriberRunning } from '~/server/utils/transcriber'

export default defineEventHandler((event) => {
  // Reads no body, so the bodyless-POST guard rather than the JSON one —
  // matching /api/listen/stop.
  const guard = assertSameOrigin(event)
  if (!guard.ok) {
    setResponseStatus(event, 403)
    return { success: false, error: guard.error }
  }

  const signalled = stopTranscriber()
  // It finishes the file it is on before exiting, so it may still be alive for
  // a few seconds. Report what is true now rather than asserting it has gone.
  return { success: true, data: { signalled, running: isTranscriberRunning() } }
})
