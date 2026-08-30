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
| 774.14282 | 30.0 dB | ~6200 | Phase 2 TDMA voice |

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

```bash
./scripts/lwin_record.sh 400        # ~6.5 min; writes recordings/tgid-<tg>-<time>.wav
python3 scripts/label_recordings.py # labels each call with talkgroup name + duration
```

Only **unencrypted** talkgroups are recorded. Two safeguards:
- `lwin_clear_whitelist.txt` (referenced from `lwin_record.tsv`) limits op25 to talkgroups
  the reference DB marks `clear`.
- `-n` (`--nocrypt`) silences any encrypted traffic that slips through.

Encrypted talkgroups observed on this site and deliberately **excluded**: 17086 Prison
Security (full), 17165 BRPD Dispatch 1 (partial), 17133 Baker PD HQ (partial),
17050 Sheriff Dispatch North (partial). No decryption was attempted.

### Results from a 6.5-minute run
15 calls, 34.1 s of decoded voice, all `clear`:

| TG | Calls | Audio | Agency |
|---|---|---|---|
| 17345 | 3 | 12.5s | St. George Fire OPS |
| 17139 | 8 | 11.9s | Baton Rouge Fire Dispatch 1 |
| 17000 | 2 | 6.0s | LSU Police Dispatch 1 |
| 6848 | 2 | 3.8s | Acadian EMS Zone 4 |

Audio verified as genuine speech, not noise: 95-97% of energy in the 300-3400 Hz voice
band with 26-49% syllabic (2-8 Hz) envelope modulation.

### Why `-L` logfile workers do NOT work here (and what does)
`-L` demodulates all calls from a **single fixed** center frequency — the workers never
retune (`trunking.py:1743` needs `tsys.center_frequency`). But site 13's control channel is
773 MHz while voice is granted onto **856-860 MHz**, ~85 MHz apart; no sample rate spans
that. This box also has **no sound card** (`snd-aloop` unavailable), so `-U`/`-O` are out.

The working approach: normal trunking (op25 retunes the one radio per call) with audio sent
over UDP, captured by `scripts/udp_audio_record.py`. op25 emits 320-byte UDP packets of
S16LE PCM @ 8 kHz to `--wireshark-port`; `-w` enables that output **without** requiring a
sound device. Calls are split on a 2 s gap and labelled by correlating WAV start times
against op25's timestamped `tg(NNN)` log lines.

### Three fixes required to make recording work
1. **Run from op25's `apps/` directory.** `rx.py` does `sys.path.append('tdma')` — a
   *relative* path. Running elsewhere fails with `No module named 'lfsr'`; setting
   PYTHONPATH instead shifts the failure to `op25_c4fm_mod` (in `apps/tx/`).
2. **op25 uses the GNU Radio 3.8 `wavfile_sink` API.** `p25_decoder.py:117` called
   `wavfile_sink(filename, n_channels, sample_rate, bits_per_sample)`; GR 3.10 replaced
   the trailing int with two enums. Patched to:
   `wavfile_sink(filename, n_channels, sample_rate, blocks.FORMAT_WAV, blocks.FORMAT_PCM_16, False)`
   This only fires on the `-L` logfile path, so metadata-only runs never hit it.
3. **Do not pass `-2`.** LWIN is P25 **Phase I**; `-2` sets `num_ambe=2` (TDMA) and breaks
   Phase 1 IMBE voice. Voice updates logging `slot(-)` confirm FDMA.

### Site 13 band split (matters for recording)
Control channel is 700 MHz (**773.05625**) but voice is granted onto **800 MHz**
(851-853 MHz). A single receiver retunes between the two, so both must be receivable.

## Privacy note
These are unencrypted public-safety transmissions — legal to receive in the US and
publicly streamed by services like Broadcastify. Recordings nonetheless contain real
incident traffic involving real people. They are kept local, are gitignored, and are not
redistributed here.

## Layout
```
scripts/    sweep_peaks.py find_control.py analyze_p25*.py scan_p25band.py
            symbols.py sig2.py lwin_decode.sh fetch_lwin_db.py annotate_lwin.py
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
