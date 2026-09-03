/**
 * How many radios and voice streams the running capture actually has, read
 * off the config the capture was launched with.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The old console rendered a config summary and two spinners — `700 MHz
 * voice receivers` / `800 MHz voice receivers` — so an operator could see a
 * two-radio, ten-receiver session was in fact a two-radio, ten-receiver
 * session (see the since-deleted ListenControl.vue's `configSummary` at
 * 02c2804, and its own
 * comment: "a reloaded page has no other way to tell a 9-receiver two-radio
 * session from a 1-receiver one"). The Strip Bay redesign dropped all of it,
 * leaving `{n} armed · {idle|on air|stalled}` — which says nothing about the
 * receiver pool a missed call would have been missed by. This is the
 * derivation half of putting that back.
 *
 * WHY lwin_both.json AND NOT THE SESSION CONFIG
 * ----------------------------------------------
 * The tracked session's stored config (server/utils/session.ts) records what
 * the operator ASKED for; `lwin_both.json` is what scripts/make_multirx_cfg.py
 * actually built and what multi_rx.py was actually handed — devices, channels,
 * per-channel device binding and tuned centres, all validated by that script's
 * validate() before a radio is opened. It is regenerated on every capture
 * start, so for a running capture it is current by construction. It is also
 * the only one of the two that survives a console restart, and the only one
 * that exists at all for a capture started from a shell.
 *
 * WHAT MAY NOT CROSS THE WIRE
 * ---------------------------
 * The channel objects in that file carry absolute filesystem paths —
 * `whitelist`, `blacklist` and `crypt_keys` (which names lwin_keys.json, the
 * key file this project never surfaces). So this returns a CLOSED shape of
 * derived counts and frequencies and nothing else: no caller can accidentally
 * forward a raw channel, because a raw channel never leaves this function.
 */

/** One radio's leg of the capture: a device plus the channels bound to it. */
export interface ReceiverLeg {
  /**
   * The generator's own device name ('one' / 'pro' — HackRF One / HackRF
   * Pro). Not for display: it means nothing to an operator. Carried because
   * it is the key channels are bound by, and because it is what a log or a
   * hand-run of make_multirx_cfg.py calls this leg.
   */
  device: string
  /** The device's tuned centre, Hz, straight from the config. */
  centreHz: number
  /**
   * The 100 MHz band that centre falls in — 700 for 771.4185 MHz, 800 for
   * 855.725 MHz — which is how this system's two legs are named everywhere
   * else (`--legs 700,800`, the old console's two spinners, OBSERVATIONS.md).
   *
   * Derived from the TUNED CENTRE rather than from the `VC700_`/`VC800_`
   * channel-name prefix, which is the obvious alternative and the one a
   * reader will wonder about: the name prefix comes from `leg['name']` in
   * make_multirx_cfg.py and is only a label, so a leg retuned without being
   * renamed would keep announcing the old band. The centre cannot disagree
   * with where the radio is actually listening. Floor rather than round on
   * purpose — 771.4 MHz rounds UP to 800 and would report the 700 MHz leg as
   * the 800 MHz one.
   */
  bandMhz: number
  /** Voice receivers bound to this device. */
  voice: number
  /**
   * Control-channel receivers bound to this device — normally 1 on whichever
   * leg has a control channel, and 0 on the other. make_multirx_cfg.py emits
   * a `CC` channel only `if leg['control']`, so 0 on every leg is a legal
   * config, not a parse failure.
   */
  control: number
}

export interface ReceiverLayout {
  /** Devices the config opens. One HackRF each. */
  radios: number
  /** Voice receivers across all legs — the size of the concurrent-call pool. */
  voiceTotal: number
  /** Control-channel receivers across all legs. Never voice. See CONTROL_NAME. */
  controlTotal: number
  /** One entry per device, in the config's own device order (700 then 800). */
  legs: ReceiverLeg[]
}

/**
 * A channel that is a control receiver rather than a voice one.
 *
 * make_multirx_cfg.py names it exactly `CC` and pins it to the control
 * channel with a whitelist holding one talkgroup that does not exist, so
 * find_talkgroup never matches and it never calls tune_voice — it decodes the
 * trunking control stream and records nothing. Counting it as a voice
 * receiver would overstate the concurrent-call pool by one, which is exactly
 * the number an operator would use to reason about a missed call.
 *
 * `CC_1` etc. is matched too, against the day a second control receiver is
 * pinned on the other leg.
 *
 * WHY THIS IS A DENY-LIST AND VOICE IS "EVERYTHING ELSE"
 * ------------------------------------------------------
 * The tempting inverse — count only names matching `VC` — silently reports
 * zero voice receivers the moment the generator renames its voice channels,
 * and a zero that looks deliberate is worse than a wrong label. The
 * generator's only non-recording receiver is this one, so "not the control
 * channel" is the accurate definition of a voice receiver: a future voice leg
 * named something new is still counted, and only a new NON-voice channel type
 * would need this rule revisited — a change that cannot happen without
 * touching make_multirx_cfg.py, where this comment's counterpart lives.
 */
const CONTROL_NAME = /^CC(?:_|$)/i

/** Hz per 100 MHz band. See ReceiverLeg.bandMhz. */
const BAND_HZ = 100_000_000

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/**
 * Derive the receiver layout from the text of an op25 multi_rx config.
 *
 * Returns null for anything this cannot honestly read — unparseable JSON, no
 * `channels` array, no usable device — rather than a layout of zeroes, which
 * the bay would render as a confident claim that a capture is running on no
 * radios. `followedTalkgroups()` in server/utils/queries.ts sets the
 * precedent for the shape of that quiet failure (it returns `[]` on a missing
 * whitelist: "no session has ever run on this checkout"), and a fresh
 * checkout where no capture has ever run is exactly the case that hits it.
 *
 * A config WITH devices but with an empty `channels` array is not a failure:
 * it is a real, if useless, config, and it returns a layout with zero
 * receivers so the caller can say so.
 */
export function parseReceiverLayout(text: string): ReceiverLayout | null {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    // Half-written file (the generator writes it on every capture start, and
    // this can be read mid-write) or not a config at all. Either way there is
    // nothing to report, and reporting nothing is the whole point.
    return null
  }
  if (!isRecord(raw)) return null
  if (!Array.isArray(raw.devices) || !Array.isArray(raw.channels)) return null

  // Legs keyed by device name, built from `devices` FIRST so a device with no
  // channels bound to it still appears (it is a radio that will be opened) and
  // so the order below is the config's own device order rather than whatever
  // order the channels happen to be listed in.
  const legs = new Map<string, ReceiverLeg>()
  for (const d of raw.devices) {
    if (!isRecord(d)) continue
    const { name, frequency } = d
    // A device with no name cannot be the target of a channel's `device`
    // binding, and one with no centre has no band to report. Both are
    // malformed at the level of a single entry, so drop the entry rather than
    // the whole config: the remaining legs are still true.
    if (typeof name !== 'string' || !name) continue
    if (typeof frequency !== 'number' || !Number.isFinite(frequency)) continue
    if (legs.has(name)) continue          // two devices sharing a name: keep the first
    legs.set(name, {
      device: name,
      centreHz: frequency,
      bandMhz: Math.floor(frequency / BAND_HZ) * 100,
      voice: 0,
      control: 0,
    })
  }
  if (!legs.size) return null

  let voiceTotal = 0
  let controlTotal = 0
  for (const c of raw.channels) {
    if (!isRecord(c)) continue
    const { name, device } = c
    if (typeof name !== 'string' || typeof device !== 'string') continue
    const leg = legs.get(device)
    // A channel bound to a device the config never declares is counted
    // NOWHERE — not in a leg, not in the totals. multi_rx.py has no radio to
    // give it, so it cannot receive anything; including it in the totals would
    // inflate the concurrent-call pool by a receiver that does not exist,
    // which is the one number this display is read for.
    if (!leg) continue
    if (CONTROL_NAME.test(name)) {
      leg.control += 1
      controlTotal += 1
    } else {
      leg.voice += 1
      voiceTotal += 1
    }
  }

  return { radios: legs.size, voiceTotal, controlTotal, legs: [...legs.values()] }
}
