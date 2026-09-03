# Capture Container Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start and stop radio captures from the web console again, by giving op25 and the recorders their own container with real USB access to the HackRFs.

**Architecture:** A new `capture` service runs op25 and all eight `udp_audio_record` recorders together, because they talk to each other over `127.0.0.1:23460+` and separating them would reproduce the loopback bug this project already paid for once. A small Python control server is PID 1 in that container and owns op25 as its child, exposing `start`/`stop`/`status` on the compose network. The web container keeps its slim image and calls that API instead of spawning op25 itself.

op25 is **not** rebuilt. The host's existing build is bind-mounted read-only, exactly as `stt_server.Dockerfile` already does with the whisper binaries. This was verified before planning: a stock `ubuntu:26.04` installs gnuradio `3.10.12.0-6` and Python `3.14.4` — both identical to the host — and `from gnuradio import op25_repeater` imports successfully against the bind-mounted `/usr/local` artifacts.

**Tech Stack:** Docker Compose, `ubuntu:26.04`, GNU Radio 3.10.12 + gr-osmosdr, Python 3.14 stdlib (`http.server` — no new dependencies), Nuxt 3 / Nitro on the web side.

## Why the capture container is separate from `web`

Putting op25 inside the web container would mean every `restart web` kills the live capture: Docker kills the container's whole cgroup, and cgroup membership — not the PID namespace — decides what dies. `pid: host` does not protect it and neither does `setsid`. That would silently reinstate the exact coupling `server/utils/transcriber.ts:96` exists to remove, and would turn the documented recovery command `./scripts/stack.sh restart web` into a radio outage.

## Global Constraints

- **Every container runs as UID 1000.** A root container creating `sdr.db-wal` stops the host recorders writing. The capture container writes recordings and `sdr.db` rows, so this is load-bearing here, not incidental.
- USB access is granted by `group_add: ["46"]` (the numeric host `plugdev` GID — never the name, the container's `/etc/group` will not match), a `/dev/bus/usb` bind mount, and `device_cgroup_rules: ['c 189:* rmw']`. **Never enumerate device nodes**: the HackRFs re-enumerated once already today, moving from one minor number to another. Never use `privileged: true`.
- `lwin_keys.json` holds live ADP key material. Never read, modify, or commit it, and never place it in a build context. Key **ids** are not secret; key **material** must never reach the browser.
- `recordings/` holds real public-safety audio. Never redistribute it, and never commit any `.wav` except the existing `scripts/silence.wav`.
- Read-only against the corpus from any read path: `file:sdr.db?mode=ro`. Never open a SQLite connection to a path that does not exist — that creates the file.
- The control API is **unpublished**, reachable only on the compose network, exactly like `whisper:8081`.
- The control API must never accept free-form command arguments. It takes a small, validated, structured request and builds the command line itself.
- Comments explain *why*, at length, for non-obvious decisions.
- Conventional commit messages.
- Stage only the files each task touches. Never `git add -A` — the working tree carries files the live capture rewrites continuously.
- Vitest collects only `server/**/*.test.ts` and `utils/**/*.test.ts`. Baseline is **122/122 vitest, 279/279 Python**.

## File Structure

- `docker/capture/Dockerfile` — new. `ubuntu:26.04` + gnuradio, gr-osmosdr, hackrf tools, `util-linux` (the launcher uses `script -q -f -c`). Its own build context, kept empty of repo content so key material cannot enter it.
- `scripts/capture_control.py` — new. The control server: PID 1 of the capture container, owns op25 as a child.
- `docker-compose.yml` — modified. Adds the `capture` service.
- `server/utils/processes.ts` — modified. The capture guard becomes capability-based; starting delegates to the control API.
- `server/utils/processes.test.ts` — modified. 18 existing tests, including the container-refusal ones.
- `scripts/stack.sh` — modified. `capture` joins the status view and the restart verb.

---

### Task 1: The capture image and its hardware

**Files:**
- Create: `docker/capture/Dockerfile`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: a `capture` service whose container can see both HackRFs and import `gnuradio.op25_repeater`. Task 2 runs its control server inside it.

**This task must not start a capture.** Its whole point is to prove the hardware path with the live host capture still running and untouched.

- [ ] **Step 1: Write the Dockerfile**

Create `docker/capture/Dockerfile`:

```dockerfile
# op25 needs GNU Radio, and GNU Radio's Python modules are ABI-tied to both the
# gnuradio build and the Python minor version. ubuntu:26.04 is not a stylistic
# choice: it is the host's own distribution, and it ships gnuradio 3.10.12.0-6
# with Python 3.14 -- byte-identical to what the host's op25 was compiled
# against. Verified before this plan was written: a stock ubuntu:26.04 with
# these packages imports the host's bind-mounted op25_repeater successfully.
# Changing this base is very likely to break that import.
FROM ubuntu:26.04

# gnuradio + gr-osmosdr: the SDR runtime op25 links against.
# hackrf: hackrf_info, used to prove the devices are visible.
# util-linux: lwin_listen_multi.sh wraps op25 in `script -q -f -c` for its log.
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      gnuradio gr-osmosdr hackrf python3 util-linux procps \
 && rm -rf /var/lib/apt/lists/*

# op25 itself is NOT baked in. The host's build is bind-mounted read-only at
# runtime, the same way stt_server.Dockerfile mounts the whisper binaries --
# so rebuilding or re-patching op25 does not mean rebuilding this image.
# ldconfig runs at start, not here: the libraries arrive with the mount.

WORKDIR /home/besquivel/rtl
```

- [ ] **Step 2: Build it**

```bash
docker build -f docker/capture/Dockerfile -t rtl-capture docker/capture
```

Expected: success. The build context is `docker/capture` and contains only the Dockerfile, so no repo content — and therefore no key material — can enter the image.

- [ ] **Step 3: Add the service to `docker-compose.yml`**

Add alongside the existing services, following their comment style:

```yaml
  capture:
    build:
      context: ./docker/capture
      dockerfile: Dockerfile
    image: rtl-capture
    container_name: rtl-capture
    restart: unless-stopped
    user: "1000:1000"
    # plugdev by NUMERIC gid. The HackRF device nodes are root:plugdev 0660, and
    # this container's /etc/group has no plugdev entry to resolve the name
    # against. 46 is this host's plugdev gid (getent group plugdev).
    group_add: ["46"]
    # USB passthrough that survives a replug. The whole /dev/bus/usb tree is
    # mounted rather than individual nodes, and the cgroup rule matches usb's
    # major 189 with ANY minor, because the HackRFs re-enumerate to new minor
    # numbers whenever they are unplugged -- which happened once already during
    # this project. Enumerating nodes here would work until the next replug.
    device_cgroup_rules:
      - 'c 189:* rmw'
    volumes:
      - *repo
      - /dev/bus/usb:/dev/bus/usb
      # The host's op25 build. Read-only, and the reason this image needs no
      # gnuradio-dev or cmake: the compiled blocks already exist on the host and
      # are ABI-compatible with this image's gnuradio.
      - /usr/local/lib/x86_64-linux-gnu:/usr/local/lib/x86_64-linux-gnu:ro
      - /usr/local/lib/python3.14/dist-packages:/usr/local/lib/python3.14/dist-packages:ro
    working_dir: /home/besquivel/rtl
    # Task 2 replaces this with the control server. Until then the container
    # exists only to prove the hardware path, so it idles rather than exiting
    # and fighting the restart policy.
    command: ["sleep", "infinity"]
```

- [ ] **Step 4: Bring up only this service**

```bash
docker compose up -d capture
```

Do NOT recreate any other service. `whisper`, `stt-watch` and `web` must keep their uptime; check with `docker compose ps` before and after.

- [ ] **Step 5: Prove the hardware path — the gate for this task**

```bash
docker exec rtl-capture ldconfig
docker exec rtl-capture hackrf_info
docker exec rtl-capture python3 -c "from gnuradio import op25_repeater; print('op25 OK')"
docker exec rtl-capture id
```

Expected: `hackrf_info` lists **both** serials (`…930c64dc275e54c3` and `…977c64de2d717413`), the import prints `op25 OK`, and `id` shows `uid=1000` with group `46` present.

`hackrf_info` will report the devices as busy or in use — the host capture currently owns them. **That is the correct and expected result**: it proves the container can see and open the USB devices. Do not stop the host capture to get a cleaner reading.

If `hackrf_info` finds no devices at all, the passthrough is wrong — report it rather than reaching for `privileged: true`.

- [ ] **Step 6: Confirm nothing else moved**

`docker compose ps` — `whisper`, `stt-watch` and `web` unchanged. `./scripts/stack.sh status` still reports `op25 running` on the host with recordings landing.

- [ ] **Step 7: Commit**

```bash
git add docker/capture/Dockerfile docker-compose.yml
git commit
```

---

### Task 2: The control server

**Files:**
- Create: `scripts/capture_control.py`
- Modify: `docker-compose.yml` (replace the placeholder `command`)

**Interfaces:**
- Consumes: `scripts/lwin_listen_multi.sh`, which it invokes as a child.
- Produces: `GET /status`, `POST /start`, `POST /stop` on port `8082`, unpublished. Task 3's web client calls these.

**This task must not start a real capture.** Verify the refusal and status paths only; Task 4 owns the first real start.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_capture_control.py`, matching the existing Python suite's style (279 tests currently pass via `python3 -m unittest discover -s scripts/tests`):

```python
import unittest
from scripts.capture_control import build_args, ValidationError


class BuildArgsTest(unittest.TestCase):
    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "; rm -rf /"})

    def test_rejects_non_integer_duration(self):
        with self.assertRaises(ValidationError):
            build_args({"mode": "multi", "durationSec": "10800; id"})

    def test_builds_the_documented_invocation(self):
        args = build_args({
            "mode": "multi",
            "ess": True,
            "includeEncrypted": True,
            "durationSec": 10800,
        })
        self.assertEqual(
            args,
            ["--ess", "--include-encrypted", "--pd", "10800"],
        )

    def test_omits_flags_that_were_not_requested(self):
        self.assertEqual(build_args({"mode": "multi"}), [])
```

- [ ] **Step 2: Run it and watch it fail**

`python3 -m unittest scripts.tests.test_capture_control -v` — expect `ModuleNotFoundError`.

- [ ] **Step 3: Write the control server**

Create `scripts/capture_control.py`. Python 3 stdlib only — no new dependencies, and the image already has python3.

Requirements, all load-bearing:

- **`build_args(req)` accepts only a validated, structured request** and constructs the argument list itself. `mode` comes from a fixed allowlist. `durationSec` must be an `int` within a sane range. Booleans are booleans. **No caller-supplied string ever reaches the command line.** Raise `ValidationError` otherwise. This is the security boundary of the whole feature: anything that can reach the web app can reach this endpoint.
- **Never use `shell=True`.** `subprocess.Popen` with an argument list.
- **op25 is a child of this process**, started in its own process group so the control server can stop the whole group cleanly.
- **`POST /start` refuses when a capture is already running**, with a clear message. The HackRFs are exclusive; two captures cannot coexist.
- **`GET /status`** reports whether a capture is running and its pid, and must never crash when none is.
- **`POST /stop`** signals the process group, waits briefly, and reports what it did. Never `SIGKILL` as the first move — the launcher has a cleanup trap that must run, or recorders are orphaned.
- Bind `0.0.0.0:8082` — the port is not published, so this is the compose network only.
- Log to stdout so `docker compose logs capture` is useful.

- [ ] **Step 4: Run the tests — they pass**

- [ ] **Step 5: Make it the container's command**

In `docker-compose.yml`, replace the placeholder:

```yaml
    command: ["python3", "scripts/capture_control.py"]
```

`docker compose up -d capture` — recreate ONLY `capture`.

- [ ] **Step 6: Verify the API without starting a capture**

```bash
docker exec rtl-web node -e 'fetch("http://capture:8082/status").then(r=>r.json()).then(j=>console.log(j))'
```

Expected: valid JSON reporting no capture running. This also proves the compose-network name resolves from `web`, which Task 3 depends on.

Then confirm a malformed start is refused, using a request that must never execute:

```bash
docker exec rtl-web node -e 'fetch("http://capture:8082/start",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({mode:"; id"})}).then(r=>r.text()).then(t=>console.log(t))'
```

Expected: a validation refusal, and **no** process started. Confirm with `docker exec rtl-capture ps -ef`.

- [ ] **Step 7: Commit**

---

### Task 3: Capability-based guard, and the web client

**Files:**
- Modify: `server/utils/processes.ts`
- Modify: `server/utils/processes.test.ts`
- Modify: `scripts/stack.sh`

**Interfaces:**
- Consumes: the control API from Task 2.
- Produces: `startListening()` delegating to the control API; a capability probe replacing the container check.

- [ ] **Step 1: Write the failing tests**

In `server/utils/processes.test.ts`, replace the container-refusal tests with capability-refusal tests. The existing suite has 18 tests; the container ones assert the old behaviour and must be rewritten, not merely added to.

The new tests must fail if the guard is removed. Assert the refusal message names what is actually missing, and that no spawn is attempted. Follow `server/utils/transcriber.test.ts`, which mocks `node:child_process` so a regression cannot reach a real process — do the same here.

- [ ] **Step 2: Run them and watch them fail**

- [ ] **Step 3: Replace the guard**

In `server/utils/processes.ts`, the current guard refuses when `inContainer()` is true. That was always a proxy for "this process cannot reach the HackRFs"; now the web container legitimately cannot, while the capture container legitimately can, and `inContainer()` is true in both.

Replace it with a capability probe — USB present, `hackrf_info` on PATH, `gnuradio.op25_repeater` importable — and keep the refusal message actionable, naming `./scripts/stack.sh` as the existing messages do.

**Do not touch `startTranscriber()` or `stopTranscriber()` at `transcriber.ts:110` and `:167`.** Those guards are correct: the transcriber is compose-managed and that reasoning has not changed.

- [ ] **Step 4: Delegate starting to the control API**

`startListening()` in the web container must POST the structured request to `http://capture:8082/start` rather than spawning. Keep `buildListenArgs`'s existing option handling on the web side for the structured fields; do not send a pre-built command line — the control server builds and validates that itself.

Surface the control server's error verbatim, the way `server/api/listen/start.post.ts:207` already returns thrown messages to the operator.

- [ ] **Step 5: Tests pass; full suite green**

`pnpm test` — 122 vitest baseline plus your changes, 279 Python plus Task 2's.

- [ ] **Step 6: Teach `stack.sh` about the new service**

`capture` joins the CONTAINERS listing and is a valid target for `restart`. Keep the host section reporting op25 honestly — it now runs inside a container but is still visible in the host PID namespace, so state which half owns it rather than double-counting.

- [ ] **Step 7: Commit**

---

### Task 4: Cutover — the first in-container capture

**Files:** none necessarily; this task is verification. Commit only if it uncovers a fix.

**The user has explicitly approved a radio outage window for this task.** It is still a live public-safety monitor, so keep the window short and restore on any failure.

- [ ] **Step 1: Record the starting state**

`./scripts/stack.sh status`, the op25 pid, the newest call time, and the last few recordings. You need this to prove restoration.

- [ ] **Step 2: Stop the host capture**

Stop the host-launched capture cleanly so its trap runs and no recorders are orphaned. Confirm the HackRFs are free with `hackrf_info` on the host.

- [ ] **Step 3: Start one from the console**

Use the web UI on port 3002, not curl — the point is that the operator's own path works end to end.

- [ ] **Step 4: Verify the whole chain**

- both `multi_rx.py` processes and 8 recorders running **inside `rtl-capture`** (`docker exec rtl-capture ps -ef`)
- control-channel lock in `results/op25_multi.log` — TSBK grants on both the 700 and 800 legs
- new `.wav` files landing in `recordings/`, owned `besquivel:besquivel`, **not root**
- transcripts appearing within seconds, via the existing containerized `stt-watch`
- `sdr.db`, `-wal`, `-shm` still `besquivel:besquivel`
- the console showing `on air` — owned by a session, not `on air · outside session`

- [ ] **Step 5: Verify stop, then restart**

Stop from the UI; confirm op25 and every recorder exit and nothing is orphaned. Start again. Then `./scripts/stack.sh restart web` and confirm the capture **survives** — that is the entire reason the capture container is separate.

- [ ] **Step 6: If anything fails, restore immediately**

Start the host capture as before and report. Do not leave the radio down while debugging.

- [ ] **Step 7: Record the result in the report**

Include how long the outage lasted and the first transcript timestamp after cutover.
