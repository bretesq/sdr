# Session log — 2026-08-31

Two threads ran today: a full rewrite of the web console and its data layer, and
an RF investigation into why the RTL-SDRs cannot decode the LWIN control
channel. The software is finished and pushed. The RF thread ends at a specific,
testable hypothesis waiting on a new antenna.

---

## Part 1 — software (done)

### The console

`web/server.py` (Python stdlib, ~14 KB) is gone, replaced by a Nuxt 3 +
PrimeVue 4 application at the repo root. Three panels — Listen & Record,
Recordings, Talkgroups — with the full selection surface the old console had
(`--preset`, `--tg`, `--tag`, `--match`, `--all-areas`, independent
`--include-partial` / `--include-encrypted`), server-side search including
transcripts, virtual scrolling over all rows, sortable columns, and per-call
P25 metadata.

Reachable at `http://0.0.0.0:3000`. Note commands go through
`./node_modules/.bin/` rather than `pnpm run`: pnpm 11.17 aborts every script
run on `ERR_PNPM_IGNORED_BUILDS` for esbuild, which is cosmetic but fatal to
`pnpm run`.

### SQLite

Everything now lives in `sdr.db`: 4,163 talkgroups, 149 sites, 243 categories,
3,275 calls with 3,263 transcripts, 3,765 control-channel grants, and a
sessions table. FTS5 for transcript search. The JSON files under `reference/`
and the `.wav`/`.txt` files remain on disk as sources and artifacts; op25 still
reads its whitelist as a file.

Rebuild at any time:

```bash
python3 scripts/import_to_sqlite.py
python3 scripts/import_grants.py results/lwin_cdr.log
```

### The bug that mattered most

`udp_audio_record.py` wrote `recordings/calls.json` in its `finally` block
containing **only that session's calls** — a truncating write. Every recording
session silently discarded the metadata for every recording before it. A
60-second test took the file from 2,953 entries to 7; the run after that took it
to 1. `stt_watch.py` merged transcripts into the same file and they were
clobbered minutes later, which is why no transcript ever survived there.

Nothing was permanently lost — every field is derivable — and the full history
was reconstructed from filenames, WAV headers and the reference DB. Calls are
now `INSERT`ed as they flush, so a crash loses at most the call in progress.

Verified live on a 75-second session: db calls 3240 → 3247 (grew by exactly the
7 recorded), transcripts 3220 → 3220 (nothing clobbered).

### Security

Four findings from a parallel review, all fixed and verified:

- **CSRF (HIGH).** `application/x-www-form-urlencoded` is CORS-safelisted, so a
  plain HTML form on any page the operator visited could start a recording — and
  by omitting `duration`, an *indefinite* one. `/stop` read no body at all, so a
  bodyless `no-cors` fetch could kill a live session. Now requires a JSON
  content-type and rejects cross-site `Sec-Fetch-Site`.
- **TOCTOU (BLOCKER).** The "already running" check sat before `await readBody`,
  so two simultaneous Starts both spawned. Two op25 instances on one HackRF, and
  the first became permanently unstoppable.
- **ReDoS.** `--match` reaches Python's backtracking `re` with no time limit;
  `((.*)*)*!` ran over 25 s against a real category string.
- **Recycled pid.** A stale pid could make Stop SIGINT then SIGKILL an unrelated
  process *group*. Now paired with `/proc/<pid>/stat` starttime and a `cmdline`
  check.

### Tests

68 total, from 28 at the start of the day and **zero** on the Python side
across 2,177 lines — backwards from the risk, since the Python captures audio
that exists nowhere else.

```bash
./node_modules/.bin/vitest run                  # 28 TypeScript
python3 -m unittest discover -s scripts/tests   # 40 Python
```

`test_static.py::test_no_undefined_names` exists because a `sed` meant to remove
a duplicated `import sys` deleted `import socket`; the file still parsed, the
recorder died with `NameError`, and a 30-second session captured nothing. It is
verified non-vacuous — reintroducing that deletion makes `ast.parse` pass and the
test fail.

---

## Part 2 — RF investigation (blocked on hardware)

### The question

README section 7 concluded a dedicated control-channel receiver "is not possible
here", because both RTL-SDRs measure ~+2.7 dB against the ~15 dB P25 needs. The
prize for solving it is measured in the same section: a control-channel capture
saw **33 talkgroups against the 9** heard while following voice.

### The hardware

PatriotWaves "Skyfall Trunker": two RTL2832U receivers, R820T2/R860 tuners,
28.8 MHz TCXO, one BNC input split internally to both receivers, supplied with
an 8" adjustable duck.

EEPROM reports `Realtek / RTL2838UHIDIR / Generic RTL2832U OEM` — that is an
unflashed USB descriptor and says nothing about the tuner or oscillator. I
initially over-read it as evidence of a generic dongle. Both vendor claims that
can be tested hold up.

### What was ruled out

| candidate | measurement | verdict |
|---|---|---|
| clock error | **−0.3 ppm** both units, against the control channel as reference | TCXO confirmed; not the problem |
| README's `-p 25` | +25 ppm at 773 MHz = ~19 kHz, wider than a 12.5 kHz channel | **actively harmful, now corrected** |
| receiver health | 33.3 dB SNR on 104.1 MHz FM | front end is fine |
| `[R82XX] PLL not locked!` | fires at 104 MHz too, where SNR is 33 dB | cosmetic |
| tuner IF bandwidth | +0.3 dB at 300 kHz | within noise |
| tuner gain | 0.6 dB at gain 0, 4.7 dB at 44.5 | more gain is better; 40 was already right |
| antenna placement | vertical, window-mounted | 4.7 → 5.0 dB, no change |
| real decode attempt | 0 TSBK, wide and narrow tuner, 60 s each | no lock |

### What was found

The antenna is **resonant at 375 MHz, not 773**. This rests on geometry alone.
An earlier band sweep appeared to confirm it and did not — see the spur comb
below; its UHF figures were the dongle's own 460.8 MHz spur. Re-measured in
spur-free windows:

| band | excess over floor |
|---|---|
| FM 88-108 | +18.4 dB |
| UHF 465-473 (clean) | +12.0 dB |
| LWIN 800 voice | +9.0 dB |
| UHF 421-429 (clean) | +8.2 dB |
| LWIN 700 control | +6.0 dB |
| VHF 155-162 | +4.3 dB |

Excess-over-floor cannot measure antenna response across bands anyway — FM
broadcast is 100 kW nearby while LWIN is a distant simulcast, so this mostly
reflects transmitter power and distance. The length is the actual argument:

| | quarter wave |
|---|---|
| supplied 8" duck | 20.0 cm → **375 MHz** |
| LWIN control 773.06 MHz | **9.70 cm = 3.82 in** |
| LWIN voice 852.5 MHz | 8.80 cm = 3.46 in |

At 773 MHz the 8" whip is roughly a **half** wave, and a half-wave whip with no
ground plane presents a very high feedpoint impedance — a poor match, losing
most of the signal before it reaches the receiver.

This reconciles the vendor's claim with the observation: it is a real P25
antenna, sized for the 400 MHz systems many agencies use. LWIN is at 700/800.

### Why the TCXO and R820T2 do not help here

Antenna mismatch is loss **ahead of the first amplifier**, so by Friis it adds
directly to system noise figure:

```
R820T2 + well-matched antenna              3.5 dB
R820T2 + 15 dB mismatch                   18.5 dB
R820T  + 15 dB mismatch                   18.8 dB
```

Upgrading the tuner across a 15 dB mismatch buys 0.3 dB of 18.5. The TCXO buys
frequency *accuracy*, not sensitivity — genuinely valuable for a 12.5 kHz
channel, and it becomes decisive once the antenna is fixed, but it cannot
recover signal that never arrived.

### The 28.8 MHz spur comb

A federal-band survey with the RTL, run while waiting on the antenna, "found"
strong carriers at 417.599 MHz (+20 dB) and 460.801 MHz (+31 dB) — far above
LWIN's +5.8. Both are internal spurs. The dongle's 28.8 MHz crystal produces a
comb at every multiple and half-multiple:

    13.5 x 28.8 = 388.8 MHz   +14.2 dB
    14.0        = 403.2       +23.6
    14.5        = 417.6       +33.0
    15.0        = 432.0       +41.0
    15.5        = 446.4       +41.0
    16.0        = 460.8       +43.4
    off-comb controls 410.0 / 425.0 / 440.0  ->  +11.2 / +1.1 / +10.1

They **pass** README section 8's retune discriminator, holding their absolute
frequency as the LO moves, because they are fixed internal spurs rather than
LO-relative artifacts. That test separates LO images from real signals; it does
not separate internal spurs from real signals. Comb spacing is what does.

Nothing real was found in 406-420 or 450-470 MHz.

This also forced a correction to the antenna section. It had cited "+24.2 dB at
460-470, its best band" as evidence the antenna was fine at UHF — that was the
460.8 spur. Spur-free windows give UHF 465-473 at +12.0 against LWIN 700 at
+6.0: a 6 dB gap, not 18. The 375 MHz resonance conclusion stands, but on
geometry alone; the band sweep was never evidence for it.

### Two of my own findings, retracted

Both tools initially tuned directly to the channel and measured the peak within
±6.25 kHz of DC. The RTL2832U has a large DC-offset spike exactly there, so the
"signal" was the spike.

| claimed | measured properly |
|---|---|
| gain 0 beats gain 40 by **+17 dB** | gain 0 is 0.6 dB, gain 44.5 is 4.7 dB — the opposite |
| narrow tuner worth **+1.6 dB** | +0.3 dB, within noise |

The bogus gain sweep even had a plausible shape: the DC spike is roughly
constant while the noise floor rises with gain, so "SNR" fell monotonically and
looked like textbook front-end overload. Both tools now tune 25 kHz low — which
is exactly why op25's own working config passes `-o 25000`.

---

## Resume here when the antenna arrives

```bash
# 1. Baseline the new antenna
python3 scripts/cc_snr.py -d 1

# 2. If it clears ~15 dB, try a real decode. lwin_cdr.tsv parks on the
#    control channel and never retunes, which is the case that matters.
cd src/op25/op25/gr-op25_repeater/apps
python3 rx.py --args rtl=1 -N LNA:44 -S 1024000 -o 25000 \
  -T ~/rtl/lwin_cdr.tsv -V -v 2
```

Any TSBK above zero means the dedicated control-channel receiver works.

If it does:

- Rewrite README section 7, which currently says it is impossible.
- Run the control-channel receiver alongside the HackRF's voice following, and
  import the grants continuously rather than from occasional captures.
- Note the internal splitter costs 3.5 dB (reactive) or 6 dB (resistive) per
  port — worth measuring, since it applies to both receivers permanently.

If it does not, the remaining lever is a proper external antenna with a ground
plane, or an LNA at the antenna — which these dongles cannot power, having no
bias tee.

### Software still open

- **Grants / CDR view.** 3,765 grants covering 33 talkgroups and 101 radios,
  with no UI. This is the largest unused asset in the database, and it delivers
  most of what the dedicated receiver was for — just sequentially rather than
  simultaneously. Biggest remaining win.
- **Sessions view.** The table exists and is written; there is no history UI.
- **`src_addr` on recorded calls.** Only in control-channel grants, so it needs
  the second receiver.
- **CI.** Nothing runs the 68 tests automatically.
