# Dockerising the console — whisper, stt-watch, web

**Date:** 2026-09-02
**Status:** Design approved, ready for implementation planning

## Problem

Two services died silently today and neither failure was visible until transcripts
stopped appearing in the UI.

`rtl-stt-server` failed twice. The first time it became a zombie: alive, accepting TCP,
answering `GET /` in 0.4 ms, and hanging every inference forever — Docker could not even
restart it (`PID … is zombie and can not be killed`). The second time it exited with code
128. It already runs with `--restart unless-stopped`, which fires only on exit, so the
zombie was invisible to it.

`stt_watch.py` died with the capture session that spawned it, and a later capture launched
without `--stt` never started a replacement. Nothing supervised it.

Meanwhile `stt_backend`'s CPU fallback masked the first outage: transcription kept
trickling at 123 s per clip instead of stopping, so coverage decayed from 98% to 55% while
the system looked like it was working.

## Goal

Reliability first, then reproducibility. Make the services supervised and their health
observable; then have the stack declared in files rather than in shell history.

## What is containerised, and what is not

**Containers:** `whisper`, `stt-watch`, `web`.

**Stays on the host:** op25 `multi_rx.py`, the eight `udp_audio_record.py` recorders, and
the HackRFs. These need USB device access and real-time scheduling, and the host already
carries 34 gnuradio/osmosdr packages. Duplicating that toolchain into an image would
create a second SDR install to keep in sync with the first, for no gain.

```
HOST                                    CONTAINERS
  op25 multi_rx  ──audio──▶ recorders     whisper    GPU, healthcheck POSTs
  HackRF One + Pro (USB)      │                      silence.wav to /inference
                              ▼
                          recordings/ ◀── stt-watch  polls, transcribes,
                          sdr.db      ◀──            writes transcripts
                              ▲
                              └───────────  web      nuxt dev, HMR, pid: host
```

## Services

| service | image | purpose |
|---|---|---|
| `whisper` | `rtl-whisper-cuda` (existing `scripts/stt_server.Dockerfile`) | CUDA whisper-server on :8081 |
| `stt-watch` | `python:3.12-slim` | watches `recordings/`, transcribes, writes to `sdr.db` |
| `web` | `node:24-slim` | `nuxt dev` with HMR on :3002 |

All three run as `1000:1000` and bind-mount the repository at `/home/besquivel/rtl`.
All three take `restart: unless-stopped`.

`stt_watch.py` needs no pip dependencies — it imports `stt_backend` and `sdr_db`, which use
only `urllib` and `sqlite3` from the standard library. `python:3.12-slim` plus the
bind-mounted repo is the whole runtime.

`node:24-slim` matches the host's Node 24.15.0. pnpm comes from corepack.

## The three decisions that carry the design

### The repo mounts at its real path

The bind mount target is `/home/besquivel/rtl`, not `/app` or `/workspace`. `sdrRoot()`
returns that literal path (`server/utils/paths.ts`), and every script builds absolute paths
from it. Mounting anywhere else does not fail loudly — it resolves to paths that do not
exist, and the console reports an empty corpus rather than an error.

### Every container runs as UID 1000

SQLite creates `sdr.db-wal` and `sdr.db-shm` beside the database. If a root container
creates them, the host's recorders — running as the operator — can no longer write, and
recording stops. This is the same failure class as the root-owned stray file encountered
earlier, but with a worse blast radius.

Consequence, accepted deliberately: a UID-1000 container cannot `nsenter` into the host's
namespaces, because that needs `CAP_SYS_ADMIN`.

- `pgrep` and `pkill` still work, via `pid: host`. `isRadioBusy()` and releasing the radio
  are unaffected.
- **Starting a capture from the UI does not work in the container.** `/api/listen/start`
  returns a stated error directing the operator to the shell, rather than spawning op25
  into a container with no SDR stack. In practice every capture this project has run was
  launched from a shell; the privileged alternative was rejected because a mistake in that
  path stops recording.

### The healthcheck must transcribe, not ping

`GET /` returns 200 in 0.4 ms on a wedged whisper-server. Only exercising real inference
distinguishes a working server from a zombie.

`scripts/silence.wav` (32 KB) is copied into the image at build time and POSTed to
`/inference` by the healthcheck. Requiring a response is the entire difference between a
restart policy that recovers the system and one that watched a corpse for 26 hours.

Parameters: `interval: 60s`, `timeout: 30s`, `retries: 2`, `start_period: 90s`. The start
period covers model load; the timeout is generous against a busy GPU shared with vLLM, and
still an order of magnitude below the 123 s that indicates the CPU fallback.

## Deliberate removal: no CPU fallback in the container

The `stt-watch` container will not carry the `whisper-cli` CPU binary, so
`transcribe_via_cli` cannot run there.

That fallback is what hid the first outage. Transcription did not stop; it degraded to
123 s per clip and fell steadily behind, which reads as "slow" rather than "broken". Loud
failure is better here: without the binary, a whisper outage stops transcription outright,
the healthcheck restarts whisper, and `stt-watch` resumes on its own.

The host copy of `stt_watch.py` keeps its fallback. This choice is scoped to the container.

## Failure surfacing

`scripts/stack.sh` with `up`, `down`, and `status`. `status` prints the containers and
their health **and** the host half — op25, the recorder count, the newest call, and
transcript coverage — in one view.

Half this system stays on the host by design, so a status command that reports only
containers recreates the exact gap that let `stt_watch` die unnoticed.

## Code changes

- `server/utils/processes.ts` — `startListening()` returns a stated error when
  `SDR_IN_CONTAINER=1` is set, instead of spawning.
- `scripts/stt_server.Dockerfile` — `COPY silence.wav`, and the `HEALTHCHECK` its own
  comment already promises but never declared.
- `docker-compose.yml` — new, three services.
- `scripts/stack.sh` — new.

`scripts/stt_server.sh` keeps working unchanged for anyone starting whisper directly.

## Testing

- Existing 113 Vitest tests must stay green.
- One new test for the container-mode error path in `startListening()`. It lives in
  `server/utils`, which Vitest already collects.
- Manual, and the check that matters: kill whisper mid-run, confirm the healthcheck marks
  it unhealthy and the restart policy brings it back, and confirm `stt-watch` resumes with
  no intervention. Then confirm transcript coverage climbs on its own.

## Success criteria

- A wedged (not exited) whisper-server is detected and restarted without intervention.
- `stt_watch` survives a capture session ending, and starting a capture without `--stt`.
- `scripts/stack.sh status` answers "what is down?" for both halves in one command.
- The console still reads `sdr.db` and `recordings/` correctly, and live listening and the
  archive work unchanged.
- No container writes a root-owned file anywhere the host recorders need to write.
