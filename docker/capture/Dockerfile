# op25 needs GNU Radio, and GNU Radio's Python modules are ABI-tied to both the
# gnuradio build and the Python minor version. ubuntu:26.04 is not a stylistic
# choice: it is the host's own distribution, and it ships gnuradio 3.10.12.0-6
# with Python 3.14 -- byte-identical to what the host's op25 was compiled
# against. Verified before this plan was written: a stock ubuntu:26.04 with
# these packages imports the host's bind-mounted op25_repeater successfully.
# Changing this base is very likely to break that import.
FROM ubuntu:26.04

# gnuradio + gr-osmosdr: the SDR runtime op25 links against.
# hackrf: hackrf_info, used to prove the devices are visible.
# util-linux: lwin_listen_multi.sh wraps op25 in `script -q -f -c` for its log.
#
# soapysdr-module-hackrf is the one that is easy to miss, and its absence is
# silent until the moment a capture actually starts. gr-osmosdr does not talk to
# libhackrf directly: it goes through SoapySDR, which loads a per-radio driver
# PLUGIN at runtime. --no-install-recommends pulls in libsoapysdr0.8 (the
# library) but none of those plugins, so SoapySDR starts up fine and simply
# matches no devices.
#
# The failure this caused looked nothing like a missing package: `hackrf_info`
# listed both HackRFs by serial (it uses libhackrf directly and never consults
# SoapySDR), `from gnuradio import op25_repeater` imported cleanly, and the
# container passed every hardware check we had -- then op25 died at startup with
# `RuntimeError: SoapySDR::Device::make() no match`. Verified against the host,
# which carries soapysdr-module-hackrf while the image carried only
# libsoapysdr0.8.
#
# soapysdr-tools supplies SoapySDRUtil, so `SoapySDRUtil --find` can prove
# device enumeration works WITHOUT starting a capture -- the check that would
# have caught this before the cutover.
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      gnuradio gr-osmosdr hackrf soapysdr-module-hackrf soapysdr-tools \
      python3 util-linux procps \
 && rm -rf /var/lib/apt/lists/*

# op25 itself is NOT baked in. The host's build is bind-mounted read-only at
# runtime, the same way stt_server.Dockerfile mounts the whisper binaries --
# so rebuilding or re-patching op25 does not mean rebuilding this image.
# op25's shared libraries arrive at runtime via that bind mount, so they
# cannot be in the image's ld.so.cache -- that cache is built at image build
# time, before the mount exists. Rebuilding it with ldconfig would fix the
# lookup, but ldconfig needs root and this container runs as UID 1000 by
# design, so instead the loader is pointed at the mount directly. Mirrors
# stt_server.Dockerfile's `ENV LD_LIBRARY_PATH=/opt/whisper/bin`, which mounts
# its binaries the same way for the same reason.
ENV LD_LIBRARY_PATH=/usr/local/lib/x86_64-linux-gnu

WORKDIR /home/besquivel/rtl
