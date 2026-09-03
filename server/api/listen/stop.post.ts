import { stopListening, stopDelegatedCapture, isRadioBusy } from '~/server/utils/processes'
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

  const session = await sessionStore.get()
  if (!session) {
    // No tracked session, but op25 may still hold the radio — bash can die
    // while the rx.py behind its pty survives. Answering 409 here would leave
    // the operator with a busy HackRF and no way to release it from the UI.
    if (isRadioBusy()) {
      // No session is tracked at all here, which — after this task's
      // fix-round-2 change to isSessionAlive()'s tolerance — should now only
      // mean an ORDINARY host session whose bash already died (op25's rx.py
      // survives behind a dead pty). It should NOT mean "a still-alive
      // delegated session, tracking merely lost to a blip": that specific
      // case is exactly what MAX_CONSECUTIVE_UNKNOWN in session.ts exists to
      // prevent — get() now tolerates a bounded run of 'unknown' answers
      // before ever closing a delegated row. CORRECTED FRAMING (final-review.md
      // M3): that tolerance is COUNT-based (3 consecutive 'unknown' polls),
      // not time-based — nothing resets it on elapsed wall-clock time. With
      // ListenControl.vue polling every 5s, three unknowns in a row IS
      // roughly 15s of SUSTAINED unreachability for a session someone is
      // actively watching — but for an unattended session with nobody
      // polling, those same three consecutive answers could span far longer
      // than 15s. The count still only ever moves toward closing on
      // consecutive bad answers, never on one blip, which is the actual
      // safety property this fence relies on — just don't read "~15s" as a
      // real-time guarantee.
      //
      // This is deliberately the untargeted, pid-less stopListening(0), not
      // stopDelegatedCapture() — task-3-review.md's fix-round-2 finding was
      // explicit that a delegated stop must go through the control API and
      // must NEVER fall through to pkill, and stopDelegatedCapture() has no
      // pid to scope a request to once no session is tracked (it would only
      // ever be able to ask the control API to stop "whatever it's running,"
      // which is indistinguishable from this pkill-based recovery in intent
      // but adds a network round trip with nothing to gain). So: an honest
      // limitation, not an oversight — a session that falls all the way to
      // "untracked" is the pre-existing stranded-radio recovery path, and
      // stays on it.
      //
      // CORRECTED MODEL (final-review.md I1 — "the tenth instance"): this
      // container's AppArmor confinement does NOT block signalling "any
      // process it did not start itself", as a previous version of this
      // comment and the ledger both claimed. The actual boundary is HOST vs.
      // CONTAINER: `docker-default`'s peer rule lets this container signal
      // ANOTHER `docker-default` container fine (in particular `capture`),
      // and refuses only the (unconfined) host. So if a capture container is
      // ALSO running concurrently when this untargeted path fires, it is NOT
      // merely "honest reporting" — the pkill below can actually reach and
      // kill that unrelated, healthy delegated capture, even while the host
      // radio it was aimed at survives EPERM. See
      // server/utils/processes.ts's comment above stopListening()'s throw
      // for the full measured trace; whether to scope this path away from
      // that blast radius is a decision for whoever owns this fix, not
      // something this comment resolves.
      // no pid to signal; releases the radio — or throws honestly if it
      // couldn't (see stopListening()'s isRadioBusy() check: pkill's own
      // exit code reads as success even when every kill() it attempted was
      // refused, so this must not be assumed to have worked just because it
      // did not throw before this fix).
      try {
        await stopListening(0)
        return {
          success: true,
          data: { message: 'No tracked session; released the radio op25 was holding.' },
        }
      } catch (err) {
        setResponseStatus(event, 500)
        return { success: false, error: err instanceof Error ? err.message : 'Failed to release the radio' }
      }
    }
    setResponseStatus(event, 409)
    return { success: false, error: 'No listening session is running' }
  }

  try {
    // A delegated session's `pid` is only meaningful inside the capture
    // container's own PID namespace (see session.ts's module comment) —
    // stopListening()'s local pid/pkill ladder has nothing valid to signal
    // for one and would fall straight through to its untargeted final
    // `pkill -f` step. Stop it the same way it was started: through the
    // control API, which holds the real pgid.
    if (session.backend === 'delegated') {
      await stopDelegatedCapture()
    } else {
      await stopListening(session.pid, session.procStart)
    }
    sessionStore.clear()
    return { success: true, data: { message: `Stopped session (pid ${session.pid})` } }
  } catch (err) {
    setResponseStatus(event, 500)
    return { success: false, error: err instanceof Error ? err.message : 'Failed to stop' }
  }
})
