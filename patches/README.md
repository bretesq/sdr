# Local patches to vendored op25

`.gitignore:61` ignores `src/`, so the op25 checkout under `src/op25/` is **not
tracked by this repository**. Any local change to it is invisible to our git and
is lost the moment op25 is re-cloned or reset. These patch files are the only
record.

op25 *is* its own git checkout, so each patch here is generated with
`git -C src/op25 diff` against a named upstream commit rather than hand-rolled.
That makes it authoritative: it applies to pristine upstream and reproduces our
file byte-for-byte, which is verified below.

**Re-apply after any op25 re-clone, reset or rebuild:**

```bash
cd /home/besquivel/rtl/src/op25
for p in /home/besquivel/rtl/patches/op25-*.patch; do
  if patch -p1 --forward --dry-run -i "$p" >/dev/null 2>&1; then
    patch -p1 --forward -i "$p" && echo "applied: $(basename "$p")"
  else
    echo "already applied, or does not apply: $(basename "$p")"
  fi
done
```

The dry run first is deliberate: `patch --forward` on an already-applied patch
prints `Reversed (or previously applied) patch detected! Skipping patch.` and
**exits non-zero**, so a bare `|| echo FAILED` would cry wolf on every re-run.
Note `-p1` from `src/op25`, not from `apps/` — these are `git diff` paths.

Verify afterwards:

```bash
cd /home/besquivel/rtl/src/op25/op25/gr-op25_repeater/apps
grep -c "leaving it unclaimed" tk_p25.py   # expect 1
grep -c "def can_reach" tk_p25.py          # expect 1
```

`scripts/lwin_listen_multi.sh` checks both markers at startup and refuses to run
without them, because every failure mode below is **silent**.

`op25-tk_p25-*.patch` are runtime Python changes under `apps/`, so they need
**no cmake rebuild** — `multi_rx.py` does `importlib.import_module('tk_p25')`
with the apps directory on `sys.path`, and there is no other `tk_p25.py` on this
system that could shadow it.

`op25-p25p1-*.patch` is **C++ under `lib/`, and does need a rebuild**:

```bash
cd /home/besquivel/rtl/src/op25/build && make -j8 && sudo make install
```

Re-applying that patch without rebuilding changes nothing at all: the running
decoder is `/usr/local/lib/x86_64-linux-gnu/libgnuradio-op25_repeater.so`, and
until `make install` replaces it the source edit is inert. That failure is
silent in the direction that matters — packet data simply stays invisible, which
is indistinguishable from a system that carries none.

Not recorded here: `README.md` gotcha #4 (op25's `cmake_policy(SET CMP0026 OLD)`
vs CMake 4.2) is a build-system change under `src/op25/CMakeLists.txt`, and
`src/op25` carries several other local modifications visible via
`git -C src/op25 status` that predate this work.

---

## `op25-tk_p25-multiband-receiver-pool.patch`

Generated against upstream **`71abcd0`** ("Additional -v11 debug of terminal
commands"). Verified: applies cleanly to `71abcd0:…/tk_p25.py` and reproduces
our working file byte-for-byte.

**Why any of this is needed.** LWIN site 13 splits voice across 769–772 and
851–860 MHz, 87 MHz apart, which no HackRF sample rate spans. Voice receivers
therefore sit on two non-tunable devices covering different bands, and about
three quarters of grants land on the 800 MHz leg. `tk_p25.py`'s receiver pool
was written for receivers that can all reach everything.

It makes three related changes to `p25_receiver`:

**1. `find_talkgroup` becomes window-aware (`can_reach`).** The pool picked by
priority and claim status alone, with no notion that a non-tunable device only
covers `centre ± ((rate × usable_bw)/2 − if_rate/2)`. Receivers on one band kept
claiming grants on the other, failing to tune, releasing, and being unavailable
when a reachable grant arrived — measured 1,300 tune attempts producing 6 calls
on the 700 leg, against 60 attempts producing 10 after the fix, and
`Unable to tune` went from 1,243 to 0.

Driven by `freq_min`/`freq_max` in the channel config, which
`scripts/make_multirx_cfg.py` emits from the demodulator's own bound. Both
default to 0, which disables the check and restores upstream behaviour, so the
change is inert for any config that omits them.

**2. `tune_voice` returns a status.** It ignored the return of `frequency_set`
(`multi_rx.change_freq`), which is `False` when the frequency lies outside the
device window, and claimed the talkgroup anyway
(`talkgroups[tgid]['receiver'] = self`). The receiver then recorded silence for a
whole call *and* blocked any other receiver from taking it — losing the call more
thoroughly than not covering the band at all. It now returns before the claim.

`is False` rather than a truth test: only `multi_rx.change_freq` returns a bool,
and a `frequency_set` returning `None` must keep upstream behaviour. `tk_p25.py`
is used only by `multi_rx.py` (`rx.py` uses `trunking.py`), so `rx.py` is
unaffected.

**3. `scan_for_talkgroups` honours that status.** It logged a `voice update`
line, called `log_call()` and called `meta_update()` unconditionally around
`tune_voice`, so an unreachable grant still produced a logged call the receiver
never took — and `scripts/op25_log.py` latched that tgid/freq against the
receiver id for `TG_TTL`, so any audio still arriving on that channel's UDP port
from its *previous* frequency was written out under the wrong talkgroup. All
three side effects now happen only after a successful tune.

Change 3 matters most when `freq_min`/`freq_max` are **absent**: change 1 is then
disabled, so the unreachable path fires on most cross-band grants and change 2
alone leaves the mislabelling in place. With our own config the path is not
exercised at all (measured: 0 occurrences).

**Upstream:** not submitted. Worth reporting to boatbod/op25 — the bug is generic
to any multi-band, multi-device trunking config, not specific to LWIN.

---

## `op25-p25p1-read-sndcp-packet-data.patch`

Generated against upstream **`71abcd0`** ("Additional -v11 debug of terminal
commands"). Verified: applies cleanly to pristine `71abcd0` and reproduces our
working files byte-for-byte.

**Why this is needed.** LWIN site 13 runs integrated voice and data. Measured
over 35 minutes on 2026-09-03 from `results/op25_multi.log`:

| TSBK | meaning | count |
|---|---|---|
| `0x16` | SNDCP data channel announcement | 5,705 |
| `0x14` | SNDCP data channel **grant** to a radio | 362 |
| `0x15` | SNDCP data page request | 140 |

All 362 grants named the same channel (`0x14cc` → 769.68125 MHz) across 112
distinct radios. None of that payload was readable, and **op25 dropped it twice
over, silently both times**:

1. `p25_framer.cc` capped DUID `0x0c` at 962 bits — header + 3 data blocks,
   sized for multi-block trunking. A real IPv4+UDP datagram is 28 bytes of
   header before any payload and an LRRP position report runs to ~58, needing
   5–6 data blocks. Longer frames were truncated, so the header `crc16` at
   `p25p1_fdma.cc:494` failed and `process_PDU` returned **before any logging**.
2. Anything that survived would have hit `p25p1_fdma.cc:502`, which requires
   `sap == 61` (trunking). SNDCP user data is not SAP 61.

So the log showed zero `PDU:` lines and zero `non-MBT message ignored` lines
even while a receiver sat on the data channel for hours. Absence of evidence
looked exactly like evidence of absence.

**What it changes.**

* `max_frame_lengths[0x0c]`: `962` → `1728`, yielding header + 7 data blocks.
  1728 is the hard ceiling, not a round number: `frame_body` is a
  `std::vector<bool>` sized `P25_VOICE_FRAME_SIZE`, and the three
  `frame_body[next_bit++]` writes are **unchecked**, so anything larger is a
  silent overflow. Going beyond 7 blocks means growing that buffer and
  bounds-checking those writes — a separate change, and one that wants the
  `blks` field of real headers as evidence that it is needed.
* `process_PDU`'s non-MBT branch now logs the raw 12-byte header block and the
  raw data blocks at `-v 10`, instead of discarding them.

**What it deliberately does not do.** It interprets nothing. Block format
(confirmed carries 2 octets of DBSN/CRC9 before its 10 of user data;
unconfirmed uses all 12) and header field offsets are decided in
`scripts/p25_packet.py`. Field offsets are the part of this work least supported
by evidence — op25 itself reads `blks` from octet 6, which does not match the
layout the standard is usually quoted as having — and a wrong guess in C++ costs
a rebuild and a capture outage, while a wrong guess in Python costs an edit.

**The log format is a contract.** `scripts/p25_packet.py`'s `PDU_LINE` regex
must match what this patch prints, and `scripts/tests/test_p25_packet.py` pins
it. If they drift, the regex matches nothing and the result reads as "this
system carries no data" — the same false negative described above. Change both
together.

Verify after applying and rebuilding:

```bash
grep -c "1728,	                // c - pdu" src/op25/op25/gr-op25_repeater/lib/p25_framer.cc   # expect 1
grep -c "NAC 0x%03x PDU: fmt=" src/op25/op25/gr-op25_repeater/lib/p25p1_fdma.cc              # expect 1
```

### Addendum: the three PDU outcomes (2026-09-04)

The first version of this patch logged only SUCCESS. That was not enough to
diagnose anything: an 11-hour capture with a receiver pinned to a real data
channel, and ~29 data grants/hour naming that exact frequency, produced **zero**
PDU lines — and zero was consistent with four different explanations at once
(frames never arrived / arrived and failed trellis decode / arrived and failed
the header CRC / arrived fine but the receiver was elsewhere).

`d_stat_pdu_attempted` and `d_stat_pdu_passed` already count two of those, but
they are readable only via a `"fec_stats"` command that nothing in op25 polls,
and `scripts/make_multirx_cfg.py` deliberately emits **no terminal section**, so
there is no channel to send that command down. The counters were unreachable by
construction.

So the patch now names each outcome separately:

| Log line | Means |
|---|---|
| `p25_framer::load_nid() PDU nid seen, ... need N more symbols` | a DUID `0x0c` NID was received; the framer is now waiting for N more symbols |
| `PDU: process_PDU entered, fr_len=N` | the frame COMPLETED and reached the decoder |
| `PDU: block deinterleave/trellis FAILED` | reached us, but a block would not decode — signal quality |
| `PDU: header crc16 FAILED (N blocks)` | blocks decoded, header CRC did not — truncated reassembly or bit errors |
| `PDU: fmt=.. sap=.. blks=.. hdr=.. : ..` | success, payload follows |

The first two together are the load-bearing pair. **NID logged with no
`process_PDU entered` means the body never completed** — a sync/length problem,
not a FEC one — and that is invisible without this line, because `rx_sync.cc`'s
sync-expiry path calls `sync_reset()` and drops the pending frame silently.

Note `rx_sync.cc:533-536` already truncates `d_fragment_len` to what actually
arrived **when a new frame sync is detected mid-frame**, so a PDU followed by
another P25 frame completes at its true length regardless of
`max_frame_lengths`. The gap is the LAST frame of a burst: if the carrier drops
before `d_fragment_len` symbols arrive, nothing flushes it. Raising the limit to
1728 made that window wider (88 ms → 168 ms of required carrier), so if the
instrumentation shows NIDs without completions, the fix is a flush-on-expiry in
`rx_sync`, not a further change to the constant.

### Gotcha: `p25p1_fdma.cc` is CRLF, `p25_framer.cc` is CRLF too

Both files are CRLF in upstream `71abcd0`. Editing them with anything that
normalises line endings (Python's `read_text()`/`write_text()`, which applies
universal newlines on read and writes `\n`) rewrites **every line**, turning a
20-line patch into a 527-line one that is useless as a record. This happened
once while writing this patch and was caught only by `git diff --stat` looking
absurd for a two-line change.

Use byte-level I/O (`read_bytes`/`write_bytes`) on these two files, and sanity
check with:

```bash
git -C src/op25 diff --stat -- op25/gr-op25_repeater/lib/   # expect tens of lines, not hundreds
```

---

## `op25-tk_p25-follow-sndcp-data-grants.patch`

Generated as a diff on top of `op25-tk_p25-multiband-receiver-pool.patch`, not
against pristine — both touch `tk_p25.py`, so they must be applied **in that
order**. Verified: pristine `71abcd0` + multiband + this one reproduces our
working `tk_p25.py` byte-for-byte.

**Why this is needed.** LWIN has no fixed data channel. An early 35-minute
sample saw all 362 SNDCP grants (TSBK `0x14`) name `0x14cc` = 769.68125, which
looked like a permanent assignment and got a receiver pinned there. Over 11
hours that reading collapsed:

| measure | value |
|---|---|
| SNDCP data grants | 8,084 |
| distinct radios | 984 |
| **distinct channels granted** | **19** |
| share on the 800 MHz leg | 78% |
| share on 769.68125 (the pinned frequency) | **4.0%** |

Data channels are allocated out of the ordinary traffic-channel pool exactly as
voice is. A pinned receiver sees one frequency in nineteen.

**What it changes.** op25 logged `0x14` as `unhandled` and did nothing with it.
Now `p25_system.decode_tsbk` decodes it and calls a new
`rx_ctl.tune_data_receivers(freq, llid)`, which retunes every channel marked
`data_only` in the config (emitted by `scripts/make_multirx_cfg.py`).

**What it deliberately does not do.** It does not route data grants through the
voice pool. `tune_voice` is talkgroup-centric — it reads
`self.talkgroups[tgid]['tag']`, and a data grant has no talkgroup — but the real
reason is capacity: `find_talkgroup` would let data grants compete with voice
for receivers, and the 800 leg carries 84% of voice traffic. This path only ever
touches `data_only` channels, so **voice coverage cannot regress**. Two tests in
`scripts/tests/test_multirx_cfg.py` hold both halves of that: the data channel
is marked, and nothing else is.

Two behaviours worth knowing:

* **Dwell.** `DATA_DWELL_SEC = 3.0`. LWIN issues ~21 grants/minute across 15+
  channels, so an unconstrained retune would hop away mid-burst and decode
  nothing. The receiver also needs ~0.17 s to acquire sync on this system.
* **Device window.** Grants outside a receiver's `freq_min`/`freq_max` are
  skipped rather than attempted, for the same reason `can_reach` exists in the
  multiband patch: the two HackRFs are non-tunable and cover different bands, so
  78% of grants are physically unreachable by the 700-leg receiver.

**The hard limit this does NOT fix.** `iden_up` id 1 carries `toff +30` MHz, so
a grant naming 769.68125 assigns the pair **769.68125 down / 799.68125 up**.
Our receivers are on the downlink, so they hear **outbound data only** (system →
radio). Inbound traffic — where LRRP position reports and ARS registrations
travel — is at 799–805 MHz (700 leg) and 806–824 MHz (800 leg), outside every
window this config can reach. Reading those needs another receiver and antenna;
the RTL-SDRs measured +0.4 dB on this band and will not do it.

### Addendum: the raw bit dump, and what it proved (2026-09-04)

`process_blocks` now also emits the whole post-status bit vector for DUID
`0x0c` frames at `-v 10`:

```
NAC 0x1bd PDU raw: bits=700 blocks=3 : 5575f5ff77ff1bdc...
```

Bits packed MSB-first; blocks start at bit 112 (48 frame sync + 64 NID) every
196 bits. **Gated on `duid == 0x0c`** because `process_blocks` also serves
`process_TSBK`, which runs thousands of times a minute on the control channel.

**Why, and what it settled.** Every data block in every PDU failed
`block_deinterleave` while headers decoded perfectly — 0 header CRC failures in
29 PDUs. That is systematic, not signal quality, and the suspected cause was
that data blocks are rate-**3/4** trellis at 18 octets while `process_blocks`
implements rate-1/2 at 12 (the path built for TSBKs and PDU headers). Both
occupy 196 bits on air, so framing looks right and only the decode is wrong.

Rather than write a second trellis decoder in C++ on that hypothesis, the dump
let one be prototyped in Python against real bits. It was right, and the
payload is **not encrypted**:

```
10.51.1.10:49516 -> 172.16.94.223:4005  CHECKSUM VALID  ARS (registration)
10.51.1.10:4001  -> 172.16.93.225:4001  CHECKSUM VALID  LRRP (location)
```

The decoder lives in `scripts/p25_packet.py`, not here. **No rate-3/4
implementation is needed in op25 at all** — the raw dump plus Python is a
complete solution, and it keeps the "C++ interprets nothing" split intact.

Two things the real bits taught that the hypothesis did not:

* Data blocks carry 2 octets of DBSN/CRC9 before 16 of user data, and the
  reassembled payload then opens with a **2-octet SNDCP prefix** before the IP
  header. `parse_ipv4` finds nothing at offset 0 and validates at offset 2.
* The rate depends on the packet FORMAT. A response PDU (`fmt 0x03`) carries
  rate-**1/2** blocks of 12 octets — op25 decoded one cleanly to
  `fc ff ff ff ff ff ff ff bd 1d fc 83` while failing every `fmt 0x16` block.
  Applying 3/4 to those would produce garbage and report success.

The rate-3/4 constellation table was **recovered from SDRTrunk's
`P25_3_4_Node` bytecode**, not written from memory. The same parser was pointed
at `P25_1_2_Node` first and returned a table byte-for-byte identical to op25's
independent copy — getting a known-correct answer out is what licenses trusting
the unknown one. `scripts/tests/test_p25_packet.py` pins both tables and three
real frames.

### Addendum: one receiver per grant, and a pool (2026-09-04)

Two bugs and a capacity finding, all from measuring what the single data
receiver was actually doing over 893 reachable grants:

```
grant disposition: tuned 234   dwell-blocked 659   outside-window 1018
  -> 74% of REACHABLE grants refused because the receiver was already busy

rx 4  (700 leg): 51 tunes, 80% yielded no PDU at all
rx 12 (800 leg): 183 tunes, 19% yielded nothing
     first PDU after a retune: median 0.50 s
     last  PDU after a retune: median 3.24 s, max 35.4 s
```

**The dwell length was not the problem.** Median time-to-last-PDU (3.24 s)
already exceeds `DATA_DWELL_SEC` (3.0 s), so shortening the dwell would truncate
live sessions while lengthening it would block more grants. 74% blocking on the
leg carrying 78% of traffic is a concurrency shortage.

So `n_data` is now a per-leg count: 1 on the 700 leg (which earns no more —
80% of its retunes yielded nothing) and **2** on the 800 leg. The port-budget
formula follows `n_data` rather than assuming one receiver per leg, and
`test_capture_control` derives the count from the leg definitions so raising it
fails loudly instead of silently overflowing the block.

**Bug 1: every eligible receiver was tuned to the same frequency.**
`tune_data_receivers` had no `return` after a successful tune, so a pool of N
receivers covered one channel N times instead of N channels once — defeating
the entire point of a pool. One receiver per grant now.

**Bug 2: repeated grants consumed extra receivers.** Grants arrive in
triplicate on this system. Without suppression, three receivers would be spent
on one channel. A receiver still inside its dwell on that frequency is already
working on it, so the grant is now ignored.

Measured after both fixes plus the pool:

```
dwell-blocked share of reachable grants:  74%  ->  4%
data blocks recovered (of those claimed): 91%  -> 100%
```

`DATA_DWELL_SEC` was deliberately left at 3.0. Moving receivers and dwell in
the same change would have made neither effect measurable; with blocking down
to 4% the dwell is no longer the binding constraint anyway.
