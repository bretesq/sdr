# Baton Rouge, LA (70809) — Spectrum Survey & Decode Report
Date: 2026-08-30 · Location: Baton Rouge, LA 70809

## Hardware
| Device | Serial / ID | Bus | Notes |
|---|---|---|---|
| HackRF **Pro** r1.2, fw 2026.01.3 | 977c64de2d717413 | 001 | Best VHF/UHF antenna of the three |
| RTL-SDR #0 (RTL2838, R820T) | SN 00000202 | 003 | **+24.6 ppm** clock error (measured) |
| RTL-SDR #1 (RTL2838, R820T) | SN 00000001 | 003 | Best FM antenna — only dongle that decoded RDS |

## DECODED DATA (working results)

### FM RDS — live digital broadcast data
| Freq | PI code | Station (PS) | Program type | RadioText |
|---|---|---|---|---|
| **88.5 MHz** | `0x6CFA` | WJFM / "SonLife Radio" | Religion, Jazz | `SonLife Radio Network www.jsm.org` |
| **104.1 MHz** | `0x47D6` | "The Vibe" | Weather | `104.1 The Vibe - Wanna Be Startin' Somethin' - Michael Jackson`<br>`R&B and Back in the Day Jams` |

547 and 559 RDS groups decoded respectively (~50 s each) — includes **live now-playing song metadata**.
88.5 WJFM is Jimmy Swaggart Ministries' SonLife Radio, headquartered in Baton Rouge.

### FM broadcast stations confirmed present (sweep)
88.5 (+32.8 dB), 89.3 WRKF/NPR (+28.1), 104.1 (+31.9), 100.7 (+24.9), 102.5 WFMF (+19.0),
107.3 (+17.0), 98.1 (+16.5), 96.1 (+16.0), 101.5, 102.1, 103.3

## LWIN P25 TRUNKING — located, not decoded

**Control channels found blind, then corroborated.** Method: `hackrf_sweep` + per-bin
time-variance analysis. A P25 control channel transmits *continuously* (low variance);
voice channels are bursty (high variance). Ranking by mean-power with a low-std filter
isolates control channels with no prior frequency knowledge.

| Measured (MHz) | Mean over floor | Std dev | Verdict | Published LWIN |
|---|---|---|---|---|
| 769.1866 / 769.2114 | +16.4 dB | 1.24 | CONTINUOUS | **769.16875** ✓ |
| 772.0721 / 772.0970 | +14.3 dB | 1.69 | CONTINUOUS | **772.08125** ✓ |
| 769.86, 772.67, 773.04, 774.14 | +9…13 dB | 3.7–5.5 | bursty (voice) | 769.56875, 770.66875, 771.28125 ✓ |

(25 kHz sweep bins straddle each 12.5 kHz P25 channel — hence adjacent pairs.)

**High-resolution confirmation** of the control channel via 2 Msps offset-tuned IQ capture:
- Measured **772.08071 MHz** vs 772.08125 nominal → only **−540 Hz (0.7 ppm)** error. HackRF is well calibrated.
- Occupied bandwidth **9.4 kHz** — consistent with a P25 narrowband 12.5 kHz channel.
- Peak SNR **16.6 dB** (122 Hz resolution bins).

**800 MHz band (850–870 MHz): empirically EMPTY** — zero candidates above +8 dB.
Local public-safety traffic is on 700 MHz LWIN, not the 800 MHz band.

### Why op25 did not lock (investigated, not guessed)
Attempts: RTL-SDR @1 Msps (`-q 0`); RTL-SDR @1 Msps (`-q 25`, ppm-corrected); HackRF @2 Msps
(invalid — below the Pro's 8 Msps driver floor); HackRF @8 Msps `cqpsk`; HackRF @8 Msps `fsk4`
across **all 8 published channels**; HackRF @8 Msps with **`-o 25000` DC-offset tuning**.
Result every time: `NAC 0x0`, `tsbks 0`, control-channel timeout.

Root cause is signal quality, established by direct IQ analysis:
- Instantaneous-frequency histogram of the channel shows **one broad Gaussian peak**, not the
  four discrete levels (−1800/−600/+600/+1800 Hz) that P25 C4FM produces.
- No 4800 baud symbol clock recoverable (strongest line 7.0 kHz, not 4800 Hz).
- P25 needs ~15–20 dB in-channel SNR; measured peak is ~16.6 dB in narrow bins, lower
  averaged across the 9.4 kHz channel. Marginal — below threshold with this antenna.
- Ruled out individually: ppm error, sample rate, modulation type (cqpsk *and* fsk4),
  wrong control channel (all 8 tried), and DC-offset collision (`-o 25000`). Only SNR remains.

**This is an antenna limitation, not a software one.**

## Antenna analysis — the binding constraint
| Frequency | Radio | SNR |
|---|---|---|
| 98 MHz (FM) | HackRF | **+33 dB** |
| 162.4 MHz (NOAA WX) | HackRF | +6.8 dB |
| 161.975 / 162.025 (AIS) | HackRF | −0.6 / −1.0 dB (**below floor**) |
| 772.08 MHz (LWIN CC) | HackRF | +14.3 dB |
| 772.08 MHz (LWIN CC) | RTL #0 | +5.7 … +9.9 dB |

Excellent below ~110 MHz, weak above. The attached antennas are FM-tuned.
ADS-B (1090), AIS (162), and ISM (433/915) all returned zero decodes for this one reason.

## Not decoded, with reason
- **ADS-B 1090 MHz** — not cleanly tested (a run was killed by my own `pkill`); antenna-limited regardless.
- **AIS 161.975/162.025** — measured *below* noise floor. Not receivable with this antenna.
- **ISM 433.92 / 915 MHz** — `rtl_433` 4 min, both bands, zero decodes.
- **NOAA WX 162.400** — present at only +6.8 dB; too weak for SAME digital headers.

## Durable toolchain fixes (reusable)
1. **RTL-SDR #0 requires `-p 25`** (+24.6 ppm, measured against the known LWIN carrier).
   At 772 MHz that is ~19 kHz — larger than a whole 12.5 kHz P25 channel.
   Irrelevant for FM (200 kHz channels) but fatal for narrowband.
2. **op25 vs CMake 4.2**: op25 sets `cmake_policy(SET CMP0026 OLD)` / `CMP0045 OLD`, which
   CMake 4 removed. `-DCMAKE_POLICY_VERSION_MINIMUM` does *not* help. Patch both to `NEW`
   and raise `cmake_minimum_required` to 3.10 — then it builds and installs clean.
3. **HackRF Pro reports a 8–20 Msps floor through gr-osmosdr** (the original HackRF One allows 2).
   `hackrf_transfer` still accepts 2 Msps directly. Use `-S 8000000` for op25.
4. **rtl_433 silently ignores `.cs8`** — logs `Input format "Unknown"` and falls back to cu8.
   HackRF writes *signed* 8-bit; the RTL ecosystem expects *unsigned*. Zero decodes, no error.
5. **`-a 1` (HackRF amp) overloads the front end** with strong local FM present — produced a
   false wideband floor 15–20 dB up across 24–1800 MHz. Use `-a 0` for surveys.
6. **RDS needs >=171 kHz sample rate** (57 kHz subcarrier). Lower rates silently drop RDS.

## Recommendation to unblock the rest
One purchase fixes ADS-B, AIS, ISM **and** LWIN: a **wideband discone or VHF/UHF antenna**
(and/or a dedicated 1090 MHz ADS-B antenna with an LNA). Present antennas are FM-band.
With ~6 dB more gain at 700–800 MHz, the LWIN control channel should decode — it is
already located, verified, and 2–4 dB short of threshold.
