import { isTranscriberRunning, isSttServerRunning } from '~/server/utils/transcriber'
import { transcriptionHealth } from '~/server/utils/queries'

/**
 * Three independent facts, deliberately not collapsed into one boolean.
 *
 * `reachable` only means the server answered an HTTP request. It answered in
 * 0.4ms for 26 hours while hanging every transcription, so on its own it is not
 * evidence of anything. `state` is measured from output instead — see
 * transcriptionHealth() for what it actually counts.
 *
 * reachable && state === 'degraded' IS the wedge signature this task exists to
 * surface: the server is answering, but nothing it is asked to do is finishing.
 * `idle` when nothing has ended recently is not a cop-out either — quiet air is
 * the normal night-time state on this system, and reporting "degraded" because
 * nobody keyed a mic would train the operator to ignore the indicator.
 */
export default defineEventHandler(async () => {
  const h = transcriptionHealth()
  const state
    = h.recentCalls === 0 ? 'idle'
      : h.awaiting === 0 ? 'healthy'
        : 'degraded'
  return {
    success: true,
    data: {
      running: isTranscriberRunning(),
      reachable: await isSttServerRunning(),
      state,
      awaiting: h.awaiting,
      oldestAwaitingSec: h.oldestAwaitingSec,
    },
  }
})
