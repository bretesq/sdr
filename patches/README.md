# Local patches to vendored op25

`.gitignore:61` ignores `src/`, so the op25 checkout under
`src/op25/` is **not tracked by this repository**. Any local change to it is
invisible to git and is lost the moment op25 is re-cloned or reset. These patch
files are the only record.

**Re-apply all of them after any op25 re-clone, `git reset`, or rebuild:**

```bash
cd /home/besquivel/rtl/src/op25/op25/gr-op25_repeater/apps
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
**exits non-zero**, so a bare `|| echo FAILED` would cry wolf every time the
loop is re-run. Check the real state afterwards with:

```bash
grep -c "leaving it unclaimed" tk_p25.py    # expect 1
```

These are runtime Python changes under `apps/`, so **no cmake rebuild is
needed** — `multi_rx.py` does `importlib.import_module('tk_p25')` with the apps
directory on `sys.path`. (A patch to `lib/*.cc` would need a rebuild; none here
do.) There is no other `tk_p25.py` on this system that could shadow it.

Patches that are *not* recorded here but are described in prose:
`README.md` gotcha #4 (op25's `cmake_policy(SET CMP0026 OLD)` vs CMake 4.2) is a
build-system change under `src/op25/CMakeLists.txt`, unrelated to these.

---

## `op25-tk_p25-release-unreachable-grant.patch`

**What:** `tk_p25.py tune_voice()` ignored the return value of
`frequency_set`, which is `multi_rx.change_freq` and returns `False` when the
requested frequency lies outside the device's usable window
(`p25_demodulator_dev.set_relative_frequency` refuses, and the device is not
`tunable`). The talkgroup was then claimed anyway at the end of the method
(`talkgroups[tgid]['receiver'] = self`, tk_p25.py:2345).

**Effect of the bug:** that receiver records silence and stays occupied for the
whole call, and because the talkgroup is claimed no other receiver will take
it. The call is lost more thoroughly than if the band were simply not covered.

**Why it matters here:** LWIN site 13 splits its voice across 769–772 MHz and
851–860 MHz, 87 MHz apart — no HackRF sample rate spans both, so voice
receivers necessarily sit on two devices covering different bands. Roughly 73%
of grants land on the 800 MHz leg. Without this patch, every 800 MHz grant
handed to a 700 MHz receiver is a lost call.

**Fix:** return before the claim, leaving the grant unclaimed so a receiver on
the other band picks it up on the next `scan_for_talkgroups`. `is False` rather
than a truth test, so a `frequency_set` returning `None` keeps the old
behaviour — only `multi_rx.change_freq` returns a bool, and `tk_p25.py` is used
only by `multi_rx.py` (`rx.py` uses `trunking.py` instead).

**Upstream:** not submitted. Worth reporting to boatbod/op25; the bug is
generic to any multi-band, multi-device trunking config, not specific to LWIN.
