# SDR Web Console — Nuxt 3 + PrimeVue 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the Python stdlib web console (`web/server.py`) to a Nuxt 3 + TypeScript full-stack app with PrimeVue 4 components, supporting listen/record control, audio playback, and talkgroup browsing.

**Architecture:** Single Nuxt 3 application with Vue 3 components and TypeScript Nitro server routes for process orchestration. Keeps the existing file layout (`recordings/`, `reference/`, `scripts/`) untouched. Status updates via 5-second polling.

**Tech Stack:** Nuxt 3, Vue 3, PrimeVue 4, PrimeFlex, TypeScript, Node.js `child_process`

**Spec:** `docs/superpowers/specs/2026-08-31-web-console-nuxt-migration.md`

## Global Constraints

- Node.js 18+ required
- Serve on `0.0.0.0:3000` (or configured PORT) — must be LAN-accessible
- Recordings and metadata stay in `/home/besquivel/rtl/recordings/`; reference DB in `/home/besquivel/rtl/reference/`
- API response format: `{ success: boolean, data?: T, error?: string }`
- No breaking changes to `lwin_listen.sh` or any other shell script
- All types explicitly declared — no `any` (per user's global CLAUDE.md conventions)
- Nuxt app lives at the repo root (`/home/besquivel/rtl`), alongside the existing `scripts/`, `reference/`, `recordings/`

---

## File Structure

```
/home/besquivel/rtl/
├── nuxt.config.ts                    create
├── tsconfig.json                     create
├── package.json                      create
├── app.vue                           create
├── plugins/primevue.ts               create
├── layouts/default.vue               create
├── pages/index.vue                   create
├── components/
│   ├── ListenControl.vue             create
│   ├── RecordingsList.vue            create
│   └── TalkgroupBrowser.vue          create
├── server/
│   ├── api/
│   │   ├── listen/{start.post,stop.post,status.get}.ts
│   │   ├── recordings/{list.get,search.get,[name].get}.ts
│   │   ├── talkgroups/{list.get,whitelist.get}.ts
│   │   └── config/presets.get.ts
│   └── utils/
│       ├── paths.ts                  create (path resolution)
│       ├── processes.ts              create (spawn/stop/log tail)
│       ├── session.ts                create (in-memory session store)
│       ├── files.ts                  create (JSON, scan, audio stream)
│       └── talkgroups.ts             create (reference DB filters)
└── web/server.py                     archive at the end
```

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
    "typecheck": "nuxt typecheck"
  },
  "dependencies": {
    "nuxt": "^3.14.0",
    "vue": "^3.5.0",
    "primevue": "^4.2.0",
    "@primevue/themes": "^4.2.0",
    "primeicons": "^7.0.0",
    "primeflex": "^3.3.1"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "typescript": "^5.6.0",
    "vue-tsc": "^2.1.0"
  }
}
```

- [ ] **Step 2: Create `nuxt.config.ts`**

PrimeVue 4 ships a Nuxt module (`@primevue/nuxt-module`) but it is a separate package; this plan registers PrimeVue through a plugin instead to keep the dependency list minimal.

```typescript
export default defineNuxtConfig({
  compatibilityDate: '2025-01-01',
  devtools: { enabled: true },
  css: [
    'primeicons/primeicons.css',
    'primeflex/primeflex.css',
  ],
  build: {
    transpile: ['primevue'],
  },
  typescript: {
    strict: true,
    typeCheck: false,
  },
  runtimeConfig: {
    sdrRoot: process.env.SDR_ROOT || '/home/besquivel/rtl',
  },
})
```

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
import RadioButton from 'primevue/radiobutton'
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
  nuxtApp.vueApp.component('RadioButton', RadioButton)
  nuxtApp.vueApp.component('Select', Select)
  nuxtApp.vueApp.component('Tag', Tag)
})
```

> Note: PrimeVue 4 renamed `Dropdown` to `Select`. `Dropdown` still exists as a deprecated alias; this plan uses `Select`.

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

- [ ] **Step 6: Install and verify the dev server boots**

```bash
cd /home/besquivel/rtl
npm install
npm run dev
```

Expected: server listens on `http://0.0.0.0:3000`, no module resolution errors. Stop with Ctrl-C.

- [ ] **Step 7: Commit**

```bash
git add package.json package-lock.json nuxt.config.ts tsconfig.json plugins/ .gitignore
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
npx vitest run server/utils/paths.test.ts
```

Expected: FAIL — `Cannot find module './paths'`.

(If vitest is not installed: `npm i -D vitest @vitejs/plugin-vue` and add `"test": "vitest run"` to `package.json` scripts. Do this once, here.)

- [ ] **Step 3: Implement `server/utils/paths.ts`**

```typescript
import { join, resolve, basename } from 'node:path'

const RECORDING_NAME = /^TG\d+_[A-Za-z0-9.\-]+_\d{8}-\d{6}(?:_\d+)?\.(wav|txt)$/

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

export function listenLogPath(): string {
  return join(sdrRoot(), 'web', 'listen.log')
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
npx vitest run server/utils/paths.test.ts
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
  - `type Encryption = 'clear' | 'partial' | 'encrypted'`
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
  it('extracts tgid and a UTC timestamp', () => {
    const r = parseRecordingFilename('TG17165_17-BRPD-DSP1_20260830-170008.wav')
    expect(r.tgid).toBe(17165)
    // 2026-08-30T17:00:08Z
    expect(r.start).toBe(Date.UTC(2026, 7, 30, 17, 0, 8) / 1000)
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
npx vitest run server/utils/files.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `server/utils/files.ts`**

Timestamps in filenames are local wall-clock from `udp_audio_record.py`. This implementation treats them as UTC for determinism; the UI formats with `toLocaleString()`. If local-time interpretation is wanted later, change `Date.UTC` to `new Date(y, m, d, ...)` in one place.

```typescript
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

export type Encryption = 'clear' | 'partial' | 'encrypted'

export interface TalkgroupEntry {
  tgid: number
  alpha: string
  desc: string
  cat: string
  enc: Encryption
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
  const start = Date.UTC(
    Number(y), Number(mo) - 1, Number(d),
    Number(h), Number(mi), Number(s),
  ) / 1000

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
npx vitest run server/utils/files.test.ts
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

**Prerequisite:** inspect the real shape of `recordings/calls.json` before implementing — it may be an array or an object keyed by filename.

```bash
head -c 600 /home/besquivel/rtl/recordings/calls.json
```

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
npx vitest run server/utils/files.test.ts
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

  return recordings.map((rec) => {
    const c = byFile.get(rec.file)
    if (!c) return rec

    const merged: Recording = { ...rec }
    for (const key of Object.keys(c) as (keyof Recording)[]) {
      const v = c[key]
      if (v !== null && v !== undefined) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ;(merged as any)[key] = v
      }
    }
    merged.file = rec.file
    return merged
  })
}
```

- [ ] **Step 4: Run, confirm pass**

```bash
npx vitest run server/utils/files.test.ts
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

**Prerequisite:** confirm the JSON shape and the field names actually used.

```bash
python3 -c "import json;d=json.load(open('/home/besquivel/rtl/reference/lwin_talkgroups.json'));k=list(d)[:2];print(type(d));print({i:d[i] for i in k})"
```

Adjust the `TalkgroupEntry` field names in Task 3 if they differ from `alpha`/`desc`/`cat`/`enc`.

- [ ] **Step 1: Write the failing test**

```typescript
// server/utils/talkgroups.test.ts
import { describe, it, expect } from 'vitest'
import { filterByArea, filterTalkgroups } from './talkgroups'
import type { TalkgroupEntry } from './files'

const tgs: TalkgroupEntry[] = [
  { tgid: 17165, alpha: '17-BRPD DSP1', desc: 'BR Police Dispatch 1', cat: 'EBR (17) BR Police', enc: 'partial' },
  { tgid: 6039,  alpha: 'LDWF R4-DISP', desc: 'Wildlife Region 4',    cat: 'LDWF',                enc: 'clear'   },
]

describe('filterByArea', () => {
  it('keeps everything for area=all', () => {
    expect(filterByArea(tgs, 'all')).toHaveLength(2)
  })

  it('keeps Baton Rouge categories by substring match for area=br', () => {
    const out = filterByArea(tgs, 'br')
    expect(out.map(t => t.tgid)).toContain(17165)
  })
})

describe('filterTalkgroups', () => {
  it('filters by exact category', () => {
    expect(filterTalkgroups(tgs, { category: 'LDWF' })).toHaveLength(1)
  })

  it('searches tgid, alpha and desc case-insensitively', () => {
    expect(filterTalkgroups(tgs, { text: 'brpd' })).toHaveLength(1)
    expect(filterTalkgroups(tgs, { text: '6039' })).toHaveLength(1)
    expect(filterTalkgroups(tgs, { text: 'wildlife' })).toHaveLength(1)
  })
})
```

- [ ] **Step 2: Run, confirm failure**

```bash
npx vitest run server/utils/talkgroups.test.ts
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

export function loadTalkgroups(path: string): TalkgroupEntry[] {
  const raw = loadJSON<unknown>(path, {})
  const values = Array.isArray(raw) ? raw : Object.values(raw as Record<string, unknown>)

  return values.filter((v): v is TalkgroupEntry =>
    !!v && typeof v === 'object' && 'tgid' in (v as object),
  )
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
  opts: { category?: string; text?: string },
): TalkgroupEntry[] {
  let out = tgs

  if (opts.category) {
    out = out.filter(tg => tg.cat === opts.category)
  }

  if (opts.text) {
    const q = opts.text.toLowerCase()
    out = out.filter(tg =>
      String(tg.tgid).includes(q) ||
      (tg.alpha ?? '').toLowerCase().includes(q) ||
      (tg.desc ?? '').toLowerCase().includes(q),
    )
  }

  return out
}
```

- [ ] **Step 4: Run, confirm pass**

```bash
npx vitest run server/utils/talkgroups.test.ts
```

Expected: 5 passed.

- [ ] **Step 5: Verify against the real DB**

```bash
node --input-type=module -e "
import {loadTalkgroups, filterByArea} from './server/utils/talkgroups.ts'
" 2>/dev/null || npx vitest run
```

If the TS import is awkward from bare node, instead assert in a temporary test that `loadTalkgroups('/home/besquivel/rtl/reference/lwin_talkgroups.json').length > 4000` and that `filterByArea(..., 'br').length` is non-zero. Delete the temporary test after confirming.

- [ ] **Step 6: Commit**

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

  it('maps encryption scope to the right flag', () => {
    expect(buildListenArgs({ encryption: 'partial' })).toContain('--include-partial')
    expect(buildListenArgs({ encryption: 'encrypted' })).toContain('--include-encrypted')
    expect(buildListenArgs({ encryption: 'clear' })).not.toContain('--include-partial')
  })

  it('puts duration last as a positional argument', () => {
    const args = buildListenArgs({ preset: 'pd', stt: true, duration: 600 })
    expect(args[args.length - 1]).toBe('600')
    expect(args).toContain('--stt')
  })
})

describe('countCalls', () => {
  it('counts distinct saved recordings, not voice-update lines', () => {
    const log = [
      'voice update: tg(17165)',
      'voice update: tg(17165)',
      'saved recordings/TG17165_17-BRPD-DSP1_20260830-170008.wav',
      'saved recordings/TG5000_SP-A-DISP1_20260830-170051.wav',
    ].join('\n')
    expect(countCalls(log)).toBe(2)
  })

  it('returns 0 for an empty log', () => {
    expect(countCalls('')).toBe(0)
  })
})
```

- [ ] **Step 2: Run, confirm failure**

```bash
npx vitest run server/utils/processes.test.ts
```

- [ ] **Step 3: Confirm the real log line format before implementing**

`countCalls` must match what `udp_audio_record.py` actually prints when it flushes a call. Check:

```bash
grep -oE '.{0,40}TG[0-9]+_[^ ]+\.wav' /home/besquivel/rtl/web/listen.log | tail -5
```

Adjust the regex in Step 4 to match the observed line, and update the test fixture in Step 1 to the real format. Counting `.wav` filenames is deliberate — a single call produces many `voice update` lines, so counting those would badly overcount.

- [ ] **Step 4: Implement `server/utils/processes.ts`**

```typescript
import { spawn } from 'node:child_process'
import { readFileSync, statSync, openSync, readSync, closeSync } from 'node:fs'
import { join } from 'node:path'
import { scriptsDir, sdrRoot } from './paths'
import type { Encryption } from './files'

export interface ListenOptions {
  preset?: string
  talkgroups?: string
  encryption?: Encryption
  stt?: boolean
  duration?: number
}

export function buildListenArgs(opts: ListenOptions): string[] {
  const args: string[] = []

  if (opts.preset) args.push('--preset', opts.preset)
  if (opts.talkgroups) args.push('--tg', opts.talkgroups)
  if (opts.encryption === 'partial') args.push('--include-partial')
  if (opts.encryption === 'encrypted') args.push('--include-encrypted')
  if (opts.stt) args.push('--stt')
  if (opts.duration) args.push(String(opts.duration))

  return args
}

const SAVED_WAV = /TG\d+_[^\s/]+\.wav/g

/** Count distinct saved .wav files mentioned in the log. */
export function countCalls(logText: string): number {
  const matches = logText.match(SAVED_WAV)
  if (!matches) return 0
  return new Set(matches).size
}

/** Read the tail of a file without loading the whole thing. */
export function readTail(path: string, maxBytes = 256 * 1024): string {
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

export function startListening(opts: ListenOptions): { pid: number; config: ListenOptions } {
  const script = join(scriptsDir(), 'lwin_listen.sh')
  const child = spawn('bash', [script, ...buildListenArgs(opts)], {
    cwd: sdrRoot(),
    detached: true,      // own process group, so we can signal the whole tree
    stdio: 'ignore',
  })
  child.unref()

  if (!child.pid) throw new Error('failed to spawn lwin_listen.sh')
  return { pid: child.pid, config: opts }
}

export async function stopListening(pid: number): Promise<void> {
  if (!isProcessRunning(pid)) return

  try {
    process.kill(-pid, 'SIGINT')     // negative pid = process group
  } catch {
    process.kill(pid, 'SIGINT')
  }

  for (let waited = 0; waited < 8000 && isProcessRunning(pid); waited += 200) {
    await new Promise(r => setTimeout(r, 200))
  }

  if (isProcessRunning(pid)) {
    try { process.kill(-pid, 'SIGKILL') } catch { /* already gone */ }
  }
}
```

- [ ] **Step 5: Implement `server/utils/session.ts`**

```typescript
import { isProcessRunning } from './processes'
import type { ListenOptions } from './processes'

export interface Session {
  pid: number
  config: ListenOptions
  startTime: number
}

let current: Session | null = null

export const sessionStore = {
  set(s: Session): void {
    current = s
  },

  /** Returns the live session, clearing it if the process has died. */
  get(): Session | null {
    if (current && !isProcessRunning(current.pid)) current = null
    return current
  },

  clear(): void {
    current = null
  },

  isRunning(): boolean {
    return this.get() !== null
  },
}
```

- [ ] **Step 6: Run, confirm pass**

```bash
npx vitest run server/utils/processes.test.ts
```

Expected: 6 passed.

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

const PRESETS = new Set(['pd', 'pd-all', 'fire', 'fire-all', 'ems', 'interop', 'schools', 'publicworks', 'all'])
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
  if (body.duration !== undefined && (!Number.isInteger(body.duration) || body.duration < 1)) {
    setResponseStatus(event, 400)
    return { success: false, error: 'Duration must be a positive integer' }
  }
  if (!body.preset && !body.talkgroups) {
    setResponseStatus(event, 400)
    return { success: false, error: 'Pick a preset or enter talkgroup IDs' }
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
npm run dev &
sleep 5
curl -s localhost:3000/api/listen/status
curl -s -X POST localhost:3000/api/listen/start \
  -H 'Content-Type: application/json' -d '{"preset":"pd","encryption":"partial","duration":45}'
sleep 5
curl -s localhost:3000/api/listen/status
curl -s -X POST localhost:3000/api/listen/stop
sleep 2
curl -s localhost:3000/api/listen/status
```

Expected: status starts `running:false`; start returns a pid; status shows `running:true`; stop succeeds; status returns to `running:false`.

Also confirm no orphan processes remain:

```bash
pgrep -af 'lwin_listen|rx.py|udp_audio_record' || echo "clean"
```

Expected: `clean`.

- [ ] **Step 5: Commit**

```bash
git add server/api/listen/
git commit -m "feat: add listen start/stop/status API routes"
```

---

## Task 8: Recordings API Routes

**Files:**
- Create: `server/api/recordings/list.get.ts`
- Create: `server/api/recordings/search.get.ts`
- Create: `server/api/recordings/[name].get.ts`

**Interfaces:**
- Consumes: `scanRecordings`, `mergeCalls`, `loadJSON` (Tasks 3–4); `safeRecordingPath`, `recordingsDir`, `referenceDir` (Task 2).
- Produces: `GET /api/recordings/list`, `GET /api/recordings/search`, `GET /api/recordings/:name`.

`[name].get.ts` serves **both** `.wav` (streamed, Range-aware) and `.txt` (plain text), dispatching on the extension. Nuxt cannot route `[name].txt.get.ts` as a distinct file — the dot is not a route separator — so one handler covers both.

- [ ] **Step 1: Create a shared loader `server/utils/recordings.ts`**

```typescript
import { join } from 'node:path'
import { loadJSON, scanRecordings, mergeCalls } from './files'
import type { Recording, TalkgroupEntry } from './files'
import { recordingsDir, referenceDir } from './paths'

export function allRecordings(): Recording[] {
  const tgdb = loadJSON<Record<string, TalkgroupEntry>>(
    join(referenceDir(), 'lwin_talkgroups.json'), {},
  )
  const calls = loadJSON<unknown>(join(recordingsDir(), 'calls.json'), [])
  return mergeCalls(scanRecordings(recordingsDir(), tgdb), calls)
}
```

- [ ] **Step 2: Create `server/api/recordings/list.get.ts`**

```typescript
import { allRecordings } from '~/server/utils/recordings'

export default defineEventHandler(() => {
  return { success: true, data: allRecordings() }
})
```

- [ ] **Step 3: Create `server/api/recordings/search.get.ts`**

```typescript
import { allRecordings } from '~/server/utils/recordings'

export default defineEventHandler((event) => {
  const q = getQuery(event)
  const tgid = q.tgid ? Number.parseInt(String(q.tgid), 10) : null
  const text = q.text ? String(q.text).toLowerCase() : ''
  const enc = q.enc ? String(q.enc) : ''

  let out = allRecordings()

  if (tgid !== null && Number.isFinite(tgid)) out = out.filter(r => r.tgid === tgid)
  if (enc && enc !== 'all') out = out.filter(r => r.enc === enc)
  if (text) {
    out = out.filter(r =>
      String(r.tgid ?? '').includes(text) ||
      (r.alpha ?? '').toLowerCase().includes(text) ||
      (r.desc ?? '').toLowerCase().includes(text),
    )
  }

  return { success: true, data: out }
})
```

- [ ] **Step 4: Create `server/api/recordings/[name].get.ts`**

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
    return sendStream(event, createReadStream(path))
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
  return sendStream(event, createReadStream(path, { start, end }))
})
```

- [ ] **Step 5: Exercise the routes**

```bash
npm run dev &
sleep 5

curl -s localhost:3000/api/recordings/list | head -c 400
curl -s 'localhost:3000/api/recordings/search?enc=clear' | head -c 200

WAV=$(ls /home/besquivel/rtl/recordings/*.wav 2>/dev/null | head -1 | xargs -r basename)
[ -n "$WAV" ] && curl -s -D- -o /dev/null "localhost:3000/api/recordings/$WAV"
[ -n "$WAV" ] && curl -s -D- -o /dev/null -H 'Range: bytes=0-1023' "localhost:3000/api/recordings/$WAV"

# path traversal must 404
curl -s -o /dev/null -w '%{http_code}\n' 'localhost:3000/api/recordings/..%2F..%2Fetc%2Fpasswd'
```

Expected: list/search return JSON; plain GET returns `200` with `Content-Length`; Range GET returns `206` with `Content-Range`; traversal returns `404`.

- [ ] **Step 6: Commit**

```bash
git add server/utils/recordings.ts server/api/recordings/
git commit -m "feat: add recordings list, search and range-aware streaming"
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

  const all = loadTalkgroups(join(referenceDir(), 'lwin_talkgroups.json'))
  const data = filterTalkgroups(filterByArea(all, area), { category, text })

  return { success: true, data }
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
curl -s 'localhost:3000/api/talkgroups/list?area=br' | head -c 300
curl -s 'localhost:3000/api/talkgroups/list?area=all&text=brpd' | head -c 300
curl -s localhost:3000/api/talkgroups/whitelist | head -c 300
curl -s localhost:3000/api/config/presets
```

Expected: `area=br` returns fewer entries than `area=all`; whitelist returns a non-empty `tgids` array; presets returns nine entries.

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
      <Tag value="Recording" severity="success" />
      <div class="text-2xl font-bold mt-2">{{ callCount }} calls</div>
      <div class="text-sm text-color-secondary">since {{ startedAt }}</div>
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
        <span class="block mb-1 text-sm">Encryption scope</span>
        <div class="flex flex-column gap-2">
          <div v-for="opt in encOptions" :key="opt.value" class="flex align-items-center gap-2">
            <RadioButton
              :input-id="opt.value" v-model="encryption"
              :value="opt.value" :disabled="running"
            />
            <label :for="opt.value" class="text-sm">{{ opt.label }}</label>
          </div>
        </div>
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

interface StatusPayload {
  running: boolean
  pid: number | null
  callCount: number
  startTime: number | null
}

interface ApiResponse<T> { success: boolean, data?: T, error?: string }

const preset = ref<string | null>(null)
const talkgroups = ref('')
const encryption = ref<'clear' | 'partial' | 'encrypted'>('clear')
const stt = ref(false)
const duration = ref<number | null>(null)

const running = ref(false)
const callCount = ref(0)
const startTime = ref<number | null>(null)
const busy = ref(false)
const error = ref('')

const presets = ref<PresetOption[]>([])

const encOptions: PresetOption[] = [
  { value: 'clear',     label: 'Clear only (default)' },
  { value: 'partial',   label: 'Include partially encrypted (BRPD / EBR SO)' },
  { value: 'encrypted', label: 'Include fully encrypted (records silence)' },
]

const startedAt = computed(() =>
  startTime.value ? new Date(startTime.value * 1000).toLocaleTimeString() : '',
)

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
    callCount.value = res.data.callCount
    startTime.value = res.data.startTime
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
        encryption: encryption.value,
        stt: stt.value,
        duration: duration.value ?? undefined,
      },
    })
    if (!res.success) error.value = res.error ?? 'Failed to start'
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Failed to start'
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
    error.value = e instanceof Error ? e.message : 'Failed to stop'
  } finally {
    busy.value = false
    await refresh()
  }
}
</script>
```

> `$fetch` throws on non-2xx by default, so the 400/409 branches surface through the `catch`. The `res.success` check covers handlers that return a 200 with `success: false`.

- [ ] **Step 2: Verify in the browser**

```bash
npm run dev
```

Open `http://localhost:3000`. Confirm the preset list populates, Start is enabled, and starting a 45-second `pd` session flips the panel to the running state with a live call count.

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
          <p v-else-if="transcript" class="m-0 text-sm line-height-3">{{ transcript }}</p>
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
  enc: 'clear' | 'partial' | 'encrypted' | null
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

const encOptions = [
  { value: 'all',       label: 'All' },
  { value: 'clear',     label: 'Clear' },
  { value: 'partial',   label: 'Partial' },
  { value: 'encrypted', label: 'Encrypted' },
]

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return recordings.value.filter((r) => {
    if (encFilter.value !== 'all' && r.enc !== encFilter.value) return false
    if (!q) return true
    return String(r.tgid ?? '').includes(q)
      || (r.alpha ?? '').toLowerCase().includes(q)
      || (r.desc ?? '').toLowerCase().includes(q)
  })
})

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

function encSeverity(enc: string | null): string {
  if (enc === 'clear') return 'success'
  if (enc === 'partial') return 'warn'
  if (enc === 'encrypted') return 'danger'
  return 'secondary'
}
</script>
```

- [ ] **Step 2: Verify in the browser**

Confirm the table lists existing `recordings/*.wav`, search narrows it, the encryption filter works, and clicking play opens a dialog whose audio element plays **and seeks** (seeking is what exercises the Range path).

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
      <InputText v-model="search" placeholder="Search TG or alpha" class="flex-1" />
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
      <Column header="Enc" style="width: 7rem">
        <template #body="{ data }">
          <Tag :value="data.enc" :severity="encSeverity(data.enc)" />
        </template>
      </Column>
      <Column header="Whitelist" style="width: 7rem">
        <template #body="{ data }">
          <Tag v-if="whitelist.has(data.tgid)" value="active" severity="info" />
        </template>
      </Column>
    </DataTable>

    <p class="text-sm text-color-secondary mt-3 mb-0">
      Rows marked <Tag value="active" severity="info" /> are in the current
      <code>lwin_active_whitelist.txt</code>.
    </p>
  </section>
</template>

<script setup lang="ts">
interface Talkgroup {
  tgid: number
  alpha: string
  desc: string
  cat: string
  enc: 'clear' | 'partial' | 'encrypted'
}

interface Option { value: string, label: string }
interface ApiResponse<T> { success: boolean, data?: T, error?: string }

const area = ref<'br' | 'all'>('br')
const category = ref<string | null>(null)
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

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return talkgroups.value.filter((t) => {
    if (category.value && t.cat !== category.value) return false
    if (!q) return true
    return String(t.tgid).includes(q) || (t.alpha ?? '').toLowerCase().includes(q)
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
  if (enc === 'partial') return 'warn'
  if (enc === 'encrypted') return 'danger'
  return 'secondary'
}
</script>
```

- [ ] **Step 2: Verify in the browser**

Confirm: `area=br` shows a Baton Rouge subset and `Statewide` shows the full ~4163; the category dropdown repopulates when area changes; whitelisted rows are marked and shaded.

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
npm run dev
```

Walk every acceptance check:
- Start a 60-second `pd --include-partial` session; call count climbs; Stop returns the panel to idle.
- `pgrep -af 'lwin_listen|rx.py'` prints nothing after Stop.
- A recording plays and **seeks**.
- Search and the encryption filter both narrow the recordings table.
- Talkgroups: area toggle, category filter, whitelist highlighting.
- Load `http://10.56.1.77:3000` from another machine on the LAN.

- [ ] **Step 4: Production build**

```bash
npm run build
node .output/server/index.mjs &
sleep 5
curl -s localhost:3000/api/listen/status
```

Expected: build succeeds; the built server answers on 3000. Repeat two or three checks from Step 3 against it (recordings list, an audio Range request).

- [ ] **Step 5: Commit**

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

- [ ] **Step 1: Confirm what still writes into `web/`**

```bash
grep -rn "web/listen\|web/server\|web/index.html\|serve.sh" scripts/ README.md
```

`scripts/lwin_listen.sh` may write `web/listen.log`. If so, keep that path (the status route reads it) and note it in the README; if not, update `listenLogPath()` in `server/utils/paths.ts` to wherever the log actually lands.

- [ ] **Step 2: Rewrite README § Web console**

```markdown
## Web console

A Nuxt 3 + PrimeVue 4 app (`pages/`, `components/`, `server/api/`) replaces the
old Python stdlib server.

```bash
npm install
npm run dev          # http://0.0.0.0:3000, hot reload
# or
npm run build && node .output/server/index.mjs
```

Open **http://10.56.1.77:3000/** (or **http://127.0.0.1:3000/**) — three panels:

- **Listen & Record** — preset or explicit talkgroup IDs, encryption scope
  (`--include-partial` / `--include-encrypted`), Whisper STT, duration. Start
  spawns `scripts/lwin_listen.sh` in its own process group; Stop sends SIGINT to
  the group. Status polls every 5 s and shows the live call count parsed from
  `web/listen.log`.
- **Recordings** — every `recordings/TG*.wav` with metadata from
  `recordings/calls.json` and the reference DB, its Whisper transcript, search
  and an encryption filter. Audio is served with HTTP Range support so seeking
  works.
- **Talkgroups** — the reference DB, Baton Rouge area or statewide, with
  encryption flags; rows in `lwin_active_whitelist.txt` are marked.

Session state lives in memory, so a server restart forgets a running session
(the recorder itself keeps running — stop it with Ctrl-C in its own terminal).
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

```bash
git rm web/server.py web/index.html web/serve.sh
```

- [ ] **Step 5: Full verification after removal**

```bash
npm run build
node .output/server/index.mjs &
sleep 5
curl -s localhost:3000/api/listen/status
curl -s localhost:3000/api/recordings/list | head -c 200
npx vitest run
```

Expected: build clean, both routes answer, all unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add README.md .gitignore
git commit -m "docs: document Nuxt web console and remove Python server"
```

---

## Acceptance Criteria

- [ ] `npm run build` succeeds with no TypeScript errors
- [ ] `npx vitest run` passes
- [ ] Start → call count climbs → Stop leaves no `lwin_listen` / `rx.py` / `udp_audio_record` processes
- [ ] A recording plays and seeks (Range requests return 206)
- [ ] Path traversal on `/api/recordings/:name` returns 404
- [ ] Recordings search and encryption filter both work
- [ ] Talkgroups: area toggle, category filter, whitelist highlighting all work
- [ ] Reachable from another machine at `http://10.56.1.77:3000`
- [ ] `web/server.py` is gone and the README describes the Nuxt app

## Known Gaps (deliberate, per spec)

- Session state is in-memory; a server restart orphans a running recorder.
- Status is polled every 5 s, not pushed. SSE is a later upgrade.
- No auth — the app is LAN-only by design.
