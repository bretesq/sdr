# SDR Web Console — Nuxt 3 + PrimeVue 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the Python stdlib web console (`web/server.py`) to a Nuxt 3 + TypeScript full-stack app with PrimeVue 4 components, supporting listen/record control, audio playback, and talkgroup browsing.

**Architecture:** Single Nuxt 3 application with Vue 3 components and TypeScript Nitro server routes for process orchestration. Keeps the existing file layout (`recordings/`, `reference/`, `scripts/`) untouched. Status updates via 5-second polling.

**Tech Stack:** Nuxt 3, Vue 3, PrimeVue 4, PrimeFlex (layout only), TypeScript, Node.js `child_process`

**Spec:** `docs/superpowers/specs/2026-08-31-web-console-nuxt-migration.md`
**Review:** `docs/superpowers/plans/2026-08-31-review-findings.md` — this revision folds in B1–B4 and M1–M19.

## Global Constraints

- Node.js 22 LTS (the box currently runs v24.15.0; pin to 22 if Nuxt misbehaves)
- **Package manager: pnpm** (no lockfile exists, and per CLAUDE.md that means pnpm). Commit `pnpm-lock.yaml`, never `package-lock.json`.
- Serve on `0.0.0.0:3000` (or configured PORT) — must be LAN-accessible
- Recordings and metadata stay in `/home/besquivel/rtl/recordings/`; reference DB in `/home/besquivel/rtl/reference/`
- API response format: `{ success: boolean, data?: T, error?: string }`
- No breaking changes to `lwin_listen.sh` or any other shell script
- **All types explicitly declared — no `any`, no `@ts-ignore`, no `@ts-expect-error`** (CLAUDE.md). This is enforced: a reviewer rejected the first draft for one `as any`.
- Nuxt app lives at the repo root (`/home/besquivel/rtl`), alongside `scripts/`, `reference/`, `recordings/`

## Verified Data Contracts

**These are measured against the live repo, not assumed. Do not re-derive them; do not "fix" code to match a different shape.**

| Source | Shape |
|---|---|
| `reference/lwin_talkgroups.json` | Object keyed by **tgid-as-string**. Values have `alpha, cat, desc, enc, hex, mode, tag, tgcat` — **no `tgid` field**. 4,163 entries. |
| `recordings/calls.json` | Flat **array** of objects: `file, tgid, alpha, desc, enc, cat, start, dur`. **No `transcript` key.** 2,953 entries. Rewritten wholesale at session end by `udp_audio_record.py`, truncated to that session. |
| `enc` values (both sources) | `'clear'` \| `'partial'` \| `'full'`. **Never `'encrypted'`.** DB distribution: clear 3193, full 856, partial 114. |
| `lwin_active_whitelist.txt` | One bare tgid per line. 601 lines — matches `filterByArea(..., 'br')` exactly. |
| Recording filenames | `TG<tgid>_<alpha-with-spaces-as-hyphens>_YYYYMMDD-HHMMSS[_n].wav`. Verified: 0 of 3,232 files fail this pattern. Timestamps are **local wall-clock**, not UTC. |
| `recordings/` | 3,232 `.wav`, 3,231 `.txt`, 6,463 files / 221 MB total. 279 wavs have no `calls.json` entry. |
| `web/listen.log` | Written by **nothing** in `scripts/`. It existed only because `server.py` redirected the child's stdout into it. The Nitro server must own it now (see Task 6). |

## Feature Parity Checklist

The existing `web/index.html` does these things. Each is either implemented below or listed in Known Gaps as a conscious drop. Nothing lapses by omission.

- [x] Statewide recording (`--all-areas`) — Task 6, Task 10
- [x] Tag selection (`--tag`) and regex selection (`--match`) — Task 6, Task 10
- [x] **Independent** partial / fully-encrypted checkboxes (not a radio group) — Task 10
- [x] Recordings search across alpha, desc, **cat, transcript, file**, tgid — Task 11
- [x] Encryption filter with `clear` / `partial` / `full` / `none` (unlabelled) — Task 11
- [x] Talkgroups panel encryption filter — Task 12
- [x] Talkgroup search includes `tag`; `mode` displayed — Task 9, Task 12
- [x] `[BLANK_AUDIO]` transcripts visually dimmed — Task 11
- [x] Running config + pid shown in the status line — Task 10
- [x] Stop refreshes the recordings list after a flush delay — Task 10, Task 11
- [x] Talkgroup counts ("showing N of M · K in whitelist") — Task 12
- [ ] Per-row inline `<audio>` players — **deliberately replaced** by a Dialog (see Known Gaps)

---

## Task 0: Confirm the Data Contracts

**Files:** none — this is a read-only gate.

Run these before writing any code. Each must produce the stated result. If one does not, **stop** and reconcile the Verified Data Contracts table above before continuing — every type in this plan depends on them.

- [ ] **Step 1: Talkgroup DB shape**

```bash
cd /home/besquivel/rtl
python3 -c "
import json, collections
d = json.load(open('reference/lwin_talkgroups.json'))
assert isinstance(d, dict), 'expected an object keyed by tgid'
k = next(iter(d))
assert 'tgid' not in d[k], 'entries should NOT carry a tgid field'
print('keys on a value:', sorted(d[k]))
print('enc distribution:', collections.Counter(v.get('enc') for v in d.values()))
print('entries:', len(d))
"
```

Expected: value keys `['alpha','cat','desc','enc','hex','mode','tag','tgcat']`; enc `{'clear': 3193, 'full': 856, 'partial': 114}`; 4163 entries.

- [ ] **Step 2: calls.json shape**

```bash
python3 -c "
import json, collections
d = json.load(open('recordings/calls.json'))
assert isinstance(d, list), 'expected an array'
ks = set()
for e in d: ks.update(e)
print('union of keys:', sorted(ks))
print('entries:', len(d), '| with transcript:', sum(1 for e in d if e.get('transcript')))
print('enc:', collections.Counter(e.get('enc') for e in d))
"
```

Expected: keys `['alpha','cat','desc','dur','enc','file','start','tgid']`; 2953 entries; **0** with transcript.

- [ ] **Step 3: Filename pattern holds for every recording**

```bash
ls recordings/*.wav | xargs -n1 basename \
  | grep -vcE '^TG[0-9]+_[A-Za-z0-9.\-]+_[0-9]{8}-[0-9]{6}(_[0-9]+)?\.wav$'
```

Expected: `0`.

- [ ] **Step 4: Timestamps are local, not UTC**

```bash
python3 -c "
import json, datetime
d = json.load(open('recordings/calls.json'))
e = d[0]
stamp = e['file'].split('_')[-1].replace('.wav','')
local = datetime.datetime.strptime(stamp, '%Y%m%d-%H%M%S').timestamp()
print('calls.json start:', e['start'])
print('parsed as local :', local, '(delta %.0fs)' % (e['start'] - local))
print('parsed as UTC   :', datetime.datetime.strptime(stamp, '%Y%m%d-%H%M%S').replace(tzinfo=datetime.timezone.utc).timestamp())
"
```

Expected: the local parse matches `start` to within a second; the UTC parse is off by the local UTC offset (18000 s here). This is why Task 3 uses `new Date(...)`, not `Date.UTC(...)`.

- [ ] **Step 5: Nothing writes `web/listen.log`**

```bash
grep -c 'listen\.log' scripts/lwin_listen.sh || echo "0 — the Nitro server must own this log"
```

Expected: `0`.

- [ ] **Step 6: The CLI flags this plan generates all exist**

```bash
grep -oE '^\s+--[a-z-]+\)' scripts/lwin_listen.sh | tr -d ' )'
```

Expected to include: `--preset --tag --tg --match --all-areas --include-partial --include-encrypted --stt --list`.

- [ ] **Step 7: Whitelist format and size**

```bash
head -3 lwin_active_whitelist.txt && wc -l < lwin_active_whitelist.txt
```

Expected: bare integers, one per line; 601 lines.

---

## File Structure

```
/home/besquivel/rtl/
├── nuxt.config.ts                    create
├── tsconfig.json                     create
├── package.json                      create
├── app.vue                           create  (just <NuxtPage />; no layout)
├── vitest.config.ts                  create
├── plugins/primevue.ts               create
├── assets/css/compat.css             create  (PrimeFlex 3 -> Aura token bridge)
├── pages/index.vue                   create
├── components/
│   ├── ListenControl.vue             create
│   ├── RecordingsList.vue            create
│   └── TalkgroupBrowser.vue          create
├── server/
│   ├── api/
│   │   ├── listen/{start.post,stop.post,status.get}.ts
│   │   ├── recordings/{list.get,[name].get}.ts     <- one route serves .wav AND .txt
│   │   ├── talkgroups/{list.get,whitelist.get}.ts
│   │   └── config/presets.get.ts
│   └── utils/
│       ├── paths.ts                  create  (path resolution + filename validation)
│       ├── files.ts                  create  (JSON, filename parse, scan, merge)
│       ├── talkgroups.ts             create  (reference DB load + filters)
│       ├── processes.ts              create  (spawn/stop/log tail)
│       ├── session.ts                create  (session store + pidfile recovery)
│       └── recordings.ts             create  (allRecordings: merge + transcripts)
└── web/{server.py,index.html,serve.sh}   DELETE in Task 14
```

No `layouts/` — `app.vue` renders `<NuxtPage />` directly. A single-page app does not need a layout indirection, and a `layouts/default.vue` that nothing wraps is dead weight.

No `search.get.ts` — see Task 8.

---

## Task 1: Initialize Nuxt 3 Project & Dependencies

**Files:**
- Create: `package.json`, `nuxt.config.ts`, `tsconfig.json`, `plugins/primevue.ts`
- Modify: `.gitignore`

**Interfaces:**
- Produces: Nuxt dev server at `http://0.0.0.0:3000`; PrimeVue 4 components auto-available in templates.

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "sdr-web-console",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "nuxt dev --host 0.0.0.0",
    "build": "nuxt build",
    "preview": "nuxt preview",
    "typecheck": "nuxt typecheck",
    "test": "vitest run"
  },
  "dependencies": {
    "nuxt": "^3.15.0",
    "vue": "^3.5.0",
    "primevue": "^4.2.0",
    "@primevue/themes": "^4.2.0",
    "primeicons": "^7.0.0",
    "primeflex": "^3.3.1"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "typescript": "^5.6.0",
    "vue-tsc": "^2.1.0",
    "vitest": "^2.1.0"
  }
}
```

`primeflex` is kept for **layout classes only** — its theme classes do not work under PrimeVue 4 (see Step 3a). `vitest` is declared here rather than installed ad-hoc in Task 2.

- [ ] **Step 2: Create `nuxt.config.ts`**

PrimeVue 4 ships a Nuxt module (`@primevue/nuxt-module`) but it is a separate package; this plan registers PrimeVue through a plugin instead to keep the dependency list minimal.

```typescript
export default defineNuxtConfig({
  compatibilityDate: '2025-01-01',
  devtools: { enabled: true },
  css: [
    'primeicons/primeicons.css',
    'primeflex/primeflex.css',
    '~/assets/css/compat.css',   // must come AFTER primeflex — see Step 3a
  ],
  build: {
    transpile: ['primevue'],
  },
  typescript: {
    strict: true,
    typeCheck: false,
  },
  // The repo root holds 221 MB / 6,463 files in recordings/ (growing during every
  // recording session), 81 MB in src/, 55 MB in results/. Nuxt does NOT read
  // .gitignore for its watch list, so without this the dev server registers
  // watchers on all of it — slow boot, ENOSPC risk on WSL2, and a watcher event
  // for every .wav the recorder flushes while you are using the app.
  ignore: [
    'recordings/**', 'results/**', 'captures/**', 'models/**',
    'src/**', 'reference/**', 'web/**', 'tools/**', 'docs/**', 'adp_brute/**',
    'lwin_*.tsv', 'lwin_*.txt', 'lwin_keys.json',
  ],
})
```

No `runtimeConfig` entry: `paths.ts` reads `process.env.SDR_ROOT` directly, and a `runtimeConfig.sdrRoot` key would be fed by `NUXT_SDR_ROOT` anyway — two names for one value is worse than one.

- [ ] **Step 3: Create `plugins/primevue.ts`**

PrimeVue 4 requires an explicit theme preset. Components used across the three panels are registered globally here.

```typescript
import PrimeVue from 'primevue/config'
import Aura from '@primevue/themes/aura'

import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import Select from 'primevue/select'
import Tag from 'primevue/tag'

export default defineNuxtPlugin((nuxtApp) => {
  nuxtApp.vueApp.use(PrimeVue, {
    theme: { preset: Aura },
  })

  nuxtApp.vueApp.component('Button', Button)
  nuxtApp.vueApp.component('Checkbox', Checkbox)
  nuxtApp.vueApp.component('Column', Column)
  nuxtApp.vueApp.component('DataTable', DataTable)
  nuxtApp.vueApp.component('Dialog', Dialog)
  nuxtApp.vueApp.component('InputNumber', InputNumber)
  nuxtApp.vueApp.component('InputText', InputText)
  nuxtApp.vueApp.component('Message', Message)
  nuxtApp.vueApp.component('ProgressSpinner', ProgressSpinner)
  nuxtApp.vueApp.component('Select', Select)
  nuxtApp.vueApp.component('Tag', Tag)
})
```

> PrimeVue 4 renamed `Dropdown` to `Select` (the old name survives as a deprecated alias); this plan uses `Select`. No `RadioButton` — the encryption scope is three independent checkboxes, not a radio group (see Task 10).

- [ ] **Step 3a: Create `assets/css/compat.css` — required, not cosmetic**

PrimeFlex 3 was built for PrimeOne v3 and its *theme* classes resolve unprefixed CSS variables:

| Class | Resolves |
|---|---|
| `.surface-card` | `var(--surface-card)` |
| `.surface-100` | `var(--surface-100)` |
| `.text-color-secondary` | `var(--text-color-secondary)` |
| `.border-round` | `var(--border-radius)` |

PrimeVue 4's Aura preset defines `--p-`-prefixed tokens (`--p-content-background`, `--p-surface-100`, `--p-text-muted-color`, `--p-content-border-radius`) and **does not define the legacy names**. PrimeFlex is deprecated in v4 in favour of Tailwind. Without this shim every panel loses its background and radius against a body Nuxt never styles — an unstyled-white page — and `TalkgroupBrowser`'s whitelist highlighting (which returns `'surface-100'`) silently does nothing.

Pure-layout classes are unaffected and used throughout: `grid`, `col-12`, `lg:col-4/8`, `flex`, `flex-column`, `flex-wrap`, `flex-1`, `gap-*`, `p-*`, `m*-*`, `w-full`, `w-*rem`, `text-sm/base/xl/2xl/3xl`, `font-bold`, `line-height-3`, `align-items-center`, `justify-content-between`.

```css
/* Bridge PrimeFlex 3's theme classes onto PrimeVue 4 Aura design tokens. */
:root {
  --surface-card: var(--p-content-background);
  --surface-100: var(--p-surface-100);
  --surface-border: var(--p-content-border-color);
  --text-color: var(--p-text-color);
  --text-color-secondary: var(--p-text-muted-color);
  --border-radius: var(--p-content-border-radius);
}

/* Nuxt + PrimeVue 4 style no <body> at all. Required either way. */
body {
  margin: 0;
  background: var(--p-content-background);
  color: var(--p-text-color);
}
```

**Verify at Task 10 Step 2** (30 seconds, do not install anything early to pre-prove it): open devtools, inspect a `<section>` panel, and confirm `background-color` resolves to a colour rather than an unresolved `var()`.

- [ ] **Step 4: Create `tsconfig.json`**

```json
{
  "extends": "./.nuxt/tsconfig.json"
}
```

- [ ] **Step 5: Append Node artifacts to `.gitignore`**

```
# Node / Nuxt
node_modules/
.nuxt/
.output/
dist/
```

- [ ] **Step 6: Create `vitest.config.ts`**

Vitest's default excludes do not cover `.nuxt/`, so a bare run would scan generated output.

```typescript
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    include: ['server/**/*.test.ts'],
    environment: 'node',
  },
})
```

- [ ] **Step 7: Install and verify the dev server boots**

```bash
cd /home/besquivel/rtl
pnpm install
pnpm dev
```

Expected: server listens on `http://0.0.0.0:3000`, no module resolution errors. Stop with Ctrl-C.

If boot is slow or throws `ENOSPC: System limit for number of file watchers`, the `ignore` list in Step 2 is wrong or incomplete — fix it there rather than raising the inotify limit.

- [ ] **Step 8: Commit**

```bash
git add package.json pnpm-lock.yaml nuxt.config.ts tsconfig.json vitest.config.ts plugins/ assets/ .gitignore
git commit -m "feat: initialize Nuxt 3 + PrimeVue 4 project"
```

---

## Task 2: Path Resolution Utility

**Files:**
- Create: `server/utils/paths.ts`
- Test: `server/utils/paths.test.ts`

**Interfaces:**
- Produces: `sdrRoot(): string`, `recordingsDir(): string`, `referenceDir(): string`, `scriptsDir(): string`, `listenLogPath(): string`, `whitelistPath(): string`, `safeRecordingPath(name: string): string | null`

Every later task consumes these instead of hardcoding `/home/besquivel/rtl`.

- [ ] **Step 1: Write the failing test**

```typescript
// server/utils/paths.test.ts
import { describe, it, expect } from 'vitest'
import { safeRecordingPath } from './paths'

describe('safeRecordingPath', () => {
  it('accepts a well-formed recording name', () => {
    const p = safeRecordingPath('TG17165_17-BRPD-DSP1_20260830-170008.wav')
    expect(p).toContain('/recordings/TG17165_17-BRPD-DSP1_20260830-170008.wav')
  })

  it('accepts the matching transcript name', () => {
    const p = safeRecordingPath('TG17165_17-BRPD-DSP1_20260830-170008.txt')
    expect(p).toContain('.txt')
  })

  it('rejects path traversal', () => {
    expect(safeRecordingPath('../../etc/passwd')).toBeNull()
    expect(safeRecordingPath('TG1_x_20260830-170008.wav/../../../etc/passwd')).toBeNull()
  })

  it('rejects names that do not match the recording pattern', () => {
    expect(safeRecordingPath('calls.json')).toBeNull()
    expect(safeRecordingPath('server.py')).toBeNull()
  })
})
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
pnpm vitest run server/utils/paths.test.ts
```

Expected: FAIL — `Cannot find module './paths'`. (Vitest and its config were installed in Task 1.)

- [ ] **Step 3: Implement `server/utils/paths.ts`**

```typescript
import { join, resolve, basename } from 'node:path'

// `TG\d+` covers all 3,232 current recordings. `TGunknown` is also accepted:
// udp_audio_record.py:86 emits TGunknown_<stamp>.wav when no grant matched the
// audio, and server.py could still serve those. None exist today, but rejecting
// them here would make them silently unplayable.
const RECORDING_NAME = /^TG(?:\d+|unknown)_[A-Za-z0-9.\-]+_\d{8}-\d{6}(?:_\d+)?\.(wav|txt)$/

export function sdrRoot(): string {
  return process.env.SDR_ROOT || '/home/besquivel/rtl'
}

export function recordingsDir(): string {
  return join(sdrRoot(), 'recordings')
}

export function referenceDir(): string {
  return join(sdrRoot(), 'reference')
}

export function scriptsDir(): string {
  return join(sdrRoot(), 'scripts')
}

// The Nitro server owns all four of these. Nothing in scripts/ writes them.
// All are already in .gitignore from the Python server era.
export function listenLogPath(): string {
  return join(sdrRoot(), 'web', 'listen.log')
}

export function listenPidPath(): string {
  return join(sdrRoot(), 'web', 'listen.pid')
}

export function listenConfigPath(): string {
  return join(sdrRoot(), 'web', 'listen.config.json')
}

export function listenStartedPath(): string {
  return join(sdrRoot(), 'web', 'listen.started')
}

export function whitelistPath(): string {
  return join(sdrRoot(), 'lwin_active_whitelist.txt')
}

/**
 * Resolve a user-supplied recording filename to an absolute path,
 * or null if it is not a legal recording name or escapes the directory.
 */
export function safeRecordingPath(name: string): string | null {
  if (basename(name) !== name) return null
  if (!RECORDING_NAME.test(name)) return null

  const dir = recordingsDir()
  const full = resolve(dir, name)
  if (!full.startsWith(dir + '/')) return null
  return full
}
```

- [ ] **Step 4: Run the test, confirm it passes**

```bash
pnpm vitest run server/utils/paths.test.ts
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add server/utils/paths.ts server/utils/paths.test.ts package.json
git commit -m "feat: add path resolution and filename validation utility"
```

---

## Task 3: Recording Filename Parsing & JSON Loading

**Files:**
- Create: `server/utils/files.ts`
- Test: `server/utils/files.test.ts`

**Interfaces:**
- Produces:
  - `interface Recording { file: string; tgid: number | null; alpha: string | null; desc: string | null; cat: string | null; enc: Encryption | null; start: number; dur: number; transcript: string | null }`
  - `type Encryption = 'clear' | 'partial' | 'full'`
  - `loadJSON<T>(path: string, fallback: T): T`
  - `parseRecordingFilename(name: string): { tgid: number | null; start: number }`
  - `scanRecordings(dir: string, tgdb: Record<string, TalkgroupEntry>): Recording[]`
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write the failing test**

```typescript
// server/utils/files.test.ts
import { describe, it, expect } from 'vitest'
import { parseRecordingFilename, loadJSON } from './files'

describe('parseRecordingFilename', () => {
  it('extracts tgid and a LOCAL-time timestamp', () => {
    const r = parseRecordingFilename('TG17165_17-BRPD-DSP1_20260830-170008.wav')
    expect(r.tgid).toBe(17165)
    // udp_audio_record.py stamps local wall-clock, and calls.json's `start` is
    // Python's local .timestamp(). Parsing as UTC is off by the UTC offset
    // (5 h here) for every recording not present in calls.json.
    expect(r.start).toBe(new Date(2026, 7, 30, 17, 0, 8).getTime() / 1000)
  })

  it('handles the duplicate-suffix form', () => {
    const r = parseRecordingFilename('TG5000_SP-A-DISP1_20260830-170051_2.wav')
    expect(r.tgid).toBe(5000)
  })

  it('returns nulls for an unparseable name', () => {
    const r = parseRecordingFilename('notarecording.wav')
    expect(r.tgid).toBeNull()
    expect(r.start).toBe(0)
  })
})

describe('loadJSON', () => {
  it('returns the fallback when the file is missing', () => {
    expect(loadJSON('/nonexistent/nope.json', { a: 1 })).toEqual({ a: 1 })
  })
})
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
pnpm vitest run server/utils/files.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `server/utils/files.ts`**

Timestamps in filenames are **local wall-clock** from `udp_audio_record.py`, and `calls.json`'s `start` is Python's local `.timestamp()`. Parse as local. `mergeCalls` overwrites `start` for the 2,953 files in `calls.json`, so a UTC misparse would be invisible on the happy path and wrong only for the ~279 that aren't — exactly the bug that survives review.

```typescript
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Encryption label as it appears in the reference DB and calls.json.
 * Verified vocabulary — 'encrypted' is NOT a value that ever occurs.
 * Do not conflate this with the listen-scope flags (see ListenOptions).
 */
export type Encryption = 'clear' | 'partial' | 'full'

export interface TalkgroupEntry {
  tgid: number      // synthesized from the JSON key — absent from the value object
  alpha: string
  desc: string
  cat: string
  enc: Encryption
  tag: string       // e.g. "Law Dispatch" — searched, and what --tag selects on
  mode: string      // e.g. "D" / "De" — shown so "D enc" is visible
}

export interface Recording {
  file: string
  tgid: number | null
  alpha: string | null
  desc: string | null
  cat: string | null
  enc: Encryption | null
  start: number
  dur: number
  transcript: string | null
}

const NAME = /^TG(\d+)_.+_(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(?:_\d+)?\.wav$/

export function loadJSON<T>(path: string, fallback: T): T {
  try {
    return JSON.parse(readFileSync(path, 'utf-8')) as T
  } catch {
    return fallback
  }
}

export function parseRecordingFilename(name: string): { tgid: number | null; start: number } {
  const m = NAME.exec(name)
  if (!m) return { tgid: null, start: 0 }

  const [, tg, y, mo, d, h, mi, s] = m
  // LOCAL time — see the note above. Do not change to Date.UTC.
  const start = new Date(
    Number(y), Number(mo) - 1, Number(d),
    Number(h), Number(mi), Number(s),
  ).getTime() / 1000

  return { tgid: Number(tg), start }
}

export function scanRecordings(
  dir: string,
  tgdb: Record<string, TalkgroupEntry>,
): Recording[] {
  let files: string[]
  try {
    files = readdirSync(dir)
  } catch {
    return []
  }

  const out: Recording[] = []
  for (const file of files) {
    if (!file.endsWith('.wav')) continue
    const { tgid, start } = parseRecordingFilename(file)
    const entry = tgid === null ? undefined : tgdb[String(tgid)]

    out.push({
      file,
      tgid,
      alpha: entry?.alpha ?? null,
      desc: entry?.desc ?? null,
      cat: entry?.cat ?? null,
      enc: entry?.enc ?? null,
      start,
      dur: 0,
      transcript: null,
    })
  }

  return out.sort((a, b) => b.start - a.start)
}
```

- [ ] **Step 4: Run the test, confirm it passes**

```bash
pnpm vitest run server/utils/files.test.ts
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add server/utils/files.ts server/utils/files.test.ts
git commit -m "feat: add recording filename parsing and directory scan"
```

---

## Task 4: Merge `calls.json` Metadata Into Scanned Recordings

**Files:**
- Modify: `server/utils/files.ts`
- Modify: `server/utils/files.test.ts`

**Interfaces:**
- Consumes: `Recording`, `scanRecordings` from Task 3.
- Produces: `mergeCalls(recordings: Recording[], calls: unknown): Recording[]`

**Shape is already established** (Task 0 Step 2): `calls.json` is a flat **array** of 2,953 objects with exactly `file, tgid, alpha, desc, enc, cat, start, dur` — and no `transcript` key. `mergeCalls` still accepts the object-keyed form defensively, but the array path is the only one that occurs.

Two behaviours to know, because they look like bugs and aren't:

- `udp_audio_record.py` writes `calls.json` **once**, in its `finally` block, containing **only that session's calls**. So during a live session the file still holds the *previous* session's data: new recordings show `dur: 0` and a filename-derived `start` until the session ends. `server.py` behaved identically. This is why Task 10's Stop must refresh the recordings list (gap item 11).
- `stt_watch.py` merges `transcript` into `calls.json`, but `udp_audio_record.py` rewrites the file at session end and clobbers those merges — hence 0 transcript keys on disk today. **The `.txt` fallback fetch in `RecordingsList` is load-bearing, not a nicety.**

- [ ] **Step 1: Write the failing test**

```typescript
// append to server/utils/files.test.ts
import { mergeCalls } from './files'
import type { Recording } from './files'

const base: Recording = {
  file: 'TG17165_x_20260830-170008.wav',
  tgid: 17165, alpha: null, desc: null, cat: null, enc: null,
  start: 100, dur: 0, transcript: null,
}

describe('mergeCalls', () => {
  it('merges duration and transcript from an array-shaped calls.json', () => {
    const merged = mergeCalls([base], [
      { file: 'TG17165_x_20260830-170008.wav', dur: 12.1, transcript: 'hello' },
    ])
    expect(merged[0].dur).toBe(12.1)
    expect(merged[0].transcript).toBe('hello')
  })

  it('merges from an object-shaped calls.json', () => {
    const merged = mergeCalls([base], {
      'TG17165_x_20260830-170008.wav': { dur: 5, transcript: 'yo' },
    })
    expect(merged[0].dur).toBe(5)
  })

  it('never lets calls.json blank out a scanned field', () => {
    const merged = mergeCalls([{ ...base, alpha: 'FROM-DB' }], [
      { file: 'TG17165_x_20260830-170008.wav', alpha: null, dur: 3 },
    ])
    expect(merged[0].alpha).toBe('FROM-DB')
    expect(merged[0].dur).toBe(3)
  })

  it('leaves recordings absent from calls.json untouched', () => {
    const merged = mergeCalls([base], [])
    expect(merged[0].dur).toBe(0)
  })
})
```

- [ ] **Step 2: Run, confirm failure**

```bash
pnpm vitest run server/utils/files.test.ts
```

Expected: FAIL — `mergeCalls` is not exported.

- [ ] **Step 3: Implement `mergeCalls` in `server/utils/files.ts`**

```typescript
type CallRecord = Partial<Recording> & { file?: string }

/**
 * Merge metadata from calls.json into scanned recordings.
 * Accepts either an array of call records or an object keyed by filename.
 * Only non-null values from calls.json override scanned values.
 */
export function mergeCalls(recordings: Recording[], calls: unknown): Recording[] {
  const byFile = new Map<string, CallRecord>()

  if (Array.isArray(calls)) {
    for (const c of calls as CallRecord[]) {
      if (c && typeof c === 'object' && typeof c.file === 'string') byFile.set(c.file, c)
    }
  } else if (calls && typeof calls === 'object') {
    for (const [key, val] of Object.entries(calls as Record<string, CallRecord>)) {
      if (!val || typeof val !== 'object') continue
      byFile.set(typeof val.file === 'string' ? val.file : key, val)
    }
  }

  // Fields enumerated explicitly: the generic key-walk needed an `as any`,
  // which the Global Constraints forbid. calls.json's key set is known and
  // fixed (Task 0 Step 2), so there is nothing to be generic about.
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
      file:       rec.file,     // never let calls.json rename the file
    }
  })
}
```

- [ ] **Step 4: Run, confirm pass**

```bash
pnpm vitest run server/utils/files.test.ts
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add server/utils/files.ts server/utils/files.test.ts
git commit -m "feat: merge calls.json metadata into scanned recordings"
```

---

## Task 5: Talkgroup Reference DB Utilities

**Files:**
- Create: `server/utils/talkgroups.ts`
- Test: `server/utils/talkgroups.test.ts`

**Interfaces:**
- Consumes: `loadJSON`, `TalkgroupEntry`, `Encryption` from `files.ts`.
- Produces:
  - `loadTalkgroups(path: string): TalkgroupEntry[]`
  - `loadWhitelist(path: string): Set<number>`
  - `filterByArea(tgs: TalkgroupEntry[], area: 'br' | 'all'): TalkgroupEntry[]`
  - `filterTalkgroups(tgs, opts: { category?: string; text?: string }): TalkgroupEntry[]`

Shape and field names were pinned in **Task 0 Step 1**. Do not re-derive them here, and do not edit Task 3's `TalkgroupEntry` — it already matches.

- [ ] **Step 1: Write the failing test**

Fixture values are **copied verbatim from the real DB** — an invented category like `'EBR (17) BR Police'` contains none of the `BR_AREA_KEYWORDS` and would make a correct implementation fail its own test.

```typescript
// server/utils/talkgroups.test.ts
import { describe, it, expect } from 'vitest'
import { loadTalkgroups, filterByArea, filterTalkgroups } from './talkgroups'
import { referenceDir } from './paths'
import { join } from 'node:path'
import type { TalkgroupEntry } from './files'

// Real entries: tgid 17165 and 6039 as they actually appear in the DB.
const tgs: TalkgroupEntry[] = [
  { tgid: 17165, alpha: '17-BRPD DSP1', desc: 'Dispatch 1',
    cat: 'East Baton Rouge Parish (17) - Baton Rouge Police',
    enc: 'partial', tag: 'Law Dispatch', mode: 'D enc' },
  { tgid: 6039, alpha: 'LDWF R4-DISP', desc: 'Region 4 Dispatch',
    cat: 'Dept. of Wildlife and Fisheries - Region 4 (Lafayette)',
    enc: 'clear', tag: 'Law Dispatch', mode: 'D' },
]

describe('loadTalkgroups', () => {
  it('synthesizes tgid from the JSON key', () => {
    // The DB is an object keyed by tgid; entries carry NO tgid field.
    // A naive `'tgid' in v` filter returns zero rows for all 4,163 entries.
    const all = loadTalkgroups(join(referenceDir(), 'lwin_talkgroups.json'))
    expect(all.length).toBe(4163)
    expect(all.every(t => Number.isFinite(t.tgid))).toBe(true)

    const brpd = all.find(t => t.tgid === 17165)
    expect(brpd?.alpha).toBe('17-BRPD DSP1')
    expect(brpd?.enc).toBe('partial')
    expect(brpd?.tag).toBe('Law Dispatch')
  })

  it('returns entries sorted ascending by tgid', () => {
    const all = loadTalkgroups(join(referenceDir(), 'lwin_talkgroups.json'))
    for (let i = 1; i < all.length; i++) {
      expect(all[i].tgid).toBeGreaterThan(all[i - 1].tgid)
    }
  })
})

describe('filterByArea', () => {
  it('keeps everything for area=all', () => {
    expect(filterByArea(tgs, 'all')).toHaveLength(2)
  })

  it('matches BR-area categories by substring', () => {
    const out = filterByArea(tgs, 'br')
    // 17165 matches 'East Baton Rouge'; 6039 matches 'Wildlife and Fisheries'.
    expect(out.map(t => t.tgid)).toContain(17165)
    expect(out.map(t => t.tgid)).toContain(6039)
  })

  it('selects exactly 601 of 4163 against the real DB', () => {
    // 601 is also the line count of lwin_active_whitelist.txt — if this drifts,
    // either the keyword list or the DB changed.
    const all = loadTalkgroups(join(referenceDir(), 'lwin_talkgroups.json'))
    expect(filterByArea(all, 'br')).toHaveLength(601)
  })
})

describe('filterTalkgroups', () => {
  it('filters by exact category', () => {
    const out = filterTalkgroups(tgs, {
      category: 'Dept. of Wildlife and Fisheries - Region 4 (Lafayette)',
    })
    expect(out).toHaveLength(1)
    expect(out[0].tgid).toBe(6039)
  })

  it('searches tgid, alpha, desc, cat and tag case-insensitively', () => {
    expect(filterTalkgroups(tgs, { text: 'brpd' })).toHaveLength(1)
    expect(filterTalkgroups(tgs, { text: '6039' })).toHaveLength(1)
    expect(filterTalkgroups(tgs, { text: 'wildlife' })).toHaveLength(1)
    expect(filterTalkgroups(tgs, { text: 'law dispatch' })).toHaveLength(2)
  })

  it('filters by encryption label', () => {
    expect(filterTalkgroups(tgs, { enc: 'partial' })).toHaveLength(1)
    expect(filterTalkgroups(tgs, { enc: 'full' })).toHaveLength(0)
  })
})
```

- [ ] **Step 2: Run, confirm failure**

```bash
pnpm vitest run server/utils/talkgroups.test.ts
```

- [ ] **Step 3: Implement `server/utils/talkgroups.ts`**

Note: categories in the reference DB are strings like `"EBR (17) BR Police"`, so area matching is a **substring** test against known area keywords, not set membership. This mirrors `scripts/make_whitelist.py`.

```typescript
import { readFileSync } from 'node:fs'
import { loadJSON } from './files'
import type { TalkgroupEntry } from './files'

const BR_AREA_KEYWORDS = [
  'East Baton Rouge', 'Baton Rouge', 'LSU', 'Southern University',
  'State Police - Troop A', 'West Baton Rouge', 'Livingston', 'Ascension',
  'Iberville', 'Feliciana', 'Pointe Coupee', 'EMS Agencies',
  'Wildlife and Fisheries',
]

/**
 * The DB is an object keyed by tgid-as-string; the value objects carry NO tgid.
 * Synthesize it from the key. Sorting is explicit: relying on Object.values()
 * order works only because V8 iterates integer-like keys numerically, which is
 * an engine detail, not a guarantee — and server.py sorted explicitly.
 */
export function loadTalkgroups(path: string): TalkgroupEntry[] {
  const raw = loadJSON<Record<string, Omit<TalkgroupEntry, 'tgid'>>>(path, {})

  return Object.entries(raw)
    .map(([key, v]) => ({ tgid: Number.parseInt(key, 10), ...v }))
    .filter(tg => Number.isFinite(tg.tgid))
    .sort((a, b) => a.tgid - b.tgid)
}

export function loadWhitelist(path: string): Set<number> {
  try {
    const text = readFileSync(path, 'utf-8')
    const ids = text
      .split('\n')
      .map(line => line.trim().split(/[\s,]/)[0])
      .map(tok => Number.parseInt(tok, 10))
      .filter(n => Number.isFinite(n))
    return new Set(ids)
  } catch {
    return new Set()
  }
}

export function filterByArea(
  tgs: TalkgroupEntry[],
  area: 'br' | 'all',
): TalkgroupEntry[] {
  if (area === 'all') return tgs
  return tgs.filter(tg =>
    BR_AREA_KEYWORDS.some(k => (tg.cat ?? '').includes(k)),
  )
}

export function filterTalkgroups(
  tgs: TalkgroupEntry[],
  opts: { category?: string; text?: string; enc?: string },
): TalkgroupEntry[] {
  let out = tgs

  if (opts.category) {
    out = out.filter(tg => tg.cat === opts.category)
  }

  // The old console's talkgroups panel had its own encryption filter — needed to
  // answer "show me the encrypted talkgroups in EBR", a core question here.
  if (opts.enc && opts.enc !== 'all') {
    out = out.filter(tg => tg.enc === opts.enc)
  }

  if (opts.text) {
    const q = opts.text.toLowerCase()
    // server.py searched [alpha, desc, cat, tag, tgid] — match that.
    out = out.filter(tg =>
      String(tg.tgid).includes(q) ||
      (tg.alpha ?? '').toLowerCase().includes(q) ||
      (tg.desc ?? '').toLowerCase().includes(q) ||
      (tg.cat ?? '').toLowerCase().includes(q) ||
      (tg.tag ?? '').toLowerCase().includes(q),
    )
  }

  return out
}
```

- [ ] **Step 4: Run, confirm pass**

```bash
pnpm vitest run server/utils/talkgroups.test.ts
```

Expected: **8 passed**. Three of these assert against the live DB (4163 entries, ascending tgid, 601 BR-area), so a passing run is also the real-data verification — no separate step needed.

- [ ] **Step 5: Commit**

```bash
git add server/utils/talkgroups.ts server/utils/talkgroups.test.ts
git commit -m "feat: add talkgroup reference DB filters"
```

---

## Task 6: Listen Log Parsing & Session Store

**Files:**
- Create: `server/utils/processes.ts`
- Create: `server/utils/session.ts`
- Test: `server/utils/processes.test.ts`

**Interfaces:**
- Consumes: `listenLogPath`, `scriptsDir` from `paths.ts`.
- Produces:
  - `interface ListenOptions { preset?: string; talkgroups?: string; encryption?: Encryption; stt?: boolean; duration?: number }`
  - `buildListenArgs(opts: ListenOptions): string[]`
  - `countCalls(logText: string): number`
  - `isProcessRunning(pid: number): boolean`
  - `startListening(opts: ListenOptions): { pid: number; config: ListenOptions }`
  - `stopListening(pid: number): Promise<void>`
  - `sessionStore` singleton with `set/get/clear/isRunning`

- [ ] **Step 1: Write the failing test**

```typescript
// server/utils/processes.test.ts
import { describe, it, expect } from 'vitest'
import { buildListenArgs, countCalls } from './processes'

describe('buildListenArgs', () => {
  it('maps a preset to --preset', () => {
    expect(buildListenArgs({ preset: 'pd' })).toEqual(['--preset', 'pd'])
  })

  it('maps explicit talkgroups to --tg', () => {
    expect(buildListenArgs({ talkgroups: '17165,17167' })).toEqual(['--tg', '17165,17167'])
  })

  it('treats partial and fully-encrypted as INDEPENDENT flags', () => {
    // make_whitelist.py adds 'partial' only under --include-partial and 'full'
    // only under --include-encrypted. They compose; they are not exclusive.
    expect(buildListenArgs({ includePartial: true })).toContain('--include-partial')
    expect(buildListenArgs({ includeEncrypted: true })).toContain('--include-encrypted')

    const both = buildListenArgs({ includePartial: true, includeEncrypted: true })
    expect(both).toContain('--include-partial')
    expect(both).toContain('--include-encrypted')

    expect(buildListenArgs({})).not.toContain('--include-partial')
    expect(buildListenArgs({})).not.toContain('--include-encrypted')
  })

  it('supports tag, match and all-areas selection', () => {
    expect(buildListenArgs({ tag: 'Law Dispatch,Law Talk' }))
      .toEqual(['--tag', 'Law Dispatch,Law Talk'])
    expect(buildListenArgs({ match: 'BRPD' })).toEqual(['--match', 'BRPD'])
    expect(buildListenArgs({ allAreas: true })).toEqual(['--all-areas'])
  })

  it('puts duration last as a positional argument', () => {
    const args = buildListenArgs({ preset: 'pd', stt: true, duration: 600 })
    expect(args[args.length - 1]).toBe('600')
    expect(args).toContain('--stt')
  })
})

describe('countCalls', () => {
  it('counts distinct saved recordings from real log lines', () => {
    // Verbatim format from udp_audio_record.py:97 and stt_watch.py:71.
    const log = [
      'voice update:  tg(17051), freq(851837500), slot(-), prio(3)',
      'voice update:  tg(17051), freq(851837500), slot(-), prio(3)',
      '  TG17051_17-SO-DISP-S_20260831-081255.wav  1.3s  Dispatch South',
      'stt_watch: transcribing TG17051_17-SO-DISP-S_20260831-081255.wav',
      '  TG17165_17-BRPD-DSP1_20260831-081301.wav  11.7s  Dispatch 1',
    ].join('\n')
    // Two distinct .wav names: the stt_watch line must not double-count.
    expect(countCalls(log)).toBe(2)
  })

  it('returns 0 for an empty log', () => {
    expect(countCalls('')).toBe(0)
  })
})
```

- [ ] **Step 2: Run, confirm failure**

```bash
pnpm vitest run server/utils/processes.test.ts
```

- [ ] **Step 3: Understand why `countCalls` counts filenames (no action — read this)**

The format is already confirmed. `udp_audio_record.py:97` prints, per saved call:

```
  TG17051_17-SO-DISP-S_20260831-081255.wav  1.3s  Dispatch South
```

and with `--stt`, `stt_watch.py:71` prints `stt_watch: transcribing <same>.wav`.

Two consequences the implementation depends on:

- **Count `.wav` filenames, not `voice update` lines.** One call emits many `voice update:  tg(N), freq(...)` lines — counting those overcounts by an order of magnitude.
- **Dedupe with a Set.** The `stt_watch:` line repeats a filename already counted, so without `new Set(...).size` every call is counted twice under `--stt`.

Counting recordings on disk instead was considered and rejected: it would `readdirSync` a 3,232-entry growing directory every 5 s, and it cannot tell this session's calls from a concurrent CLI session's.

- [ ] **Step 4: Implement `server/utils/processes.ts`**

```typescript
import { spawn } from 'node:child_process'
import { readFileSync, statSync, openSync, readSync, closeSync } from 'node:fs'
import { join } from 'node:path'
import { scriptsDir, sdrRoot, listenLogPath } from './paths'

/**
 * Mirrors scripts/lwin_listen.sh's flags 1:1. Deliberately NOT typed with
 * `Encryption` — that is the DB's label vocabulary ('clear'|'partial'|'full').
 * These two booleans are independent listen-scope switches; conflating them
 * into one enum makes "partial AND full" unreachable.
 */
export interface ListenOptions {
  preset?: string
  talkgroups?: string
  tag?: string
  match?: string
  allAreas?: boolean
  includePartial?: boolean
  includeEncrypted?: boolean
  stt?: boolean
  duration?: number
}

export function buildListenArgs(opts: ListenOptions): string[] {
  const args: string[] = []

  if (opts.preset)           args.push('--preset', opts.preset)
  if (opts.tag)              args.push('--tag', opts.tag)
  if (opts.talkgroups)       args.push('--tg', opts.talkgroups)
  if (opts.match)            args.push('--match', opts.match)
  if (opts.allAreas)         args.push('--all-areas')
  if (opts.includePartial)   args.push('--include-partial')
  if (opts.includeEncrypted) args.push('--include-encrypted')
  if (opts.stt)              args.push('--stt')
  if (opts.duration)         args.push(String(opts.duration))  // positional — must stay last

  return args
}

const SAVED_WAV = /TG\d+_[^\s/]+\.wav/g

/** Count distinct saved .wav files mentioned in the log. */
export function countCalls(logText: string): number {
  const matches = logText.match(SAVED_WAV)
  if (!matches) return 0
  return new Set(matches).size
}

/**
 * Read the tail of a file without loading the whole thing.
 * Cap is 4 MB: a session log runs ~160 bytes/call (the saved-call line plus its
 * paired stt_watch line), so 4 MB covers ~25,000 calls. At 256 KB a long session
 * would silently drop half its calls and the displayed count would go DOWN.
 */
export function readTail(path: string, maxBytes = 4 * 1024 * 1024): string {
  try {
    const size = statSync(path).size
    const start = Math.max(0, size - maxBytes)
    const len = size - start
    const fd = openSync(path, 'r')
    try {
      const buf = Buffer.alloc(len)
      readSync(fd, buf, 0, len, start)
      return buf.toString('utf-8')
    } finally {
      closeSync(fd)
    }
  } catch {
    return ''
  }
}

export function isProcessRunning(pid: number): boolean {
  try {
    process.kill(pid, 0)
  } catch {
    return false
  }
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, 'utf-8')
    const state = stat.slice(stat.lastIndexOf(')') + 1).trim().split(/\s+/)[0]
    return state !== 'Z'
  } catch {
    return false
  }
}

/**
 * `lwin_listen.sh` writes NO log of its own — web/listen.log existed only
 * because server.py redirected the child's stdout into it. This server now owns
 * that log, opened 'w' (truncating) so countCalls() measures exactly this
 * session. server.py used 'ab', which made the count cumulative across every
 * session ever (3,150 distinct .wav names in the current file).
 */
export function startListening(opts: ListenOptions): { pid: number; config: ListenOptions } {
  const script = join(scriptsDir(), 'lwin_listen.sh')
  const fd = openSync(listenLogPath(), 'w')
  try {
    const child = spawn('bash', [script, ...buildListenArgs(opts)], {
      cwd: sdrRoot(),
      detached: true,               // setsid: child.pid becomes the process-group leader
      stdio: ['ignore', fd, fd],
    })
    child.unref()

    if (!child.pid) throw new Error('failed to spawn lwin_listen.sh')
    return { pid: child.pid, config: opts }
  } finally {
    closeSync(fd)                   // the child holds its own dup; not closing leaks an fd per session
  }
}

export async function stopListening(pid: number): Promise<void> {
  if (!isProcessRunning(pid)) return

  // Both fallbacks must be guarded. The group can exit between the check above
  // and the signal — routine for --stt and duration-limited sessions — and an
  // unguarded ESRCH here would surface as a 500 for what was a clean stop.
  try {
    process.kill(-pid, 'SIGINT')    // negative pid = whole process group
  } catch {
    try { process.kill(pid, 'SIGINT') } catch { /* already gone — nothing to stop */ }
  }

  for (let waited = 0; waited < 8000 && isProcessRunning(pid); waited += 200) {
    await new Promise(r => setTimeout(r, 200))
  }

  if (isProcessRunning(pid)) {
    // Group-scoped, so rx.py cannot be stranded holding the HackRF.
    try { process.kill(-pid, 'SIGKILL') } catch { /* already gone */ }
  }
}
```

- [ ] **Step 5: Implement `server/utils/session.ts`**

**Why this is not just in-memory.** `server.py` kept `web/listen.pid` on disk, so a session survived a server restart and stayed stoppable. Pure in-memory state is a *hardware* bug, not benign state loss: after any Nitro restart (including a dev-server reload) `isRunning()` returns false while `lwin_listen.sh` is still running. Press Start and a second `rx.py` contends for the same HackRF — then whichever session stops first runs `cleanup`'s **unscoped** `pkill -f "gr-op25_repeater/apps/rx.py"` and kills the other one's receiver too.

The pidfile also restores meaning to `isProcessRunning`'s zombie check: when the pid comes from disk this process is not the child's parent and cannot reap it, so `/proc/<pid>/stat` state `Z` becomes reachable.

```typescript
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import { isProcessRunning } from './processes'
import { listenPidPath, listenConfigPath, listenStartedPath } from './paths'
import type { ListenOptions } from './processes'

export interface Session {
  pid: number
  config: ListenOptions
  startTime: number
}

let current: Session | null = null

function persist(s: Session): void {
  writeFileSync(listenPidPath(), String(s.pid))
  writeFileSync(listenConfigPath(), JSON.stringify(s.config))
  writeFileSync(listenStartedPath(), String(s.startTime))
}

/** Recover a session started before this server process existed. */
function recover(): Session | null {
  try {
    const pid = Number.parseInt(readFileSync(listenPidPath(), 'utf-8').trim(), 10)
    if (!Number.isFinite(pid) || !isProcessRunning(pid)) return null

    let config: ListenOptions = {}
    try {
      config = JSON.parse(readFileSync(listenConfigPath(), 'utf-8')) as ListenOptions
    } catch { /* config is a nicety; the pid is what matters for Stop */ }

    let startTime = Date.now() / 1000
    try {
      const t = Number.parseFloat(readFileSync(listenStartedPath(), 'utf-8').trim())
      if (Number.isFinite(t)) startTime = t
    } catch { /* fall back to now */ }

    return { pid, config, startTime }
  } catch {
    return null
  }
}

function removeSidecars(): void {
  for (const p of [listenPidPath(), listenConfigPath(), listenStartedPath()]) {
    try { unlinkSync(p) } catch { /* already absent */ }
  }
}

export const sessionStore = {
  set(s: Session): void {
    current = s
    persist(s)
  },

  /**
   * The live session, from memory or recovered from the sidecar files.
   * Clears both when the process is gone.
   */
  get(): Session | null {
    if (current && !isProcessRunning(current.pid)) {
      current = null
      removeSidecars()
    }
    if (!current) {
      current = recover()
    }
    return current
  },

  clear(): void {
    current = null
    removeSidecars()
  },

  isRunning(): boolean {
    return this.get() !== null
  },
}
```

All three sidecar paths are already in `.gitignore` (`web/listen.pid`, `web/listen.config.json`, `web/listen.started`) from the Python server — leave those entries in place.

- [ ] **Step 6: Run, confirm pass**

```bash
pnpm vitest run server/utils/processes.test.ts
```

Expected: **7 passed**.

- [ ] **Step 7: Commit**

```bash
git add server/utils/processes.ts server/utils/session.ts server/utils/processes.test.ts
git commit -m "feat: add listen process control and session store"
```

---

## Task 7: Listen API Routes

**Files:**
- Create: `server/api/listen/start.post.ts`
- Create: `server/api/listen/stop.post.ts`
- Create: `server/api/listen/status.get.ts`

**Interfaces:**
- Consumes: `startListening`, `stopListening`, `countCalls`, `readTail` (Task 6); `sessionStore` (Task 6); `listenLogPath` (Task 2).
- Produces: `POST /api/listen/start`, `POST /api/listen/stop`, `GET /api/listen/status`.

- [ ] **Step 1: Create `server/api/listen/start.post.ts`**

```typescript
import { startListening } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'
import type { ListenOptions } from '~/server/utils/processes'

// Verified against make_whitelist.py's PRESETS dict.
const PRESETS = new Set([
  'pd', 'pd-all', 'fire', 'fire-all', 'ems',
  'interop', 'schools', 'publicworks', 'all',
])
const TG_LIST = /^\d+(,\d+)*$/

export default defineEventHandler(async (event) => {
  if (sessionStore.isRunning()) {
    setResponseStatus(event, 409)
    return { success: false, error: 'A listening session is already running' }
  }

  const body = await readBody<ListenOptions>(event)

  if (body.preset && !PRESETS.has(body.preset)) {
    setResponseStatus(event, 400)
    return { success: false, error: `Unknown preset: ${body.preset}` }
  }
  if (body.talkgroups && !TG_LIST.test(body.talkgroups)) {
    setResponseStatus(event, 400)
    return { success: false, error: 'Talkgroups must be a comma-separated list of numbers' }
  }
  if (body.match) {
    // --match is a regex handed to Python's re; reject one that cannot compile
    // here so the failure is a 400 rather than a silently empty whitelist.
    try {
      new RegExp(body.match)
    } catch {
      setResponseStatus(event, 400)
      return { success: false, error: `Not a valid regex: ${body.match}` }
    }
  }
  if (body.duration !== undefined && (!Number.isInteger(body.duration) || body.duration < 1)) {
    setResponseStatus(event, 400)
    return { success: false, error: 'Duration must be a positive integer' }
  }
  // tag and match are selection sources too — requiring preset-or-talkgroups
  // alone would reject a legitimate tag-only or match-only session.
  if (!body.preset && !body.talkgroups && !body.tag && !body.match) {
    setResponseStatus(event, 400)
    return { success: false, error: 'Pick a preset, or enter talkgroup IDs, a tag, or a match regex' }
  }

  try {
    const { pid, config } = startListening(body)
    const startTime = Date.now() / 1000
    sessionStore.set({ pid, config, startTime })
    return { success: true, data: { pid, config, startTime } }
  } catch (err) {
    setResponseStatus(event, 500)
    return { success: false, error: err instanceof Error ? err.message : 'Failed to start' }
  }
})
```

> Validation matters here: `talkgroups` and `preset` are interpolated into a spawned command. `spawn` with an argument array already prevents shell injection, but rejecting malformed input keeps errors legible.

- [ ] **Step 2: Create `server/api/listen/stop.post.ts`**

```typescript
import { stopListening } from '~/server/utils/processes'
import { sessionStore } from '~/server/utils/session'

export default defineEventHandler(async (event) => {
  const session = sessionStore.get()
  if (!session) {
    setResponseStatus(event, 409)
    return { success: false, error: 'No listening session is running' }
  }

  try {
    await stopListening(session.pid)
    sessionStore.clear()
    return { success: true, data: { message: `Stopped session (pid ${session.pid})` } }
  } catch (err) {
    setResponseStatus(event, 500)
    return { success: false, error: err instanceof Error ? err.message : 'Failed to stop' }
  }
})
```

- [ ] **Step 3: Create `server/api/listen/status.get.ts`**

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
      callCount: session ? countCalls(readTail(listenLogPath())) : 0,
      startTime: session?.startTime ?? null,
      lastUpdate: Date.now() / 1000,
    },
  }
})
```

- [ ] **Step 4: Exercise the routes end to end**

```bash
pnpm dev &
sleep 5
curl -s localhost:3000/api/listen/status
curl -s -X POST localhost:3000/api/listen/start \
  -H 'Content-Type: application/json' \
  -d '{"preset":"pd","includePartial":true,"duration":45}'
sleep 8
curl -s localhost:3000/api/listen/status
curl -s -X POST localhost:3000/api/listen/stop
sleep 2
curl -s localhost:3000/api/listen/status
```

Expected: status starts `running:false`; start returns a pid; status shows `running:true` with a `callCount` that is **0 or climbing, never ~1600** (a stale figure means the log redirect in Task 6 is wrong); stop succeeds; status returns to `running:false`.

Confirm the log is this session's, not a leftover:

```bash
wc -c web/listen.log        # small, and freshly written
head -3 web/listen.log      # should be this session's startup lines
```

Confirm no orphans — note `stt_watch` in the pattern, since `--stt` sessions are where an orphan is least obvious:

```bash
pgrep -af 'lwin_listen|rx.py|udp_audio_record|stt_watch' || echo "clean"
```

Expected: `clean`.

Then verify the pidfile recovery path (M1), which is the whole point of the sidecar files:

```bash
curl -s -X POST localhost:3000/api/listen/start \
  -H 'Content-Type: application/json' -d '{"preset":"pd","duration":120}'
kill %1 && sleep 1          # kill the Nitro server, NOT the recorder
pnpm dev & sleep 5
curl -s localhost:3000/api/listen/status    # must still report running:true
curl -s -X POST localhost:3000/api/listen/stop
pgrep -af 'lwin_listen|rx.py|udp_audio_record|stt_watch' || echo "clean"
```

Expected: the restarted server recovers the session from `web/listen.pid` and can still stop it. Without this, pressing Start after a restart would put a second `rx.py` on the HackRF.

- [ ] **Step 5: Commit**

```bash
git add server/api/listen/
git commit -m "feat: add listen start/stop/status API routes"
```

---

## Task 8: Recordings API Routes

**Files:**
- Create: `server/utils/recordings.ts`
- Create: `server/api/recordings/list.get.ts`
- Create: `server/api/recordings/[name].get.ts`

**Interfaces:**
- Consumes: `scanRecordings`, `mergeCalls`, `loadJSON`, `Recording` (Tasks 3–4); `safeRecordingPath`, `recordingsDir`, `referenceDir` (Task 2).
- Produces: `allRecordings(): Recording[]` (in `server/utils/recordings.ts`); `GET /api/recordings/list`; `GET /api/recordings/:name`.

**No `search.get.ts`.** The spec called for one, but the transcript corpus is only 154 KB of text across 3,231 files (~80 ms to read in full), so `list` returns transcripts inline and `RecordingsList` filters all six fields client-side — instant, and no debounce needed. A server-side search route would have been dead code: the component never called it.

`[name].get.ts` serves **both** `.wav` (streamed, Range-aware) and `.txt` (plain text), dispatching on the extension. Nuxt cannot route `[name].txt.get.ts` as a distinct file — the dot is not a route separator — so one handler covers both.

- [ ] **Step 1: Create a shared loader `server/utils/recordings.ts`**

```typescript
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadJSON, scanRecordings, mergeCalls } from './files'
import type { Recording, TalkgroupEntry } from './files'
import { recordingsDir, referenceDir } from './paths'

/**
 * Attach the .txt transcript for each recording.
 *
 * Necessary because calls.json carries no transcript field: stt_watch.py merges
 * transcripts in, then udp_audio_record.py rewrites the file at session end and
 * clobbers them. The .txt files on disk are the only durable copy.
 *
 * Cheap enough to do per request — 3,231 files but only ~154 KB of text total
 * (~48 chars average), measured at ~80 ms. Transcripts must be in the payload
 * so the client can search them, which is the whole point of --stt.
 */
function attachTranscripts(recordings: Recording[]): Recording[] {
  const dir = recordingsDir()
  return recordings.map((rec) => {
    // The .txt file is AUTHORITATIVE — no early return on rec.transcript.
    // calls.json has no transcript key today, but if udp_audio_record.py is
    // ever fixed to stop clobbering stt_watch.py's merges, a `if (rec.transcript)
    // return rec` guard here would silently flip which source wins. Read the
    // file unconditionally; it costs ~80 ms for all 3,231.
    try {
      const txt = readFileSync(join(dir, rec.file.replace(/\.wav$/, '.txt')), 'utf-8').trim()
      return txt ? { ...rec, transcript: txt } : rec
    } catch {
      return rec           // no transcript yet — expected for a fresh recording
    }
  })
}

export function allRecordings(): Recording[] {
  const tgdb = loadJSON<Record<string, TalkgroupEntry>>(
    join(referenceDir(), 'lwin_talkgroups.json'), {},
  )
  const calls = loadJSON<unknown>(join(recordingsDir(), 'calls.json'), [])
  return attachTranscripts(mergeCalls(scanRecordings(recordingsDir(), tgdb), calls))
}
```

If the 80 ms ever matters, cache by `statSync(dir).mtimeMs` — but measure before adding the complexity.

- [ ] **Step 2: Create `server/api/recordings/list.get.ts`**

```typescript
import { allRecordings } from '~/server/utils/recordings'

export default defineEventHandler(() => {
  return { success: true, data: allRecordings() }
})
```

- [ ] **Step 3: Create `server/api/recordings/[name].get.ts`**

This one route serves both the `.wav` (streamed, Range-aware) and the `.txt` transcript, dispatching on the extension. Nitro cannot route a `[name].txt.get.ts` as a distinct file — a dot is not a route separator — and `[name]` does receive a dotted filename, so one handler covers both. This is a deliberate improvement on the spec's two separate routes.

```typescript
import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs'
import { safeRecordingPath } from '~/server/utils/paths'

export default defineEventHandler((event) => {
  const name = getRouterParam(event, 'name') ?? ''
  const path = safeRecordingPath(name)

  if (!path || !existsSync(path)) {
    throw createError({ statusCode: 404, statusMessage: 'Not found' })
  }

  if (name.endsWith('.txt')) {
    setHeader(event, 'Content-Type', 'text/plain; charset=utf-8')
    return readFileSync(path, 'utf-8')
  }

  const size = statSync(path).size
  const range = getHeader(event, 'range')

  setHeader(event, 'Content-Type', 'audio/wav')
  setHeader(event, 'Accept-Ranges', 'bytes')

  if (!range) {
    setHeader(event, 'Content-Length', String(size))
    // Returning a Node Readable directly: h3 handles it, and sendStream() is
    // deprecated in h3 v1.
    return createReadStream(path)
  }

  const m = /^bytes=(\d*)-(\d*)$/.exec(range.trim())
  if (!m) {
    setResponseStatus(event, 416)
    setHeader(event, 'Content-Range', `bytes */${size}`)
    return ''
  }

  const start = m[1] ? Number(m[1]) : 0
  const end = m[2] ? Math.min(Number(m[2]), size - 1) : size - 1

  if (start >= size || end < start) {
    setResponseStatus(event, 416)
    setHeader(event, 'Content-Range', `bytes */${size}`)
    return ''
  }

  setResponseStatus(event, 206)
  setHeader(event, 'Content-Range', `bytes ${start}-${end}/${size}`)
  setHeader(event, 'Content-Length', String(end - start + 1))
  return createReadStream(path, { start, end })
})
```

- [ ] **Step 4: Exercise the routes**

```bash
pnpm dev &
sleep 5

# Count and transcript coverage — transcripts must be present, or client-side
# transcript search silently matches nothing.
curl -s localhost:3000/api/recordings/list \
  | python3 -c "
import json,sys
d = json.load(sys.stdin)['data']
print('recordings:', len(d))
print('with transcript:', sum(1 for r in d if r['transcript']))
print('with dur:', sum(1 for r in d if r['dur']))
print('enc values:', sorted({r['enc'] for r in d}))
"

WAV=$(ls recordings/*.wav 2>/dev/null | head -1 | xargs -r basename)
[ -n "$WAV" ] && curl -s -D- -o /dev/null "localhost:3000/api/recordings/$WAV" | head -6
[ -n "$WAV" ] && curl -s -D- -o /dev/null -H 'Range: bytes=0-1023' "localhost:3000/api/recordings/$WAV" | head -6
[ -n "$WAV" ] && curl -s "localhost:3000/api/recordings/${WAV%.wav}.txt" | head -2

# path traversal must 404
curl -s -o /dev/null -w 'traversal: %{http_code}\n' 'localhost:3000/api/recordings/..%2F..%2Fetc%2Fpasswd'
curl -s -o /dev/null -w 'calls.json: %{http_code}\n' 'localhost:3000/api/recordings/calls.json'
```

Expected: **3232 recordings, ~3231 with transcript**, ~2953 with dur; `enc values: ['clear','full','partial',None]` — if `'encrypted'` appears anywhere, B2 was not applied. Plain GET `200` with `Content-Length`; Range GET `206` with `Content-Range`; both traversal and `calls.json` return `404`.

- [ ] **Step 5: Commit**

```bash
git add server/utils/recordings.ts server/api/recordings/
git commit -m "feat: add recordings list and range-aware streaming with transcripts"
```

---

## Task 9: Talkgroups & Config API Routes

**Files:**
- Create: `server/api/talkgroups/list.get.ts`
- Create: `server/api/talkgroups/whitelist.get.ts`
- Create: `server/api/config/presets.get.ts`

**Interfaces:**
- Consumes: `loadTalkgroups`, `loadWhitelist`, `filterByArea`, `filterTalkgroups` (Task 5); `referenceDir`, `whitelistPath` (Task 2).
- Produces: `GET /api/talkgroups/list`, `GET /api/talkgroups/whitelist`, `GET /api/config/presets`.

- [ ] **Step 1: Create `server/api/talkgroups/list.get.ts`**

```typescript
import { join } from 'node:path'
import { loadTalkgroups, filterByArea, filterTalkgroups } from '~/server/utils/talkgroups'
import { referenceDir } from '~/server/utils/paths'

export default defineEventHandler((event) => {
  const q = getQuery(event)
  const area = q.area === 'all' ? 'all' : 'br'
  const category = q.category ? String(q.category) : undefined
  const text = q.text ? String(q.text) : undefined
  const enc = q.enc ? String(q.enc) : undefined

  const all = loadTalkgroups(join(referenceDir(), 'lwin_talkgroups.json'))
  const data = filterTalkgroups(filterByArea(all, area), { category, text, enc })

  // Entries carry tag and mode as well as tgid/alpha/desc/cat/enc — return them
  // whole. server.py returned both; tag is searched and mode shows "D enc".
  return { success: true, data, total: all.length }
})
```

- [ ] **Step 2: Create `server/api/talkgroups/whitelist.get.ts`**

```typescript
import { join } from 'node:path'
import { loadTalkgroups, loadWhitelist } from '~/server/utils/talkgroups'
import { referenceDir, whitelistPath } from '~/server/utils/paths'

export default defineEventHandler(() => {
  const ids = loadWhitelist(whitelistPath())
  const all = loadTalkgroups(join(referenceDir(), 'lwin_talkgroups.json'))

  return {
    success: true,
    data: {
      tgids: [...ids],
      talkgroups: all.filter(tg => ids.has(tg.tgid)),
    },
  }
})
```

- [ ] **Step 3: Create `server/api/config/presets.get.ts`**

Preset labels mirror the flags documented in README §6.

```typescript
export default defineEventHandler(() => {
  return {
    success: true,
    data: {
      presets: [
        { value: 'pd',          label: 'Police / Sheriff Dispatch' },
        { value: 'pd-all',      label: 'Police — Dispatch + Talk + Tac' },
        { value: 'fire',        label: 'Fire Dispatch' },
        { value: 'fire-all',    label: 'Fire — Dispatch + Tac + Talk' },
        { value: 'ems',         label: 'EMS + Hospital' },
        { value: 'interop',     label: 'Interop / Emergency Ops' },
        { value: 'schools',     label: 'Schools' },
        { value: 'publicworks', label: 'Public Works' },
        { value: 'all',         label: 'All Baton Rouge Area' },
      ],
      areas: [
        { value: 'br',  label: 'Baton Rouge Area' },
        { value: 'all', label: 'Statewide' },
      ],
    },
  }
})
```

- [ ] **Step 4: Exercise the routes**

```bash
jqcount() { python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d['data']))"; }

curl -s 'localhost:3000/api/talkgroups/list?area=all' | jqcount   # expect 4163
curl -s 'localhost:3000/api/talkgroups/list?area=br'  | jqcount   # expect 601
curl -s 'localhost:3000/api/talkgroups/list?area=all&enc=full' | jqcount   # expect 856
curl -s 'localhost:3000/api/talkgroups/list?area=all&text=brpd' | head -c 300
curl -s localhost:3000/api/talkgroups/whitelist | jqcount
curl -s localhost:3000/api/config/presets
```

Expected: **4163 / 601 / 856** exactly — `601` is the headline number, because `[]` here is the signature of B1 not being applied. `enc=full` returning 0 is the signature of B2 not being applied. Whitelist returns 601. Presets returns nine entries.

Also confirm `tag` and `mode` survive to the client:

```bash
curl -s 'localhost:3000/api/talkgroups/list?area=all&text=17165' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['data'][0])"
```

Expected: includes `'tag': 'Law Dispatch'` and `'mode': 'D enc'`.

- [ ] **Step 5: Commit**

```bash
git add server/api/talkgroups/ server/api/config/
git commit -m "feat: add talkgroups and config API routes"
```

---

## Task 10: ListenControl Component

**Files:**
- Create: `components/ListenControl.vue`

**Interfaces:**
- Consumes: `GET /api/config/presets`, `POST /api/listen/start`, `POST /api/listen/stop`, `GET /api/listen/status`.
- Produces: `<ListenControl />` — self-contained, no props, no emits.

- [ ] **Step 1: Create `components/ListenControl.vue`**

```vue
<template>
  <section class="p-4 border-round surface-card">
    <h2 class="text-xl font-bold mt-0 mb-3">Listen &amp; Record</h2>

    <div v-if="running" class="p-3 mb-3 border-round surface-100">
      <div class="flex align-items-center gap-2">
        <Tag value="RECORDING" severity="success" />
        <span class="text-sm text-color-secondary">pid {{ pid }}</span>
      </div>
      <div class="text-2xl font-bold mt-2">{{ callCount }} calls</div>
      <div class="text-sm text-color-secondary">since {{ startedAt }}</div>
      <!-- What the session is actually following. server.py's status line showed
           this; without it you can see that something runs but not what. -->
      <div v-if="configSummary" class="text-sm mt-1">{{ configSummary }}</div>
    </div>

    <div class="flex flex-column gap-3">
      <div>
        <label for="preset" class="block mb-1 text-sm">Preset</label>
        <Select
          id="preset" v-model="preset" :options="presets"
          option-label="label" option-value="value"
          placeholder="Select a preset" :disabled="running" class="w-full"
          show-clear
        />
      </div>

      <div>
        <label for="tgs" class="block mb-1 text-sm">Or explicit talkgroup IDs</label>
        <InputText
          id="tgs" v-model="talkgroups" placeholder="17165,17167,17169"
          :disabled="running" class="w-full"
        />
      </div>

      <div>
        <label for="tag" class="block mb-1 text-sm">Or by tag</label>
        <InputText
          id="tag" v-model="tag" placeholder="Law Dispatch,Law Talk"
          :disabled="running" class="w-full"
        />
      </div>

      <div>
        <label for="match" class="block mb-1 text-sm">Or by regex (alpha / desc / category)</label>
        <InputText
          id="match" v-model="match" placeholder="BRPD"
          :disabled="running" class="w-full"
        />
      </div>

      <!--
        Three INDEPENDENT checkboxes, not a radio group. make_whitelist.py adds
        'partial' only under --include-partial and 'full' only under
        --include-encrypted, so the flags compose. A radio group would make
        "partial AND full" unreachable and would silently drop partial when
        "encrypted" was chosen.
      -->
      <div class="flex align-items-center gap-2">
        <Checkbox input-id="partial" v-model="includePartial" binary :disabled="running" />
        <label for="partial" class="text-sm">Include partially-encrypted TGs (BRPD / EBR SO)</label>
      </div>

      <div class="flex align-items-center gap-2">
        <Checkbox input-id="encrypted" v-model="includeEncrypted" binary :disabled="running" />
        <label for="encrypted" class="text-sm">Include fully-encrypted TGs (records silence)</label>
      </div>

      <div class="flex align-items-center gap-2">
        <Checkbox input-id="allareas" v-model="allAreas" binary :disabled="running" />
        <label for="allareas" class="text-sm">All areas (statewide, not just Baton Rouge)</label>
      </div>

      <div class="flex align-items-center gap-2">
        <Checkbox input-id="stt" v-model="stt" binary :disabled="running" />
        <label for="stt" class="text-sm">Transcribe with Whisper</label>
      </div>

      <div>
        <label for="dur" class="block mb-1 text-sm">Duration (seconds)</label>
        <InputNumber
          id="dur" v-model="duration" :disabled="running"
          :min="1" placeholder="blank = until stopped" class="w-full"
        />
      </div>

      <div class="flex gap-2">
        <Button
          v-if="!running" label="Start" icon="pi pi-play" severity="success"
          :loading="busy" @click="start"
        />
        <Button
          v-else label="Stop" icon="pi pi-stop" severity="danger"
          :loading="busy" @click="stop"
        />
      </div>

      <Message v-if="error" severity="error" :closable="true" @close="error = ''">
        {{ error }}
      </Message>
    </div>
  </section>
</template>

<script setup lang="ts">
interface PresetOption { value: string, label: string }

interface ListenConfig {
  preset?: string
  talkgroups?: string
  tag?: string
  match?: string
  allAreas?: boolean
  includePartial?: boolean
  includeEncrypted?: boolean
  stt?: boolean
  duration?: number
}

interface StatusPayload {
  running: boolean
  pid: number | null
  config: ListenConfig | null
  callCount: number
  startTime: number | null
}

interface ApiResponse<T> { success: boolean, data?: T, error?: string }

// Defaults to 'all', matching make_whitelist.py's own --preset default and the
// old console. Starting at null meant a fresh page + Start returned a 400.
const preset = ref<string | null>('all')
const talkgroups = ref('')
const tag = ref('')
const match = ref('')
const allAreas = ref(false)
const includePartial = ref(false)
const includeEncrypted = ref(false)
const stt = ref(false)
const duration = ref<number | null>(null)

const running = ref(false)
const pid = ref<number | null>(null)
const callCount = ref(0)
const startTime = ref<number | null>(null)
const runningConfig = ref<ListenConfig | null>(null)
const busy = ref(false)
const error = ref('')

const presets = ref<PresetOption[]>([])

// Bumped on stop so RecordingsList knows to reload. calls.json is written only
// at session end, so this is exactly when new metadata becomes available.
const recordingsRefresh = useState<number>('recordings-refresh', () => 0)

const startedAt = computed(() =>
  startTime.value ? new Date(startTime.value * 1000).toLocaleTimeString() : '',
)

const configSummary = computed(() => {
  const c = runningConfig.value
  if (!c) return ''
  const bits: string[] = []
  if (c.preset) bits.push(c.preset)
  if (c.talkgroups) bits.push(`tg ${c.talkgroups}`)
  if (c.tag) bits.push(`tag "${c.tag}"`)
  if (c.match) bits.push(`match /${c.match}/`)
  if (c.allAreas) bits.push('statewide')
  if (c.includePartial) bits.push('+partial')
  if (c.includeEncrypted) bits.push('+encrypted')
  if (c.stt) bits.push('stt')
  if (c.duration) bits.push(`${c.duration}s`)
  return bits.join(' · ')
})

/**
 * ofetch throws a FetchError whose .message is just the status line
 * (`[POST] "/api/listen/start": 409 Conflict`); the handler's JSON body is on
 * .data. Without this every server-side message is invisible to the user.
 */
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

let timer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  try {
    const res = await $fetch<ApiResponse<{ presets: PresetOption[] }>>('/api/config/presets')
    if (res.success && res.data) presets.value = res.data.presets
  } catch {
    error.value = 'Could not load presets'
  }

  await refresh()
  timer = setInterval(refresh, 5000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

async function refresh(): Promise<void> {
  try {
    const res = await $fetch<ApiResponse<StatusPayload>>('/api/listen/status')
    if (!res.success || !res.data) return
    running.value = res.data.running
    pid.value = res.data.pid
    callCount.value = res.data.callCount
    startTime.value = res.data.startTime
    runningConfig.value = res.data.config
  } catch {
    // transient; leave the last known state in place
  }
}

async function start(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const res = await $fetch<ApiResponse<unknown>>('/api/listen/start', {
      method: 'POST',
      body: {
        preset: preset.value ?? undefined,
        talkgroups: talkgroups.value || undefined,
        tag: tag.value || undefined,
        match: match.value || undefined,
        allAreas: allAreas.value,
        includePartial: includePartial.value,
        includeEncrypted: includeEncrypted.value,
        stt: stt.value,
        duration: duration.value ?? undefined,
      },
    })
    if (!res.success) error.value = res.error ?? 'Failed to start'
  } catch (e) {
    error.value = apiError(e, 'Failed to start')
  } finally {
    busy.value = false
    await refresh()
  }
}

async function stop(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const res = await $fetch<ApiResponse<unknown>>('/api/listen/stop', { method: 'POST' })
    if (!res.success) error.value = res.error ?? 'Failed to stop'
  } catch (e) {
    error.value = apiError(e, 'Failed to stop')
  } finally {
    busy.value = false
    await refresh()
    // udp_audio_record.py writes calls.json in its finally block; give it a
    // moment to flush the last call, then tell RecordingsList to reload.
    // server.py's UI did exactly this (setTimeout(loadRecordings, 1500)).
    setTimeout(() => { recordingsRefresh.value++ }, 1500)
  }
}
</script>
```

> `$fetch` throws on non-2xx, so the 400/409 branches arrive in the `catch` — but a `FetchError`'s `.message` is only the status line. `apiError()` reaches into `.data` for the handler's own message. The `res.success` check still covers handlers that return 200 with `success: false`.

- [ ] **Step 2: Verify in the browser — including the CSS shim**

```bash
pnpm dev
```

Open `http://localhost:3000` and check, in order:

1. **The CSS shim works.** Inspect the `<section>` panel in devtools. `background-color` must resolve to a colour, not an unresolved `var(--surface-card)`. If it is unresolved, `assets/css/compat.css` is missing from `nuxt.config.ts`'s `css` array or is ordered before PrimeFlex.
2. The preset dropdown populates with nine entries and shows `all` selected.
3. Start a 45-second session with `pd` + "Include partially-encrypted". The panel flips to RECORDING, shows the pid, and `configSummary` reads `pd · +partial · 45s`.
4. The call count climbs from 0. **A count near 1600 that never changes means the Task 6 log redirect is missing** and it is reading the stale pre-migration `web/listen.log`.
5. Press Stop. The panel returns to idle, and no `lwin_listen`/`rx.py`/`stt_watch` processes remain.
6. Trigger a validation error deliberately — clear the preset and all four selection fields, press Start — and confirm the message reads *"Pick a preset, or enter talkgroup IDs, a tag, or a match regex"* rather than `[POST] "/api/listen/start": 400 Bad Request`. That is the `apiError()` path.

- [ ] **Step 3: Commit**

```bash
git add components/ListenControl.vue
git commit -m "feat: add ListenControl component"
```

---

## Task 11: RecordingsList Component

**Files:**
- Create: `components/RecordingsList.vue`

**Interfaces:**
- Consumes: `GET /api/recordings/list`, `GET /api/recordings/:name` (audio + transcript).
- Produces: `<RecordingsList />` — no props, no emits.

- [ ] **Step 1: Create `components/RecordingsList.vue`**

```vue
<template>
  <section class="p-4 border-round surface-card">
    <div class="flex align-items-center justify-content-between mb-3">
      <h2 class="text-xl font-bold m-0">Recordings</h2>
      <Button icon="pi pi-refresh" text rounded :loading="loading" @click="load" />
    </div>

    <div class="flex gap-2 mb-3">
      <InputText v-model="search" placeholder="Search TG, alpha, description" class="flex-1" />
      <Select
        v-model="encFilter" :options="encOptions"
        option-label="label" option-value="value" class="w-10rem"
      />
    </div>

    <DataTable
      :value="filtered" :loading="loading" paginator :rows="10"
      data-key="file" size="small" striped-rows
    >
      <template #empty>No recordings yet.</template>

      <Column field="tgid" header="TG" style="width: 6rem" />
      <Column field="alpha" header="Talkgroup">
        <template #body="{ data }">
          {{ data.alpha ?? '—' }}
        </template>
      </Column>
      <Column header="Transcript">
        <template #body="{ data }">
          <span
            v-if="data.transcript"
            class="text-sm"
            :class="{ blank: isBlank(data.transcript) }"
          >{{ truncate(data.transcript) }}</span>
          <span v-else class="text-sm text-color-secondary">—</span>
        </template>
      </Column>
      <Column header="When" style="width: 11rem">
        <template #body="{ data }">{{ formatTime(data.start) }}</template>
      </Column>
      <Column header="Len" style="width: 5rem">
        <template #body="{ data }">{{ formatDuration(data.dur) }}</template>
      </Column>
      <Column header="Enc" style="width: 7rem">
        <template #body="{ data }">
          <Tag :value="data.enc ?? 'unknown'" :severity="encSeverity(data.enc)" />
        </template>
      </Column>
      <Column header="" style="width: 4rem">
        <template #body="{ data }">
          <Button icon="pi pi-play" text rounded @click="open(data)" />
        </template>
      </Column>
    </DataTable>

    <Dialog
      v-model:visible="dialogOpen" modal
      :header="selected?.alpha ?? selected?.file ?? 'Recording'"
      :style="{ width: '40rem', maxWidth: '95vw' }"
    >
      <div v-if="selected" class="flex flex-column gap-3">
        <audio :src="`/api/recordings/${selected.file}`" controls class="w-full" />

        <div class="text-sm">
          <div><strong>TG:</strong> {{ selected.tgid ?? '—' }}</div>
          <div><strong>Description:</strong> {{ selected.desc ?? '—' }}</div>
          <div><strong>Category:</strong> {{ selected.cat ?? '—' }}</div>
          <div><strong>Encryption:</strong> {{ selected.enc ?? 'unknown' }}</div>
          <div><strong>Recorded:</strong> {{ formatTime(selected.start) }}</div>
        </div>

        <div>
          <h3 class="text-base font-bold mb-2">Transcript</h3>
          <ProgressSpinner v-if="loadingTranscript" style="width: 2rem; height: 2rem" />
          <p
            v-else-if="transcript"
            class="m-0 text-sm line-height-3"
            :class="{ blank: isBlank(transcript) }"
          >{{ transcript }}</p>
          <p v-else class="m-0 text-sm text-color-secondary">No transcript for this call.</p>
        </div>
      </div>
    </Dialog>
  </section>
</template>

<script setup lang="ts">
interface Recording {
  file: string
  tgid: number | null
  alpha: string | null
  desc: string | null
  cat: string | null
  enc: 'clear' | 'partial' | 'full' | null
  start: number
  dur: number
  transcript: string | null
}

interface ApiResponse<T> { success: boolean, data?: T, error?: string }

const recordings = ref<Recording[]>([])
const loading = ref(false)
const search = ref('')
const encFilter = ref('all')

const dialogOpen = ref(false)
const selected = ref<Recording | null>(null)
const transcript = ref('')
const loadingTranscript = ref(false)

// Real vocabulary: 'full', never 'encrypted'. 'none' covers recordings whose
// talkgroup is not in the reference DB — 279 of 3,232 have no calls.json entry,
// and the old console had this option for exactly that reason.
const encOptions = [
  { value: 'all',     label: 'All' },
  { value: 'clear',   label: 'Clear' },
  { value: 'partial', label: 'Partial' },
  { value: 'full',    label: 'Full' },
  { value: 'none',    label: 'Unlabelled' },
]

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return recordings.value.filter((r) => {
    if (encFilter.value === 'none') {
      if (r.enc) return false
    } else if (encFilter.value !== 'all' && r.enc !== encFilter.value) {
      return false
    }
    if (!q) return true
    // Six fields, matching server.py's [alpha, desc, cat, transcript, file, tgid].
    // Transcript search is the point of --stt: 3,231 transcripts on disk.
    return String(r.tgid ?? '').includes(q)
      || (r.alpha ?? '').toLowerCase().includes(q)
      || (r.desc ?? '').toLowerCase().includes(q)
      || (r.cat ?? '').toLowerCase().includes(q)
      || (r.transcript ?? '').toLowerCase().includes(q)
      || r.file.toLowerCase().includes(q)
  })
})

// Bumped by ListenControl 1.5 s after Stop, when calls.json has been flushed.
const recordingsRefresh = useState<number>('recordings-refresh', () => 0)
watch(recordingsRefresh, load)

onMounted(load)

async function load(): Promise<void> {
  loading.value = true
  try {
    const res = await $fetch<ApiResponse<Recording[]>>('/api/recordings/list')
    if (res.success && res.data) recordings.value = res.data
  } catch {
    recordings.value = []
  } finally {
    loading.value = false
  }
}

async function open(rec: Recording): Promise<void> {
  selected.value = rec
  dialogOpen.value = true

  if (rec.transcript) {
    transcript.value = rec.transcript
    return
  }

  transcript.value = ''
  loadingTranscript.value = true
  try {
    transcript.value = await $fetch<string>(
      `/api/recordings/${rec.file.replace(/\.wav$/, '.txt')}`,
    )
  } catch {
    transcript.value = ''
  } finally {
    loadingTranscript.value = false
  }
}

function formatTime(ts: number): string {
  return ts ? new Date(ts * 1000).toLocaleString() : '—'
}

function formatDuration(sec: number): string {
  if (!sec) return '—'
  return sec < 60 ? `${sec.toFixed(1)}s` : `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`
}

/**
 * op25's -n silences encrypted bursts, so partial-encryption talkgroups produce
 * many calls that transcribe to exactly [BLANK_AUDIO] — 528 of 3,231 today.
 * Without dimming they are indistinguishable from real content at a glance.
 */
function isBlank(t: string): boolean {
  return t.startsWith('[BLANK_AUDIO]')
}

function truncate(t: string, n = 60): string {
  return t.length > n ? `${t.slice(0, n)}…` : t
}

function encSeverity(enc: string | null): string {
  if (enc === 'clear') return 'success'
  if (enc === 'partial') return 'warn'   // PrimeVue 4 uses 'warn', not 'warning'
  if (enc === 'full') return 'danger'
  return 'secondary'
}
</script>

<style scoped>
/* [BLANK_AUDIO] — a silenced encrypted burst, not speech. */
.blank {
  opacity: 0.5;
  font-style: italic;
}
</style>
```

- [ ] **Step 2: Verify in the browser**

Confirm the table lists existing `recordings/*.wav` (3232 rows), search narrows it, the encryption filter works, and clicking play opens a dialog whose audio element plays **and seeks** (seeking is what exercises the Range path).

Then isolate the cross-component refresh, which is the only new shared-state machinery in this plan. Start a short session, press Stop, and in devtools watch the `recordings-refresh` state value:

- **Increments on Stop, table reloads** → working.
- **Increments, table does not reload** → the `watch()` in this component is wrong.
- **Never increments** → `ListenControl`'s `setTimeout` or its `useState` key is wrong (the key string must match exactly in both components).

Checking the counter separately from the table matters because "table didn't reload" otherwise has four indistinguishable causes — including the innocent one, that `calls.json` had nothing new to flush.

- [ ] **Step 3: Commit**

```bash
git add components/RecordingsList.vue
git commit -m "feat: add RecordingsList component with playback and transcripts"
```

---

## Task 12: TalkgroupBrowser Component

**Files:**
- Create: `components/TalkgroupBrowser.vue`

**Interfaces:**
- Consumes: `GET /api/talkgroups/list`, `GET /api/talkgroups/whitelist`, `GET /api/config/presets` (for the area options).
- Produces: `<TalkgroupBrowser />` — no props, no emits.

- [ ] **Step 1: Create `components/TalkgroupBrowser.vue`**

```vue
<template>
  <section class="p-4 border-round surface-card">
    <h2 class="text-xl font-bold mt-0 mb-3">Talkgroups</h2>

    <div class="flex gap-2 mb-3 flex-wrap">
      <Select
        v-model="area" :options="areaOptions"
        option-label="label" option-value="value"
        class="w-12rem" @change="load"
      />
      <Select
        v-model="category" :options="categoryOptions"
        option-label="label" option-value="value"
        placeholder="All categories" class="w-14rem" show-clear
      />
      <Select
        v-model="encFilter" :options="encOptions"
        option-label="label" option-value="value" class="w-10rem"
      />
      <InputText v-model="search" placeholder="Search TG, alpha, desc, category, tag" class="flex-1" />
    </div>

    <DataTable
      :value="filtered" :loading="loading" paginator :rows="12"
      data-key="tgid" size="small" striped-rows :row-class="rowClass"
    >
      <template #empty>No talkgroups match.</template>

      <Column field="tgid" header="TG" style="width: 6rem" />
      <Column field="alpha" header="Alpha" style="width: 12rem" />
      <Column field="desc" header="Description" />
      <Column field="cat" header="Category" style="width: 14rem" />
      <Column field="tag" header="Tag" style="width: 9rem" />
      <Column header="Enc" style="width: 7rem">
        <template #body="{ data }">
          <Tag :value="data.enc" :severity="encSeverity(data.enc)" />
          <!-- mode is "D enc" for encrypted talkgroups, "D" otherwise -->
          <div class="text-sm text-color-secondary">{{ data.mode }}</div>
        </template>
      </Column>
      <Column header="Whitelist" style="width: 7rem">
        <template #body="{ data }">
          <Tag v-if="whitelist.has(data.tgid)" value="active" severity="info" />
        </template>
      </Column>
    </DataTable>

    <!--
      Counts, not just a legend sentence. This is how you notice at a glance that
      a filtering bug has silently emptied the table — the failure mode B1 caused.
    -->
    <p class="text-sm text-color-secondary mt-3 mb-0">
      showing {{ filtered.length }} of {{ talkgroups.length }} talkgroups
      ({{ area === 'br' ? 'Baton Rouge area' : 'statewide' }})
      · {{ whitelist.size }} in the current
      <code>lwin_active_whitelist.txt</code>, marked
      <Tag value="active" severity="info" />
    </p>
  </section>
</template>

<script setup lang="ts">
interface Talkgroup {
  tgid: number
  alpha: string
  desc: string
  cat: string
  enc: 'clear' | 'partial' | 'full'
  tag: string
  mode: string
}

interface Option { value: string, label: string }
interface ApiResponse<T> { success: boolean, data?: T, error?: string }

const area = ref<'br' | 'all'>('br')
const category = ref<string | null>(null)
const encFilter = ref('all')
const search = ref('')

const talkgroups = ref<Talkgroup[]>([])
const whitelist = ref<Set<number>>(new Set())
const loading = ref(false)

const areaOptions: Option[] = [
  { value: 'br',  label: 'Baton Rouge Area' },
  { value: 'all', label: 'Statewide' },
]

const categoryOptions = computed<Option[]>(() => {
  const cats = new Set(talkgroups.value.map(t => t.cat).filter(Boolean))
  return [...cats].sort().map(c => ({ value: c, label: c }))
})

const encOptions: Option[] = [
  { value: 'all',     label: 'All encryption' },
  { value: 'clear',   label: 'Clear' },
  { value: 'partial', label: 'Partial' },
  { value: 'full',    label: 'Full' },
]

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return talkgroups.value.filter((t) => {
    if (category.value && t.cat !== category.value) return false
    if (encFilter.value !== 'all' && t.enc !== encFilter.value) return false
    if (!q) return true
    // server.py searched [alpha, desc, cat, tag, tgid].
    return String(t.tgid).includes(q)
      || (t.alpha ?? '').toLowerCase().includes(q)
      || (t.desc ?? '').toLowerCase().includes(q)
      || (t.cat ?? '').toLowerCase().includes(q)
      || (t.tag ?? '').toLowerCase().includes(q)
  })
})

onMounted(async () => {
  await Promise.all([load(), loadWhitelist()])
})

async function load(): Promise<void> {
  loading.value = true
  category.value = null
  try {
    const res = await $fetch<ApiResponse<Talkgroup[]>>('/api/talkgroups/list', {
      query: { area: area.value },
    })
    if (res.success && res.data) talkgroups.value = res.data
  } catch {
    talkgroups.value = []
  } finally {
    loading.value = false
  }
}

async function loadWhitelist(): Promise<void> {
  try {
    const res = await $fetch<ApiResponse<{ tgids: number[] }>>('/api/talkgroups/whitelist')
    if (res.success && res.data) whitelist.value = new Set(res.data.tgids)
  } catch {
    whitelist.value = new Set()
  }
}

function rowClass(data: Talkgroup): string {
  return whitelist.value.has(data.tgid) ? 'surface-100' : ''
}

function encSeverity(enc: string): string {
  if (enc === 'clear') return 'success'
  if (enc === 'partial') return 'warn'   // PrimeVue 4 uses 'warn', not 'warning'
  if (enc === 'full') return 'danger'
  return 'secondary'
}
</script>
```

- [ ] **Step 2: Verify in the browser**

Confirm, with exact numbers so the check is falsifiable:

1. **Baton Rouge area shows `showing 601 of 601`.** An empty table means B1 was not applied.
2. **Statewide shows `showing 4163 of 4163`.**
3. Statewide + encryption `Full` shows **856**; `Partial` shows **114**; `Clear` shows **3193**. Zero rows for `Full` means B2 was not applied.
4. The `601` in the footer matches `wc -l < lwin_active_whitelist.txt`.
5. The category dropdown repopulates when area changes.
6. **Whitelisted rows are shaded, not just badged.** If the `active` badge appears but the row has no background tint, `assets/css/compat.css` is missing — `rowClass` returns `surface-100`, which is a PrimeFlex theme class.
7. Searching `law dispatch` matches on the `tag` column.
8. Talkgroup 17165 shows `mode` as `D enc`.

- [ ] **Step 3: Commit**

```bash
git add components/TalkgroupBrowser.vue
git commit -m "feat: add TalkgroupBrowser component"
```

---

## Task 13: Dashboard Layout & Production Verification

**Files:**
- Create: `app.vue`
- Create: `pages/index.vue`

**Interfaces:**
- Consumes: all three components (auto-imported from `components/`).
- Produces: the dashboard at `/`.

- [ ] **Step 1: Create `app.vue`**

```vue
<template>
  <NuxtPage />
</template>
```

- [ ] **Step 2: Create `pages/index.vue`**

```vue
<template>
  <main class="p-4">
    <header class="mb-4">
      <h1 class="text-3xl font-bold m-0">SDR Console</h1>
      <p class="text-color-secondary mt-1 mb-0">
        Baton Rouge LWIN P25 — recording, playback and talkgroup reference
      </p>
    </header>

    <div class="grid">
      <div class="col-12 lg:col-4">
        <ListenControl />
      </div>
      <div class="col-12 lg:col-8">
        <RecordingsList />
      </div>
      <div class="col-12">
        <TalkgroupBrowser />
      </div>
    </div>
  </main>
</template>

<script setup lang="ts">
useHead({ title: 'SDR Console — LWIN P25' })
</script>
```

- [ ] **Step 3: Full manual pass in dev**

```bash
pnpm dev
```

Walk every acceptance check. Numbers are the point — "it looks fine" would have passed the pre-review plan.

**Chrome (do this first — everything else is easier to judge once it renders):**
- Panels have a background and rounded corners; the body is not raw white. Inspect one: no unresolved `var()`.

**Listen & Record:**
- Preset dropdown has 9 entries, `all` preselected.
- Start `pd` + partial for 60 s → RECORDING, pid shown, summary reads `pd · +partial · 60s`.
- Call count starts at **0** and climbs. Not ~1600.
- Stop → idle, and `pgrep -af 'lwin_listen|rx.py|udp_audio_record|stt_watch'` prints nothing.
- **The recordings table reloads on its own ~1.5 s after Stop**, showing the new calls with real durations.
- Restart the dev server mid-session; status still reports running and Stop still works.

**Recordings:**
- Table shows **3232** rows.
- A recording plays **and seeks** (seeking is what exercises the Range path).
- Search a word you know is in a transcript — it matches. Search `brpd` — it matches alpha.
- Encryption filter: `Full` → 241 rows, `Partial` → 773, `Clear` → 1939, `Unlabelled` → 279.
- A `[BLANK_AUDIO]` transcript renders dimmed and italic.

**Talkgroups:**
- BR area `showing 601 of 601`; Statewide `4163 of 4163`; Statewide + Full `856`.
- Whitelisted rows are **shaded**, not only badged.

**LAN:** load `http://10.56.1.77:3000` from another machine.

- [ ] **Step 4: Production build**

```bash
pnpm build
node .output/server/index.mjs &
sleep 5
curl -s localhost:3000/api/listen/status
curl -s localhost:3000/api/talkgroups/list?area=br \
  | python3 -c "import json,sys;print(len(json.load(sys.stdin)['data']))"
WAV=$(ls recordings/*.wav | head -1 | xargs basename)
curl -s -o /dev/null -w '%{http_code}\n' -H 'Range: bytes=0-1023' "localhost:3000/api/recordings/$WAV"
```

Expected: build succeeds; status answers; talkgroups returns **601**; the Range request returns **206**.

- [ ] **Step 5: Full test suite**

```bash
pnpm vitest run
```

Expected: **4 + 8 + 8 + 7 = 27 passed** across `paths`, `files`, `talkgroups`, `processes`.

- [ ] **Step 6: Commit**

```bash
git add app.vue pages/index.vue
git commit -m "feat: add dashboard layout"
```

---

## Task 14: Documentation & Retire the Python Server

**Files:**
- Modify: `README.md` (§ "Web console" and the "Layout" block)
- Modify: `.gitignore` (drop `web/` runtime entries that no longer exist; add `web/listen.log` only if the log still lands there)
- Delete: `web/server.py`, `web/index.html`, `web/serve.sh`

Do this **only after** Task 13 passes end to end.

- [ ] **Step 1: Confirm nothing outside the Nitro server depends on `web/`**

Already established (Task 0 Step 5): **nothing** in `scripts/` writes `web/listen.log` — it existed only because `server.py` did `Popen(..., stdout=open(logpath, 'ab'), stderr=STDOUT)`. The Nitro server now owns all four sidecar files. Confirm no other references linger:

```bash
grep -rn "web/listen\|web/server\|web/index.html\|serve\.sh" scripts/ README.md
```

Expected: hits in `README.md` only (which this task rewrites). Any hit in `scripts/` is a real dependency — resolve it before deleting anything.

- [ ] **Step 2: Rewrite README § Web console**

```markdown
## Web console

A Nuxt 3 + PrimeVue 4 app (`pages/`, `components/`, `server/api/`) replaces the
old Python stdlib server.

```bash
pnpm install
pnpm dev             # http://0.0.0.0:3000, hot reload
# or
pnpm build && node .output/server/index.mjs
```

Open **http://10.56.1.77:3000/** (or **http://127.0.0.1:3000/**) — three panels:

- **Listen & Record** — select by preset, explicit talkgroup IDs, tag
  (`--tag "Law Dispatch"`) or regex (`--match BRPD`); statewide via
  `--all-areas`; independent `--include-partial` / `--include-encrypted`
  switches; Whisper STT; duration. Start spawns `scripts/lwin_listen.sh` in its
  own process group and captures its output to `web/listen.log`; Stop sends
  SIGINT to the group, which `lwin_listen.sh`'s own `cleanup` trap turns into an
  orderly teardown of op25, the recorder and the STT watcher. Status polls every
  5 s and shows the live call count, the pid and what the session is following.
- **Recordings** — every `recordings/TG*.wav` with metadata from
  `recordings/calls.json` and the reference DB, plus its Whisper transcript.
  Search covers talkgroup, alpha, description, category, filename **and
  transcript text**; the encryption filter offers clear / partial / full /
  unlabelled. `[BLANK_AUDIO]` transcripts (a silenced encrypted burst, not
  speech) are dimmed. Audio is served with HTTP Range support so seeking works.
- **Talkgroups** — the reference DB, Baton Rouge area (601) or statewide (4163),
  filterable by category and encryption, searchable including the `tag` field;
  rows in `lwin_active_whitelist.txt` are marked and shaded.

A running session is tracked in `web/listen.pid`, so restarting the web server
does not lose the ability to stop it.
```

- [ ] **Step 3: Update the README "Layout" block**

```
components/ ListenControl.vue RecordingsList.vue TalkgroupBrowser.vue
pages/      index.vue                <- dashboard
server/     api/ + utils/            <- Nitro routes, process control
scripts/    lwin_listen.sh           <- start listening (one command)
            ...
```

- [ ] **Step 4: Remove the Python server**

Delete, don't archive — it is in git history if ever needed, and a `server.py.old` next to a live Nuxt app is a trap for the next reader.

```bash
git rm web/server.py web/index.html web/serve.sh

# Untracked leftovers from the Python era
rm -rf web/__pycache__ web/server.pid web/server.lock
```

`web/` survives as the home of the four gitignored sidecar files (`listen.log`, `listen.pid`, `listen.config.json`, `listen.started`), so do not remove the directory itself.

- [ ] **Step 5: Full verification after removal**

```bash
pnpm build
node .output/server/index.mjs &
sleep 5
curl -s localhost:3000/api/listen/status
curl -s localhost:3000/api/recordings/list | head -c 200
pnpm vitest run
git status --short          # nothing stray left in web/
```

Expected: build clean, both routes answer, **27 tests pass**, and `web/` shows only gitignored sidecar files.

- [ ] **Step 6: Commit**

```bash
git add README.md .gitignore
git commit -m "docs: document Nuxt web console and remove Python server"
```

---

## Acceptance Criteria

Each carries the number that makes it falsifiable. A criterion that can be satisfied by "it looks fine" is not a criterion — three of the pre-review versions of these were unreachable and passed inspection anyway.

**Build and tests**
- [ ] `pnpm build` succeeds with no TypeScript errors, and no `any` appears anywhere in `server/` or `components/`
- [ ] `pnpm vitest run` → **27 passed**

**Rendering**
- [ ] Panels have a resolved background colour (no unresolved `var(--surface-card)` in devtools)
- [ ] Whitelisted talkgroup rows are **shaded**, not only badged

**Listen**
- [ ] Preset dropdown has 9 entries with `all` preselected; Start works on a fresh page with no other input
- [ ] Call count starts at **0** and climbs — never a stale ~1600
- [ ] Status shows the pid and a config summary of what is being followed
- [ ] Partial and fully-encrypted are **independently** selectable; both on emits both flags
- [ ] `--tag`, `--match` and `--all-areas` are all reachable from the UI
- [ ] Stop leaves no `lwin_listen` / `rx.py` / `udp_audio_record` / **`stt_watch`** processes
- [ ] Restarting the web server mid-session preserves the ability to Stop (pidfile recovery)
- [ ] A deliberate validation error shows the handler's message, not `400 Bad Request`

**Recordings**
- [ ] `/api/recordings/list` returns **3232** rows, ~**3231** with a transcript
- [ ] A recording plays **and seeks**; Range requests return **206**, malformed ranges **416**
- [ ] Search matches on transcript text, not just alpha/desc
- [ ] Encryption filter: Full **241**, Partial **773**, Clear **1939**, Unlabelled **279**
- [ ] `[BLANK_AUDIO]` transcripts render dimmed
- [ ] The table reloads ~1.5 s after Stop, showing the new calls with real durations
- [ ] Path traversal and `calls.json` on `/api/recordings/:name` both return **404**

**Talkgroups**
- [ ] BR area **601**, statewide **4163**, statewide+Full **856**, +Partial **114**, +Clear **3193**
- [ ] The footer's whitelist count matches `wc -l < lwin_active_whitelist.txt` (601)
- [ ] Search matches on `tag`; `mode` is displayed (`D enc` for 17165)

**Deployment**
- [ ] Reachable from another machine at `http://10.56.1.77:3000`
- [ ] `web/server.py`, `web/index.html`, `web/serve.sh` are gone; `web/` holds only gitignored sidecar files
- [ ] README describes the Nuxt app, including the features restored from the old console

## Known Gaps (deliberate)

- **Status is polled every 5 s, not pushed.** SSE is a later upgrade; polling is adequate on a LAN with one user.
- **No auth.** LAN-only by design, same as the Python server.
- **Per-row inline `<audio>` players replaced by a Dialog.** The old console rendered `<audio controls preload="metadata">` on every row, so you could scan and play without a click. The Dialog carries more metadata and scales better to 3,232 rows, but it is a real workflow change — and `preload="metadata"` was also how the old UI showed true durations for the 279 recordings whose `calls.json` `dur` is missing. If that matters, add `preload="metadata"` inline players back to the table.
- **`calls.json` lags a live session.** `udp_audio_record.py` writes it only at session end, so during a session new recordings show `dur: 0` and a filename-derived start. `server.py` behaved identically. The post-Stop refresh is the mitigation.
- **`stt_watch.py`'s transcript merges into `calls.json` are clobbered** when `udp_audio_record.py` rewrites the file at session end. This is a pre-existing bug in the Python tooling, not something the web console introduces; the `.txt` files are read directly to work around it. Worth fixing in `udp_audio_record.py` separately.
