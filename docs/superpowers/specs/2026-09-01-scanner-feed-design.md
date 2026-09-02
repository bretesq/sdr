# Scanner Feed — live listening in the web console

**Date:** 2026-09-01
**Status:** Design approved, ready for implementation planning
**Scope:** Near-live ("Scanner Feed") only. True-live PCM streaming is a separate spec.

## Problem

The console records every call to `recordings/*.wav` and `sdr.db`, but the only way
to hear one is to find its row in `RecordingsList` and press play. There is no way to
select a set of talkgroups and simply listen to them as traffic comes in.

## What this builds

A panel that lets the operator pick talkgroups from the ones the running session is
actually following, arm the feed, and hear each matching call play back-to-back a few
seconds after it ends — scanner semantics, the way Broadcastify Calls works.

## What this deliberately does not build

True-live audio (hearing a transmission as it is spoken, ~0.5 s behind). That requires
`udp_audio_record.py` to fan out a copy of each 320-byte frame to a Nitro ingest port,
a WebSocket transport, and an AudioWorklet client — which means editing the Python
recorder and restarting the radio session to test. It is a second spec.

Two findings from this design that belong to that spec, recorded so they are not
rediscovered:

- A second UDP listener on the recorder's port is not viable. `SO_REUSEADDR` does not
  duplicate unicast datagrams, and `SO_REUSEPORT` on Linux **load-balances** them — a
  Node-side tap would steal roughly half the packets and corrupt both the WAVs and the
  tap. Fan-out must be an explicit `sendto` from the recorder.
- Talkgroup tagging must come from the recorder. The UDP audio stream carries no TGID;
  `udp_audio_record.py` recovers it by tailing op25's log via `op25_log.LogTail`,
  including the `--rx-id N` filtering that stops 8 concurrent receivers from
  mislabelling each other's calls. A Node-side tap would have to reimplement all of it.

The near-live and true-live paths share the **selection state** and the
**now-playing/queue display**, not the transport — clips carry discrete metadata,
PCM is continuous. No transport abstraction is introduced in this spec; the seam is
noted in prose and drawn when the second implementation exists.

## Measurements this design rests on

Taken from the live 6-hour campaign on 2026-09-01 (~180 min elapsed at time of design):

| quantity | value |
|---|---|
| calls in window | 551 |
| mean / max call duration | 4.2 s / 32.4 s |
| audio seconds, summed across receivers | 2216 s = 20% of the window |
| max simultaneous calls | 4 |
| talkgroups followed (`lwin_active_whitelist.txt`) | 100 |
| talkgroups that actually produced calls | 15 |
| calls recorded on a talkgroup outside the whitelist | 0 |
| `ended_at` NULL / `dur` NULL | 0 / 0 |
| id-order inversions vs `ended_at` | 2 |
| `MAX(calls.id)` | 5157 |
| ADP calls under a keyid not held | 2 (keyid `0x1320`) |

The 20% figure is a sum across concurrent receivers, not a serial-playback duty cycle.
With up to 4 simultaneous calls, bursts exceed what one serial player can absorb, which
is why the queue needs a drop policy rather than unbounded FIFO.

## Architecture

New pieces in **bold**; everything else already exists.

```
udp_audio_record.py x8 --> recordings/*.wav --> sdr.db (calls)
                                                    |
                        /api/recordings/stream  ----+  SSE summary (trigger only)
                                                    |
   browser  <-- on each push --> /api/recordings/list?afterId=&tgids=   EXTENDED
            <-- on mount ------> /api/listen/followed                   NEW
            <-- per clip ------> /api/recordings/<file>   (unchanged, Range)
```

The existing SSE route stays a **summary-only trigger**. `stream.get.ts` argues in its
own docstring for "a summary, never rows … so there is exactly one place that builds a
recordings query"; holding per-client talkgroup filter state on the server would break
that. The client is told *something changed* and asks *what* through the one query
builder.

## Server units

### `server/utils/queries.ts` — extend `listRecordings`

Add to `RecordingQuery`:

- `afterId?: number` — emits `c.id > ?`. Cursor for the live feed.
- `tgids?: number[]` — emits `c.tgid IN (…)`, alongside the existing single `tgid`.
  Both may be supplied; they AND together, since each is an independent narrowing.
  An empty `tgids` array matches nothing rather than everything — an armed feed with
  no talkgroups selected must be silent, not a firehose.

Add to the return value:

- `maxId: number` — an unfiltered `SELECT MAX(id) FROM calls`.

This extends the single query builder rather than standing up a parallel
`listCallsSince`, consistent with the decision recorded in `stream.get.ts`.

**Why the cursor is `id` and not a timestamp.** `calls.id` is assigned at commit. With
8 recorder processes writing one WAL database, rowids are globally monotonic, so
`id > lastSeenId` cannot skip a row. A `start`- or `ended_at`-based cursor can: a long
call *starts* before a short one but *commits* after it, so a timestamp cursor drops it
silently. Two such inversions were measured in a single 3-hour window, and they are by
construction the *longest* transmissions — the ones most worth hearing. Do not
"simplify" this to a timestamp.

**Why `maxId` is needed separately.** `listRecordings` orders by `c.start DESC`
(`queries.ts:232`), so a `limit=1` query returns the newest call *by start time*, whose
`id` is not necessarily the maximum — the same inversions make that seed too low, and a
too-low seed replays calls. `maxId` must be an explicit aggregate.

### `server/api/listen/followed.get.ts` (new)

The selector's source of truth. Returns, for each talkgroup in
`lwin_active_whitelist.txt`:

- `tgid`, and `alpha` / `desc` / `cat` from a LEFT JOIN on `talkgroups`
- `recentCalls` — count of calls in the trailing 6 hours, for activity ranking. Six
  hours because it spans a full capture campaign and a shift change; it is a display
  ordering only, so the exact figure is not load-bearing.

Plus session state: `radioBusy` (from `isRadioBusy()`), `tracked` (whether
`sessionStore` has a session), and the whitelist file's mtime.

**Activity ranking is load-bearing, not cosmetic.** Only 15 of the 100 followed
talkgroups produced a call in 6 hours; without ranking, 85 dead rows bury the live ones.

**On liveness.** `lwin_listen_multi.sh:117` overwrites one shared whitelist path at each
session start, and the file persists unchanged after a session dies. Its contents alone
are therefore not proof anything is running. Pairing it with `isRadioBusy()` lets the
panel report the real state — including the current condition, a session started from a
shell rather than the console, which reads `running: false, radioBusy: true`.

### `server/utils/keys.ts` (new)

Reads `lwin_keys.json` and exposes **only the set of held keyids** (currently `0x1`,
`0x8`, `0x2F08`).

**Key material must never cross to the browser.** That file holds live ADP key bytes.
The client needs to know only whether a keyid is held, in order to decide whether a call
will decode to speech or to noise.

## Client units

Split so the hard part is testable without a browser. Colocated `.test.ts` follows the
existing `utils/tencodeSegments.ts` pattern.

| unit | responsibility | depends on |
|---|---|---|
| `utils/scannerQueue.ts` | **pure** queue engine: admit / classify / prune / next. No DOM, no fetch, no Vue. | nothing |
| `composables/useScannerFeed.ts` | wiring: EventSource -> cursor fetch -> feed the queue -> drive one `<audio>` element | `scannerQueue`, browser APIs |
| `components/ScannerFeed.vue` | panel: talkgroup selector, now-playing, queue list, counters | `useScannerFeed` |

### Queue engine contract

```
admit(call, selectedTgids, heldKeyids) -> 'playable' | 'locked' | 'rejected'
    locked   <= call.algid === 170 (0xAA) && !heldKeyids.has(call.keyid)
    rejected <= call.tgid not in selectedTgids

prune(nowMs, stalenessMs)
    drops ANY entry, playable or locked, where nowMs - (ended_at * 1000) > stalenessMs
    increments `skipped` for dropped *playable* entries only

next(nowMs)
    prune, then shift the oldest remaining playable entry
```

`stalenessMs` defaults to **30 s** and is operator-adjustable in the panel over the
range 10-300 s. It is client state persisted in `localStorage`; the server has no
opinion about it.

**Staleness is measured from `ended_at`, never from `start`.** The longest measured call
is 32.4 s against a default 30 s bound; measuring age from `start` would make every long
transmission born-stale and never played, silently discarding exactly the calls most
worth hearing. `ended_at` and `dur` are both fully populated (0 NULLs in 551 rows), so
`ended_at` can be relied on directly.

**Encryption is classified from `algid`/`keyid`, not from `enc_observed`/`enc_evidence`.**
Those two columns are NULL on every live row — they are filled by a later reconciliation
pass — so a live-path filter keyed off them would classify everything as clear.

Locked entries are display-only: they render with a lock badge and the keyid, age out on
the same staleness bound, and do **not** count toward `skipped`. Skipping noise that was
never going to be played is not a loss worth reporting. They stay visible because an
unheld keyid appearing live is a crack target, which feeds the existing ADP recovery
workflow.

### Cursor seeding

On **Play** — not on mount — the client seeds `lastSeenId` from `maxId`. Arming the feed
starts from now rather than replaying the 5,157-row backlog. Capturing at Play rather
than at mount closes the window where calls land while the panel sits open unarmed.

### Playback

One long-lived `<audio>` element, reused for every clip. Browsers gate autoplay on a
user gesture and the unlock attaches to *that element*; constructing a fresh `Audio()`
per call loses the unlock on Safari/iOS and the feed goes silent after the first clip.
The Play button is the gesture; every subsequent clip sets `.src` on the same element.

Loop: `ended` -> `next()` -> set `src` -> `play()`. Empty queue -> idle, wait for the
next SSE push.

No prefetch. Clips are roughly 70 KB (4.4 s x 8 kHz x 16-bit mono) over a LAN, and the
small inter-clip gap reads as scanner behaviour rather than a defect.

`udp_audio_record.py` closes the WAV at line 111 before committing the row at line 126,
so a row in `calls` always has a complete file on disk. No 404-retry logic is needed.

## UI placement

`ScannerFeed.vue` mounts in `pages/index.vue` alongside the existing `ListenControl`,
`RecordingsList` and `TalkgroupBrowser`, above `RecordingsList` — the live feed is the
thing being watched, the recordings table is the archive being searched. The panel
collapses to its header when not armed, so it costs little vertical space to an
operator who is not listening.

## Error handling and edge cases

| case | behaviour |
|---|---|
| SSE connection drops | `EventSource` reconnects on its own. The `afterId` cursor means nothing is missed, only delayed; genuinely late calls then age out as stale, which is correct. |
| Fetched rows arrive `start DESC` | Client reverses to ascending before enqueuing, so calls are heard in the order they happened. |
| No session running | Panel reads "radio idle". Play stays armed and waits. |
| Session running but untracked | Panel reads "following N TGs, radio busy, untracked session". The feed still works, because it depends on the whitelist file and `sdr.db`, not on `sessionStore`. |
| Selected talkgroup silent | The common case — only 15 of 100 followed TGs were active in 6 h. The empty state must look normal, not broken. |
| Backgrounded tab | Timers throttle; SSE and audio continue. `prune` runs inside `next()` rather than on a timer, so throttling cannot corrupt the queue. |
| `sdr.db` absent | `getDb()` already throws 503; the panel surfaces it and stays idle. |

## Testing

**`utils/scannerQueue.test.ts`** — pure, no browser. Carries regressions for the two
bugs found during design:

- a 32.4 s call against a 30 s staleness bound must still play (staleness from
  `ended_at`, not `start`)
- an out-of-order `ended_at` fixture, built from the two real inversions measured on
  2026-09-01, must not be skipped by the cursor

plus: `locked` classification for an unheld keyid, `rejected` for an unselected
talkgroup, `skipped` counting playable drops only, and FIFO order under interleaved
admits.

**`server/utils/queries.test.ts`** — extended for `afterId`, `tgids`, and `maxId`,
including that `maxId` is unaffected by the filters.

**`server/utils/keys.test.ts`** (new) — asserts that the exposed value contains keyid
presence only and that **no key material appears in the response**.

## Success criteria

- Selecting one or more talkgroups and pressing Play produces audio within roughly
  2-6 s of a matching call ending.
- The feed never drifts more than the staleness bound plus the current clip's duration
  behind live (a clip already playing is never cut off).
- Long transmissions (>30 s) play rather than being dropped.
- Unkeyed encrypted calls are visible and silent.
- The feed works against a session started from a shell, not only one started from the
  console.
- No ADP key material is reachable from the browser.
