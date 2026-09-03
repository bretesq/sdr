import { describe, it, expect, vi, afterEach } from 'vitest'

/**
 * node:child_process and node:fs's existsSync are mocked here, in addition to
 * — not instead of — exercising captureCapabilityGap()/startListening()'s
 * guard below. See transcriber.test.ts's identical comment for the full
 * reasoning; the short version: this file's code path can signal or spawn a
 * REAL process on this exact host (which has real HackRF hardware, a real
 * hackrf_info on PATH, and a real op25 build — this is the box the live
 * capture runs on). If captureCapabilityGap() ever regressed to report
 * "capable" when it should not, an unmocked startListening() would spawn a
 * genuine `bash scripts/lwin_listen_multi.sh ...` fighting the live host
 * capture for the same two HackRFs. Mocking spawn/execFileSync closes that
 * hole independently of guard health, and mocking existsSync means these
 * tests control which branch runs instead of depending on what happens to be
 * true of this machine's filesystem right now.
 *
 * global.fetch is stubbed for the same reason on the delegation side: without
 * it, a guard regression that reaches delegateStart() would issue a real HTTP
 * POST to http://capture:8082/start — reachable from this host outside any
 * container — and could start a real capture in the capture container.
 */
import {
  buildListenArgs, countCalls, scriptFor, LAUNCHERS, inContainer, startListening,
  captureCapabilityGap, isCaptureCapable, delegatedSessionLiveness, stopDelegatedCapture,
  isRadioBusy, stopListening,
} from './processes'

const mockSpawn = vi.fn()
const mockExecFileSync = vi.fn()
const mockSpawnSync = vi.fn()
const mockExistsSync = vi.fn()
const mockFetch = vi.fn()
const mockOpenSync = vi.fn()
const mockCloseSync = vi.fn()

// Vitest hoists these calls above the imports above at runtime (regardless of
// lexical position), so both modules are already mocks by the time
// processes.ts's own top-level imports resolve. The `mock`-prefixed names are
// required by that same hoisting transform — see transcriber.test.ts's
// comment on the identical child_process mock for why.
vi.mock('node:child_process', () => ({
  spawn: (...args: unknown[]) => mockSpawn(...args),
  execFileSync: (...args: unknown[]) => mockExecFileSync(...args),
  // spawnSync backs pkillPattern()'s last-resort release attempt (see its own
  // comment in processes.ts for why it is spawnSync and not execFileSync).
  // Mocked for the exact same reason as execFileSync above: unmocked, a
  // stopListening() test would run a REAL pkill against whatever this host's
  // pgrep patterns happen to match right now.
  spawnSync: (...args: unknown[]) => mockSpawnSync(...args),
}))
vi.mock('node:fs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs')>()
  // readFileSync/statSync/readSync (used by readTail()/processStartTime()/
  // isProcessRunning(), none of which this file's tests touch) stay real.
  // existsSync is mocked because captureCapabilityGap() calls it — these
  // tests need to control which branch runs rather than depend on what
  // happens to be true of this machine's filesystem right now. openSync/
  // closeSync are mocked too: the "capable, spawns locally" test below
  // exercises startListening()'s local-spawn branch, which opens
  // web/listen.log for real — on THIS host that is a real file next to the
  // live capture's own working tree, and a test has no business truncating
  // it just to prove a spawn call happened.
  return {
    ...actual,
    existsSync: (...args: unknown[]) => mockExistsSync(...args),
    openSync: (...args: unknown[]) => mockOpenSync(...args),
    closeSync: (...args: unknown[]) => mockCloseSync(...args),
  }
})

vi.stubGlobal('fetch', mockFetch)

afterEach(() => {
  vi.clearAllMocks()
})

/**
 * A fake fetch Response for the control API, real enough for
 * readControlResponse() (processes.ts): it reads `.text()` first and
 * JSON.parses it, rather than calling `.json()` directly, so a non-JSON body
 * can be exercised too (see the "falls back to a truncated raw body" test).
 */
function fakeControlResponse(status: number, ok: boolean, body: unknown): { ok: boolean, status: number, text: () => Promise<string> } {
  return { ok, status, text: async () => JSON.stringify(body) }
}

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

describe('multi-receiver mode', () => {
  it('defaults to the single-receiver launcher when no mode is given', () => {
    // An existing client sends no `mode`; it must behave exactly as before.
    expect(scriptFor(undefined)).toBe('lwin_listen.sh')
    expect(scriptFor('single')).toBe('lwin_listen.sh')
  })

  it('selects the multi launcher for mode multi', () => {
    expect(scriptFor('multi')).toBe('lwin_listen_multi.sh')
  })

  it('lists both launchers so the pid identity check recognises either', () => {
    // isOurListenSession matches /proc/<pid>/cmdline against this list. A
    // multi-mode session missing from it is invisible to Stop, which then
    // answers 409 while two HackRFs are still recording.
    expect(LAUNCHERS).toContain('lwin_listen.sh')
    expect(LAUNCHERS).toContain('lwin_listen_multi.sh')
  })

  it('emits NO multi-only flags in single mode', () => {
    // lwin_listen.sh exits 1 on an unknown option, so leaking --legs into a
    // single-mode run would fail the session outright rather than degrade it.
    const args = buildListenArgs({
      preset: 'pd', legs: '700,800', nVoice700: 3, nVoice800: 5, census: false,
    })
    expect(args).toEqual(['--preset', 'pd'])
  })

  it('emits the multi-only flags in multi mode', () => {
    const args = buildListenArgs({
      mode: 'multi', preset: 'pd-all', legs: '700,800',
      nVoice700: 3, nVoice800: 5,
    })
    expect(args).toContain('--legs')
    expect(args[args.indexOf('--legs') + 1]).toBe('700,800')
    expect(args[args.indexOf('--n-voice-700') + 1]).toBe('3')
    expect(args[args.indexOf('--n-voice-800') + 1]).toBe('5')
    expect(args).toContain('--preset')
  })

  it('passes --no-census only when census is explicitly false', () => {
    // The script defaults to running the census, so absent means "leave it on".
    expect(buildListenArgs({ mode: 'multi', preset: 'pd' })).not.toContain('--no-census')
    expect(buildListenArgs({ mode: 'multi', preset: 'pd', census: true }))
      .not.toContain('--no-census')
    expect(buildListenArgs({ mode: 'multi', preset: 'pd', census: false }))
      .toContain('--no-census')
  })

  it('accepts zero as a receiver count rather than dropping it', () => {
    // `if (opts.nVoice700)` would swallow 0. The script rejects 0 with a clear
    // error; silently omitting it would start a differently-shaped session.
    const args = buildListenArgs({ mode: 'multi', preset: 'pd', nVoice700: 0 })
    expect(args[args.indexOf('--n-voice-700') + 1]).toBe('0')
  })

  it('still puts duration last in multi mode', () => {
    // It is positional in lwin_listen_multi.sh too.
    const args = buildListenArgs({
      mode: 'multi', preset: 'pd', legs: '700', nVoice700: 3, duration: 300,
    })
    expect(args[args.length - 1]).toBe('300')
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

describe('inContainer', () => {
  // startListening() no longer consults this (see 'capture capability guard'
  // below) — transcriber.ts's own compose-managed guard still does, and its
  // reasoning is unchanged, so this pure boolean-env behaviour still needs
  // covering on its own.
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

/**
 * A fixed, non-empty PATH so hackrfInfoOnPath()'s scan has a deterministic
 * set of directories to probe with the mocked existsSync — the real
 * process.env.PATH on THIS host varies by shell and would otherwise make
 * these tests depend on what happens to be installed here.
 */
const TEST_PATH = '/usr/bin:/bin'

describe('captureCapabilityGap', () => {
  const ORIGINAL_PATH = process.env.PATH

  afterEach(() => {
    if (ORIGINAL_PATH === undefined) delete process.env.PATH
    else process.env.PATH = ORIGINAL_PATH
  })

  it('reports missing USB access first, without touching PATH or python3', () => {
    process.env.PATH = TEST_PATH
    mockExistsSync.mockReturnValue(false)
    expect(captureCapabilityGap()).toMatch(/dev\/bus\/usb/)
    // Short-circuited: USB already answered "not capable", so hackrf_info's
    // PATH scan and the python3 import never need to run.
    expect(mockExecFileSync).not.toHaveBeenCalled()
  })

  it('reports missing hackrf_info once USB is present', () => {
    process.env.PATH = TEST_PATH
    mockExistsSync.mockImplementation((p: unknown) => p === '/dev/bus/usb')
    expect(captureCapabilityGap()).toMatch(/hackrf_info/)
    expect(mockExecFileSync).not.toHaveBeenCalled()
  })

  it('reports a failed gnuradio import once USB and hackrf_info are present', () => {
    process.env.PATH = TEST_PATH
    mockExistsSync.mockReturnValue(true)
    mockExecFileSync.mockImplementation(() => {
      throw new Error('ENOENT: no such file or directory, posix_spawn \'python3\'')
    })
    expect(captureCapabilityGap()).toMatch(/gnuradio\.op25_repeater/)
  })

  it('reports capable (null) when USB, hackrf_info and the import all succeed', () => {
    process.env.PATH = TEST_PATH
    mockExistsSync.mockReturnValue(true)
    mockExecFileSync.mockReturnValue(Buffer.from(''))
    expect(captureCapabilityGap()).toBeNull()
    expect(isCaptureCapable()).toBe(true)
  })
})

describe('capture capability guard on startListening()', () => {
  const ORIGINAL_PATH = process.env.PATH

  afterEach(() => {
    if (ORIGINAL_PATH === undefined) delete process.env.PATH
    else process.env.PATH = ORIGINAL_PATH
  })

  /** Force captureCapabilityGap() to report "not capable" (no USB). */
  function forceNotCapable(): void {
    process.env.PATH = TEST_PATH
    mockExistsSync.mockReturnValue(false)
  }

  it('delegates to the capture control API instead of spawning when this process is not capable', async () => {
    forceNotCapable()
    mockFetch.mockResolvedValue(fakeControlResponse(200, true, {
      started: true, pid: 4242, args: ['--ess', '--include-encrypted', '--pd', '10800'],
    }))

    const opts = { mode: 'multi' as const, preset: 'pd', ess: true, includeEncrypted: true, duration: 10800 }
    const result = await startListening(opts, 7)

    expect(result).toEqual({ pid: 4242, config: opts, backend: 'delegated' })
    expect(mockSpawn).not.toHaveBeenCalled()
    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('http://capture:8082/start')
    expect(init.method).toBe('POST')
    // Only the fields the control server's build_args() actually accepts —
    // never a pre-built command line, and never the extra web-side fields
    // (preset here) this shape has no room for. sessionId IS included
    // (unlike preset) — it identifies our own session row, not an operator
    // choice, and the control API validates it independently regardless.
    expect(JSON.parse(init.body as string)).toEqual({
      mode: 'multi', ess: true, includeEncrypted: true, durationSec: 10800, sessionId: 7,
    })
  })

  it('refuses a request the control API cannot express, before any network call', async () => {
    forceNotCapable()
    // No `mode` at all -> defaults to 'single' on the web side, which the
    // control API has no way to run (ALLOWED_MODES is {'multi'} only).
    await expect(startListening({ preset: 'pd', duration: 600 }))
      .rejects.toThrow(/mode "single"/)
    await expect(startListening({ preset: 'pd', duration: 600 }))
      .rejects.toThrow(/cannot be delegated/i)
    expect(mockFetch).not.toHaveBeenCalled()
    expect(mockSpawn).not.toHaveBeenCalled()
  })

  it('refuses a request missing duration, since the remote PD preset only applies with one', async () => {
    forceNotCapable()
    await expect(startListening({ mode: 'multi', preset: 'pd' }))
      .rejects.toThrow(/duration/i)
    expect(mockFetch).not.toHaveBeenCalled()
    expect(mockSpawn).not.toHaveBeenCalled()
  })

  it('surfaces an actionable message naming a real recovery command when the control API is unreachable', async () => {
    forceNotCapable()
    mockFetch.mockRejectedValue(new Error('fetch failed: ECONNREFUSED'))
    await expect(startListening({ mode: 'multi', preset: 'pd', duration: 600 }))
      .rejects.toThrow(/stack\.sh/)
    expect(mockSpawn).not.toHaveBeenCalled()
  })

  it('surfaces the control API\'s own error verbatim on a non-2xx response', async () => {
    forceNotCapable()
    mockFetch.mockResolvedValue(
      fakeControlResponse(409, false, { error: 'a capture is already running; stop it first', pid: 111 }),
    )
    await expect(startListening({ mode: 'multi', preset: 'pd', duration: 600 }))
      .rejects.toThrow('a capture is already running; stop it first')
    expect(mockSpawn).not.toHaveBeenCalled()
  })

  it('falls back to a truncated raw body when a non-2xx response is not valid JSON', async () => {
    // M1 in task-3-review.md: a proxy/gateway failure could hand back an
    // HTML or plain-text body instead of the control server's own JSON
    // contract. The raw text should still reach the operator, not a bare
    // "HTTP 502" with no detail at all.
    forceNotCapable()
    mockFetch.mockResolvedValue({
      ok: false,
      status: 502,
      text: async () => '<html>Bad Gateway</html>',
    })
    await expect(startListening({ mode: 'multi', preset: 'pd', duration: 600 }))
      .rejects.toThrow(/Bad Gateway/)
    expect(mockSpawn).not.toHaveBeenCalled()
  })

  /**
   * The other half of the guard: if captureCapabilityGap()'s check were ever
   * removed (or its condition inverted) so that startListening() always took
   * ONE branch regardless of capability, this test and the delegation test
   * above would start disagreeing with mockSpawn/mockFetch — whichever
   * branch got hard-wired would fire in both tests, failing exactly one of
   * the "not called" assertions across the two.
   */
  it('spawns locally instead of delegating when this process IS capable', async () => {
    process.env.PATH = TEST_PATH
    mockExistsSync.mockReturnValue(true)
    mockExecFileSync.mockReturnValue(Buffer.from(''))
    mockSpawn.mockReturnValue({ pid: 4321, unref: vi.fn() })

    const result = await startListening({ preset: 'pd', duration: 600 })

    expect(result).toEqual({ pid: 4321, config: { preset: 'pd', duration: 600 }, backend: 'local' })
    expect(mockFetch).not.toHaveBeenCalled()
    expect(mockSpawn).toHaveBeenCalledTimes(1)
    const [cmd, args] = mockSpawn.mock.calls[0] as [string, string[]]
    expect(cmd).toBe('bash')
    expect(args[0]).toMatch(/lwin_listen\.sh$/)
    expect(args).toContain('--preset')
  })
})

describe('delegatedSessionLiveness', () => {
  // Three states, not two — task-3-review.md's fix-round-2 finding
  // ("unreachable != stopped"). 'unknown' must never collapse into
  // 'stopped': that collapse is exactly what let a single transient
  // control-API blip permanently untrack a healthy session (see
  // session.ts's isSessionAlive() for how a caller is supposed to use
  // 'unknown' — tolerate a bounded number of them, not treat one as final).

  it('reports "alive" only when the control API confirms BOTH something running AND that it is this pid', async () => {
    mockFetch.mockResolvedValue(fakeControlResponse(200, true, { running: true, pid: 42 }))
    expect(await delegatedSessionLiveness(42)).toBe('alive')
  })

  it('reports "stopped" — a DEFINITIVE answer — when running:true names a DIFFERENT pid', async () => {
    // Some OTHER capture is live (e.g. started from a shell after ours
    // ended) — the control API answered definitively, it's just that the
    // definitive answer is "not this session." Must NOT be 'unknown': the
    // control API is reachable and gave a real answer.
    mockFetch.mockResolvedValue(fakeControlResponse(200, true, { running: true, pid: 42 }))
    expect(await delegatedSessionLiveness(99)).toBe('stopped')
  })

  it('reports "stopped" when the control API affirmatively reports nothing running', async () => {
    mockFetch.mockResolvedValue(fakeControlResponse(200, true, { running: false, pid: null }))
    expect(await delegatedSessionLiveness(42)).toBe('stopped')
  })

  it('reports "unknown" — NOT "stopped" — when the control API is unreachable', async () => {
    mockFetch.mockRejectedValue(new Error('ECONNREFUSED'))
    expect(await delegatedSessionLiveness(42)).toBe('unknown')
  })

  it('reports "unknown" — NOT "stopped" — on a non-2xx response', async () => {
    // capture_control.py's GET /status always answers 200 when healthy; a
    // non-2xx means something is wrong reaching it, not an authoritative
    // "not running."
    mockFetch.mockResolvedValue(fakeControlResponse(500, false, { error: 'boom' }))
    expect(await delegatedSessionLiveness(42)).toBe('unknown')
  })

  it('reports "unknown" on a 200 whose body this function cannot recognize', async () => {
    mockFetch.mockResolvedValue(fakeControlResponse(200, true, { unexpected: 'shape' }))
    expect(await delegatedSessionLiveness(42)).toBe('unknown')
  })
})

describe('stopDelegatedCapture', () => {
  it('POSTs to the control API\'s /stop and resolves on a 200, regardless of whether anything was running', async () => {
    // /stop is idempotent per the control server's own contract -- "nothing
    // was running" is still a 200, not an error -- so this resolves either way.
    mockFetch.mockResolvedValue(fakeControlResponse(200, true, { stopped: true, pid: 42, forced: false }))
    await expect(stopDelegatedCapture()).resolves.toBeUndefined()
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('http://capture:8082/stop')
    expect(init.method).toBe('POST')
  })

  it('surfaces an actionable message naming a real recovery command when the control API is unreachable', async () => {
    mockFetch.mockRejectedValue(new Error('ECONNREFUSED'))
    await expect(stopDelegatedCapture()).rejects.toThrow(/stack\.sh/)
  })

  it('surfaces the control API\'s own error verbatim on a non-2xx response', async () => {
    mockFetch.mockResolvedValue(fakeControlResponse(500, false, { error: 'internal error while stopping' }))
    await expect(stopDelegatedCapture()).rejects.toThrow('internal error while stopping')
  })
})

/** A pgrep failure shaped like its real "nothing matched" exit (status 1, no stdout). */
function pgrepNoMatch(): never {
  throw Object.assign(new Error('Command failed'), { status: 1, stdout: '' })
}

describe('isRadioBusy', () => {
  it('is true when pgrep matches a receiver pattern', () => {
    mockExecFileSync.mockReturnValue('332179\n')
    expect(isRadioBusy()).toBe(true)
  })

  it('is false when pgrep matches nothing (its normal "no match" exit)', () => {
    mockExecFileSync.mockImplementation(pgrepNoMatch)
    expect(isRadioBusy()).toBe(false)
  })
})

describe('stopListening — untracked release (pid 0)', () => {
  it('returns immediately without touching pkill when the radio is already free', async () => {
    mockExecFileSync.mockImplementation(pgrepNoMatch)
    await expect(stopListening(0)).resolves.toBeUndefined()
    expect(mockSpawnSync).not.toHaveBeenCalled()
  })

  it('resolves once pkill actually releases the radio', async () => {
    // Busy on the entry guard's check, free from the very next isRadioBusy()
    // call on — the wait loop's first iteration then breaks immediately, so
    // this never needs a real or faked sleep.
    let calls = 0
    mockExecFileSync.mockImplementation(() => {
      calls += 1
      if (calls === 1) return '331916\n'
      return pgrepNoMatch()
    })
    mockSpawnSync.mockReturnValue({ status: 1, stderr: '' })
    await expect(stopListening(0)).resolves.toBeUndefined()
  })

  /**
   * The regression this fix closes. Before it, stopListening()'s last-resort
   * pkill loop only checked whether execFileSync('pkill', ...) THREW — and
   * real procps pkill exits 0 (no throw) as soon as its pattern matches any
   * process, regardless of whether the kill() syscall on that process
   * actually succeeded. A signal refused with EPERM for every matched pid —
   * exactly what happens when the `web` container (pid: host, but confined by
   * the docker-default AppArmor profile) tries to signal a capture the host
   * started outside any container — therefore read as "matched nothing,
   * which is the goal state" and stopListening() resolved successfully while
   * the radio stayed on the air. Reverting the isRadioBusy()-after-pkill
   * check this test exercises reproduces exactly that silent false success.
   */
  it('throws, naming the EPERM cause and a real host recovery command, when pkill matches but cannot actually signal the process', async () => {
    vi.useFakeTimers()
    try {
      // isRadioBusy() stays true for the entire attempt: this container can
      // always SEE the host process (pid: host gives it /proc), it can just
      // never successfully signal it.
      mockExecFileSync.mockReturnValue('331916\n')
      // A real pkill in this scenario exits 0 (pattern matched) while
      // reporting the true, per-pid outcome only on stderr.
      mockSpawnSync.mockReturnValue({
        status: 0,
        stderr: 'pkill: killing pid 331916 failed: Permission denied\n',
      })

      // .catch() attached in the same tick stopListening() is invoked, so the
      // rejection — which lands later, once the faked wait loop above drains
      // — is never briefly "unhandled" from Node's perspective.
      const caughtPromise = stopListening(0).catch((e: unknown) => e as Error)
      // Drains the 8s wait-for-release loop (40 x 200ms) under fake timers,
      // rather than a real 8-second wait.
      await vi.advanceTimersByTimeAsync(8000)
      const caught = await caughtPromise

      expect(caught).toBeDefined()
      expect(caught?.message).toMatch(/Permission denied/)
      // Honest AND actionable: names the real command to run on the host,
      // not just "something went wrong".
      expect(caught?.message).toMatch(/pkill -INT -f/)
    } finally {
      vi.useRealTimers()
    }
  })
})
