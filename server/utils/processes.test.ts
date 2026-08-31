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
