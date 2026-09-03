import { stopTranscriber, isTranscriberRunning } from '~/server/utils/transcriber'

export default defineEventHandler((event) => {
  // Reads no body, so the bodyless-POST guard rather than the JSON one —
  // matching /api/listen/stop.
  const guard = assertSameOrigin(event)
  if (!guard.ok) {
    setResponseStatus(event, 403)
    return { success: false, error: guard.error }
  }

  // stopTranscriber() now throws in-container (see its guard) instead of
  // silently sending a signal that `restart: unless-stopped` immediately
  // undoes. Mirrors start.post.ts's catch, so a refusal reaches the operator
  // as an actionable message rather than a swallowed no-op.
  try {
    const signalled = stopTranscriber()
    // It finishes the file it is on before exiting, so it may still be alive
    // for a few seconds. Report what is true now rather than asserting it has
    // gone.
    return { success: true, data: { signalled, running: isTranscriberRunning() } }
  } catch (err) {
    setResponseStatus(event, 500)
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Failed to stop the transcriber',
    }
  }
})
