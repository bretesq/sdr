# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One operator, one machine. The person at the keyboard owns the radio hardware, the
database and every decision the console can make — there are no accounts, roles,
concurrent sessions or second audiences to design around.

Two situations, and the design serves both:

- **Operating.** Sitting with the receiver running, wanting to know what is happening on
  the air right now, or to find something that already happened.
- **Investigating.** Working a signal, a protocol or an encryption question over hours or
  days, where the console is instrumentation and the findings are the output.

Expert defaults beat onboarding. Density beats hand-holding. The operator does not need
to be protected from their own tools, and an action that is obvious to them does not need
a confirmation ceremony.

## Product Purpose

Receive, decode, record and review the Louisiana Wireless Information Network (LWIN) P25
trunked radio system from a fixed location in Baton Rouge — and make the resulting corpus
searchable and listenable.

Success has two shapes, matching the two situations above: an operator can hear the
traffic they care about within seconds of it happening, and can later answer a question
about the corpus ("what was said on this talkgroup around this time") without leaving the
console.

## Positioning

Two modes of one instrument, deliberately equal — a live monitoring console and a
research/decode lab. Neither is subordinate to the other, and design work should stop
treating one as the real product and the other as a side effect.

What a neighbouring product could not truthfully copy: this console sits directly on top
of its own receiver and its own decode pipeline. It is not a feed reader. It holds the
grant census, the call corpus, the reference database and the live audio path in one
place, so a question can move from "what am I hearing" to "what has this talkgroup done
for the past three days" to "what does the protocol say is happening" without changing
tools.

## Operating Context

- Runs on the operator's own machine, reached over the LAN. Recordings and the database
  stay local; nothing is redistributed.
- The radio is a **shared, single resource**: two HackRFs (One at 8 Msps on the 700 leg,
  Pro at 12 Msps on the 800 leg) driving one op25 `multi_rx.py` process with a pool of
  voice receivers and one control receiver pinned to 773.05625 MHz. Only one capture
  session can exist at a time, and the console is not always the thing that started it —
  a session launched from a shell is invisible to the console's own session store but is
  still fully operational.
- A session is not always running. An idle radio is a normal, frequent state, not an
  error, and the interface should read as deliberate when nothing is on the air.
- Long-running work happens outside the console: capture sessions of hours, transcription
  backlogs, and GPU key-recovery runs on a separate machine. The console reports on that
  work more often than it starts it.
- Traffic is bursty and often silent. Calls average ~4 seconds; a quiet hour and a
  200-call hour are both ordinary.

## Capabilities and Constraints

**Confirmed capabilities**

- Start and stop capture sessions by preset, explicit talkgroup IDs, tag, or regex, with
  independent switches for partial/encrypted inclusion and statewide scope.
- Browse and search the call corpus — talkgroup, alpha, description, category, filename,
  radio ten-codes, and full transcript text (FTS5). Audio served with HTTP Range support.
- Live listening: select talkgroups the running session follows and hear each call play
  a few seconds after it ends, scanner-style.
- Browse the LWIN reference database (4,163 talkgroups, 149 sites) with category and
  encryption filters.
- Local speech-to-text over recordings via a persistent CUDA whisper.cpp server, with a
  CPU fallback.

**Data**

SQLite at `sdr.db`: `calls` (~7.4k), `talkgroups` (4,163), `grants` (~423k), `sites`
(149), `call_codes`, `sessions`, plus a `calls_fts` full-text index. Recordings are
per-call WAVs in `recordings/`, gitignored.

**Technical constraints**

- Nuxt 3 + Nitro + Vue 3 + PrimeVue 4 (Aura), Node 22, `node:sqlite`.
- Vitest collects only `server/**/*.test.ts` and `utils/**/*.test.ts`; Vue components and
  composables have no automated coverage by design, and are verified by running the app.
  A Python `unittest` suite covers `scripts/`.
- All database reads live in `server/utils/queries.ts`; API routes stay thin.
- The audio stream carries no talkgroup ID — it is recovered by tailing op25's log in
  parallel, per receiver. Anything touching live audio inherits that coupling.
- Encrypted-call state is read from `algid`/`keyid`, not from the reconciliation columns,
  which are NULL on live rows.

**Undecided**

- Whether the console will ever be reachable beyond the LAN. Until decided, treat
  local-only as the operating assumption and do not design for untrusted viewers.

## Brand Commitments

None established. The project has no name beyond its repository, no logo, and no voice
commitments. Nothing here is binding on future visual work.

## Evidence on Hand

- `README.md` — the survey and decode record: system identity (NAC 0x1BD, WACN 0xBEE00,
  RFSS 1 Site 13 "Baton Rouge Simulcast"), adjacent-site ring, frequencies verified
  against RadioReference, FM RDS decodes, hardware, and hard-won gotchas.
- `OBSERVATIONS.md` — accumulated findings, including capabilities the documentation
  records as mutually exclusive with a single receiver.
- `reference/` — the complete LWIN reference database pulled locally.
- A live corpus: ~7.4k recorded calls with transcripts, ~423k grant records.

**Do not fabricate.** There are no users besides the operator, no customers, no
testimonials, no pricing, no deployment story, and no uptime or performance claims. The
README's counts and decode results are real measurements — cite them as measurements, at
the values actually recorded, or not at all.

**Known stale:** the README's Legal note states that encrypted talkgroups "were left
alone." That is no longer accurate (see below) and the section needs revising by the
operator.

## Research Capabilities

Recorded here because they are real and durable, but they belong to the investigation
side of the instrument, not to routine operating. Future UI work should **not** assume
these are everyday concerns or build the main interface around them.

- P25 ADP (`algid` 0xAA) key recovery by known-plaintext attack against harvested
  ciphertext, using GPU brute force on a separate machine. Four key IDs are currently
  held and decrypt live traffic to intelligible speech.
- Supporting tooling: pair harvesting from op25 logs, superframe candidate selection
  ranked by proximity to a transmission edge, and key verification against decrypted
  audio rather than a statistical proxy.
- Key material lives in a gitignored keyfile and must never reach the browser or a
  commit. Key **IDs** are not secret — they travel in the clear in every P25 ESS field —
  and surfacing them is correct.

## Product Principles

1. **Report the real state, never a convenient one.** A session started outside the
   console, an idle radio, a key that is not held, a call that could not be played — each
   has a truthful representation, and none is allowed to masquerade as another.
2. **Two modes, one instrument.** Operating and investigating are equal. Neither should
   be reachable only by going through the other.
3. **The corpus is the product's memory.** Anything recorded should be findable later by
   what the operator actually remembers about it — a phrase, a talkgroup, a code, a time.
4. **Expert density over guardrails.** One operator who owns the hardware does not need
   protection from it. Prefer information per screen and directness over ceremony.
5. **Local by default.** Recordings, transcripts, the database and key material stay on
   this machine. Nothing is designed as if it will be shared.
