#!/usr/bin/env python3
"""Does narrowing the tuner bandwidth improve control-channel SNR?

WHY
---
The vendor's Windows driver bundle is stock hayguen/ExtIO_RTL, whose release
notes advertise, for R820T2/R860 tuners:

    "Narrow tuner bandwidths - down to ~300 kHz - improving dynamic and
     'sensitivity' by removing unnecessary signals"

That is a front-end filter setting, not a resampling trick: the R820T2's IF
filter defaults to several MHz wide, so everything in that window reaches the
mixer and the RTL2832U's 8-bit ADC. A strong nearby transmitter then steals
headroom from the weak one you actually want.

This system has that exact problem documented. README section 4, gotcha 2:

    "Over-amplification looks exactly like an empty band. -a 1 on the HackRF
     with strong local FM raised the whole 24-1800 MHz floor 15-20 dB (pure
     intermod)."

A P25 control channel is 12.5 kHz. Nothing outside a few hundred kHz of it is
wanted, so if wideband energy is desensitising the receiver, clamping the tuner
IF should show up as a better SNR — for free, no new hardware.

librtlsdr 2.0.2 exposes rtlsdr_set_tuner_bandwidth, so this is testable on
Linux; rtl_power and rtl_sdr just do not expose the knob.

    python3 scripts/tuner_bw_test.py
    python3 scripts/tuner_bw_test.py -d 1 -f 773056250 --secs 4
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import sys

import numpy as np

DEFAULT_CC = 773_056_250
SAMPLE_RATE = 1_024_000

# 0 asks the driver for automatic (widest) — the default everything else uses.
BANDWIDTHS = [0, 1_500_000, 1_000_000, 600_000, 300_000, 200_000]


def load() -> ctypes.CDLL:
    lib = ctypes.CDLL(ctypes.util.find_library('rtlsdr') or 'librtlsdr.so.0')
    lib.rtlsdr_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32]
    lib.rtlsdr_close.argtypes = [ctypes.c_void_p]
    lib.rtlsdr_set_center_freq.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.rtlsdr_set_sample_rate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.rtlsdr_set_tuner_gain_mode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.rtlsdr_set_tuner_gain.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.rtlsdr_set_tuner_bandwidth.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.rtlsdr_reset_buffer.argtypes = [ctypes.c_void_p]
    lib.rtlsdr_read_sync.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    lib.rtlsdr_set_freq_correction.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return lib


def snr_at_dc(iq: np.ndarray, rate: int, channel_hz: float = 12_500.0) -> float:
    """SNR of the carrier at band centre, in dB.

    The receiver is tuned directly to the control channel, so the signal sits at
    DC. Welch-average to steady the estimate, take the peak inside +/- half a
    channel width, and compare it to the median of everything outside a guard
    band — a median, so a second carrier in the window cannot drag the floor up.
    """
    nfft = 4096
    nseg = len(iq) // nfft
    if nseg < 1:
        return float('nan')
    win = np.hanning(nfft)
    acc = np.zeros(nfft)
    for i in range(nseg):
        seg = iq[i * nfft:(i + 1) * nfft] * win
        acc += np.abs(np.fft.fftshift(np.fft.fft(seg))) ** 2
    psd = 10 * np.log10(acc / nseg + 1e-20)

    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / rate))
    signal = np.abs(freqs) <= channel_hz / 2
    noise = np.abs(freqs) > channel_hz * 4          # guard band
    if not signal.any() or not noise.any():
        return float('nan')
    return float(psd[signal].max() - np.median(psd[noise]))


def measure(lib, dev, freq: int, gain_tenths: int, bw: int, secs: float) -> float:
    lib.rtlsdr_set_tuner_bandwidth(dev, bw)
    lib.rtlsdr_set_center_freq(dev, freq)
    lib.rtlsdr_reset_buffer(dev)

    want = int(SAMPLE_RATE * 2 * secs) & ~0x3FFF     # multiple of 16 KiB
    buf = (ctypes.c_ubyte * want)()
    got = ctypes.c_int(0)
    if lib.rtlsdr_read_sync(dev, buf, want, ctypes.byref(got)) != 0 or got.value < 4096:
        return float('nan')

    raw = np.frombuffer(bytes(buf[:got.value]), dtype=np.uint8).astype(np.float32)
    iq = (raw[0::2] - 127.5) + 1j * (raw[1::2] - 127.5)
    return snr_at_dc(iq, SAMPLE_RATE)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-d', '--device', type=int, default=0)
    ap.add_argument('-f', '--freq', type=int, default=DEFAULT_CC)
    ap.add_argument('-g', '--gain', type=float, default=40.0, help='dB')
    ap.add_argument('--secs', type=float, default=3.0, help='capture seconds per setting')
    ap.add_argument('--ppm', type=int, default=0, help='measured -0.3 ppm on both, so 0')
    a = ap.parse_args()

    lib = load()
    dev = ctypes.c_void_p()
    if lib.rtlsdr_open(ctypes.byref(dev), a.device) != 0:
        print(f'cannot open device {a.device} — in use?', file=sys.stderr)
        return 1

    try:
        lib.rtlsdr_set_sample_rate(dev, SAMPLE_RATE)
        lib.rtlsdr_set_freq_correction(dev, a.ppm)
        lib.rtlsdr_set_tuner_gain_mode(dev, 1)          # manual
        lib.rtlsdr_set_tuner_gain(dev, int(a.gain * 10))

        print(f'dongle {a.device}  {a.freq / 1e6:.5f} MHz  gain {a.gain} dB  '
              f'{a.secs}s per setting')
        print(f'{"tuner BW":>12}  {"SNR":>8}   vs auto')

        baseline = None
        for bw in BANDWIDTHS:
            snr = measure(lib, dev, a.freq, int(a.gain * 10), bw, a.secs)
            label = 'auto/wide' if bw == 0 else f'{bw / 1000:.0f} kHz'
            if bw == 0:
                baseline = snr
                print(f'{label:>12}  {snr:7.1f} dB   —')
            else:
                delta = snr - baseline if baseline == baseline else float('nan')
                mark = '  <-- better' if delta > 1.0 else ''
                print(f'{label:>12}  {snr:7.1f} dB   {delta:+5.1f} dB{mark}')
    finally:
        lib.rtlsdr_set_tuner_bandwidth(dev, 0)          # restore automatic
        lib.rtlsdr_close(dev)

    print()
    print('A P25 channel is 12.5 kHz wide, so nothing beyond a few hundred kHz')
    print('is wanted. If a narrow setting wins by more than ~2 dB, the receiver')
    print('is being desensitised by out-of-band energy and op25 should be run')
    print('with the tuner clamped — free SNR, no new hardware.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
