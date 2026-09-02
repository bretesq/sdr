# Runtime image for the CUDA whisper-server.
#
# The binaries themselves are NOT baked in: they are built by
# `stt_server.sh build` into tools/whisper.cpp/build-cuda and bind-mounted, so
# rebuilding whisper.cpp does not mean rebuilding this image.
#
# WHY A CONTAINER AT ALL
# ----------------------
# Ubuntu 26.04's glibc 2.43 declares rsqrt/rsqrtf noexcept, which collides with
# CUDA 13.1's crt/math_functions.h:
#   error: exception specification is incompatible with that of previous
#          function "rsqrt"
# That is a header-level conflict, not a host-compiler version problem: g++-14
# and g++-15 both fail. Ubuntu 24.04 (glibc 2.39) compiles cleanly, so both the
# build and the runtime live in a container and the host needs no CUDA toolkit.
FROM nvidia/cuda:13.0.2-runtime-ubuntu24.04

# libgomp1: ggml-cpu is built with OpenMP even in the CUDA build, and the
# server refuses to start without it ("libgomp.so.1: cannot open shared object
# file"). curl: the HEALTHCHECK below.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

ENV LD_LIBRARY_PATH=/opt/whisper/bin

# The probe payload. Small, fixed, and already in the repo, so the healthcheck
# costs the GPU almost nothing and never depends on the corpus.
COPY silence.wav /opt/hc/silence.wav

ENV STT_PORT=8081

# A liveness probe has to exercise inference, not connectivity.
#
# A wedged whisper-server answers GET / in 0.4ms while hanging every
# transcription forever. That exact state ran here for 26 hours: `--restart
# unless-stopped` never fired because the container never exited, and Docker
# could not have restarted it anyway ("PID ... is zombie and can not be
# killed"). Only a probe that asks for real work can tell the two apart.
#
# 25s of curl inside a 30s timeout: a healthy GPU transcribes this clip in
# ~0.2s, and the CPU fallback path takes ~123s, so anything near the ceiling is
# already a fault. start-period covers model load on a cold start.
HEALTHCHECK --interval=60s --timeout=30s --retries=2 --start-period=90s \
  CMD curl -sf --max-time 25 -o /dev/null \
      -F file=@/opt/hc/silence.wav \
      -F response_format=json \
      "http://127.0.0.1:${STT_PORT}/inference" || exit 1
