import { startTranscriber } from '~/server/utils/transcriber'

export default defineEventHandler((event) => {
  // Spawns a process, so it gets the same CSRF guard as the listen routes.
  // Cheaper to abuse than a radio, but still not something a page the operator
  // happens to visit should be able to do.
  const guard = assertJsonSameOrigin(event)
  if (!guard.ok) {
    setResponseStatus(event, guard.status)
    return { success: false, error: guard.error }
  }

  try {
    const started = startTranscriber()
    return {
      success: true,
      data: {
        running: true,
        // Distinguished so the UI can say "already running" rather than
        // implying this call did something.
        started,
      },
    }
  } catch (err) {
    setResponseStatus(event, 500)
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Failed to start the transcriber',
    }
  }
})
