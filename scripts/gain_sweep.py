#!/usr/bin/env python3
"""Find the gain that maximises SNR on a channel, and check for overload.

WHY
---
Every measurement on this system so far used gain 40 dB, chosen arbitrarily. The
R820T2 offers 29 steps from 0 to 49.6 dB, and more gain is not monotonically
better: past a point the LNA and mixer start generating intermodulation from
strong out-of-band signals, which raises the noise floor faster than it raises
the wanted signal. README section 4 gotcha 2 documents exactly that failure on
the HackRF here —

    "Over-amplification looks exactly like an empty band. -a 1 with strong local
     FM raised the whole 24-1800 MHz floor 15-20 dB (pure intermod)."

— and the fix there was to turn gain DOWN. The same trap applies to an 8-bit
RTL2832U fed by a wideband antenna in a city with strong FM.

This sweeps every supported gain step, reporting signal, noise floor and SNR
separately. Watch the floor: if it climbs with gain while SNR flattens or falls,
the front end is overloading and the best setting is lower than the maximum.

Also tries the RTL2832U's own AGC, which nothing in this project has used.

    python3 scripts/gain_sweep.py -d 1
    python3 scripts/gain_sweep.py -d 1 -f 104100000   # sanity: a strong signal
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import sys

import numpy as np

DEFAULT_CC = 773_056_250
SAMPLE_RATE = 1_024_000

# The receiver is deliberately tuned BELOW the channel by this much, so the
# wanted carrier lands at +OFFSET_HZ and the RTL2832U's DC offset spike stays at
# 0 where it can be excluded.
#
# Measuring at DC does not work and produces confidently wrong numbers. A gain
# sweep done that way reported 24.8 dB at gain 0 falling to 5.7 dB at gain 40,
# apparently a 17 dB win for turning gain down. It was the DC spike the whole
# time: measured properly, off DC, the carrier is 0.6 dB at gain 0 and 1.8 dB at
# gain 40 — the opposite conclusion. op25 avoids the same trap with `-o 25000`.
OFFSET_HZ = 25_000


def load() -> ctypes.CDLL:
    lib = ctypes.CDLL(ctypes.util.find_library('rtlsdr') or 'librtlsdr.so.0')
    lib.rtlsdr_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32]
    lib.rtlsdr_close.argtypes = [ctypes.c_void_p]
    lib.rtlsdr_set_center_freq.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.rtlsdr_set_sample_rate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.rtlsdr_set_tuner_gain_mode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.rtlsdr_set_tuner_gain.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.rtlsdr_set_agc_mode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.rtlsdr_set_tuner_bandwidth.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.rtlsdr_get_tuner_gains.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    lib.rtlsdr_reset_buffer.argtypes = [ctypes.c_void_p]
    lib.rtlsdr_read_sync.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    lib.rtlsdr_set_freq_correction.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return lib


def capture(lib, dev, secs: float) -> np.ndarray | None:
    lib.rtlsdr_reset_buffer(dev)
    want = int(SAMPLE_RATE * 2 * secs) & ~0x3FFF
    buf = (ctypes.c_ubyte * want)()
    got = ctypes.c_int(0)
    if lib.rtlsdr_read_sync(dev, buf, want, ctypes.byref(got)) != 0 or got.value < 8192:
        return None
    raw = np.frombuffer(bytes(buf[:got.value]), dtype=np.uint8).astype(np.float32)
    return (raw[0::2] - 127.5) + 1j * (raw[1::2] - 127.5)


def analyse(iq: np.ndarray, rate: int, channel_hz: float = 12_500.0):
    """Return (signal_db, floor_db, snr_db, clip_pct)."""
    nfft = 4096
    nseg = len(iq) // nfft
    win = np.hanning(nfft)
    acc = np.zeros(nfft)
    for i in range(nseg):
        acc += np.abs(np.fft.fftshift(np.fft.fft(iq[i * nfft:(i + 1) * nfft] * win))) ** 2
    psd = 10 * np.log10(acc / max(nseg, 1) + 1e-20)

    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / rate))
    # Carrier at +OFFSET_HZ, DC spike excluded — see the OFFSET_HZ note above.
    sig = psd[np.abs(freqs - OFFSET_HZ) <= channel_hz / 2].max()
    floor = float(np.median(psd[(np.abs(freqs) > OFFSET_HZ + channel_hz * 3)
                                & (np.abs(freqs) < rate / 4)]))

    # An 8-bit ADC saturates at +/-127.5. Sustained clipping means the front end
    # is being driven past what the converter can represent.
    clip = float(np.mean(np.abs(iq.real) > 125) + np.mean(np.abs(iq.imag) > 125)) * 50
    return float(sig), floor, float(sig - floor), clip


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-d', '--device', type=int, default=1)
    ap.add_argument('-f', '--freq', type=int, default=DEFAULT_CC)
    ap.add_argument('--secs', type=float, default=2.0)
    ap.add_argument('--bw', type=int, default=300_000,
                    help='tuner IF bandwidth; 0 = auto (default 300000, the best measured)')
    a = ap.parse_args()

    lib = load()
    dev = ctypes.c_void_p()
    if lib.rtlsdr_open(ctypes.byref(dev), a.device) != 0:
        print(f'cannot open device {a.device} — in use?', file=sys.stderr)
        return 1

    try:
        lib.rtlsdr_set_sample_rate(dev, SAMPLE_RATE)
        lib.rtlsdr_set_freq_correction(dev, 0)
        lib.rtlsdr_set_tuner_bandwidth(dev, a.bw)
        lib.rtlsdr_set_center_freq(dev, a.freq - OFFSET_HZ)   # carrier -> +OFFSET_HZ

        n = lib.rtlsdr_get_tuner_gains(dev, None)
        arr = (ctypes.c_int * n)()
        lib.rtlsdr_get_tuner_gains(dev, arr)
        gains = list(arr)

        print(f'dongle {a.device}  {a.freq / 1e6:.5f} MHz  tuner BW '
              f'{"auto" if a.bw == 0 else f"{a.bw // 1000} kHz"}  {a.secs}s per step')
        print(f'{"gain":>7} {"signal":>9} {"floor":>9} {"SNR":>8} {"clip":>7}')

        lib.rtlsdr_set_agc_mode(dev, 0)
        lib.rtlsdr_set_tuner_gain_mode(dev, 1)

        best = (-999.0, None)
        for g in gains:
            lib.rtlsdr_set_tuner_gain(dev, g)
            iq = capture(lib, dev, a.secs)
            if iq is None:
                continue
            sig, floor, snr, clip = analyse(iq, SAMPLE_RATE)
            flag = ''
            if clip > 1.0:
                flag = '  CLIPPING'
            if snr > best[0]:
                best = (snr, g / 10.0)
            print(f'{g / 10:6.1f}  {sig:8.1f}  {floor:8.1f}  {snr:7.1f} {clip:6.2f}%{flag}')

        # The RTL2832U's own AGC, which nothing in this project has tried.
        lib.rtlsdr_set_tuner_gain_mode(dev, 0)
        lib.rtlsdr_set_agc_mode(dev, 1)
        iq = capture(lib, dev, a.secs)
        if iq is not None:
            sig, floor, snr, clip = analyse(iq, SAMPLE_RATE)
            print(f'{"AGC":>6}  {sig:8.1f}  {floor:8.1f}  {snr:7.1f} {clip:6.2f}%')
            if snr > best[0]:
                best = (snr, 'AGC')
    finally:
        lib.rtlsdr_set_agc_mode(dev, 0)
        lib.rtlsdr_set_tuner_bandwidth(dev, 0)
        lib.rtlsdr_close(dev)

    print()
    print(f'  best {best[0]:.1f} dB at gain {best[1]}')
    print('  If the floor rises with gain while SNR flattens, the front end is')
    print('  overloading and less gain is better — the same trap README section 4')
    print('  gotcha 2 documents on the HackRF.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
