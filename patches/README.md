# Local patches to vendored op25

`.gitignore:61` ignores `src/`, so the op25 checkout under `src/op25/` is **not
tracked by this repository**. Any local change to it is invisible to our git and
is lost the moment op25 is re-cloned or reset. These patch files are the only
record.

op25 *is* its own git checkout, so each patch here is generated with
`git -C src/op25 diff` against a named upstream commit rather than hand-rolled.
That makes it authoritative: it applies to pristine upstream and reproduces our
file byte-for-byte, which is verified below.

**Re-apply after any op25 re-clone, reset or rebuild:**

```bash
cd /home/besquivel/rtl/src/op25
for p in /home/besquivel/rtl/patches/op25-*.patch; do
  if patch -p1 --forward --dry-run -i "$p" >/dev/null 2>&1; then
    patch -p1 --forward -i "$p" && echo "applied: $(basename "$p")"
  else
    echo "already applied, or does not apply: $(basename "$p")"
  fi
done
```

The dry run first is deliberate: `patch --forward` on an already-applied patch
prints `Reversed (or previously applied) patch detected! Skipping patch.` and
**exits non-zero**, so a bare `|| echo FAILED` would cry wolf on every re-run.
Note `-p1` from `src/op25`, not from `apps/` — these are `git diff` paths.

Verify afterwards:

```bash
cd /home/besquivel/rtl/src/op25/op25/gr-op25_repeater/apps
grep -c "leaving it unclaimed" tk_p25.py   # expect 1
grep -c "def can_reach" tk_p25.py          # expect 1
```

`scripts/lwin_listen_multi.sh` checks both markers at startup and refuses to run
without them, because every failure mode below is **silent**.

These are runtime Python changes under `apps/`, so **no cmake rebuild is
needed** — `multi_rx.py` does `importlib.import_module('tk_p25')` with the apps
directory on `sys.path`, and there is no other `tk_p25.py` on this system that
could shadow it. (A patch to `lib/*.cc` would need a rebuild; none here do.)

Not recorded here: `README.md` gotcha #4 (op25's `cmake_policy(SET CMP0026 OLD)`
vs CMake 4.2) is a build-system change under `src/op25/CMakeLists.txt`, and
`src/op25` carries several other local modifications visible via
`git -C src/op25 status` that predate this work.

---

## `op25-tk_p25-multiband-receiver-pool.patch`

Generated against upstream **`71abcd0`** ("Additional -v11 debug of terminal
commands"). Verified: applies cleanly to `71abcd0:…/tk_p25.py` and reproduces
our working file byte-for-byte.

**Why any of this is needed.** LWIN site 13 splits voice across 769–772 and
851–860 MHz, 87 MHz apart, which no HackRF sample rate spans. Voice receivers
therefore sit on two non-tunable devices covering different bands, and about
three quarters of grants land on the 800 MHz leg. `tk_p25.py`'s receiver pool
was written for receivers that can all reach everything.

It makes three related changes to `p25_receiver`:

**1. `find_talkgroup` becomes window-aware (`can_reach`).** The pool picked by
priority and claim status alone, with no notion that a non-tunable device only
covers `centre ± ((rate × usable_bw)/2 − if_rate/2)`. Receivers on one band kept
claiming grants on the other, failing to tune, releasing, and being unavailable
when a reachable grant arrived — measured 1,300 tune attempts producing 6 calls
on the 700 leg, against 60 attempts producing 10 after the fix, and
`Unable to tune` went from 1,243 to 0.

Driven by `freq_min`/`freq_max` in the channel config, which
`scripts/make_multirx_cfg.py` emits from the demodulator's own bound. Both
default to 0, which disables the check and restores upstream behaviour, so the
change is inert for any config that omits them.

**2. `tune_voice` returns a status.** It ignored the return of `frequency_set`
(`multi_rx.change_freq`), which is `False` when the frequency lies outside the
device window, and claimed the talkgroup anyway
(`talkgroups[tgid]['receiver'] = self`). The receiver then recorded silence for a
whole call *and* blocked any other receiver from taking it — losing the call more
thoroughly than not covering the band at all. It now returns before the claim.

`is False` rather than a truth test: only `multi_rx.change_freq` returns a bool,
and a `frequency_set` returning `None` must keep upstream behaviour. `tk_p25.py`
is used only by `multi_rx.py` (`rx.py` uses `trunking.py`), so `rx.py` is
unaffected.

**3. `scan_for_talkgroups` honours that status.** It logged a `voice update`
line, called `log_call()` and called `meta_update()` unconditionally around
`tune_voice`, so an unreachable grant still produced a logged call the receiver
never took — and `scripts/op25_log.py` latched that tgid/freq against the
receiver id for `TG_TTL`, so any audio still arriving on that channel's UDP port
from its *previous* frequency was written out under the wrong talkgroup. All
three side effects now happen only after a successful tune.

Change 3 matters most when `freq_min`/`freq_max` are **absent**: change 1 is then
disabled, so the unreachable path fires on most cross-band grants and change 2
alone leaves the mislabelling in place. With our own config the path is not
exercised at all (measured: 0 occurrences).

**Upstream:** not submitted. Worth reporting to boatbod/op25 — the bug is generic
to any multi-band, multi-device trunking config, not specific to LWIN.
