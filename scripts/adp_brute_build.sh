#!/usr/bin/env bash
# Build + benchmark the CUDA ADP/RC4 brute-forcer.
# Prereqs: a CUDA 13.1 toolkit at /usr/local/cuda and an NVIDIA GPU.
# Note: this box's glibc 2.42 conflicts with the CUDA CRT's rsqrt/rsqrtf
# noexcept spec; the CRT header at
#   /usr/local/cuda/targets/x86_64-linux/include/crt/math_functions.h
# was patched in place (lines 629 & 653) to add `noexcept` to those two
# declarations. A backup is at $CLAUDE_JOB_DIR/tmp/math_functions.h.bak.
set -euo pipefail
cd "$(dirname "$0")"

NVCC=/usr/local/cuda/bin/nvcc
$NVCC -O3 -arch=sm_120 -x cu adp_brute.cu -o adp_brute_cuda
echo "built ./adp_brute_cuda"
