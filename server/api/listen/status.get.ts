import { countCalls, readTail, isRadioBusy } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'
import { listenLogPath } from '~/server/utils/paths'

export default defineEventHandler(async () => {
  const session = await sessionStore.get()

  return {
    success: true,
    data: {
      running: session !== null,
      pid: session?.pid ?? null,
      config: session?.config ?? null,
      // web/listen.log is written ONLY by the local-spawn path — a delegated
      // session's op25/recorders run inside the capture container and their
      // stdout goes to `docker compose logs capture` instead, so this file is
      // simply stale (possibly from an unrelated earlier LOCAL session) for
      // the whole duration of a delegated one. Reading it unconditionally
      // once a delegated session reports `running: true` (which it now
      // correctly does — see task-3-review.md's Critical C1) would report a
      // leftover, unrelated count as if it belonged to the live session,
      // which is worse than the honest "0 — not tracked here" this reports
      // instead. A real per-session count (e.g. querying `calls` by
      // session_id, now that sessionId is threaded through to delegated
      // sessions too) is a reasonable follow-up but is out of this fix's
      // scope — tracked in task-3-report.md rather than attempted here.
      callCount: session?.backend === 'local' ? countCalls(readTail(listenLogPath())) : 0,
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
