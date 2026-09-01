#!/usr/bin/env bash
# Lifecycle for the persistent CUDA whisper-server that stt_watch.py talks to.
#
# WHY PERSISTENT
# --------------
# stt_watch.py used to exec whisper-cli once per .wav. Measured on this corpus
# (50 real clips, median 2.5 s, ggml-small.en):
#
#                        spawn per clip     persistent server
#   CPU (16 threads)      1.081 s/clip        0.685 s/clip
#   GPU (sm_120)         ~0.970 s/clip        0.133 s/clip
#
# The GPU encoder is 6.8x faster (399 ms -> 59 ms on a 2.34 s clip) but each
# fresh process spends ~690 ms uploading weights to VRAM, which cancels the win
# exactly. Spawn-per-clip on the GPU is no faster than CPU. The speedup only
# exists behind a long-lived process, hence this server.
#
# Usage:
#   stt_server.sh build            build the CUDA binaries (needs ~2 min)
#   stt_server.sh start|stop|restart|status
#
# Env: STT_PORT (8081), STT_MODEL (ggml-medium.en.bin), STT_IMAGE, STT_NAME
set -euo pipefail

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="${STT_NAME:-rtl-stt-server}"
IMAGE="${STT_IMAGE:-rtl-whisper-cuda}"
PORT="${STT_PORT:-8081}"
MODEL="${STT_MODEL:-ggml-medium.en.bin}"
BINDIR="$R/tools/whisper.cpp/build-cuda/bin"
# Blackwell (RTX PRO 6000 / GB202) is compute capability 12.0. Building without
# this emits kernels the card cannot run.
ARCH="${STT_CUDA_ARCH:-120}"
BUILD_IMAGE="nvidia/cuda:13.0.2-devel-ubuntu24.04"

die() { echo "stt_server: $*" >&2; exit 1; }

ensure_image() {
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "stt_server: building image $IMAGE"
    docker build -q -t "$IMAGE" -f "$R/scripts/stt_server.Dockerfile" "$R/scripts" >/dev/null
  fi
}

cmd_build() {
  # Runs cmake inside the devel image so the host needs no CUDA toolkit. Output
  # lands in build-cuda/ on the host and is bind-mounted at serve time.
  docker run --rm --gpus all -v "$R:/w" -w /w/tools/whisper.cpp "$BUILD_IMAGE" bash -c '
    set -e
    apt-get update -qq && apt-get install -y -qq cmake build-essential >/dev/null
    cmake -B build-cuda -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES='"$ARCH"' \
          -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF
    cmake --build build-cuda -j "$(nproc)" --target whisper-cli whisper-server
  '
  echo "stt_server: built $BINDIR"
}

cmd_start() {
  [ -x "$BINDIR/whisper-server" ] || die "no whisper-server at $BINDIR — run: $0 build"
  [ -f "$R/models/$MODEL" ]       || die "no model at $R/models/$MODEL"
  if [ -n "$(docker ps -q -f "name=^${NAME}$")" ]; then
    echo "stt_server: already running"; return 0
  fi
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  ensure_image
  # recordings/ is deliberately NOT mounted: audio reaches the server as an HTTP
  # upload, so the container never touches the corpus. It therefore cannot leave
  # root-owned .txt files behind for the (non-root) watcher to trip over.
  docker run -d --name "$NAME" --restart unless-stopped --gpus all \
    -v "$BINDIR:/opt/whisper/bin:ro" \
    -v "$R/models:/models:ro" \
    -p "127.0.0.1:$PORT:$PORT" \
    "$IMAGE" \
    /opt/whisper/bin/whisper-server -m "/models/$MODEL" \
      --port "$PORT" --host 0.0.0.0 --language en >/dev/null
  echo "stt_server: started $NAME on 127.0.0.1:$PORT ($MODEL)"
}

cmd_stop()   { docker rm -f "$NAME" >/dev/null 2>&1 && echo "stt_server: stopped" || echo "stt_server: not running"; }
cmd_status() {
  if [ -n "$(docker ps -q -f "name=^${NAME}$")" ]; then
    printf 'stt_server: running  health='
    curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$PORT/" || echo unreachable
  else
    echo "stt_server: not running"
  fi
}

case "${1:-status}" in
  build)   cmd_build ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  *)       die "usage: $0 {build|start|stop|restart|status}" ;;
esac
