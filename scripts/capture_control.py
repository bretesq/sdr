#!/usr/bin/env python3
"""The capture container's control server.

PID 1 of the `capture` container (docker-compose.yml's `command:`). op25 and
its recorders need real USB access to two HackRFs plus the whole GNU Radio
stack, which the `web` container deliberately does not have -- see
server/utils/processes.ts's inContainer() guard. This server is what `web`
talks to instead: it owns op25 as its own child and exposes that over
GET /status, POST /start, POST /stop on the compose network only (port 8082,
never published -- see docker-compose.yml's `whisper` service for the same
pattern with a published vs. unpublished port).

THE SECURITY BOUNDARY. POST /start decides what argv runs on a machine with
SDR hardware, and anything that can reach the web app can reach this
endpoint. build_args() is the only function that turns a request into a
command line, and it does so by picking fixed, literal tokens (`--ess`,
`--include-encrypted`, `--pd`) off a validated, structured request -- never by
forwarding a caller-supplied string. Everything else in this file exists to
get a validated request to build_args() and a subprocess.Popen() argument
list from it, with shell=True never used anywhere.

Python 3 stdlib only: the image installs python3 and nothing else for this
server (see docs/superpowers/plans/2026-09-03-capture-container.md).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Mirrors server/utils/paths.ts's sdrRoot(): every script in this repo builds
# absolute paths from this same literal, and docker-compose.yml's `*repo`
# anchor bind-mounts the host at this exact path inside the container too, so
# the env var and the container path agree without needing SDR_ROOT set at
# all. The env var exists only so a future non-standard layout (or a test)
# can override it.
SDR_ROOT = os.environ.get("SDR_ROOT", "/home/besquivel/rtl")

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8082

# --- the security boundary --------------------------------------------------

# The only script this server is allowed to invoke, and the only "mode" a
# caller can select. lwin_listen.sh (single-receiver) is deliberately not
# wired in: Task 2's brief only asks this server to consume
# lwin_listen_multi.sh, and adding a second launcher here would double the
# surface build_args() has to reason about for no requested capability.
ALLOWED_MODES = frozenset({"multi"})

# A "sane range" for durationSec. 1 second minimum rules out a 0 or negative
# value that would make lwin_listen_multi.sh treat the run as unbounded
# (`[ "$SECS" -eq 0 ] ... && RUN=99999`) when the caller very clearly asked
# for a bounded one. 24 hours is generous for a single session; anything
# longer almost certainly means a caller passed seconds where they meant
# something else (or a decimal slipped a place), and pinning two exclusive
# HackRFs for longer than that on a typo is worth refusing loudly rather than
# honoring.
MIN_DURATION_SEC = 1
MAX_DURATION_SEC = 24 * 60 * 60

# The exact request shape this endpoint accepts. Anything else is rejected
# rather than silently ignored, per the brief: a caller-supplied field this
# server doesn't recognize is far more likely to be a mistake (or a probe)
# than something safe to drop on the floor.
ALLOWED_FIELDS = frozenset({"mode", "ess", "includeEncrypted", "durationSec"})


class ValidationError(ValueError):
    """A /start request does not fit the fixed, validated request shape.

    This is a distinct type specifically so the HTTP layer can turn it into a
    400 that quotes the caller's own mistake back at them, while any *other*
    exception out of build_args() (there should never be one, but "should
    never" is not a proof) surfaces as a 500 instead of being mistaken for a
    validation failure. Never caught and retried with a "cleaned up" version
    of the same request -- the correct response to an invalid field is to
    refuse it, not to guess what the caller meant and build a command line
    out of the guess.
    """


def build_args(req: object) -> list[str]:
    """Turn a validated, structured request into lwin_listen_multi.sh argv.

    No element of the returned list is ever a caller-supplied string. Every
    token is either a fixed literal this function owns (--ess,
    --include-encrypted, --pd) or str(n) of an int that has already been
    range-checked. `mode` is checked against a fixed allowlist and never
    itself appears in the output -- it only selects (elsewhere, in main())
    which script gets run.
    """
    if not isinstance(req, dict):
        raise ValidationError("request body must be a JSON object")

    unknown = set(req) - ALLOWED_FIELDS
    if unknown:
        raise ValidationError(f"unknown field(s): {sorted(unknown)}")

    # isinstance() first, always: `x in a_frozenset` hashes x, and a caller
    # can hand us an unhashable JSON value (a list, an object) for any field.
    # Without the type check first, that raises an uncaught TypeError instead
    # of the ValidationError every other bad input produces here -- a crash
    # is not a rejection, and this boundary must reject, not crash.
    mode = req.get("mode")
    if not isinstance(mode, str) or mode not in ALLOWED_MODES:
        raise ValidationError(f"mode must be one of {sorted(ALLOWED_MODES)}")

    args: list[str] = []

    ess = req.get("ess", False)
    if not isinstance(ess, bool):
        raise ValidationError("ess must be a boolean")
    if ess:
        args.append("--ess")

    include_encrypted = req.get("includeEncrypted", False)
    if not isinstance(include_encrypted, bool):
        raise ValidationError("includeEncrypted must be a boolean")
    if include_encrypted:
        args.append("--include-encrypted")

    duration = req.get("durationSec")
    if duration is not None:
        # bool is a subclass of int in Python, so isinstance(True, int) is
        # True -- without excluding bool explicitly, {"durationSec": true}
        # would sail through as a 1-second capture instead of being rejected
        # for being the wrong type.
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise ValidationError("durationSec must be an integer number of seconds")
        if not (MIN_DURATION_SEC <= duration <= MAX_DURATION_SEC):
            raise ValidationError(
                f"durationSec must be between {MIN_DURATION_SEC} and {MAX_DURATION_SEC}"
            )
        # This endpoint exposes no field for choosing a talkgroup preset --
        # it exists to run exactly one operational profile, the standard PD
        # (police/sheriff dispatch) capture that lwin_active_whitelist.txt
        # already encodes and that server/utils/processes.ts's own refusal
        # message documents as the canonical manual fallback:
        #   ./scripts/lwin_listen_multi.sh --ess --include-encrypted --pd 10800
        # So --pd is hardcoded here, not derived from the request.
        #
        # `--pd` and the duration are two INDEPENDENT tokens consumed by two
        # independent branches of lwin_listen_multi.sh's own argument loop:
        # `--pd` matches its own case (GEN+=(--preset pd)); the bare number
        # after it matches nothing else and falls through to the `*)` catch-
        # all, which assigns it to SECS. They are not "a flag and its value"
        # -- do not "simplify" this to `--pd=10800` or reorder the number
        # away from being last, or the script's `-*) exit 1` case will treat
        # a misplaced flag as unknown and refuse to run, or a repositioned
        # number will be swallowed as an argument to the wrong flag instead
        # of landing in SECS.
        args.append("--pd")
        args.append(str(duration))

    return args


def script_for(mode: str) -> str:
    """The launcher build_args()'s `mode` selects. Mirrors scriptFor() in
    server/utils/processes.ts, restricted to the one mode this server
    supports (see ALLOWED_MODES)."""
    assert mode in ALLOWED_MODES  # build_args() must already have checked this
    return os.path.join(SDR_ROOT, "scripts", "lwin_listen_multi.sh")


# --- boot-time validation ----------------------------------------------------

# The two read-only bind mounts docker-compose.yml's `capture` service
# carries the host's op25 build in. Checked explicitly at boot rather than
# discovered later: Task 1's review found that Docker silently creates an
# empty directory when a bind-mount source is missing on the host, instead of
# failing the container start. Left unchecked, that turns into a bare
# ImportError the first time someone tries to record -- exactly the failure
# mode this whole project keeps re-learning the cost of. This server owns
# startup, so it is the one place that can catch it before anyone is
# depending on a capture that was never going to work.
REQUIRED_MOUNTS = (
    "/usr/local/lib/x86_64-linux-gnu",
    "/usr/local/lib/python3.14/dist-packages",
)


class StartupError(RuntimeError):
    """Raised by check_op25_available() when the container cannot possibly
    record, so main() can fail loudly and immediately rather than starting a
    server that will only fail once someone tries to use it."""


def check_op25_available() -> None:
    problems = []
    for path in REQUIRED_MOUNTS:
        if not os.path.isdir(path):
            problems.append(f"{path} does not exist")
        elif not os.listdir(path):
            # An empty directory here IS the missing-bind-mount failure,
            # just delayed -- see the module comment above.
            problems.append(f"{path} exists but is empty (bind mount is likely missing on the host)")
    if problems:
        raise StartupError(
            "op25 is not available in this container:\n  "
            + "\n  ".join(problems)
            + "\nCheck docker-compose.yml's `capture` service volumes and that "
            "the host paths exist, then `docker compose up -d --force-recreate capture`."
        )

    try:
        import gnuradio.op25_repeater  # noqa: F401  (import-for-effect: proves the mount + ABI actually work)
    except ImportError as exc:
        raise StartupError(
            f"op25 mounts are present but gnuradio.op25_repeater still is not "
            f"importable ({exc}). Check LD_LIBRARY_PATH in docker/capture/Dockerfile "
            "and that the host's op25 build matches this image's gnuradio/Python "
            "versions."
        ) from exc


# --- capture lifecycle -------------------------------------------------------

class CaptureState:
    """The one op25 capture this container can ever be running at once.

    A single instance, guarded by `lock`. Every field is read or written only
    while holding `lock`, EXCEPT that the long blocking calls in stop() --
    signalling and waiting on the child -- deliberately run with the lock
    released (see stop()'s comment) so GET /status is never blocked for the
    several seconds a stop can take.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.request: dict | None = None      # the validated request that started it
        self.started_at: float | None = None  # time.time(), for status reporting
        self.stopping = False                 # set while a stop is in flight

    def _reap_if_exited_locked(self) -> None:
        """If the tracked child has exited on its own (duration ran out, or
        it crashed), reap it and clear state. Must be called with lock held.

        Without this, a duration-limited capture that finished its run keeps
        looking "running" forever afterwards: /status would report a stale
        pid and /start would refuse a legitimate next capture with a
        spurious "already running" -- for a process that is, in fact, long
        dead. poll() is non-blocking and safe to call on an already-reaped
        child, so this is cheap enough to call from both /status and /start.
        """
        if self.process is not None and self.process.poll() is not None:
            self.process = None
            self.request = None
            self.started_at = None

    def snapshot(self) -> dict:
        """GET /status's payload. Never raises, even if nothing is running
        or the tracked process just exited -- that is the normal, expected
        common case, not an error."""
        with self.lock:
            self._reap_if_exited_locked()
            if self.process is None:
                return {"running": False, "pid": None}
            return {
                "running": True,
                "pid": self.process.pid,
                "startedAt": datetime.fromtimestamp(
                    self.started_at, tz=timezone.utc
                ).isoformat(),
                "request": self.request,
            }

    def start(self, req: dict) -> dict:
        """Validate, then start a capture. Raises ValidationError for a bad
        request (build_args()'s job) or AlreadyRunning if one is already in
        flight (this method's own job -- two captures cannot share the
        HackRFs)."""
        args = build_args(req)  # raises ValidationError; nothing spawned yet
        mode = req["mode"]  # build_args() already proved this key exists and is valid
        script = script_for(mode)

        with self.lock:
            self._reap_if_exited_locked()
            if self.process is not None or self.stopping:
                raise AlreadyRunning(self.process.pid if self.process else None)

            # start_new_session=True is Python's setsid(): the child becomes
            # the leader of its own new process group (and session), so
            # stop() can signal the WHOLE group with os.killpg rather than
            # just the launcher's own bash process. Without it, a SIGINT to
            # the tracked pid alone would leave op25 and the eight recorders
            # -- all children of that bash, not of this server -- running
            # unsupervised. This is the Python equivalent of
            # server/utils/processes.ts's `detached: true` (Node's spawn
            # option that does the same setsid() under the hood).
            #
            # stdout/stderr are inherited from this process rather than
            # piped: piping without a reader thread risks the child
            # deadlocking once its pipe buffer fills, and this server has no
            # use for the child's output beyond making it visible --
            # inheriting means it lands directly in this container's own
            # stdout, which is exactly where `docker compose logs capture`
            # already looks.
            proc = subprocess.Popen(
                ["bash", script, *args],
                cwd=SDR_ROOT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.process = proc
            self.request = dict(req)
            self.started_at = time.time()

        # A launcher that is going to fail immediately (a missing op25 patch,
        # `script` not on PATH, a bad whitelist) typically does so within a
        # couple hundred milliseconds -- well before op25 itself has had time
        # to open a HackRF. A short, bounded poll here turns that into an
        # honest error response instead of a `{"started": true}` that lied:
        # the alternative is a caller trusting a success response for a
        # capture that was already dead before the response left this
        # process.
        time.sleep(0.3)
        with self.lock:
            self._reap_if_exited_locked()
            if self.process is None:
                raise LaunchFailed(proc.returncode)
            pid = self.process.pid

        log(f"started pid={pid} args={args}")
        return {"started": True, "pid": pid, "args": args}

    def stop(self) -> dict:
        with self.lock:
            self._reap_if_exited_locked()
            if self.process is None:
                return {"stopped": False, "message": "no capture running"}
            proc = self.process
            pgid = proc.pid  # the launcher is its own process-group leader (start_new_session=True)
            self.stopping = True

        # Signalling and waiting happen OUTSIDE the lock. A stop can take
        # several seconds (the launcher's cleanup trap has to run, and the
        # eight recorders have to notice and exit), and GET /status polling
        # from the console must keep answering during that window rather
        # than blocking on this method's own mutex -- `self.stopping` above
        # is what a concurrent start() checks instead, so it still gets a
        # correct 409 without needing the lock held here.
        forced = False
        try:
            # SIGINT, never SIGKILL first. lwin_listen_multi.sh's trap runs
            # on SIGINT/SIGTERM and is what stops the eight udp_audio_record
            # recorders cleanly; a first-resort SIGKILL bypasses that trap
            # entirely and is exactly how this project orphaned recorders
            # twice already. os.killpg (not os.kill) reaches the whole
            # process group op25 and the recorders are members of, not just
            # the launcher's own bash.
            os.killpg(pgid, signal.SIGINT)
        except ProcessLookupError:
            pass  # already gone between the check above and here -- fine, not an error

        exited = _wait_for_exit(proc, timeout_sec=8.0)
        if not exited:
            log(f"pid={pgid} still alive 8s after SIGINT; sending SIGKILL")
            forced = True
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _wait_for_exit(proc, timeout_sec=2.0)

        with self.lock:
            self.process = None
            self.request = None
            self.started_at = None
            self.stopping = False

        log(f"stopped pid={pgid} forced={forced}")
        return {"stopped": True, "pid": pgid, "forced": forced}


class AlreadyRunning(Exception):
    def __init__(self, pid: int | None) -> None:
        super().__init__("a capture is already running; stop it first")
        self.pid = pid


class LaunchFailed(Exception):
    def __init__(self, returncode: int | None) -> None:
        super().__init__(f"launcher exited immediately (code {returncode}) -- check docker compose logs capture")
        self.returncode = returncode


def _wait_for_exit(proc: subprocess.Popen, timeout_sec: float) -> bool:
    """Poll (never blocking-wait) for `proc` to exit, up to `timeout_sec`.
    Polling rather than proc.wait(timeout=...) so the caller's intent --
    "wait briefly, then act" -- stays a plain, readable loop, and so a future
    caller adding logic between checks doesn't have to fight a timed
    blocking call to do it."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.2)
    return proc.poll() is not None


STATE = CaptureState()


def log(message: str) -> None:
    # Plain print, matching this repo's other long-running scripts (e.g.
    # stt_watch.py) rather than the logging module -- flush=True so lines
    # appear promptly in `docker compose logs -f capture` instead of sitting
    # in a buffer. Never logs the request body's raw bytes or the process
    # environment: the validated dict logged in start() is exactly the
    # fields ALLOWED_FIELDS permits, which holds no secrets by construction.
    print(f"capture_control: {message}", flush=True)


# --- HTTP layer ---------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "capture-control/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 (stdlib signature)
        # Route through log() so every line -- ours and http.server's own
        # per-request line -- goes through the same "capture_control: "
        # prefix and the same stdout `docker compose logs capture` reads.
        log(format % args)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/status":
            self._send_json(200, STATE.snapshot())
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/start":
            self._handle_start()
        elif self.path == "/stop":
            self._send_json(200, STATE.stop())
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_start(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            req = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"invalid JSON body: {exc}"})
            return

        try:
            result = STATE.start(req)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
        except AlreadyRunning as exc:
            self._send_json(409, {"error": str(exc), "pid": exc.pid})
        except LaunchFailed as exc:
            self._send_json(502, {"error": str(exc)})
        else:
            self._send_json(200, result)


def main() -> None:
    try:
        check_op25_available()
    except StartupError as exc:
        # Loud and immediate, per the brief: a radio tool that starts up
        # broken and only reveals it when someone tries to record is the
        # failure mode this whole project keeps fighting. `restart:
        # unless-stopped` will keep restarting this container and re-emit
        # this same message each time, which is the point -- it stays
        # visible in `docker compose logs capture` until someone fixes the
        # actual mount, rather than degrading into a mysterious ImportError
        # the first time an operator tries to start a capture.
        print(f"capture_control: FATAL: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)

    log(f"op25 available; listening on {LISTEN_HOST}:{LISTEN_PORT}")
    httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
