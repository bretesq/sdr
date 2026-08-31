#!/usr/bin/env python3
"""Measure control-channel SNR on a receiver, and say whether P25 will decode.

WHY
---
README section 7 concludes that a dedicated control-channel receiver "is not
possible here", on the strength of both RTL-SDRs measuring +2.7 dB against the
~15 dB P25 needs. That measurement is real — this script reproduces it — but the
conclusion does not follow from it, because section 7 also notes:

    "Their antennas were never swapped (only the HackRF's was)."

So the number describes the stock antenna that shipped with a $10 generic DVB-T
dongle, not the receiver. For comparison, on the same channel:

    HackRF + swapped antenna    +28.3 dB
    RTL-SDR + stock antenna      +2.5 dB      (measured, reproduces section 7)

An R820T is not 26 dB worse than a HackRF. Its noise figure is around 3.5 dB
against the HackRF's ~8, so on raw sensitivity the dongle should be comparable
or better. The gap is the antenna, and that is the one variable never tested.

This matters because section 7 also measures the prize: a radio that follows
calls misses the grants issued while it is away, and a 6-minute control-channel
capture saw 33 talkgroups against the 9 seen while recording audio.

USE
---
Run it once now for a baseline, swap the good antenna onto the dongle, run it
again. If the second number clears about +15 dB, the dedicated control-channel
receiver is viable and section 7's conclusion should be revised.

    python3 scripts/cc_snr.py                  # default: dongle 0 and 1
    python3 scripts/cc_snr.py -d 1 -i 30
    python3 scripts/cc_snr.py -f 769.19434e6   # site 50's control channel
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import subprocess
import sys
import tempfile

# Baton Rouge Simulcast (RFSS 1 site 13) control channel.
DEFAULT_CC = 773_056_250

# Roughly where P25 C4FM starts decoding reliably. Below this op25 will not lock.
P25_THRESHOLD_DB = 15.0

# Measured on this system for reference, README sections 1 and 7.
HACKRF_GOOD_ANTENNA_DB = 28.3

# Per-dongle clock error. Dongle 0 is +24.6 ppm, which at 773 MHz is ~19 kHz —
# wider than a whole 12.5 kHz P25 channel, so it must be corrected or the
# receiver is tuned outside the channel entirely.
DEFAULT_PPM = {0: 25, 1: 0}


def measure(device: int, centre: float, ppm: int, integration: int,
            gain: float, span: float) -> tuple[float, float, float] | None:
    """Return (snr_db, peak_db, floor_db), or None if the sweep produced nothing."""
    lo, hi = centre - span / 2, centre + span / 2
    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
        path = tmp.name
    try:
        subprocess.run(
            ['rtl_power', '-d', str(device), '-p', str(ppm),
             '-f', f'{lo:.0f}:{hi:.0f}:2k', '-g', str(gain),
             '-i', str(integration), '-1', path],
            capture_output=True, timeout=integration + 30, check=False)

        best = None
        with open(path) as f:
            for row in csv.reader(f):
                if len(row) < 7:
                    continue
                start, step = float(row[2]), float(row[4])
                vals = [float(v) for v in row[6:] if v.strip()]
                if not vals:
                    continue
                floor = statistics.median(vals)
                idx = int((centre - start) / step)
                if not 0 <= idx < len(vals):
                    continue
                # A few bins either side: the channel is 12.5 kHz and any
                # residual clock error shifts the peak slightly.
                peak = max(vals[max(0, idx - 3):idx + 4])
                snr = peak - floor
                if best is None or snr > best[0]:
                    best = (snr, peak, floor)
        return best
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-d', '--device', type=int, action='append',
                    help='dongle index; repeatable. Default: 0 and 1')
    ap.add_argument('-f', '--freq', type=float, default=DEFAULT_CC,
                    help=f'centre frequency in Hz (default {DEFAULT_CC})')
    ap.add_argument('-i', '--integration', type=int, default=20, help='seconds per sweep')
    ap.add_argument('-g', '--gain', type=float, default=40.0)
    ap.add_argument('-p', '--ppm', type=int, help='override the per-dongle default')
    ap.add_argument('--span', type=float, default=250e3, help='sweep width in Hz')
    a = ap.parse_args()

    devices = a.device or [0, 1]

    print(f'control channel {a.freq / 1e6:.5f} MHz   '
          f'{a.integration}s per sweep, gain {a.gain}')
    print(f'{"":10} {"peak":>9} {"floor":>9} {"SNR":>8}   verdict')

    results = {}
    for d in devices:
        ppm = a.ppm if a.ppm is not None else DEFAULT_PPM.get(d, 0)
        r = measure(d, a.freq, ppm, a.integration, a.gain, a.span)
        if r is None:
            print(f'  dongle {d}   (no data — is the device present and free?)')
            continue
        snr, peak, floor = r
        results[d] = snr
        verdict = 'P25 should decode' if snr >= P25_THRESHOLD_DB else \
                  f'{P25_THRESHOLD_DB - snr:.0f} dB short'
        print(f'  dongle {d}  {peak:8.2f}  {floor:8.2f}  {snr:+7.1f}   {verdict}'
              f'   (ppm {ppm:+d})')

    if results:
        best = max(results.values())
        print()
        print(f'  best {best:+.1f} dB against ~{P25_THRESHOLD_DB:.0f} dB needed; '
              f'HackRF with the good antenna measured {HACKRF_GOOD_ANTENNA_DB:+.1f} dB '
              f'on this channel')
        if best < P25_THRESHOLD_DB:
            print(f'  short by {P25_THRESHOLD_DB - best:.0f} dB. Before concluding the '
                  f'receiver is at fault, note the gap to the HackRF is '
                  f'{HACKRF_GOOD_ANTENNA_DB - best:.0f} dB, which is far more than the '
                  f'~5 dB the two front ends differ by — so it is the antenna. '
                  f'Swap the good one onto this dongle and re-run.')
        else:
            print('  viable: park this dongle on the control channel and let the '
                  'HackRF follow voice. README section 7 estimates that recovers '
                  'the ~60% of calls a single retuning radio misses.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
