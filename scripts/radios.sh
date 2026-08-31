# Radio identities. Source this; do not execute it.
#
# WHY SERIALS AND NOT `soapy=0`:
#
# On 2026-08-31 a second HackRF (a HackRF One) was plugged in and enumerated as
# SoapySDR index 0, AHEAD of the Pro. Every script here used
# `--args soapy=0,driver=hackrf`, so they all silently began opening the One
# while still applying the Pro's gain settings — and every SNR baseline in this
# repository was measured on the Pro. Enumeration order is not stable across
# replugs either, so an index is never a safe way to name a radio.
#
# The serial must be the full 32-character zero-padded form SoapySDR reports
# (`SoapySDRUtil --find="driver=hackrf"`), not the 16-character form printed on
# the board or by `hackrf_info`'s "Serial number" line tail.
#
# `soapy=0` stays in the args string: that is gr-osmosdr's own source index,
# unrelated to which device is selected. `driver` and `serial` are passed
# through to SoapySDR. Verified working — osmosdr.source() logs
# `Opening HackRF Pro #1 977c…` / `Opening HackRF One #0 930c…` respectively.

HRF_PRO_SERIAL=0000000000000000977c64de2d717413   # HackRF Pro r1.2, fw 2026.01.3
HRF_ONE_SERIAL=0000000000000000930c64dc275e54c3   # HackRF One r10,  fw v2.2.0

HRF_PRO_ARGS="soapy=0,driver=hackrf,serial=$HRF_PRO_SERIAL"
HRF_ONE_ARGS="soapy=0,driver=hackrf,serial=$HRF_ONE_SERIAL"

# Gains are PER RADIO and are not interchangeable. The One's input runs 13.7 dB
# hotter than the Pro's: measured at 8 Msps, VGA:44 clips 0.64% of samples on
# the One, while VGA:14 drops the 773.05625 control channel to 12.7 dB — below
# op25's ~15 dB threshold. VGA:20 gives 21.4 dB with 0.0000% clipping.
# See docs/2026-08-31-wideband-multichannel.md section 10.3.
HRF_PRO_GAINS="AMP:0,LNA:40,VGA:44"
HRF_ONE_GAINS="AMP:0,LNA:40,VGA:20"
