#!/usr/bin/env bash
# Deploy adp_brute to wopr (384 cores), compile, and run the brute force there.
# Requires: ssh key auth to wopr (run this after `ssh-copy-id wopr`).
set -euo pipefail
R=/home/besquivel/rtl
REMOTE_DIR=wopr_adp
# Fully-VERIFIED known-pair from the multi-radio capture (2026-08-31). The 18:51:42
# call's recording (recordings/TG17169_17-BRPD-DSP3_20260831-185142.wav) is near-silence
# steady-state, so every LDU2 frame encodes to the same IMBE codeword 75 5f c0 ee f8 02
# 00 00 4e 77 3e — a real known-plaintext (NOT the pure-silence codeword 01 50 20 00...).
# The CIPHERTXT b2 71 7f fe... is LDU2 position 6 (6 ESS frames precede it) -> offset
# = 101 + 6*11 + 267 = 434 (op25_crypt_adp.cc). The previous failed runs used the
# silence PT and hardcoded offset 368; both were wrong for this call.
MI="dc 9f 34 eb 9a 6a 97 ec 00"
CT="b2 71 7f fe d3 e8 ee 70 ab 42 f4"
PT="75 5f c0 ee f8 02 00 00 4e 77 3e"
POSITION=6

NTHREADS="${1:-256}"

# ---------------------------------------------------------------------------
# Single-machine (full 2^40 on wopr) — default when invoked as `deploy_wopr.sh [nthreads]`
# ---------------------------------------------------------------------------
if [ -z "${SHARD:-}" ] || [ "${SHARD:-}" = "full" ]; then
    ssh wopr "mkdir -p ~/$REMOTE_DIR"
    scp "$R/scripts/adp_brute.cpp" wopr:~/$REMOTE_DIR/adp_brute.cpp
    ssh wopr "cd ~/$REMOTE_DIR && g++ -O3 -march=native -fopenmp -o adp_brute adp_brute.cpp"
    ssh wopr "cd ~/$REMOTE_DIR && nohup ./adp_brute \"$MI\" \"$CT\" \"$PT\" $NTHREADS --position $POSITION > adp_run.log 2>&1 &"
    echo "launched: $NTHREADS-thread ADP brute force on wopr; progress in ~/wopr_adp/adp_run.log"
    exit 0
fi

# ---------------------------------------------------------------------------
# Multi-machine sharding: SHARD=<i>/<N>
#   Splits the 2^40 space into N equal slices. Each machine runs its slice.
#   Requires a SHARED found-file so any machine that finds the key aborts the
#   others early. The found-file must live on a shared mount (NFS) visible to
#   all machines.
#
# Usage:
#   SHARD=0/4 SHARED_FOUND=/mnt/nfs/adp_found deploy_wopr.sh
#   SHARD=1/4 SHARED_FOUND=/mnt/nfs/adp_found deploy_wopr.sh
#   ...
#   SHARD=3/4 SHARED_FOUND=/mnt/nfs/adp_found deploy_wopr.sh
#
# NTHREADS controls per-machine threads (default 256).
# ---------------------------------------------------------------------------
SHARD_INDEX=${SHARD%%/*}
SHARD_COUNT=${SHARD##*/}
SLICE=$(( (1 << 40) / SHARD_COUNT ))
START=$(( SHARD_INDEX * SLICE ))
SHARED_FOUND="${SHARED_FOUND:-/mnt/nfs/adp_found}"

ssh wopr "mkdir -p ~/$REMOTE_DIR"
scp "$R/scripts/adp_brute.cpp" wopr:~/$REMOTE_DIR/adp_brute.cpp
ssh wopr "cd ~/$REMOTE_DIR && g++ -O3 -march=native -fopenmp -o adp_brute adp_brute.cpp"
ssh wopr "cd ~/$REMOTE_DIR && nohup ./adp_brute \"$MI\" \"$CT\" \"$PT\" $NTHREADS --start $START --count $SLICE --found-file $SHARED_FOUND > shard_${SHARD_INDEX}of${SHARD_COUNT}.log 2>&1 &"
echo "launched: shard $SHARD_INDEX of $SHARD_COUNT on wopr (start=$START, count=$SLICE), found-file=$SHARED_FOUND"
