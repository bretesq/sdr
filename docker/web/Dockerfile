# node:24-slim is Debian-slim and ships without procps, so it has no `pgrep`
# or `pkill` binaries. server/utils/processes.ts shells out to both
# (execFileSync) for isRadioBusy() and for releasing a stranded radio, and
# relies on `pid: host` in compose to let those binaries see and signal the
# host's op25/udp_audio_record processes.
#
# Without procps, execFileSync('pgrep', ...) throws ENOENT — and
# isRadioBusy()'s `catch { return false }` (there to treat pgrep's normal
# "no match" exit 1 as "not busy") swallows that ENOENT identically. The
# container would then report the radio as free even while a capture is
# running live, silently, with no error anywhere. That is the wrong failure
# mode for the exact process pid: host exists to support, and it undermines
# Task 4's status view, which is built on isRadioBusy() reporting the truth.
#
# Empty build context (./docker/web) on purpose: a context of the repo root
# would tar up lwin_keys.json (live ADP key material) even though nothing
# here COPYs it, and that file must never enter a build context.
FROM node:24-slim
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends procps \
 && rm -rf /var/lib/apt/lists/*
