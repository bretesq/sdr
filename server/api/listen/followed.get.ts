import { readFileSync, statSync } from 'node:fs'
import { followedTalkgroups } from '~/server/utils/queries'
import { heldKeyIds } from '~/server/utils/keys'
import { isRadioBusy } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'
import { multiRxConfigPath, whitelistPath } from '~/server/utils/paths'
import { parseReceiverLayout } from '~/utils/receiverLayout'
import type { ReceiverLayout } from '~/utils/receiverLayout'

/**
 * Everything the Scanner Feed panel needs to arm itself, in one request.
 *
 * Read-only, so no CSRF guard: unlike /api/listen/start and /stop this drives
 * no radio and writes nothing. It reveals talkgroup ids the recordings list
 * already returns, plus which key IDs are held — never key material.
 *
 * `tracked` and `radioBusy` are reported separately on purpose. A session
 * started from a shell rather than from the console shows tracked:false with
 * radioBusy:true, and the feed works fine in that state because it depends on
 * the whitelist file and sdr.db rather than on sessionStore. Reporting only
 * `tracked` would make a working feed look dead.
 *
 * `sessionStartedAt` rides alongside `tracked` so the client can tell a
 * session that just opened (op25 not up yet) from one whose op25 died while
 * its recorders kept the process group alive — see utils/captureStatus.ts's
 * receiverStatus(), which is what actually turns this age into a verdict.
 * Epoch seconds, matching Session.startTime in server/utils/session.ts;
 * null whenever nothing is tracked.
 *
 * `sessionDurationSec` rides alongside it for the companion problem:
 * `sessionStartedAt` says when a tracked session began, but says nothing
 * about when it will end. A session started with a `--pd <seconds>` bound
 * (server/utils/processes.ts's buildListenArgs()) stops on its own once that
 * many seconds elapse — a clean, requested stop that looked identical to a
 * crash from this console's own displayed state (`on air · console
 * session`, unchanged right up to the silent end) until this field existed.
 * See utils/captureStatus.ts's captureExpiry(), which turns this alongside
 * `sessionStartedAt` into an actual expiry time. Read straight off the
 * tracked session's own stored config (`session.config.duration`, the exact
 * value the operator's Start request carried); null whenever nothing is
 * tracked OR the tracked session has no recorded duration (an unbounded run
 * with no `--pd`), never a guessed default.
 *
 * `receiverLayout` says how many radios and voice receivers the capture is
 * built from — the reference information the Strip Bay redesign lost when it
 * dropped the old ListenControl.vue's config summary and its two
 * voice-receiver controls (that file has since been deleted; see its
 * `configSummary` at 02c2804). Derived from lwin_both.json (see
 * utils/receiverLayout.ts for the
 * derivation and paths.ts's multiRxConfigPath() for the file), never from the
 * session's stored config: the file is what multi_rx.py was actually handed.
 *
 * WHY THIS RIDES HERE RATHER THAN ON A SECOND ENDPOINT
 * ----------------------------------------------------
 * Not merely to save a request (though it does: the bay already polls this
 * every FOLLOWED_POLL_MS = 20s, so the layout arrives on a schedule that
 * already exists, with no new fetch, no new poll loop and no new client
 * error state). The real reason is that a layout is only honest ALONGSIDE
 * `tracked`/`radioBusy`. lwin_both.json describes the last capture launched,
 * which is the running one only while a capture is running — so the client
 * must pair the layout with the liveness signals to caption it correctly.
 * Served from two endpoints, those arrive from two reads at two instants: a
 * capture that stops between them would let the bay caption a layout "this
 * capture" using a `tracked` that has since gone false. One handler, one
 * instant, no skew.
 *
 * Null when lwin_both.json is missing or unreadable — a fresh checkout where
 * no capture has ever run, the same case `whitelistMtime` and
 * followedTalkgroups() degrade quietly on. The bay shows no layout at all
 * rather than a zeroed one.
 */
export default defineEventHandler(async () => {
  let whitelistMtime: number | null
  try {
    whitelistMtime = statSync(whitelistPath()).mtimeMs / 1000
  } catch {
    whitelistMtime = null       // no session has ever run on this checkout
  }

  // Read here rather than inside utils/receiverLayout.ts so that file stays
  // pure and vitest-collectable (same split as utils/captureStatus.ts). The
  // read is deliberately unguarded by any freshness check: mtime tells us
  // when the file was written, not whether the capture it describes is still
  // up, and `tracked`/`radioBusy` below already answer that far better.
  let receiverLayout: ReceiverLayout | null
  try {
    receiverLayout = parseReceiverLayout(readFileSync(multiRxConfigPath(), 'utf-8'))
  } catch {
    receiverLayout = null       // no capture has ever run on this checkout
  }

  // Fetched once and reused for both fields below — sessionStore.get() is a
  // real liveness check (a control-API round trip for a delegated session,
  // see session.ts), not a free read; calling it twice would double that cost
  // for no benefit.
  const session = await sessionStore.get()

  return {
    success: true,
    data: {
      talkgroups: followedTalkgroups(),
      heldKeyIds: heldKeyIds(),
      radioBusy: isRadioBusy(),
      tracked: session !== null,
      sessionStartedAt: session?.startTime ?? null,
      sessionDurationSec: session?.config.duration ?? null,
      receiverLayout,
      whitelistMtime,
    },
  }
})
