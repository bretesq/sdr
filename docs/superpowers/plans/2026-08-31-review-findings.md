# Review findings — `2026-08-31-web-console-migration.md`

**Reviewer:** verification pass against the live repo at `/home/besquivel/rtl`
**Date:** 2026-08-31
**Continues from:** B1, B2 (delivered inline) and B3's diagnosis (delivered inline).

> Scope note: B1 (`tgid` absent from `reference/lwin_talkgroups.json` entries), B2 (`enc` is
> `'clear'|'partial'|'full'`, not `'encrypted'`) and B3's *diagnosis* were delivered inline and
> are not repeated. This file starts at B3's recommended fix.

---

## 1. B3 — BLOCKER — recommended fix

**Location:** Task 6 Step 4 (`startListening`), Task 6 Step 4 (`readTail`), Task 7 Step 3 (`status.get.ts`)

### Decision: redirect the child's stdout/stderr to a **truncated per-session** `web/listen.log`.

Keep `countCalls` exactly as the plan has it — its regex and `new Set(...).size` dedupe are correct.
Only the *input* needs fixing.

### Why not the `recordings/*.wav` mtime alternative

Counting `recordings/*.wav` with `start >= session.startTime` was the other candidate. Rejected:

- It re-`readdirSync`es a **3,232-entry** directory on every 5-second poll (and the directory grows
  during the session), where reading the tail of one file is O(cap).
- `start` comes from `parseRecordingFilename`, which is itself broken by the timezone bug (**M5**) —
  so the comparison against `session.startTime` would be off by 5 hours until M5 is fixed, silently
  counting either everything or nothing.
- It cannot distinguish a call recorded by *this* session from one recorded by a CLI session running
  concurrently.
- The log is needed anyway: without a redirect there is no diagnostic record of a session at all,
  which is a straight regression from `server.py`.

Truncating at session start also makes the count *per-session*, fixing the second half of the
diagnosis (`'ab'` made the old log cumulative — 3,150 distinct `.wav` names today).

### `server/utils/processes.ts`

```typescript
import { spawn } from 'node:child_process'
import { readFileSync, statSync, openSync, readSync, closeSync } from 'node:fs'
import { join } from 'node:path'
import { scriptsDir, sdrRoot, listenLogPath } from './paths'

export function startListening(opts: ListenOptions): { pid: number; config: ListenOptions } {
  // 'w' truncates: the log covers exactly this session, so countCalls() starts at 0.
  const fd = openSync(listenLogPath(), 'w')
  try {
    const child = spawn(
      'bash',
      [join(scriptsDir(), 'lwin_listen.sh'), ...buildListenArgs(opts)],
      {
        cwd: sdrRoot(),
        detached: true,          // own process group, so we can signal the whole tree
        stdio: ['ignore', fd, fd],
      },
    )
    child.unref()

    if (!child.pid) throw new Error('failed to spawn lwin_listen.sh')
    return { pid: child.pid, config: opts }
  } finally {
    // The child holds its own dup of the fd. Not closing leaks one fd per session.
    closeSync(fd)
  }
}
```

`readTail`'s default cap must also rise. A 3,000-call session log measured ~500 KB (the current
`web/listen.log` is 502,434 bytes for ~3,150 calls, i.e. ~160 bytes/call including the paired
`stt_watch:` lines). At 256 KB the tail silently drops roughly half a long session:

```typescript
/** Read the tail of a file without loading the whole thing. */
export function readTail(path: string, maxBytes = 4 * 1024 * 1024): string {
```

4 MB covers ~25,000 calls — far beyond any realistic session — and is cheap to read once every
5 seconds. (If you would rather not read even that: record `logOffset: number` on the `Session` at
start and read only `[logOffset, size)`. Given truncation makes the offset always 0, the cap is the
simpler mechanism.)

### `server/api/listen/status.get.ts`

No structural change; it already calls `countCalls(readTail(listenLogPath()))`. Add one guard so a
stale log from a previous run cannot be attributed to a session that is not running:

```typescript
import { countCalls, readTail } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'
import { listenLogPath } from '~/server/utils/paths'

export default defineEventHandler(() => {
  const session = sessionStore.get()

  return {
    success: true,
    data: {
      running: session !== null,
      pid: session?.pid ?? null,
      config: session?.config ?? null,
      // Only report a count while a session owns the log; otherwise 0.
      callCount: session ? countCalls(readTail(listenLogPath())) : 0,
      startTime: session?.startTime ?? null,
      lastUpdate: Date.now() / 1000,
    },
  }
})
```

(The plan already had the `session ? ... : 0` ternary — it is correct and should be kept, not
"simplified" away during implementation.)

### Also update Task 14 Step 1

Its conditional is an unresolved placeholder:

> `scripts/lwin_listen.sh` may write `web/listen.log`. If so, keep that path…

It does **not**. Verified: the string `listen.log` appears nowhere in `scripts/lwin_listen.sh`. The
log exists only because `server.py` did:

```python
with open(self.logpath, 'ab') as log:
    self.proc = subprocess.Popen([...], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
```

Rewrite the step to state that the Nitro server owns the log, and that `web/listen.log` must stay in
`.gitignore` (it already is, along with `web/listen.pid`, `web/listen.config.json`,
`web/listen.started`, `web/server.lock`, `web/server.pid`).

---

## 2. BLOCKERs beyond B1–B3

### B4 — BLOCKER — Task 5's test asserts a pass that fails on its own fixture

**Location:** Task 5 Step 1 (fixture) and Task 5 Step 4 (expected count)

The fixture uses `cat: 'EBR (17) BR Police'`. That string contains **none** of the 13
`BR_AREA_KEYWORDS` — not `'East Baton Rouge'`, not `'Baton Rouge'`. So `filterByArea(tgs, 'br')`
returns `[]` and this assertion fails against a correct implementation:

```typescript
it('keeps Baton Rouge categories by substring match for area=br', () => {
  const out = filterByArea(tgs, 'br')
  expect(out.map(t => t.tgid)).toContain(17165)   // FAILS: out is []
})
```

Step 4 also says "Expected: 5 passed" when the block contains 4 `it()`s (2 + 2).

An implementer following TDD will burn time debugging correct code against a broken fixture — the
worst failure mode a plan can have.

**Fix** — use the real values (verified from `reference/lwin_talkgroups.json`):

```typescript
const tgs: TalkgroupEntry[] = [
  { tgid: 17165, alpha: '17-BRPD DSP1', desc: 'Dispatch 1',
    cat: 'East Baton Rouge Parish (17) - Baton Rouge Police', enc: 'partial' },
  { tgid: 6039,  alpha: 'LDWF R4-DISP', desc: 'Region 4 Dispatch',
    cat: 'Dept. of Wildlife and Fisheries - Region 4 (Lafayette)', enc: 'clear' },
]
```

Note two knock-on edits: the `filterTalkgroups(tgs, { category: 'LDWF' })` assertion must use the
long category string, and `filterByArea(tgs, 'br')` now returns **both** rows (6039's real category
contains `'Wildlife and Fisheries'`, which *is* a BR_AREA keyword) — so assert `toContain(17165)`
rather than a length. Correct the expected count to 4.

No blockers beyond B1–B4.

---

## 3. MAJOR findings

### M1 — Dropping the pidfile lets two op25 instances fight over the HackRF

**Location:** Task 6 Step 5 (`server/utils/session.ts`); spec § "Loss of State"

`server.py` read `web/listen.pid` from disk, so a session survived a server restart and stayed
stoppable. The plan's module-level `let current: Session | null = null` means that after any Nitro
restart (production restart, or a dev-server reload) `sessionStore.isRunning()` returns `false`
**while `lwin_listen.sh` is still running**.

Press Start and you spawn a second `rx.py` against the same HackRF. Then whichever session stops
first runs `lwin_listen.sh`'s `cleanup`, which does an unscoped
`pkill -f "gr-op25_repeater/apps/rx.py"` — killing the *other* session's receiver too.

This is a hardware-contention bug, not the benign "state loss" the spec signs off on, and it is a
regression from behavior that works today.

**Fix:** keep writing the three sidecar files exactly as `server.py` did — `web/listen.pid`,
`web/listen.started`, `web/listen.config.json` — and have `sessionStore.get()` fall back to reading
them when the in-memory slot is empty. ~15 lines, and it restores stop-after-restart. Note that this
also makes the `/proc/<pid>/stat` zombie check in `isProcessRunning` meaningful again (see the SIGINT
section below for why it is otherwise moot).

Minimum acceptable alternative if the in-memory design is kept deliberately: a pre-spawn
`pgrep -f lwin_listen.sh` guard in `start.post.ts` that returns 409.

---

### M2 — `$fetch` catch surfaces the HTTP status line, never the handler's error message

**Location:** Task 10 Step 1 (`start()` and `stop()`), and the explanatory note directly beneath it

The plan asserts:

> `$fetch` throws on non-2xx by default, so the 400/409 branches surface through the `catch`.

They surface, but not usefully. ofetch throws a `FetchError` whose `.message` is
`[POST] "/api/listen/start": 409 Conflict`; the parsed response body is on `.data`. So every
carefully-worded server message is invisible:

- `'A listening session is already running'`
- `'Pick a preset or enter talkgroup IDs'`
- `` `Unknown preset: ${body.preset}` ``
- `'Talkgroups must be a comma-separated list of numbers'`
- `'Duration must be a positive integer'`

Because the plan states this works, the implementer will not check it. Same bug in `stop()`.

**Fix** — no `as any` (per Global Constraint line 20):

```typescript
function apiError(e: unknown, fallback: string): string {
  if (e && typeof e === 'object' && 'data' in e) {
    const d = (e as { data?: unknown }).data
    if (d && typeof d === 'object' && 'error' in d
        && typeof (d as { error?: unknown }).error === 'string') {
      return (d as { error: string }).error
    }
  }
  return e instanceof Error ? e.message : fallback
}
```

Then `error.value = apiError(e, 'Failed to start')` in both handlers.

---

### M3 — `mergeCalls` uses `as any`, violating CLAUDE.md and the plan's own constraint

**Location:** Task 4 Step 3, and Task 4's **Prerequisite**

```typescript
// eslint-disable-next-line @typescript-eslint/no-explicit-any
;(merged as any)[key] = v
```

Two problems: the plan's Global Constraints (line 20) say *"All types explicitly declared — no
`any`"*, and the `eslint-disable` names a rule from an ESLint setup that no task installs, so it is
noise.

The generic key-walk is not needed. Verified shape of `recordings/calls.json`: a flat **array** of
2,953 objects with exactly `file, tgid, alpha, desc, enc, cat, start, dur` — and **no `transcript`
field**. So enumerate:

```typescript
return recordings.map((rec) => {
  const c = byFile.get(rec.file)
  if (!c) return rec
  return {
    ...rec,
    tgid:       c.tgid       ?? rec.tgid,
    alpha:      c.alpha      ?? rec.alpha,
    desc:       c.desc       ?? rec.desc,
    cat:        c.cat        ?? rec.cat,
    enc:        c.enc        ?? rec.enc,
    start:      c.start      ?? rec.start,
    dur:        c.dur        ?? rec.dur,
    transcript: c.transcript ?? rec.transcript,
    file:       rec.file,
  }
})
```

**Also correct the Prerequisite.** It reads *"it may be an array or an object keyed by filename."*
It is an array — written **once**, in `udp_audio_record.py`'s `finally` block
(`json.dump(calls, open(os.path.join(OUT, 'calls.json'), 'w'), indent=1)`), and **truncated to that
session's calls only**. The object-shaped branch and its test are dead code documenting behavior
that never occurs. Two consequences worth stating in the plan:

- During a live session `calls.json` still holds the *previous* session's calls, so new recordings
  show `dur: 0` and a filename-derived `start` until the session ends. (`server.py` behaved the
  same way — not a regression, but it should be documented, not discovered.)
- `stt_watch.py` merges `transcript` into `calls.json`, but `udp_audio_record.py` rewrites the file
  at session end and clobbers those merges. Hence no `transcript` key in the live file today. The
  `.txt` fallback fetch in `RecordingsList` is therefore load-bearing, not a nicety.

---

### M4 — Three CLI capabilities of the existing console are silently dropped

**Location:** Task 6 Step 4 (`buildListenArgs`); Task 10 Step 1 (`ListenControl`)

`web/server.py` builds `--tag`, `--match` and `--all-areas`; `web/index.html` has inputs for all
three; all three exist in `scripts/lwin_listen.sh`'s arg loop. The plan's `buildListenArgs` emits
none of them and never says it is dropping them.

`--all-areas` is the significant loss: **statewide recording becomes impossible from the console.**

**Fix:** already given in the B2 `ListenOptions` / `buildListenArgs` block delivered inline. If any
of the three is being dropped deliberately, it belongs in the plan's *Known Gaps* section as a
decision rather than an omission.

---

### M5 — Filename timestamps parsed as UTC but written as local: ~279 recordings display 5 hours early

**Location:** Task 3 Step 3 (`parseRecordingFilename`), and the explanatory note above it

The note frames `Date.UTC` as a determinism choice:

> Timestamps in filenames are local wall-clock from `udp_audio_record.py`. This implementation
> treats them as UTC for determinism.

It is not determinism, it is an off-by-timezone. Verified against the live data:

| Source | Value for filename stamp `20260830-222255` |
|---|---|
| `calls.json` (`TG17278_17-EMS-COMN_20260830-222255.wav`) | `1788146575.6002645` |
| Python local (`strptime(...).timestamp()`, what `server.py` used) | `1788146575` ✓ |
| Plan's `Date.UTC(...)` | `1788128575` ✗ (18,000 s = 5 h earlier) |

`mergeCalls` overwrites `start` for the 2,953 files present in `calls.json`, so the bug is invisible
on the happy path. But `recordings/` holds **3,232 `.wav` files**, so roughly **279 older recordings
render 5 hours off** — the worst kind of bug: correct-looking in the common case.

**Fix** (one line, one place, as the note itself anticipates):

```typescript
const start = new Date(
  Number(y), Number(mo) - 1, Number(d),
  Number(h), Number(mi), Number(s),
).getTime() / 1000
```

Sort order is unaffected — the shift is uniform, so `scanRecordings`'s `sort((a,b) => b.start - a.start)`
before the merge still yields the right relative order.

**Related, lower priority:** `udp_audio_record.py:86` can emit `TGunknown_<stamp>.wav` when no
talkgroup grant is matched. The plan's `NAME` regex requires `TG(\d+)`, so such a file gets
`{tgid: null, start: 0}` — and `safeRecordingPath`'s `RECORDING_NAME` rejects it outright, making it
**unplayable**. `server.py` handled this: it used a separate `TS = re.compile(r'\d{8}-\d{6}')` regex
to recover the timestamp regardless. Verified 0 such files exist today, so this is a note or a
one-line regex widening, not a rework.

---

### M6 — PrimeFlex 3 theme classes will not resolve under PrimeVue 4's Aura

**Location:** Task 1 Step 2 (`css:` array); Tasks 10, 11, 12, 13 templates

**Mechanism.** PrimeFlex 3 emits declarations that reference the *PrimeOne v3* CSS variable names:

- `.surface-card` → `background-color: var(--surface-card)`
- `.surface-100` → `background-color: var(--surface-100)`
- `.text-color-secondary` → `color: var(--text-color-secondary)`
- `.border-round` → `border-radius: var(--border-radius)`

PrimeVue 4's Aura preset emits `--p-`-prefixed design tokens (`--p-content-background`,
`--p-surface-100`, `--p-text-muted-color`, `--p-content-border-radius`, …) and does **not** define
the legacy unprefixed names. PrimeFlex is deprecated in v4 in favour of Tailwind. So those four
classes resolve to nothing.

**What still works** — every pure-layout class the plan uses is unaffected, because they compile to
literal CSS with no variable indirection:

`grid`, `col-12`, `lg:col-4`, `lg:col-8`, `flex`, `flex-column`, `flex-wrap`, `flex-1`, `gap-2`,
`gap-3`, `p-3`, `p-4`, `m-0`, `mt-0`, `mt-1`, `mt-2`, `mt-3`, `mb-0`, `mb-1`, `mb-2`, `mb-3`,
`mb-4`, `w-full`, `w-10rem`, `w-12rem`, `w-14rem`, `block`, `text-sm`, `text-base`, `text-xl`,
`text-2xl`, `text-3xl`, `font-bold`, `line-height-3`, `align-items-center`, `justify-content-between`.

So the responsive three-panel layout is fine. What breaks is chrome and one acceptance criterion.

**Consequences with teeth:**

1. `rowClass` in `TalkgroupBrowser` returns `'surface-100'` — so the plan's own acceptance criterion
   *"Talkgroups: … whitelist highlighting all work"* **fails** even after B1 is fixed. (The
   `Tag value="active" severity="info"` badge still renders, so the failure is partial and easy to
   wave past.)
2. All three `section class="p-4 border-round surface-card"` panels lose their background and corner
   radius, against a `<body>` that Nuxt + PrimeVue 4 never styles at all — the plan adds no global
   CSS, so the page is unstyled-white with unstyled-white panels.
3. `text-color-secondary` (used in `ListenControl`, `pages/index.vue`, `RecordingsList`,
   `TalkgroupBrowser`) inherits normal body colour, flattening the visual hierarchy.
4. `surface-100` also styles the running-session status block in `ListenControl`.

**Honesty note for the implementer:** this is the one finding in this review asserted from mechanism
rather than from a file read in this repo. Do **not** install Nuxt to pre-prove it. Confirm in 30
seconds at Task 10 Step 2: open devtools, inspect a panel, and look for an unresolved `var()` on
`background-color`.

**Fix — pick one:**

*(a) Shim the legacy names.* Smallest diff, keeps every template as written. Add
`assets/css/compat.css` and put it first in the `css:` array:

```css
:root {
  --surface-card: var(--p-content-background);
  --surface-100: var(--p-surface-100);
  --surface-border: var(--p-content-border-color);
  --text-color: var(--p-text-color);
  --text-color-secondary: var(--p-text-muted-color);
  --border-radius: var(--p-content-border-radius);
}
body { margin: 0; background: var(--p-content-background); color: var(--p-text-color); }
```

*(b) Drop the theme classes.* Use v4 tokens directly in each component's `<style scoped>` and keep
PrimeFlex only for layout.

Either way, add the explicit `body` rule — it is missing regardless of which path you take.

---

### M7 — Nuxt at the repo root will watch 221 MB / 6,463 files in `recordings/`

**Location:** Task 1 Step 2 (`nuxt.config.ts`); Global Constraints line 21

The plan puts the Nuxt app at `/home/besquivel/rtl`, making `srcDir` the repo root. Nuxt 3 does not
read `.gitignore` for its watch `ignore` list. Measured contents of that root:

| Directory | Size | Notes |
|---|---|---|
| `recordings/` | 221 MB | 6,463 files, and **growing during every recording session** |
| `src/` | 81 MB | op25 / GNU Radio source tree |
| `results/` | 55 MB | op25 logs |
| `reference/` | 944 KB | |
| plus | | `models/`, `captures/`, `tools/`, `web/`, 12 root `*.tsv` files |

Two distinct problems: chokidar/inotify registration cost at boot (slow `npm run dev`, and on WSL2 a
real risk of `ENOSPC: System limit for number of file watchers`), and — worse — every new `.wav`
that `udp_audio_record.py` flushes during a live session fires a watcher event on the dev server
while you are using the app to run that very session.

**Fix:**

```typescript
ignore: [
  'recordings/**', 'results/**', 'captures/**', 'models/**',
  'src/**', 'reference/**', 'web/**', 'tools/**', 'docs/**',
  '*.tsv', '*.txt', '*.json',
],
```

(Scope the last three carefully so `package.json` / `tsconfig.json` are not swept up — prefer
explicit root-level globs like `lwin_*.tsv`, `lwin_*.txt`, `lwin_keys.json`.)

---

### M8 — `stopListening`'s SIGINT fallback can throw uncaught, turning a success into a 500

**Location:** Task 6 Step 4 (`stopListening`)

```typescript
try {
  process.kill(-pid, 'SIGINT')     // negative pid = process group
} catch {
  process.kill(pid, 'SIGINT')
}
```

If the group exits between the `isProcessRunning(pid)` guard and this call — a live race, since
`--stt` sessions and duration-limited sessions exit on their own — `kill(-pid)` throws `ESRCH`, and
the fallback throws `ESRCH` too. That escapes the `catch` with nothing to catch it, propagates out of
`stop.post.ts`'s `try`, and returns a 500 for what was actually a clean stop.

**Fix:**

```typescript
try {
  process.kill(-pid, 'SIGINT')
} catch {
  try { process.kill(pid, 'SIGINT') } catch { /* already gone — nothing to stop */ }
}
```

(Note the plan's `SIGKILL` fallback later in the same function is already correctly guarded, so this
is the only unguarded signal.)

---

### M9 — `/api/talkgroups/list` has no sort; correctness depends on a V8 implementation detail

**Location:** Task 9 Step 1 (`server/api/talkgroups/list.get.ts`), via `loadTalkgroups` in Task 5 Step 3

`server.py` sorted explicitly: `tgs.sort(key=lambda t: t['tgid'])`. The plan relies on
`Object.values(raw)` order. That *happens* to come out ascending-by-tgid only because V8 treats
integer-like string keys (`"1"`, `"5000"`, `"17165"`) as array indices and iterates them in ascending
numeric order ahead of string keys. That is engine behavior, not a language guarantee for this case,
and it silently breaks the moment a non-integer key appears in the DB.

**Fix:** fold an explicit sort into `loadTalkgroups` (already shown in the B1 fix):

```typescript
.sort((a, b) => a.tgid - b.tgid)
```

Also worth stating in the plan: `filterByArea` selects **601 of 4,163** talkgroups for `area=br`
(verified), which exactly matches the 601 lines in `lwin_active_whitelist.txt`. Task 12 Step 2's
"Statewide shows the full ~4163" is correct; add "and BR shows 601" so the check is falsifiable.

---

## 4. MINOR findings

| ID | Location | Issue | Fix |
|---|---|---|---|
| M10 | Task 10 Step 1 | `preset` starts `null` with `show-clear`; `start.post.ts` 400s when neither preset nor talkgroups is set → fresh page + Start fails. Old console defaulted to `all`. | `const preset = ref<string \| null>('all')` |
| M11 | Tasks 11, 12 | Search and filters narrower than the old console. | See § 5 items 4–7 |
| M12 | Task 8 Step 3 | `/api/recordings/search` is dead code — `RecordingsList` filters entirely client-side and never calls it. | Wire it up (with debounce) or drop it from Task 8 and the route list |
| M13 | Task 1 Step 1 | `nuxt ^3.14.0` (Nov 2024) against the installed Node **v24.15.0**. | Bump to a current 3.x, or pin Node to 22 LTS |
| M14 | Task 1 Steps 6–7 | No lockfile exists, so per CLAUDE.md this is a **pnpm** project (pnpm 11.17.0 is installed); the plan uses `npm install` and commits `package-lock.json`. | `pnpm install` / `pnpm dev`; commit `pnpm-lock.yaml` |
| M15 | Task 1 Step 2 | `runtimeConfig.sdrRoot` is defined but never read; `paths.ts` reads `process.env.SDR_ROOT` directly, and Nuxt maps that runtimeConfig key to `NUXT_SDR_ROOT` anyway. | Delete the runtimeConfig entry, or read it via `useRuntimeConfig()` in `paths.ts` |
| M16 | Task 2 Step 2 | No `vitest.config.ts`; vitest's default excludes do not cover `.nuxt/`, which bare `npx vitest run` (Task 14 Step 5) will scan. | Add `vitest.config.ts` with `test: { include: ['server/**/*.test.ts'] }` when vitest is introduced |
| M17 | Task 8 Step 4 | `sendStream` is deprecated in h3 v1 (still functional); the manual `Content-Length` alongside it is redundant. | `return createReadStream(path, { start, end })` — h3 handles a Node `Readable` return |
| M18a | File Structure block | `layouts/default.vue` is listed but no task creates it, and `app.vue` is just `<NuxtPage />` with no `<NuxtLayout>`. | Remove from the structure block, or add the layout + wrapper |
| M18b | Task 8 Step 1 | Creates `server/utils/recordings.ts`, which is absent from Task 8's **Files** and **Interfaces** headers. | Add it to both |
| M18c | File Structure vs Task 14 Step 4 | Structure says `web/server.py — archive at the end`; Task 14 `git rm`s it. Also leaves `web/__pycache__/`, `web/server.pid`, `web/server.lock` behind. | Pick one word; add the leftovers to the removal list |
| M18d | Task 14 Step 1 | Unresolved placeholder ("may write `web/listen.log`"). | Resolved in § 1 above |
| M19 | Task 3 Step 4, Task 5 Step 4 | Stated pass counts: Task 3 says 4 (correct); Task 4 says 8 (correct); Task 6 says 6 (correct); **Task 5 says 5 but has 4**. | Correct Task 5 to 4 |

---

## 5. `web/index.html` gap analysis

I read `web/index.html` (13,301 bytes) in full. The existing console does the following that the
plan does not, in rough order of user impact.

### Capability losses (you cannot do this any more)

1. **Statewide recording.** `<label class="chk"><input type="checkbox" id="allAreas"> all areas
   (statewide)</label>` → `all_areas` → `--all-areas`. The plan has no equivalent control and
   `buildListenArgs` never emits the flag. This is the single biggest drop. *(= M4)*

2. **Tag-based talkgroup selection.** `<input type="text" id="tag" placeholder='e.g. "Law Dispatch,Law Talk"'>`
   → `--tag`. Gone. Note the reference DB has a real `tag` field on every entry
   (`"tag": "Law Dispatch"`), and `make_whitelist.py`'s `PRESETS` are themselves just named tag
   lists — so `--tag` is how you build a selection the presets do not cover. *(= M4)*

3. **Regex match selection.** `<input type="text" id="match" placeholder="e.g. BRPD">` → `--match`,
   a regex over alpha / description / category. Gone. *(= M4)*

4. **Independent partial + encrypted checkboxes.** `#incPartial` and `#incFull` are two separate
   checkboxes, and `make_whitelist.py` treats the flags independently (`allowed.add('full')` only
   under `--include-encrypted`; `'partial'` only under `--include-partial`). The plan's radio group
   makes them mutually exclusive, so "partial **and** fully encrypted" is unreachable, and choosing
   `encrypted` sends *only* `--include-encrypted` → clear + full but **not** partial. *(covered in
   B2; listed here for completeness of the gap analysis)*

### Search / filter regressions

5. **Recordings search covered six fields, not three.** The old console:

   ```javascript
   const hay = [c.alpha, c.desc, c.cat, c.transcript, c.file, c.tgid]
     .map(x => (x || '').toLowerCase()).join(' ');
   return hay.includes(q);
   ```

   The plan searches `tgid` / `alpha` / `desc` only. **Losing transcript search is the big one** —
   it is arguably the most valuable feature of the whole panel and the entire point of `--stt`
   (there are 3,231 `.txt` transcripts on disk). Losing `cat` and `file` matters less but is free to
   restore. Placeholder text in the old UI advertised it: *"filter recordings by alpha / description
   / category / transcript"*.

6. **Recordings encryption filter had five options, using the real vocabulary.**
   `<option value="">all encryption types</option>`, `clear`, `partial`, **`full`**, and
   **`none`** ("unknown / unlabelled", implemented as `if (enc === 'none') { if (c.enc) return false; }`).
   The plan has four and uses a value (`encrypted`) that matches nothing. Given 856 talkgroups are
   `full`, and that unlabelled recordings do occur, both the `full` rename and the `none` option are
   needed. *(the rename is B2; the `none` option is new here)*

7. **Talkgroups panel had its own encryption filter.** `<select id="tgEnc">` with clear / partial /
   full. The plan's `TalkgroupBrowser` offers area + category + text only — so you cannot answer
   "show me the encrypted talkgroups in EBR", which is a core question for this project (see
   `OBSERVATIONS.md §5`, referenced from `lwin_listen.sh`'s own help text).

8. **Talkgroup search included the `tag` field.**
   `[t.alpha, t.desc, t.cat, t.tag, t.tgid]`. The plan's `Talkgroup` interface omits `tag`
   altogether — it is neither returned by `/api/talkgroups/list` (which projects only
   `tgid/alpha/desc/cat/enc`) nor displayed nor searched. `server.py` returned it, along with
   `mode`. Worth restoring both: `mode` is how you see `"D enc"` vs `"D"`.

### UI affordances dropped

9. **`[BLANK_AUDIO]` transcripts were visually distinguished.**
   `const blank = (c.transcript || '').startsWith('[BLANK_AUDIO]')` → `.tx.blank` (dim + italic).
   This matters a lot in practice: op25's `-n` silences encrypted bursts, so partial-encryption
   talkgroups produce many calls that transcribe to exactly `[BLANK_AUDIO]`. Without the styling
   they are indistinguishable from real content at a glance. The plan renders all transcript text
   identically.

10. **The status line showed the running config and pid.**
    `` `<span class="on">RUNNING</span> (pid ${st.pid}) ${esc(bits.join(' · '))}` `` with
    `since <time>` and `<n> calls so far`. The plan's `status.get.ts` *returns* `config`, but
    `ListenControl` never renders it — so you can see that a session is running but not **what it is
    following**. Cheap to add and directly useful.

11. **Stop refreshed the recordings list.**
    ```javascript
    async function stopListen() {
      const r = await postJSON('/stop');
      pollStatus();
      setTimeout(loadRecordings, 1500);   // let the recorder flush its last call
    }
    ```
    The plan's `stop()` calls only `refresh()` (status). Since `calls.json` is written **only** at
    session end, this refresh is exactly when the new metadata becomes available — dropping it means
    a session's recordings never appear without a manual click. Worth copying verbatim, comment
    included.

12. **Per-row inline audio players.** The old console rendered `<audio controls preload="metadata">`
    on every row, so you could scan and play without opening anything. The plan requires a click into
    a `Dialog` per recording. This is a deliberate design change rather than an oversight (and the
    Dialog carries more metadata), but it is a real workflow change on a list of 3,232 items and
    should be an acknowledged decision. Note `preload="metadata"` is also how the old UI got the
    browser to show real durations for the ~279 recordings whose `calls.json` `dur` is missing.

13. **Talkgroup count hint.** `showing ${rows.length} of ${tgAll.length} talkgroups … · ${wl.size}
    in the current active whitelist`. The plan's static footer sentence carries no counts. Minor,
    but it is how you notice at a glance that B1-style filtering has silently emptied the table.

### Things the plan improves on (for balance)

- Collapsing `/audio/<name>` + `/transcript/<name>` into one `[name].get.ts` is cleaner than the
  spec's two routes, and correct (see § 6 note on dotted params).
- `server.py`'s `serve_audio` read the **entire file into memory** (`data = f.read()`) for
  non-Range requests; the plan streams. Strictly better.
- `server.py`'s Range parser had an off-by-one in the `'start-'` branch and no 416 for
  `end < start`; the plan handles both.
- The plan's `callCount` from the log is better than `server.py`'s `len(calls.json)`, which during a
  live session reports the **previous** session's count (since `calls.json` is only written at
  session end). This is a genuine fix — provided B3 is applied.

---

## 6. Does `process.kill(-pid, 'SIGINT')` tear down the whole tree?

**Yes. The plan is right here, and it leaves no orphans.** This was worth checking because
`lwin_listen.sh` backgrounds three separate children, one of them behind a `script` wrapper that is
known not to forward signals.

### What the script actually spawns

```bash
python3 "$R/scripts/udp_audio_record.py" $PORT "$RUN" "$R/recordings" \
        "$R/results/op25_record.log" &
REC_PID=$!
...
if [ "$STT" -eq 1 ]; then
  python3 "$R/scripts/stt_watch.py" --dir "$R/recordings" &
  STT_PID=$!
fi
...
OP25_CMD="cd $A && exec python3 rx.py --args soapy=0,driver=hackrf ... -n -v 2"
script -q -f -c "$OP25_CMD" "$R/results/op25_record.log" >/dev/null 2>&1 &
OP25_PID=$!
```

### `cleanup()`, quoted in full

```bash
cleanup() {
  echo; echo "stopping..."
  [ -n "${OP25_PID:-}" ] && kill "$OP25_PID" 2>/dev/null
  pkill -f "gr-op25_repeater/apps/rx.py" 2>/dev/null
  [ -n "${REC_PID:-}"  ] && kill -INT "$REC_PID" 2>/dev/null
  [ -n "${STT_PID:-}"  ] && kill -INT "$STT_PID" 2>/dev/null
  wait 2>/dev/null
  n=$(ls -1 "$R"/recordings/TG*.wav 2>/dev/null | wc -l)
  echo "-> $n call(s) in $R/recordings/"
  exit 0
}
trap cleanup INT TERM
```

`cleanup` reaps all three: `OP25_PID` (the `script` wrapper) by `kill`, `rx.py` itself by the
unscoped `pkill -f "gr-op25_repeater/apps/rx.py"`, and `REC_PID` / `STT_PID` by `kill -INT`. That
`pkill` is **load-bearing**: `script -q -f -c` does not forward signals to the command it runs, so
killing the wrapper alone would strand `rx.py` holding the HackRF.

### Two independent mechanisms, either sufficient

1. **Process group.** `detached: true` makes Node call `setsid()`, so `child.pid` **is** the new
   session and process-group leader. Every descendant the script backgrounds inherits that pgid —
   `udp_audio_record.py`, `stt_watch.py`, the `script` wrapper, and the `rx.py` it `exec`s.
   `process.kill(-pid, 'SIGINT')` signals all of them **directly**, so nothing is stranded even if
   the trap never fired. This is exactly what `server.py` did
   (`os.killpg(os.getpgid(pid), signal.SIGINT)`), so the plan preserves proven behavior.

2. **The trap.** The same SIGINT reaches bash and runs `cleanup` as above.

Both firing is harmless duplication — `kill` on an already-dead pid is a no-op and every call in
`cleanup` is `2>/dev/null`-suppressed.

### Timing and the SIGKILL escalation

The plan's 8-second SIGINT grace period (`for (let waited = 0; waited < 8000 && ...)`) is generous.
The only thing that must complete is `udp_audio_record.py`'s `finally`, which flushes the in-progress
call and writes `calls.json` — sub-second. `server.py` allowed 5 s. The `SIGKILL` fallback is also
correctly group-scoped (`process.kill(-pid, 'SIGKILL')`), so it will not leave `rx.py` behind.

One caveat on hardware: a `SIGKILL`-ed `rx.py` does not release the HackRF cleanly. Only reachable if
SIGINT is ignored for 8 s, and the same was true of the old server (which had no escalation at all,
so this is an improvement).

### One knock-on worth knowing

Because Node keeps the `ChildProcess` object after `child.unref()` and still reaps on `SIGCHLD`, the
pid **disappears** rather than becoming a zombie. So the `/proc/<pid>/stat` state `!== 'Z'` check in
`isProcessRunning` is effectively dead code in the plan's in-memory design — harmless, but it only
becomes meaningful again once you recover a pid from a pidfile, where the current process is not the
parent and cannot reap. That is one more argument for **M1**.

### Verifying it

Task 7 Step 4 and Task 13 Step 3 already have the right check:

```bash
pgrep -af 'lwin_listen|rx.py|udp_audio_record' || echo "clean"
```

Add `stt_watch` to that pattern — the plan's expression omits it, and `--stt` sessions are exactly
where an orphan would be least obvious.

---

## 7. Verdict

**Not safe to execute as written. Revise first.**

Four blockers, each of which independently breaks a shipped feature:

- **B1** — `tgid` is absent from `lwin_talkgroups.json` entries, so `loadTalkgroups` filters out all
  4,163 and the talkgroups panel is permanently empty.
- **B2** — `enc` is `'clear'|'partial'|'full'`, so every `'encrypted'` filter matches zero rows.
- **B3** — `stdio: 'ignore'` means nothing writes `web/listen.log`, so the live call count is frozen
  at a stale ~1,600 drawn from a previous session's log.
- **B4** — a Task 5 test the plan asserts will pass fails on its own fixture, plus a wrong expected
  pass count in the same step.

Three of the plan's own nine Acceptance Criteria are unreachable as written (talkgroup panel →
B1 + M6; recordings encryption filter → B2; live call count → B3).

None of this is architectural. The task decomposition, TDD ordering, dependency direction between
tasks, process-group signalling (verified sound — § 6), Range streaming, and the decision to collapse
the spec's two `[name]` routes into one are all good. The plan's own Task 5 Step 5 and Task 8 Step 5
verification steps would have caught B1 and B2 had they been executed as prerequisites rather than
written as post-hoc confirmations.

**Recommendation: one revision pass folding B1–B4 and M1–M9 into the plan text — roughly 90 minutes
of editing — then execute.** Do the B2 fix first: replacing the `encryption` enum with
`includePartial` / `includeEncrypted` booleans dissolves M4's dropped flags and the exclusive-radio
regression at the same time. Treat § 5 as a checklist of features to consciously keep or consciously
drop, rather than letting them lapse by omission.
