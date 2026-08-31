# Wideband multi-channel capture on the HackRF Pro — feasibility

Date: 2026-08-31 (measurements added same day). Question: the HackRF Pro can run far more bandwidth than the
2 Msps we use. Can we spend it on **listening to several LWIN calls at once**
instead of one at a time?

**Answer: yes. It is the single largest available gain in capture yield — we are
currently hearing about a quarter of the traffic — and the two technical risks
are both retired by measurement (§7): USB streams clean to 20 Msps, and 8-bit at
12 MHz costs nothing measurable.**

**The one blocker left is an antenna, not bandwidth.** Site 13's active control
channel is 773.05625 MHz and its voice is 78–87 MHz away in the 800 MHz band; no
sample rate spans that. The listed 800 MHz control channels that would have
avoided a second radio are **measured dead** (§5), and the RTL cannot hold
773.05625 with the antenna it has: **+0.6 dB against the ~15 dB needed** (§4).

There is a **zero-hardware Phase 0** that is strictly better than either script
we run today (§8), and a ~$20 antenna unlocks 25% → 73%.

---

## 1. What we do today

`scripts/lwin_listen.sh` runs op25's **`rx.py`**:

```
python3 rx.py --args soapy=0,driver=hackrf -N AMP:0,LNA:40,VGA:44 \
  -S 2000000 -q 0 -o 25000 -T lwin_active.tsv -V -w -u 23456 -n -v 2
```

One device, 2 Msps, **one** demodulator, **one** tuner. On a grant the receiver
leaves the control channel (773.05625), hard-tunes the voice channel, records,
returns. Everything granted while it is away is lost. `OBSERVATIONS.md` §3.3
already states the trade — "a single radio can see every call or hear calls,
not both" — but frames it as a law of the hardware. It is a limitation of
`rx.py`, not of the radio.

## 2. What the traffic actually looks like

Measured from `sdr.db`, `grants` table: 3,765 grant TSBKs over 359 s
(2026-08-30 15:44:45 → 15:50:44). That window has **0 rows in `calls`**, i.e.
it was a CDR-mode run (whitelist `999999`, receiver never left the control
channel) — so the grant census is **complete**, not sampled. Concurrency
figures below are therefore a floor that is also the true value, not an
undercount.

Grants collapsed into calls (same tgid+freq, 3 s gap splits): **111 calls**,
median on-air 6.8 s, mean 9.6 s, p90 22.3 s.

### Concurrency

| simultaneous calls | % of the 359 s |
|---|---|
| 0 | 2.2 |
| 1 | 12.7 |
| 2 | 20.7 |
| 3 | 21.2 |
| 4 | 20.5 |
| 5 | 12.7 |
| 6 | 7.0 |
| 7 | 2.9 |
| 8 | 0.2 |

**Two or more calls are in progress 85% of the time.** Peak 8.

### What a pool of N receivers would have captured

| receivers | calls captured |
|---|---|
| 1 (today) | 28 / 111 = **25%** |
| 2 | 55 / 111 = 50% |
| 3 | 78 / 111 = 70% |
| 4 | 93 / 111 = 84% |
| 6 | 109 / 111 = 98% |
| 8 | 111 / 111 = 100% |

### Where the voice channels are — the hard constraint

15 distinct granted frequencies, in **two bands 90.6 MHz apart**:

| leg | frequencies | span | grants | calls |
|---|---|---|---|---|
| 700 MHz | 769.68125, 769.93125, 770.75625, 772.68125 | **3.00 MHz** | 1,025 (27%) | 28 (25%) |
| 800 MHz | 851.2875 … 860.2375 (11 freqs) | **8.95 MHz** | 2,740 (73%) | 83 (75%) |

The control channel (773.05625) sits in the 700 MHz leg. **No sample rate spans
control + the 800 MHz voice leg** — 78 MHz apart, HackRF tops out at 20 Msps.
That is the real architectural constraint, and it is why the plan needs a second
radio, not more bandwidth.

Each leg *individually* fits a HackRF window:

- 800 leg: needs ±4.475 MHz. At **12 Msps** with `usable_bw_pct 0.85` the limit
  is `(12e6×0.85)/2 − if1/2 = 5.05 MHz`. Fits with 0.58 MHz margin.
- 700 leg: 3.00 MHz — fits in 4 Msps.

## 3. The software supports it (verified by reading the code)

`multi_rx.py` + `tk_p25.py`, boatbod op25, as checked out in `src/`:

- **N channels share one device.** `multi_rx.py:727 find_device` binds a channel
  to a device if `|chan_freq − dev_freq| + 6250 ≤ rate/2`; only a device marked
  `"tunable": true` is refused to a second channel (`:754`).
- **A "retune" inside the window is free.** `channel.set_freq` (`multi_rx.py:449`)
  calls `demod.set_relative_frequency()` first and only hard-tunes if that
  fails. The relative path rewrites bandpass taps out of a cache
  (`p25_demodulator.py:462`) — no hardware tune, no settling time.
- **There is a real voice-receiver pool.** `tk_p25.py:2576 find_talkgroup` skips
  any talkgroup whose `['receiver']` is already claimed, so N receivers on one
  system take N different calls, priority-ordered.
- **A receiver can be pinned to the control channel.** Per-channel whitelist at
  `tk_p25.py:2259`; point it at a non-existent talkgroup and that receiver never
  leaves the CC. Same trick `lwin_cdr_run.sh` already uses.
- CPU is not a concern: the per-channel cost is the decimating bandpass, whose
  work is `ntaps × output_rate`, not `× input_rate`. At 12 Msps that is
  ~800 taps × 96 kHz ≈ 8×10⁷ complex MAC/s per channel on a 16-core 9950X.

### One sharp edge, found in the code

`tk_p25.py:2300 tune_voice` **ignores the return value** of `frequency_set`.
`multi_rx.py change_freq` returns `False` when the requested frequency is
outside the device window — but the receiver has already claimed the talkgroup.
So a receiver parked on the 800 leg that is handed a 700 MHz grant will *claim
the call, record nothing, and stay occupied* until it times out. That is worse
than simply not covering the 700 leg. Consequence: **stage single-band first**
(§4), and gate any cross-band config on `grep -c "Unable to tune"` in the op25
log.

## 4. Measured: the RTL cannot hold the control channel

Run 2026-08-31 on RTL #1 (serial 00000001, the only dongle not claimed by the DVB
driver — see §7.3), stock duck antenna:

```
$ python3 scripts/cc_snr.py -d 1 -i 20
control channel 773.05625 MHz   20s per sweep, gain 40.0
                peak     floor      SNR   verdict
  dongle 1    -17.07    -17.70     +0.6   14 dB short   (ppm +0)
```

**+0.6 dB against the ~15 dB op25 needs. 14 dB short.** This confirms the
expectation and closes README §7's "decisive test" in the negative *for the
current antenna*. The 28 dB gap to the HackRF's +28.3 dB on the same channel is
far larger than the ~5 dB the two front ends differ by, so it remains an antenna
problem, not a receiver problem — but it is a real blocker today.

Consequence: **any architecture that parks an RTL on 773.05625 is dead** until a
second decent UHF antenna exists.

## 5. Measured: the 800 MHz control channels are not usable

RadioReference lists four control channels for RFSS 1 site 13 —
`773.05625  774.54375  851.0375  851.4875` — the last two in the 800 MHz band.
If either were live, one HackRF could hold the control channel *and* every
granted 800 MHz voice channel with no second radio. **Tested 2026-08-31 with the
good antenna. They are not live.**

Method: one 10 s capture at 12 Msps, centre 855.725 MHz, baseband filter 12 MHz,
`-l 40 -g 44 -a 0`. Per-channel SNR measured against a local floor (±250 kHz,
known channels excised) in 1 s slices, plus a **continuity** figure — the
fraction of slices >10 dB above floor. Continuity is the discriminator: a P25
control channel is *continuous*, voice is bursty.
(`scan_p25band.py`'s symbol-clock test applied to every continuous carrier.)

| channel | listed as | peak SNR | continuity | clock | verdict |
|---|---|---|---|---|---|
| 851.0375 | site 13 control | **+0.5 dB** | 0% | — | **nothing transmitting** |
| 851.4875 | site 13 control | **+0.4 dB** | 0% | — | **nothing transmitting** |
| 853.2000 | site 17 control | +17.5 dB | **100%** | **4800 Hz** | live control channel |
| 854.3375 | site 21 control | +18.5 dB | 90% | **4800 Hz** | live, but fading |
| 851.95, 852.6125, 853.7375 | site 17 control | ≤0.5 dB | 0% | — | idle alternates |
| 854.9625, 855.2125 | site 31 control | ≤0.3 dB | 0% | — | idle alternates |
| 859.2375 | site 13 voice | +20.5 dB | 100% | 6000 Hz @ 40.7 dB | **unidentified** — see §5.2 |

Site 13's granted 800 MHz voice channels all showed up as expected — 11 of 11
present, peak SNR 13.5–27.9 dB, continuity 10–70%, textbook bursty voice.

### 5.1 The one live in-window control channel does not help either

853.2 is a genuine LWIN control channel. op25 confirmed it off-air:

```
14:14:59  Reconfiguring NAC from 0x000 to 0x1b0
          NAC 0x1b0  WACN 0xbee00  SYSID 0x1bd   853.200000/808.200000  tsbks 14
          voice freq 769.593750, active tgids [ 6848 ]
```

NAC 0x1b0 is **site 17 Denham Springs** (README §1). But two things kill it:

1. **It grants voice onto 700 MHz** — `voice freq 769.593750`. Site 17 is the
   *mirror image* of site 13: control in the 800 band, voice in the 700 band. A
   receiver parked on the 800 leg gains nothing from holding its control channel.
2. **It will not hold lock here.** `control channel timeout` every ~1.0 s for the
   full 70 s run, cycling through 852.6125 → 853.7375 → 851.95 → 853.2. It
   decodes bursts of 11–26 TSBKs and drops. At 17.7 dB it is right at the edge.

### 5.2 Loose end worth a look

859.2375 is a continuous +20.5 dB carrier with a strong 6000 Hz symbol clock
(40.7 dB above the local median — not the junk reading `OBSERVATIONS.md` §7.1
warns about). It is in site 13's RadioReference frequency list but was **never
granted** during the CDR census.

**6000 symbols/s is exactly the P25 Phase 2 TDMA rate**, and that combination —
in the site's channel list, continuous, absent from a Phase 1 grant census — is
the signature of a Phase 2 channel. Our `grants` table has `opcode` NULL for all
3,765 rows, so this cannot be ruled out from the data we have.

It matters for Phase 1 concretely: a channel configured `demod_type: cqpsk` /
`symbol_rate: 4800` will not decode Phase 2, and a receiver that claims such a
grant records nothing while staying occupied — the same failure mode as the
cross-band trap. **Check before building Phase 1:** with a receiver on
773.05625 at `-v 10`, look for Phase 2 grant opcodes and `tdma_cc`. If present,
those channels need `symbol_rate: 6000` and slot handling (`rx.py -2`, or
per-channel TDMA config in `multi_rx`). Not worth chasing before Phase 0.

**Conclusion: site 13's active control channel is 773.05625, full stop. Nothing
in the 800 MHz band substitutes for it, so the wideband-voice plan does require
a second receiver — and §4 says that means antenna work.**

## 6. Does 40 Msps at 4-bit change anything? No.

**1. Nothing needs more than 9.2 MHz, and 40 MHz still cannot bridge the gap that
actually constrains the design.** 773.05625 → 860.2375 is **87.2 MHz**; the legs
are 90.6 MHz apart end to end. The entire Baton Rouge 800 MHz footprint — 37
channels across four sites — spans 9.200 MHz and fits inside 12 Msps.

**2. It costs ~24 dB of ADC headroom, and we now know headroom is not the
binding constraint anyway.** Quantisation SNR is `6.02N + 1.76` dB across the
Nyquist band; processing gain into a 12.5 kHz channel is `10·log10(fs/12.5 kHz)`:

| mode | quant. SNR | processing gain | in-channel ceiling |
|---|---|---|---|
| 8-bit @ 12 Msps | 49.9 dB | 29.8 dB | **79.7 dB** |
| 4-bit @ 40 Msps | 25.8 dB | 35.1 dB | **60.9 dB** |

(The rows differ in two variables. Isolating bit depth, the loss is the full
24.1 dB; the 5.3 dB the wider window recovers is a bandwidth effect, not a
benefit of 4-bit.) Measured at 12 Msps: **clip = 0.0000%, RMS −16.1 dBFS** — some
16 dB of headroom unused. Giving 24 dB of that away to buy bandwidth nothing
needs is a straight loss.

**3. A 40 MHz window admits the worst possible neighbours.** Centred at 855.725
it spans **835.7–875.7 MHz**, swallowing the 869–894 cellular/SMR *downlink* —
local base stations, typically the strongest signals present. At 12 Msps the
window is 849.7–861.7 and stays clear of 869+.

**4. It is not reachable from the stack on this box.** `hackrf_transfer` reports
`-s sample_rate_hz # (2-20MHz supported)`; `libhackrf.so.0.9.2` (fw 2026.01.3,
API 1.10, Board ID 5 "HackRF Pro") exports no bit-depth setter; SoapyHackRF
advertises `CS8/CS16/CF32` only. The Pro's hardware may well support it — it is
just not plumbed through the only path op25 has to the radio (SoapySDR, README
gotcha #9). Reason 1 holds regardless.

**Verdict: stay 8-bit.**

## 7. Measured: the two risks I flagged are both retired

### 7.1 USB is not a constraint — even at 20 Msps

`hackrf_transfer -B`, HackRF on bus 001 (480 Mbps, shared with a `cdc_ether`
adapter):

| rate | throughput | per-second overruns | total |
|---|---|---|---|
| 12 Msps | 24.0 MB/s | **0** in every 1 s window | 1 (at teardown), 240 MB moved |
| 16 Msps | 32.0 MB/s | **0** | **0** |
| 20 Msps | 39.9 MB/s | 1 (cumulative, at startup) | 1 |

No need to replug into bus 004/006. 12 Msps has ample margin; 16 is clean too.

### 7.2 8-bit at 12 MHz costs nothing measurable

The two live control channels give a *continuous* reference, so the A/B is not
confounded by voice burstiness. Same antenna, same `-l 40 -g 44 -a 0`, captures
minutes apart:

| reference | 2 Msps (IF 1.75 MHz) | 12 Msps (IF 12 MHz) | delta |
|---|---|---|---|
| 853.2 — site 17 CC, strong, 100% continuous | 16.9 dB | **17.7 dB** | **+0.9 dB** |
| 854.3375 — site 21 CC, fading | 11.7 dB | 8.4 dB | −3.4 dB |

853.2 is the trustworthy row: measured 15.9, 17.5 and 17.7 dB across three
captures, so the narrow-IF 16.9 dB sits *inside* its own spread. Wideband is at
worst neutral. The 854.3375 delta is smaller than that channel's own fade range
(8.4–18.5 dB observed).

**Caveat: this characterises the strong-signal case, and Phase 1's margin lives
in the weak one.** The weakest granted 800-leg channel measured 13.5 dB — much
closer to 854.3375's range than to 853.2's — and 854.3375 is precisely the row
too noisy to draw a conclusion from. A longer paired capture on a weak
continuous carrier would tighten this. It does not change the recommendation
(there is 16 dB of unused headroom, and the physics favours wideband), but the
weak-signal penalty is bounded by measurement at "somewhere between −3 and
+1 dB", not established at zero.

Why this comes out better than feared: channel SNR against **thermal** noise does
not depend on sample rate — the same 12.5 kHz is filtered out either way — while
processing gain against **quantisation** noise improves from 13.2 dB to 21.0 dB.
The cost is only headroom, and at −16.1 dBFS with 0% clipping there is headroom
to spare. The 1–2 dB `tuner_bw_test.py` found from narrowing the tuner IF is
apparently offset by the extra decimation gain.

## 8. Recommended build order

The technical risks are gone. What remains is one piece of cheap hardware.

### Phase 0 — the 700 MHz leg, zero hardware, do this first

One HackRF, non-tunable, covering site 13's **entire 700 MHz leg including its
live control channel**:

| | |
|---|---|
| covers | 769.68125 … 774.54375 (4 granted voice + both 700 MHz control) |
| rate / centre | **8 Msps @ 771.4185 MHz** |
| `if_rate` | **25000** (`get_decim`: 8 Msps → 25000, decim 80/4, if1 100 kHz) |
| DC clearance | 662 kHz to the nearest audible carrier |
| channels | 1 pinned control + 3 voice |

Yield: **100% of 700-leg calls (28/28 with 3 voice receivers) *and* a 100% grant
census, in one run** — which `OBSERVATIONS.md` §3.3 records as mutually
exclusive.

**Read the audio number carefully: it is a comparable volume of calls, not the
same calls.** Today's single receiver captures 28 of 111 calls opportunistically
across *both* legs; Phase 0 captures 28 of 28 on the 700 leg and **zero on the
800 leg**. The counts coinciding at 25% is arithmetic coincidence, not
equivalence. Anyone diffing recordings before and after will notice that
800-leg traffic — BRPD Dispatch 1 (TG 17165) among it — has disappeared. Phase 0
is still the right first step, for two reasons that have nothing to do with
audio volume: the census becomes complete, and it builds and validates the whole
`multi_rx` path (config, per-channel UDP recording, the `--rx-id` change) on the
band that already decodes reliably, before any money is spent. If continuous
800-leg coverage matters more than that, skip to Phase 1 and buy the antenna.

### Phase 1 — the 800 MHz leg. Needs one antenna.

Site 13's control channel is 773.05625 and the RTL reads +0.6 dB on it (§4). Two
ways out:

- **A second UHF antenna (~$20), good antenna to the Skyfall.** A quarter wave at
  773 MHz is 3.8". No loss on the control-channel path. Recommended; README §7
  and commit `ac80f4a` already point here.
- **A 2-way splitter (~$10) off the good antenna.** Costs ~3.5 dB on both paths.
  The control channel can afford it (+28.3 → ~+24.8 dB). The 800 leg cannot
  comfortably: its weakest granted channel measured 13.5 dB, and −3.5 dB puts it
  at ~10 dB, below op25's threshold.

Then: RTL pinned to 773.05625; HackRF non-tunable **12 Msps @ 855.725 MHz**,
`if_rate 24000`, `usable_bw_pct 0.85`, 5 voice channels.

### Phase 2 — both legs

Add a third receiver for the 700 MHz voice sub-window, or a second wideband radio.

### Config details that are easy to get wrong

- **`if_rate` must match `get_decim`'s second stage** or every channel pays an
  `arb_resampler`: 8 Msps → 25000; 12 Msps → 24000; 16 Msps → 25000.
  **10 Msps cannot cover the 800 leg** (limit 4.20 MHz < 4.688 needed).
- **Centres are chosen off-channel** (771.4185 / 855.725). The DC spike is the
  trap of commit `cf019d4`, and a device-level `offset` cannot protect
  off-centre channels.
- **`"crypt_behavior": 1`**, not the `2` in op25's shipped examples — `2` makes
  `find_talkgroup` skip encrypted talkgroups outright; `1` is today's
  silence-but-record `-n`, which `--include-partial` needs.
- **Pin the control-channel receiver** with a per-channel whitelist
  (`tk_p25.py:2259`) holding only a non-existent talkgroup — the `999999` trick
  from `lwin_cdr_run.sh`.
- **Set the baseband filter explicitly.** `hackrf_transfer` defaults to
  ≤0.75×fs, which at 12 Msps picks 8 MHz and clips the window edges; the
  captures here used `-b 12000000`. SoapyHackRF sets bandwidth itself
  (`activateStream - Set RX bandwidth`), so verify what it chose in op25's log.
- **One `"destination": "udp://0.0.0.0:<port>"` per channel**, so
  `udp_audio_record.py` needs one instance each. Its regexes (`TGPAT`,
  `FREQPAT`, `ESSPAT`) do not anchor on op25's `[msgq_id]` prefix and would
  cross-attribute metadata between channels — add an `--rx-id N` filter. This is
  the main integration work.
- **Never mix legs on one device.** `tk_p25.py:2300 tune_voice` ignores the
  return value of `frequency_set`, and `change_freq` returns `False` for an
  out-of-window frequency *after* the talkgroup is claimed — the receiver records
  nothing and stays occupied. Make
  `grep -c "Unable to tune" results/op25_record.log` an acceptance gate.
- **Free RTL #0 from the DVB driver** before Phase 1. `/sys/bus/usb/devices/3-2.4.3`
  (serial 00000202) has `dvb_usb_rtl28xxu` bound despite the blacklist file;
  the modules are live in the running kernel.
  `sudo modprobe -r dvb_usb_rtl28xxu rtl2832_sdr rtl2832 dvb_usb_v2`, then replug.
  RTL #1 (3-2.4.2, serial 00000001) is clean.

### Expected yield

| configuration | audio | grant census | hardware |
|---|---|---|---|
| today, `lwin_listen.sh` | 25% | partial | — |
| today, `lwin_cdr_run.sh` | 0% | 100% | — |
| **Phase 0** — 8 Msps, 700 leg | 25% | **100%** | **none** |
| **Phase 1** — 12 Msps, 800 leg + RTL CC | **~73%** | 100% | one antenna |
| Phase 2 — both legs | ~92% | 100% | + a third receiver |

## 9. Caveat on the traffic numbers

All statistics come from **one 359 s window** on 2026-08-30, 111 calls, preset
`all` for the Baton Rouge area. The census within that window is complete (§2),
but one six-minute sample at one time of day is thin for headline percentages.
Re-run `lwin_cdr_run.sh` for an hour across a busy period before treating
"98% at 6 receivers" as a design target rather than an order-of-magnitude
estimate. The 25% figure for today's single receiver is the robust one, and it is
the one that motivates the work.
