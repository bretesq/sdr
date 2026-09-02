# Dockerising the console — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put whisper, the transcription watcher and the web app under Docker with supervision that can actually detect a wedged service, while op25 and the recorders stay on the host.

**Architecture:** Three compose services — `whisper`, `stt-watch`, `web` — all running as UID 1000 with the repository bind-mounted at its real path. The whisper image gains a healthcheck that transcribes a known clip rather than pinging, because a wedged server answers `GET /` instantly while hanging every inference. op25, the recorders and the HackRFs remain host processes.

**Tech Stack:** Docker Compose, `nvidia/cuda:13.0.2-runtime-ubuntu24.04` (existing whisper image), `python:3.12-slim`, `node:24-slim`, Nuxt 3, Vitest.

**Design spec:** `docs/superpowers/specs/2026-09-02-dockerize-design.md`

## Global Constraints

- Package manager is **pnpm**. Tests: `pnpm test`. Lint: `pnpm lint`. Both must pass before a task is complete. Current baseline: **113/113 Vitest, lint clean**.
- Vitest only collects `server/**/*.test.ts` and `utils/**/*.test.ts`. Do not put tests anywhere else.
- Never suppress type errors with `as any`, `@ts-ignore`, or `@ts-expect-error`. Never leave an empty catch block.
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`).
- This codebase's comment style explains *why*, at length, for non-obvious decisions. Match that density.
- **The repository bind-mounts at `/home/besquivel/rtl` — the literal host path.** `sdrRoot()` returns that string and every script builds absolute paths from it. Mounting at `/app` or `/workspace` does not fail loudly; it resolves to paths that do not exist and the console reports an empty corpus.
- **Every container runs as `1000:1000`.** SQLite creates `sdr.db-wal` and `sdr.db-shm` beside the database; if a root container creates them, the host's recorders can no longer write and recording stops.
- **Never write to any path outside the repository.** `sdr.db`, `recordings/` and `lwin_keys.json` are live operational state; `lwin_keys.json` is gitignored with no revert path.
- Do not stop, start or reconfigure the radio while implementing. A capture may be running.

---

### Task 1: Refuse capture start in the container

`/api/listen/start` spawns `bash lwin_listen_multi.sh`, which needs the HackRFs over USB and 34 gnuradio/osmosdr packages the web image does not carry. Reaching the host's namespaces would need `CAP_SYS_ADMIN`, which this container deliberately lacks. It must refuse clearly rather than spawn op25 into a container that cannot run it.

**Files:**
- Modify: `server/utils/processes.ts` (`startListening`, around line 207)
- Test: `server/utils/processes.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `inContainer(): boolean`, exported from `server/utils/processes.ts`. Task 3 sets the `SDR_IN_CONTAINER=1` environment variable that drives it.

- [ ] **Step 1: Write the failing test**

Append to `server/utils/processes.test.ts`:

```ts
describe('container mode', () => {
  /**
   * The web container can see host processes through `pid: host`, so reading
   * and releasing the radio still work. Only STARTING a capture is impossible:
   * op25 needs USB access to the HackRFs and a gnuradio stack the image does
   * not carry. Spawning anyway would fail deep inside bash with an error the
   * operator cannot act on.
   */
  it('refuses to start a capture and names the command to run instead', () => {
    process.env.SDR_IN_CONTAINER = '1'
    try {
      expect(inContainer()).toBe(true)
      expect(() => startListening({ preset: 'pd' })).toThrow(/container/i)
      expect(() => startListening({ preset: 'pd' })).toThrow(/lwin_listen_multi\.sh/)
    } finally {
      delete process.env.SDR_IN_CONTAINER
    }
  })

  it('reports host mode when the variable is absent', () => {
    delete process.env.SDR_IN_CONTAINER
    expect(inContainer()).toBe(false)
  })

  it('treats any value other than "1" as host mode', () => {
    // A stray empty or "0" value must not silently disable capture start.
    process.env.SDR_IN_CONTAINER = '0'
    try {
      expect(inContainer()).toBe(false)
    } finally {
      delete process.env.SDR_IN_CONTAINER
    }
  })
})
```

Add `inContainer` and `startListening` to the existing import at the top of the file:

```ts
import {
  buildListenArgs, countCalls, scriptFor, LAUNCHERS, inContainer, startListening,
} from './processes'
```

**Do not call `startListening` outside the container-mode branch.** On the host it really does spawn op25 and claim the radio.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm exec vitest run server/utils/processes.test.ts -t "container mode"`
Expected: FAIL — `inContainer is not a function`.

- [ ] **Step 3: Implement**

In `server/utils/processes.ts`, add above `startListening`:

```ts
/**
 * Is this process the containerised web app rather than the host one?
 *
 * Set by docker-compose. Checked explicitly against '1' so that an empty or '0'
 * value cannot silently disable capture start on a host run.
 */
export function inContainer(): boolean {
  return process.env.SDR_IN_CONTAINER === '1'
}
```

Then as the first statement inside `startListening`:

```ts
  // The container can watch the radio but cannot start it.
  //
  // op25 needs the HackRFs over USB and the 34 gnuradio/osmosdr packages the
  // web image does not carry, and entering the host's namespaces would need
  // CAP_SYS_ADMIN — which this container deliberately lacks, because running it
  // as root would let SQLite create root-owned sdr.db-wal files and stop the
  // host's recorders writing.
  //
  // `pgrep` and `pkill` still work through `pid: host`, so isRadioBusy() and
  // releasing the radio are unaffected. Only starting is refused, and it says
  // exactly what to run instead.
  if (inContainer()) {
    throw new Error(
      'Captures cannot be started from the container — op25 needs USB access to '
      + 'the HackRFs. Start one on the host instead, for example: '
      + './scripts/lwin_listen_multi.sh --ess --include-encrypted --pd 10800',
    )
  }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm exec vitest run server/utils/processes.test.ts`
Expected: PASS, including the pre-existing tests.

- [ ] **Step 5: Full suite and lint**

Run: `pnpm test && pnpm lint`
Expected: both clean, Vitest count up by 3 from 113.

- [ ] **Step 6: Commit**

```bash
git add server/utils/processes.ts server/utils/processes.test.ts
git commit -m "feat(api): refuse capture start in the container, and say what to run

The web container can see host processes through pid: host, so reading and
releasing the radio still work. Starting one cannot: op25 needs USB access to
the HackRFs and 34 gnuradio/osmosdr packages the image does not carry, and
reaching the host's namespaces would need CAP_SYS_ADMIN, which the container
deliberately lacks -- running it as root would let SQLite create root-owned
sdr.db-wal files and stop the host recorders writing.

Checked against '1' explicitly so a stray empty or '0' value cannot disable
capture start on a host run."
```

---

### Task 2: A healthcheck that transcribes

This is the fix the whole change exists for. `rtl-stt-server` spent 26 hours alive, answering `GET /` in 0.4 ms, and hanging every inference forever. `--restart unless-stopped` was already set and never fired, because the container never exited. The Dockerfile's own comment says "curl: the HEALTHCHECK below" — but no `HEALTHCHECK` was ever declared.

**Files:**
- Modify: `scripts/stt_server.Dockerfile`

**Interfaces:**
- Consumes: `scripts/silence.wav` (32 KB, already in the repo — the Docker build context is `scripts/`).
- Produces: an image whose health status flips to `unhealthy` when inference stalls. Task 3's compose file relies on that status for `depends_on: service_healthy`.

- [ ] **Step 1: Add the probe asset and the healthcheck**

Append to `scripts/stt_server.Dockerfile`, after the existing `ENV LD_LIBRARY_PATH` line:

```dockerfile
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
```

- [ ] **Step 2: Rebuild the image**

Run:

```bash
cd /home/besquivel/rtl
docker build -t rtl-whisper-cuda -f scripts/stt_server.Dockerfile scripts
```

Expected: build succeeds. It is a small image; the whisper binaries are bind-mounted at run time, not baked in.

- [ ] **Step 3: Verify the healthcheck reports healthy against a working server**

The stack may already be running from `scripts/stt_server.sh`. Recreate it on the new image:

```bash
bash scripts/stt_server.sh stop
bash scripts/stt_server.sh start
sleep 100          # start-period is 90s
docker inspect --format '{{.State.Health.Status}}' rtl-stt-server
```

Expected: `healthy`.

- [ ] **Step 4: Verify it reports unhealthy against a stalled server — this is the whole point**

A probe nobody has watched fail is not yet evidence. Stop the server's process without removing the container, so it is running-but-not-serving:

```bash
docker exec rtl-stt-server pkill -STOP whisper-server
sleep 150          # two 60s intervals plus a 30s timeout
docker inspect --format '{{.State.Health.Status}}' rtl-stt-server
docker inspect --format '{{range .State.Health.Log}}{{.ExitCode}} {{end}}' rtl-stt-server
```

Expected: `unhealthy`, with non-zero exit codes in the log. Then release it:

```bash
docker exec rtl-stt-server pkill -CONT whisper-server
```

Record both readings in your report. If the status stays `healthy` while the process is stopped, the probe is not exercising inference and the task is not done.

- [ ] **Step 5: Commit**

```bash
git add scripts/stt_server.Dockerfile
git commit -m "fix(stt): healthcheck that transcribes instead of pinging

rtl-stt-server spent 26 hours alive, answering GET / in 0.4ms and hanging every
inference forever. --restart unless-stopped never fired because the container
never exited, and docker could not have restarted it anyway: 'PID ... is zombie
and can not be killed'. The Dockerfile's own comment promised a HEALTHCHECK it
never declared.

The probe POSTs the repo's silence.wav to /inference, so only real work counts
as alive. A healthy GPU answers in ~0.2s and the CPU fallback takes ~123s, so
the 25s curl ceiling inside a 30s timeout separates the two by an order of
magnitude either way."
```

---

### Task 3: The compose stack

**Files:**
- Create: `docker-compose.yml`
- Modify: `.gitignore` (only if `docker-compose.override.yml` is not already ignored)

**Interfaces:**
- Consumes: `inContainer()` from Task 1 via `SDR_IN_CONTAINER=1`; the healthy image from Task 2 via `depends_on: service_healthy`.
- Produces: services named `whisper`, `stt-watch`, `web`, and container names `rtl-stt-server`, `rtl-stt-watch`, `rtl-web`. Task 4's `stack.sh` reads `docker compose ps`.

- [ ] **Step 1: Write the compose file**

Create `docker-compose.yml` at the repository root:

```yaml
# The console's supervised half.
#
# op25, the eight udp_audio_record.py recorders and the HackRFs stay on the
# host: they need USB device access and real-time scheduling, and the host
# already carries 34 gnuradio/osmosdr packages that would otherwise be
# duplicated here and drift out of sync.
name: rtl-console

# Every service mounts the repository at its REAL host path. sdrRoot() returns
# the literal string '/home/besquivel/rtl' and every script builds absolute
# paths from it, so mounting at /app resolves to paths that do not exist — the
# console then reports an empty corpus rather than an error.
x-repo: &repo /home/besquivel/rtl:/home/besquivel/rtl

services:
  whisper:
    build:
      context: ./scripts
      dockerfile: stt_server.Dockerfile
    image: rtl-whisper-cuda
    container_name: rtl-stt-server
    restart: unless-stopped
    # The binaries are bind-mounted rather than baked in, so rebuilding
    # whisper.cpp does not mean rebuilding this image.
    volumes:
      - ./tools/whisper.cpp/build-cuda/bin:/opt/whisper/bin:ro
      - ./models:/models:ro
    ports:
      # Loopback only. The host's own stt_watch and any manual curl reach it
      # here; the other containers reach it by service name on the compose net.
      - "127.0.0.1:8081:8081"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    command:
      - /opt/whisper/bin/whisper-server
      - -m
      - /models/ggml-medium.en.bin
      - --port
      - "8081"
      - --host
      - 0.0.0.0
      - --language
      - en

  stt-watch:
    image: python:3.12-slim
    container_name: rtl-stt-watch
    restart: unless-stopped
    # UID 1000: this service writes transcripts into sdr.db, and SQLite creates
    # sdr.db-wal beside it. A root-owned WAL file stops the host's recorders
    # writing, which stops recording.
    user: "1000:1000"
    working_dir: /home/besquivel/rtl
    volumes:
      - *repo
    environment:
      # stt_backend reads STT_URL; on the compose network the server is a
      # service name, not loopback.
      STT_URL: http://whisper:8081
    depends_on:
      whisper:
        condition: service_healthy
    # --no-cpu-fallback on purpose. The CPU path is what HID the first outage:
    # transcription did not stop, it degraded to ~123s per clip and coverage
    # slid from 98% to 55% while the system looked like it was working. Here a
    # whisper outage stops transcription outright, the healthcheck restarts
    # whisper, and this service resumes on its own. The host copy of
    # stt_watch.py keeps its fallback; this choice is scoped to the container.
    command:
      - python3
      - scripts/stt_watch.py
      - --dir
      - /home/besquivel/rtl/recordings
      - --no-cpu-fallback

  web:
    image: node:24-slim
    container_name: rtl-web
    restart: unless-stopped
    user: "1000:1000"
    # pid: host is what keeps isRadioBusy() and releasing the radio working —
    # pgrep and pkill read the host's /proc and signal host processes as the
    # same UID. Starting a capture is refused instead; see SDR_IN_CONTAINER.
    pid: host
    working_dir: /home/besquivel/rtl
    volumes:
      - *repo
    environment:
      SDR_IN_CONTAINER: "1"
      SDR_ROOT: /home/besquivel/rtl
    ports:
      - "3002:3002"
    command:
      - sh
      - -c
      - corepack enable && pnpm dev --host 0.0.0.0 --port 3002
```

- [ ] **Step 2: Validate the file before running anything**

Run:

```bash
cd /home/besquivel/rtl
docker compose config >/dev/null && echo "compose file valid"
docker compose config | grep -E "source:|target:|user:|pid:" | head -12
```

Expected: valid, and every `target:` for the repo mount reads `/home/besquivel/rtl`.

- [ ] **Step 3: Bring up whisper and stt-watch only**

The `web` service is left for the next step so a failure is attributable.

```bash
docker compose up -d whisper stt-watch
sleep 100
docker compose ps
```

Expected: `whisper` healthy, `stt-watch` running.

- [ ] **Step 4: Verify transcription flows through the containers**

```bash
docker compose logs --tail 8 stt-watch
```

Expected: `transcribing …` / `done (…)` lines with sub-second times, and **no** `FALLING BACK TO CPU` line — that path is disabled here by `--no-cpu-fallback`.

Confirm the file ownership hazard did not materialise:

```bash
ls -la sdr.db sdr.db-wal 2>/dev/null | awk '{print $3, $4, $9}'
```

Expected: owned by `besquivel besquivel`, not root. If any file is root-owned, stop and report it — the host recorders cannot write past that.

- [ ] **Step 5: Bring up the web service**

```bash
docker compose up -d web
sleep 40
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3002/
```

Expected: `200`. Note the host may already be serving 3002 from a `pnpm dev` run; stop that first (`kill` the `nuxt dev` PID) so the port is free and you know which one answered.

- [ ] **Step 6: Verify the container refuses to start a capture**

```bash
curl -s -X POST http://127.0.0.1:3002/api/listen/start \
  -H 'content-type: application/json' -H 'sec-fetch-site: same-origin' \
  -d '{"preset":"pd"}'
```

Expected: a JSON error mentioning the container and naming `lwin_listen_multi.sh`. **It must not spawn anything** — confirm with `ps -eo comm,args --no-headers | awk '$1=="python3" && /multi_rx/'` that no new op25 appeared.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(docker): compose stack for whisper, stt-watch and the web app

Three services, all as UID 1000 with the repo bind-mounted at its real path.
sdrRoot() returns the literal '/home/besquivel/rtl' and every script builds
absolute paths from it, so mounting at /app resolves to paths that do not exist
and the console reports an empty corpus rather than an error. UID 1000 because
SQLite creates sdr.db-wal beside the database and a root-owned WAL file stops
the host recorders writing.

op25, the recorders and the HackRFs stay on the host: USB and real-time
scheduling, and 34 gnuradio/osmosdr packages that would otherwise be duplicated
and drift. The web service takes pid: host so pgrep and pkill keep working for
radio state and release; only starting a capture is refused.

stt-watch runs with --no-cpu-fallback. That fallback is what hid the first
outage -- transcription degraded to ~123s per clip while coverage slid to 55%
and the system looked healthy. Loud beats slow-and-silent."
```

---

### Task 4: One status view over both halves

Half this system stays on the host by design. A status command that reports only containers would recreate the exact blind spot that let `stt_watch` die unnoticed when a capture session ended.

**Files:**
- Create: `scripts/stack.sh`

**Interfaces:**
- Consumes: the service names from Task 3.
- Produces: `scripts/stack.sh {up|down|status}`.

- [ ] **Step 1: Write the script**

Create `scripts/stack.sh`:

```bash
#!/usr/bin/env bash
# One view over both halves of the console.
#
# The supervised half runs under compose; op25, the recorders and the HackRFs
# run on the host. Reporting only the containers is how a dead transcription
# watcher goes unnoticed for hours, so `status` always prints both.
set -euo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$R"

host_status() {
  echo "HOST"

  # Match on the process NAME, never on the full argv.
  #
  # `pgrep -f "multi_rx.py"` matches its own command line, so it reports the
  # radio as running whenever this script is the only thing running. That bug
  # cost this project a stalled overnight harvest and, later, a status report
  # that claimed a capture was live twenty-five minutes after op25 had died.
  if ps -eo comm,args --no-headers | awk '$1=="python3" && /multi_rx/' | grep -q .; then
    echo "  op25          running"
  else
    echo "  op25          stopped"
  fi

  printf '  recorders     %s\n' "$(ps -eo args --no-headers | grep -c '[u]dp_audio_record' || true)"

  python3 - <<'PY'
import sqlite3, time
try:
    c = sqlite3.connect('file:sdr.db?mode=ro', uri=True).cursor()
except Exception as e:
    print(f"  corpus        unreadable ({e})")
    raise SystemExit(0)
t = c.execute('SELECT MAX(start) FROM calls').fetchone()[0]
if t is None:
    print("  newest call   none")
else:
    print(f"  newest call   {(time.time() - t) / 60:.0f} min ago")
tot, tr = c.execute(
    "SELECT COUNT(*), SUM(CASE WHEN transcript IS NOT NULL "
    "AND LENGTH(TRIM(transcript)) > 0 THEN 1 ELSE 0 END) "
    "FROM calls WHERE start > strftime('%s','now','-30 minutes')").fetchone()
print(f"  transcripts   {tr or 0}/{tot} in the last 30 min")
PY
}

case "${1:-status}" in
  up)     docker compose up -d && echo && host_status ;;
  down)   docker compose down ;;
  status)
    echo "CONTAINERS"
    docker compose ps --format '  {{.Service}}\t{{.Status}}' 2>/dev/null \
      || echo "  compose is not running"
    echo
    host_status
    ;;
  *) echo "usage: $0 {up|down|status}" >&2; exit 1 ;;
esac
```

- [ ] **Step 2: Make it executable and run it**

```bash
chmod +x scripts/stack.sh
./scripts/stack.sh status
```

Expected: a CONTAINERS block listing the three services with health, and a HOST block reporting op25, the recorder count, the newest call's age and transcript coverage.

- [ ] **Step 3: Verify the host half reports honestly**

The point of this script is that it does not lie about the host. Confirm it reads op25's real state rather than matching itself:

```bash
./scripts/stack.sh status | grep op25
ps -eo comm,args --no-headers | awk '$1=="python3" && /multi_rx/' | wc -l
```

Expected: `running` exactly when that count is non-zero, `stopped` when it is zero. Report both numbers.

- [ ] **Step 4: Commit**

```bash
git add scripts/stack.sh
git commit -m "feat(docker): stack.sh status reporting containers and the host together

Half the console stays on the host by design, so a status command that showed
only containers would recreate the blind spot that let stt_watch die unnoticed
when its capture session ended.

The op25 check matches the process NAME, not the full argv: pgrep -f
'multi_rx.py' matches its own command line, which stalled an overnight harvest
loop and later produced a status report claiming a capture was live twenty-five
minutes after op25 had died."
```

---

## Verification against the spec

| Spec requirement | Task |
|---|---|
| Three services: whisper, stt-watch, web | 3 |
| All containers as UID 1000 | 3 |
| Repo bind-mounted at `/home/besquivel/rtl` | 3 |
| op25, recorders, HackRFs stay on the host | 3 (by omission, stated in the file) |
| Healthcheck POSTs `silence.wav` to `/inference` | 2 |
| Healthcheck params 60s/30s/2/90s | 2 |
| `restart: unless-stopped` on all three | 3 |
| `pid: host` so pgrep/pkill keep working | 3 |
| Capture start refused with a stated error | 1 |
| No CPU fallback in the container | 3 (`--no-cpu-fallback`) |
| Host `stt_watch.py` keeps its fallback | untouched — the flag is passed only in compose |
| `stack.sh` reports both halves | 4 |
| Existing 113 Vitest tests stay green | 1 (step 5) |
| New test for container-mode error path | 1 |
| Wedged whisper detected and restarted | 2 (step 4 proves detection) |
| No container writes a root-owned file | 3 (step 4 checks it) |
