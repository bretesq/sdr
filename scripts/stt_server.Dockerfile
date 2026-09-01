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
