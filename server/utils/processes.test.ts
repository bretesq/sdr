import { describe, it, expect } from 'vitest'
import {
  buildListenArgs, countCalls, scriptFor, LAUNCHERS, inContainer, startListening,
} from './processes'

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

describe('container mode', () => {
  /**
   * The web container can see host processes through `pid: host`, so reading
   * and releasing the radio still work. Only STARTING a capture is impossible:
   * op25 needs USB access to the HackRFs and a gnuradio stack the image does
   * not carry. Spawning anyway would fail deep inside bash with an error the
   * operator cannot act on.
   */
  it('refuses to start a capture and names the command to run instead', () => {
    process.env.SDR_IN_CONTAINER = '1'
    try {
      expect(inContainer()).toBe(true)
      expect(() => startListening({ preset: 'pd' })).toThrow(/container/i)
      expect(() => startListening({ preset: 'pd' })).toThrow(/lwin_listen_multi\.sh/)
    } finally {
      delete process.env.SDR_IN_CONTAINER
    }
  })

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
