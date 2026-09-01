#!/usr/bin/env bash
# Shard the 40-bit ADP key search across every GPU on this host.
#
# One process per GPU via CUDA_VISIBLE_DEVICES, each taking an equal slice of
# the keyspace, all sharing one --found-file so the first hit stops the rest.
#
# Measured on wopr (10x RTX PRO 6000 Blackwell Server): 83.4M keys/s per GPU,
# so a full 2^40 pass is ~3.7 h on one GPU and ~22 min across ten. Combined with
# --pairs, one pass covers a whole superframe's 18 codewords rather than one.
#
# Usage:
#   adp_brute_multigpu.sh <mi_hex> <pt_hex> --pairs FILE [--gpus N] [--count-per-shard M]
#   adp_brute_multigpu.sh <mi_hex> <pt_hex> --frame ldu2 --position 5 --ct <ct_hex>
set -euo pipefail

BIN="${ADP_BIN:-$HOME/wopr_adp/adp_brute_cuda}"
[ -x "$BIN" ] || BIN="$(dirname "$0")/../adp_brute_cuda"
[ -x "$BIN" ] || { echo "no adp_brute_cuda found (set ADP_BIN)"; exit 1; }

MI="${1:?usage: $0 <mi_hex> <pt_hex> [options]}"; shift
PT="${1:?usage: $0 <mi_hex> <pt_hex> [options]}"; shift

GPUS=$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)
COUNT_PER_SHARD=""
CT="00 00 00 00 00 00 00 00 00 00 00"
PASSTHRU=()
while [ $# -gt 0 ]; do
    case "$1" in
        --gpus)             GPUS="$2"; shift 2 ;;
        --count-per-shard)  COUNT_PER_SHARD="$2"; shift 2 ;;
        --ct)               CT="$2"; shift 2 ;;
        *)                  PASSTHRU+=("$1"); shift ;;
    esac
done

TOTAL=$((1 << 40))
SLICE=$(( TOTAL / GPUS ))
RUNDIR="${RUNDIR:-$HOME/wopr_adp/run_$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$RUNDIR"
FOUND="$RUNDIR/found.key"

echo "sharding 2^40 across $GPUS GPU(s), slice=$SLICE, run dir $RUNDIR"
for g in $(seq 0 $((GPUS - 1))); do
    START=$(( g * SLICE ))
    # The last shard absorbs the remainder from integer division, so no key
    # index is ever skipped.
    if [ "$g" -eq $((GPUS - 1)) ]; then CNT=$(( TOTAL - START )); else CNT=$SLICE; fi
    [ -n "$COUNT_PER_SHARD" ] && CNT=$COUNT_PER_SHARD
    CUDA_VISIBLE_DEVICES=$g nohup "$BIN" "$MI" "$CT" "$PT" 1024 \
        --start "$START" --count "$CNT" --found-file "$FOUND" "${PASSTHRU[@]}" \
        > "$RUNDIR/gpu$g.log" 2>&1 &
    echo "  gpu$g: [$START .. $((START + CNT)))  pid $!"
done

wait
echo "all shards finished"
if [ -s "$FOUND" ]; then
    echo -n "KEY: "; xxd -p "$FOUND"
    grep -h "MATCHED CANDIDATE" "$RUNDIR"/gpu*.log 2>/dev/null | head -1 || true
else
    echo "no key found in the searched range"
fi
