# Baton Rouge, LA — SDR Spectrum Survey & Decode Lab

Working SDR setup and decoded results for Baton Rouge, LA 70809.
Date: 2026-08-30.

## Headline results

1. **LWIN P25 trunking — fully decoded.** Louisiana's statewide P25 system, Baton Rouge
   Simulcast site. System identity, site topology, and live talkgroup traffic recovered
   off the air and cross-verified against RadioReference.
2. **FM RDS — decoded**, including live now-playing song metadata.
3. **Complete LWIN reference database** pulled locally (`reference/`).

---

## 1. LWIN P25 Trunking (primary result)

### Decoded off-air
```
NAC   0x1BD
WACN  0xBEE00      <- Louisiana statewide
SYSID 0x1BD
RFSS  1   Site 13  -> "Baton Rouge Simulcast" (East Baton Rouge Parish)
Control channel: 773.05625 MHz   (op25 self-reported ch1: 16e8 -> 773.056250)
```
From a 40 s capture: **1599 TSBK messages, 0 discarded frames.**

### Adjacent sites decoded (all RFSS 1) — a geographic ring around Baton Rouge
| Site | Hex | NAC | Name | Parish |
|---|---|---|---|---|
| 10 | 0x0A | — | Geismar | Ascension |
| **13** | **0x0D** | **1BD** | **Baton Rouge Simulcast** | **East Baton Rouge (receiving)** |
| 17 | 0x11 | 1B0 | Denham Springs | Livingston |
| 21 | 0x15 | 1B8 | Saint George | East Baton Rouge |
| 28 | 0x1C | — | Baywood | East Baton Rouge |
| 30 | 0x1E | 1B4 | Ramah | Iberville |
| 31 | 0x1F | 1BF | Zachary | East Baton Rouge |
| 32 | 0x20 | — | Sage Hill | West Feliciana |
| 37 | 0x25 | 1B1 | Livingston | Livingston |
| 44 | 0x2C | — | Clinton | East Feliciana |
| 50 | 0x32 | 1BD | South Baton Rouge | East Baton Rouge |
| 72 | 0x48 | — | Livonia | Pointe Coupee |

### Site 13 frequencies (RadioReference, verified against decode)
`24 FREQS · 4 CONTROL` — control: **773.05625**, 774.54375, 851.0375, 851.4875

**Notable — site 13 is split across two bands.** The control channel is at 700 MHz
(773.05625, +28 dB here) but **voice traffic is granted onto 800 MHz** (851-853 MHz):
op25 logged `new freq=852.150000`, `851.837500`, `852.987500`, `852.750000`, `852.562500`.

An early 850-870 MHz sweep with the *old* antenna found nothing, which was misleading on
two counts: the antenna has since been swapped, and voice channels are **bursty** — a
time-averaged sweep buries them. Re-sweeping 850-856 with the current antenna shows real
activity at 852.9 / 854.4 / 855.0 / 858.2 MHz (+8 to +11 dB, high variance = voice).
The 800 MHz leg is weaker than the 700 MHz control channel but usable.

### Live talkgroup traffic observed (40 s capture, resolved against local DB)
| TG | Grants | Alpha | Description | Enc | Category |
|---|---|---|---|---|---|
| 17345 | 220 | 17-SGFD Ops | St. George Fire OPS | clear | EBR (17) Fire/EMS |
| 17344 | 110 | 17-SGFD Prevent | St. George Fire Prevention | clear | EBR (17) Fire/EMS |
| 17086 | 57 | 17 JAIL SEC1 | Prison Security | **full** | EBR (17) Sheriff |
| 17165 | 51 | 17-BRPD DSP1 | Baton Rouge Police Dispatch 1 | **partial** | EBR (17) BR Police |
| 6039 | 38 | LDWF R4-DISP | Wildlife & Fisheries Region 4 Dispatch | clear | LDWF |
| 17139 | 28 | 17-BRFD DSP1 | Baton Rouge Fire Dispatch 1 | clear | EBR (17) BR Fire |
| 17338 | 22 | — | not in reference DB | ? | — |
| 6848 | 14 | AASI - ZONE 4 | Acadian EMS — EBR/WBR/Iberville/Livingston | clear | EMS Agencies |
| 6846 | 6 | AASI - ZONE 2 | Acadian EMS — Vermillion/Lafayette/St. | clear | EMS Agencies |

Two observed talkgroups (17086 Prison Security, 17165 BRPD Dispatch 1) are flagged
encrypted in the database — **control metadata only was captured; no audio was decoded
and no decryption was attempted.**

Local reference DB: **4163 LWIN talkgroups** (856 fully encrypted, 114 partial, 3193 clear)
across 243 categories, in `reference/lwin_talkgroups.json`.

### How to reproduce
```bash
./scripts/lwin_decode.sh 40          # capture 40 s + decode
python3 scripts/annotate_lwin.py     # resolve talkgroup IDs to names
```

### The channel-hunting method (works with no prior frequency list)
`scripts/scan_p25band.py` captures the whole 768–776 MHz band at 8 Msps and tests
**every** narrowband carrier for its symbol rate. This distinguishes:
- **4800 baud** -> P25 Phase 1 C4FM (control channel = strongest, most continuous clock)
- **6000 baud** -> P25 Phase 2 TDMA (voice)

Measured here:
| Freq | SNR | Clock | Meaning |
|---|---|---|---|
| 773.05688 | 28.3 dB | 4800 @ 41.4 dB | **control channel** |
| 769.19434 | 35.7 dB | 4800 @ 19.3 dB | Phase 1 |
| 772.08081 | 23.9 dB | 6000 | Phase 2 TDMA voice |
| 774.14282 | 30.0 dB | ~6200 (unreliable) | not on Site 13's list |

This is what finally located the control channel. Power-and-variance analysis alone was
misleading: 772.08 is a strong *continuous* carrier but is a Phase 2 voice channel, not
the control channel — which is why every early op25 attempt failed.

---

## 2. FM RDS decodes
| Freq | PI | Station | RadioText |
|---|---|---|---|
| 88.5 | 0x6CFA | WJFM / SonLife Radio | `SonLife Radio Network www.jsm.org` |
| 104.1 | 0x47D6 | "The Vibe" | `104.1 The Vibe - Wanna Be Startin' Somethin' - Michael Jackson` |

547 and 559 RDS groups in ~50 s each. Decoded on **RTL-SDR #1** (dongle 0 got zero — its
antenna is measurably worse).

---

## 3. Hardware
| Device | ID | Bus | Notes |
|---|---|---|---|
| HackRF **Pro** r1.2 fw 2026.01.3 | 977c64de2d717413 | 001 | best UHF antenna; used for all P25 work |
| RTL-SDR #0 | SN 00000202 | 003 | **+24.6 ppm** clock error (measured); weak antenna |
| RTL-SDR #1 | SN 00000001 | 003 | best FM antenna; only one that decoded RDS |

---

## 4. Gotchas worth remembering (all cost real time here)

1. **rtl_433 silently ignores `.cs8`** — logs `Input format "Unknown"`, falls back to cu8.
   HackRF writes *signed* 8-bit; the RTL ecosystem expects *unsigned*. Zero decodes, no error.
2. **Over-amplification looks exactly like an empty band.** `-a 1` on the HackRF with strong
   local FM raised the whole 24–1800 MHz floor 15–20 dB (pure intermod). Later, `-g 62`
   cut the LWIN control-channel SNR from 23.9 dB to 13.1 dB and broke decoding entirely.
   **`-l 40 -g 44 -a 0` is the working setting here.**
3. **RTL-SDR #0 needs `-p 25`.** At 772 MHz, +24.6 ppm = ~19 kHz — wider than a whole
   12.5 kHz P25 channel. Irrelevant for FM (200 kHz channels), fatal for narrowband.
4. **op25 vs CMake 4.2**: op25 sets `cmake_policy(SET CMP0026 OLD)` / `CMP0045 OLD`, removed
   in CMake 4. `-DCMAKE_POLICY_VERSION_MINIMUM` does **not** help. Patch both to `NEW` and
   raise `cmake_minimum_required` to 3.10.
5. **HackRF Pro reports an 8–20 Msps floor through gr-osmosdr** (HackRF One allows 2).
   `hackrf_transfer` still accepts 2 Msps directly.
6. **op25 `rx.py` ignores `-F` if you also pass `-f`** — `elif (self.rtl_found or
   options.frequency)` is evaluated *before* `elif options.ifile`, so it takes the SDR path
   with a null source and dies. Omit `-f` when decoding a file.
7. **RDS needs >=171 kHz sample rate** (57 kHz subcarrier). Lower rates silently drop RDS.
8. **DVB driver must be unbound**: blacklist `dvb_usb_rtl28xxu` (done in
   `/etc/modprobe.d/blacklist-rtlsdr.conf`).
9. **Live op25 works — but only via SoapySDR, not gr-osmosdr.** gr-osmosdr forces an
   8 Msps floor on the HackRF Pro (`supported sample rates 8000000-20000000`), giving op25
   a decim=333 chain that never locked. SoapySDR exposes **1-20 MSps** for the same radio;
   at `-S 2000000` (decim=83) op25 locks immediately:
   ```
   --args 'soapy=0,driver=hackrf' -N 'AMP:0,LNA:40,VGA:44' -S 2000000 -o 25000
   ```
   This was the single blocker on live trunk-following. The file-based pipeline
   (`scripts/lwin_decode.sh`) still works and remains useful for offline re-analysis.

---

## 5. Antenna reality check
| Band | SNR (HackRF, new antenna) |
|---|---|
| 88–108 MHz FM | +33 dB |
| 162.4 MHz NOAA WX | +10.4 dB |
| 161.975/162.025 AIS | **below noise floor** |
| 772 MHz LWIN | +17 to +28 dB |

ADS-B (1090), AIS (162) and ISM (433/915) produced no decodes — antenna-limited, not
software. A dedicated 1090 MHz ADS-B antenna + LNA would unblock ADS-B; AIS needs a
proper marine-band antenna (relevant here given Mississippi River port traffic).

---

---

## 6. Recording clear talkgroups

**One command:**
```bash
./scripts/lwin_listen.sh          # run until Ctrl-C
./scripts/lwin_listen.sh 600      # or a fixed number of seconds
python3 scripts/list_recordings.py
```

Files are named with the talkgroup **at save time**:
```
TG19592_39-PCSO-PTRL_20260830-162729.wav     Sheriff - Patrol
TG18513_63-WFSO-DSP_20260830-162744.wav      Sheriff - Dispatch
TG20039_32-FIRE-DSP1_20260830-162756.wav     Parishwide Fire Dispatch 1
TG17513_61-PAPD-DISP_20260830-162849.wav     Port Allen Police - Dispatch
TG17524_61-JAIL-CNTR_20260830-162948.wav     Prison - Central
```
`recordings/calls.json` carries the full metadata (tgid, alpha, description, category,
encryption flag, start time, duration).

### How the talkgroup gets into the filename
The UDP audio stream carries **only PCM** — no talkgroup ID. `udp_audio_record.py`
therefore tails op25's log in parallel, tracks the currently-active talkgroup
(`voice update: tg(N)` / `hold active tg(N)` / `set tgid=N`), and stamps it on the file
when the call is flushed. A talkgroup older than 12 s is treated as stale, and a call
whose grant lands just *after* the first audio packet is back-filled.

Verified on a 160 s run: **10/10 calls labelled, 0 mismatches** against an independent
re-derivation from op25's own timestamps, and 0 encrypted talkgroups recorded.

Only **unencrypted** talkgroups are recorded. Two safeguards:
- `lwin_clear_whitelist.txt` (534 clear talkgroups) referenced from `lwin_record.tsv`
- `-n` (`--nocrypt`) silences any encrypted traffic that slips through

Encrypted talkgroups observed on this site and deliberately **excluded**: 17086 Prison
Security (full), 17165 BRPD Dispatch 1 (partial), 17133 Baker PD HQ (partial),
17050 Sheriff Dispatch North (partial). No decryption was attempted.

### Four fixes required to make recording work
1. **Run op25 from its `apps/` directory.** `rx.py` does `sys.path.append('tdma')` — a
   *relative* path. Running elsewhere fails with `No module named 'lfsr'`; setting
   PYTHONPATH instead shifts the failure to `op25_c4fm_mod` (in `apps/tx/`).
2. **op25 uses the GNU Radio 3.8 `wavfile_sink` API.** `p25_decoder.py:117` called
   `wavfile_sink(filename, n_channels, sample_rate, bits_per_sample)`; GR 3.10 replaced
   the trailing int with two enums. Patched to
   `wavfile_sink(..., blocks.FORMAT_WAV, blocks.FORMAT_PCM_16, False)`.
3. **Do not pass `-2`.** LWIN is P25 **Phase I**; `-2` sets `num_ambe=2` (TDMA) and breaks
   Phase 1 IMBE voice. Voice updates logging `slot(-)` confirm FDMA.
4. **Run op25 under a pty (`script -q -f`), not `python3 -u`.** The log must be written in
   real time or the recorder mislabels calls, but `-u` crashes op25 on Python 3.14
   (`'_io.FileIO' object has no attribute 'detach'` -> `lost sys.stderr`) because op25
   reconfigures stdout. A pty makes Python line-buffer naturally.

### Site 13 band split (matters for recording)
Control channel is 700 MHz (**773.05625**) but voice is granted onto **800 MHz**
(851-853 MHz). A single receiver retunes between the two, so both must be receivable.

## Privacy note
These are unencrypted public-safety transmissions — legal to receive in the US and
publicly streamed by services like Broadcastify. Recordings nonetheless contain real
incident traffic involving real people. They are kept local, are gitignored, and are not
redistributed here.

---

## 7. Complete call-detail record (the widest LWIN view)

A single radio can either **stay on the control channel** (see every call, hear none) or
**follow calls** (hear audio, miss grants that occur while it is away). Running both modes
gives the full picture.

```bash
./scripts/lwin_cdr_run.sh 360     # control-channel only, never retunes
python3 scripts/lwin_cdr.py       # parse into a call-detail record
```

The trick: point the whitelist at a **non-existent talkgroup** (`lwin_nofollow.txt`
contains `999999`), so op25 never tunes away and logs 100% of grants.

### 6-minute result — 33 talkgroups vs 9 seen while recording audio
- **3765 grant events**, 14 172 TSBK messages, 20 distinct opcodes
- **101 distinct source radios** (unit IDs)
- 19 voice channels in use (851-860 MHz)
- **2479 grants on clear talkgroups, 1167 on encrypted**

| Agency | Talkgroups observed |
|---|---|
| LA State Police | Troop A / G / I Dispatch |
| DOTD Motorist Assistance Patrol | Regions 1, 2, 5, 7 |
| Sheriffs | Pointe Coupee, East Feliciana, Iberville, Livingston, St. Bernard |
| Municipal | Port Allen PD, Central PD, Baton Rouge PD (1-4), Baton Rouge Fire |
| Campus / schools | LSU PD, LSU Golf Course, EBR School Board Safety |
| EMS / wildlife | Acadian EMS Zone 4, EBR Medical Common, LDWF Region 7 |

Busiest single talkgroup was 17050 (EBR Sheriff Dispatch North, 494 grants) — which is
*partially encrypted*, so it is excluded from audio recording.

### Whitelist coverage
`lwin_clear_whitelist.txt` was expanded from the 8 talkgroups initially observed to
**534 clear talkgroups** across every Baton Rouge-area category in the reference DB
(EBR, WBR, Livingston, Ascension, Iberville, E/W Feliciana, Pointe Coupee, LSU, Southern,
State Police Troop A, EMS agencies, LDWF). Zero encrypted talkgroups are in that list.

### Why a dedicated control-channel receiver is not possible here (measured)
Ideal setup: park an RTL-SDR on the control channel while the HackRF follows voice.
Measured control-channel SNR on **both** RTL-SDRs: **+2.7 dB** — far below the ~15 dB P25
needs. Their antennas were never swapped (only the HackRF's was). Until a second decent
UHF antenna or a coax splitter is added, single-radio retuning is the only option, and the
CDR/audio split above is the best workaround.

(Incidentally, RTL dongle **1** is well calibrated: its control-channel peak landed
+2.1 kHz from nominal, versus dongle 0's +24.6 ppm. Use `-p 0` for dongle 1.)

---

## 8. Broadening results & survey methodology fix

### Wideband survey: use a ROLLING baseline, not a global one
`scripts/find_control.py` compares every bin to one global median. Across an 830 MHz
sweep that is wrong — the noise floor is not flat, so whole regions with a locally-high
floor (e.g. 130-137 MHz airband) appeared as ~40 consecutive "signals" at +13 dB.

`scripts/find_signals.py` replaces it with a **rolling-median baseline** (default 2 MHz
window) plus a local-MAD z-score. Same 130-960 MHz sweep: 8 real carriers instead of dozens
of phantoms.

| MHz | excess | z | std | Identification |
|---|---|---|---|---|
| 769.1667 | 17.4 dB | 18.3 | 1.22 | LWIN control channel (site 50 / 72) |
| 319.9510 | 13.4 dB | 33.5 | 1.76 | continuous carrier, UHF 225-400 band |
| 157.6961 | 12.9 dB | 26.3 | 5.39 | VHF marine / business, bursty |
| 858.2843 | 10.2 dB | 17.6 | 2.67 | LWIN 800 MHz voice |
| 280.0000 | 10.3 dB | 49.0 | 2.39 | **local RFI — see below** |

### 280.000000 MHz is local interference, not a signal
A 38 dB, **0.5 kHz wide**, unmodulated constant-envelope carrier at an exactly round
frequency. Retuning test (the standard discriminator — a real signal keeps its *absolute*
frequency, an LO artifact keeps its *offset*):

| LO center | peak offset | absolute |
|---|---|---|
| 279.850 MHz | +150.0 kHz | 280.00002 MHz |
| 280.400 MHz | −400.0 kHz | 279.99998 MHz |

Absolute frequency is stable, so it is **not** an SDR/LO artifact — it is a real emitter.
But sub-kHz bandwidth, no modulation, and a dead-round frequency mark it as a clock or
oscillator harmonic from nearby electronics. Worth tracking down if it ever lands on a band
of interest.

### ACARS / APRS: attempted, antenna-limited
`acarsdec` on 130.025 + 131.550 and `multimon-ng -a AFSK1200` on 144.390 both ran 7 min and
decoded **nothing**. Measured band energy on the RTL antennas: **+0.9 dB** at 131.550 and
**+0.3 dB** at 144.390 — no usable signal. Same root cause as ADS-B and AIS.

Two acarsdec gotchas found on the way:
- All frequencies must fit one tuner window (~2 MHz). `129.125 … 131.725` is rejected with
  `Frequencies too far apart`.
- **`-r <device>` must come last**, immediately before the frequency list. Putting `-g 49.6`
  after `-r` makes acarsdec parse the gain as a frequency: `Invalid frequency 49600000`.

### Where the remaining headroom is
Everything still blocked (ADS-B, AIS, ACARS, APRS, a dedicated LWIN control-channel
receiver) is blocked by the **RTL-SDR antennas**, which were never swapped. The single
cheapest unlock is a coax splitter feeding the good antenna to an RTL-SDR, or a second
wideband antenna. That one change enables a dedicated control-channel receiver, which would
recover the ~60% of calls the single radio misses while it is away following a call.

---

## 9. What encryption is LWIN actually using?

Measured, not assumed. The P25 **ESS** (Encryption Sync Sequence) in the LDU2 voice frame
carries **ALGID**, **KID** and the message indicator *in the clear*, so the cipher can be
identified without any decryption. op25 prints it at `-v 10` (`p25p1_fdma.cc:279`).

```bash
# follow the encrypted talkgroups (no -n, no audio recorder) and read ESS headers
script -q -f -c "cd <op25>/apps && python3 rx.py ... -T lwin_enc.tsv -V -v 10" results/enc_id.log
python3 scripts/identify_encryption.py
```

### Result — 4-minute sample, 148 ESS frames, 9 talkgroups
| ALGID | KID | Count | Algorithm |
|---|---|---|---|
| `0x80` | 0x0 | 138 | **CLEAR — unencrypted** |
| `0xAA` | 0x8 | 9 | **ADP / RC4 (Motorola "Advanced Digital Privacy", 40-bit)** |
| `0xC4` | 0x9726 | 1 | not a valid P25 ALGID — bit error (marginal 800 MHz SNR) |

| TG | RR flag | Alpha | Observed |
|---|---|---|---|
| 17050 | partial | 17-SO DISP N | clear ×39 (+1 corrupt) |
| 17171 | partial | 17-BRPD DSP4 | clear ×30, **ADP ×2** |
| 17165 | partial | 17-BRPD DSP1 | clear ×18, **ADP ×6** |
| 17086 | full | 17 JAIL SEC1 | clear ×23 |
| 17051 | partial | 17-SO DISP S | clear ×21 |
| 17133 | partial | 17-BAKER PD HQ | clear ×7 |
| 17169 | partial | 17-BRPD DSP3 | **ADP ×1** |

**The only encryption algorithm observed is ADP (ALGID 0xAA), key ID 0x8**, and only on
Baton Rouge PD dispatch talkgroups (17165 / 17169 / 17171) — all sharing one key ID.
**No AES-256 (0x84), AES-128 (0x85) or DES (0x81/0x83) was seen** on the sampled talkgroups.

Two things worth noting:
- The "partial" flag in RadioReference is accurate: **93% of frames on these talkgroups
  were `0x80` clear**. They encrypt selectively, not continuously.
- **17086 "Prison Security" is flagged `full` in RadioReference but transmitted `0x80`
  clear in all 23 observations here** — the database flag looks stale, or those particular
  transmissions were not encrypted.

Caveats: ~4 minutes, only the 9 whitelisted talkgroups, and the 800 MHz voice channels are
marginal here (the stray `0xC4` is direct evidence of frame corruption). Other agencies on
LWIN (State Police, DOTD, other parishes) were not sampled and may differ. A longer run
across more talkgroups would firm this up.

**No decryption was attempted and no key material was recovered** — only the cleartext
algorithm identifier was read. Encrypted talkgroups remain excluded from recording.

## Layout
```
scripts/    lwin_listen.sh          <- start listening (one command)
            list_recordings.py      <- show what was recorded
            lwin_cdr_run.sh / lwin_cdr.py   <- full call-detail record
            find_signals.py         <- wideband survey (rolling baseline)
            udp_audio_record.py lwin_decode.sh annotate_lwin.py
            scan_p25band.py sweep_peaks.py find_control.py fetch_lwin_db.py
results/    sweeps, op25 logs, RDS json, SPECTRUM_REPORT.md
reference/  lwin_talkgroups.json  lwin_sites.json  lwin_categories.json
captures/   raw IQ (.cs8) and op25 input (.cfile)  [large, gitignored]
src/        op25 (patched), rtl-ais, redsea, acarsdec
```

## Legal note
Everything here is **receive-only** of unencrypted, publicly broadcast signals, which is
legal in the US. Encrypted talkgroups were not decrypted (several EBR law-enforcement
talkgroups are flagged full/partial encryption in the reference DB and were left alone).
Cellular bands were not touched (18 USC 2511). No digital-pager message content was logged.
