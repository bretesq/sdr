#!/usr/bin/env python3
"""Toggle each RTL2832U GPIO pin so you can see which one drives the LED.

BACKGROUND
----------
PatriotWaves documents "LED RED/BLUE designation for radio configuration" on
these units. On an RTL2832U there is no LED the driver lights by itself — an
indicator LED is wired to one of the demodulator's GPIO pins, and something has
to drive that pin. Stock librtlsdr never touches them except GPIO0, which
`rtl_biast` uses for bias-tee power on the boards that have one.

So an LED that "never turns on" is the expected behaviour under rtl_sdr, op25,
GQRX and anything else built on librtlsdr. It is not a fault. The vendor's
Windows tooling presumably sets the pin; nothing in this Linux stack does.

This walks GPIO0..GPIO5 on the chosen dongle, holding each high then low, so you
can watch the device and see which pin (if any) is wired to the LED. It uses
librtlsdr's own bias-tee GPIO call, which is a thin wrapper over
`rtlsdr_set_gpio_bit`.

    python3 scripts/led_probe.py            # dongle 0, GPIO 0-5
    python3 scripts/led_probe.py -d 1 -g 0  # just GPIO0 on dongle 1
    python3 scripts/led_probe.py --hold 5   # slower, easier to watch

SAFETY
------
Only pins 0-5 are touched, only as outputs, and every pin is returned low on
exit including on Ctrl-C. GPIO0 is the bias-tee line on boards that have one: if
this unit does, that pin will briefly put ~4.5 V on the antenna centre
conductor. Disconnect any powered LNA or receive-only antenna before running, or
skip GPIO0 with `-g 1 -g 2 -g 3 -g 4 -g 5`.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import sys
import time


def load_librtlsdr() -> ctypes.CDLL:
    path = ctypes.util.find_library('rtlsdr') or 'librtlsdr.so.0'
    lib = ctypes.CDLL(path)
    lib.rtlsdr_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32]
    lib.rtlsdr_close.argtypes = [ctypes.c_void_p]
    lib.rtlsdr_get_device_count.restype = ctypes.c_uint32
    # set_bias_tee_gpio(dev, gpio, on) — present in librtlsdr 0.6.0+.
    if hasattr(lib, 'rtlsdr_set_bias_tee_gpio'):
        lib.rtlsdr_set_bias_tee_gpio.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    return lib


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-d', '--device', type=int, default=0)
    ap.add_argument('-g', '--gpio', type=int, action='append',
                    help='GPIO pin to test; repeatable. Default: 0 through 5')
    ap.add_argument('--hold', type=float, default=3.0,
                    help='seconds to hold each pin high (default 3)')
    a = ap.parse_args()

    pins = a.gpio if a.gpio else list(range(6))

    lib = load_librtlsdr()
    if not hasattr(lib, 'rtlsdr_set_bias_tee_gpio'):
        print('This librtlsdr has no rtlsdr_set_bias_tee_gpio, so GPIO pins '
              'cannot be driven from here. Upgrade librtlsdr, or test the LED '
              'with the vendor\'s Windows tooling.', file=sys.stderr)
        return 1

    count = lib.rtlsdr_get_device_count()
    if a.device >= count:
        print(f'No device {a.device} (found {count})', file=sys.stderr)
        return 1

    dev = ctypes.c_void_p()
    if lib.rtlsdr_open(ctypes.byref(dev), a.device) != 0:
        print(f'Could not open device {a.device} — is something else using it?',
              file=sys.stderr)
        return 1

    print(f'dongle {a.device}: walking GPIO {pins}, {a.hold}s each.')
    print('Watch the device. Note which pin, if any, changes the LED.\n')
    try:
        for pin in pins:
            print(f'  GPIO{pin}  HIGH ... ', end='', flush=True)
            lib.rtlsdr_set_bias_tee_gpio(dev, pin, 1)
            time.sleep(a.hold)
            lib.rtlsdr_set_bias_tee_gpio(dev, pin, 0)
            print('low')
    except KeyboardInterrupt:
        print('\n  interrupted')
    finally:
        # Leave nothing energised, especially GPIO0 if it is a bias tee.
        for pin in pins:
            try:
                lib.rtlsdr_set_bias_tee_gpio(dev, pin, 0)
            except Exception:      # noqa: BLE001 - closing down, keep going
                pass
        lib.rtlsdr_close(dev)
        print('\nall pins returned low.')

    print('\nIf none of them lit it: nothing in the Linux SDR stack drives that')
    print('LED, which is expected. librtlsdr only ever touches GPIO0, and only')
    print('for bias-tee power. The LED is a vendor/Windows-side indicator, not')
    print('a sign that anything is wrong with the receiver.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
