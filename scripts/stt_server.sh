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

# Is $NAME now a container docker compose owns (docker-compose.yml's "whisper"
# service, container_name rtl-stt-server)?
#
# This script and compose became TWO lifecycle owners of the same container
# the moment Task 3 migrated whisper under compose. That is the hazard, not
# merely a duplicated restart: cmd_stop's `docker rm -f` would delete the
# compose-managed container outright, and cmd_start's `docker run` below
# carries no `--network` at all, so a replacement lands on the default bridge
# instead of `rtl-console_default` — the network `http://whisper:8081`
# resolves on for both stt-watch and web. `docker compose ps` then would not
# even list the replacement, so `stack.sh status` reports whisper MISSING
# while a working whisper is in fact running on the wrong network, and
# transcription stops dead (stt-watch runs --no-cpu-fallback).
#
# Distinguish "no such container" (permit — this script is still the right
# tool when compose is not running) from every OTHER way `docker inspect` can
# fail (permission issue, daemon busy, a race with something else touching
# the container). The two must not be conflated: falling through to
# `docker rm -f` / `docker run` on "can't tell" is the same
# failure-reads-as-a-healthy-negative shape as the other three
# containerization bugs already found on this branch. So an inspect we can't
# interpret is treated as "yes, managed" and refused, not as "no, proceed".
container_exists() {
  [ -n "$(docker ps -aq -f "name=^${NAME}$" 2>/dev/null)" ]
}

is_compose_managed() {
  container_exists || return 1
  local project
  if ! project="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$NAME" 2>/dev/null)"; then
    return 0
  fi
  [ -n "$project" ]
}

refuse_if_compose_managed() {
  if is_compose_managed; then
    die "$NAME is managed by docker compose now (docker-compose.yml's 'whisper' service) —" \
        "this script is a second lifecycle owner of the same container: cmd_stop's" \
        "'docker rm -f' would delete the compose-managed container outright, and" \
        "cmd_start's 'docker run' carries no --network, landing a replacement off" \
        "the compose network instead of back on it." \
        "Use instead: ./scripts/stack.sh restart whisper" \
        "(or, to genuinely stop it: docker compose stop whisper)"
  fi
}

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
  # Checked FIRST, before the binary/model checks below: those are cheap reads
  # that tell the operator nothing wrong is about to happen, right before
  # `docker rm -f` (a few lines down) would delete a container compose owns.
  refuse_if_compose_managed
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
  #
  # --init: without it, whisper-server runs as PID 1 of the container's PID
  # namespace, and the kernel gives PID 1 special signal immunity — an
  # unhandled SIGTERM/SIGSTOP/etc. sent to it from inside that namespace is
  # silently discarded unless the process installed a handler (man 7
  # pid_namespaces). whisper-server installs none. That immunity is exactly
  # what turned a wedged server into a 26-hour outage: `docker restart`
  # couldn't stop it either ("PID ... is zombie and can not be killed"), and
  # `--restart unless-stopped` never fired because the container never exited.
  # tini (via --init) becomes PID 1 instead, forwards signals properly, and
  # reaps zombies, so whisper-server (now PID 2) is an ordinary process again.
  docker run -d --name "$NAME" --restart unless-stopped --gpus all --init \
    -v "$BINDIR:/opt/whisper/bin:ro" \
    -v "$R/models:/models:ro" \
    -p "127.0.0.1:$PORT:$PORT" \
    -e STT_PORT="$PORT" \
    "$IMAGE" \
    /opt/whisper/bin/whisper-server -m "/models/$MODEL" \
      --port "$PORT" --host 0.0.0.0 --language en >/dev/null
  echo "stt_server: started $NAME on 127.0.0.1:$PORT ($MODEL)"
}

cmd_stop()   { refuse_if_compose_managed; docker rm -f "$NAME" >/dev/null 2>&1 && echo "stt_server: stopped" || echo "stt_server: not running"; }
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
