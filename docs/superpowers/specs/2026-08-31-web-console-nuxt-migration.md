# SDR Web Console — Nuxt 3 + PrimeVue 4 Migration

**Date:** 2026-08-31  
**Author:** bretesq  
**Status:** Design (Ready for Implementation)

## Executive Summary

Migrate the current Python stdlib web console (`web/server.py`) to a modern **Nuxt 3 full-stack application** with **PrimeVue 4 components**, **TypeScript**, and **Node.js backend logic**. The app manages SDR recording sessions, playback, and talkgroup browsing for the Baton Rouge LWIN P25 system.

**Why this migration:**
1. **Better UI/UX** — PrimeVue 4 components over hand-rolled HTML
2. **Modern stack** — TypeScript, reactive data binding, developer experience
3. **Real-time interactivity** — SSE/polling for live status updates
4. **Maintainability** — Unified JavaScript/TypeScript codebase (no Python/HTML split)
5. **Extensibility** — Easier to add features (filtering, sorting, bulk actions)

**Scope:** Migrate all three panels (Listen/Record, Recordings, Talkgroups) + backend orchestration logic. Keep the same file-based architecture (JSON metadata, filesystem recordings).

---

## Architecture

### Technology Stack

| Layer | Tech |
|-------|------|
| **Frontend** | Vue 3 (`<script setup>`), PrimeVue 4, PrimeFlex, TypeScript |
| **Backend** | Nuxt 3 Nitro server routes (`/server/api`), Node.js `child_process` |
| **Process Mgmt** | Spawn `lwin_listen.sh`, tail logs, manage process groups |
| **Data** | File system (JSON, audio files), in-memory state |
| **Real-time** | Server-Sent Events (SSE) or polling (5s) for status updates |
| **Deployment** | Same machine, LAN-accessible (`10.56.1.77:3000` or similar) |

### High-Level Flow

```
User Browser (PrimeVue UI)
        ↓
    Nuxt 3 Pages + Components (Vue 3)
        ↓
    Nitro Server Routes (/server/api/*)
        ↓
    Node.js Logic (TypeScript)
        ├── Process Management (child_process)
        ├── File I/O (JSON, logs, audio)
        └── CLI Orchestration (lwin_listen.sh, etc.)
```

### Directory Structure

```
sdr/
├── nuxt.config.ts
├── tsconfig.json
├── package.json
├── components/
│   ├── ListenControl.vue
│   ├── RecordingsList.vue
│   └── TalkgroupBrowser.vue
├── pages/
│   └── index.vue
├── server/
│   ├── api/
│   │   ├── listen/
│   │   │   ├── start.post.ts
│   │   │   ├── stop.post.ts
│   │   │   └── status.get.ts
│   │   ├── recordings/
│   │   │   ├── list.get.ts
│   │   │   ├── search.get.ts
│   │   │   ├── [name].get.ts
│   │   │   └── [name].txt.get.ts
│   │   ├── talkgroups/
│   │   │   ├── list.get.ts
│   │   │   └── whitelist.get.ts
│   │   ├── events/
│   │   │   └── status.get.ts (SSE)
│   │   └── config/
│   │       └── presets.get.ts
│   └── utils/
│       ├── processes.ts
│       ├── files.ts
│       └── talkgroups.ts
├── public/
│   └── (audio files served from recordings/ at runtime)
└── docs/
    └── superpowers/specs/
        └── this file
```

---

## Frontend Design

### Pages

#### `pages/index.vue` — Main Dashboard
A three-column layout showing all functionality:
1. **Left column (30%):** ListenControl component
2. **Center column (35%):** RecordingsList component
3. **Right column (35%):** TalkgroupBrowser component

Uses **PrimeFlex** grid system for responsive layout. On mobile, stacks vertically.

---

### Components

#### `components/ListenControl.vue`

**Purpose:** Start/stop listening sessions, configure options.

**State:**
- `listening: boolean` — is a session running?
- `sessionPid: number | null`
- `callCount: number` — live call count from `listen.log`
- `selectedPreset: string` — 'pd', 'fire', 'ems', etc.
- `customTalkgroups: string` — comma-separated list
- `encryption: 'clear' | 'partial' | 'encrypted'`
- `sttEnabled: boolean` — enable Whisper transcription
- `duration: number` — session duration in seconds

**UI Elements (PrimeVue):**
- **Dropdown** for preset selection
- **InputText** for manual talkgroup IDs
- **RadioButton** group for encryption scope
- **Checkbox** for STT toggle
- **InputNumber** for duration
- **Button** (Start/Stop, color changes based on state)
- **ProgressSpinner** while starting
- **Tag** showing call count, updated every 5s
- **Message** (toast) for errors

**Behavior:**
- Start button disabled if already running
- Stop button disabled if not running
- Poll `GET /api/listen/status` every 5s; update `callCount` and `listening`
- On Start: POST to `/api/listen/start` with options
- On Stop: POST to `/api/listen/stop`
- Show success/error toasts on completion

---

#### `components/RecordingsList.vue`

**Purpose:** Display all recordings with playback, transcripts, search/filter.

**State:**
- `recordings: Recording[]` — all recording metadata
- `filteredRecordings: Recording[]` — search/filtered results
- `searchText: string`
- `encryptionFilter: 'all' | 'clear' | 'partial' | 'encrypted'`
- `selectedTg: number | null` — filter by talkgroup

**UI Elements (PrimeVue):**
- **InputText** for search (tgid, alpha, description)
- **Dropdown** for encryption filter
- **DataTable** with columns: Talkgroup, Alpha, Start Time, Duration, Encryption, Transcript
- **Button** in each row for audio playback (icon: play)
- **Dialog** overlay for full playback + transcript display
  - Audio player (`<audio>`) with Range-enabled seeking
  - Transcript displayed below
  - Download button for .wav file (optional)

**Behavior:**
- Load recordings on mount: `GET /api/recordings/list`
- Debounced search: filters as user types
- Encryption filter applied immediately
- Click play → open dialog, stream from `/api/recordings/[name]`
- Transcript loaded from `/api/recordings/[name].txt`
- Sort by start time (newest first)

---

#### `components/TalkgroupBrowser.vue`

**Purpose:** Browse LWIN reference database, see encryption flags, highlight active whitelist.

**State:**
- `talkgroups: Talkgroup[]`
- `filteredTalkgroups: Talkgroup[]`
- `area: 'br' | 'all'` — Baton Rouge or statewide
- `selectedCategory: string` — filter by category
- `searchText: string`
- `activeWhitelist: Set<number>` — tgids in active whitelist

**UI Elements (PrimeVue):**
- **Dropdown** for area selection (BR area / Statewide)
- **Dropdown** for category filter
- **InputText** for search (tgid, alpha)
- **DataTable** with columns: TG ID, Alpha, Description, Category, Encryption, Status
  - **Status** badge highlights if tgid is in active whitelist
  - Encrypted talkgroups shown with red icon
  - Partial encryption with orange icon
  - Clear with green icon

**Behavior:**
- Load talkgroups on mount: `GET /api/talkgroups/list?area=br`
- Load active whitelist: `GET /api/talkgroups/whitelist`
- Filter by area/category/text as user selects
- Visually highlight rows in whitelist (badge or background color)

---

## Backend Design

### Core Concepts

**Process State:**
- Store in memory (single instance per server start)
- Track: PID, start time, config, call count
- On server restart, state is lost (acceptable for local dev/ops)

**File Paths:**
```
/home/besquivel/rtl/
├── recordings/
│   ├── TG*.wav
│   ├── TG*.txt
│   └── calls.json
├── reference/
│   ├── lwin_talkgroups.json
│   ├── lwin_sites.json
│   └── lwin_categories.json
├── scripts/
│   ├── lwin_listen.sh
│   ├── lwin_decode.sh
│   └── [other scripts]
└── lwin_active_whitelist.txt
```

### Server Routes

#### Listen Management

**POST `/api/listen/start`**

Request body:
```typescript
{
  preset?: 'pd' | 'fire' | 'ems' | 'interop' | 'schools' | 'publicworks' | 'all';
  talkgroups?: string;  // comma-separated tgids
  encryption?: 'clear' | 'partial' | 'encrypted';
  stt?: boolean;
  duration?: number;
}
```

Response (200):
```typescript
{
  success: true;
  pid: number;
  config: {
    preset?: string;
    talkgroups?: string;
    encryption: string;
    stt: boolean;
    duration?: number;
  };
  startTime: number;  // unix timestamp
}
```

Response (400/500):
```typescript
{
  success: false;
  error: string;
}
```

**Implementation:**
1. Check if already running (return error if so)
2. Build CLI args for `lwin_listen.sh` from request body
3. Spawn child process: `lwin_listen.sh [args]`
4. Capture stdout/stderr, tail for live log
5. Extract call count from `listen.log`
6. Store in memory: `{ pid, config, startTime, lastUpdate, callCount }`
7. Return PID + config

**Preset to CLI mapping:**
- `--pd` for police dispatch
- `--fire` for fire dispatch
- etc. (see README § 6, Talkgroup selection flags)

---

**GET `/api/listen/status`**

Response (200):
```typescript
{
  running: boolean;
  pid: number | null;
  config: object | null;
  callCount: number;
  startTime: number | null;
  lastUpdate: number;  // unix timestamp
}
```

**Implementation:**
1. Check if stored PID is still alive
2. If alive, tail `listen.log` to extract call count
3. Return all state

---

**POST `/api/listen/stop`**

Response (200):
```typescript
{
  success: true;
  message: string;
}
```

Response (400):
```typescript
{
  success: false;
  error: string;
}
```

**Implementation:**
1. Get stored PID
2. If running, send SIGINT to process group (use `-pid` on Unix)
3. Wait up to 5s for process to exit
4. Clean up state
5. Return success

---

#### Recordings

**GET `/api/recordings/list`**

Response (200):
```typescript
Recording[] where Recording = {
  file: string;
  tgid: number | null;
  alpha: string | null;
  desc: string | null;
  cat: string | null;
  enc: 'clear' | 'partial' | 'encrypted' | null;
  start: number;  // unix timestamp
  dur: number;    // seconds
  transcript: string | null;
}
```

**Implementation:**
1. Load `recordings/calls.json` if it exists
2. Scan `recordings/*.wav` directory
3. For each .wav, match against calls.json (by filename)
4. If in calls.json, merge metadata; else stub entry from filename
5. Sort by start time (newest first)
6. Return array

---

**GET `/api/recordings/search`**

Query params:
- `tgid?: number` — filter by talkgroup ID
- `text?: string` — search in alpha/desc
- `enc?: 'clear' | 'partial' | 'encrypted'` — encryption filter

Response: `Recording[]` (filtered)

**Implementation:**
1. Load full recording list (cached or fresh)
2. Filter by tgid if provided
3. Filter by encryption if provided
4. Search by text (case-insensitive) in alpha/desc if provided
5. Return results

---

**GET `/api/recordings/[name]`**

Stream the audio file with HTTP Range support.

**Headers:**
- `Content-Type: audio/wav`
- `Content-Length: <size>`
- `Accept-Ranges: bytes` (if ranges supported)
- `Content-Range: bytes <start>-<end>/<total>` (if 206 response)

Response:
- **200:** Full file
- **206:** Partial content (Range requested)
- **404:** File not found

**Implementation:**
1. Validate filename (`[name]` must match `TG\d+_.*\.wav`)
2. Resolve to `/home/besquivel/rtl/recordings/[name].wav`
3. Check file exists
4. Parse `Range` header if present
5. Stream file with proper headers
6. Support seeking for audio player

---

**GET `/api/recordings/[name].txt`**

Stream the transcript file.

**Headers:**
- `Content-Type: text/plain; charset=utf-8`

Response:
- **200:** Transcript content
- **404:** Transcript not found (or no transcript for this recording)

**Implementation:**
1. Validate filename (same as above)
2. Resolve to `/home/besquivel/rtl/recordings/[name].txt`
3. Return content if exists, else 404
4. If file is missing but recording exists, return empty string (don't 404)

---

#### Talkgroups

**GET `/api/talkgroups/list`**

Query params:
- `area?: 'br' | 'all'` (default: 'br')
- `category?: string` (optional filter)

Response (200):
```typescript
Talkgroup[] where Talkgroup = {
  tgid: number;
  alpha: string;
  desc: string;
  cat: string;
  enc: 'clear' | 'partial' | 'encrypted';
}
```

**Implementation:**
1. Load `reference/lwin_talkgroups.json` on startup (cache in memory)
2. Filter by area: if 'br', match against BR_AREA categories; else all
3. Filter by category if provided
4. Return results

---

**GET `/api/talkgroups/whitelist`**

Response (200): `Talkgroup[]` (subset of full DB, only active whitelist entries)

**Implementation:**
1. Load full talkgroup DB
2. Parse `lwin_active_whitelist.txt` (one tgid per line)
3. Return only talkgroups in whitelist
4. Mark with `inWhitelist: true` in response

---

#### Config

**GET `/api/config/presets`**

Response (200):
```typescript
{
  presets: {
    pd: string;            // "--pd"
    fire: string;          // "--fire"
    ems: string;           // "--ems"
    [name]: string;        // cli arg
  };
  areas: ['br', 'all'];
  categories: string[];
}
```

**Implementation:**
1. Return hardcoded preset->arg mappings
2. Load category list from `reference/lwin_categories.json`
3. Return both

---

### Server Utils

**`server/utils/processes.ts`**
- `startListening(options)` — spawn process, return PID
- `stopListening(pid)` — send SIGINT
- `isRunning(pid)` — check if process alive
- `getTailLog(path, lines)` — read last N lines of log
- `extractCallCount(logText)` — parse call count from op25 log

**`server/utils/files.ts`**
- `loadJSON(path, default)` — safe JSON load with fallback
- `scanRecordings(dir)` — list all .wav with metadata
- `getRecordingMetadata(filename)` — parse filename to extract tgid/timestamp
- `streamAudioFile(path, range)` — handle Range requests, return stream

**`server/utils/talkgroups.ts`**
- `loadTalkgroupDB()` — load and cache `lwin_talkgroups.json`
- `loadWhitelist(path)` — parse `lwin_active_whitelist.txt` to Set<number>
- `filterByArea(tgs, area)` — filter to BR area or all

---

## Real-Time Updates

### MVP: Polling (5s)

Frontend `ListenControl` component:
```typescript
// In component
const statusInterval = setInterval(async () => {
  const status = await $fetch('/api/listen/status');
  callCount.value = status.callCount;
  listening.value = status.running;
}, 5000);
```

Simple, no WebSocket complexity, good enough for local LAN.

### Future: Server-Sent Events (Optional)

If we later need sub-second updates:
- Add `GET /api/events/status` SSE endpoint
- Frontend subscribes: `new EventSource('/api/events/status')`
- Server pushes updates as `listen.log` changes
- Fallback to polling if SSE fails

For now, polling is sufficient.

---

## Data Persistence & State

### What's Persistent (File System)
- `recordings/` — .wav files and .txt transcripts
- `recordings/calls.json` — metadata
- `reference/` — talkgroup DB (read-only)
- `lwin_active_whitelist.txt` — current whitelist

### What's In-Memory (Server)
- Current listen session state (PID, config, call count, start time)
- Cached talkgroup DB (loaded once on startup)
- Process handle for SIGINT

### Loss of State
- Server restart → current session state lost (acceptable)
- Recordings/metadata persist (file-based)
- Whitelist remains active for next session

---

## Error Handling

**API Responses:**
- All endpoints return `{ success: boolean, data?, error?: string }`
- HTTP status codes: 200 (ok), 400 (bad request), 404 (not found), 500 (server error)

**Frontend:**
- Catch API errors, show toast notifications
- Disable buttons/inputs on error (don't retry silently)
- Log errors to console for debugging

**Process Management:**
- If `lwin_listen.sh` exits unexpectedly → frontend detects via status poll
- If PID becomes invalid → clean up state, show "session ended" message
- Zombie process cleanup: check `/proc/[pid]/stat` state

---

## Testing Strategy

**Unit Tests (Vitest):**
- `server/utils/` functions (JSON loading, file parsing, filtering)
- `components/` — prop validation, event emission

**Integration Tests:**
- API routes with mock child_process
- File I/O with temporary directories

**Manual Testing:**
- Start/stop listening session
- Play recording + seek with audio player
- Filter recordings by tgid/encryption
- Browse talkgroups, verify whitelist highlighting

**Deployment Test:**
- Start Nuxt dev server on target machine
- Access from browser on same LAN
- Verify all three panels functional

---

## Migration Path

1. **Phase 1:** Set up Nuxt 3 project, install dependencies
2. **Phase 2:** Build component skeleton + basic styling
3. **Phase 3:** Implement backend server routes (listen, recordings, talkgroups)
4. **Phase 4:** Integrate frontend ↔ backend
5. **Phase 5:** Testing + refinement
6. **Phase 6:** Deprecate old Python server, cutover

---

## Dependencies

**npm:**
- `nuxt@^3.x`
- `primevue@^4.x`
- `primeicons`
- `primeflex`
- `h3` (for Nitro, included with Nuxt)

**System:**
- Node.js 18+
- Same SDR setup as before (no changes to hardware/scripts)

---

## Success Criteria

- ✅ All three panels (Listen, Recordings, Talkgroups) fully functional in PrimeVue
- ✅ Start/stop listening from browser
- ✅ Audio playback with seeking works
- ✅ Live call count updates every 5s
- ✅ Search/filter on recordings work smoothly
- ✅ Talkgroup whitelist highlighting works
- ✅ No TypeScript errors
- ✅ Accessible on LAN from 10.56.1.77 (or configured host)
- ✅ Old Python server can be safely deleted

---

## Known Limitations & Future Work

- **State loss on restart:** Session state not persisted. Could add SQLite later if needed.
- **No multi-user:** Assumes single user. Could add auth if needed.
- **Polling vs SSE:** Using polling for MVP. Could upgrade to SSE for real-time updates.
- **File size limits:** No explicit handling for very large audio files. Should be fine for 2-3 hour sessions.

---

## Open Questions

None at this stage. Design is solid and ready for implementation.
