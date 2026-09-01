# Observed Encryption: per-call truth, reconciliation, and ADP capture

**Date:** 2026-09-01
**Author:** bretesq
**Status:** Design (Ready for Implementation)

## Executive Summary

A recording can say its talkgroup is "fully encrypted" while playing perfectly
clear voice. That is not a decoding failure — it is a category error in what the
label means.

`enc` is scraped off RadioReference's HTML badge (`fetch_lwin_db.py:37`) and
describes how a talkgroup is *documented*, not what any transmission carried.
P25 encryption is per-transmission: each LDU2 carries an ESS whose ALGID is sent
**in the clear** (`0x80` = clear, `0xAA` = ADP/RC4). An encryption-capable
talkgroup passes clear voice whenever radios transmit in the clear.

This design makes the **observed** encryption state a first-class fact: harvested
from op25 logs, bound to individual calls, surfaced in the UI, reconciled against
RadioReference through a human-approved override file, and — because the same
logs already contain the ciphertext — routed into the ADP key-recovery pipeline.

`reference/lwin_talkgroups.json` is **never rewritten**. It stays the upstream
scrape. Every artifact here is re-derivable from the logs with one command.

---

## Measured Baseline

All figures from `sdr.db` and `results/*.log` on 2026-09-01.

**The mislabelling is large and one-sided.**

| | |
|---|---|
| talkgroups in reference DB | 4163 (clear 3193, full 856, partial 114) |
| calls on talkgroups flagged `enc=full` | 377 |
| of those, containing real speech | **367 (97%)** |
| distinct `full` talkgroups producing speech | 16 |

**Encryption is observed only rarely, and only at high verbosity.**

| | |
|---|---|
| calls total | 4606 |
| calls with no ESS observed (`algid IS NULL`) | **3746 (81%)** |
| observed `0x80` CLEAR | 762 |
| observed `0xAA` ADP/RC4 | 94 |
| observed bit-error ALGIDs (`0x0E`,`0x45`,`0xA8`,`0xB8`) | 1 each |
| distinct ADP key ids observed | 5 (`0x22`, `0x8`, `0x2F08`, `0x1`, `0x2EF4`) |

ESS lines carry `rs_errs`, and it is not always zero — Reed-Solomon residual
errors mean both ALGID and KEYID can arrive corrupted. The four one-off ALGIDs
above are almost certainly bit errors, not four exotic ciphers.

ESS coverage tracks op25 verbosity exactly. Sessions run with `--ess` (`-v 10`)
reach 54–94% ESS coverage; the rest sit at 2%. `lwin_listen_multi.sh` already
defaults to `-v 10` for the grant census.

**Two talkgroup failure shapes, needing two kinds of evidence:**

```
TG17086  17 JAIL SEC1   RR=full   ESS: clear 27, ADP 3, unseen 124   -> genuinely MIXED
TG17166  17-BRPD TLK1   RR=full   ESS: none at all;  21 calls of speech
```

TG17086 is caught by ESS. TG17166 has **zero** ESS observations — the only
evidence it is clear is that we transcribed 21 calls of intelligible speech.
Any reconciliation that relies on ESS alone will miss it and the three other
BRPD TLK/MOTO talkgroups shaped like it.

**The ciphertext is already being captured and thrown away.**
`results/op25_multi.log` — an *ordinary* listening session, not a dedicated
capture — contains **3249 `IMBE (CIPHERTXT)` lines and 832 ESS lines**. Nothing
harvests them; `extract_enc_pair.py` is only ever pointed at
`results/lwin_enc_capture.log`.

**The current live attribution can be wrong.** `op25_log.py` holds the ESS in a
single slot for `TG_TTL = 12.0` seconds. The op25 line
(`ESS: algid=aa, keyid=22, mi=..., rs_errs=4`) carries no talkgroup, so nothing
binds the ESS to a call: an encrypted call can stamp its ALGID onto the *next*
clear call on the same receiver. Observed consequences — rows flagged `0xAA`
whose audio is plainly clear:

```
TG5000  11.0s  'We are coming up on Livingston right now.'
TG6078  10.3s  'Sam, can you run Louisiana please? X-ray 841297.'
TG17027  5.0s  '>> Track 44, no answer, left voicemail.'
```

`op25_log.py:132` already concedes the disagreement in a comment. This design
acts on it.

---

## Architecture

One new component, `scripts/enc_harvest.py`, is the single source of truth for
observed encryption. It reads op25 session logs and writes facts. It never runs
inside the recording path, so it cannot break capture, and it is re-runnable, so
it backfills history.

### The binding rule

op25 log lines carry a timestamp and a receiver index:

```
09/01/26 12:00:43.585551 [10] NAC 0x1bd LDU2: ESS: algid=aa, keyid=22, mi=...
```

For each receiver `[N]` independently:

1. Reconstruct the grant timeline — op25 logs `tg` and `freq` together, and
   `op25_log.py` already parses exactly this.
2. Attribute each ESS / CIPHERTXT observation to the grant active on **that
   receiver** at that timestamp.
3. Match `(tgid, freq, timestamp)` to the `calls` row whose `start` ..
   `ended_at` interval brackets it.

Grant boundaries are the reset, replacing the 12-second window. An observation
matching no call interval is recorded as **unbound and reported** — never
attached to the nearest call. Silent nearest-neighbour attribution is the bug
being fixed; reintroducing it in the harvester would defeat the purpose.

### Two independent evidence sources

Reconciliation uses both, because neither covers the corpus alone:

| evidence | strength | coverage |
|---|---|---|
| ESS ALGID | authoritative for that transmission | 19% of calls |
| intelligible speech in the transcript | proves the audio was not encrypted | every call with speech |

Speech evidence must exclude the known whisper silence artifacts
(`Thank you.`, `Bye.`, `[BLANK_AUDIO]`, `Thanks for watching!`, single words) —
`medium.en` emits these on dead air at 8/599 clips, and counting them as speech
would "prove" an encrypted talkgroup is clear. Require a multi-word,
non-artifact transcript.

### Schema

Three additive columns on `calls`, through the existing `_DERIVED_COLUMNS` /
`_migrate()` mechanism in `sdr_db.py` (guarded `ALTER TABLE ADD COLUMN`; no
`_USER_VERSION` bump, since the FTS layout is unchanged):

| column | values | meaning |
|---|---|---|
| `enc_observed` | `clear` / `encrypted` / `mixed` / NULL | derived from ALGIDs bound to this call |
| `enc_evidence` | `ess` / `speech` / `both` / NULL | which source established it |
| `enc_source` | `harvest` / `live` | provenance; `harvest` is authoritative |

`algid` / `keyid` / `mi` keep their meaning and are overwritten authoritatively
when harvested. `mixed` is a real state — a call carrying both `0x80` and `0xAA`
bursts — that today's single slot silently hides.

**Deliberately not added:** a `call_enc` observations table. Per-call ALGID
counts do not yet earn their storage; the aggregates the report needs read fine
off `enc_observed`. Add it when a question actually requires per-burst detail.

---

## Phases

### Phase 1 — Harvester, schema, tests

`enc_harvest.py [--log PATH ...] [--since TIMESTAMP]` parses logs, binds
observations per the rule above, writes the three columns, and prints a summary
including the unbound count. Read-only with respect to capture. Backfills every
historical call for which a log survives.

Exit criteria: harvester runs over `results/op25_multi.log` and populates
`enc_observed` for the calls it covers; unbound observations are reported, not
silently dropped; tests pass.

### Phase 2 — Reconciliation and the override file

`enc_harvest.py --report` compares per-talkgroup observed behaviour against
RadioReference and proposes a diff, with the evidence and an explicit
minimum-observation gate:

```
TG17166  17-BRPD TLK1  RR=full  speech 21/21, ESS none      -> propose clear
TG17086  17 JAIL SEC1  RR=full  ESS clear 27 / ADP 3        -> propose partial
```

Accepted entries are written by hand into `reference/enc_overrides.json`:

```json
{ "17166": { "enc": "clear", "why": "21/21 intelligible, no ESS", "reviewed": "2026-09-01" } }
```

Three layers, clear ownership: **RadioReference is upstream** (untouched, so
`fetch_lwin_db.py` can re-scrape without clobbering decisions), **overrides are
human-curated**, **observed data is evidence**. `make_whitelist.py:44` resolves
`overrides.get(tg) or ref[tg]`. The `--include-partial` / `--include-encrypted`
semantics are unchanged.

No automatic reclassification. A wrong auto-promotion changes what the receivers
follow, and at 19% ESS coverage small-N decisions are not trustworthy.

### Phase 3 — UI

The recording badge shows the **observed** value when one exists, and the
RadioReference flag marked unverified when it does not, so `full` is never
asserted over audibly clear voice. Tooltip states which: "RadioReference says
full; no ESS observed for this call".

The existing enc *filter* keeps filtering on the talkgroup flag — that is what it
means today, and changing both at once would muddy the vocabulary.

### Phase 4 — Live-path ESS reset

In `op25_log.py`, clear the ESS slot when the receiver's `tg` changes, and stamp
`enc_source='live'`. This stops the common cross-attribution (a previous call's
ESS landing on the next call) without touching anything else in the recorder.
The harvester remains authoritative; the live value is a provisional hint.

### Phase 5 — ADP pair harvest

Point `enc_pair.extract_pairs()` at ordinary session logs, not only
`lwin_enc_capture.log`, and tag emitted pairs with the call and talkgroup they
came from.

**Pairs MUST be grouped by `(algid, keyid)`.** Observed ADP (`0xAA`) key ids:

| keyid | calls |
|---|---|
| `0x22` | 63 |
| `0x8` | 21 |
| `0x2F08` | 5 |
| `0x1` | 4 |
| `0x2EF4` | 1 |

Each KID is a different key; pooling pairs across KIDs into one `adp_brute` run
searches for a key that does not exist. This is a correctness requirement, not an
optimisation.

**KEYID needs the same bit-error scepticism as ALGID.** `rs_errs` is non-zero on
some ESS lines, and `0x2F08` / `0x2EF4` are suspicious next to `0x22` and `0x8` —
a corrupted KID would form a bogus group and quietly split real pairs away from
the run that could use them. Prefer ESS lines with `rs_errs=0` when forming
pairs, require a minimum observation count before treating a KID as real, and
report low-count KIDs rather than grouping on them silently.

---

## Testing

`scripts/tests/test_enc_harvest.py`, in the style of `test_tencodes.py` —
grounded in real log excerpts, pure text in / facts out, no hardware or GPU:

- receiver isolation: an ESS on `[10]` never binds to a call on `[9]`
- grant-boundary reset: an encrypted call's ESS does not reach the next call
- unbound observations are reported, not attached to the nearest call
- `mixed` detection when a call carries both `0x80` and `0xAA`
- speech evidence rejects the whisper artifact set
- bit-error ALGIDs (`0x0E`, `0x45`, `0xA8`, `0xB8`) are discarded, not treated
  as an unknown cipher
- pairs group by `(algid, keyid)` and never merge across KIDs
- a single-observation KID with `rs_errs > 0` is reported, not grouped on

Existing gate must stay green: `eslint`, `nuxt typecheck`, `vitest`,
`python -m unittest discover -s scripts/tests`.

---

## Out of Scope

- **Decryption.** Nothing here decrypts anything. ESS metadata is transmitted in
  the clear and is read as metadata; the ADP work is separate, pre-existing, and
  only fed by this.
- **Rewriting `reference/lwin_talkgroups.json`.** It is an upstream scrape.
- **Automatic whitelist reclassification.** Explicitly rejected above.
- **Raising verbosity on capture paths.** `lwin_listen_multi.sh` already runs
  `-v 10`; the ciphertext is already there. No new log volume is introduced.
- **Changing the enc filter semantics in the UI.**

## Risks

| risk | mitigation |
|---|---|
| Log retention gaps leave calls unharvested | `enc_observed` stays NULL, UI marks unverified; never guessed |
| Speech evidence fooled by whisper hallucination | artifact denylist + multi-word requirement |
| Clock skew between log timestamps and `calls.start` | interval match with a small tolerance; unbound on ambiguity |
| Harvester and live path disagree | `enc_source` records which wrote the value; `harvest` wins |
