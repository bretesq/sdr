#!/usr/bin/env python3
"""The capture container's control server.

PID 2 of the `capture` container (docker-compose.yml's `command:`, with
`init: true` running tini as PID 1 -- see the "signal handling" note below
for why this is load-bearing, not incidental). op25 and its recorders need
real USB access to two HackRFs plus the whole GNU Radio stack, which the
`web` container deliberately does not have -- see server/utils/processes.ts's
inContainer() guard. This server is what `web` talks to instead: it owns op25
as its own child and exposes that over GET /status, POST /start, POST /stop
on the compose network only (port 8082, never published -- see
docker-compose.yml's `whisper` service for the same pattern with a published
vs. unpublished port).

SIGNAL HANDLING. Whatever is PID 1 of a container's PID namespace gets the
kernel's PID-1 signal immunity: a signal whose disposition is the default
("terminate") is silently discarded unless that process has installed its
own handler for it (man 7 pid_namespaces). Without `init: true`, THIS process
would be PID 1, `docker compose stop/restart capture` would send SIGTERM,
nothing would happen, Docker would wait out the grace period and then
SIGKILL the whole cgroup -- tearing down op25's process group instantly and
skipping lwin_listen_multi.sh's cleanup trap entirely. That is exactly the
orphaned-recorder failure mode this project has already hit twice, and it is
the identical bug `docker-compose.yml`'s `whisper` service already carries a
fix for (its own `init: true`, after a 26-hour outage from the same root
cause). `init: true` alone is NOT sufficient here, though: it makes tini PID
1 and this process PID 2, where SIGTERM is deliverable -- but the *default*
disposition for PID 2 is still "terminate immediately", with no chance to
run STATE.stop()'s SIGINT-first-then-SIGKILL ladder before dying, and tini
exits (tearing down the whole namespace) as soon as this process does. So
this file ALSO installs an explicit SIGTERM/SIGINT handler
(install_shutdown_handlers(), called from main()) that runs STATE.stop()
synchronously -- letting the cleanup trap run -- before this process exits.
Both halves are required; either alone leaves recorders orphaned on a plain
`docker compose stop capture`.

THE SECURITY BOUNDARY. POST /start decides what argv runs on a machine with
SDR hardware, and anything that can reach the web app can reach this
endpoint. build_args() is the only function that turns a request into a
command line, and it does so by picking fixed, literal tokens (`--ess`,
`--include-encrypted`, `--n-voice-700`, `--n-voice-800`, and the preset
tokens in PRESET_ARGV) off a validated, structured request -- never by
forwarding a caller-supplied string. `preset` is the one field whose value
is itself a name rather than a number or a boolean, and it is handled by
LOOKUP, not passthrough: the caller's string is used only as a key into
PRESET_ARGV, and the argv comes out of that table's fixed literal values.
See PRESET_ARGV's own comment for why the indirection is load-bearing rather
than ceremonial. Everything else in this file exists to
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
#
# sessionId is optional: server/utils/processes.ts's local-spawn path always
# has one (server/utils/session.ts opens the row before spawning), but this
# endpoint must still work for a caller that omits it -- the capture simply
# runs with no SDR_SESSION_ID, and every call it records gets session_id
# NULL, exactly like a session started from a bare shell already does.
#
# nVoice700/nVoice800 are also optional -- omitted, lwin_listen_multi.sh (via
# scripts/make_multirx_cfg.py's LEG_700/LEG_800 defaults) picks its own
# measurement-derived default for each leg. They are receiver-count TUNING of
# the operational profile, not a way to change which legs run -- that is why
# they get to pass through here while `legs` still does not.
#
# `preset` joined this set when the console gained a preset picker. It is the
# one field here whose value is a NAME, so it is also the one field that
# could have re-opened the injection path this module exists to close -- see
# PRESET_ARGV, which is why it did not: the name selects a row, the row
# supplies the tokens. Omitted, it defaults to DEFAULT_PRESET ("pd"), so
# every caller written before this field existed keeps getting the identical
# capture it always got.
ALLOWED_FIELDS = frozenset(
    {
        "mode", "ess", "includeEncrypted", "durationSec", "sessionId",
        "nVoice700", "nVoice800", "preset",
    }
)

# Sane bounds on sessionId, mirroring durationSec's MIN/MAX pair above. It is
# an autoincrement SQLite rowid (server/utils/session.ts's sessionStore.open()
# via `last_insert_rowid()`), so it is always a small positive integer in
# practice -- but this endpoint validates every field to the same standard
# regardless of how trustworthy its usual caller is, per build_args()'s own
# stated job as this feature's security boundary.
#
# MIN_SESSION_ID had no ceiling counterpart until final-review.md's I4: a
# sessionId of, say, 10**30 passed straight through, became SDR_SESSION_ID in
# the child's environment, and reached sdr_db.upsert_call()'s session_id
# column -- which SQLite backs with a 64-bit signed integer. The insert then
# raised OverflowError, caught by udp_audio_record.py's broad `except
# Exception` (its job is to keep recording even when the DB write for one
# call fails), so the .wav files kept landing while EVERY call silently
# dropped out of sdr.db for the rest of the session -- a corpus outage whose
# only trace was a WARNING line in `docker compose logs capture`.
#
# MAX_SESSION_ID applies the same PLAUSIBILITY standard as MAX_DURATION_SEC
# above, not a type-limit standard (i.e. this is not simply "the largest
# int64 SQLite can store" -- that would still let a wildly-wrong value, like
# an accidental swap with durationSec's own range, pass as "plausible").
# sessions is an autoincrement rowid seeded entirely by this app's own real
# usage -- 30 rows as of this writing. One million is generous across many
# orders of magnitude of headroom (at 10 sessions/day that's ~274 years)
# while still catching the class of value that would otherwise overflow
# SQLite's column: a typo, a unit confusion, or a caller sending garbage.
MIN_SESSION_ID = 1
MAX_SESSION_ID = 1_000_000

# Bounds for nVoice700/nVoice800, mirroring server/api/listen/start.post.ts's
# MAX_VOICE exactly (both must agree: this is the same physical launcher,
# lwin_listen_multi.sh, reached by two different front doors). Not arbitrary:
# each channel adds a decimating FIR running at the device's full sample
# rate, and each needs its own udp_audio_record.py process and a UDP port two
# above the last, so the count has to stay small enough that the port block
# (BASE_PORT 23460 in lwin_listen_multi.sh) stays inside 23460-23492. 1 is the
# floor because scripts/make_multirx_cfg.py's build() raises ValueError for a
# leg with zero voice channels -- a caller sending 0 here should get this
# server's own clear 400, not a 500 from that downstream ValueError.
MIN_VOICE = 1
MAX_VOICE = 8

# The talkgroup presets this endpoint will run, mapped to the EXACT argv this
# module emits for each one.
#
# THIS TABLE IS THE SECURITY BOUNDARY FOR `preset`, and its shape is the whole
# point. A caller-supplied string is used for ONE thing only -- as a key into
# this dict -- and the tokens that reach the command line come out of the
# VALUES, which are fixed literals this module owns and a caller cannot
# influence. `PRESET_ARGV[preset]` is never `preset`. Do NOT "simplify" this
# to `("--preset", preset)` after the membership test: that would be
# equivalent only for as long as the keys and the launcher's accepted names
# stay identical, and it would quietly re-introduce the very thing
# build_args()'s docstring promises never happens -- a caller-supplied string
# becoming an argv token. The indirection is not redundant, it is the
# invariant.
#
# Two spellings appear in the values because that is what
# scripts/lwin_listen_multi.sh's own argument loop accepts: it has six
# per-preset shortcut cases (`--pd)  GEN+=(--preset pd) ;;` and friends) and a
# generic `--preset "$2"` case for everything else. `schools`, `publicworks`
# and `all` have no shortcut, so they go through the generic form -- where the
# second token is still a literal spelled out HERE, not the caller's string.
#
# `pd` deliberately keeps its `--pd` shortcut rather than being normalized to
# `("--preset", "pd")`. It is the default, it is what every existing session
# ran with, and it is the invocation this project's own documentation and
# server/utils/processes.ts's refusal message both quote verbatim
# (`./scripts/lwin_listen_multi.sh --ess --include-encrypted --pd 10800`).
# Changing the emitted tokens for the unchanged default would make the argv
# recorded against old and new sessions differ for no behavioural reason.
#
# Keys mirror scripts/make_whitelist.py's PRESETS dict (the thing that
# actually turns a preset name into a whitelist) and
# server/api/listen/start.post.ts's own PRESETS set. All three must agree;
# scripts/tests/test_capture_control.py asserts the first pair by parsing
# make_whitelist.py, so this table cannot silently drift from the script that
# has to understand it.
PRESET_ARGV: dict[str, tuple[str, ...]] = {
    "pd":          ("--pd",),
    "pd-all":      ("--pd-all",),
    "fire":        ("--fire",),
    "fire-all":    ("--fire-all",),
    "ems":         ("--ems",),
    "interop":     ("--interop",),
    "schools":     ("--preset", "schools"),
    "publicworks": ("--preset", "publicworks"),
    "all":         ("--preset", "all"),
}

# What a request that names no preset runs. `pd` -- the police/sheriff
# dispatch profile every session before this field existed ran, so an
# unchanged caller gets an unchanged capture.
DEFAULT_PRESET = "pd"


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


def build_args(req: object) -> tuple[list[str], int | None]:
    """Turn a validated, structured request into (lwin_listen_multi.sh argv,
    validated sessionId).

    No element of the returned argv is ever a caller-supplied string. Every
    token is either a fixed literal this function owns (--ess,
    --include-encrypted, --n-voice-700, --n-voice-800, and every token in
    PRESET_ARGV's values) or str(n) of an int that has already been
    range-checked. `mode` is checked against a fixed allowlist and never
    itself appears in the output -- it only selects (elsewhere, in main())
    which script gets run.

    `preset` obeys that same rule despite being a name: it is checked against
    PRESET_ARGV's keys and then DISCARDED, with the tokens taken from that
    table's value instead. So a request asking for "schools" emits the two
    literals ("--preset", "schools") that this module spelled out at import
    time, not the two characters-in-a-row the caller happened to send. A
    preset outside the table is refused outright, exactly as an out-of-range
    durationSec or nVoice700 is -- never coerced to the default, because a
    caller who asked for a capture this server cannot run should learn that,
    not silently get a different one.

    sessionId never becomes an argv token at all -- it becomes SDR_SESSION_ID
    in the launched process's environment instead (CaptureState.start(),
    mirroring server/utils/processes.ts's local-spawn path). It is validated
    HERE regardless, alongside every other field, so this function remains
    the single place responsible for the whole request: a second, easy-to-
    forget validation path elsewhere is exactly the kind of gap that turns
    into an unvalidated value reaching a child process's environment.
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

    # nVoice700/nVoice800: receiver-count overrides for lwin_listen_multi.sh's
    # own --n-voice-700/--n-voice-800 flags (see that script's own defaults,
    # sourced from scripts/make_multirx_cfg.py's LEG_700/LEG_800). Each is an
    # independent flag-plus-value pair consumed by that script's own arg loop
    # (`--n-voice-700) NV700="$2"; shift`), so where either lands relative to
    # the preset/duration tokens below does not matter -- only THAT flag stays last
    # matters, because the bare number after it is the one token with no
    # flag of its own.
    for field, flag in (("nVoice700", "--n-voice-700"), ("nVoice800", "--n-voice-800")):
        n_voice = req.get(field)
        if n_voice is not None:
            # Same bool-before-int trap as durationSec/sessionId above:
            # isinstance(True, int) is True in Python, so {"nVoice700": true}
            # would otherwise sail through as n_voice=1.
            if isinstance(n_voice, bool) or not isinstance(n_voice, int):
                raise ValidationError(f"{field} must be an integer")
            if not (MIN_VOICE <= n_voice <= MAX_VOICE):
                raise ValidationError(f"{field} must be between {MIN_VOICE} and {MAX_VOICE}")
            args.append(flag)
            args.append(str(n_voice))

    # isinstance() before the membership test, for the same reason `mode` gets
    # it above: `x in a_dict` hashes x, and a caller can send an unhashable
    # JSON value (a list, an object) for this field. Without the type check
    # first that is an uncaught TypeError -- a crash, not a rejection.
    preset = req.get("preset", DEFAULT_PRESET)
    if not isinstance(preset, str) or preset not in PRESET_ARGV:
        raise ValidationError(f"preset must be one of {sorted(PRESET_ARGV)}")

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
        # The preset tokens and the duration are INDEPENDENT tokens consumed
        # by independent branches of lwin_listen_multi.sh's own argument
        # loop: `--pd` matches its own case (GEN+=(--preset pd)), `--preset
        # schools` matches the generic case (which does its own `shift` to
        # swallow the name); the bare number after either matches nothing
        # else and falls through to the `*)` catch-all, which assigns it to
        # SECS. The number is NOT "the value of the preset flag" -- do not
        # "simplify" this to `--pd=10800`, and do not reorder the number away
        # from being last, or the script's `-*) exit 1` case will treat a
        # misplaced flag as unknown and refuse to run, or a repositioned
        # number will be swallowed as an argument to the wrong flag (the
        # generic `--preset` case's `"$2"` will eat it outright) instead of
        # landing in SECS.
        #
        # The preset tokens are emitted HERE, inside the duration block,
        # rather than unconditionally. That is not tidiness: this block is the
        # only place a bare number lands in argv, and a preset with no
        # duration would start an UNBOUNDED capture on a wider whitelist --
        # a strictly larger operation than anything this endpoint could do
        # before. A request that names a preset without a duration is refused
        # below instead, so the field can never be silently ignored either.
        args.extend(PRESET_ARGV[preset])
        args.append(str(duration))
    elif "preset" in req:
        # Refuse rather than drop. Emitting the preset here would create the
        # unbounded-wide-capture case the comment above rules out; ignoring it
        # would run a `pd`-shaped capture for a caller who plainly asked for
        # something else, and they would have no way to tell from the
        # response. The only honest answer is to say what is missing.
        raise ValidationError("preset requires durationSec (there is no unbounded preset capture)")

    session_id = req.get("sessionId")
    if session_id is not None:
        # Same bool-before-int trap as durationSec above: isinstance(True, int)
        # is True in Python, so {"sessionId": true} would otherwise sail
        # through as session_id=1.
        if isinstance(session_id, bool) or not isinstance(session_id, int):
            raise ValidationError("sessionId must be an integer")
        if not (MIN_SESSION_ID <= session_id <= MAX_SESSION_ID):
            raise ValidationError(
                f"sessionId must be between {MIN_SESSION_ID} and {MAX_SESSION_ID}"
            )

    return args, session_id


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
        # The launcher's original pid, which is ALSO its process group id
        # (start_new_session=True makes it its own group leader). Tracked
        # separately from `process` because the two can outlive each other:
        # `process` is only ever OUR direct child, the launcher bash -- but
        # op25 and the eight recorders it forks are members of the SAME
        # group without being bash's dependents once bash itself exits. If
        # bash dies first (crash, or reaching the end of its script) while
        # they are still alive and holding the HackRFs, `process` becomes
        # None on reap while `pgid` -- and the capture it identifies -- must
        # keep reporting as running. See _capture_actually_alive_locked().
        self.pgid: int | None = None
        self.request: dict | None = None      # the validated request that started it
        self.started_at: float | None = None  # time.time(), for status reporting
        self.stopping = False                 # set while a stop is in flight

    def _capture_actually_alive_locked(self) -> bool:
        """Is the contended resource -- the process GROUP holding the
        HackRFs -- actually still alive, independent of whether the one
        process this server happens to have spawned (`self.process`, the
        launcher bash) is still around?

        This is the same question server/utils/processes.ts's isRadioBusy()
        asks on the host, for the same reason: a pid is not the resource.
        Trusting `self.process`'s own liveness alone reproduces exactly the
        bug that function exists to prevent -- the launcher can exit while
        op25/recorders it forked (same group, not its dependents once it
        exits) are still running and still holding the radios.
        """
        return self.pgid is not None and _pgid_alive(self.pgid)

    def _reap_if_exited_locked(self) -> None:
        """Clear state once the capture is ACTUALLY gone -- every member of
        its process group, not merely our own direct child. Must be called
        with lock held.

        Two things happen here, in order, and the order matters:

        1. If `self.process` (our own direct child, the launcher bash) has
           exited, reap it via poll(). This is not optional bookkeeping: an
           un-reaped zombie is still a member of its process group as far as
           the kernel is concerned, so leaving it un-reaped would make
           _capture_actually_alive_locked() report "alive" forever even
           after every other member of the group is long gone.
        2. THEN check whether the group has any member left at all. If not,
           clear every field -- including `pgid` -- so /status stops
           reporting a stale pid and /start does not refuse a legitimate
           next capture with a spurious "already running" for hardware that
           is not, in fact, in use. If the group DOES still have a member
           (op25 or a recorder outliving a dead launcher), state is left
           exactly as-is: still running, by pgid, even though `self.process`
           itself may already be gone.
        """
        if self.process is not None:
            self.process.poll()
        if self.pgid is not None and not self._capture_actually_alive_locked():
            self.process = None
            self.pgid = None
            self.request = None
            self.started_at = None

    def snapshot(self) -> dict:
        """GET /status's payload. Never raises, even if nothing is running
        or the tracked process just exited -- that is the normal, expected
        common case, not an error.

        `running` is GROUP liveness (_capture_actually_alive_locked(), via
        _reap_if_exited_locked() above) -- the same meaning it has always had,
        and the same meaning start()'s AlreadyRunning check and stop() give
        `self.pgid`. CORRECTED (final-review.md section 8, round-2 re-review):
        an earlier version of this method reported `running` from op25's OWN
        liveness instead, to fix finding 5 (op25 dying while recorders keep
        the group alive). That went too far two ways at once: (1) `_op25_alive()`
        shells out to `pgrep`, which can fail transiently (timeout, contention
        on a box already running op25 + 8 recorders + whisper's GPU
        transcription) -- collapsing THAT into the same `running: false` as a
        real death meant one blip could permanently close a live session's
        tracked row, with none of the retry tolerance this codebase already
        built for the analogous network-unreachable case
        (session.ts's MAX_CONSECUTIVE_UNKNOWN); (2) even a CONFIRMED op25
        death then closed the session immediately (sessionStore.get()'s
        auto-close), which took away the console's own Stop button for a
        state the operator still needs to act on -- Task 4 had relied on
        exactly that button to recover an equivalent stale session before.
        Neither problem is reachable anymore because `running` no longer
        depends on `_op25_alive()` at all -- it is back to being the SAME
        pgid-based signal it always was, so the tracked session survives
        exactly as long as it always did, and the console's Stop path
        (POST /stop -> this class's own stop(), gated only on self.pgid,
        never on op25's health) still reaches the orphaned recorders
        regardless of which of the two below fires.

        `degraded`/`message` are ADDITIVE, informational-only fields layered
        on top -- they never change `running`, never touch `self.pgid`, and
        nothing in this file's session-lifecycle-adjacent state reads them.
        `degraded: true` fires ONLY on a CONFIRMED op25 death
        (`_op25_alive() is False`, pgrep's own documented "no process
        matched") -- never on `_op25_alive() is None` (inconclusive: a
        missing binary, a timeout, pgrep's own error exit). An inconclusive
        check produces NO claim in either direction, matching this project's
        now-established doctrine that "could not determine" and "confirmed
        gone" must never share one signal (see _op25_alive()'s own docstring
        for the full trace of getting this wrong once already, in
        `181e715..7870c61`).
        """
        with self.lock:
            self._reap_if_exited_locked()
            if self.pgid is None:
                return {"running": False, "pid": None}
            op25_state = _op25_alive(self.pgid)  # True | False | None
            payload = {
                "running": True,
                "pid": self.pgid,
                "startedAt": datetime.fromtimestamp(
                    self.started_at, tz=timezone.utc
                ).isoformat(),
                "request": self.request,
            }
            if op25_state is False:
                # CONFIRMED, not merely suspected: pgrep's own "no process
                # matched" for op25 specifically, while the group (self.pgid)
                # -- the recorders -- survives it. `running` above stays
                # `true` on purpose (see this method's own docstring) so the
                # session remains tracked and stoppable from the console;
                # `degraded` is purely the honest label for whoever is
                # looking, whether that is a future console affordance or an
                # operator/monitoring script hitting this endpoint directly.
                payload["degraded"] = True
                payload["message"] = (
                    "op25 has exited but its process group is still alive "
                    "(recorders holding no radio); stop this session, then "
                    "start a new one, to recover"
                )
            # op25_state is True (confirmed alive) or None (inconclusive --
            # see _op25_alive()'s docstring): no `degraded` key either way.
            # An inconclusive pgrep run must produce NO claim, not a
            # tentative one -- there is no "maybe degraded" in this contract.
            return payload

    def start(self, req: dict) -> dict:
        """Validate, then start a capture. Raises ValidationError for a bad
        request (build_args()'s job) or AlreadyRunning if one is already in
        flight (this method's own job -- two captures cannot share the
        HackRFs)."""
        args, session_id = build_args(req)  # raises ValidationError; nothing spawned yet
        mode = req["mode"]  # build_args() already proved this key exists and is valid
        script = script_for(mode)

        # None means "inherit this process's own environment unchanged" --
        # subprocess.Popen's own default when env= is omitted entirely, so a
        # request with no sessionId behaves exactly as before this field
        # existed. Only build a copy (and only when one is actually needed)
        # when sessionId was given, mirroring server/utils/processes.ts:458's
        # identical env-augmentation on the local-spawn path.
        env = None
        if session_id is not None:
            env = {**os.environ, "SDR_SESSION_ID": str(session_id)}

        with self.lock:
            self._reap_if_exited_locked()
            if self.pgid is not None or self.stopping:
                raise AlreadyRunning(self.pgid)

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
            #
            # env is inherited by bash and then by udp_audio_record.py (same
            # relay server/utils/processes.ts's own comment describes for the
            # local-spawn path), which reads SDR_SESSION_ID to stamp
            # session_id on each call it records.
            proc = subprocess.Popen(
                ["bash", script, *args],
                cwd=SDR_ROOT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
            self.process = proc
            self.pgid = proc.pid  # the launcher is its own process-group leader (start_new_session=True)
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
            if self.pgid is None:
                raise LaunchFailed(proc.returncode)
            pid = self.pgid

        log(f"started pid={pid} args={args}")
        return {"started": True, "pid": pid, "args": args}

    def stop(self) -> dict:
        with self.lock:
            self._reap_if_exited_locked()
            if self.pgid is None:
                return {"stopped": False, "message": "no capture running"}
            pgid = self.pgid
            self.stopping = True

        # Signalling and waiting happen OUTSIDE the lock. A stop can take
        # several seconds (the launcher's cleanup trap has to run, and the
        # eight recorders have to notice and exit), and GET /status polling
        # from the console must keep answering during that window rather
        # than blocking on this method's own mutex -- `self.stopping` above
        # is what a concurrent start() checks instead, so it still gets a
        # correct 409 without needing the lock held here.
        #
        # Waiting is done by polling the GROUP's liveness (_pgid_alive), not
        # by waiting on `self.process` alone -- the launcher bash can exit
        # (or already be gone) while op25/recorders it forked are still the
        # thing actually holding the HackRFs; see
        # _capture_actually_alive_locked()'s comment.
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

        exited = _wait_for_group_exit(pgid, timeout_sec=8.0)
        if not exited:
            log(f"pgid={pgid} still alive 8s after SIGINT; sending SIGKILL")
            forced = True
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _wait_for_group_exit(pgid, timeout_sec=2.0)

        with self.lock:
            self.process = None
            self.pgid = None
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


def _pgid_alive(pgid: int) -> bool:
    """Does process group `pgid` still have ANY member, per the kernel?

    signal 0 sends nothing -- os.killpg's existence/permission check alone
    decides the outcome (see man 2 kill). ESRCH (raised here as
    ProcessLookupError) means the group is empty. This is the primitive
    _capture_actually_alive_locked() and _wait_for_group_exit() both build
    on: it asks the kernel directly whether the RADIO is still held, rather
    than trusting whichever single process object this server happens to
    have a Python reference to.
    """
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # The group exists but every remaining member is owned by another
        # uid -- cannot happen under this container's design (everything in
        # the group is spawned by this same process as the same uid 1000),
        # but if it ever did, "cannot signal it" must not be conflated with
        # "cannot see it": report alive rather than silently going stale.
        return True


def _op25_alive(pgid: int) -> bool | None:
    """Is op25 (multi_rx.py) ITSELF still a member of process group `pgid` --
    as opposed to merely _pgid_alive()'s question, which only asks whether
    ANY member of the group still exists.

    This is the fix for final-review.md's finding 5: `lwin_listen_multi.sh:243`
    is `wait "${REC_PIDS[0]}"`, not a wait on op25, so op25 can die (SDR
    driver fault, a signal that only reaches it, a crash) while the launcher
    already exited and all eight recorders keep running -- keeping the group
    alive on their own, with no radio behind them at all. Before this,
    snapshot() asked only _pgid_alive(), so it kept reporting `running: true`
    for a session with a fully dead radio -- confirmed as the actual
    mechanism behind this project's multi-hour unattended radio outages (see
    the ledger's "STANDING ISSUE: op25 is not staying up on this host").

    Scoped to the GROUP with pgrep's own `-g`/`--pgroup` filter (verified on
    this host's procps-ng 4.0.4 via `man pgrep`; the capture image installs
    the same procps package -- see docker/capture/Dockerfile), never a bare
    `-f` across the whole container, so this can only ever answer about
    THIS capture's own recorders/op25, never an unrelated process.

    THREE-WAY RETURN (CORRECTED after final-review.md's round-2 re-review --
    see that file's section 8): a prior version of this function collapsed
    "pgrep confirms op25 is gone" and "pgrep itself did not work" into the
    same `False`, on the reasoning that an inability to prove op25 is alive
    must not silently read as "alive". That reasoning is still right for
    THIS function in isolation -- but the caller matters just as much: a
    bare `bool` gave `snapshot()` no way to tell a CONFIRMED death apart from
    an INCONCLUSIVE check, so it had to treat both identically. Downstream,
    `delegatedSessionLiveness()` mapped that `False` straight to `'stopped'`
    with NO retry tolerance -- unlike the adjacent 'unknown' path this same
    codebase already built `MAX_CONSECUTIVE_UNKNOWN` for, specifically so one
    blip could never look like "really stopped". A single transient `pgrep`
    spawn hiccup (not exotic on this host: op25, 8 recorders, whisper's GPU
    transcription and stt-watch all share it) would then permanently close a
    live, healthy session's DB row -- worse than the bug this function exists
    to fix, because it is non-deterministic on a currently-live capture
    rather than consistently wrong.

    So this now returns THREE distinct answers, and every caller must keep
    them distinct rather than collapsing back to a bool:
    - `True`  -- pgrep CONFIRMS op25 is a member of `pgid`.
    - `False` -- pgrep CONFIRMS it is NOT (exit 1, "no process matched" --
      pgrep's own documented meaning for that code, not an inference).
    - `None`  -- COULD NOT DETERMINE: a missing binary, this function's own
      2s timeout, or any other exit code (pgrep reporting ITS OWN failure,
      not an authoritative answer either way). This is genuinely NO
      INFORMATION, not a lean toward either alive or dead -- snapshot()
      below only ever reports `degraded: true` on a CONFIRMED `False`, never
      on `None`, so an inconclusive check produces no claim at all rather
      than a wrong one in either direction.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-g", str(pgid), "-f", "python3 multi_rx\\.py"],
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"pgrep unavailable while checking op25 liveness for pgid={pgid}: {e}")
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False  # pgrep's own documented "no process matched"
    # Any other exit code is pgrep reporting its OWN error (bad argument,
    # internal failure) -- not an authoritative "no match", and not
    # authoritative "alive" either. Genuinely unknown.
    log(
        f"pgrep exited {result.returncode} while checking op25 liveness for "
        f"pgid={pgid}: {result.stderr.decode(errors='replace').strip()}"
    )
    return None


def _wait_for_group_exit(pgid: int, timeout_sec: float) -> bool:
    """Poll (never blocking-wait) until process group `pgid` has no member
    left, up to `timeout_sec`. Polls _pgid_alive() rather than waiting on any
    single Popen object, for the same reason stop() signals the whole group
    rather than one pid: the launcher can exit while op25/recorders it
    forked are still the thing actually holding the radios."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _pgid_alive(pgid):
            return True
        time.sleep(0.2)
    return not _pgid_alive(pgid)


STATE = CaptureState()


def log(message: str) -> None:
    # Plain print, matching this repo's other long-running scripts (e.g.
    # stt_watch.py) rather than the logging module -- flush=True so lines
    # appear promptly in `docker compose logs -f capture` instead of sitting
    # in a buffer. Never logs the request body's raw bytes or the process
    # environment: the validated dict logged in start() is exactly the
    # fields ALLOWED_FIELDS permits, which holds no secrets by construction.
    print(f"capture_control: {message}", flush=True)


# --- shutdown signal handling ------------------------------------------

# Set the instant the first shutdown signal is handled. See
# _handle_shutdown_signal()'s docstring for why this guard is needed HERE
# specifically, unlike POST /stop's own accepted "two concurrent calls both
# run the ladder, harmless" case: a second signal arriving mid-ladder would
# stack a second ~10s wait on top of the first rather than merely repeating
# it, and that combined wait can exceed this service's 15s
# stop_grace_period -- letting Docker's own SIGKILL cut the graceful
# shutdown short, which is the exact outcome the SIGTERM handler exists to
# prevent.
_SHUTTING_DOWN = threading.Event()


def _stop_for_shutdown() -> None:
    """The actual body of the shutdown handler, split out from
    _handle_shutdown_signal() so it can be unit tested without exercising
    os._exit() (which would kill the test runner). Runs the SAME
    SIGINT-first-then-SIGKILL ladder as POST /stop -- letting
    lwin_listen_multi.sh's cleanup trap run -- before the caller (the signal
    handler) terminates this process. Never raises: a bug in STATE.stop()
    must not prevent the process from exiting when asked to.
    """
    log("shutdown signal received; stopping any running capture before exiting")
    try:
        STATE.stop()
    except Exception as exc:  # noqa: BLE001 -- shutdown must proceed regardless
        log(f"error while stopping during shutdown (exiting anyway): {exc}")


def _handle_shutdown_signal(signum: int, frame) -> None:  # noqa: ANN001 (stdlib signal handler signature)
    """Registered for SIGTERM and SIGINT by install_shutdown_handlers().

    See the module docstring's "SIGNAL HANDLING" section for why this must
    exist at all: with `init: true`, tini is PID 1 and forwards SIGTERM to
    THIS process (PID 2) -- but PID 2's own default disposition for SIGTERM
    is still immediate termination, and tini exits (tearing down the whole
    PID namespace, and with it op25's entire process group) as soon as this
    process does. Without this handler, the fix's second half is missing:
    tini alone gets the signal delivered, but nothing here would ever run
    the cleanup ladder before dying.

    os._exit(), not sys.exit(): this runs inside a signal handler, which in
    CPython executes as ordinary Python code in the main thread (safe to
    call blocking functions from), but raising SystemExit here would unwind
    into whatever the main thread happened to be doing when the signal
    arrived (typically socketserver's request-accept loop) with no
    guarantee it propagates cleanly all the way out. os._exit() ends the
    process immediately and unconditionally once STATE.stop() has already
    run -- there is nothing left to flush (log() already used
    flush=True) and nothing left to clean up.

    REENTRANCY GUARD, and why this case is NOT the same as POST /stop's
    accepted "two concurrent calls both run the ladder, harmless" one: a
    second signal arriving while this is still blocked inside
    STATE.stop()'s wait loop can invoke this function again on the same
    thread (CPython checks for pending signals between bytecode
    instructions, including after time.sleep() returns). Left unguarded,
    that STACKS a second ~10s wait on top of the first, rather than merely
    repeating it -- POST /stop's two-concurrent-calls case re-signals an
    already-signalled group and both callers wait roughly the SAME window,
    which is genuinely harmless. Two shutdown signals nested like this can
    approach ~20s combined, past the 15s stop_grace_period
    docker-compose.yml sets for this service -- so Docker's own SIGKILL
    would fire first and cut the graceful ladder short partway through the
    second, nested wait, which is precisely the failure this handler exists
    to prevent (see Critical #1 in task-2-review.md). So: the first signal
    runs the ladder; every signal after it, until this process actually
    exits, is a no-op.
    """
    if _SHUTTING_DOWN.is_set():
        return
    _SHUTTING_DOWN.set()
    _stop_for_shutdown()
    os._exit(0)


def install_shutdown_handlers() -> None:
    """Wire SIGTERM and SIGINT to _handle_shutdown_signal.

    SIGTERM is what `docker compose stop/restart/down` sends. SIGINT is
    handled too so a manual, interactive `docker exec ... python3
    scripts/capture_control.py` (or a plain Ctrl-C) gets the same clean
    shutdown -- mirroring scripts/udp_audio_record.py's own
    signal.signal(SIGINT)/signal.signal(SIGTERM) pair.
    """
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)


# --- HTTP layer ---------------------------------------------------------


# The whole valid request body is five short fields -- {"mode": "multi",
# "ess": true, "includeEncrypted": true, "durationSec": 86400,
# "sessionId": 123456} is well under 100 bytes. 4 KiB is generous headroom
# for that and rejects anything that
# isn't a small, legitimate /start body outright, before it is ever read into
# memory. Per the brief's own threat model ("anything that can reach the web
# app can reach this endpoint"), an unbounded read here would let a caller
# force this thread to buffer an arbitrarily large body, or claim a huge
# Content-Length and never send it -- tying the thread up indefinitely.
MAX_BODY_BYTES = 4096


class Handler(BaseHTTPRequestHandler):
    server_version = "capture-control/1.0"

    # Bounds how long a read (rfile.read in _handle_start) can block waiting
    # for bytes that never arrive -- e.g. a caller that declares a
    # Content-Length and then sends it one byte at a time, or not at all.
    # Paired with MAX_BODY_BYTES above: that bounds HOW MUCH a caller can
    # make this thread buffer, this bounds HOW LONG a caller can make it
    # wait. ThreadingHTTPServer hands each connection its own thread with no
    # cap on how many it will create, so a stuck read is not free even though
    # it only blocks one thread.
    timeout = 10

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
        raw_length = self.headers.get("Content-Length")
        try:
            # int() on a caller-controlled header: a non-numeric value (or
            # one int() otherwise chokes on) must become a clean 400, not an
            # uncaught ValueError that socketserver turns into a bare
            # traceback dumped to stdout with no HTTP response at all.
            length = int(raw_length) if raw_length is not None else 0
        except ValueError:
            self._send_json(400, {"error": "Content-Length must be an integer"})
            return
        if length < 0:
            self._send_json(400, {"error": "Content-Length must not be negative"})
            return
        if length > MAX_BODY_BYTES:
            # Reject BEFORE reading a single byte -- the whole point is to
            # never let a caller-declared size make this thread buffer (or
            # block waiting on) more than a small, legitimate request body.
            self._send_json(400, {"error": f"request body too large (max {MAX_BODY_BYTES} bytes)"})
            return

        raw = self.rfile.read(length) if length else b""
        try:
            req = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, RecursionError) as exc:
            # RecursionError alongside JSONDecodeError: a deeply nested body
            # (thousands of nested `[`) blows the parser's recursion limit
            # instead of raising a JSONDecodeError, and would otherwise hit
            # the same uncaught-exception-becomes-a-bare-traceback outcome
            # as the Content-Length cases above.
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
    # Installed before anything else: cheap, has no effect while no capture
    # is running (the common case during a StartupError crash loop below),
    # and there is no reason to leave a window where a SIGTERM would still
    # hit Python's default disposition.
    install_shutdown_handlers()

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
