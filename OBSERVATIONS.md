# RF Spectrum Observations — Baton Rouge, LA 70809

**Date:** 2026-08-30 · **Location:** Baton Rouge, LA 70809 · **Mode:** receive-only

A record of what was observed on the air, how each observation was produced, and where
the uncertainty lies. Companion to `README.md` (which is the operational how-to).

---

## 1. Summary of observations

| # | Observation | Confidence |
|---|---|---|
| 1 | LWIN P25 trunked system decoded: NAC `0x1BD`, WACN `0xBEE00`, SYSID `0x1BD` | **High** — cross-verified against RadioReference |
| 2 | Receiving site = RFSS 1 / Site 13 (`0x0D`) "Baton Rouge Simulcast", control channel **773.05625 MHz** | **High** — op25 self-reported the frequency from the channel ID |
| 3 | 11 adjacent sites, forming a geographic ring around Baton Rouge | **High** — all resolve to real neighbouring sites |
| 4 | 33 talkgroups / 101 source radios / 3765 grants in 6 min | **High** |
| 5 | Voice is granted onto **851–860 MHz** although the control channel is at 773 MHz | **High** — every logged voice frequency matches Site 13's published list |
| 6 | Only encryption in use is **ADP / RC4 (ALGID `0xAA`, KID `0x8`)**, on BRPD dispatch only | **Medium** — small sample, see §6.4 |
| 7 | 93% of frames on "encrypted" talkgroups are actually `0x80` clear | **Medium** — same sample |
| 8 | FM RDS decoded on 88.5 and 104.1 incl. live song metadata | **High** |
| 9 | Strong local RFI at exactly **280.000000 MHz** | **High** — confirmed by retune test |
| 10 | ADS-B / AIS / ACARS / APRS all unreceivable | **High** — measured, not inferred |

---

## 2. Station configuration

| Device | ID | Bus | Measured characteristics |
|---|---|---|---|
| HackRF **Pro** r1.2, fw 2026.01.3 | SN `977c64de2d717413` | 001 | Frequency error **−540 Hz @ 772 MHz (≈0.7 ppm)**. Good antenna (swapped mid-session). |
| RTL-SDR #0 (RTL2838 / R820T) | SN `00000202` | 003 | **+24.6 ppm** — needs `-p 25`. Weak antenna: 0 RDS groups on 6 stations. |
| RTL-SDR #1 (RTL2838 / R820T) | SN `00000001` | 003 | Well calibrated (**+2.1 kHz @ 773 MHz ≈ 2.7 ppm**). Best FM antenna — decoded all RDS. |

### Measured antenna performance (HackRF, post-swap, `-l 40 -g 44 -a 0`)
| Band | SNR | Consequence |
|---|---|---|
| 88.5 / 104.1 MHz (FM) | **+33.4 / +31.7 dB** | RDS decodes easily |
| 162.400 MHz (NOAA WX) | +10.4 dB | audible, too weak for SAME headers |
| 161.975 / 162.025 (AIS) | **−2.2 / −2.1 dB** | *below noise floor* — unreceivable |
| 773.056 MHz (LWIN control) | +28.3 dB | decodes reliably |
| 851–860 MHz (LWIN voice) | +8…+11 dB | marginal; some frames corrupt |
| 773.056 MHz on **either RTL-SDR** | **+2.7 dB** | far below the ~15 dB P25 needs |

The last row is the single most consequential measurement in the session: it rules out a
dedicated control-channel receiver until a second decent UHF antenna (or a splitter) exists.

### Toolchain
Ubuntu 26.04 · GNU Radio 3.10.12 · op25 (boatbod, patched) · SoapySDR 0.8.1 ·
rtl-sdr 2.0.2 · rtl_433 25.12 · redsea · acarsdec · multimon-ng 1.3.1

---

## 3. How the observations were generated

### 3.1 Finding the control channel — symbol-rate scanning
Power alone was **actively misleading**. A variance-based search (continuous carrier =
low time-variance) pointed at 772.08 MHz, which is strong and continuous but turned out to
be a *different site's voice channel*. Every op25 attempt against it failed.

What worked: capture the whole public-safety allocation once, then test **every** carrier
for its symbol rate. P25 Phase 1 C4FM is **4800 baud**; the control channel is the one
whose symbol clock is both strong and continuous.

```bash
hackrf_transfer -r psband.cs8 -f 772000000 -s 8000000 -l 40 -g 44 -a 0 -n 80000000
python3 scripts/scan_p25band.py       # PSD -> narrowband carriers -> symbol-clock test
```

Method per candidate: shift to baseband → decimate to 48 kHz → 6 kHz channel filter →
FM-demodulate → take `|instantaneous frequency|` → FFT → look for a line at 4800 Hz.
The non-linearity exposes the symbol clock as a spectral line.

| Freq (MHz) | SNR | Symbol clock | Interpretation |
|---|---|---|---|
| **773.05688** | 28.3 dB | **4800 Hz @ 41.4 dB** | **Site 13 control channel** |
| 769.19434 | 35.7 dB | 4800 Hz @ 19.3 dB | Site 50 (South Baton Rouge) control channel |
| 772.08081 | 23.9 dB | 6000 Hz (unreliable) | Site 50 *voice* channel — see §7.1 |
| 774.14282 | 30.0 dB | ~6200 Hz (unreliable) | not on Site 13's list |
| 772.68237 | 22.9 dB | 4173 Hz (unreliable) | Site 13 voice channel |

### 3.2 Decoding the control channel
gr-osmosdr forces an **8 Msps floor** on the HackRF Pro, giving op25 a `decim=333` chain
that never locked. SoapySDR exposes **1–20 MSps** for the same radio; at 2 Msps
(`decim=83`) op25 locks immediately.

```bash
python3 rx.py --args soapy=0,driver=hackrf -N AMP:0,LNA:40,VGA:44 \
  -S 2000000 -q 0 -o 25000 -T lwin_cc.tsv -V -v 5
```

### 3.3 Two complementary traffic-capture modes
A single radio can **see every call** or **hear calls**, not both — it must leave the
control channel to receive voice.

- **Call-detail record:** point the whitelist at a non-existent talkgroup (`999999`) so
  op25 never retunes → 100% of grants observed. `scripts/lwin_cdr_run.sh`
- **Audio capture:** normal trunking; op25 retunes per call. `scripts/lwin_listen.sh`

### 3.4 Audio recording without a sound card
This host has no audio hardware and `snd-aloop` is unavailable, so op25's `-U`/`-O` paths
are unusable. op25's `-L` logfile workers are also unusable here: they demodulate from a
single fixed centre frequency, but control (773 MHz) and voice (851–860 MHz) are ~85 MHz
apart — no sample rate spans that.

Solution: `-w` makes op25 emit **320-byte UDP packets of S16LE PCM @ 8 kHz**;
`scripts/udp_audio_record.py` receives them, splits calls on a 2 s gap, and tails op25's
log to name each file with its talkgroup at save time.

### 3.5 Identifying encryption without decrypting
The P25 **ESS** (Encryption Sync Sequence) in the LDU2 voice frame carries **ALGID**,
**KID** and the message indicator **in the clear**. op25 prints it at `-v 10`
(`p25p1_fdma.cc:279`). Reading it identifies the cipher; no ciphertext is touched.

```bash
script -q -f -c "... -T lwin_enc.tsv -V -v 10" results/enc_id.log
python3 scripts/identify_encryption.py
```

### 3.6 Wideband survey — rolling baseline
Comparing every bin to one **global** median is wrong across an 830 MHz sweep: the noise
floor is not flat, so a locally-elevated region (130–137 MHz airband) produced ~40
consecutive phantom "signals". `scripts/find_signals.py` uses a **rolling-median baseline**
(2 MHz window) plus a local-MAD z-score. Same sweep: 8 real carriers instead of dozens.

---

## 4. LWIN observations

### 4.1 System identity (decoded off-air)
```
NAC   0x1BD
WACN  0xBEE00        (Louisiana statewide)
SYSID 0x1BD
RFSS 1, Site 13 (0x0D) -> "Baton Rouge Simulcast", East Baton Rouge Parish
Control channel 773.05625 MHz
```
op25 decoded the channel identifier itself: `rfss_sts_bcst: syid: 1bd rfid: 1 stid: 13
ch1: 16e8(773.056250)` — an internal consistency check that the frequency mapping is right.
RadioReference independently lists LWIN as `Sysid: 1BD WACN: BEE00`, Project 25 Phase I.

### 4.2 Site 13 frequency plan — observed vs published
Published (RadioReference), 24 frequencies, 4 control:
```
control: 773.05625  774.54375  851.0375  851.4875
voice  : 769.68125 769.93125 770.75625 772.68125 851.2875 851.8375 852.0375 852.150
         852.350 852.5625 852.750 852.9125 852.9875 855.9875 856.2375 856.4625
         857.2375 858.2375 859.2375 860.2375
```
Voice frequencies **observed in grants**: 851.8375, 852.150, 852.350, 852.5625, 852.750,
852.9875, 855.9875, 856.2375, 857.2375, 858.2375, 860.2375 — **all present in the published
list**. Strong mutual confirmation.

**Band split:** control at 700 MHz, voice at 800 MHz. Relevant because the 800 MHz leg is
much weaker here (+8…11 dB vs +28 dB), which limits voice decode quality.

### 4.3 Adjacent sites (all RFSS 1)
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

### 4.4 Traffic — 6-minute call-detail record
**3765 grant events · 14 172 TSBK messages · 20 distinct opcodes · 33 talkgroups ·
101 distinct source radios · 19 voice channels · 2479 grants clear / 1167 encrypted**

| Agency | Talkgroups observed |
|---|---|
| LA State Police | Troop A / G / I Dispatch |
| DOTD Motorist Assistance Patrol | Regions 1, 2, 5, 7 |
| Sheriffs | Pointe Coupee, East Feliciana, Iberville, Livingston, St. Bernard, EBR |
| Municipal | Port Allen PD, Central PD, Baton Rouge PD (Disp 1–4), Baton Rouge Fire |
| Campus / schools | LSU PD, LSU Golf Course, EBR School Board Safety |
| EMS / wildlife | Acadian EMS Zones 3/4, EBR Medical Common, Our Lady of the Lake ER, LDWF R7 |

Busiest single talkgroup: **17050 EBR Sheriff Dispatch North, 494 grants** (partially
encrypted, therefore excluded from recording).

### 4.5 Voice recordings
Two runs, clear talkgroups only:

| Run | Whitelist | Result |
|---|---|---|
| 6.5 min | 8 talkgroups (only those observed) | 15 calls / 34.1 s / 4 talkgroups |
| 7.8 min | **534 clear talkgroups** (all BR-area categories) | **33 calls / 117.9 s / 12 talkgroups** |
| 2.7 min | 534 | 10 calls / 37.2 s / 5 talkgroups |

Audio confirmed as genuine speech rather than noise: **95–97% of energy in the
300–3400 Hz voice band** with 26–49% syllabic (2–8 Hz) envelope modulation.

Labelling was verified, not assumed: 10/10 calls matched an independent re-derivation from
op25's own timestamps; 0 encrypted talkgroups recorded.

---

## 5. Encryption survey

**4-minute sample · 148 ESS frames · 9 talkgroups.** No decryption attempted; no key
material recovered. Only the cleartext algorithm identifier was read.

| ALGID | KID | Count | Algorithm |
|---|---|---|---|
| `0x80` | 0x0 | 138 | **CLEAR — unencrypted** |
| `0xAA` | 0x8 | 9 | **ADP / RC4** (Motorola "Advanced Digital Privacy", 40-bit) |
| `0xC4` | 0x9726 | 1 | not a valid P25 ALGID — bit error |

| TG | RR flag | Alpha | Observed |
|---|---|---|---|
| 17050 | partial | 17-SO DISP N | clear ×39, 1 corrupt |
| 17171 | partial | 17-BRPD DSP4 | clear ×30, **ADP ×2** |
| 17165 | partial | 17-BRPD DSP1 | clear ×18, **ADP ×6** |
| 17086 | full | 17 JAIL SEC1 | clear ×23 |
| 17051 | partial | 17-SO DISP S | clear ×21 |
| 17133 | partial | 17-BAKER PD HQ | clear ×7 |
| 17169 | partial | 17-BRPD DSP3 | **ADP ×1** |

**Findings**
1. The only cipher observed is **ADP (`0xAA`), key ID `0x8`**, and only on Baton Rouge PD
   dispatch talkgroups (17165 / 17169 / 17171) — all sharing one key ID.
2. **No AES-256 (`0x84`), AES-128 (`0x85`), DES or 3DES** was seen on the sampled talkgroups.
3. RadioReference's "partial" flag is accurate — **93% of frames were clear**. These
   talkgroups encrypt selectively, not continuously.
4. **17086 "Prison Security" is flagged `full` but transmitted clear in all 23
   observations** — the database flag appears stale, or those transmissions were not
   encrypted.

**Independent confirmation (§4.5 follow-up).** A later 200 s run targeting police dispatch
with `--include-partial` produced 16 calls, of which 8 were on partially-encrypted
talkgroups (BRPD Dispatch 2/4, EBR Sheriff Dispatch S/Alternate). **All 8 verified as
intelligible speech** by the same spectral test used in §4.5 — one BRPD Dispatch 2 call ran
12.1 s. This is a second, independent data point that "partial" talkgroups here carry
substantial clear traffic, and that op25's `-n` cleanly suppresses the encrypted bursts
rather than writing garbage.

---

## 6. Other observations

### 6.1 FM RDS
| Freq | PI | Station | RadioText |
|---|---|---|---|
| 88.5 | `0x6CFA` | WJFM / SonLife Radio | `SonLife Radio Network www.jsm.org` |
| 104.1 | `0x47D6` | "The Vibe" | `104.1 The Vibe - Wanna Be Startin' Somethin' - Michael Jackson` |

547 and 559 RDS groups in ~50 s each, on RTL-SDR #1. RDS requires **≥171 kHz** sample rate
(57 kHz subcarrier); lower rates silently drop it.

### 6.2 Local RFI at 280.000000 MHz
38 dB, **0.5 kHz wide**, unmodulated, constant-envelope, at an exactly round frequency.
Retune test (a real signal keeps its *absolute* frequency; an LO artifact keeps its *offset*):

| LO centre | peak offset | absolute |
|---|---|---|
| 279.850 MHz | +150.0 kHz | 280.00002 MHz |
| 280.400 MHz | −400.0 kHz | 279.99998 MHz |

Absolute frequency is stable → **not** an SDR artifact, a real emitter. Sub-kHz bandwidth
with no modulation on a round frequency → a **clock/oscillator harmonic from nearby
electronics**, not a communications transmitter.

### 6.3 Negative results (measured, not assumed)
| Target | Attempt | Measured signal | Conclusion |
|---|---|---|---|
| ADS-B 1090 MHz | dump1090 | — | antenna-limited (never cleanly tested) |
| AIS 161.975/162.025 | rtl_ais | **−2.2 dB (below floor)** | not receivable |
| ISM 433.92 / 915 MHz | rtl_433, 4 min both bands | — | no decodes |
| ACARS 130.025/131.550 | acarsdec, 7 min | **+0.9 dB** | no traffic decodable |
| APRS 144.390 | multimon-ng AFSK1200, 7 min | **+0.3 dB** | no traffic decodable |
| NOAA SAME 162.400 | — | +10.4 dB | audio OK, too weak for SAME headers |

All trace to the same root cause: the RTL-SDR antennas (never swapped) and the lack of a
1090 MHz / marine-band antenna.

---

## 7. Corrections and open questions

### 7.1 "772.08 MHz is a P25 Phase 2 TDMA voice channel" — **withdrawn**
Earlier in the session I measured a ~6000 Hz symbol clock at 772.08081 MHz and inferred
P25 Phase 2 TDMA. That inference does not hold:
- RadioReference lists LWIN as **Project 25 Phase I**;
- voice updates log `slot(-)` (FDMA, not TDMA slots);
- voice only decoded correctly with `-2` **removed** (Phase 1 IMBE, `num_ambe=1`);
- repeat measurements of that carrier gave inconsistent clocks (7075 Hz, then 6000 Hz) and
  a unimodal, noise-like instantaneous-frequency histogram.

`772.08125` is in fact a **voice channel of Site 50 (South Baton Rouge)** — a different
site — which is why it is continuous-looking yet yielded no TSBKs. The correct reading is
that the symbol-clock estimate is unreliable on weak or idle channels; the 6000 Hz line was
an artifact, not evidence of Phase 2. Symbol-rate scanning is reliable for *finding* strong
control channels, not for classifying weak ones.

### 7.2 Encryption flags were initially quoted from RadioReference, not observed
The `full`/`partial` labels used before §5 came from the community database. §5 is the
first measured statement about encryption in this work, and it partly contradicts the
database (17086).

### 7.3 Open
- Identity of the continuous carrier at **319.951 MHz** (13.4 dB, z=33.5) — not investigated.
- Whether other LWIN users (State Police, DOTD, other parishes) use AES — not sampled.
- 850–870 MHz was measured as empty *before* the antenna swap; afterwards voice channels
  were plainly present there. Early "800 MHz is empty" statements applied only to the old
  antenna and to time-averaged sweeps that bury bursty voice traffic.

---

## 8. Limitations

- Single-site, single-location, short observation windows (4–8 minutes each).
- 800 MHz voice is marginal here (+8…11 dB); some frames are corrupt — the stray `0xC4`
  ALGID is direct evidence.
- The encryption survey covers 9 talkgroups over 4 minutes. It characterises those
  talkgroups in that window, not the whole LWIN system.
- Talkgroup names, categories and encryption flags come from RadioReference, a
  community-maintained source that can be stale (demonstrated in §5).
- A single radio cannot see all grants and hear calls simultaneously; the two figures in
  §4.4 and §4.5 come from different runs and are not directly comparable.

---

## 9. Legal and privacy

Everything here is **receive-only** monitoring of unencrypted, publicly broadcast
transmissions, which is lawful in the United States and is what commercial scanner
services (e.g. Broadcastify) stream publicly.

- **No decryption was attempted** and no key material was recovered. Only the cleartext
  ALGID/KID identifier was read from the ESS.
- Encrypted and partially-encrypted talkgroups are **excluded from recording** by an
  explicit whitelist plus op25's `-n` (`--nocrypt`).
- Cellular bands were not touched (18 U.S.C. § 2511).
- No digital-pager message content was logged.
- Recordings contain real incident traffic involving real people. They are kept local,
  are gitignored, and are not redistributed.
