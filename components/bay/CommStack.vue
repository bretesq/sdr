<template>
  <aside class="stack">
    <!--
      THE BLOCK REGION — everything above the standby list, in one scroller.

      Its own element rather than four loose children of `.stack` because the
      standby list is the primary control on this console and must keep a
      usable height at every viewport it runs on. See bay.css's `.stack__head`
      for the flex arithmetic; the short version is that this region yields
      first and scrolls itself, so the list below it can never be squeezed to
      nothing by the blocks above. The Standby header and the list itself sit
      OUTSIDE it, pinned: a roster search whose field has scrolled off is a
      search the operator has to go find.
    -->
    <div class="stack__head">
      <!-- ACTIVE: what the bay is doing right now -->
      <div class="stack__block">
        <span class="stack__label">Active</span>
        <div class="readout" :class="{ 'readout--dim': !armed }">
          {{ armed ? selected.length : '—' }}<span class="readout__unit"> tg armed</span>
        </div>
        <button
          class="arm"
          :class="{ 'arm--on': armed }"
          :disabled="!armed && selected.length === 0"
          style="margin-top: 10px"
          @click="$emit('toggle')"
        >
          <span class="arm__lamp" />
          {{ armed ? 'Stop' : 'Arm bay' }}
        </button>
        <p v-if="!armed && selected.length === 0" class="idle__sub" style="text-align: left; margin-top: 8px">
          Tick a talkgroup below to arm.
        </p>
      </div>

      <!-- RECEIVER: the machinery, reported honestly -->
      <div ref="receiverBlock" class="stack__block">
        <span class="stack__label">Receiver</span>
        <div class="readout readout--dim" style="font-size: 15px">
          {{ receiverLine }}
        </div>
        <p class="idle__sub" style="text-align: left; margin-top: 6px">
          {{ receiverNote }}
        </p>
        <!--
          LAYOUT: how many radios and voice streams are actually in play.

          The old console had this and the redesign dropped it: the since-deleted
          ListenControl.vue carried a config summary plus `700 MHz voice
          receivers` / `800 MHz voice receivers` controls (see its
          `configSummary` at 02c2804), and the bay replaced all of it with
          `{n} armed · {idle|on air|stalled}` — which says nothing about the
          receiver pool a missed call would have been missed by. It sits in the
          Receiver block rather than in Capture because it describes the
          machinery, not a control: the counts are not settable from this
          console (see `layoutNote`), so putting them beside the Capture fields
          would invite exactly the wrong reading.

          Reference information, so it is `idle__sub` at reading size rather
          than a `readout` — the receiver STATE above is what an operator scans
          for; this is what they check once and stop noticing. Three separate
          `<p>`s rather than one, because the leg split is suppressed for a
          single-leg capture (where it only repeats the totals) and every line
          disappears together when there is no layout to report.

          FOLDED, because "check it once and stop noticing" is precisely what a
          fold is for: the three lines came to 114px of a 768px column — half of
          what the standby list was left with. The crease still prints the
          totals, so the fact an operator would glance for is not behind the
          fold; only the leg split and the provenance caption are. `layoutLine`
          gates the whole fold for the same reason it gated the first `<p>`: no
          layout on disk, nothing to report, no crease.
        -->
        <template v-if="layoutLine">
          <button
            class="fold"
            type="button"
            :aria-expanded="layoutOpen"
            aria-controls="stack-layout"
            @click="toggleLayout"
          >
            <span class="stack__label fold__label">Layout</span>
            <span v-if="!layoutOpen" class="fold__sum">{{ layoutLine }}</span>
            <span class="fold__box" :class="{ 'fold__box--open': layoutOpen }" aria-hidden="true" />
          </button>
          <div v-show="layoutOpen" id="stack-layout" class="fold__body">
            <p class="idle__sub" style="text-align: left">
              Layout: {{ layoutLine }}
            </p>
            <p v-if="layoutLegLine" class="idle__sub" style="text-align: left">
              {{ layoutLegLine }}
            </p>
            <p v-if="layoutNote" class="idle__sub" style="text-align: left">
              {{ layoutNote }}
            </p>
          </div>
        </template>
      </div>

      <!--
        CAPTURE: starts and stops the RADIO. Separate from the Active block
        above on purpose — Arm/Stop up there only gates which clips this
        browser tab plays; it has never touched op25. This is the control the
        redesign dropped entirely — after the bay replaced it, no page rendered
        the old ListenControl.vue, which sat unreferenced until it was deleted —
        so an operator had no way to start or stop a capture from the console at
        all. See utils/listenControl.ts's module
        docstring for exactly why the surface below is this small: it is every
        field server/utils/processes.ts's buildControlRequest() will actually
        delegate to the capture container, and nothing it refuses.
      -->
      <div ref="captureBlock" class="stack__block">
        <span class="stack__label">Capture</span>

        <!--
          THE SETUP FIELDS FOLD; START/STOP AND EVERY WARNING DO NOT.

          This block was 386px of a 768px column — more than the standby list,
          the Capture block's own presets, and the Receiver block put together,
          for a control that is touched when a capture is started and not again
          for hours. Folded, it is the four lines an operator actually needs at
          rest: what Start would send, the button, what the button will do, and
          anything that went wrong.

          What stays OUT of the fold is the part that matters. Stop must never
          be behind a fold on a public-safety console, so the `.arm` button is a
          sibling of the fold, not a child of it. The two `capture__warn` lines
          are siblings too: `!durationValid` DISABLES Start, and a disabled
          button whose reason is folded away is a dead control with no
          explanation; the wider-preset warning is, by its own note below, "a
          fact about the selection, not about the button", and a fact does not
          stop being true because its field is folded. Both render nothing in
          the ordinary state, so they cost the collapsed block no height.

          `v-show`, not `v-if`: a half-typed duration must survive a fold.
        -->
        <button
          class="fold"
          type="button"
          :aria-expanded="captureOpen"
          aria-controls="capture-fields"
          @click="toggleCapture"
        >
          <span class="stack__label fold__label">Settings</span>
          <span v-if="!captureOpen" class="fold__sum">{{ captureSummary }}</span>
          <span class="fold__box" :class="{ 'fold__box--open': captureOpen }" aria-hidden="true" />
        </button>

        <div v-show="captureOpen" id="capture-fields" class="fold__body">
          <p class="idle__sub" style="text-align: left; margin-bottom: 8px">
            Two radios, multi-receiver — the only capture shape this console can hand
            to the capture container. The preset picks which talkgroups it follows.
          </p>

        <label class="capture__row capture__row--wide">
          <span>Preset</span>
          <span class="capture__row-field">
            <select
              v-model="preset"
              class="field capture__preset"
              :disabled="!canStart || busy"
              aria-label="Talkgroup preset"
            >
              <option v-for="p in CAPTURE_PRESETS" :key="p" :value="p">
                {{ p }} — {{ CAPTURE_PRESET_LABELS[p] }}
              </option>
            </select>
          </span>
        </label>
        <!--
          What the selected preset actually follows: the TAG NAMES
          make_whitelist.py filters the roster by, which is the precise version
          of the option's human label. Always shown, for the same reason
          `durationHuman` is always shown beside the duration field — a value
          whose consequences are one indirection away should state them on
          screen rather than requiring the operator to remember what "interop"
          expands to.
        -->
        <p class="idle__sub" style="text-align: left; margin-top: -2px">
          Follows: {{ CAPTURE_PRESET_TAGS[preset] }}
        </p>

        <!--
          Two things the operator must not be misled about. They are INDEPENDENT
          conditions, so they are two independent `v-if`s: chained, whichever
          rendered first would suppress the other in states where both are true.

          1. The picker applies to the NEXT Start, never to a running capture.
             The primary defence is structural, not textual: like every other
             control in this block it is `:disabled` whenever Start is
             unavailable, so it cannot be changed under a running session and
             appear to have done something. This line says so in words — and it
             is gated on `tracked`, NOT on `!canStart`. canStartCapture() is
             `!tracked && !radioBusy`, so `!canStart` is also true when the
             radio is busy with a capture this console never started
             ('onAirOutside'), where there is no console session to "retune" and
             this sentence would be describing something that does not exist.

          2. A wider preset raises concurrent-call load. The 800 MHz leg's
             voice-receiver count was raised 5 -> 7 in 042cc3a on concurrency
             measured under `pd` ALONE (peak 5 of 5, ceiling touched 17 times in
             7,136 calls); every other preset here follows strictly more
             talkgroups than that measurement covered, so the headroom behind
             those counts is unverified for them. Deliberately qualitative — the
             honest number would be a fresh measurement, and inventing a ratio
             from the current roster would rot the moment the roster changes.
             Shown whenever a wide preset is SELECTED, regardless of whether
             Start happens to be available this instant: it is a fact about the
             selection, not about the button.
        -->
        <p v-if="tracked" class="idle__sub" style="text-align: left">
          Applies to the next Start — changing it does not retune the running capture.
        </p>

        <label class="capture__row">
          <span>Duration</span>
          <span class="capture__row-field">
            <input
              v-model.number="duration"
              class="field capture__duration"
              type="number"
              :min="MIN_CAPTURE_DURATION_SEC"
              :max="MAX_CAPTURE_DURATION_SEC"
              :disabled="!canStart || busy"
              aria-label="Capture duration in seconds"
            >
            <span class="capture__hint-inline">{{ durationHuman }}</span>
          </span>
        </label>
        <label class="capture__toggle">
          <input v-model="ess" type="checkbox" :disabled="!canStart || busy">
          Capture encryption headers (ESS, ~10&times; log volume)
        </label>

        <label class="capture__toggle">
          <input v-model="includeEncrypted" type="checkbox" :disabled="!canStart || busy">
          Include fully-encrypted talkgroups (records silence)
        </label>
        </div>

        <p
          v-if="preset !== DEFAULT_CAPTURE_PRESET"
          class="idle__sub capture__warn"
          style="text-align: left; margin-top: 8px"
        >
          Wider than {{ DEFAULT_CAPTURE_PRESET }}. The voice-receiver counts were tuned
          on concurrency measured under {{ DEFAULT_CAPTURE_PRESET }} alone — more
          talkgroups means more overlapping calls, and calls past the receiver pool
          are missed, not queued.
        </p>
        <p v-if="!durationValid" class="idle__sub capture__warn" style="text-align: left; margin-top: 8px">
          Must be a whole number of seconds from {{ MIN_CAPTURE_DURATION_SEC }} to
          {{ MAX_CAPTURE_DURATION_SEC }} (24h) — capture_control.py's own bound. Required:
          the delegated request has no "run until stopped".
        </p>

        <button
          class="arm"
          :class="{ 'arm--on': canStop }"
          :disabled="busy || (!canStart && !canStop) || (canStart && !durationValid)"
          style="margin-top: 10px"
          @click="canStop ? stopCapture() : startCapture()"
        >
          <span class="arm__lamp" />
          {{ captureButtonLabel }}
        </button>

        <p class="idle__sub" style="text-align: left; margin-top: 6px">
          {{ captureHint }}
        </p>
        <p v-if="captureError" class="idle__sub capture__warn" style="text-align: left; margin-top: 6px">
          {{ captureError }}
        </p>
      </div>
    </div>

    <!-- STANDBY: the talkgroups this session can actually produce -->
    <div class="stack__block" style="padding-bottom: 8px">
      <span class="stack__label">Standby — {{ followed.length }} followed, {{ activeCount }} active</span>
      <input
        v-model="query"
        class="field"
        type="search"
        placeholder="search talkgroups — id or name"
        aria-label="Filter the followed talkgroups and search the full roster"
      >
    </div>

    <div class="standby">
      <!--
        THE CAPTURE'S OWN TALKGROUPS — the only rows that are controls.

        Armable, because op25 is recording them. The encryption mark rides in
        the strip vocabulary (.mark--locked) rather than a badge of its own:
        see utils/talkgroupEncryption.ts's talkgroupMark() for why a talkgroup
        and a call strip must say "encrypted" the same way, and bay.css's
        `.standby .mark--locked` for why the colour is re-tuned here.
      -->
      <button
        v-for="t in shown"
        :key="t.tgid"
        class="standby__row"
        :class="{ 'standby__row--on': selected.includes(t.tgid) }"
        type="button"
        :aria-pressed="selected.includes(t.tgid)"
        @click="$emit('toggleTg', t.tgid)"
      >
        <span class="standby__tick" />
        <span>{{ t.tgid }}</span>
        <span class="standby__name">{{ t.alpha ?? 'unlisted' }}</span>
        <span v-if="t.mark" class="mark" :class="t.mark.cls" :title="t.mark.title">{{ t.mark.label }}</span>
        <span v-else />
        <span class="standby__n">{{ t.recentCalls || '' }}</span>
      </button>

      <p v-if="!shown.length && !rosterRows.length" class="idle">
        no match
        <span class="idle__sub">{{ noMatchNote }}</span>
      </p>

      <!--
        THE REST OF THE ROSTER — reference, never a control.

        4,163 talkgroups exist; the running capture records 222. A row from the
        other 3,941 can never produce a clip, so it is rendered as a <div>
        with no tick and no handler: arming permanent silence is not something
        the operator can do here by mistake, rather than something they are
        merely warned about. The note below says what would actually be needed.
      -->
      <template v-if="rosterRows.length">
        <p v-if="!shown.length" class="standby__note">
          Nothing in the running capture matches that.
        </p>
        <div class="standby__group">
          <span>Roster · not in this capture</span>
          <span>{{ rosterCountLabel }}</span>
        </div>
        <div
          v-for="t in rosterRows"
          :key="`roster-${t.tgid}`"
          class="standby__row standby__row--offair"
        >
          <span />
          <span>{{ t.tgid }}</span>
          <span class="standby__name">{{ t.alpha || 'unlisted' }}</span>
          <span v-if="t.mark" class="mark" :class="t.mark.cls" :title="t.mark.title">{{ t.mark.label }}</span>
          <span v-else />
          <span class="mark mark--note">not captured</span>
        </div>
        <p class="standby__note">
          op25 emits audio only for the talkgroups in the running capture’s whitelist, so
          these cannot be armed — a tick here would arm silence. Recording one takes a
          capture started on a preset that includes it, not a checkbox.
        </p>
      </template>

      <p v-if="rosterError" class="standby__note capture__warn">{{ rosterError }}</p>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import { receiverStatus, captureExpiry, canStartCapture, canStopCapture } from '~/utils/captureStatus'
import { talkgroupMark } from '~/utils/talkgroupEncryption'
import type { TalkgroupEncryptionVerdict, TalkgroupMark } from '~/utils/talkgroupEncryption'
import type { ReceiverStatus } from '~/utils/captureStatus'
import type { ReceiverLayout } from '~/utils/receiverLayout'
import {
  buildCaptureStartBody, isValidCaptureDuration, apiError,
  DEFAULT_CAPTURE_DURATION_SEC, MIN_CAPTURE_DURATION_SEC, MAX_CAPTURE_DURATION_SEC,
  CAPTURE_PRESETS, CAPTURE_PRESET_LABELS, CAPTURE_PRESET_TAGS, DEFAULT_CAPTURE_PRESET,
} from '~/utils/listenControl'
import type { CapturePreset } from '~/utils/listenControl'

/**
 * The comm stack: active above, standby below, exactly as a radio stack reads.
 *
 * The standby list — the ARMABLE list — is still the session whitelist rather
 * than the full 4,163-row reference, because op25 only emits audio for
 * talkgroups the running session follows, and a control offered here that
 * cannot produce sound would be a lie the operator could not see through.
 *
 * WHAT THE SEARCH FIELD NOW REACHES, AND WHY THAT IS NOT A CONTRADICTION
 * ----------------------------------------------------------------------
 * The field used to filter the whitelist and nothing else, so a talkgroup that
 * was not in the running capture simply did not exist as far as this console
 * was concerned — an operator asking "is 19014 a thing?" got "no match", which
 * is a different and wronger answer than "yes, and this capture isn't recording
 * it". The field now also queries the roster (`/api/talkgroups/list?area=all`),
 * and those hits are printed BELOW the armable list, as reference rows with no
 * tick and no click handler. Two sections, two kinds of thing: controls above,
 * facts below. The lie the old behaviour avoided is avoided structurally rather
 * than by omission.
 */

/** One row of the whitelist, as `/api/listen/followed` returns it. */
interface FollowedRow {
  tgid: number
  alpha: string | null
  recentCalls: number
  encryption: TalkgroupEncryptionVerdict
}

/** A whitelist row with its pencil mark resolved once, for the template. */
interface StandbyRow extends FollowedRow {
  mark: TalkgroupMark | null
}

/** A roster hit, as `/api/talkgroups/list` returns it. */
interface RosterTalkgroup {
  tgid: number
  alpha: string
  desc: string
  cat: string
  tag: string
  encryption: TalkgroupEncryptionVerdict
  inWhitelist: boolean
}

interface RosterRow extends RosterTalkgroup {
  mark: TalkgroupMark | null
}

interface RosterResponse {
  success: boolean
  data: RosterTalkgroup[]
  /** Whole-roster count (4,163), independent of the filters. */
  total: number
  /** How many rows the filters hit, before `limit` capped the response. */
  matched: number
}

const props = defineProps<{
  followed: FollowedRow[]
  selected: number[]
  armed: boolean
  radioBusy: boolean
  tracked: boolean
  /** Epoch seconds the tracked session opened, or null when untracked. */
  sessionStartedAt: number | null
  /**
   * Seconds the tracked session was started with, or null when untracked or
   * the session has no recorded duration (an unbounded run, no `--pd`). See
   * server/api/listen/followed.get.ts's own docstring for where this comes
   * from — it is the operator's own Start request, never a guess made here.
   */
  sessionDurationSec: number | null
  /**
   * How many radios and voice receivers the capture is built from, derived
   * server-side from the op25 config on disk — or null when that file is
   * missing or unreadable (a fresh checkout where no capture has ever run),
   * in which case the bay shows no layout at all rather than a zeroed one.
   * See utils/receiverLayout.ts for the derivation and
   * server/api/listen/followed.get.ts for why it rides on the same poll as
   * `tracked`/`radioBusy` — the layout can only be captioned honestly
   * alongside them.
   */
  receiverLayout: ReceiverLayout | null
}>()

const emit = defineEmits<{
  toggle: []
  toggleTg: [tgid: number]
  /**
   * Fired after a Start or Stop attempt settles, success or failure. This
   * component only owns the request/response and its own busy/error state —
   * `radioBusy`/`tracked`/`sessionStartedAt`/`sessionDurationSec` are props,
   * read from the SAME `/api/listen/followed` poll `useScannerFeed`
   * (composables/useScannerFeed.ts) already runs every
   * `FOLLOWED_POLL_MS` (20s) for the stall indicator. Without this emit the
   * operator would see their own Start/Stop take effect only on that next
   * poll — up to 20s of the button appearing to have done nothing. The
   * parent owns that composable, so refreshing it is the parent's call to
   * make; this only asks for it.
   */
  refreshCapture: []
}>()

const query = ref('')

const activeCount = computed(() => props.followed.filter(t => t.recentCalls > 0).length)

/* ---------------------------------------------------------------------------
 * ROSTER SEARCH — the 4,163 talkgroups the capture is not recording
 *
 * Deliberately a second, debounced request rather than a client-side filter
 * over a preloaded roster: 4,163 rows each carrying a category string is a
 * payload this panel has no other use for, and the server already owns the
 * search (server/utils/queries.ts's listTalkgroups, which matches id, alpha,
 * description, category and tag — more fields than the local filter below can
 * see) plus the whitelist comparison that decides whether a hit is armable.
 * Deriving `inWhitelist` here would mean shipping the whitelist to the browser
 * and re-implementing the file parser's agreement with the server's.
 * ------------------------------------------------------------------------- */

/**
 * Below this, the roster is not queried at all. One character matches
 * thousands of ids and answers nothing; it also means the common case — an
 * operator scrubbing the whitelist down to a couple of rows — makes no
 * requests at all.
 */
const ROSTER_MIN_CHARS = 2

/**
 * Rows asked for per search. The panel is a few dozen rows tall and `matched`
 * reports the true hit count regardless, so this caps the payload without
 * hiding the scale of the result — "12 shown · 384 matched" is honest where a
 * silently truncated list of twelve is not.
 */
const ROSTER_LIMIT = 40

/** Long enough that typing a talkgroup name is one request, not eight. */
const ROSTER_DEBOUNCE_MS = 250

const roster = ref<RosterTalkgroup[]>([])
const rosterMatched = ref(0)
const rosterPending = ref(false)
const rosterError = ref('')
let rosterTimer: ReturnType<typeof setTimeout> | null = null

/**
 * Monotonic request id. Two searches in flight can land out of order — a
 * two-character query is slower than the five-character one typed after it —
 * and the older response would then overwrite the newer, leaving results that
 * do not match what is in the box. Only the newest sequence may write.
 */
let rosterSeq = 0

async function searchRoster(term: string): Promise<void> {
  const seq = ++rosterSeq
  try {
    const res = await $fetch<RosterResponse>('/api/talkgroups/list', {
      query: { area: 'all', search: term, limit: ROSTER_LIMIT },
    })
    if (seq !== rosterSeq) return
    roster.value = res.data
    rosterMatched.value = res.matched
    rosterError.value = ''
  } catch (e) {
    if (seq !== rosterSeq) return
    roster.value = []
    rosterMatched.value = 0
    // Surfaced rather than swallowed: an empty roster section and a failed
    // roster search look identical, and one of them means "it does not exist".
    rosterError.value = apiError(e, 'Roster search failed')
  } finally {
    if (seq === rosterSeq) rosterPending.value = false
  }
}

watch(query, (q) => {
  const term = q.trim()
  if (rosterTimer) {
    clearTimeout(rosterTimer)
    rosterTimer = null
  }
  if (term.length < ROSTER_MIN_CHARS) {
    // Bump the sequence so a request already in flight cannot land after the
    // operator has cleared the field and repopulate a section they closed.
    rosterSeq++
    roster.value = []
    rosterMatched.value = 0
    rosterPending.value = false
    rosterError.value = ''
    return
  }
  rosterPending.value = true
  rosterTimer = setTimeout(() => { void searchRoster(term) }, ROSTER_DEBOUNCE_MS)
})

onUnmounted(() => {
  if (rosterTimer) clearTimeout(rosterTimer)
  // Blocks a landing response from writing to refs after teardown.
  rosterSeq++
})

/**
 * The armable list: the running capture's whitelist, filtered.
 *
 * Filtered locally on id and alpha, which is what it always did and what keeps
 * the common case instant and offline. The roster response then contributes
 * any WHITELISTED hit the local filter could not see — it matches description,
 * category and tag too — appended after the locally-matched rows rather than
 * merged into their busiest-first order, so the rows that were already there
 * do not shuffle under the operator as a request lands. Capped at
 * ROSTER_LIMIT, so a whitelisted row past the fortieth roster hit is reachable
 * by a narrower query rather than by scrolling; the local filter covers the
 * two fields anyone types.
 */
const shown = computed<StandbyRow[]>(() => {
  const q = query.value.trim().toLowerCase()
  const decorate = (t: FollowedRow): StandbyRow => ({ ...t, mark: talkgroupMark(t.encryption) })
  if (!q) return props.followed.map(decorate)

  const local = props.followed.filter(
    t => String(t.tgid).includes(q) || (t.alpha ?? '').toLowerCase().includes(q),
  )
  const seen = new Set(local.map(t => t.tgid))
  const byTgid = new Map(props.followed.map(t => [t.tgid, t]))
  const extra = roster.value
    .filter(r => r.inWhitelist && !seen.has(r.tgid))
    .map(r => byTgid.get(r.tgid))
    .filter((t): t is FollowedRow => t !== undefined)

  return [...local, ...extra].map(decorate)
})

/**
 * The reference list: roster hits the running capture is NOT recording.
 *
 * `inWhitelist` comes from the server, which reads the same whitelist file
 * followedTalkgroups() does through the same parser — so a row cannot be
 * armable in one list and uncaptured in the other. Whitelisted hits are
 * excluded here because they belong above, as controls.
 */
const rosterRows = computed<RosterRow[]>(() =>
  roster.value
    .filter(r => !r.inWhitelist)
    .map(r => ({ ...r, mark: talkgroupMark(r.encryption) })),
)

/**
 * "12 shown · 384 matched", or just the count when nothing was truncated.
 * The distinction matters: a capped list that says only "12" invites the
 * reading that the roster holds twelve of these.
 */
const rosterCountLabel = computed(() => {
  if (rosterPending.value) return 'searching…'
  const shownN = rosterRows.value.length
  if (rosterMatched.value > roster.value.length) return `${shownN} shown · ${rosterMatched.value} matched`
  return `${shownN}`
})

/**
 * The empty state, which has four genuinely different causes and used to have
 * one sentence. "No match" for a talkgroup that exists but is not being
 * recorded was the specific wrong answer this feature exists to replace, so it
 * cannot come back here either.
 */
const noMatchNote = computed(() => {
  if (!props.followed.length) return 'No session has written a whitelist yet.'
  // An empty query cannot reach any branch below: with no term `shown` IS the
  // whole whitelist, so an empty `shown` means an empty whitelist, which the
  // line above already answered. A "nothing matches that" string here would be
  // claiming a filter found nothing when no filter was applied.
  const term = query.value.trim()
  if (term.length < ROSTER_MIN_CHARS) {
    return `Nothing in the running capture matches. Type ${ROSTER_MIN_CHARS} characters to search the full roster.`
  }
  if (rosterPending.value) return 'Searching the full roster…'
  return 'Nothing in the running capture matches, and nothing in the 4,163-talkgroup roster either.'
})

/**
 * radioBusy and tracked are reported separately on purpose. A session started
 * from a shell rather than through this console reads busy-but-untracked, and
 * the feed works perfectly in that state — calling it "no session" would make a
 * working bay look dead.
 *
 * The fourth state — tracked with no radio, past a grace period — is the one
 * this component used to have no way to say at all: RADIO_PATTERNS matches
 * op25 itself, not its recorders, so op25 dying while its recorders survive
 * used to fall through to "idle" here, the calmest possible reading of a
 * session that is open with nothing receiving. receiverStatus() (utils/
 * captureStatus.ts) is what tells "just started" apart from "actually
 * stalled" — see that file for the grace period and its measurement.
 *
 * No ticking clock drives this: Date.now() is read fresh each time this
 * computed re-evaluates, which is only when a prop changes (a fresh
 * /api/listen/followed read). A timer that just re-ran the same stale
 * radioBusy/tracked values through Date.now() would buy nothing — the radio
 * state itself only changes on the next server read, ticker or not.
 */
const status = computed(() => receiverStatus({
  radioBusy: props.radioBusy,
  tracked: props.tracked,
  sessionStartedAt: props.sessionStartedAt,
  nowMs: Date.now(),
}))

/**
 * THE SILENT-EXPIRY PROBLEM — see utils/captureStatus.ts's captureExpiry()
 * for the full incident writeup. Short version: `on air · console session`
 * used to read identically one second before a bounded session's `--pd`
 * duration ran out and one hour into a healthy run, because nothing here
 * knew the session HAD a deadline. `expiry` is the pure timing math
 * (captureExpiry(), unit-tested for its own boundaries); the wall-clock
 * string below is assembled here rather than in that file so the tested
 * logic stays free of `toLocaleTimeString`'s locale/timezone dependence —
 * exactly the same split this component already keeps between
 * receiverStatus() (state) and RECEIVER_LINE/RECEIVER_NOTE (strings).
 *
 * Same "no ticking clock" note as `status` above applies to `expiresAtMs`
 * itself — it is a fixed point in time, so a stale computed (this only
 * re-evaluates when a prop changes, i.e. on the next /api/listen/followed
 * poll) still reads correctly. It would NOT be safe to derive a live
 * countdown ("ends in 12m") from `remainingMs` the same way: that number
 * only updates on the next poll too, so a countdown would freeze at
 * whatever value it had when this last recomputed and go stale the moment
 * time keeps moving without a fresh poll — decaying into exactly the kind
 * of quietly-wrong readout this feature exists to remove. An absolute clock
 * time stays true regardless of when it was last rendered, which is why
 * that is the only form used below.
 */
const expiry = computed(() => captureExpiry({
  sessionStartedAt: props.sessionStartedAt,
  sessionDurationSec: props.sessionDurationSec,
  nowMs: Date.now(),
}))

/**
 * "It ends at 14:32", appended to the on-air note — or null when there is
 * nothing to say (no duration recorded, i.e. an unbounded session), which
 * leaves RECEIVER_NOTE.onAirConsole completely unchanged, the no-regression
 * case utils/captureStatus.test.ts covers.
 *
 * `remainingMs <= 0` (the session has already run past its requested
 * duration but the next poll hasn't yet caught the resulting stop) gets its
 * own honest phrasing rather than a clock time that would read as "ends in
 * the past" — the boundary utils/captureStatus.test.ts's captureExpiry
 * suite covers numerically.
 */
const expiryNote = computed(() => {
  const { expiresAtMs, remainingMs } = expiry.value
  if (expiresAtMs === null || remainingMs === null) return null
  if (remainingMs <= 0) {
    return 'It has run past its requested duration and can stop at any moment.'
  }
  const clock = new Date(expiresAtMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return `It ends at ${clock} unless stopped first — nothing renews it automatically.`
})

// Record, not a switch: TS's exhaustiveness check on a Record's key type
// catches a future ReceiverStatus member left unhandled at compile time,
// which a switch with no default cannot (and vue/return-in-computed-property
// rejects a switch with no default anyway, since it cannot see the switch is
// exhaustive over a closed union).
const RECEIVER_LINE: Record<ReceiverStatus, string> = {
  onAirConsole: 'on air · console session',
  onAirOutside: 'on air · outside session',
  stalled: 'stalled · console session open',
  idle: 'idle',
}

const RECEIVER_NOTE: Record<ReceiverStatus, string> = {
  onAirConsole: 'This console started the capture and can stop it.',
  onAirOutside: 'Something else started this capture. The bay still fills; stopping it is not this console’s to do.',
  stalled: 'A session is still open but nothing is receiving. Start will refuse while it stays open — Stop reaches the recorders even though op25 is gone.',
  idle: 'No capture running. Filed strips still read; nothing new will land.',
}

const receiverLine = computed(() => RECEIVER_LINE[status.value])

// Expiry is appended only to the on-air-console note, per the brief: this
// adds expiry information to the existing on-air case, not a fifth state —
// 'onAirOutside' has no session row to read a duration from, 'stalled' and
// 'idle' are not describing a running capture at all.
const receiverNote = computed(() => {
  const base = RECEIVER_NOTE[status.value]
  if (status.value === 'onAirConsole' && expiryNote.value) {
    return `${base} ${expiryNote.value}`
  }
  return base
})

/* ---------------------------------------------------------------------------
 * RECEIVER LAYOUT — how many radios and voice streams are in play
 *
 * The counts themselves are derived server-side (utils/receiverLayout.ts,
 * unit-tested); everything below is only phrasing, kept here for the same
 * reason RECEIVER_LINE/RECEIVER_NOTE and `expiryNote` are: the tested file
 * stays free of pluralisation and locale-shaped string work, and the strings
 * stay next to the markup that renders them.
 * ------------------------------------------------------------------------- */

/**
 * "2 radios", "1 radio", "no voice receivers" — never "0 voice receivers".
 * A zero-receiver layout is a real config (make_multirx_cfg.py will happily
 * build devices with no channels bound), and a bare `0` beside two other
 * counts reads as a rendering bug rather than as a statement.
 */
function countOf(n: number, noun: string): string {
  if (n === 0) return `no ${noun}s`
  return `${n} ${noun}${n === 1 ? '' : 's'}`
}

/**
 * "700 MHz" per leg — or the tuned centre to three decimals when two legs
 * land in the same 100 MHz band, where the band label would print the same
 * string twice ("700 MHz: 3 voice · 700 MHz: 7 voice") and distinguish
 * nothing. Two 700 MHz legs are not the shape this system runs today, but the
 * fallback costs one line and the failure it prevents is a display that looks
 * authoritative while being unreadable. `centreHz` is carried by
 * ReceiverLeg for exactly this.
 */
function legLabels(layout: ReceiverLayout): string[] {
  const bands = layout.legs.map(l => l.bandMhz)
  const distinct = new Set(bands).size === bands.length
  return layout.legs.map(l => distinct
    ? `${l.bandMhz} MHz`
    : `${Math.round(l.centreHz / 1e3) / 1000} MHz`)
}

/**
 * The totals: radios, the voice-receiver pool, and the control receiver(s).
 *
 * Voice and control are stated separately rather than as one channel count
 * because only the voice receivers can take a call — the `CC` receiver
 * decodes the trunking control stream and records nothing (see
 * utils/receiverLayout.ts's CONTROL_NAME). An operator reasoning about a
 * missed call needs the pool size, and 11 would be the wrong number.
 *
 * The control clause is dropped entirely when there is none: the generator
 * emits `CC` only for a leg that has a control channel, so "no control
 * channels" is a legal config, and saying so in a totals line nobody reads
 * for that would be noise.
 */
const layoutLine = computed(() => {
  const l = props.receiverLayout
  if (!l) return null
  const bits = [countOf(l.radios, 'radio'), countOf(l.voiceTotal, 'voice receiver')]
  if (l.controlTotal > 0) bits.push(countOf(l.controlTotal, 'control channel'))
  return bits.join(' · ')
})

/**
 * The per-leg split — "700 MHz: 3 voice + 1 control · 800 MHz: 7 voice" —
 * which is the half of the old console's display that the totals cannot
 * replace: the two legs have different receiver counts (3 and 7 today), so a
 * call missed on the 700 MHz leg is missed against a pool of three, not ten.
 *
 * Suppressed for a single-leg capture, where each figure would simply repeat
 * the totals line above it.
 */
const layoutLegLine = computed(() => {
  const l = props.receiverLayout
  if (!l || l.legs.length < 2) return null
  const labels = legLabels(l)
  return l.legs.map((leg, i) => {
    const rx = [`${leg.voice} voice`]
    if (leg.control > 0) rx.push(`${leg.control} control`)
    return `${labels[i]}: ${rx.join(' + ')}`
  }).join(' · ')
})

/**
 * WHERE THE LAYOUT COMES FROM, AND WHETHER IT IS THE RUNNING ONE
 * ---------------------------------------------------------------
 * lwin_both.json is rewritten by every capture start, so while a capture of
 * OURS is up the layout is that capture's by construction. When nothing is
 * running it describes the LAST one — still worth showing (it is what a Start
 * will build again), but presenting it then as if it were live would be
 * precisely the quietly-wrong readout the expiry work above exists to
 * remove. So the caption is keyed on the same `ReceiverStatus` the Receiver
 * line is, and each state says which of those it is.
 *
 * 'onAirOutside' gets the most guarded wording of the four: a capture this
 * console did not start MAY have been launched some other way (a hand-rolled
 * multi_rx invocation, or lwin_listen_multi.sh with a different `-o`), in
 * which case the file on disk is not describing what is on the air at all.
 * That is unlikely rather than impossible, and the honest thing is to name
 * the doubt rather than let the operator assume it away.
 */
const LAYOUT_SOURCE: Record<ReceiverStatus, string> = {
  onAirConsole: 'What this capture was launched with, read from the op25 config on disk.',
  onAirOutside: 'The last layout written to disk. This capture was started elsewhere, so it may be running a different one.',
  stalled: 'What this open session was launched with. op25 is gone, so nothing is receiving on any of it.',
  idle: 'What the last capture was launched with. Nothing is receiving now.',
}

/**
 * Appended to every one of the four, because the operator's next thought on
 * reading a receiver count is reliably "can I change it". They cannot, here:
 * the delegated request does carry `nVoice700`/`nVoice800`
 * (server/utils/processes.ts's buildControlRequest()), but the Capture block
 * below deliberately does not expose them — an operator-rare setting whose
 * safe values were measured, not chosen. Appended in one place rather than
 * written into all four strings so it cannot be lost from one of them.
 */
const NOT_ADJUSTABLE = 'The counts are not adjustable from the console.'

const layoutNote = computed(() => {
  if (!props.receiverLayout) return null
  return `${LAYOUT_SOURCE[status.value]} ${NOT_ADJUSTABLE}`
})

/* ===========================================================================
 * THE CREASES — which folds are open, and making an open one visible
 *
 * Two of them, one vocabulary (bay.css's `.fold`): the Receiver block's layout
 * reference, and the Capture block's setup fields. Both start closed and
 * neither is persisted. Closed is not a state where a fact is hidden — each
 * crease prints its own summary, `layoutLine` and `captureSummary` — and a
 * remembered fold would mean an operator opening this console to a layout
 * nobody chose today.
 * ========================================================================= */

/** Reference an operator reads once, when reasoning about a missed call. */
const layoutOpen = ref(false)

/** Fields touched at the start of a capture and not again for hours. */
const captureOpen = ref(false)

const receiverBlock = ref<HTMLElement | null>(null)
const captureBlock = ref<HTMLElement | null>(null)

/**
 * Opening a crease has to SHOW what it opened.
 *
 * The block region scrolls itself once its blocks no longer fit (bay.css's
 * `.stack__head`), and at 1366x768 an open crease is exactly what stops them
 * fitting — so without this the operator presses `+`, what unfolded lands
 * below that region's scroll edge and nothing visibly happens.
 *
 * The whole BLOCK is revealed, not just the fold body: the Capture block's
 * Start button sits after the fields, so bringing only the body into view
 * would open the settings and push the button they are settings FOR out of
 * sight. `block: 'nearest'` moves the region by the minimum needed and does
 * nothing at all when the block already fits, so the same click at 1600x900,
 * where there is room, is not jolted for the benefit of the laptop. Next tick,
 * because `v-show` has not laid the body out yet on the tick the click lands.
 */
async function reveal(el: HTMLElement | null): Promise<void> {
  await nextTick()
  el?.scrollIntoView({ block: 'nearest' })
}

function toggleLayout(): void {
  layoutOpen.value = !layoutOpen.value
  if (layoutOpen.value) void reveal(receiverBlock.value)
}

function toggleCapture(): void {
  captureOpen.value = !captureOpen.value
  if (captureOpen.value) void reveal(captureBlock.value)
}

/* ===========================================================================
 * CAPTURE CONTROL — starts and stops the radio, not the audio feed
 *
 * `canStart`/`canStop` are NOT read off `status` above: `receiverStatus()`
 * intentionally collapses "just started, op25 not up yet" into the same
 * `'idle'` display state as genuine idle (see STALL_GRACE_MS), which is
 * right for the Receiver line but wrong for these two — see
 * canStartCapture()/canStopCapture()'s own docstring in
 * utils/captureStatus.ts for why they read `tracked`/`radioBusy` directly.
 * ========================================================================= */

interface ApiResponse<T> { success: boolean, data?: T, error?: string }

const canStart = computed(() => canStartCapture({ tracked: props.tracked, radioBusy: props.radioBusy }))
const canStop = computed(() => canStopCapture({ tracked: props.tracked }))

/**
 * Seconds, not the ISO-ish shape a `<input type=date>` would use — this is
 * exactly the `durationSec` field capture_control.py validates, sent
 * unconverted. `number | string | null` because `v-model.number` runs the
 * raw DOM string through Vue's `looseToNumber` (parseFloat, falling back to
 * the ORIGINAL string on `NaN`) — so clearing the field puts the empty
 * STRING `''` here, not `null` or `0`. See `isValidCaptureDuration`'s own
 * docstring in utils/listenControl.ts; that one function is the only place
 * this shape needs to be reasoned about.
 */
const duration = ref<number | string | null>(DEFAULT_CAPTURE_DURATION_SEC)

/**
 * Which talkgroups the NEXT capture follows.
 *
 * Defaults to `pd`, what every capture ran when this was not selectable —
 * so a fresh page offers the familiar profile and a wider one is always a
 * deliberate act. It is a `<select>` rather than free text because the value
 * is checked against an allowlist three times before it becomes argv (here,
 * `buildControlRequest()`, and `capture_control.py`'s `PRESET_ARGV` lookup);
 * offering a control that could produce a value any of those refuse would be
 * the same mistake this whole block was written to avoid.
 */
const preset = ref<CapturePreset>(DEFAULT_CAPTURE_PRESET)
const ess = ref(false)
const includeEncrypted = ref(false)
const busy = ref(false)
const captureError = ref('')

const durationValid = computed(() => isValidCaptureDuration(duration.value))

/**
 * "86400s · 24h" beside the field. A bare second count is the same trap two
 * real sessions fell into, both with 10800 (3h) typed in as whatever number
 * came to mind and no indication it meant the capture would silently end at
 * that mark with nothing to renew it (recorded at the time in the deleted
 * ListenControl.vue's `duration` docstring, 166fbb8) — showing the
 * human-scale equivalent live is what makes the number in the box a
 * considered choice rather than an unchecked one.
 */
const durationHuman = computed(() => {
  const s = duration.value
  if (typeof s !== 'number' || !Number.isFinite(s)) return ''
  const h = s / 3600
  return `${Math.round(h * 100) / 100}h`
})

/**
 * What Start would send, printed on the crease — `pd · 24h`, plus a flag for
 * each option that is ON.
 *
 * The point of a folded flight strip is that it still reads: an operator must
 * never have to unfold this to find out what the button beneath it does. The
 * duration is the HUMAN figure (`durationHuman`) rather than the raw seconds
 * for the reason that computed exists at all, and it is dropped rather than
 * faked when the field does not hold a number — that state has the
 * `!durationValid` line beneath the crease saying so in full, and a summary
 * reading `pd · NaNh` would be a second, worse account of the same fact.
 * Only the ON options are listed, so the ordinary case is two terms and not a
 * row of negations to read past.
 */
const captureSummary = computed(() => {
  const bits: string[] = [preset.value]
  if (durationHuman.value) bits.push(durationHuman.value)
  if (ess.value) bits.push('ESS')
  if (includeEncrypted.value) bits.push('+encrypted')
  return bits.join(' · ')
})

const captureButtonLabel = computed(() => {
  if (busy.value) return canStop.value ? 'Stopping…' : 'Starting…'
  return canStop.value ? 'Stop capture' : 'Start capture'
})

// Distinct from receiverNote above (which explains the RECEIVER, i.e. what
// is physically happening) — this explains what THIS BUTTON will do, which
// is not always the same sentence: 'onAirOutside' already has a full
// explanation on the Receiver block above, so this one stays short and
// points back at it rather than repeating it. Record, not a switch, for the
// same exhaustiveness reason as RECEIVER_LINE/RECEIVER_NOTE above.
const CAPTURE_HINT: Record<ReceiverStatus, string> = {
  onAirConsole: 'Stop ends the capture this console started, immediately.',
  onAirOutside: 'Already on air from elsewhere — see the receiver note above. This console can’t start or stop it.',
  stalled: 'Stop releases the recorders even though op25 is gone. Start stays refused until then.',
  idle: 'Runs for the duration above, or until Stop is pressed.',
}

/**
 * One override on top of CAPTURE_HINT, for the same reason canStart/canStop
 * are not looked up from `status` at all (see the block comment above): a
 * session that just opened but hasn't granted yet also reads as `'idle'`
 * here, and CAPTURE_HINT's idle line ("Runs for the duration above...")
 * would be actively wrong right when the operator is watching for it
 * hardest — Start is refused (canStart is false) while it claims Start is
 * what happens next, and it says nothing about the Stop button sitting
 * right there, lit. `canStop` is what actually tells this apart from
 * genuine idle (idle has `tracked` false, so canStop is false there too),
 * so branching on it instead of adding a fifth ReceiverStatus member is
 * what keeps this a display-only distinction rather than a new state
 * something else in the bay would also have to learn.
 */
const captureHint = computed(() => {
  if (status.value === 'idle' && canStop.value) {
    return 'Session just opened — op25 hasn’t granted yet. Stop is available if it doesn’t come up shortly.'
  }
  return CAPTURE_HINT[status.value]
})

async function startCapture(): Promise<void> {
  // Belt-and-braces: the button is already disabled for every one of these,
  // but a stray extra call (e.g. a fast double-click landing between one
  // Vue render and the next) must not re-enter a request already in flight.
  // The `typeof` check is what actually narrows `duration.value` to `number`
  // below — `durationValid.value` says the same thing but TS can't follow
  // that link through a separate computed.
  if (!canStart.value || !durationValid.value || busy.value || typeof duration.value !== 'number') return
  busy.value = true
  captureError.value = ''
  try {
    const res = await $fetch<ApiResponse<unknown>>('/api/listen/start', {
      method: 'POST',
      body: buildCaptureStartBody({
        duration: duration.value,
        ess: ess.value,
        includeEncrypted: includeEncrypted.value,
        preset: preset.value,
      }),
    })
    if (!res.success) captureError.value = res.error ?? 'Failed to start'
  } catch (e) {
    // Surfaced verbatim — see utils/listenControl.ts's apiError() for why a
    // bare FetchError.message would hide the control API's own 400/409/502
    // text (e.g. "A listening session is already running").
    captureError.value = apiError(e, 'Failed to start')
  } finally {
    busy.value = false
    emit('refreshCapture')
  }
}

async function stopCapture(): Promise<void> {
  if (!canStop.value || busy.value) return
  busy.value = true
  captureError.value = ''
  try {
    const res = await $fetch<ApiResponse<unknown>>('/api/listen/stop', { method: 'POST' })
    if (!res.success) captureError.value = res.error ?? 'Failed to stop'
  } catch (e) {
    captureError.value = apiError(e, 'Failed to stop')
  } finally {
    busy.value = false
    emit('refreshCapture')
  }
}
</script>
